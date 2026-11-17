"""Original human smith and finite-stock market (ARCHITECTURE.md §12.10/11/29)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pymoonstone.sim.m68k import s8, s16, s32, u8, u16
from pymoonstone.sim.rewards import recalc_hp

if TYPE_CHECKING:
    from pymoonstone.sim.actor import Actor
    from pymoonstone.sim.inventory import Inventory
    from pymoonstone.sim.overworld import OverworldState
    from pymoonstone.sim.sheet_types import SheetRegion, SheetState
    from pymoonstone.sim.shop_types import ShopRules


def _offer(mask: int, count: int) -> int:
    return next((index for index in range(count) if mask & (1 << index)), -1)


def smith(state: SheetState, rules: ShopRules, world: OverworldState, region: SheetRegion) -> None:
    """Use signed affordability, lowest mask bit, and original equipment replacement."""
    actor = world.actors[state.knight - 1]
    inv = world.inventories[actor.inventory]
    if region.field == 92:
        _armour(actor, inv, rules, region.mask)
    elif region.field == 88:
        _sword(actor, rules, region.mask)
    elif (
        region.field == 76
        and s16(actor.gold) >= s16(rules.dagger_price)
        and s8(actor.daggers) < s8(rules.dagger_cap)
    ):
        actor.gold = u16(actor.gold - rules.dagger_price)
        actor.daggers = u8(actor.daggers + 1)


def _armour(actor: Actor, inv: Inventory, rules: ShopRules, mask: int) -> None:
    index = _offer(mask, len(rules.armour))
    if index < 0:
        return
    price, item, gain = rules.armour[index]
    if s16(actor.gold) >= s16(price):
        actor.gold, actor.armour = u16(actor.gold - price), item
        actor.hp = u16(actor.hp + gain)
        recalc_hp(actor, inv)


def _sword(actor: Actor, rules: ShopRules, mask: int) -> None:
    index = _offer(mask, len(rules.swords))
    if index < 0:
        return
    price, item = rules.swords[index]
    if s16(actor.gold) >= s16(price) and s32(actor.sword) < s32(item):
        actor.gold, actor.sword = u16(actor.gold - price), item


def market(state: SheetState, rules: ShopRules, world: OverworldState, region: SheetRegion) -> None:
    """Trade by pointer x, retaining byte masks and signed wrapped gold arithmetic."""
    slot = region.field
    own = world.inventories[world.actors[state.knight - 1].inventory]
    own.byte(slot)  # Validate the slot before indexing its price.
    price = rules.prices[slot // 2]
    if s16(state.pointer_x) >= s16(rules.split_x):
        state.sounds.append("buy")
        _buy(state, world, own, slot, region.mask, price)
    else:
        _sell(state, rules, world, own, slot, region.mask, price)


def _buy(
    state: SheetState, world: OverworldState, own: Inventory, slot: int, mask: int, price: int
) -> None:
    actor, stock = world.actors[state.knight - 1], world.market
    if s16(price) > s16(actor.gold):
        return
    actor.gold = u16(actor.gold - price)
    index = slot // 2
    if slot in (20, 22):
        own.counts[index] = u8(own.byte(slot) | mask)
        stock.counts[index] = u8(stock.byte(slot) ^ mask)
    else:
        stock.counts[index] = u8(stock.byte(slot) - 1)
        own.counts[index] = u8(own.byte(slot) + 1)
        if slot == 6:
            actor.hp = u16(actor.hp + 20)
            recalc_hp(actor, own)


def _sell(
    state: SheetState,
    rules: ShopRules,
    world: OverworldState,
    own: Inventory,
    slot: int,
    mask: int,
    price: int,
) -> None:
    actor, stock = world.actors[state.knight - 1], world.market
    index = slot // 2
    if slot in (20, 22):
        stock.counts[index] = u8(stock.byte(slot) | mask)
        own.counts[index] = u8(own.byte(slot) ^ mask)
    else:
        own.counts[index] = u8(own.byte(slot) - 1)
        stock.counts[index] = u8(stock.byte(slot) + 1)
    actor.gold = u16(actor.gold + (price >> rules.sell_shift))
    if s16(actor.gold) > s16(rules.gold_cap):
        actor.gold = rules.gold_cap
    if slot == 4:
        actor.sword = 22
    recalc_hp(actor, own)
    # Sheet dispatch appends the sell event after rebuilding its draw/region lists.
