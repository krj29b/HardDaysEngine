"""Synthetic original map-dragon flight and attack rules (§11.19)."""

from dataclasses import replace

import pytest

from pymoonstone.sim.actor import Actor
from pymoonstone.sim.fight import Fight, FightConstants
from pymoonstone.sim.fight_script import ScriptImage
from pymoonstone.sim.map_dragon import (
    DragonFlight,
    attacks,
    automatic_loss,
    flight_step,
    settle,
    spawn,
)
from pymoonstone.sim.rng import Rng
from pymoonstone.sim.types import KnightId
from tests.unit.map_setup import make_world
from tests.unit.turn_setup import DRAGON, TURN_RULES


def test_flight_tracks_target_and_advances_decoded_phase() -> None:
    actor = Actor(x=30, y=50, facing=3, anim_phase=15)
    cells = DragonFlight(80, 3, 2)
    assert flight_step(actor, cells, 70, DRAGON) == DRAGON.scripts[0]
    assert (actor.x, actor.y, actor.anim_phase, cells.timer) == (33, 52, 0, 79)
    flight_step(actor, cells, 20, DRAGON)
    assert actor.y == 50


@pytest.mark.parametrize(
    ("x", "speed", "expected", "next_speed"),
    [
        (299, 3, 295, -3),
        (65527, -3, 65531, 3),
        (297, 3, 300, 3),
    ],
)
def test_flight_signed_edges_and_facing(x: int, speed: int, expected: int, next_speed: int) -> None:
    actor = Actor(x=x, y=50, facing=1)
    cells = DragonFlight(80, speed, 2)
    flight_step(actor, cells, 50, DRAGON)
    assert actor.x == expected and cells.speed_x == next_speed
    assert actor.facing == (3 if speed != next_speed else 1)


def test_hover_has_no_clipping_tracking_or_phase_change() -> None:
    actor = Actor(x=350, y=250, anim_phase=7)
    cells = DragonFlight(41, 3, 2)
    assert flight_step(actor, cells, 20, DRAGON) == DRAGON.hover
    assert (actor.x, actor.y, actor.anim_phase) == (353, 250, 7)
    cells.timer = 0
    flight_step(actor, cells, 20, DRAGON)
    assert cells.timer == 80 and actor.x == 295 and actor.y == 0


@pytest.mark.parametrize(("y", "target", "expected"), [(180, 190, 0), (0, 65535, 180)])
def test_flight_y_wraps(y: int, target: int, expected: int) -> None:
    actor = Actor(x=100, y=y)
    flight_step(actor, DragonFlight(80, 3, 2), target, DRAGON)
    assert actor.y == expected


def test_spawn_uses_shared_rng_for_live_record_order_and_real_job() -> None:
    world = make_world((KnightId.GREEN,))
    world.day, world.dragon.lives = 2, 1
    for actor in world.actors:
        actor.lives = 0
    world.current_actor.lives = 1
    expected = Rng(world.prng.state)
    while world.turn_order[expected.draw() & 3] != KnightId.GREEN:
        continue
    fight = Fight(
        ScriptImage(bytes(256), {}),
        {},
        {},
        world.actors,
        world.dragon,
        FightConstants(1, 65535),
        rng=world.prng,
    )
    assert spawn(world, fight, DRAGON)
    assert world.prng.state == expected.state
    assert world.dragon.engaged_with == int(KnightId.GREEN) + 1
    assert world.dragon.type == 40 and world.dragon.frame_list == 3
    job = fight.engine.jobs[0]
    assert (job.script, job.x, job.height, job.depth) == (DRAGON.scripts[0], *DRAGON.initial_job)
    assert job.owner == 5 and job.type == 40
    assert not spawn(world, fight, DRAGON)


def test_spawn_suppression_and_no_living_target_are_bounded() -> None:
    world = make_world()
    fight = Fight(
        ScriptImage(bytes(256), {}), {}, {}, world.actors, world.dragon, FightConstants(1, 65535)
    )
    world.day = 1
    assert not spawn(world, fight, DRAGON)
    world.day, world.dragon.lives = 2, 255
    assert not spawn(world, fight, DRAGON)
    world.dragon.lives = 1
    for actor in world.actors:
        actor.lives = 0
    assert not spawn(world, fight, DRAGON)
    assert not world.dragon_active and not any(j.active for j in fight.engine.jobs)


def test_attack_requires_target_and_previous_touch_even_on_last_budget_frame() -> None:
    world = make_world()
    world.dragon_active = True
    world.dragon.lives, world.dragon.engaged_with = 1, 1
    world.dragon_touch[:] = [1]
    assert attacks(world)
    world.spent = world.budget
    world.frame(0)
    assert world.dragon_encounter and not world.turn_over
    world.dragon.engaged_with = 2
    assert not attacks(world)
    world.dragon.engaged_with, world.dragon.lives = 1, 255
    assert not attacks(world)


def test_automatic_computer_loss_and_frog_human_loss_transfer_loot() -> None:
    world = make_world()
    world.dragon.inventory = 5
    world.current_actor.kind = 4
    world.inventories[1].counts[0] = 2
    assert automatic_loss(world, TURN_RULES)
    assert world.current_actor.lives == 3
    assert world.inventories[5].counts[0] == 1 and world.inventories[1].counts[0] == 1
    world.current_actor.kind, world.current_actor.frog_days = 3, 1
    assert automatic_loss(world, TURN_RULES)
    assert world.current_actor.lives == 3
    world.current_actor.frog_days = 0
    assert not automatic_loss(world, TURN_RULES)
    assert world.spent == world.budget


def test_dragon_win_marks_slain_awards_decoded_progress_and_exhausts_budget() -> None:
    world = make_world()
    world.current_actor.progress = 65535
    settle(world, replace(TURN_RULES, dragon=replace(DRAGON, progress=3)), lost=False)
    assert world.dragon.lives == 255 and world.current_actor.progress == 2
    assert world.spent == world.budget
