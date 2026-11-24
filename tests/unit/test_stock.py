"""Synthetic campaign stock, hoard and key initialization (§12.29)."""

from dataclasses import asdict, replace

import pytest

from harddaysengine.sim.inventory import Inventory
from harddaysengine.sim.shop_types import StockRules
from harddaysengine.sim.stock import initialize
from tests.unit.sheet_setup import sheet
from tests.unit.turn_setup import GIFTS

STOCK = StockRules(4, 3, 8, 2, 3, 1, (8, 4, 2, 1), ((100, 4),))


def test_starting_stock_and_hoard_are_independent_and_keys_share_one_index() -> None:
    traces = []
    for _ in range(2):
        world, _ = sheet()
        world.lairs = [replace(world.lairs[0], loot=Inventory()) for _ in range(8)]
        world.market.counts[2] = 123
        initialize(world, STOCK, GIFTS)
        assert sum(world.market.counts) == 5 and world.market.byte(8) == 2
        assert sum(world.inventories[5].counts) == 4
        assert all(not any(inv.counts) for handle, inv in world.inventories.items() if handle < 5)
        indices = []
        for group, mask in enumerate(STOCK.key_groups):
            pair = world.lairs[group * 2 : group * 2 + 2]
            indices.append(next(index for index, lair in enumerate(pair) if lair.loot.byte(20)))
            assert sum(lair.loot.byte(20) for lair in pair) == mask
            assert all(lair.gold == 0 for lair in pair)
        assert len(set(indices)) == 1
        traces.append((asdict(world.gifts), world.prng.state, world.market.counts.copy()))
    assert traces[0] == traces[1]


def test_key_group_shape_fails_instead_of_indexing_unmodeled_memory() -> None:
    world, _ = sheet()
    with pytest.raises(ValueError, match="key groups"):
        initialize(world, STOCK, GIFTS)


def test_legal_bottom_edge_clamps_within_nominal_grid() -> None:
    world, _ = sheet()
    terrain = bytearray(1000)
    terrain[999] = 3
    world.rules = replace(
        world.data(),
        terrain=bytes(terrain),
        sizes=((8, 10), *world.data().sizes[1:]),
    )
    actor = world.current_actor
    actor.map_x, actor.map_y = 310, 190
    assert world.tile() == 999
    assert (actor.tile_x, actor.tile_y) == (39, 24)
    assert world.grid_byte(world.data().terrain) == 3
