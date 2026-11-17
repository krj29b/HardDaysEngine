"""Ordered sheet icons, text and hit regions (ARCHITECTURE.md §12.3/4/26)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pymoonstone.sim.m68k import s8, s16
from pymoonstone.sim.rewards import recalc_endurance, recalc_hp
from pymoonstone.sim.sheet_types import SheetIcon, SheetRegion, SheetText

if TYPE_CHECKING:
    from pymoonstone.sim.actor import Actor
    from pymoonstone.sim.inventory import Inventory
    from pymoonstone.sim.overworld import OverworldState
    from pymoonstone.sim.sheet_types import SheetRules, SheetState


def button_table(state: SheetState, side: int) -> tuple[int, int]:
    """Choose original table/flags, including plain travel inspection (§12.4)."""
    if side == 0:
        index = {9: 1, 3: 4, 6: 5}.get(state.number, 6)
        return index, 3 if index == 6 else 19
    if state.read_only:
        return 6, 3
    index = {1: 2, 2: 2, 8: 2, 10: 2, 5: 3, 6: 3}.get(state.number, 6)
    return index, 3 if index == 6 else 35


def button(
    state: SheetState, rules: SheetRules, actor: Actor, region: SheetRegion
) -> tuple[str, int]:
    table, flags = button_table(state, region.side)
    index = region.button
    if not 0 <= index < 27:
        raise ValueError("sheet button outside its decoded table")
    text = rules.buttons[table][index] or ""
    if region.side == 0 and index < 3 and s16(actor.progress) >= s16(rules.stat_price):
        offset = (70, 72, 71)[index]
        if actor.read(offset, 1) != 5:
            return rules.buttons[0][index] or "", flags ^ 64
    return text, flags


def _icon(state: SheetState, rules: SheetRules, icon: SheetIcon, region: SheetRegion) -> None:
    if not 0 <= icon.frame < len(rules.sizes):
        raise ValueError(f"sheet cel frame {icon.frame} is not loaded")
    state.icons.append(icon)
    width, height = rules.sizes[icon.frame]
    state.regions.append(
        SheetRegion(
            icon.x,
            icon.y,
            width,
            height,
            region.button,
            region.side,
            region.type,
            region.mask,
            region.field,
            region.id,
        )
    )


def row(
    state: SheetState,
    rules: SheetRules,
    frame: int,
    x: int,
    y: int,
    count: int,
    step: int,
    region: SheetRegion,
) -> None:
    for index in range(count):
        _icon(state, rules, SheetIcon(frame, x + index * step, y), region)


def inventory(state: SheetState, rules: SheetRules, inv: Inventory, shift: int, side: int) -> None:
    """Keep the previous row's step for the moonstone spacing quirk (§12.3)."""
    step = 0
    for slot, frame, x, y, stride, cap, index, kind in (
        (2, 9, 87, 164, 16, 4, 19, 5),
        (6, 3, 33, 149, 12, 3, 20, 1),
        (8, 10, 69, 133, 20, 4, 21, 1),
    ):
        if inv.byte(slot):
            step = stride
            row(
                state,
                rules,
                frame,
                x + shift,
                y,
                min(cap, inv.byte(slot)),
                step,
                SheetRegion(0, 0, 0, 0, index, side, kind, field=slot),
            )
    for bit in range(4):
        if inv.byte(20) & (1 << bit):
            row(
                state,
                rules,
                bit + 5,
                76 + bit * 18 + shift,
                111,
                1,
                0,
                SheetRegion(0, 0, 0, 0, 17, side, 1, 1 << bit, 20),
            )
    x = 103 + shift
    for bit, frame, index in ((0, 2, 14), (1, 1, 15), (2, 0, 16), (3, 0, 14)):
        if inv.byte(22) & (1 << bit):
            row(
                state,
                rules,
                frame,
                x,
                111,
                1,
                0,
                SheetRegion(0, 0, 0, 0, index, side, 1, 1 << bit, 22),
            )
            x += step
    row(
        state,
        rules,
        4,
        31 + shift,
        165,
        min(4, inv.byte(0)),
        13,
        SheetRegion(0, 0, 0, 0, 18, side, 5, field=0),
    )
    _scrolls(state, rules, inv, shift, side)


def _scrolls(state: SheetState, rules: SheetRules, inv: Inventory, shift: int, side: int) -> None:
    for index in range(5):
        count = inv.byte(10 + index * 2)
        region = SheetRegion(0, 0, 0, 0, 22 + index, side, 5, field=10 + index * 2)
        if count:
            row(state, rules, 11 + index, 30 + index * 25 + shift, 183, 1, 0, region)
        if count > 1:
            row(state, rules, 16, 34 + index * 25 + shift, 183, 1, 0, region)


def knight_panel(
    state: SheetState, rules: SheetRules, world: OverworldState, handle: int, shift: int, side: int
) -> None:
    actor = world.actors[handle - 1]
    inv = world.inventories[actor.inventory]
    recalc_hp(actor, inv)
    recalc_endurance(actor)
    state.texts.append(SheetText(world.names[handle - 1], 60 + shift, 24))
    for frame, y, offset, index in ((38, 35, 70, 0), (39, 42, 71, 2), (40, 49, 72, 1)):
        value = actor.read(offset, 1)
        row(
            state,
            rules,
            frame,
            41 + shift,
            y,
            value,
            0,
            SheetRegion(0, 0, 0, 0, index, side, 3, field=offset),
        )
        state.texts.append(SheetText(str(value), 63 + shift, y))
    for frame, y in ((41, 35), (42, 42), (43, 49)):
        state.icons.append(SheetIcon(frame, 89 + shift, y))
    width, height = rules.sizes[42]
    state.regions.append(SheetRegion(89 + shift, 42, width, height, 4, side, 2, field=74))
    for text, y in (
        (str(s16(actor.progress)), 35),
        (str(s16(actor.gold)), 42),
        (f"{s16(actor.hp)}/{s16(actor.hp_max)}", 49),
    ):
        state.texts.append(SheetText(text, 112 + shift, y))
    lives = 5 if s8(actor.lives) < 0 else actor.lives
    row(
        state,
        rules,
        17 + side + (2 if actor.frog_days else 0),
        41 + shift,
        59,
        lives,
        19,
        SheetRegion(0, 0, 0, 0, 3, side, 3, field=73),
    )
    row(
        state,
        rules,
        21,
        38 + shift,
        78,
        actor.daggers,
        10,
        SheetRegion(0, 0, 0, 0, 5, side, 3, field=76),
    )
    sword = min(25, actor.sword)
    row(
        state,
        rules,
        sword,
        43 + shift,
        95,
        1,
        0,
        SheetRegion(0, 0, 0, 0, sword - 12, side, 2, field=88),
    )
    row(
        state,
        rules,
        actor.armour,
        29 + shift,
        112,
        1,
        0,
        SheetRegion(0, 0, 0, 0, actor.armour - 21, side, 2, field=92),
    )
    inventory(state, rules, inv, shift, side)


def loot_panel(state: SheetState, rules: SheetRules, world: OverworldState) -> None:
    if state.number == 2:
        source = world.lairs[state.lair]
        inv, gold = source.loot, source.gold
    else:
        inv, gold = world.inventories[world.dragon.inventory], world.dragon.gold
    if state.number == 2:
        state.icons.extend(
            SheetIcon(frame, x + 150, y)
            for frame, x, y in ((31, 58, 46), (32, 76, 33), (33, 58, 60))
        )
    if gold:
        row(state, rules, 37, 237, 64, 1, 0, SheetRegion(0, 0, 0, 0, 4, 1, 2, field=74))
        state.texts.append(SheetText(f"{s16(gold)} gp.", 242, 91))
    if inv.byte(4):
        row(state, rules, 25, 193, 95, 1, 0, SheetRegion(0, 0, 0, 0, 13, 1, 1, field=4))
    inventory(state, rules, inv, 150, 1)


def rebuild(state: SheetState, rules: SheetRules, world: OverworldState) -> None:
    """Rebuild authoritative hit regions only during updates (§12.26)."""
    state.icons.clear()
    state.texts.clear()
    state.regions.clear()
    wide = state.number in (3, 9)
    shift = 74 if wide else 0
    for frame, x, y, mirror in rules.panels[int(wide)]:
        state.icons.append(SheetIcon(frame, x + shift, y, bool(mirror)))
        if frame == 26:
            state.regions.append(SheetRegion(x + shift, y, 25, 117, id=7))
    knight_panel(state, rules, world, state.knight, shift, 0)
    _right(state, rules, world)
    if state.number == 3:
        state.regions[:] = [
            SheetRegion(
                r.x,
                r.y,
                r.width,
                r.height,
                r.button,
                r.side,
                12 if r.field < 20 and not r.id else r.type,
                r.mask,
                r.field,
                r.id,
            )
            for r in state.regions
        ]


def _right(state: SheetState, rules: SheetRules, world: OverworldState) -> None:
    if state.number == 5:
        smith_panel(state, rules)
    elif state.number == 6:
        stock = world.market
        if stock.byte(4):
            row(state, rules, 25, 193, 95, 1, 0, SheetRegion(0, 0, 0, 0, 13, 1, 1, field=4))
        inventory(state, rules, stock, 150, 1)
    elif state.number in (2, 10):
        loot_panel(state, rules, world)
    elif state.number in (1, 8, 11):
        knight_panel(state, rules, world, state.other, 150, 1)
        if state.number in (8, 11):
            state.icons.append(SheetIcon(44, 146, 71))
            state.regions.append(SheetRegion(146, 71, 20, 20, id=8))


def smith_panel(state: SheetState, rules: SheetRules) -> None:
    """Fixed offers at the original panel-relative coordinates (§12.3/29)."""
    for frame, x, y, button, mask, field in (
        (28, 23, 145, 7, 1, 92),
        (29, 64, 145, 8, 2, 92),
        (30, 106, 144, 9, 4, 92),
        (23, 50, 107, 11, 1, 88),
        (24, 47, 125, 12, 2, 88),
    ):
        row(
            state,
            rules,
            frame,
            x + 150,
            y,
            1,
            0,
            SheetRegion(0, 0, 0, 0, button, 1, 10, mask, field),
        )
    row(state, rules, 21, 182, 89, 13, 9, SheetRegion(0, 0, 0, 0, 5, 1, 10, field=76))
