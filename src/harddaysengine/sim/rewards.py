"""Shared original gift, stat and loot rules (ARCHITECTURE.md §9.3/4, §12.9/12)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING

from harddaysengine.sim.m68k import s8, s16, u8, u16

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from harddaysengine.sim.actor import Actor
    from harddaysengine.sim.inventory import Inventory
    from harddaysengine.sim.rng import Rng
    from harddaysengine.sim.turn_rules import GiftRules


@dataclass(slots=True)
class GiftState:
    last: int = -1
    item_cycle: int = 0
    gold_cycle: int = 0


class GiftKind(IntEnum):
    ITEM = 1
    GOLD = 2
    STAT = 3
    CURSE = 4


@dataclass(slots=True, frozen=True)
class GiftResult:
    """Reward and original message index, without another random draw (§12.35)."""

    kind: GiftKind
    message: int = 0
    stat: int = 0
    item: int = 0
    amount: int = 0


def armour_hp_bonus(armour: int) -> int:
    """HP bonus contributed by an armour code (§9.3)."""
    return {28: 10, 29: 20, 30: 30}.get(armour, 0)


def recalc_hp(actor: Actor, inventory: Inventory) -> None:
    """Word arithmetic, signed cap and forced sharp sword (§9.3)."""
    bonus = armour_hp_bonus(actor.armour)
    actor.hp_max = u16(actor.constitution * 10 + inventory.byte(6) * 20 + bonus + 10)
    if s16(actor.hp) >= s16(actor.hp_max):
        actor.hp = actor.hp_max
    if inventory.byte(4):
        actor.sword = 25


def recalc_endurance(actor: Actor) -> None:
    """Byte arithmetic of the original endurance calculation (§9.3)."""
    actor.derived_endurance = u8(actor.endurance * 2 + (2 if actor.armour in (28, 30) else 0) + 4)


def pick_stat(actor: Actor, rng: Rng, rules: GiftRules) -> int:
    """Pick a decoded stat row, rejecting exact-five bytes (§12.9)."""
    return pick_stat_row(actor, rng, rules)[1]


def pick_stat_row(actor: Actor, rng: Rng, rules: GiftRules) -> tuple[int, int]:
    """Keep the chosen table row for the wizard's distinct stat speeches (§12.35)."""
    if all(s8(actor.read(offset, 1)) >= 5 for offset in (70, 71, 72)):
        return -1, 0
    for _ in range(4096):
        row = rng.draw() & 15
        row = row - 7 if row > 8 else row
        offset = rules.stats[row]
        if actor.read(offset, 1) != 5:
            return row, offset
    raise ValueError("stat selection did not terminate")


def item_gift(
    actor: Actor, inventory: Inventory, rng: Rng, rules: GiftRules, state: GiftState
) -> int:
    """Preserve last-gift memory even when rejecting a duplicate sharp sword (§12.12)."""
    slot = inventory_gift(inventory, rng, rules, state)
    if slot == 4:
        actor.sword = 25
    if slot == 6:
        actor.hp = u16(actor.hp + 20)
        recalc_hp(actor, inventory)
    return slot


def inventory_gift(inventory: Inventory, rng: Rng, rules: GiftRules, state: GiftState) -> int:
    """Inventory-only gift for stock/lairs, with shared rejection memory (§12.12/29)."""
    for _ in range(4096):
        roll = rng.percent()
        slot = next(slot for limit, slot in rules.bands if roll <= limit)
        if slot == state.last:
            continue
        state.last = slot
        if slot == 4 and inventory.byte(4):
            continue
        inventory.counts[slot // 2] = u8(inventory.byte(slot) + 1)
        return slot
    raise ValueError("item gift did not terminate")


def _stat_gift(actor: Actor, inventory: Inventory, offset: int) -> None:
    actor.write(offset, 1, actor.read(offset, 1) + 1)
    if offset == 71:
        recalc_hp(actor, inventory)
        actor.hp = u16(actor.hp + 10)
    if offset == 72:
        recalc_endurance(actor)


def wizard_gift(
    actor: Actor, inventory: Inventory, rng: Rng, rules: GiftRules, state: GiftState
) -> GiftResult:
    """Wizard's original roll and dialogue result, shared with AI (§12.12/35)."""
    item_limit, stat_limit, gold_limit = rules.limits
    for _ in range(4096):
        roll = u8(rng.percent() + actor.recency)
        if roll <= item_limit:
            slot = item_gift(actor, inventory, rng, rules, state)
            state.item_cycle = (state.item_cycle + 1) & 3
            return GiftResult(GiftKind.ITEM, state.item_cycle, item=slot)
        if roll <= stat_limit:
            row, offset = pick_stat_row(actor, rng, rules)
            if not offset:
                continue
            _stat_gift(actor, inventory, offset)
            return GiftResult(GiftKind.STAT, row, stat=offset)
        if roll <= gold_limit:
            gold = rng.draw() & 31
            amount = (gold - 10 if gold > 21 else gold) + 10
            actor.gold = u16(actor.gold + amount)
            state.gold_cycle = (state.gold_cycle + 1) % 3
            return GiftResult(GiftKind.GOLD, state.gold_cycle, amount=amount)
        if actor.recency != 255:
            actor.frog_days = 3
            return GiftResult(GiftKind.CURSE)
    raise ValueError("wizard gift did not terminate")


def _gold(winner: Actor, loser: Actor) -> None:
    half = u16(loser.gold) >> 1
    winner.gold, loser.gold = u16(winner.gold + half), half


def _move_slot(
    winner: Actor, loser: Actor, target: Inventory, source: Inventory, slot: int, *, all_items: bool
) -> None:
    count = source.byte(slot)
    if not count:
        return
    index = slot // 2
    if slot in (20, 22):
        target.counts[index] = target.byte(slot) | count
        source.counts[index] = 0
    elif all_items:
        target.counts[index] = u8(target.byte(slot) + count)
        source.counts[index] = 0
        if slot == 4:
            loser.sword = 22
    else:
        source.counts[index] = u8(count - 1)
        target.counts[index] = u8(target.byte(slot) + 1)
        if slot == 4 and count == 1:
            loser.sword = 22


def transfer_loot(
    winner: Actor,
    loser: Actor,
    actors: Sequence[Actor],
    inventories: Mapping[int, Inventory],
    rules: GiftRules,
) -> None:
    """Slot order, dragon gold-first (single halving) and zero-lives transfer (§9.4/11.19)."""
    target, source = inventories[winner.inventory], inventories[loser.inventory]
    took_gold = winner.type == 20
    if took_gold:
        _gold(winner, loser)
    slots = [slot for slot in rules.transfer if source.byte(slot)]
    all_items = loser.lives == 0
    for slot in slots if all_items else slots[:1]:
        _move_slot(winner, loser, target, source, slot, all_items=all_items)
    if not all_items and not slots and not took_gold:
        _gold(winner, loser)
    for actor in actors:
        recalc_hp(actor, inventories[actor.inventory])
        recalc_endurance(actor)
