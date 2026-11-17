"""Real executable setup, entry release, scaled waves and warning ownership (§10.24)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from pymoonstone.app.fight_runtime import FightLoader
from pymoonstone.app.scene_assets import GameAssetBundle
from pymoonstone.assets.adf import DiskSet
from pymoonstone.sim.actor import Actor
from pymoonstone.sim.fight_setup import SpawnLanes
from pymoonstone.sim.inventory import Inventory
from pymoonstone.sim.palette_fx import pack_rgb12
from pymoonstone.sim.rng import Rng

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.adf


@pytest.fixture(scope="module")
def loader(adf_dir: Path) -> FightLoader:
    return FightLoader(GameAssetBundle(DiskSet.from_directory(adf_dir)))


def _knights(hp: int, maximum: int, strength: int = 3) -> list[Actor]:
    return [
        Actor(hp=hp, hp_max=maximum, strength=strength, kind=i, inventory=i + 1) for i in range(4)
    ]


def test_entry_script_releases_paused_creatures_and_session_lanes_continue(
    loader: FightLoader,
) -> None:
    arena = next(a for a in loader.defs.arenas if a.name == "trogg_axe")
    cursor = SpawnLanes()
    knights = _knights(90, 90, 4)
    runtime = loader.creature(
        arena,
        "fo1.t",
        knights,
        Actor(),
        {i: Inventory() for i in range(1, 5)},
        Rng(3),
        1,
        lanes=cursor,
    )
    fight = runtime.fight
    job = fight.find_job(1)
    assert job is not None and job.script == loader.setup_rules.enter_script
    assert fight.max_alive == arena.max_alive + 2
    assert fight.alive_now == fight.max_alive
    # Action-8 base damage is read independently; no sword or inventory bonus is equipped.
    damage = loader.defs.knight.damage_table[2] + 4
    column = max(0, min(15, ((damage + (90 >> 2)) >> 1) - 6)) >> 1
    candidate = arena.total + 2 - arena.cut_row[column]
    assert fight.total == (candidate if candidate > 0 else arena.total + 2)
    enemies = [j for j in fight.engine.jobs if j.active and j.owner != 1]
    assert enemies and all(j.paused for j in enemies)
    initial_cursor = cursor.counter
    for vbl in range(1, 601):
        runtime.step_vbl(vbl, [0, 0])
        if all(not j.paused for j in enemies):
            break
    assert all(not j.paused for j in enemies)
    assert cursor.counter == initial_cursor
    before = cursor.counter
    another = loader.creature(
        arena, "fo1.t", _knights(20, 20), Actor(), {}, Rng(3), 1, lanes=cursor
    )
    assert another.lanes is cursor
    assert cursor.counter == (before + 2 - 1) % 3 + 1


def test_lair_count_override_precedes_original_cut(loader: FightLoader) -> None:
    arena = next(a for a in loader.defs.arenas if a.name == "trogg_axe")
    runtime = loader.creature(
        arena, "fo1.t", _knights(20, 20), Actor(), {}, Rng(4), 1, lair_count=9
    )
    assert runtime.fight.max_alive == 1
    # Starting stats put this knight in the first cut column.
    assert runtime.fight.total == 9 - arena.cut_row[0]


def test_duel_warning_ramps_leave_backdrop_and_blood_entries_unchanged(loader: FightLoader) -> None:
    runtime = loader.duel("fo1.t", _knights(9, 50), Actor(), {}, Rng(5), 1, 2)
    baseline = [pack_rgb12(r >> 4, g >> 4, b >> 4) for r, g, b in runtime.scene.palette]
    for vbl in range(1, 181):
        runtime.step_vbl(vbl, [0, 0])
    warning = runtime.warnings
    assert warning is not None and warning.started == [True, True]
    assert {r.index for r in warning.ramps} == set(range(6, 12))
    assert all(warning.colors[i] == baseline[i] for i in range(32) if i not in range(6, 12))
    assert any(warning.colors[i] != baseline[i] for i in range(6, 12))
    assert runtime.fight.fight_active
