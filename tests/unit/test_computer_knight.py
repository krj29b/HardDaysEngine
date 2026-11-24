"""Synthetic computer knight decisions, movement and draw order (§10.31)."""

from dataclasses import replace

import pytest

from harddaysengine.sim.actor import Actor
from harddaysengine.sim.fight import KEEP, Fight, HandlerResult, Restart
from harddaysengine.sim.fight_ai.approach import ApproachRules
from harddaysengine.sim.fight_ai.knight import ComputerKnightRules, computer_knight_handler
from harddaysengine.sim.rng import Rng
from tests.unit.knight_setup import RULES as KNIGHT, make_fight

RULES = ComputerKnightRules(
    ApproachRules((7, 0) * 4, (0, 3) * 4, (0, 9) * 4, 65533, 3),
    bytes([0, 100, 255]),
    73,
    81,
    89,
    111,
)


def _fight() -> tuple[Fight, Actor, Actor]:
    fight, _ = make_fight()
    fight.second = 2
    fight.rng = Rng(0x2468ACE1)
    actor, target = fight.actor(2), fight.actor(1)
    actor.type, actor.reach_x, actor.too_close_x, actor.depth_reach = 0x10, 160, 0, 4
    actor.x, target.x = 200, 130
    return fight, actor, target


def _think(fight: Fight, rules: ComputerKnightRules = RULES) -> HandlerResult:
    return computer_knight_handler(rules, KNIGHT, fight, 2)


@pytest.mark.parametrize(
    ("distance", "last", "daggers", "action"),
    [
        (73, 0, 1, 8),
        (73, 8, 1, 32),
        (81, 8, 1, 32),
        (82, 32, 1, 4),
        (89, 4, 0, 4),
        (89, 4, 1, 12),
        (90, 0, 1, 12),
    ],
)
def test_ordered_attack_boundaries_and_previous_action(
    distance: int, last: int, daggers: int, action: int
) -> None:
    fight, actor, target = _fight()
    actor.action, actor.daggers, target.x = last, daggers, actor.x - distance
    result = _think(fight)
    assert actor.action == action and isinstance(result, Restart)
    assert result.script == fight.table_long(actor.action_scripts, action)
    expected = Rng(0x2468ACE1)
    expected.percent()
    expected.percent()
    assert fight.rng.state == expected.state and fight.ai_last_action == last


@pytest.mark.parametrize(("opponent_action", "guard"), [(8, 16), (32, 28), (4, 28)])
def test_guard_consumes_only_one_draw_and_does_not_reset_cooldown(
    opponent_action: int, guard: int
) -> None:
    fight, actor, target = _fight()
    target.action, actor.cooldown = opponent_action, 0x1234
    result = _think(fight)
    assert actor.action == guard and isinstance(result, Restart)
    expected = Rng(0x2468ACE1)
    expected.percent()
    assert fight.rng.state == expected.state and actor.cooldown == 0x1234


@pytest.mark.parametrize(("left", "daggers", "expected"), [(True, 1, 12), (False, 0, 0)])
def test_failed_guard_preserves_true_distance_for_attack_selection(
    left: bool, daggers: int, expected: int
) -> None:
    fight, actor, target = _fight()
    actor.reach_x, actor.daggers = 200, daggers
    target.x = actor.x + (-150 if left else 150)
    target.facing = 1 if left else 3
    target.action = 8
    _think(fight)
    assert actor.action == expected


def test_latched_block_skips_guard_draw_and_walk_releases_latch() -> None:
    fight, actor, target = _fight()
    actor.behaviour_flags = 0x85
    actor.action, target.x = 4, 110
    actor.too_close_x = 100
    _think(fight)
    expected = Rng(0x2468ACE1)
    expected.percent()
    assert fight.rng.state == expected.state and actor.x == 207
    assert actor.behaviour_flags == 5 and actor.action == 4


def test_declined_attack_consumes_discarded_taunt_draw_and_keeps_action() -> None:
    fight, actor, _ = _fight()
    fight.ai_day = 1
    actor.action = 0x14
    _think(fight)
    expected = Rng(0x2468ACE1)
    for _ in range(3):
        expected.draw()
    assert fight.rng.state == expected.state and actor.action == 0x14


@pytest.mark.parametrize(
    ("gloated", "distance", "action"), [(False, 73, 8), (True, 73, 0x14), (False, 170, 0x14)]
)
def test_down_opponent_clears_only_high_cooldown_without_random(
    gloated: bool, distance: int, action: int
) -> None:
    fight, actor, target = _fight()
    target.hp, target.x = 0xFFFF, actor.x - distance
    actor.cooldown, actor.action = 0xABCD, 0x14
    fight.gloated = gloated
    _think(fight)
    assert actor.cooldown == 0xCD and actor.action == action and fight.rng.state == 0x2468ACE1


def test_depth_approach_walks_without_random_using_old_phase_and_override() -> None:
    fight, actor, target = _fight()
    actor.anim_phase, actor.behaviour_flags = 1, 0x80
    target.y, target.x = 130, 10
    _think(fight)
    assert (actor.x, actor.y, actor.anim_phase, actor.behaviour_flags) == (193, 103, 2, 0)
    assert fight.rng.state == 0x2468ACE1


def test_down_step_uses_bounded_four_phase_table_without_last_action_overlap() -> None:
    fight, actor, target = _fight()
    actor.anim_phase, actor.action = 4, 0x14
    target.y = 130
    _think(fight)
    assert actor.x == 200 and actor.y == 109 and actor.anim_phase == 1
    assert fight.ai_last_action == 0x14


@pytest.mark.parametrize(
    ("kind", "own", "target_action", "keep"),
    [
        (12, 8, 28, True),
        (16, 8, 28, False),
        (16, 32, 28, True),
        (16, 4, 28, True),
        (12, 32, 16, False),
    ],
)
def test_ai_hit_reply_has_its_own_stop_conditions(
    kind: int, own: int, target_action: int, keep: bool
) -> None:
    fight, actor, target = _fight()
    actor.hit_target, actor.action = 1, own
    target.type, target.action = kind, target_action
    result = _think(fight)
    assert (result == KEEP) == keep
    assert fight.rng.state == 0x2468ACE1


def test_hurt_precedes_hit_and_always_uses_knight_rule_even_for_other_types() -> None:
    fight, actor, target = _fight()
    actor.attacker = actor.hit_target = 1
    target.type, target.action, target.strength = 0, 8, 7
    result = _think(fight)
    assert actor.hp == 10 and isinstance(result, Restart)
    assert result.script == fight.table_long(actor.hurt_scripts, 8)
    assert fight.rng.state == 0x2468ACE1


def test_first_computer_targets_second_instead_of_itself() -> None:
    fight, actor, target = _fight()
    fight.first, fight.second = 2, 1
    target.hp = 0
    _think(fight)
    assert actor.action == 8


def test_day_is_clamped_to_odds_bounds_and_signed_odds_are_used() -> None:
    fight, actor, _ = _fight()
    # Day past table end clamps to final entry (255 -> signed -1, always attacks).
    fight.ai_day = 99
    _think(fight)
    assert actor.action == 8
    # Negative day clamps to index 0 (odds = 0).
    fight, actor, _ = _fight()
    fight.ai_day = 0xFFFF
    _think(fight, replace(RULES, odds=bytes([100, 0])))
    assert actor.action == 0
    # Empty odds table is rejected at construction.
    with pytest.raises(ValueError, match="non-empty"):
        replace(RULES, odds=b"")


@pytest.mark.parametrize(("distance", "action"), [(111, 16), (112, 12)])
def test_guard_range_is_inclusive_and_equal_odds_can_guard(distance: int, action: int) -> None:
    fight, actor, target = _fight()
    actor.daggers = 1
    target.x, target.action = actor.x - distance, 8
    rules = replace(RULES, odds=bytes([44]))
    _think(fight, rules)
    assert actor.action == action


def test_equal_attack_odds_declines_and_consumes_taunt_draw() -> None:
    fight, actor, _ = _fight()
    _think(fight, replace(RULES, odds=bytes([77])))
    expected = Rng(0x2468ACE1)
    for _ in range(3):
        expected.draw()
    assert fight.rng.state == expected.state and actor.action == 0


def test_same_facing_skips_guard_but_still_consumes_its_draw() -> None:
    fight, actor, target = _fight()
    target.action, target.facing = 8, 3
    _think(fight)
    assert actor.action == 8
    expected = Rng(0x2468ACE1)
    expected.percent()
    expected.percent()
    assert fight.rng.state == expected.state
