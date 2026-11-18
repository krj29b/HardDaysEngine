"""Independent original price/startup reads and campaign trades (§12.29)."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pytest

from harddaysengine.app.audit import AuditRig
from harddaysengine.app.game import GameScene, GameSession
from harddaysengine.app.input import InputFrame, PlayerInput
from harddaysengine.assets.adf import DiskSet
from harddaysengine.assets.hunk import parse_hunk_executable
from harddaysengine.assets.shop_data import parse_shop_rules, parse_stock_rules
from harddaysengine.sim.types import KnightId

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.adf


def test_prices_match_independent_instruction_operands(adf_dir: Path) -> None:
    exe = parse_hunk_executable("mog", DiskSet.from_directory(adf_dir).read_file("mog"))
    rules, stock = parse_shop_rules(exe), parse_stock_rules(exe)
    code = exe.sections[0].data

    def word(pos: int) -> int:
        return int.from_bytes(code[pos : pos + 2], "big")

    def long(pos: int) -> int:
        return int.from_bytes(code[pos : pos + 4], "big")

    assert rules.armour == tuple(
        (word(price + 2), long(item + 2), word(hp + 2))
        for price, item, hp in (
            (0xC2C4, 0xC2D4, 0xC2DC),
            (0xC2F8, 0xC308, 0xC310),
            (0xC32C, 0xC33C, 0xC344),
        )
    )
    assert rules.swords == tuple(
        (word(p + 2), long(i + 2)) for p, i in ((0xC360, 0xC37C), (0xC394, 0xC3B0))
    )
    assert rules.prices == tuple(word(0xCBFC + i * 6) for i in range(12))
    assert (rules.dagger_price, rules.dagger_cap, rules.gold_cap) == (
        word(0xC3C2),
        word(0xC3CA),
        word(0xC494),
    )
    assert stock.market_rolls == (word(0x4DCA) & 255) + 1


@dataclass
class _Oracle:
    state: int
    bands: tuple[tuple[int, int], ...]
    last: int = -1

    def draw(self) -> int:
        for _ in range(8):
            rotate = ((self.state >> 3) | (self.state << 29)) & 0xFFFFFFFF
            bit = ((rotate ^ self.state) >> 1) & 1
            self.state = (self.state >> 1) | (bit << 31)
        return self.state

    def percent(self) -> int:
        value = self.draw() & 127
        return value - 27 if value >= 100 else value

    def gift(self, inv: list[int]) -> None:
        while True:
            roll = self.percent()
            slot = next(slot for limit, slot in self.bands if roll <= limit)
            if slot == self.last:
                continue
            self.last = slot
            if slot == 4 and inv[2]:
                continue
            inv[slot // 2] += 1
            return

    def gold(self) -> int:
        value = self.draw() & 31
        return (value - 10 if value > 21 else value) + 10


def _lair_oracle(
    oracle: _Oracle, code: bytes, odds: tuple[tuple[int, int], ...]
) -> tuple[list[list[int]], list[int]]:
    key = oracle.draw() & 7
    while key > 5:
        key = oracle.draw() & 7
    lairs = [[0] * 12 for _ in range(24)]
    for group, pos in enumerate((0x4E64, 0x4E74, 0x4E84, 0x4E94)):
        lairs[group * 6 + key][10] = int.from_bytes(code[pos : pos + 2], "big")
    amounts = []
    for inv in lairs:
        roll = oracle.percent()
        kind = next(kind for limit, kind in odds if roll <= limit)
        amounts.append(oracle.gold() if kind in (1, 3) else 0)
        for _ in range({2: 3, 3: 2}.get(kind, 0)):
            oracle.gift(inv)
    return lairs, amounts


@pytest.mark.parametrize("seed", range(4))
def test_startup_inventories_match_independent_rng_and_table_oracle(
    adf_dir: Path, seed: int
) -> None:
    disks = DiskSet.from_directory(adf_dir)
    exe = parse_hunk_executable("mog", disks.read_file("mog"))
    code, data = exe.sections[0].data, exe.sections[4].data
    session = GameSession(seed=seed, disk_set=disks)
    world = session.overworld
    initial = world.data().seeds[seed]
    bands_at = int.from_bytes(code[0x9AA2:0x9AA6], "big")
    bands = tuple(struct.iter_unpack(">II", data[bands_at : bands_at + 80]))
    loot_at = int.from_bytes(code[0x4F1C:0x4F20], "big")
    odds = tuple(struct.iter_unpack(">HH", data[loot_at : loot_at + 16]))

    oracle = _Oracle(initial, bands)
    hoard, stock = [0] * 12, [0] * 12
    for _ in range(4):
        oracle.gift(hoard)
    hoard_gold = oracle.gold()
    count = (int.from_bytes(code[0x4DCA:0x4DCC], "big") & 255) + 1
    for _ in range(count):
        oracle.gift(stock)
    stock[4] += int.from_bytes(code[0x4DE4:0x4DE6], "big")
    lairs, amounts = _lair_oracle(oracle, code, odds)
    assert world.market.counts == stock
    assert world.inventories[5].counts == hoard and world.dragon.gold == hoard_gold
    assert [lair.loot.counts for lair in world.lairs] == lairs
    assert [lair.gold for lair in world.lairs] == amounts
    assert world.prng.state == oracle.state and world.gifts.last == oracle.last


def _click(session: GameSession, field: int, side: int, mask: int = 0) -> None:
    screen = session.sheet_screen
    assert screen is not None
    region = next(
        r
        for r in screen.state.regions
        if r.field == field and r.side == side and r.mask == mask and not r.id
    )
    screen.state.pointer_x, screen.state.pointer_y = region.x, region.y
    session.step_tick(InputFrame(players={KnightId.RED: PlayerInput(fire=True)}))


def test_natural_stock_purchase_sale_and_return_persist(adf_dir: Path) -> None:
    session = GameSession(disk_set=DiskSet.from_directory(adf_dir))
    session._open_town(KnightId.RED, waterdeep=False)
    while session.palette_fx.is_fading:
        session.step_tick(InputFrame())
    screen = session.town_screen
    assert screen is not None
    button = screen.data.buttons[3]
    screen.state.pointer_x, screen.state.pointer_y = button.x, button.y
    session.step_tick(InputFrame(players={KnightId.RED: PlayerInput(fire=True)}))
    world, actor = session.overworld, session.overworld.current_actor
    assert session.scene == GameScene.STATUS and session.sheet_screen is not None
    rules = session.sheet_screen.rules.shop
    assert rules is not None
    # Talisman stock includes the original two additional pieces at startup.
    before, actor.gold = world.market.byte(8), 149
    rng = world.prng.state
    _click(session, 8, 1)
    assert actor.gold == 149 - rules.prices[4]
    assert world.market.byte(8) == before - 1 and world.inventories[1].byte(8) == 1
    _click(session, 8, 0)
    assert world.market.byte(8) == before and world.inventories[1].byte(8) == 0
    assert actor.gold == 149 - rules.prices[4] + (rules.prices[4] >> rules.sell_shift)
    assert session.sheet_screen.state.sounds == ["sell"] and world.prng.state == rng
    state = session.sheet_screen.state
    done = next(r for r in state.regions if r.id == 7)
    state.pointer_x, state.pointer_y = done.x, done.y
    session.step_tick(InputFrame(players={KnightId.RED: PlayerInput(fire=True)}))
    assert session.scene == GameScene.LOCATION
    session._open_sheet(6)
    assert world.market.byte(8) == before and world.prng.state == rng


@pytest.mark.parametrize("waterdeep", [False, True])
def test_original_smith_offers_and_prices_drive_host_purchase(
    adf_dir: Path, waterdeep: bool
) -> None:
    session = GameSession(disk_set=DiskSet.from_directory(adf_dir))
    session._open_town(KnightId.RED, waterdeep=waterdeep)
    while session.palette_fx.is_fading:
        session.step_tick(InputFrame())
    session._open_sheet(5)
    actor = session.overworld.current_actor
    assert session.sheet_screen is not None and session.sheet_screen.rules.shop is not None
    rules = session.sheet_screen.rules.shop
    actor.gold, actor.hp, actor.armour = 100, 1, 30
    _click(session, 92, 1, 1)
    assert (actor.gold, actor.armour, actor.hp) == (
        100 - rules.armour[0][0],
        rules.armour[0][1],
        1 + rules.armour[0][2],
    )
    _click(session, 88, 1, 1)
    assert actor.sword == rules.swords[0][1]
    assert actor.gold == 100 - rules.armour[0][0] - rules.swords[0][0]


def test_reset_recreates_original_stock_and_hash_covers_stock(adf_dir: Path) -> None:
    session = GameSession(disk_set=DiskSet.from_directory(adf_dir))
    world = session.overworld
    expected = (world.market.counts.copy(), world.prng.state, world.gifts.last)
    before = session.state_hash()
    world.market.counts[0] += 1
    assert session.state_hash() != before
    session._reset_campaign(1)
    world = session.overworld
    assert (world.market.counts, world.prng.state, world.gifts.last) == expected


def test_bottom_edge_suffix_matches_original_adjacent_bytes(adf_dir: Path) -> None:
    disks = DiskSet.from_directory(adf_dir)
    exe = parse_hunk_executable("mog", disks.read_file("mog"))
    session = GameSession(disk_set=disks)
    world = session.overworld
    actor = world.current_actor
    actor.map_x, actor.map_y = 305, 190
    index = world.tile()
    assert index == 1038
    code, data = exe.sections[36].data, exe.sections[4].data
    for load, grid, tail in (
        (0x8BA, world.data().terrain, world.data().terrain_tail),
        (0x13FE, world.data().regions, world.data().region_tail),
    ):
        start = int.from_bytes(code[load + 2 : load + 6], "big")
        assert tail == data[start + 1000 : start + 1040]
        assert world.grid_byte(grid, tail) == data[start + index]


@pytest.mark.gl
@pytest.mark.parametrize("number", [5, 6])
def test_shop_render_is_pure_after_transaction(adf_dir: Path, number: int) -> None:
    session = GameSession(disk_set=DiskSet.from_directory(adf_dir))
    session._open_sheet(number)
    session.overworld.current_actor.gold = 149
    for _ in range(24):
        session.step_tick(InputFrame())
    _click(session, 76 if number == 5 else 8, 1)
    rig = AuditRig(adf_dir)
    try:
        before = session.state_hash()
        first, second = rig.capture(session, "shop"), rig.capture(session, "shop_again")
        assert session.state_hash() == before
        assert np.array_equal(first.rgb, second.rgb)
    finally:
        rig.release()
