"""Approach and step helpers (ARCHITECTURE.md §10.20)."""

from __future__ import annotations

import dataclasses

import pytest

from pymoonstone.sim.fight import KEEP, FightError, Restart
from pymoonstone.sim.fight_ai.approach import (
    ApproachRules,
    abs_word,
    approach,
    take_step,
    walk,
)
from pymoonstone.sim.fight_moves import DOWN, FIRE, LEFT, RIGHT, UP
from pymoonstone.sim.m68k import u16
from tests.unit.brawler_setup import (
    APPROACH,
    REACH,
    SIDE_SCRIPTS,
    TROGG_TABLES,
    UP_SCRIPTS,
    make_trogg_fight,
)
from tests.unit.knight_setup import RULES

PROBES = RULES.probes


def test_abs_word_is_the_68000_negate() -> None:
    assert abs_word(5) == 5
    assert abs_word(u16(-5)) == 5
    assert abs_word(0x8000) == 0x8000


def test_a_step_table_must_hold_sixteen_words() -> None:
    with pytest.raises(ValueError, match="16 words"):
        dataclasses.replace(APPROACH, up=(1, 2))
    assert isinstance(APPROACH, ApproachRules)


@pytest.mark.parametrize(("x", "facing"), [(200, 3), (50, 1), (100, 3)])
def test_approach_faces_the_target(x: int, facing: int) -> None:
    fight, handle = make_trogg_fight(x)
    trogg = fight.actor(handle)
    trogg.facing = 0
    approach(fight, trogg, fight.actor(1))
    assert trogg.facing == facing


@pytest.mark.parametrize(
    ("y", "bit", "near"), [(102, 0, 1), (96, 0, 1), (110, UP, 0), (90, DOWN, 0)]
)
def test_approach_corrects_the_depth_outside_depth_reach(y: int, bit: int, near: int) -> None:
    fight, handle = make_trogg_fight(150, y)
    trogg = fight.actor(handle)
    approach(fight, trogg, fight.actor(1))
    assert trogg.input & (UP | DOWN) == bit
    assert fight.approach.near == near
    assert fight.approach.vertical == 1 - near


@pytest.mark.parametrize(("x", "bit"), [(120, RIGHT), (80, LEFT), (130, RIGHT), (100, LEFT)])
def test_too_close_backs_away(x: int, bit: int) -> None:
    fight, handle = make_trogg_fight(x)
    trogg = fight.actor(handle)
    flag, distance = approach(fight, trogg, fight.actor(1))
    assert flag
    assert distance == u16(100 - x)
    assert trogg.input == bit


@pytest.mark.parametrize(("y", "flag", "bit"), [(100, False, 0), (120, True, UP)])
def test_in_reach_returns_the_distance_and_the_depth_flag(y: int, flag: bool, bit: int) -> None:
    fight, handle = make_trogg_fight(150, y)
    trogg = fight.actor(handle)
    assert approach(fight, trogg, fight.actor(1)) == (flag, 50)
    assert trogg.input == bit


@pytest.mark.parametrize(("x", "bit"), [(200, LEFT), (20, RIGHT)])
def test_out_of_reach_walks_towards_the_target(x: int, bit: int) -> None:
    fight, handle = make_trogg_fight(x)
    trogg = fight.actor(handle)
    assert approach(fight, trogg, fight.actor(1)) == (True, REACH)
    assert trogg.input == bit


def test_approach_resets_the_cells() -> None:
    fight, handle = make_trogg_fight(150)
    cells = fight.approach
    cells.dx = cells.dy = cells.dy_override = cells.near = 9
    approach(fight, fight.actor(handle), fight.actor(1))
    assert (cells.dx, cells.dy, cells.dy_override, cells.near) == (0, 0, 0, 1)


def _walk(x: int, word: int, phase: int = 0, y: int = 100) -> tuple[Restart, int, int, int]:
    """Walk the Trogg (facing left) by ``word``; the result, new x, y and phase."""
    fight, handle = make_trogg_fight(x, y)
    trogg = fight.actor(handle)
    trogg.input, trogg.anim_phase = word, phase
    result = walk(fight, APPROACH, handle, PROBES, KEEP)
    assert isinstance(result, Restart)
    return result, trogg.x, trogg.y, trogg.anim_phase


def test_walking_forwards_takes_the_side_step_of_the_old_phase() -> None:
    result, x, y, phase = _walk(200, LEFT)
    assert (x, y, phase) == (198, 100, 1)
    assert result == Restart(SIDE_SCRIPTS[1], 198, 0, 100, 3)


def test_backing_away_runs_the_cycle_backwards_and_skips_empty_phases() -> None:
    result, x, _, phase = _walk(200, RIGHT)
    assert (x, phase) == (202, 2)
    assert result.script == SIDE_SCRIPTS[2]


def test_a_phase_without_a_script_is_skipped() -> None:
    result, x, _, phase = _walk(200, LEFT, phase=2)
    assert phase == 0
    assert x == 180
    assert result.script == SIDE_SCRIPTS[0]


def test_a_vertical_step_drops_the_sideways_part() -> None:
    result, x, y, phase = _walk(200, UP)
    assert (x, y, phase) == (200, 89, 1)
    assert result.script == UP_SCRIPTS[1]
    result, x, y, _ = _walk(200, DOWN)
    assert (x, y) == (200, 112)


def test_a_diagonal_takes_the_depth_override_and_the_side_block() -> None:
    result, x, y, phase = _walk(200, UP | LEFT, phase=1)
    assert (x, y, phase) == (194, 94, 2)
    assert result.script == SIDE_SCRIPTS[2]


def test_a_block_without_scripts_raises() -> None:
    tables = dataclasses.replace(TROGG_TABLES, walk_scripts=(0,) * 24)
    fight, handle = make_trogg_fight(200, tables=tables)
    fight.actor(handle).input = LEFT
    with pytest.raises(FightError, match="no script"):
        walk(fight, APPROACH, handle, PROBES, KEEP)


def test_walking_without_input_stands_and_without_a_direction_raises() -> None:
    fight, handle = make_trogg_fight(200)
    assert walk(fight, APPROACH, handle, PROBES, KEEP) is KEEP
    fight.actor(handle).input = FIRE
    with pytest.raises(FightError, match="no direction"):
        walk(fight, APPROACH, handle, PROBES, KEEP)


def test_the_movement_probe_stops_a_blocked_step() -> None:
    fight, handle = make_trogg_fight(200)
    trogg, knight = fight.actor(handle), fight.actor(2)
    knight.x, knight.box_min_x, knight.box_max_x = 180, 160, 200
    knight.box_min_y = trogg.box_min_y = 60
    knight.box_max_y = trogg.box_max_y = 100
    trogg.box_min_x, trogg.box_max_x = 201, 220
    fight.start_job(TROGG_TABLES.idle_script, 2, 1, (180, 0, 100), 3, 0x0C)
    trogg.input, trogg.anim_phase = LEFT, 1
    result = walk(fight, APPROACH, handle, PROBES, KEEP)
    assert result == Restart(TROGG_TABLES.idle_script, 200, 0, 100, 3)
    assert trogg.anim_phase == 0
    trogg.input = UP | LEFT
    result = walk(fight, APPROACH, handle, PROBES, KEEP)
    assert isinstance(result, Restart)
    assert (trogg.x, trogg.y, trogg.input) == (200, 94, UP)
    assert result.script == SIDE_SCRIPTS[1]


def test_take_step_uses_the_cells_of_the_helpers() -> None:
    fight, handle = make_trogg_fight(200)
    trogg = fight.actor(handle)
    trogg.input = RIGHT
    fight.approach.dx, fight.approach.dy = 5, 3
    result = take_step(fight, handle, 0x20, 1, PROBES)
    assert result == Restart(UP_SCRIPTS[1], 205, 0, 100, 3)
    assert fight.approach.block == 0x20
