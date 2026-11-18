"""Real-disk Dragon setup, natural hurt/death outcomes, host wiring and replay (§10.30)."""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import fields, replace
from typing import TYPE_CHECKING

import pytest

from harddaysengine.app.fight_audit import build_dragon_audit
from harddaysengine.app.fight_runtime import FightLoader, FightRuntime
from harddaysengine.app.game import GameScene, GameSession
from harddaysengine.app.scene_assets import GameAssetBundle
from harddaysengine.assets.adf import DiskSet
from harddaysengine.assets.dragon_rules import parse_dragon_rules
from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.sim.actor import Actor
from harddaysengine.sim.fight import DRAGON, END_FIGHT, Fight, HandlerResult, Restart, end_fight
from harddaysengine.sim.fight_ai.dragon import DRAGON_TYPE, PART_TYPE
from harddaysengine.sim.inventory import Inventory
from harddaysengine.sim.m68k import s16
from harddaysengine.sim.overworld import EncounterTrigger, MoonPhase, TriggerKind
from harddaysengine.sim.rng import Rng
from harddaysengine.sim.types import KnightId
from tests.adf.test_fight_defs_adf import Walker, _arena_sets, _check

if TYPE_CHECKING:
    from pathlib import Path

    from harddaysengine.sim.fight_script import RoutineCall

pytestmark = pytest.mark.adf


@pytest.fixture(scope="module")
def bundle(adf_dir: Path) -> GameAssetBundle:
    return GameAssetBundle(DiskSet.from_directory(adf_dir))


def _play(bundle: GameAssetBundle, *, win: bool) -> tuple[Fight, list[str], list[int], set[int]]:
    audit = build_dragon_audit(bundle)
    f = audit.scene.fight
    if not win:
        f.actor(1).hp = 20
    hashes, ended, scripts = [], [], set()
    original = f.handlers[DRAGON_TYPE]

    def handler(fight: Fight, owner: int) -> HandlerResult:
        result = original(fight, owner)
        if isinstance(result, Restart):
            scripts.add(result.script)
        return result

    def end(fight: Fight, call: RoutineCall | None) -> None:
        if call is not None and call.owner == DRAGON:
            ended.append(s16(fight.actor(DRAGON).hp))
        end_fight(fight, call)

    f.handlers[DRAGON_TYPE], f.routines[END_FIGHT] = handler, end
    runtime = FightRuntime(audit.scene, 1)
    for frame in range(1500):
        f.ports[0] = audit.input_word() if win else 0
        output = audit.scene.advance(frame * 6)
        assert not output.skipped_calls
        hashes.append(
            hashlib.sha256(json.dumps(runtime.snapshot(), sort_keys=True).encode()).hexdigest()
        )
        if not f.fight_active:
            break
    assert not f.fight_active
    return f, hashes, ended, scripts


def test_equipped_knight_wins_through_original_hurt_death_and_replays(
    bundle: GameAssetBundle,
) -> None:
    f, hashes, ended, scripts = _play(bundle, win=True)
    assert s16(f.actor(1).hp) > 0 and s16(f.actor(DRAGON).hp) <= 0
    assert len(ended) == 1 and ended[0] <= 0
    loader = FightLoader(bundle)
    arena = loader.arena_for(DRAGON_TYPE)
    assert arena.creature.hurt_scripts[2] in scripts
    assert loader.dragon_rules.scripts.breath in scripts
    assert hashes == _play(bundle, win=True)[1]


def test_idle_knight_loses_while_dragon_lives(bundle: GameAssetBundle) -> None:
    f, _, ended, _ = _play(bundle, win=False)
    assert s16(f.actor(1).hp) <= 0 and s16(f.actor(DRAGON).hp) > 0 and not ended


def test_setup_matches_independent_operands_and_keeps_persistent_record(
    bundle: GameAssetBundle,
) -> None:
    loader = FightLoader(bundle)
    arena = loader.arena_for(DRAGON_TYPE)
    records = [Actor(hp=80, hp_max=80, strength=5) for _ in range(4)]
    dragon = Actor(hp=1, gold=37)
    runtime = loader.creature(arena, "fo1.t", records, dragon, {1: Inventory()}, Rng(7), 1)
    f = runtime.fight
    code = loader.mog.hunk_data(0)

    def word(pos: int) -> int:
        return int.from_bytes(code[pos : pos + 2], "big")

    assert f.actor(DRAGON) is dragon and dragon.gold == 37
    assert (dragon.x, dragon.height, dragon.y) == (word(0x452A), word(0x4530), word(0x4536))
    assert dragon.hp == dragon.hp_max == word(0x4680)
    assert f.total == f.max_alive == f.alive_now == 1 and runtime.waves is None
    for owner, yp in ((f.dragon_cells.link_a, 0x456C), (f.dragon_cells.link_b, 0x45BC)):
        actor = f.actor(owner)
        assert actor.type == PART_TYPE and actor.y == word(yp)
        assert (actor.damage_table, actor.hurt_scripts, actor.walk_scripts) == (
            dragon.damage_table,
            dragon.hurt_scripts,
            dragon.walk_scripts,
        )
        assert actor.hp == word(0x4680) and actor.hp != word(0x4572)
    before = runtime.snapshot()
    f.dragon_cells.saved_reach = 1
    assert runtime.snapshot() != before
    assert loader.dragon_rules.part_action == struct.unpack_from(">H", code, 0x6A0A)[0] == 0x14


def test_every_fixed_script_draw_uses_exact_loaded_frames(bundle: GameAssetBundle) -> None:
    loader = FightLoader(bundle)
    arena = loader.arena_for(DRAGON_TYPE)
    scripts = loader.dragon_rules.scripts
    entries = {getattr(scripts, field.name) for field in fields(scripts)}
    assert _check(Walker(loader.mog), entries, 2, _arena_sets(bundle, loader.defs, arena)) == []


@pytest.mark.parametrize("offset", [0, 0x6574, 0x69A4, 0x6632, 0x6992])
def test_bad_version_handler_helper_or_facing_is_rejected(
    bundle: GameAssetBundle, offset: int
) -> None:
    loader = FightLoader(bundle)
    executable = loader.mog.executable
    section = executable.sections[0]
    changed = replace(
        section, data=section.data[:offset] + b"\xff\xff" + section.data[offset + 2 :]
    )
    with pytest.raises(MogVersionError):
        parse_dragon_rules(
            replace(executable, sections=(changed, *executable.sections[1:])),
            loader.caster_rules.flight,
            loader.stalker_rules.spark,
            loader.defs.frame_set_lists,
        )


def test_dragon_intercept_uses_original_runtime_and_preserves_replay_hashes(
    bundle: GameAssetBundle,
) -> None:
    hashes = []
    for _ in range(2):
        session = GameSession(start_scene=GameScene.OVERWORLD)
        session.asset_bundle = bundle
        trigger = EncounterTrigger(
            TriggerKind.DRAGON_INTERCEPT,
            KnightId.RED,
            -1,
            1,
            MoonPhase.NEW_MOON,
            creature_type=DRAGON_TYPE,
        )
        session._start_combat(trigger, KnightId.RED)
        assert session.active_encounter is not None
        runtime = session.active_encounter.runtime
        assert runtime.fight.actor(DRAGON) is session.dragon_actor
        assert runtime.fight.find_job(DRAGON) is not None
        trace = []
        for vbl in range(1, 121):
            runtime.step_vbl(vbl, [0, 0])
            trace.append(session.state_hash())
        hashes.append(trace)
    assert hashes[0] == hashes[1]
