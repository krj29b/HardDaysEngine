"""Computer decisions: draw order, persistent lines and original quirks (§11.20)."""

from dataclasses import asdict, replace
from unittest.mock import patch

import pytest

from pymoonstone.sim.actor import Actor
from pymoonstone.sim.ai_map import build_roam, computer_frame, distance, pick_opponent, walk_step
from pymoonstone.sim.ai_map_state import ShopWish, WalkLine
from pymoonstone.sim.ai_shop import ARMOUR, DAGGERS, LIFE, SWORD, purchase, shop_wish
from pymoonstone.sim.fight_moves import DOWN, LEFT, RIGHT, UP
from pymoonstone.sim.inventory import Inventory
from pymoonstone.sim.m68k import s16
from pymoonstone.sim.overworld import Arrival, OverworldState
from pymoonstone.sim.rng import Rng
from pymoonstone.sim.turns import TurnResult, advance
from tests.unit.ai_map_setup import AI_RULES
from tests.unit.map_setup import make_world
from tests.unit.turn_setup import TURN_RULES


def _world() -> OverworldState:
    world = make_world()
    world.turn_rules, world.ai_rules = TURN_RULES, AI_RULES
    assert advance(world, TURN_RULES) == TurnResult.PLAY
    return world


def test_roam_equal_rows_reverse_and_cache_until_turn_change() -> None:
    world = _world()
    for lair in world.lairs:
        lair.map_x, lair.map_y = 100, 100
    build_roam(world)
    assert [row[1] for row in world.ai.roam] == list(reversed(range(24)))
    saved = world.ai.roam.copy()
    world.lairs[0].map_x = 0
    build_roam(world)
    assert world.ai.roam == saved
    world.ai.walk.initialized = True
    advance(world, TURN_RULES)
    assert not world.ai.built and not world.ai.walk.initialized
    build_roam(world)
    assert world.ai.roam != saved


def test_hidden_lairs_sort_last_and_hidden_pick_engages_without_draw() -> None:
    world = _world()
    for lair in world.lairs:
        lair.map_x = 65535
    build_roam(world)
    assert all(row[0] == 65535 for row in world.ai.roam)
    world.ai.hidden = True
    with patch.object(Rng, "draw", side_effect=AssertionError("unexpected draw")):
        pick_opponent(world, AI_RULES)
    assert world.current_actor.engaged_with == 1


def test_opponent_skips_computers_and_draws_for_null_candidate() -> None:
    world = _world()
    with patch.object(Rng, "draw", return_value=126) as draw:
        pick_opponent(world, AI_RULES)
    assert draw.call_count == 2 and world.current_actor.engaged_with == 0
    world.actors[0].lives = 0
    with patch.object(Rng, "draw", return_value=25) as draw:
        pick_opponent(world, AI_RULES)
    assert draw.call_count == 1 and world.current_actor.engaged_with == 1


def test_neighbors_preserve_record_order_for_equal_distance() -> None:
    world = _world()
    for actor in world.actors:
        actor.map_x = actor.map_y = 100
    with patch.object(Rng, "draw", return_value=126):
        pick_opponent(world, AI_RULES)
    assert [row[1] for row in world.ai.neighbors] == [1, 3, 4]


@pytest.mark.parametrize(
    ("target", "words"),
    [
        ((4, 2), [RIGHT, RIGHT, RIGHT | DOWN, RIGHT]),
        ((-4, -2), [LEFT, LEFT, LEFT | UP, LEFT]),
        ((2, 4), [DOWN, DOWN, DOWN | RIGHT, DOWN]),
        ((0, 0), [RIGHT] * 4),
    ],
)
def test_walk_line_error_sign_and_zero_target(target: tuple[int, int], words: list[int]) -> None:
    line = WalkLine()
    actor = Actor()
    assert [walk_step(line, actor, target) for _ in range(4)] == words
    saved = asdict(line)
    walk_step(line, actor, (300, 180))
    assert line.dx == saved["dx"] and line.dy == saved["dy"]


def test_frame_draws_roam_then_stat_then_opponent_before_terrain() -> None:
    world = _world()
    world.current_actor.progress = 3
    world.current_actor.gold = 0
    world.current_actor.daggers = 10
    # Two rejected roam zeros; row2; stat row0; failed human; failed null.
    with patch.object(Rng, "draw", side_effect=[0, 0, 2, 0, 126, 126]) as draw:
        computer_frame(world, AI_RULES)
    assert draw.call_count == 6 and world.spent == 1
    assert world.current_actor.strength == 3 and world.current_actor.progress == 0
    assert world.current_actor.derived_endurance == 8
    line = asdict(world.ai.walk)
    with patch.object(Rng, "draw", side_effect=[3, 126, 126]) as draw:
        computer_frame(world, AI_RULES)
    assert draw.call_count == 3
    assert world.ai.walk.dx == line["dx"]


def test_terrain_block_costs_ai_budget_but_does_not_initialize_walk() -> None:
    world = _world()
    world.rules = replace(world.data(), terrain=bytes([255]) * 1000)
    position = world.current_actor.map_x, world.current_actor.map_y
    with patch.object(Rng, "draw", side_effect=[1, 126, 126]):
        computer_frame(world, AI_RULES)
    assert world.blocked and world.spent == 1 and world.move == 0
    assert not world.ai.walk.initialized
    assert (world.current_actor.map_x, world.current_actor.map_y) == position


@pytest.mark.parametrize(("lives", "hp", "expected"), [(3, 40, 4), (4, 9, 4), (4, 10, 4)])
def test_rest_uses_lives_or_strict_quarter_threshold(lives: int, hp: int, expected: int) -> None:
    world = _world()
    actor = world.current_actor
    actor.lives, actor.hp, actor.hp_max, actor.life_loss = lives, hp, 40, 1
    world.inventories[actor.inventory].counts[0] = 1
    with patch.object(Rng, "draw", side_effect=[1, 126, 126]):
        computer_frame(world, AI_RULES)
    used = lives <= 3 or hp < 10
    assert actor.lives == expected
    assert actor.hp == (40 if used else hp)
    assert actor.life_loss == (0 if used else 1)
    assert world.inventories[actor.inventory].byte(0) == int(not used)


def test_scroll_transfer_then_speed_uses_post_transfer_inventory_and_keeps_opponent() -> None:
    world = _world()
    actor, opponent = world.current_actor, world.actors[0]
    actor.engaged_with = 1
    actor.map_x, actor.map_y = 0, 0
    opponent.map_x, opponent.map_y = 300, 150
    inv = world.inventories[actor.inventory]
    inv.counts[7] = 1
    world.inventories[1].counts[5] = 1
    budget, progress = world.budget, actor.progress
    with patch.object(Rng, "draw", return_value=1):
        computer_frame(world, AI_RULES)
    assert inv.byte(14) == inv.byte(10) == 0
    assert world.inventories[1].byte(10) == 0
    assert actor.engaged_with == 1 and actor.progress == progress
    assert world.budget == budget * 2


def test_town_arrival_suppresses_meeting_and_charges_final_frame() -> None:
    world = _world()
    actor = world.current_actor
    actor.engaged_with, actor.gold, actor.lives = 1, 100, 1
    world.arrivals[:3] = [Arrival(1, 1), Arrival(25, 25), Arrival()]
    position = actor.map_x, actor.map_y
    with patch.object(Rng, "draw", return_value=1):
        assert computer_frame(world, AI_RULES) is None
    assert world.turn_over and world.spent == world.budget + 1
    assert actor.gold == 74 and actor.lives == 2 and world.ai.town is None
    assert (actor.map_x, actor.map_y) == position and world.ai.walk.initialized


def test_arrival_stops_at_zero_or_seventh_row_and_duel_continuation_draws_nothing() -> None:
    world = _world()
    world.current_actor.engaged_with = 1
    world.current_actor.gold = 0
    world.arrivals[:] = [Arrival(30, 2)] * 7 + [Arrival(1, 1), Arrival(), Arrival()]
    with patch.object(Rng, "draw", return_value=1):
        assert computer_frame(world, AI_RULES) is None
        world.arrivals[:2] = [Arrival(1, 1), Arrival()]
        assert computer_frame(world, AI_RULES) == Arrival(1, 1)
    assert world.ai.pending
    with patch.object(Rng, "draw", side_effect=AssertionError("repeated decision")):
        computer_frame(world, AI_RULES)
    assert not world.ai.pending and world.turn_over


@pytest.mark.parametrize(
    ("gold", "lives", "armour", "sword", "daggers", "expected"),
    [
        (10, 1, 27, 22, 0, None),
        (27, 2, 27, 22, 0, (26, LIFE, 99)),
        (77, 4, 27, 22, 0, (77, ARMOUR, 30)),
        (51, 4, 27, 22, 0, (51, ARMOUR, 29)),
        (31, 4, 27, 22, 0, (31, ARMOUR, 28)),
        (30, 4, 30, 22, 9, (11, SWORD, 24)),
        (26, 4, 30, 22, 9, (11, SWORD, 99)),
        (30, 4, 30, 23, 9, None),
        (30, 4, 30, 24, 5, (2, DAGGERS, 99)),
    ],
)
def test_shop_priorities_and_stale_sword_item(
    gold: int,
    lives: int,
    armour: int,
    sword: int,
    daggers: int,
    expected: tuple[int, int, int] | None,
) -> None:
    actor = Actor(gold=gold, lives=lives, armour=armour, sword=sword, daggers=daggers)
    wish = ShopWish(item=99)
    assert shop_wish(actor, wish, AI_RULES) == (expected is not None)
    if expected:
        assert (wish.cost, wish.kind, wish.item) == expected


def test_dagger_purchase_subtracts_high_byte_and_word_cap_is_ineffective() -> None:
    actor = Actor(gold=32, daggers=9, type=16)
    purchase(actor, Inventory(), ShopWish(2, DAGGERS), AI_RULES)
    assert actor.daggers == 11 and s16(actor.gold) == -482
    assert actor.gold & 255 == 30


def test_distance_uses_signed_word_subtraction_and_wrapped_sum() -> None:
    assert distance(65535, 0, 1, 0) == 2
    assert distance(0, 0, 32767, 1) == -32768


def test_zero_seed_roam_retry_raises_instead_of_hanging() -> None:
    world = _world()
    world.prng.state = 0
    with pytest.raises(ValueError, match="selection did not terminate"):
        computer_frame(world, AI_RULES)


def test_dragon_return_runs_only_budget_tail_without_another_decision_or_charge() -> None:
    world = _world()
    world.ai.after_dragon = True
    world.dragon_encounter = True
    world.spent = world.budget
    before = world.spent
    with patch.object(Rng, "draw", side_effect=AssertionError("repeated decision")):
        computer_frame(world, AI_RULES)
    assert world.turn_over and not world.ai.after_dragon and not world.dragon_encounter
    assert world.spent == before


def test_turn_change_clears_town_and_continuations_without_erasing_stale_wish() -> None:
    world = _world()
    world.ai.town, world.ai.pending, world.ai.after_dragon = (100, 100), True, True
    world.ai.wish.item = 123
    advance(world, TURN_RULES)
    assert world.ai.town is None and not world.ai.pending and not world.ai.after_dragon
    assert world.ai.wish.item == 123


def test_capped_stats_spend_no_points_and_consume_no_stat_draw() -> None:
    world = _world()
    actor = world.current_actor
    actor.strength = actor.constitution = actor.endurance = 5
    actor.progress = 6
    with patch.object(Rng, "draw", side_effect=[1, 126, 126]) as draw:
        computer_frame(world, AI_RULES)
    assert draw.call_count == 3 and actor.progress == 6


def test_nearer_town_uses_tile_distance_but_engaged_opponent_remains_walk_target() -> None:
    world = _world()
    actor, opponent = world.current_actor, world.actors[0]
    actor.gold, actor.lives, actor.engaged_with = 100, 1, 1
    actor.map_x, actor.map_y = 290, 140
    opponent.map_x, opponent.map_y = 200, 180
    world.tile()
    with patch.object(Rng, "draw", return_value=1):
        computer_frame(world, AI_RULES)
    assert world.ai.town == (290, 150)
    assert world.ai.walk.dx == 90 and world.ai.walk.dy == 40


def test_pending_meeting_exhausts_recomputed_budget_before_final_charge() -> None:
    world = _world()
    world.ai.pending = True
    world.budget, world.spent = 200, 100
    with patch.object(Rng, "draw", side_effect=AssertionError("repeated decision")):
        computer_frame(world, AI_RULES)
    assert world.spent == 201 and world.turn_over


def test_saturated_speed_threshold_is_signed_and_doubles_with_word_wrapping() -> None:
    world = _world()
    actor = world.current_actor
    actor.engaged_with, actor.derived_endurance = 1, 255
    actor.map_x, actor.map_y = world.actors[0].map_x, world.actors[0].map_y
    world.arrivals[:] = [Arrival() for _ in range(10)]
    world.inventories[actor.inventory].counts[5] = 1
    world.budget = 40000
    with patch.object(Rng, "draw", return_value=1):
        computer_frame(world, AI_RULES)
    assert world.budget == 14464 and world.inventories[actor.inventory].byte(10) == 0
