"""Real-disk computer knight outcomes, original setup and replay (§10.31)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from harddaysengine.app.fight_audit import FightAudit, build_computer_knight_audit
from harddaysengine.app.fight_runtime import FightLoader, FightRuntime, FightUnavailableError
from harddaysengine.app.game import GameScene, GameSession
from harddaysengine.app.input import InputFrame, PlayerInput
from harddaysengine.app.scene_assets import GameAssetBundle
from harddaysengine.assets.adf import DiskSet
from harddaysengine.assets.computer_knight_rules import _entry, parse_computer_knight_rules
from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.mog_code import MogCode
from harddaysengine.sim.actor import Actor
from harddaysengine.sim.fight import END_FIGHT, Fight, HandlerResult, Restart, end_fight
from harddaysengine.sim.fight_ai.knight import COMPUTER_KNIGHT_TYPE
from harddaysengine.sim.fight_moves import DOWN, FIRE, LEFT, RIGHT, UP
from harddaysengine.sim.m68k import s16
from harddaysengine.sim.overworld import EncounterTrigger, MoonPhase, TriggerKind
from harddaysengine.sim.rng import Rng
from harddaysengine.sim.types import KnightId
from tests.adf.test_fight_defs_adf import Walker, _check, _frame_counts

if TYPE_CHECKING:
    from pathlib import Path

    from harddaysengine.sim.fight_script import RoutineCall

pytestmark = pytest.mark.adf


@pytest.fixture(scope="module")
def bundle(adf_dir: Path) -> GameAssetBundle:
    return GameAssetBundle(DiskSet.from_directory(adf_dir))


def _play(bundle: GameAssetBundle, *, win: bool) -> tuple[Fight, list[str], list[int], set[int]]:
    audit = build_computer_knight_audit(bundle)
    fight = audit.scene.fight
    if not win:
        fight.actor(1).hp = 20
    hashes, died, scripts = [], [], set()
    original = fight.handlers[COMPUTER_KNIGHT_TYPE]

    def handler(fight: Fight, handle: int) -> HandlerResult:
        result = original(fight, handle)
        if isinstance(result, Restart):
            scripts.add(result.script)
        return result

    def death(fight: Fight, call: RoutineCall | None) -> None:
        if call is not None:
            died.append(call.owner)
            assert s16(fight.actor(call.owner).hp) <= 0
        end_fight(fight, call)

    fight.handlers[COMPUTER_KNIGHT_TYPE], fight.routines[END_FIGHT] = handler, death
    runtime = FightRuntime(audit.scene, 1, opponent=2)
    for frame in range(1500):
        fight.ports[0] = audit.input_word() if win else 0
        output = audit.scene.advance(frame * 6)
        assert all(call.name.startswith("sound_") for call in output.skipped_calls)
        hashes.append(
            hashlib.sha256(json.dumps(runtime.snapshot(), sort_keys=True).encode()).hexdigest()
        )
        if not fight.fight_active:
            break
    assert not fight.fight_active
    return fight, hashes, died, scripts


def test_equipped_knight_wins_through_hurt_script_death_and_replays(
    bundle: GameAssetBundle,
) -> None:
    fight, hashes, died, scripts = _play(bundle, win=True)
    assert s16(fight.actor(1).hp) > 0 and s16(fight.actor(2).hp) <= 0
    assert died == [2]
    assert FightLoader(bundle).defs.knight.hurt_scripts[8] in scripts
    assert hashes == _play(bundle, win=True)[1]


def test_idle_human_loses_to_computer_knight_and_replays(bundle: GameAssetBundle) -> None:
    fight, hashes, died, _ = _play(bundle, win=False)
    assert s16(fight.actor(1).hp) <= 0 and s16(fight.actor(2).hp) > 0
    assert died == [1] and hashes == _play(bundle, win=False)[1]


@pytest.mark.parametrize("computer", [1, 2])
def test_duel_selects_computer_jobs_ports_black_palette_and_reach(
    bundle: GameAssetBundle, computer: int
) -> None:
    loader = FightLoader(bundle)
    records = [Actor(kind=i, hp=20, hp_max=20) for i in range(4)]
    runtime = loader.duel(
        "fo1.t", records, Actor(), {}, Rng(7), 1, 2, computers=frozenset({computer}), day=7
    )
    fight, init = runtime.fight, loader.defs.knight
    actor = fight.actor(computer)
    job = fight.find_job(computer)
    assert job is not None and job.type == actor.type == COMPUTER_KNIGHT_TYPE
    assert actor.kind == actor.input_port == 4
    assert fight.actor(3 - computer).input_port == 2
    assert (actor.reach_x, actor.too_close_x, actor.depth_reach) == (
        init.reach_x,
        init.too_close_x,
        init.depth_reach,
    )
    assert fight.ai_day == 7
    old = runtime.snapshot()
    fight.ai_last_action = 32
    assert runtime.snapshot() != old
    words = loader.mog.hunk_data(40)
    rules = loader.computer_knight_rules
    assert rules.strike8_range == int.from_bytes(words[0xE98:0xE9A], "big")
    assert rules.heavy_range == int.from_bytes(words[0xEBC:0xEBE], "big")
    assert rules.strike4_range == int.from_bytes(words[0xEE0:0xEE2], "big")
    assert rules.guard_range == int.from_bytes(words[0xE06:0xE08], "big")


def test_two_computers_are_rejected(bundle: GameAssetBundle) -> None:
    with pytest.raises(FightUnavailableError, match="human"):
        FightLoader(bundle).duel(
            "fo1.t",
            [Actor() for _ in range(4)],
            Actor(),
            {},
            Rng(1),
            1,
            2,
            computers=frozenset({1, 2}),
        )


def test_every_knight_script_uses_exact_duel_frames(bundle: GameAssetBundle) -> None:
    loader = FightLoader(bundle)
    defs = loader.defs
    lists = [defs.knight_cels, defs.duel_cels]
    sets = {index + 1: _frame_counts(bundle, cels.slots) for index, cels in enumerate(lists)}
    entries = {
        defs.knight.idle_script,
        defs.knight.after_hit_script,
        *defs.knight.action_scripts,
        *defs.knight.hurt_scripts,
        *defs.knight.walk_scripts,
    } - {0}
    assert _check(Walker(loader.mog), entries, 2, sets) == []


def test_bad_crc_and_changed_handler_are_rejected(bundle: GameAssetBundle) -> None:
    loader = FightLoader(bundle)
    executable = loader.mog.executable
    section = executable.sections[0]
    broken = replace(section, data=b"\xff\xff" + section.data[2:])
    with pytest.raises(MogVersionError):
        parse_computer_knight_rules(
            replace(executable, sections=(broken, *executable.sections[1:]))
        )
    body = executable.sections[40]
    broken = replace(body, data=body.data[:0xCFC] + b"\xff\xff" + body.data[0xCFE:])
    changed = replace(
        executable, sections=(*executable.sections[:40], broken, *executable.sections[41:])
    )
    with pytest.raises(MogVersionError, match="instruction"):
        _entry(MogCode(changed))


def test_campaign_computer_duel_is_enabled_and_replays(bundle: GameAssetBundle) -> None:
    traces = []
    for _ in range(2):
        session = GameSession(start_scene=GameScene.OVERWORLD)
        session.asset_bundle = bundle
        opponent = next(kid for kid in KnightId if kid not in session.players)
        first = session.players[0]
        trigger = EncounterTrigger(
            TriggerKind.PVP_DUEL, first, -1, 1, MoonPhase.NEW_MOON, opponent_knight_id=opponent
        )
        session._start_combat(trigger, first)
        assert session.active_encounter is not None
        runtime = session.active_encounter.runtime
        assert runtime.fight.actor(int(opponent) + 1).type == COMPUTER_KNIGHT_TYPE
        trace = []
        for vbl in range(1, 121):
            runtime.step_vbl(vbl, [0, 0])
            trace.append(session.state_hash())
        traces.append(trace)
    assert traces[0] == traces[1]


@pytest.mark.parametrize("win", [True, False])
def test_campaign_host_samples_only_human_input_and_settles_computer_duel(
    bundle: GameAssetBundle, win: bool
) -> None:
    session = GameSession(start_scene=GameScene.OVERWORLD)
    session.asset_bundle = bundle
    first = session.players[0]
    opponent = next(kid for kid in KnightId if kid not in session.players)
    session._reset_campaign(1, (first,))
    assert len(session.knight_combat) == 4
    legacy = session.knight_combat[first]
    if win:
        legacy.hp = legacy.max_hp = 200
        legacy.sword_tier = 4
    lives = legacy.lives
    trigger = EncounterTrigger(
        TriggerKind.PVP_DUEL, first, -1, 1, MoonPhase.NEW_MOON, opponent_knight_id=opponent
    )
    session._start_combat(trigger, first)
    active, loader = session.active_encounter, session.fight_loader
    assert active is not None and loader is not None
    audit = FightAudit(active.runtime.scene, loader.knight_rules, int(opponent) + 1)
    for _ in range(6000):
        word = audit.input_word() if win else 0
        player = PlayerInput(
            dx=int(bool(word & RIGHT)) - int(bool(word & LEFT)),
            dy=int(bool(word & DOWN)) - int(bool(word & UP)),
            fire=bool(word & FIRE),
        )
        session.step_tick(InputFrame(players={first: player}))
        if session.active_encounter is None:
            break
    assert session.active_encounter is None and session.scene == GameScene.OVERWORLD
    assert session.last_defeat_mask == (2 if win else 1)
    assert session.knight_combat[first].lives == lives - int(not win)
    assert not any(job.active for job in active.runtime.fight.engine.jobs)
