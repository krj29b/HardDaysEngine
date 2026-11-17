"""Synthetic signed arithmetic, panel and dispatch tests (ARCHITECTURE.md §12.29)."""

from dataclasses import asdict, replace
from unittest.mock import patch

import pytest

from pymoonstone.sim.sheet_types import SheetRegion
from pymoonstone.sim.sheets import click, redraw, step
from pymoonstone.sim.shop_types import ShopRules
from pymoonstone.sim.shops import market, smith
from tests.unit.sheet_setup import RULES, sheet

SHOP = ShopRules(
    ((13, 28, 10), (27, 29, 20), (41, 30, 30)),
    ((7, 23), (19, 24)),
    3,
    9,
    tuple(7 + i * 2 for i in range(12)),
    157,
    1,
    160,
)
SHEETS = replace(RULES, shop=SHOP)


@pytest.mark.parametrize("mask,index", [(1, 0), (2, 1), (4, 2), (7, 0)])
def test_armour_can_downgrade_and_uses_lowest_mask_bit(mask: int, index: int) -> None:
    world, state = sheet(5)
    actor = world.actors[0]
    actor.gold, actor.hp, actor.armour = 100, 5, 30
    smith(state, SHOP, world, SheetRegion(0, 0, 1, 1, type=10, mask=mask, field=92))
    price, code, hp = SHOP.armour[index]
    assert (actor.gold, actor.armour, actor.hp) == (100 - price, code, 5 + hp)
    assert state.changes == 0


@pytest.mark.parametrize("gold,bought", [(12, False), (13, True), (32768, False), (65535, False)])
def test_smith_affordability_is_signed_word(gold: int, bought: bool) -> None:
    world, state = sheet(5)
    actor = world.actors[0]
    actor.gold = gold
    smith(state, SHOP, world, SheetRegion(0, 0, 1, 1, type=10, mask=1, field=92))
    assert actor.gold == (gold - 13 if bought else gold)


@pytest.mark.parametrize("worn,bought", [(22, True), (23, False), (25, False), (0xFFFFFFFF, True)])
def test_smith_sword_rejects_equal_or_better_signed_long(worn: int, bought: bool) -> None:
    world, state = sheet(5)
    actor = world.actors[0]
    actor.sword, actor.gold = worn, 40
    smith(state, SHOP, world, SheetRegion(0, 0, 1, 1, type=10, mask=3, field=88))
    assert (actor.sword, actor.gold) == ((23, 33) if bought else (worn, 40))


@pytest.mark.parametrize("daggers,bought", [(8, True), (9, False), (10, False), (255, True)])
def test_dagger_cap_is_signed_byte_and_increment_wraps(daggers: int, bought: bool) -> None:
    world, state = sheet(5)
    actor = world.actors[0]
    actor.gold, actor.daggers = 20, daggers
    smith(state, SHOP, world, SheetRegion(0, 0, 1, 1, type=10, field=76))
    assert actor.daggers == ((daggers + 1) & 255 if bought else daggers)
    assert actor.gold == (17 if bought else 20)


@pytest.mark.parametrize("slot", range(0, 24, 2))
def test_market_round_trip_counted_and_mask_slots(slot: int) -> None:
    world, state = sheet(6)
    actor, own = world.actors[0], world.inventories[1]
    actor.gold, actor.hp = 100, 1
    world.market.counts[slot // 2] = 3
    region = SheetRegion(0, 0, 1, 1, type=1, mask=2, field=slot)
    state.pointer_x = 160
    market(state, SHOP, world, region)
    flag = slot in (20, 22)
    assert own.byte(slot) == (2 if flag else 1)
    assert world.market.byte(slot) == (1 if flag else 2)
    assert actor.gold == 100 - SHOP.prices[slot // 2]
    state.pointer_x = 159
    market(state, SHOP, world, region)
    assert own.byte(slot) == 0 and world.market.byte(slot) == 3
    assert actor.gold == 100 - SHOP.prices[slot // 2] + (SHOP.prices[slot // 2] >> 1)
    assert state.changes == 0


@pytest.mark.parametrize("gold,expected", [(156, 157), (32767, 32770), (65535, 2)])
def test_market_gold_wraps_before_signed_cap(gold: int, expected: int) -> None:
    world, state = sheet(6)
    actor = world.actors[0]
    actor.gold, state.pointer_x = gold, 159
    world.inventories[1].counts[0] = 1
    market(state, SHOP, world, SheetRegion(0, 0, 1, 1, type=5, field=0))
    assert actor.gold == expected


def test_buy_sound_precedes_refusal_and_wrong_record_write_is_omitted() -> None:
    world, state = sheet(6)
    actor = world.actors[0]
    state.pointer_x, actor.gold = 160, 0
    before = asdict(actor)
    world.market.counts[0] = 1
    market(state, SHOP, world, SheetRegion(0, 0, 1, 1, type=5, field=0))
    assert state.sounds == ["buy"] and asdict(actor) == before
    actor.gold, actor.hp_max = 100, 123
    market(state, SHOP, world, SheetRegion(0, 0, 1, 1, type=5, field=0))
    assert actor.hp_max == 123 and world.inventories[1].byte(0) == 1


def test_ring_buy_adds_hp_and_sale_recalculates_without_extra_healing() -> None:
    world, state = sheet(6)
    actor, own = world.actors[0], world.inventories[1]
    actor.hp, actor.gold, state.pointer_x = 1, 100, 160
    world.market.counts[3] = 1
    market(state, SHOP, world, SheetRegion(0, 0, 1, 1, type=1, field=6))
    assert actor.hp == 21 and actor.hp_max == actor.constitution * 10 + 30
    state.pointer_x = 159
    market(state, SHOP, world, SheetRegion(0, 0, 1, 1, type=1, field=6))
    assert actor.hp == 21 and actor.hp_max == actor.constitution * 10 + 10
    assert own.byte(6) == 0


def test_layout_has_original_smith_offers_and_market_depletes_live_regions() -> None:
    world, state = sheet(5)
    redraw(state, SHEETS, world)
    offers = [r for r in state.regions if r.type == 10]
    assert len(offers) == 18
    assert [(r.mask, r.field) for r in offers[:5]] == [(1, 92), (2, 92), (4, 92), (1, 88), (2, 88)]
    state.number, world.actors[0].gold = 6, 100
    world.market.counts[0] = 1
    redraw(state, SHEETS, world)
    region = next(r for r in state.regions if r.side and r.field == 0)
    state.pointer_x, state.pointer_y = region.x, region.y
    step(state, SHEETS, world, 0, 0, True)
    assert not any(r.side and r.field == 0 for r in state.regions)
    gold = world.actors[0].gold
    step(state, SHEETS, world, 0, 0, True)
    assert world.actors[0].gold == gold
    assert state.sounds == []


@pytest.mark.parametrize("number", [5, 6])
def test_stat_click_still_runs_on_shop_sheets(number: int) -> None:
    world, state = sheet(number)
    actor = world.actors[0]
    actor.progress = SHEETS.stat_price
    redraw(state, SHEETS, world)
    region = next(r for r in state.regions if not r.side and r.field == 70)
    strength = actor.strength
    click(state, SHEETS, world, region)
    assert actor.strength == strength + 1 and actor.progress == 0


def test_sale_event_follows_redraw() -> None:
    world, state = sheet(6)
    world.inventories[1].counts[0], state.pointer_x = 1, 100
    redraw(state, SHEETS, world)
    region = next(r for r in state.regions if not r.side and r.field == 0 and not r.id)
    with patch("pymoonstone.sim.sheets.redraw") as rebuilt:
        rebuilt.side_effect = lambda *args: state.sounds.append("redraw")
        click(state, SHEETS, world, region)
    assert state.sounds == ["redraw", "sell"]


@pytest.mark.parametrize("field", [88, 92])
def test_smith_empty_mask_refuses_without_debit(field: int) -> None:
    world, state = sheet(5)
    actor = world.actors[0]
    before = asdict(actor)
    smith(state, SHOP, world, SheetRegion(0, 0, 1, 1, type=10, field=field))
    assert asdict(actor) == before


def test_sharp_sword_buy_equips_on_redraw_without_inventing_left_sale_region() -> None:
    world, state = sheet(6)
    world.market.counts[2], world.actors[0].gold = 1, 100
    redraw(state, SHEETS, world)
    region = next(r for r in state.regions if r.side and r.field == 4)
    state.pointer_x = 160
    click(state, SHEETS, world, region)
    assert world.actors[0].sword == 25 and world.inventories[1].byte(4) == 1
    assert not any(r.side and r.field == 4 for r in state.regions)
    assert not any(not r.side and r.field == 4 and not r.id for r in state.regions)
    worn = next(r for r in state.regions if not r.side and r.field == 88)
    assert worn.type == 2
