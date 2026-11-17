"""Original campaign inventory and stock initialization (ARCHITECTURE.md §11.3/12.29)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pymoonstone.sim.m68k import u8, u16
from pymoonstone.sim.rewards import inventory_gift, item_gift

if TYPE_CHECKING:
    from pymoonstone.sim.overworld import OverworldState
    from pymoonstone.sim.rng import Rng
    from pymoonstone.sim.shop_types import StockRules
    from pymoonstone.sim.turn_rules import GiftRules


def _gold(rng: Rng) -> int:
    roll = rng.draw() & 31
    return (roll - 10 if roll > 21 else roll) + 10


def initialize(world: OverworldState, rules: StockRules, gifts: GiftRules) -> None:
    """Run hoard, stock, shared key draw and ordered lair loot exactly once (§12.29)."""
    hoard = world.inventories[world.dragon.inventory]
    hoard.counts[:] = [0] * 12
    world.market.counts[:] = [0] * 12
    for _ in range(rules.hoard_rolls):
        item_gift(world.dragon, hoard, world.prng, gifts, world.gifts)
    world.dragon.gold = u16(_gold(world.prng))
    for _ in range(rules.market_rolls):
        inventory_gift(world.market, world.prng, gifts, world.gifts)
    world.market.counts[rules.extra_slot // 2] = u8(
        world.market.byte(rules.extra_slot) + rules.extra_count
    )
    _keys(world, rules)
    for index in range(len(world.lairs)):
        roll = world.prng.percent()
        kind = next(kind for limit, kind in rules.loot_bands if roll <= limit)
        _fill_lair(world, gifts, kind, index)


def _keys(world: OverworldState, rules: StockRules) -> None:
    if len(world.lairs) != len(rules.key_groups) * (rules.key_limit + 1):
        raise ValueError("lair key groups do not match decoded lair count")
    for lair in world.lairs:
        lair.gold = 0
        lair.loot.counts[:] = [0] * 12
    for _ in range(4096):
        index = world.prng.draw() & rules.key_mask
        if index <= rules.key_limit:
            for group, mask in enumerate(rules.key_groups):
                world.lairs[group * (rules.key_limit + 1) + index].loot.counts[10] = mask
            return
    raise ValueError("lair key selection did not terminate")


def _fill_lair(world: OverworldState, gifts: GiftRules, kind: int, index: int) -> None:
    lair = world.lairs[index]
    if kind in (1, 3):
        lair.gold = u16(lair.gold + _gold(world.prng))
    count = {2: 3, 3: 2}.get(kind, 0)
    for _ in range(count):
        inventory_gift(lair.loot, world.prng, gifts, world.gifts)
