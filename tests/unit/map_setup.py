"""Explicit synthetic map data, never a runtime fallback (§11.18)."""

from pymoonstone.sim.actor import Actor
from pymoonstone.sim.inventory import Inventory
from pymoonstone.sim.map_rules import LairStart, MapNode, MapRules
from pymoonstone.sim.overworld import OverworldState
from pymoonstone.sim.rng import Rng
from pymoonstone.sim.types import KnightId

KINDS = {KnightId.RED: 3, KnightId.BLUE: 0, KnightId.GREEN: 2, KnightId.YELLOW: 1}
RULES = MapRules(
    nodes=(MapNode(25, 50, 50), MapNode(27, 150, 100), MapNode(21, 10, 10)),
    lairs=tuple(LairStart(24, 3, 200 + i, 150, 4, "synthetic.t") for i in range(24)),
    terrain=bytes(1000),
    regions=bytes([4]) * 1000,
    sizes=((6, 7),) * 48,
    seeds=(0x12345678, 0x2468ACE1, 0xACE1, 0xFF112233),
    defaults=(
        (70, 1, 2),
        (71, 1, 3),
        (72, 1, 2),
        (73, 1, 4),
        (80, 2, 40),
        (92, 4, 27),
        (88, 4, 22),
    ),
    human_positions=((12, 12), (290, 12), (12, 180), (290, 180)),
    computer_positions=((20, 100), (290, 100), (160, 30), (160, 180)),
    human_names=("Blue", "Orange", "Green", "Red"),
    computer_names=("One", "Two", "Three", "Four"),
    may_text=" may act",
    lair_text="Enter lair",
    battle_text="Battle ",
    place_texts=("Village",) * 14,
    char_map=bytes(104),
    key_table=bytes(104),
    ramp_index=31,
    ramp_target=0x00F,
    ramp_period=2,
    cycle_start=21,
    cycle_end=23,
    cycle_period=3,
)


def make_world(players: tuple[KnightId, ...] = (KnightId.RED,), *, seed: int = 0) -> OverworldState:
    """Create test-owned records and explicitly inject fabricated map data."""
    actors = [Actor() for _ in range(4)]
    world = OverworldState(actors, Actor(), {i: Inventory() for i in range(1, 5)}, Rng(1), players)
    world.install(RULES, KINDS, seed)
    return world
