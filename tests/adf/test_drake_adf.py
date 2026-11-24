"""Balok seeded outcomes, fixed-script death, counter execution and shared cells (§10.28)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from harddaysengine.app.fight_audit import build_drake_audit
from harddaysengine.app.fight_runtime import FightLoader, FightRuntime
from harddaysengine.app.game import GameSession
from harddaysengine.app.scene_assets import GameAssetBundle
from harddaysengine.assets.adf import DiskSet
from harddaysengine.assets.drake_rules import parse_drake_rules
from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.sim.actor import Actor
from harddaysengine.sim.fight import (
    FIGHTER_DIED,
    DrakePhase,
    Fight,
    HandlerResult,
    Restart,
    fighter_died,
)
from harddaysengine.sim.fight_ai.drake import DRAKE_TYPE
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


def _play(bundle: GameAssetBundle, *, defend: bool) -> tuple[Fight, list[str], list[int], set[int]]:
    audit = build_drake_audit(bundle)
    fight = audit.scene.fight
    if not defend:
        fight.actor(1).hp = 20
    deaths, hashes, scripts = [], [], set()
    original = fight.handlers[DRAKE_TYPE]
    rules = FightLoader(bundle).drake_rules

    def handler(f: Fight, owner: int) -> HandlerResult:
        result = original(f, owner)
        if isinstance(result, Restart):
            scripts.add(result.script)
        return result

    def died(f: Fight, call: RoutineCall | None) -> None:
        assert call is not None and call.owner == audit.opponent
        deaths.append(s16(f.actor(call.owner).hp))
        fighter_died(f, call)

    fight.handlers[DRAKE_TYPE], fight.routines[FIGHTER_DIED] = handler, died
    runtime = FightRuntime(audit.scene, 1)
    for frame in range(2500):
        fight.ports[1] = audit.input_word() if defend else 0
        output = audit.scene.advance(frame * 6)
        assert not output.skipped_calls
        hashes.append(
            hashlib.sha256(json.dumps(runtime.snapshot(), sort_keys=True).encode()).hexdigest()
        )
        if not fight.fight_active:
            break
    assert not fight.fight_active
    assert rules.scripts.flight in scripts
    return fight, hashes, deaths, scripts


def test_seeded_win_follows_fixed_hurt_death_and_replays(bundle: GameAssetBundle) -> None:
    fight, hashes, deaths, scripts = _play(bundle, defend=True)
    assert fight.total == fight.alive_now == 0 and s16(fight.actor(1).hp) > 0
    assert len(deaths) == 1 and deaths[0] <= 0
    assert FightLoader(bundle).drake_rules.scripts.hurt in scripts
    assert hashes == _play(bundle, defend=True)[1]


def test_idle_knight_loses_through_grab_and_finishing_scripts(bundle: GameAssetBundle) -> None:
    fight, _, deaths, scripts = _play(bundle, defend=False)
    assert s16(fight.actor(1).hp) <= 0 and deaths == []
    rules = FightLoader(bundle).drake_rules
    assert {rules.scripts.grab, rules.scripts.carry, rules.scripts.finish_first} <= scripts


def test_original_single_entry_wave_and_shared_state_between_encounters(
    bundle: GameAssetBundle,
) -> None:
    loader = FightLoader(bundle)
    arena = next(a for a in loader.defs.arenas if a.name == "balok")
    assert loader.arena_for(arena.handler_offsets[0]) == arena
    records = [Actor(hp=20, hp_max=20, strength=3) for _ in range(4)]
    first = loader.creature(arena, "fo1.t", records, Actor(), {1: Inventory()}, Rng(7), 1)
    f, waves = first.fight, first.waves
    assert waves is not None and f.alive_now == f.max_alive == 1
    entry = arena.next_creature[0]
    assert len(arena.next_creature) == 1
    assert next(j for j in f.engine.jobs if j.type == DRAKE_TYPE).x == entry.x & 0xFFFF
    waves.next(f)
    assert waves.toggle == 0 and len([j for j in f.engine.jobs if j.type == DRAKE_TYPE]) == 2
    f.drake.toggle = 1
    second = loader.creature(arena, "fo1.t", records, Actor(), {1: Inventory()}, Rng(8), 1)
    assert first.fight.drake is second.fight.drake is loader.drake
    session = GameSession()
    session.fight_loader = loader
    before = session.state_hash()
    loader.drake.toggle ^= 1
    assert session.state_hash() != before


def test_unidentified_executable_is_rejected(bundle: GameAssetBundle) -> None:
    loader = FightLoader(bundle)
    executable = loader.mog.executable
    section = executable.sections[0]
    changed = replace(section, data=b"\x00\x00" + section.data[2:])
    with pytest.raises(MogVersionError):
        parse_drake_rules(
            replace(executable, sections=(changed, *executable.sections[1:])),
            loader.caster_rules.flight,
            loader.stalker_rules.spark,
        )


@pytest.mark.parametrize(("balok_x", "expected_sign"), [(85, 1), (235, -1)])
def test_balok_strike_knocks_knight_away_from_balok(
    bundle: GameAssetBundle, balok_x: int, expected_sign: int
) -> None:
    """A Balok strike facing right knocks the knight right; facing left knocks left (§10.19)."""
    audit = build_drake_audit(bundle)
    f = audit.scene.fight
    for frame in range(15):
        audit.scene.advance(frame * 6)
    knight, balok = f.actor(1), f.actor(audit.opponent)
    knight_job, balok_job = f.find_job(1), f.find_job(audit.opponent)
    assert knight_job is not None and balok_job is not None
    knight.hp = knight.hp_max = 60
    knight.x = knight_job.x = 160
    knight.y = knight_job.depth = 120
    knight.facing = knight_job.facing = 3 if expected_sign > 0 else 1
    balok.x = balok_job.x = balok_x
    balok.y = balok_job.depth = 120
    balok.facing = balok_job.facing = 1 if expected_sign > 0 else 3
    f.drake.phase = DrakePhase.IDLE
    balok_job.running = False
    hit_x: int | None = None
    for frame in range(15, 40):
        audit.scene.advance(frame * 6)
        if s16(knight.hp) < 60:
            hit_x = s16(knight.x)
            break
    assert hit_x is not None and balok.action == 8
    for frame in range(40, 60):
        audit.scene.advance(frame * 6)
    delta = s16(knight.x) - hit_x
    assert delta * expected_sign > 0
