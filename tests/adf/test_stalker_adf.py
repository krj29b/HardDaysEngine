"""Real Troll hurt-script deaths, sparks, shake, replay and host setup (§10.27)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from harddaysengine.app.fight_audit import build_stalker_audit
from harddaysengine.app.fight_runtime import FightLoader, FightRuntime
from harddaysengine.app.scene_assets import GameAssetBundle
from harddaysengine.assets.adf import DiskSet
from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.hunk import parse_hunk_executable
from harddaysengine.assets.stalker_rules import parse_stalker_rules
from harddaysengine.sim.actor import Actor
from harddaysengine.sim.fight import FIGHTER_DIED, Fight, HandlerResult, Restart, fighter_died
from harddaysengine.sim.fight_ai.stalker import STALKER_TYPE
from harddaysengine.sim.inventory import Inventory
from harddaysengine.sim.m68k import s16
from harddaysengine.sim.rng import Rng

if TYPE_CHECKING:
    from pathlib import Path

    from harddaysengine.sim.fight_script import RoutineCall

pytestmark = pytest.mark.adf


@pytest.fixture(scope="module")
def bundle(adf_dir: Path) -> GameAssetBundle:
    return GameAssetBundle(DiskSet.from_directory(adf_dir))


def _play(bundle: GameAssetBundle, *, defend: bool) -> tuple[Fight, list[str], list[int], set[str]]:
    audit = build_stalker_audit(bundle)
    f = audit.scene.fight
    hashes, deaths, draws = [], [], set()
    if not defend:
        f.actor(1).hp = 20
    fixed_hurt = FightLoader(bundle).stalker_rules.hurt_script
    original = f.handlers[STALKER_TYPE]

    def logged(fight: Fight, owner: int) -> HandlerResult:
        me = fight.actor(owner)
        hurt = me.attacker != 0 and me.hit_target == 0
        result = original(fight, owner)
        if hurt:
            assert isinstance(result, Restart) and result.script == fixed_hurt
        return result

    def died(fight: Fight, call: RoutineCall | None) -> None:
        assert call is not None and call.owner == audit.opponent
        deaths.append(s16(fight.actor(call.owner).hp))
        fighter_died(fight, call)

    f.routines[FIGHTER_DIED], f.handlers[STALKER_TYPE] = died, logged
    runtime = FightRuntime(audit.scene, 1)
    for frame in range(1000):
        f.ports[0] = audit.input_word() if defend else 0
        output = audit.scene.advance(frame * 6)
        draws |= {d.cel for d in output.tick.draws}
        hashes.append(
            hashlib.sha256(json.dumps(runtime.snapshot(), sort_keys=True).encode()).hexdigest()
        )
        if not f.fight_active:
            break
    assert not f.fight_active
    return f, hashes, deaths, draws


def test_seeded_win_uses_fixed_hurt_death_branch_and_sparks(bundle: GameAssetBundle) -> None:
    f, hashes, deaths, draws = _play(bundle, defend=True)
    assert f.total == f.alive_now == 0 and s16(f.actor(1).hp) > 0
    assert len(deaths) == 1 and deaths[0] <= 0
    assert {"Troll1.cel", "Troll2.cel", "blo.cel"} <= draws
    assert hashes == _play(bundle, defend=True)[1]


def test_idle_knight_loses_to_original_troll(bundle: GameAssetBundle) -> None:
    f, _, deaths, _ = _play(bundle, defend=False)
    assert s16(f.actor(1).hp) <= 0 and deaths == []


def test_original_empty_first_wave_spawns_once_but_keeps_cap_two(bundle: GameAssetBundle) -> None:
    loader = FightLoader(bundle)
    arena = next(a for a in loader.defs.arenas if a.name == "troll")
    assert loader.arena_for(arena.handler_offsets[0]) == arena
    assert arena.first_wave == () and not arena.one_at_a_time
    records = [Actor(hp=60, hp_max=60, strength=4) for _ in range(4)]
    runtime = loader.creature(arena, "fo1.t", records, Actor(), {1: Inventory()}, Rng(7), 1)
    f = runtime.fight
    jobs = [j for j in f.engine.jobs if j.active and j.type == STALKER_TYPE]
    assert len(jobs) == f.alive_now == 1 and f.max_alive == arena.alive_cap == 2
    assert jobs[0].x == arena.next_creature[1].x
    assert f.hop_table == 0


def test_handler_instruction_and_spark_frame_list_are_checked(bundle: GameAssetBundle) -> None:
    loader = FightLoader(bundle)
    executable = loader.mog.executable
    section = executable.sections[40]
    changed = section.data[:0x4E0] + b"\x00\x00" + section.data[0x4E2:]
    sections = list(executable.sections)
    sections[40] = replace(section, data=changed)
    with pytest.raises(MogVersionError, match="entry instruction"):
        parse_stalker_rules(
            replace(executable, sections=tuple(sections)), loader.defs.frame_set_lists
        )
    with pytest.raises(MogVersionError, match="unknown frame list"):
        parse_stalker_rules(executable, ())


def test_unknown_executable_is_rejected(bundle: GameAssetBundle) -> None:
    executable = parse_hunk_executable("mog", bundle.disk_set.read_file("mog"))
    section = executable.sections[0]
    changed = replace(section, data=b"\x00\x00" + section.data[2:])
    with pytest.raises(MogVersionError):
        parse_stalker_rules(replace(executable, sections=(changed, *executable.sections[1:])), ())
