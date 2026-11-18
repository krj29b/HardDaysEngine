"""Sheet transfer, item, pointer and dispatch regressions (§12.2-9/26)."""

from dataclasses import asdict, replace
from unittest.mock import patch

import pytest

from harddaysengine.sim.m68k import u16
from harddaysengine.sim.rng import Rng
from harddaysengine.sim.sheet_layout import button
from harddaysengine.sim.sheet_types import SheetRegion, SheetState
from harddaysengine.sim.sheets import click, close, redraw, step
from tests.unit.sheet_setup import RULES, sheet


def _region(state: SheetState, field: int, side: int = 0) -> SheetRegion:
    return next(r for r in state.regions if r.field == field and r.side == side and not r.id)


def test_two_bad_jingles_are_separated_by_their_blocking_waits() -> None:
    world, state = sheet()
    rules = replace(RULES, jingle_wait=8)
    world.inventories[1].counts[9] = 1
    redraw(state, rules, world)
    with patch.object(Rng, "draw", return_value=13):
        click(state, rules, world, _region(state, 18))
    assert state.done and state.wait == 8 and state.sounds == ["bad"]
    assert state.pending_sounds == ["bad"]
    for _ in range(7):
        step(state, rules, world, 1, 0, True)
        assert state.sounds == []
    step(state, rules, world, 1, 0, True)
    assert state.wait == 8 and state.sounds == ["bad"] and not state.pending_sounds
    for _ in range(8):
        step(state, rules, world, 0, 0, True)
    assert state.wait == 0 and state.sounds == []
    assert world.inventories[1].byte(18) == 0


@pytest.mark.parametrize("number", [1, 2, 3, 8, 9, 10, 11, 32])
def test_stat_buttons_arm_on_every_sheet_and_exact_five_is_excluded(number: int) -> None:
    world, state = sheet(number)
    actor = world.actors[0]
    actor.progress, actor.strength = RULES.stat_price * 2, 5
    redraw(state, RULES, world)
    assert not button(state, RULES, actor, _region(state, 70))[1] & 64
    actor.strength = 6
    redraw(state, RULES, world)
    region = _region(state, 70)
    assert button(state, RULES, actor, region)[1] & 64
    click(state, RULES, world, region)
    assert actor.strength == 7 and actor.progress == RULES.stat_price


def test_constitution_purchase_raises_current_hp_then_recalculates() -> None:
    world, state = sheet()
    actor = world.actors[0]
    actor.hp, actor.progress = 20, RULES.stat_price
    click(state, RULES, world, _region(state, 71))
    assert actor.constitution == 4 and actor.hp == 30 and actor.hp_max == 50
    assert actor.progress == 0


@pytest.mark.parametrize("slot", [0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22])
def test_lair_item_transfer_masks_ring_and_sharp_sword(slot: int) -> None:
    world, state = sheet(2)
    inv, loot, actor = world.inventories[1], world.lairs[0].loot, world.actors[0]
    inv.counts[slot // 2], loot.counts[slot // 2] = 1, 3
    actor.hp = 20
    redraw(state, RULES, world)
    click(state, RULES, world, _region(state, slot, 1))
    assert loot.byte(slot) == (0 if slot >= 20 else 2)
    expected = 3 if slot >= 20 else (1 if slot == 4 else 2)
    assert inv.byte(slot) == expected and state.changes == 1
    if slot == 6:
        assert actor.hp == 40 and actor.hp_max == 80
    if slot == 4:
        assert actor.sword == 25


def test_read_only_lair_regions_cannot_transfer_but_keep_plain_text() -> None:
    world, state = sheet(2)
    state.read_only = True
    world.lairs[0].gold = 20
    world.lairs[0].loot.counts[0] = 2
    redraw(state, RULES, world)
    before = asdict(world.lairs[0])
    click(state, RULES, world, _region(state, 0, 1))
    click(state, RULES, world, _region(state, 74, 1))
    assert asdict(world.lairs[0]) == before and state.changes == 0


@pytest.mark.parametrize(
    ("gold", "source", "expected", "remaining"),
    [(150, 20, 153, 17), (153, 20, 153, 20), (160, 20, 180, 0), (65530, 20, 14, 0)],
)
def test_gold_stops_at_exact_cap_and_wraps(
    gold: int, source: int, expected: int, remaining: int
) -> None:
    world, state = sheet(2)
    world.actors[0].gold, world.lairs[0].gold = gold, source
    redraw(state, RULES, world)
    click(state, RULES, world, _region(state, 74, 1))
    assert world.actors[0].gold == expected and world.lairs[0].gold == remaining


def test_living_meeting_one_take_becomes_status_dead_meeting_can_be_emptied() -> None:
    for lives, expected in ((1, 9), (0, 1), (127, 9), (128, 1), (255, 1)):
        world, state = sheet(1)
        world.actors[1].lives = lives
        world.inventories[2].counts[0] = 3
        redraw(state, RULES, world)
        click(state, RULES, world, _region(state, 0, 1))
        assert state.number == expected
        assert state.changes == int(expected == 9)


def test_acquisition_returns_to_previous_sheet_and_cycles_original_record_order() -> None:
    world, state = sheet()
    world.inventories[1].counts[7] = 1
    redraw(state, RULES, world)
    click(state, RULES, world, _region(state, 14))
    assert state.number == 8 and state.other == 2 and state.last_item == 14
    cycle = next(r for r in state.regions if r.id == 8)
    click(state, RULES, world, cycle)
    assert state.other == 3
    world.inventories[3].counts[1] = 2
    redraw(state, RULES, world)
    click(state, RULES, world, _region(state, 2, 1))
    assert state.number == 9 and state.changes == 0
    assert world.inventories[1].byte(2) == 1


def test_wyrm_selects_target_on_close_and_has_plain_take_buttons() -> None:
    world, state = sheet()
    world.inventories[1].counts[8] = 1
    redraw(state, RULES, world)
    click(state, RULES, world, _region(state, 16))
    assert state.number == 11 and state.hand_over
    assert button(state, RULES, world.actors[0], _region(state, 74, 1))[1] == 3
    close(state, world)
    assert world.dragon.engaged_with == 2 and not state.hand_over


@pytest.mark.parametrize(
    ("slot", "roll", "expected_draws", "sound"),
    [
        (10, 12, 1, ["bad"]),
        (10, 13, 1, ["good"]),
        (12, 17, 3, ["bad"]),
        (12, 18, 1, ["good"]),
        (18, 13, 1, ["bad", "bad"]),
        (18, 14, 1, ["good"]),
    ],
)
def test_item_gamble_thresholds_draw_order_and_sounds(
    slot: int, roll: int, expected_draws: int, sound: list[str]
) -> None:
    world, state = sheet()
    world.inventories[1].counts[slot // 2] = 1
    redraw(state, RULES, world)
    budget = world.budget
    with patch.object(Rng, "draw", return_value=roll) as draws:
        click(state, RULES, world, _region(state, slot))
    assert draws.call_count == expected_draws and state.sounds == sound
    if slot == 10:
        assert world.budget == (budget >> 1 if roll == 12 else u16(budget << 1))
    if slot == 12:
        assert state.done and world.ambush == (roll > 17)
    if slot == 18:
        assert state.done and state.bad_luck == int(roll == 13)


def test_rest_and_gem_draw_no_randomness_and_save_mode_position() -> None:
    world, state = sheet()
    actor = world.actors[0]
    actor.hp, actor.life_loss = actor.hp_max, 1
    lives = actor.lives
    world.inventories[1].counts[0] = world.inventories[1].counts[1] = 1
    redraw(state, RULES, world)
    with patch.object(Rng, "draw", side_effect=AssertionError("unexpected random draw")):
        click(state, RULES, world, _region(state, 0))
        assert actor.lives == lives + 1 and actor.life_loss == 0
        click(state, RULES, world, _region(state, 2))
    assert world.travel and state.done
    assert (world.saved_x, world.saved_y) == (actor.map_x, actor.map_y)


def test_held_fire_repeats_only_while_inventory_region_exists_and_pointer_clamps() -> None:
    world, state = sheet()
    world.inventories[1].counts[0] = 2
    redraw(state, RULES, world)
    region = _region(state, 0)
    state.pointer_x, state.pointer_y = region.x, region.y
    for _ in range(3):
        step(state, RULES, world, 0, 0, True)
    assert world.inventories[1].byte(0) == 0
    assert not any(r.field == 0 and not r.id for r in state.regions)
    for _ in range(200):
        step(state, RULES, world, 1, -1, False)
    assert (state.pointer_x, state.pointer_y) == (314, 0)


def test_armour_uses_byte_indexed_decoded_word_and_dagger_top_up() -> None:
    world, state = sheet(1)
    first, second = world.actors[:2]
    first.armour, second.armour, first.hp, second.hp = 29, 30, 20, 40
    redraw(state, RULES, world)
    click(state, RULES, world, _region(state, 92, 1))
    assert first.armour == 30 and second.armour == 27
    assert first.hp == 31 and second.hp == 29
    state.number, state.changes = 1, 0
    first.daggers, second.daggers = 8, 5
    redraw(state, RULES, world)
    click(state, RULES, world, _region(state, 76, 1))
    assert first.daggers == 10 and second.daggers == 3


def test_offer_drops_passive_inventory_items_but_leaves_keys_and_moonstones() -> None:
    world, state = sheet(3)
    inv = world.inventories[1]
    inv.counts[3], inv.counts[10], inv.counts[11] = 1, 15, 15
    redraw(state, RULES, world)
    assert _region(state, 6).type == 12
    assert _region(state, 20).type == _region(state, 22).type == 1
    click(state, RULES, world, _region(state, 20))
    assert inv.byte(20) == 15 and not state.done
    click(state, RULES, world, _region(state, 6))
    assert inv.byte(6) == 0 and state.done and state.last_item == 6
