"""Shared tick-driven sheet loop and dispatch (ARCHITECTURE.md §12.2/5/26)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harddaysengine.sim import sheet_actions as actions
from harddaysengine.sim.m68k import s8, s16
from harddaysengine.sim.sheet_layout import button, rebuild
from harddaysengine.sim.shops import market, smith

if TYPE_CHECKING:
    from harddaysengine.sim.overworld import OverworldState
    from harddaysengine.sim.sheet_types import SheetRegion, SheetRules, SheetState


def redraw(state: SheetState, rules: SheetRules, world: OverworldState) -> None:
    if state.number == 1:
        if s8(world.actors[state.other - 1].lives) <= 0:
            state.changes = 0
        if state.changes:
            state.number = 9
    elif state.number == 8 and state.changes:
        state.changes, state.number = 0, state.previous
    rebuild(state, rules, world)


def click(state: SheetState, rules: SheetRules, world: OverworldState, region: SheetRegion) -> None:
    """Dispatch an existing region using its decoded button flags (§12.5)."""
    if region.id == 7:
        state.done = True
    elif region.id == 8:
        actions.next_knight(state, world)
    else:
        _dispatch(state, rules, world, region)
    redraw(state, rules, world)
    if (
        state.number == 6
        and region.type in (1, 5)
        and rules.shop is not None
        and s16(state.pointer_x) < s16(rules.shop.split_x)
    ):
        state.sounds.append("sell")
    _hold_jingle(state, rules)


def _hold_jingle(state: SheetState, rules: SheetRules) -> None:
    if not rules.jingle_wait:
        return
    for index, event in enumerate(state.sounds):
        if event in ("good", "bad"):
            state.pending_sounds.extend(state.sounds[index + 1 :])
            del state.sounds[index + 1 :]
            state.wait = rules.jingle_wait
            return


def _jingle_tick(state: SheetState, rules: SheetRules) -> None:
    state.wait -= 1
    if not state.wait and state.pending_sounds:
        state.sounds[:] = state.pending_sounds
        state.pending_sounds.clear()
        _hold_jingle(state, rules)


def _dispatch(
    state: SheetState, rules: SheetRules, world: OverworldState, region: SheetRegion
) -> None:
    _, flags = button(state, rules, world.actors[state.knight - 1], region)
    kind, field = region.type, region.field
    if _shop_dispatch(state, rules, world, region):
        return
    if kind == 12:
        actions.drop_item(state, world, field)
    elif kind == 5 and flags & 16:
        actions.use_item(state, rules, world, field)
    elif kind == 3:
        _stat_or_daggers(state, rules, world, field, flags)
    elif flags & 32:
        _take(state, rules, world, region)


def _shop_dispatch(
    state: SheetState, rules: SheetRules, world: OverworldState, region: SheetRegion
) -> bool:
    if region.type != 10 and not (state.number == 6 and region.type in (1, 5)):
        return False
    if rules.shop is None:
        raise ValueError("shop transaction requires decoded rules")
    action = smith if region.type == 10 else market
    action(state, rules.shop, world, region)
    return True


def _stat_or_daggers(
    state: SheetState, rules: SheetRules, world: OverworldState, field: int, flags: int
) -> None:
    if flags & 64:
        actions.buy_stat(state, rules, world, field)
    if flags & 32 and field == 76:
        actions.take_equipment(state, rules, world, field)


def _take(state: SheetState, rules: SheetRules, world: OverworldState, region: SheetRegion) -> None:
    if region.type in (1, 5):
        actions.take_item(state, world, region.field)
    elif region.field == 74:
        actions.take_gold(state, rules, world)
    elif region.field in (88, 92):
        actions.take_equipment(state, rules, world, region.field)


def step(
    state: SheetState, rules: SheetRules, world: OverworldState, dx: int, dy: int, fire: bool
) -> None:
    """Move the pointer and dispatch held fire once per PAL tick (§12.2)."""
    if state.done and not state.wait:
        return
    state.sounds.clear()
    state.pointer_x = max(0, min(314, state.pointer_x + dx * 2))
    state.pointer_y = max(0, min(195, state.pointer_y + dy * 2))
    if state.wait:
        _jingle_tick(state, rules)
        return
    region = next((r for r in state.regions if r.contains(state.pointer_x, state.pointer_y)), None)
    state.hover = ""
    if region is not None:
        if not region.id:
            state.hover = button(state, rules, world.actors[state.knight - 1], region)[0]
        if fire:
            click(state, rules, world, region)


def close(state: SheetState, world: OverworldState) -> None:
    """Commit the Wyrm target when the shared loop closes (§12.2)."""
    if state.hand_over:
        world.dragon.engaged_with = state.other
        state.hand_over = False
