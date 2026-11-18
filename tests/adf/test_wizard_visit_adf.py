"""Original wizard stages, caller settlement and replay/render invariance (§12.36)."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from typing import TYPE_CHECKING
from unittest.mock import patch

import numpy as np
import pytest

from harddaysengine.app.audit import AuditRig
from harddaysengine.app.game import GameScene, GameSession
from harddaysengine.app.input import InputFrame, PlayerInput
from harddaysengine.app.wizard_audit import wizard_seed, wizard_until
from harddaysengine.assets.adf import DiskSet
from harddaysengine.assets.hunk import parse_hunk_executable
from harddaysengine.assets.wizard_screen_data import parse_wizard_rules
from harddaysengine.sim.overworld import Arrival
from harddaysengine.sim.rewards import GiftKind, GiftState, recalc_endurance, wizard_gift
from harddaysengine.sim.rng import Rng
from harddaysengine.sim.types import KnightId
from harddaysengine.sim.wizard import WizardPhase

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.adf


def _session(adf_dir: Path, kind: GiftKind) -> GameSession:
    session = GameSession(disk_set=DiskSet.from_directory(adf_dir))
    actor = session.actors[0]
    actor.strength = actor.constitution = actor.endurance = 2
    actor.hp, actor.hp_max, actor.recency, actor.armour = 20, 30, 0, 27
    recalc_endurance(actor)
    session.inventories[1].counts[:] = [0] * 12
    session.overworld.gifts = GiftState()
    session.overworld.prng.state = wizard_seed(session, kind)
    session._dispatch_arrival(Arrival(1, 30))
    return session


def _tick(session: GameSession, *, fire: bool = False, player: KnightId = KnightId.RED) -> None:
    session.step_tick(InputFrame(players={player: PlayerInput(fire=fire)}))


def _close_temple(session: GameSession) -> None:
    wizard_until(session, WizardPhase.TEMPLE)
    screen = session.sheet_screen
    assert screen is not None and screen.state.number == 9
    done = next(r for r in screen.state.regions if r.id == 7)
    screen.state.pointer_x, screen.state.pointer_y = done.x, done.y
    _tick(session, fire=True)
    assert session.wizard_screen is not None
    assert session.wizard_screen.state.phase == WizardPhase.TEMPLE_OUT
    for _ in range(200):
        if session.scene == GameScene.OVERWORLD:
            return
        _tick(session)
    raise AssertionError("wizard temple did not return to the map")


@pytest.mark.parametrize("kind", list(GiftKind))
def test_natural_visit_applies_one_shared_reward_and_spends_only_after_temple(
    adf_dir: Path, kind: GiftKind
) -> None:
    session = _session(adf_dir, kind)
    world = session.overworld
    actor, inv = session.actors[0], session.inventories[1]
    oracle_actor, oracle_inv, oracle_state = deepcopy(actor), deepcopy(inv), deepcopy(world.gifts)
    oracle_rng = Rng(world.prng.state)
    result = wizard_gift(
        oracle_actor, oracle_inv, oracle_rng, session._turn_rules().gifts, oracle_state
    )
    before = world.prng.state
    wizard_until(session, WizardPhase.ARRIVAL)
    assert world.prng.state == before and session.loading_cycle == 1
    with patch("harddaysengine.app.game.wizard_gift", wraps=wizard_gift) as roll:
        wizard_until(session, WizardPhase.GIFT, fire=True)
        wizard_until(session, WizardPhase.FAREWELL, fire=True)
        roll.assert_called_once()
    screen = session.wizard_screen
    assert screen is not None and screen.state.result == result and result.kind == kind
    assert asdict(actor) == asdict(oracle_actor) and inv == oracle_inv
    assert world.prng.state == oracle_rng.state and world.gifts == oracle_state
    assert screen.animation is not None and not screen.animation.fight.skipped
    assert screen.speech(1) == screen.dialogue.gift(result).lines
    assert not session.palette_fx.is_fading  # gift-to-farewell installs the class palette directly
    if kind == GiftKind.ITEM:
        assert dict(screen.rules.items)[result.item] in " ".join(screen.speech(2))
    if kind == GiftKind.GOLD:
        assert str(result.amount) in " ".join(screen.speech(2))
    wizard_until(session, WizardPhase.TEMPLE, fire=True)
    assert actor.recency == screen.dialogue.recency
    assert world.spent < world.budget
    _close_temple(session)
    assert session.wizard_screen is None and session.sheet_screen is None
    assert world.spent == world.budget


def test_input_is_sampled_at_frame_wait_end_and_other_players_cannot_advance(adf_dir: Path) -> None:
    session = _session(adf_dir, GiftKind.ITEM)
    wizard_until(session, WizardPhase.ARRIVAL)
    screen = session.wizard_screen
    assert screen is not None and screen.animation is not None
    while screen.animation.countdown != screen.animation.frame_wait:
        _tick(session)
    _tick(session, fire=True)
    for _ in range(screen.animation.frame_wait - 1):
        _tick(session)
    assert screen.state.phase == WizardPhase.ARRIVAL
    for _ in range(25):
        _tick(session, fire=True, player=KnightId.YELLOW)
    assert screen.state.phase == WizardPhase.ARRIVAL
    wizard_until(session, WizardPhase.GIFT, fire=True)


def test_replay_hashes_cover_jobs_waits_result_and_loading_cursor(adf_dir: Path) -> None:
    traces = []
    for _ in range(2):
        session = _session(adf_dir, GiftKind.STAT)
        trace = []
        for tick in range(400):
            _tick(session, fire=tick >= 180)
            trace.append(session.state_hash())
        traces.append(trace)
        screen = session.wizard_screen
        assert screen is not None and screen.animation is not None
        before = session.state_hash()
        screen.animation.fight.engine.jobs[0].work.loop_count ^= 1
        assert session.state_hash() != before
        before = session.state_hash()
        session.loading_cycle ^= 1
        assert session.state_hash() != before
    assert traces[0] == traces[1]


def test_loading_cursor_wraps_and_new_campaign_clears_visit(adf_dir: Path) -> None:
    session = _session(adf_dir, GiftKind.GOLD)
    session.wizard_screen = None
    session.loading_cycle = 13
    session._dispatch_arrival(Arrival(1, 30))
    wizard_until(session, WizardPhase.LOADING)
    assert session.loading_cycle == 0
    assert session.wizard_screen is not None and session.wizard_screen.state.loading_index == 13
    session._reset_campaign(1)
    assert session.wizard_screen is None and session.loading_cycle == 0


def test_rules_match_independent_disk_operand_reads(adf_dir: Path) -> None:
    executable = parse_hunk_executable("mog", DiskSet.from_directory(adf_dir).read_file("mog"))
    rules = parse_wizard_rules(executable)
    code = executable.sections[0].data

    def word(pos: int) -> int:
        return int.from_bytes(code[pos : pos + 2], "big")

    def long(pos: int) -> int:
        return int.from_bytes(code[pos : pos + 4], "big")

    assert rules.waits == tuple(word(p) & 255 for p in (0x9680, 0x9764, 0x985C))
    assert rules.palette_entry == long(0x95C4) // 2 and rules.palette_period == word(0x9666) & 255
    assert [s.script for s in rules.spawns] == [long(p + 2) for p in (0x95CE, 0x96EE, 0x9716)]
    assert [s.position for s in rules.spawns] == [
        tuple(word(p + d) for d in (14, 18, 22)) for p in (0x95CE, 0x96EE, 0x9716)
    ]
    for pos, name in ((0x2E5C, rules.tower), (0x2E8C, rules.gift), (0x2F04, rules.cel)):
        data, start = executable.sections[4].data, long(pos + 2)
        assert name == data[start : data.index(0, start)].decode("ascii")
    shades = [word(p + 2) for p in range(0xAAE6, 0xAB6A, 2) if word(p) in (0x30FC, 0x30BC)]
    assert shades == [v for _, row in rules.ramps for v in row]


@pytest.mark.gl
def test_rendering_is_pure_for_loading_each_stage_and_black_fade_tail(adf_dir: Path) -> None:
    rig = AuditRig(adf_dir)
    try:
        for kind in GiftKind:
            session = _session(adf_dir, kind)
            for phase in (
                WizardPhase.LOADING,
                WizardPhase.ARRIVAL,
                WizardPhase.GIFT,
                WizardPhase.FAREWELL,
            ):
                wizard_until(session, phase, fire=phase in (WizardPhase.GIFT, WizardPhase.FAREWELL))
                before = session.state_hash()
                one, two = rig.capture(session, "one"), rig.capture(session, "two")
                assert before == session.state_hash() and np.array_equal(one.rgb, two.rgb)
                assert len(np.unique(one.rgb.reshape(-1, 3), axis=0)) > 3
        session = _session(adf_dir, GiftKind.ITEM)
        for _ in range(200):
            screen = session.wizard_screen
            if (
                screen is not None
                and screen.state.phase == WizardPhase.LOADING_OUT
                and screen.state.wait == 1
            ):
                break
            _tick(session)
        assert not session.palette_fx.is_fading
        assert not np.any(rig.capture(session, "black_fade_tail").rgb)
    finally:
        rig.release()
