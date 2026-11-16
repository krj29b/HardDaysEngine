"""Movement probe, arena clip and obstacle probe (ARCHITECTURE.md §10.19)."""

from __future__ import annotations

import pytest

from pymoonstone.sim.actor import Actor
from pymoonstone.sim.fight import Fight, Wall
from pymoonstone.sim.fight_moves import (
    ALL_DIRECTIONS,
    DOWN,
    LEFT,
    RIGHT,
    UP,
    arena_clip,
    movement_mask,
    obstacle_probe,
)
from tests.unit.knight_setup import RULES, make_fight


def _box(actor: Actor, x: tuple[int, int], y: tuple[int, int]) -> None:
    actor.box_min_x, actor.box_max_x = x
    actor.box_min_y, actor.box_max_y = y


def _pair(other_x: int, other_y: int = 100) -> Fight:
    fight, _ = make_fight()
    mover, other = fight.actor(1), fight.actor(2)
    mover.x, other.x, other.y = 100, other_x, other_y
    _box(mover, (90, 110), (60, 100))
    _box(other, (other_x - 10, other_x + 10), (other_y - 40, other_y))
    fight.start_job(10, 1, 1, (0, 0, 0), 1, 0x0C)
    fight.start_job(10, 2, 1, (0, 0, 0), 3, 0x0C)
    return fight


def test_a_fighter_ahead_blocks_the_facing_direction() -> None:
    fight = _pair(118)
    assert movement_mask(fight, 1, 0, RULES.probes) == ALL_DIRECTIONS & ~(RIGHT | UP)
    fight.actor(1).facing = 3
    assert movement_mask(fight, 1, 0, RULES.probes) == ALL_DIRECTIONS & ~UP


def test_the_step_shifts_the_side_test() -> None:
    fight = _pair(135)
    assert movement_mask(fight, 1, 0, RULES.probes) == ALL_DIRECTIONS
    assert movement_mask(fight, 1, 15, RULES.probes) == ALL_DIRECTIONS
    assert movement_mask(fight, 1, 16, RULES.probes) == ALL_DIRECTIONS & ~RIGHT


def test_facing_left_blocks_left_when_the_other_is_on_the_left() -> None:
    fight = _pair(82)
    fight.actor(1).facing = 3
    assert movement_mask(fight, 1, 0, RULES.probes) == ALL_DIRECTIONS & ~(LEFT | UP)


@pytest.mark.parametrize(("other_y", "blocked"), [(110, DOWN), (90, UP), (116, 0), (85, UP)])
def test_the_vertical_probe_blocks_towards_a_near_fighter(other_y: int, blocked: int) -> None:
    fight = _pair(50, other_y)
    _box(fight.actor(2), (95, 105), (other_y - 40, other_y))
    assert movement_mask(fight, 1, 0, RULES.probes) == ALL_DIRECTIONS & ~blocked


def test_dead_fighters_and_fighters_without_a_box_do_not_block() -> None:
    fight = _pair(118)
    fight.actor(2).hp = 0
    assert movement_mask(fight, 1, 0, RULES.probes) == ALL_DIRECTIONS
    fight.actor(2).hp, fight.actor(2).box_min_x = 20, 0
    assert movement_mask(fight, 1, 0, RULES.probes) == ALL_DIRECTIONS
    fight.actor(2).box_min_x = 108
    job = fight.find_job(2)
    assert job is not None
    job.active = False
    assert movement_mask(fight, 1, 0, RULES.probes) == ALL_DIRECTIONS


@pytest.mark.parametrize(
    ("x", "y", "facing", "cleared"),
    [
        (100, 100, 1, 0),
        (281, 100, 1, RIGHT),
        (280, 100, 1, 0),
        (24, 100, 3, LEFT),
        (25, 100, 3, 0),
        (100, 147, 1, DOWN),
        (100, 20, 1, UP),
    ],
)
def test_the_arena_clip(x: int, y: int, facing: int, cleared: int) -> None:
    actor = Actor(x=x, y=y, facing=facing, input=0x0F)
    arena_clip(actor, RULES.clip)
    assert actor.input == 0x0F & ~cleared


def _walker(facing: int) -> tuple[Fight, Actor]:
    fight, _ = make_fight()
    actor = fight.actor(1)
    actor.facing, actor.input, actor.y = facing, 0x0F, 100
    _box(actor, (90, 110), (60, 100))
    return fight, actor


def test_a_wall_in_front_blocks_the_facing_direction() -> None:
    fight, actor = _walker(1)
    fight.walls = (Wall(105, 140, 101),)
    obstacle_probe(fight, actor, 5, 0, RULES.probes)
    assert actor.input == 0x0F & ~RIGHT
    fight, actor = _walker(3)
    fight.walls = (Wall(60, 95, 101),)
    obstacle_probe(fight, actor, -5, 0, RULES.probes)
    assert actor.input == 0x0F & ~LEFT


def test_a_wall_ending_at_the_feet_does_not_block_sideways() -> None:
    fight, actor = _walker(1)
    fight.walls = (Wall(105, 140, 100),)
    obstacle_probe(fight, actor, 5, 0, RULES.probes)
    assert actor.input == 0x0F


def test_a_deep_wall_blocks_going_up() -> None:
    fight, actor = _walker(1)
    fight.walls = (Wall(105, 140, 140),)
    obstacle_probe(fight, actor, 0, 0, RULES.probes)
    assert actor.input == 0x0F & ~(RIGHT | UP)
    fight, actor = _walker(1)
    fight.walls = (Wall(200, 240, 140),)
    obstacle_probe(fight, actor, 0, 0, RULES.probes)
    assert actor.input == 0x0F
