"""Original wave order, signed comparisons, lanes, warning isolation and settlement (§10.24)."""

import pytest

from harddaysengine.sim.fight_setup import (
    FightWarnings,
    SpawnLanes,
    lane_depth,
    scale_wave,
    settle_defeats,
    wave_class,
)
from tests.unit.fight_setup_data import SETUP
from tests.unit.knight_setup import RULES, make_fight


@pytest.mark.parametrize(
    ("hp", "strength", "alive", "total"),
    [
        (24, 2, 1, 5),
        (25, 2, 1, 6),
        (54, 2, 1, 6),
        (55, 2, 2, 6),
        (85, 2, 2, 7),
        (85, 3, 3, 7),
        (85, 0xFF, 2, 7),
        (0xFFFF, 2, 1, 5),
    ],
)
def test_threshold_bonuses_use_signed_record_widths(
    hp: int, strength: int, alive: int, total: int
) -> None:
    fight, _ = make_fight()
    actor = fight.actor(1)
    actor.hp_max, actor.strength = hp, strength
    fight.max_alive, fight.total = 1, 5
    scale_wave(fight, RULES, SETUP, ())
    assert (fight.max_alive, fight.total) == (alive, total)
    assert actor.action == 8


def test_lair_override_precedes_cut_and_single_concurrency_limit() -> None:
    fight, _ = make_fight()
    fight.actor(1).hp_max, fight.actor(1).strength = 100, 4
    fight.max_alive, fight.total = 2, 10
    scale_wave(fight, RULES, SETUP, (-2,) * 8, lair_count=3, single=True, cap=2)
    assert (fight.max_alive, fight.total) == (1, 5)


def test_nonpositive_cut_is_not_stored_and_cap_is_applied_after_bonuses() -> None:
    fight, _ = make_fight()
    fight.actor(1).hp_max, fight.actor(1).strength = 100, 4
    fight.max_alive, fight.total = 2, 10
    scale_wave(fight, RULES, SETUP, (3,) * 8, lair_count=2, cap=2)
    assert (fight.max_alive, fight.total) == (2, 2)


@pytest.mark.parametrize(("hp", "column"), [(20, 0), (100, 4), (200, 5)])
def test_wave_column_adds_quarter_hp_before_shifts_and_clamp(hp: int, column: int) -> None:
    fight, _ = make_fight()
    actor = fight.actor(1)
    actor.hp_max, actor.strength, actor.sword, actor.inventory = hp, 0, 0, 0
    assert wave_class(fight, RULES, SETUP) == column
    assert actor.action == 8


def test_lane_cursor_cycles_and_anchor_calculation_does_not_consume_it() -> None:
    cursor = SpawnLanes()
    assert cursor.next(110, SETUP) == 145
    assert lane_depth(3, 110, SETUP) == 120
    assert cursor.counter == 1
    assert [cursor.next(110, SETUP) for _ in range(3)] == [95, 120, 145]
    assert lane_depth(3, 220, SETUP) == 32943


def test_warnings_are_once_per_fighter_persist_after_healing_and_change_only_owned_entries() -> (
    None
):
    fight, _ = make_fight()
    fight.actor(1).hp, fight.actor(1).kind = 9, 0
    original = [0] * 32
    original[15], original[31] = 0xA00, 0xABC
    warning = FightWarnings(original.copy())
    warning.register(fight, 0, SETUP)
    warning.register(fight, 0, SETUP)
    assert len(warning.ramps) == 3
    warning.step(SETUP)
    assert warning.colors == original
    warning.step(SETUP)
    assert warning.colors[6:9] == [0x111] * 3
    fight.actor(1).hp = 100
    warning.register(fight, 0, SETUP)
    for _ in range(60):
        warning.step(SETUP)
    assert len(warning.ramps) == 3
    assert all(warning.colors[i] == original[i] for i in range(32) if i not in (6, 7, 8))


def test_duel_second_warning_uses_its_class_and_own_palette_entries() -> None:
    fight, _ = make_fight()
    fight.actor(1).hp = 10
    fight.actor(2).hp, fight.actor(2).kind = 8, 99
    warning = FightWarnings([0] * 32)
    warning.register(fight, 2, SETUP)
    assert not warning.started[0] and warning.started[1]
    assert [(r.index, r.target) for r in warning.ramps] == [(9, 0xEEE), (10, 0xFFF), (11, 0x111)]


def test_ramps_swap_start_and_target_at_each_arrival() -> None:
    fight, _ = make_fight()
    fight.actor(1).hp = 9
    warning = FightWarnings([0] * 32)
    warning.register(fight, 0, SETUP)
    for _ in range(4):
        warning.step(SETUP)
    assert warning.colors[6] == 0x222
    assert (warning.ramps[0].target, warning.ramps[0].back) == (0, 0x222)
    for _ in range(4):
        warning.step(SETUP)
    assert warning.colors[6] == 0


@pytest.mark.parametrize(
    ("first", "second", "mask"), [(1, 1, 0), (0, 1, 1), (1, 0xFFFF, 2), (0, 0xFFFF, 3)]
)
def test_defeat_mask_precedes_health_restoration_and_life_byte_wrap(
    first: int, second: int, mask: int
) -> None:
    fight, _ = make_fight()
    fight.actor(1).hp, fight.actor(2).hp = first, second
    for handle in (1, 2):
        fight.actor(handle).hp_max = 80
        fight.actor(handle).lives = 0
    assert settle_defeats(fight, 2) == mask
    for bit, handle in enumerate((1, 2)):
        if mask & (1 << bit):
            assert (fight.actor(handle).hp, fight.actor(handle).lives) == (80, 255)
