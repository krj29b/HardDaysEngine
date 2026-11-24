"""Independent Valley data and natural original campaign visit outcomes (§12.38)."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING
from unittest.mock import patch

import numpy as np
import pytest

from harddaysengine.app.audit import AuditRig
from harddaysengine.app.fight_audit import fight_host_melee_input
from harddaysengine.app.game import GameScene, GameSession
from harddaysengine.app.input import InputFrame, PlayerInput
from harddaysengine.app.valley_audit import valley_until, valley_visit
from harddaysengine.app.village_audit import village_exit
from harddaysengine.assets.adf import DiskSet
from harddaysengine.assets.hunk import parse_hunk_executable
from harddaysengine.assets.mog_code import MogCode
from harddaysengine.assets.valley_data import parse_valley_data, valley_arena
from harddaysengine.sim.actor import StatKind
from harddaysengine.sim.rewards import pick_stat
from harddaysengine.sim.types import KnightId
from harddaysengine.sim.valley import ValleyPhase

if TYPE_CHECKING:
    from pathlib import Path

    from harddaysengine.sim.rng import Rng

pytestmark = pytest.mark.adf


def _session(adf_dir: Path, *, keys: int = 15, win: bool = False) -> GameSession:
    session = GameSession(disk_set=DiskSet.from_directory(adf_dir))
    valley_visit(session, keys=keys, win=win)
    session.overworld.spent = 17
    return session


def _tick(session: GameSession, *, fire: bool = False, kid: KnightId = KnightId.RED) -> None:
    session.step_tick(InputFrame(players={kid: PlayerInput(fire=fire)}))


def _map_return(session: GameSession) -> None:
    for _ in range(200):
        if session.scene == GameScene.OVERWORLD:
            return
        _tick(session)
    raise AssertionError("Valley did not return to the map")


def test_decoded_rules_and_wall_match_independent_disk_reads(adf_dir: Path) -> None:
    exe = parse_hunk_executable("mog", DiskSet.from_directory(adf_dir).read_file("mog"))
    data, stage = parse_valley_data(exe), valley_arena(MogCode(exe))
    code, floor, tail = (exe.sections[i].data for i in (0, 12, 36))
    rules = data.rules
    assert (
        rules.keys,
        rules.life_loss,
        rules.stat_floor,
        rules.stat_loss,
        rules.progress,
    ) == tuple(
        int.from_bytes(code[pos : pos + 2], "big")
        for pos in (0x16CC, 0x171C, 0x1722, 0x172A, 0x173A)
    )
    assert rules.key_clear == int.from_bytes(code[0x1744:0x1746], "big")
    assert rules.reward_wait == code[0x175B] and rules.moon_mask == int.from_bytes(
        tail[0x672:0x676], "big"
    )
    assert stage.picture == 5 and code[0x3228:0x322A] == b"\0\x20"
    assert len(stage.obstacles) == int.from_bytes(floor[0x3E2:0x3E4], "big")
    wall = stage.obstacles[0]
    assert (wall.x1, wall.x2, wall.y, wall.unused) == tuple(
        int.from_bytes(floor[pos : pos + 2], "big", signed=True)
        for pos in (0x3E6, 0x3EA, 0x3EE, 0x3F2)
    )
    assert data.recolor == tuple(
        int.from_bytes(code[p : p + 2], "big") for p in range(0x3020, 0x3036, 4)
    )
    assert data.style == int.from_bytes(code[0x9352:0x9354], "big") == 8
    for pos, lines in ((0x16D4, data.missing), (0x174A, data.victory)):
        pointer = int.from_bytes(code[pos : pos + 4], "big")
        text = exe.sections[4].data
        for line in lines:
            start = int.from_bytes(text[pointer : pointer + 4], "big")
            assert line.text == text[start : text.index(0, start)].decode("ascii")
            assert (line.x, line.y, line.style) == tuple(
                int.from_bytes(text[pointer + d : pointer + d + 2], "big") for d in (4, 6, 8)
            )
            pointer = int.from_bytes(text[pointer + 10 : pointer + 14], "big")
        assert pointer == 0


@pytest.mark.parametrize("keys", [0, 1, 7, 14, 16, 31, 255])
def test_keys_require_exact_mask_and_missing_message_keeps_turn(adf_dir: Path, keys: int) -> None:
    session = _session(adf_dir, keys=keys)
    world, actor, inv = session.overworld, session.actors[0], session.inventories[1]
    before, counts, rng = asdict(actor), inv.counts[:], world.prng.state
    valley_until(session, ValleyPhase.MISSING)
    assert session.active_encounter is None and session.loading_cycle == 0
    assert actor.hp == before["hp"] and inv.counts == counts and world.prng.state == rng
    _tick(session, fire=True, kid=KnightId.YELLOW)
    _tick(session)
    assert session.scene == GameScene.VALLEY
    _tick(session, fire=True)
    for _ in range(5):
        _tick(session, fire=True)
        assert session.valley_screen is not None
        assert session.valley_screen.state.phase == ValleyPhase.MISSING
    _tick(session)
    _map_return(session)
    assert session.valley_screen is None and world.spent == 17
    assert inv.counts == counts and world.prng.state == rng


def test_guardian_uses_original_stage_floor_shared_rng_and_no_regional_arena(adf_dir: Path) -> None:
    session = _session(adf_dir, win=True)
    assert session.asset_bundle is not None
    with patch.object(
        type(session.asset_bundle), "get_arena", side_effect=AssertionError("regional arena")
    ):
        valley_until(session, ValleyPhase.COMBAT)
    active, loader = session.active_encounter, session.fight_loader
    assert active is not None and loader is not None
    f = active.runtime.fight
    assert f.rng is session.overworld.prng and f.actors[0] is session.actors[0]
    assert f.inventories[1] is session.inventories[1]
    assert (f.max_alive, f.total, f.alive_now) == (1, 1, 1) and active.runtime.waves is None
    assert [(w.x1, w.x2, w.y) for w in f.walls] == [(0, 309, 99)]
    assert f.actor(f.guardian.main).hp == 140 and f.actor(f.guardian.body).active
    assert session.loading_cycle == 1 and active.trigger.creature_type == 8
    session._reset_campaign(1)
    assert session.valley_screen is None and session.active_encounter is None


def test_natural_win_defers_single_moonstone_until_release_wait_and_keeps_zero_draw_turn(
    adf_dir: Path,
) -> None:
    session = _session(adf_dir, win=True)
    actor, inv, world = session.actors[0], session.inventories[1], session.overworld
    actor.progress = 65534
    inv.counts[11] = 8
    valley_until(session, ValleyPhase.VICTORY, win=True)
    assert actor.progress == 1 and inv.byte(20) == 0 and inv.byte(22) == 8
    assert session.last_defeat_mask == 0 and actor.lives == 5 and actor.hp > 0
    assert session.active_encounter is None and world.spent == 17
    screen = session.valley_screen
    assert screen is not None and screen.state.won
    # A zero RNG state proves the original zero-return trap no longer suppresses map entry.
    world.prng.state = 0
    _tick(session, fire=True)
    for _ in range(5):
        _tick(session, fire=True)
    assert screen.state.phase == ValleyPhase.VICTORY and inv.byte(22) == 8
    _tick(session)
    assert screen.state.phase == ValleyPhase.REWARD_WAIT and screen.state.wait == 10
    for _ in range(9):
        _tick(session)
        assert inv.byte(22) == 8 and screen.state.phase == ValleyPhase.REWARD_WAIT
    _tick(session)
    assert screen.state.phase == ValleyPhase.EXIT_OUT and inv.byte(22) == 8
    with patch.object(type(world.prng), "draw", wraps=world.prng.draw) as draw:
        _map_return(session)
        draw.assert_called_once()
    assert inv.byte(22) == 9 and world.spent == 17 and session.valley_screen is None


def test_natural_loss_restores_hp_loses_three_lives_and_picked_stat_before_temple(
    adf_dir: Path,
) -> None:
    session = _session(adf_dir)
    world, actor = session.overworld, session.actors[0]
    oracle = []
    original = pick_stat

    def pick(record: object, rng: Rng, gifts: object) -> int:
        # Observe the public shared picker without replacing its draws or mutations.
        assert record is actor and gifts is session._turn_rules().gifts
        assert actor.lives == 4 and actor.hp == actor.hp_max
        offset = original(actor, rng, session._turn_rules().gifts)
        oracle.extend((offset, actor.get_stat(StatKind(offset))))
        return offset

    with patch("harddaysengine.sim.valley.pick_stat", side_effect=pick):
        valley_until(session, ValleyPhase.TEMPLE)
    assert session.last_defeat_mask == 1 and actor.lives == 2
    assert actor.get_stat(StatKind(oracle[0])) == (oracle[1] if oracle[1] == 1 else oracle[1] - 1)
    assert session.inventories[1].byte(20) == 15 and actor.progress == 0
    assert session.sheet_screen is not None and session.sheet_screen.state.number == 9
    assert world.spent == 17 and session.active_encounter is None
    temple_budget = world.budget
    village_exit(session)
    assert world.spent == temple_budget and world.spent >= world.budget
    assert session.valley_screen is None


def test_loss_with_all_stats_at_cap_does_not_mutate_unrelated_record(adf_dir: Path) -> None:
    session = _session(adf_dir)
    actor = session.actors[0]
    actor.strength = actor.constitution = actor.endurance = 5
    valley_until(session, ValleyPhase.TEMPLE)
    assert (actor.strength, actor.constitution, actor.endurance) == (5, 5, 5)
    assert actor.lives == 2


def _trace(adf_dir: Path, *, win: bool) -> list[str]:
    session = _session(adf_dir, win=win)
    hashes = []
    for _ in range(4000):
        inp = InputFrame()
        if session.scene == GameScene.COMBAT and win:
            inp = fight_host_melee_input(session)
        screen = session.valley_screen
        assert screen is not None
        if screen.state.phase == ValleyPhase.VICTORY and not screen.state.wait:
            inp = InputFrame(players={KnightId.RED: PlayerInput(fire=not screen.state.fire_held)})
        if session.sheet_screen is not None:
            sheet = session.sheet_screen
            done = next(row for row in sheet.state.regions if row.id == 7)
            sheet.state.pointer_x, sheet.state.pointer_y = done.x, done.y
            inp = InputFrame(players={KnightId.RED: PlayerInput(fire=True)})
        session.step_tick(inp)
        hashes.append(session.state_hash())
        if session.scene == GameScene.OVERWORLD:
            return hashes
    raise AssertionError("Valley replay never returned to the map")


@pytest.mark.parametrize("win", [False, True])
def test_natural_fight_and_entire_caller_replay_hashes_match(adf_dir: Path, win: bool) -> None:
    assert _trace(adf_dir, win=win) == _trace(adf_dir, win=win)


@pytest.mark.gl
def test_rendering_messages_combat_fades_and_hash_cells_is_pure(adf_dir: Path) -> None:
    rig = AuditRig(adf_dir)
    try:
        session = _session(adf_dir, keys=7)
        valley_until(session, ValleyPhase.MISSING)
        for target in ("wait", "fire_held", "loading_index", "won"):
            screen = session.valley_screen
            assert screen is not None
            before = session.state_hash()
            value = getattr(screen.state, target)
            setattr(screen.state, target, value + 1)
            assert session.state_hash() != before
            setattr(screen.state, target, value)
        for win in (False, True):
            session = _session(adf_dir, win=win)
            phases = (ValleyPhase.LOADING, ValleyPhase.COMBAT, ValleyPhase.COMBAT_OUT)
            for phase in phases:
                valley_until(session, phase, win=win)
                before = session.state_hash()
                a, b = rig.capture(session, "first"), rig.capture(session, "again")
                assert before == session.state_hash() and np.array_equal(a.rgb, b.rgb)
                assert (
                    not session.active_encounter
                    or not session.active_encounter.runtime.fight.skipped
                )
            valley_until(session, ValleyPhase.VICTORY if win else ValleyPhase.TEMPLE)
            before = session.state_hash()
            rig.capture(session, "outcome")
            assert before == session.state_hash()
    finally:
        rig.release()
