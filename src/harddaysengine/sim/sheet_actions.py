"""Item use and sheet transfers (ARCHITECTURE.md §12.5/7/8/9/26)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harddaysengine.sim.m68k import s8, s16, s32, u8, u16
from harddaysengine.sim.rewards import recalc_endurance, recalc_hp

if TYPE_CHECKING:
    from harddaysengine.sim.actor import Actor
    from harddaysengine.sim.inventory import Inventory
    from harddaysengine.sim.overworld import Lair, OverworldState
    from harddaysengine.sim.sheet_types import SheetRules, SheetState


def recalc(world: OverworldState, handle: int) -> None:
    actor = world.actors[handle - 1]
    recalc_hp(actor, world.inventories[actor.inventory])
    recalc_endurance(actor)


def next_knight(state: SheetState, world: OverworldState) -> None:
    handles = [int(kid) + 1 for kid in world.turn_order]
    index = handles.index(state.other) if state.other else -1
    state.other = handles[(index + 1) % 4]
    if state.other == state.knight:
        state.other = handles[(index + 2) % 4]


def use_item(state: SheetState, rules: SheetRules, world: OverworldState, slot: int) -> None:
    actor = world.actors[state.knight - 1]
    inventory = world.inventories[actor.inventory]
    inventory.counts[slot // 2] = u8(inventory.byte(slot) - 1)
    state.last_item = slot
    if slot == 0:
        actor.life_loss = 0
        if actor.hp == actor.hp_max:
            actor.lives = u8(actor.lives + 1)
            if s8(actor.lives) >= rules.rest_test:
                actor.lives = rules.rest_cap
        actor.hp = actor.hp_max
        state.sounds.append("click")
    elif slot in (10, 12, 18):
        _gamble(state, rules, world, slot)
    elif slot == 2:
        world.saved_x, world.saved_y = actor.map_x, actor.map_y
        world.travel = True
        state.done = True
        state.sounds.append("good")
    elif slot in (14, 16):
        state.previous = state.number
        state.number = 8 if slot == 14 else 11
        state.hand_over = slot == 16
        state.other = 0
        next_knight(state, world)
        state.sounds.append("good")


def _gamble(state: SheetState, rules: SheetRules, world: OverworldState, slot: int) -> None:
    limit = {10: rules.haste_limit, 12: rules.hawk_limit, 18: rules.protection_limit}[slot]
    good = world.prng.percent() > limit
    state.sounds.append("good" if good else "bad")
    if slot == 10:
        world.budget = u16(world.budget << 1) if good else world.budget >> 1
    elif slot == 12:
        if good:
            world.start_mode(ambush=True)
        else:
            world.random_spot()
        state.done = True
    else:
        state.done = True
        if not good:
            state.sounds.append("bad")
    if not good and slot in (10, 18):
        state.bad_luck = 1


def _source_inventory(state: SheetState, world: OverworldState) -> Inventory:
    if state.number == 2:
        return world.lairs[state.lair].loot
    actor = world.dragon if state.number == 10 else world.actors[state.other - 1]
    return world.inventories[actor.inventory]


def take_item(state: SheetState, world: OverworldState, slot: int) -> None:
    actor = world.actors[state.knight - 1]
    target = world.inventories[actor.inventory]
    source = _source_inventory(state, world)
    index = slot // 2
    state.sounds.append("click")
    if slot in (20, 22):
        target.counts[index] |= source.byte(slot)
        source.counts[index] = 0
    else:
        source.counts[index] = u8(source.byte(slot) - 1)
        _gain_item(state, world, slot, target)
    recalc(world, state.knight)
    state.changes = u16(state.changes + 1)


def _gain_item(state: SheetState, world: OverworldState, slot: int, target: Inventory) -> None:
    actor = world.actors[state.knight - 1]
    if slot == 4:
        actor.sword = 25
        if state.number == 2:
            return
        other = world.dragon if state.number == 10 else world.actors[state.other - 1]
        other.sword = 22
    if slot == 6:
        actor.hp = u16(actor.hp + 20)
    target.counts[slot // 2] = u8(target.byte(slot) + 1)


def take_gold(state: SheetState, rules: SheetRules, world: OverworldState) -> None:
    actor = world.actors[state.knight - 1]
    source: Actor | Lair
    if state.number == 2:
        source = world.lairs[state.lair]
    else:
        source = world.dragon if state.number == 10 else world.actors[state.other - 1]
    if not source.gold:
        return
    # Equivalent to the bounded word loop, including a starting gold above the cap.
    until_cap = u16(rules.gold_cap - actor.gold)
    amount = min(u16(source.gold), until_cap)
    actor.gold = u16(actor.gold + amount)
    source.gold = u16(source.gold - amount)
    if not source.gold:
        state.sounds.append("coin")
    state.changes = u16(state.changes + 1)


def take_equipment(state: SheetState, rules: SheetRules, world: OverworldState, field: int) -> None:
    actor, other = world.actors[state.knight - 1], world.actors[state.other - 1]
    if field == 76:
        while actor.daggers != 10 and other.daggers:
            actor.daggers, other.daggers = u8(actor.daggers + 1), u8(other.daggers - 1)
        state.changes = u16(state.changes + 1)
    elif field == 92:
        _armour(state, rules, actor, other)
    elif field == 88:
        if actor.sword == 25:
            take_item(state, world, 4)
        elif s32(actor.sword) < s32(other.sword):
            actor.sword, other.sword = other.sword, actor.sword
            state.changes = u16(state.changes + 1)


def _armour(state: SheetState, rules: SheetRules, actor: Actor, other: Actor) -> None:
    if s32(actor.armour) >= s32(other.armour):
        return
    offset = actor.armour - 27
    if not 0 <= offset <= len(rules.armour_hp) - 2:
        raise ValueError("sheet armour lookup outside the decoded table")
    gain = s16((rules.armour_hp[offset] << 8) | rules.armour_hp[offset + 1])
    actor.armour, other.armour = other.armour, 27
    actor.hp, other.hp = u16(actor.hp + gain), u16(other.hp - gain)
    state.changes = u16(state.changes + 1)


def buy_stat(state: SheetState, rules: SheetRules, world: OverworldState, field: int) -> None:
    actor = world.actors[state.knight - 1]
    actor.write(field, 1, actor.read(field, 1) + 1)
    if field == 71:
        actor.hp = u16(actor.hp + 10)
    actor.progress = u16(actor.progress - rules.stat_price)
    recalc(world, state.knight)


def drop_item(state: SheetState, world: OverworldState, slot: int) -> None:
    actor = world.actors[state.knight - 1]
    inventory = world.inventories[actor.inventory]
    inventory.counts[slot // 2] = u8(inventory.byte(slot) - 1)
    if slot == 4:
        actor.sword = 22
    state.last_item, state.done = slot, True
    recalc(world, state.knight)
    state.changes = u16(state.changes + 1)
