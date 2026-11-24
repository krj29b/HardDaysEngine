"""Original round boundaries, clock and skip rules on synthetic records (§11.19)."""

from dataclasses import asdict

import pytest

from harddaysengine.sim.actor import Actor
from harddaysengine.sim.m68k import u16
from harddaysengine.sim.rewards import wizard_gift
from harddaysengine.sim.turns import TurnCells, TurnResult, advance, clock, overnight
from harddaysengine.sim.types import KnightId
from tests.unit.map_setup import make_world
from tests.unit.turn_setup import TURN_RULES


def test_round_visits_four_records_once_and_suspends_before_setup() -> None:
    world = make_world(tuple(KnightId))
    seen = [world.current_knight_id]
    for _ in range(3):
        world.travel = world.ambush = True
        world.spent = world.budget
        assert advance(world, TURN_RULES) == TurnResult.PLAY
        seen.append(world.current_knight_id)
        assert not world.travel and not world.ambush and world.spent == 0
        assert world.current_actor.engaged_with == 0
    assert seen == list(KnightId)
    world.actors[0].engaged_with = 4
    assert advance(world, TURN_RULES) == TurnResult.NEXT_DAY
    assert world.turns.pending_setup and world.moon_subcount == 1
    assert world.actors[0].engaged_with == 4
    assert advance(world, TURN_RULES) == TurnResult.PLAY
    assert world.current_knight_id == KnightId.RED and world.actors[0].engaged_with == 0


def test_human_selection_defines_record_order_and_computers_follow() -> None:
    world = make_world((KnightId.GREEN, KnightId.BLUE))
    assert world.turn_order == (KnightId.GREEN, KnightId.BLUE, KnightId.RED, KnightId.YELLOW)
    assert world.human_order == (KnightId.GREEN, KnightId.BLUE)
    assert [a.kind for a in world.ordered_actors()] == [2, 0, 4, 4]
    assert advance(world, TURN_RULES) == TurnResult.PLAY
    assert world.current_knight_id == KnightId.BLUE
    assert advance(world, TURN_RULES) == TurnResult.PLAY
    assert world.current_actor.kind == 4


def test_day_and_loaded_moon_advance_only_every_four_rounds() -> None:
    world = make_world(tuple(KnightId))
    for round_index in range(32):
        for _ in range(3):
            assert advance(world, TURN_RULES) == TurnResult.PLAY
        assert advance(world, TURN_RULES) == TurnResult.NEXT_DAY
        expected_day = (round_index + 1) // 4
        assert world.day == expected_day
        assert world.moon_subcount == (round_index + 1) % 4
        expected_moon = 45 if expected_day == 0 else TURN_RULES.moons[expected_day & 7]
        assert world.moon_frame == expected_moon
        assert advance(world, TURN_RULES) == TurnResult.PLAY
    assert world.total_turns == 128 and world.moon_index == 0


def test_all_frogged_humans_keep_round_screens_until_curse_decays() -> None:
    world = make_world()
    world.current_actor.frog_days = 2
    for actor in world.actors[1:]:
        actor.lives = 0
    assert advance(world, TURN_RULES) == TurnResult.NEXT_DAY
    assert world.current_actor.frog_days == 1 and not world.game_over
    assert advance(world, TURN_RULES) == TurnResult.NEXT_DAY
    assert world.current_actor.frog_days == 0
    assert advance(world, TURN_RULES) == TurnResult.PLAY


def test_no_human_lives_ends_even_with_living_computers() -> None:
    world = make_world()
    world.actors[0].lives = 0
    world.turn_index = 3
    assert advance(world, TURN_RULES) == TurnResult.NEXT_DAY
    assert advance(world, TURN_RULES) == TurnResult.GAME_OVER
    assert world.game_over and world.turns.skipped == 1


def test_dead_and_frogged_records_skip_but_signed_ff_frog_does_not() -> None:
    world = make_world(tuple(KnightId))
    world.actors[1].lives = 255
    world.actors[2].frog_days = 1
    world.actors[3].frog_days = 255
    assert advance(world, TURN_RULES) == TurnResult.PLAY
    assert world.current_knight_id == KnightId.YELLOW
    assert world.turns.skipped == 1


@pytest.mark.parametrize(
    ("recency", "frog", "hp", "maximum", "expected_recency", "expected_frog", "expected_hp"),
    [
        (255, 2, 20, 40, 255, 2, 25),
        (5, 2, 20, 40, 0, 1, 25),
        (200, 1, 40, 40, 0, 0, 40),
        (250, 0, 40, 40, 0, 0, 40),
        (130, 0, 40, 40, 120, 0, 40),
        (0, 0, 45, 40, 0, 0, 40),
        (0, 0, 20000, 40, 0, 0, 40),
        (0, 0, 65535, 40, 0, 0, 10),
    ],
)
def test_overnight_decay_and_guarded_regeneration(
    recency: int,
    frog: int,
    hp: int,
    maximum: int,
    expected_recency: int,
    expected_frog: int,
    expected_hp: int,
) -> None:
    actor = Actor(recency=recency, frog_days=frog, hp=hp, hp_max=maximum)
    overnight(actor)
    assert (actor.recency, actor.frog_days, actor.hp) == (
        expected_recency,
        expected_frog,
        expected_hp,
    )


def test_day_turnover_gifts_precede_life_loss_and_overnight_in_record_order() -> None:
    world = make_world((KnightId.GREEN,))
    expected = make_world((KnightId.GREEN,))
    for test_world in (world, expected):
        test_world.moon_subcount = 3
        for actor in test_world.actors:
            actor.hp = 10
            actor.life_loss = 1
            actor.gold = 65535
    for actor in expected.ordered_actors():
        if actor.kind == 4:
            actor.recency = 255
            actor.gold = 0
            wizard_gift(
                actor,
                expected.inventories[actor.inventory],
                expected.prng,
                TURN_RULES.gifts,
                expected.gifts,
            )
        actor.lives -= 1
    for actor in (*expected.ordered_actors(), expected.dragon):
        overnight(actor)
    clock(world, TURN_RULES)
    assert world.prng.state == expected.prng.state
    assert [asdict(a) for a in world.actors] == [asdict(a) for a in expected.actors]
    assert asdict(world.gifts) == asdict(expected.gifts)
    assert world.day == 1


def test_clock_word_wrap_and_regeneration_includes_dragon() -> None:
    world = make_world(tuple(KnightId))
    world.day, world.moon_subcount = 65535, 3
    world.dragon.hp, world.dragon.hp_max = 1, 21
    clock(world, TURN_RULES)
    assert world.day == 0 and world.dragon.hp == 6
    world.moon_subcount = u16(-1)
    clock(world, TURN_RULES)
    assert world.moon_subcount == 0 and world.day == 0


def test_new_day_requires_release_press_release() -> None:
    cells = TurnCells()
    for held in (True, True, False, False, True, True):
        assert not cells.wait_fire(held)
    assert cells.wait_fire(False)
