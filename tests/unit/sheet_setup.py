"""Explicit synthetic geometry and rule data for shared sheets (§12.26)."""

from pymoonstone.sim.overworld import OverworldState
from pymoonstone.sim.sheet_types import SheetRules, SheetState
from pymoonstone.sim.sheets import redraw
from tests.unit.map_setup import make_world

RULES = SheetRules(
    tuple(tuple(f"table{table} button{index}" for index in range(27)) for table in range(7)),
    ((8, 8),) * 50,
    (((26, 296, 83, 0),), ((26, 0, 83, 0),)),
    4,
    153,
    6,
    5,
    12,
    17,
    13,
    bytes.fromhex("0000000b00170023"),
)


def sheet(number: int = 9) -> tuple[OverworldState, SheetState]:
    """Make a sheet over authoritative synthetic campaign records."""
    world = make_world()
    world.dragon.inventory = 5
    state = SheetState(number, 1, other=2, lair=0)
    redraw(state, RULES, world)
    return world, state
