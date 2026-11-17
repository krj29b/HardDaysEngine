"""Fabricated executable operands and malformed shop tables (§12.29)."""

import struct

import pytest

from pymoonstone.assets.fight_palette import MogVersionError
from pymoonstone.assets.shop_data import _long, _price, _prices, _stock_rolls
from tests.unit.test_town_data import _mog


def test_price_pairs_and_equipment_stores_are_checked() -> None:
    code = bytearray(24)
    struct.pack_into(">HHH", code, 0, 0x0C68, 17, 74)
    struct.pack_into(">HHH", code, 6, 0x0468, 17, 74)
    struct.pack_into(">HIH", code, 12, 0x217C, 0x12345678, 92)
    mog = _mog(bytes(code), {})
    assert _price(mog, 0, 6) == 17
    assert _long(mog, 12, 0x217C, 92) == 0x12345678
    struct.pack_into(">H", code, 8, 18)
    with pytest.raises(MogVersionError, match="disagree"):
        _price(_mog(bytes(code), {}), 0, 6)
    with pytest.raises(MogVersionError, match="equipment"):
        _long(mog, 12, 0x217C, 88)


def test_market_initialization_reads_all_sparse_prices_and_displacements() -> None:
    code = bytearray(0xCC42)
    struct.pack_into(">HI", code, 0xCBF4, 0x41F9, 4)
    struct.pack_into(">HI", code, 0xC3F6, 0x47F9, 4)
    relocs = {1: (0xCBF6, 0xC3F8)}
    expected = tuple(index * 7 for index in range(12))
    for index, price in enumerate(expected):
        struct.pack_into(">HHH", code, 0xCBFA + index * 6, 0x317C, price, index * 2)
    assert _prices(_mog(bytes(code), relocs)) == expected
    struct.pack_into(">H", code, 0xCC40, 20)
    with pytest.raises(MogVersionError, match="displacement"):
        _prices(_mog(bytes(code), relocs))


def test_stock_loop_count_and_call_targets_come_from_checked_code() -> None:
    code = bytearray(0x4DDC)
    calls = (0x4D9C, 0x4DA2, 0x4DA8, 0x4DAE, 0x4DD0, 0x4DB6)
    for pos in calls:
        struct.pack_into(">HI", code, pos, 0x4EB9, 0x9A1C if pos == 0x4DB6 else 0x9A9A)
    struct.pack_into(">H", code, 0x4DCA, 0x7002)
    struct.pack_into(">HH", code, 0x4DD8, 0x51C8, 0xFFF2)
    relocs = {0: tuple(pos + 2 for pos in calls)}
    assert _stock_rolls(_mog(bytes(code), relocs)) == 3
    struct.pack_into(">I", code, 0x4DD2, 0x1110)
    with pytest.raises(MogVersionError, match="target"):
        _stock_rolls(_mog(bytes(code), relocs))
