"""Original fight data through the production host runtime (T11.8b2, §10.22)."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from harddaysengine.app.fight_audit import TROGG_ARENAS, FightAudit
from harddaysengine.app.fight_runtime import FightLoader, FightUnavailableError
from harddaysengine.app.scene_assets import GameAssetBundle
from harddaysengine.assets.adf import DiskSet
from harddaysengine.sim.actor import Actor
from harddaysengine.sim.fight_ai.brawler import BRAWLER_TYPES
from harddaysengine.sim.inventory import Inventory
from harddaysengine.sim.m68k import s16
from harddaysengine.sim.rng import Rng

if TYPE_CHECKING:
    from pathlib import Path

    from harddaysengine.app.fight_runtime import FightRuntime

pytestmark = pytest.mark.adf


@pytest.fixture(scope="module")
def loader(adf_dir: Path) -> FightLoader:
    return FightLoader(GameAssetBundle(DiskSet.from_directory(adf_dir)))


def _records() -> list[Actor]:
    return [
        Actor(hp=50, hp_max=50, strength=3, sword=0x19, daggers=99, inventory=i + 1)
        for i in range(4)
    ]


def _play(loader: FightLoader, name: str) -> tuple[FightRuntime, list[str]]:
    arena = next(a for a in loader.defs.arenas if a.name == name)
    records = _records()
    runtime = loader.creature(
        arena, "fo1.t", records, Actor(), {i: Inventory() for i in range(1, 5)}, Rng(0x2468ACE1), 1
    )
    assert runtime.fight.actor(1) is records[0]
    assert runtime.fight.actor(1).input_port == 2
    audit = FightAudit(runtime.scene, loader.knight_rules, 6)
    snapshots = []
    for vbl in range(1, 36001):
        targets = [
            j.owner
            for j in runtime.fight.engine.jobs
            if j.active and j.type in BRAWLER_TYPES and s16(runtime.fight.actor(j.owner).hp) > 0
        ]
        word = 0
        if targets:
            audit.opponent = targets[0]
            word = audit.input_word()
        output = runtime.step_vbl(vbl, [0, word])
        if output is not None:
            snapshots.append(json.dumps(runtime.snapshot(), sort_keys=True))
        if runtime.finished:
            break
    return runtime, snapshots


@pytest.mark.parametrize("name", TROGG_ARENAS)
def test_runtime_runs_original_trogg_waves_through_outcome(loader: FightLoader, name: str) -> None:
    runtime, snapshots = _play(loader, name)
    assert runtime.finished, name
    assert s16(runtime.fight.actor(1).hp) > 0
    assert runtime.fight.total == 0
    assert runtime.fight.end_timer == 0
    assert len(snapshots) > 35


def test_runtime_replay_has_identical_full_fight_snapshots(loader: FightLoader) -> None:
    # The sound-picker cells persist for the session (§10.32): replay from the same cells.
    cells = replace(loader.sound_cells)
    first, trace = _play(loader, TROGG_ARENAS[0])
    loader.sound_cells = replace(cells)
    second, replay = _play(loader, TROGG_ARENAS[0])
    assert first.finished and second.finished
    assert trace == replay


def test_unmigrated_handlers_and_unknown_offsets_are_rejected(loader: FightLoader) -> None:
    arena = next(a for a in loader.defs.arenas if a.name == "dragon")
    arena = replace(arena, creature=replace(arena.creature, actor_type=0x10))
    with pytest.raises(FightUnavailableError, match="not available yet"):
        loader.creature(arena, "fo1.t", _records(), Actor(), {}, Rng(1), 1)
    with pytest.raises(FightUnavailableError, match="unknown arena"):
        loader.arena_for(-1)


def test_duel_uses_two_real_knight_jobs_and_opposite_joystick_ports(loader: FightLoader) -> None:
    records = _records()
    runtime = loader.duel(
        "fo1.t", records, Actor(), {i: Inventory() for i in range(1, 5)}, Rng(5), 1, 2
    )
    for vbl in range(1, 7):
        output = runtime.step_vbl(vbl, [0, 0])
    assert output is not None and output.tick.draws
    assert runtime.fight.actor(1).input_port == 2
    assert runtime.fight.actor(2).input_port == 1
    assert runtime.fight.find_job(1) is not None
    assert runtime.fight.find_job(2) is not None
    assert any(draw.cel.casefold().startswith("he") for draw in output.tick.draws)


@pytest.mark.parametrize("loser", [1, 2])
def test_duel_death_scripts_end_the_runtime_for_either_fighter(
    loader: FightLoader, loser: int
) -> None:
    records = _records()
    runtime = loader.duel(
        "fo1.t", records, Actor(), {i: Inventory() for i in range(1, 5)}, Rng(5), 1, 2
    )
    actor = runtime.fight.actor(loser)
    actor.hp = 0
    runtime.fight.restart_job(loser, runtime.fight.table_long(actor.hurt_scripts, 8))
    for vbl in range(1, 1201):
        runtime.step_vbl(vbl, [0, 0])
        if runtime.finished:
            break
    assert runtime.finished
