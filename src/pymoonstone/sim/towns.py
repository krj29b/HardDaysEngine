"""Town pointer loop and original action routing (ARCHITECTURE.md §12.15)."""

from dataclasses import dataclass
from enum import IntEnum

from pymoonstone.sim.town_types import TownData


class TownAction(IntEnum):
    """Requests issued by the town to its campaign host (§12.15)."""

    NONE = 0
    SMITH = 1
    DICE = 2
    HEALER = 3
    MARKET = 4
    MYSTIC = 5
    EXIT = 6
    STATUS = 7


@dataclass(slots=True)
class TownState:
    """Mutable pointer and pending caller, retained while a child screen runs."""

    waterdeep: bool
    knight: int
    pointer_x: int
    pointer_y: int
    pending: TownAction = TownAction.NONE


def reset_pointer(state: TownState, data: TownData) -> None:
    """Every return redraw restores the town's original pointer start (§12.15)."""
    state.pointer_x, state.pointer_y = data.pointer_x, data.pointer_y
    state.pending = TownAction.NONE


def step(state: TownState, data: TownData, dx: int, dy: int, fire: bool, key: int) -> TownAction:
    """Move the owner's pointer; a live fire region takes precedence over Space."""
    if state.pending != TownAction.NONE:
        return TownAction.NONE
    state.pointer_x = max(0, min(314, state.pointer_x + dx * 2))
    state.pointer_y = max(0, min(195, state.pointer_y + dy * 2))
    hit = next(
        (
            b
            for b in data.buttons
            if b.x <= state.pointer_x < b.x + b.width and b.y <= state.pointer_y < b.y + b.height
        ),
        None,
    )
    if hit is not None and fire:
        state.pending = button_action(hit.id, state.waterdeep)
    elif key == 32:
        state.pending = TownAction.STATUS
    return state.pending


def button_action(button: int, waterdeep: bool) -> TownAction:
    """Interpret registered ids, keeping town-specific button four (§12.15)."""
    if button == 4:
        return TownAction.MYSTIC if waterdeep else TownAction.MARKET
    actions = {1: TownAction.SMITH, 2: TownAction.DICE, 3: TownAction.HEALER, 5: TownAction.EXIT}
    return actions.get(button, TownAction.NONE)
