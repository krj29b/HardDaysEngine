"""Checked original smith/market operands and stock rules (ARCHITECTURE.md §12.29)."""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.map_rules import _bytes
from harddaysengine.assets.mog_code import MogCode, Pointer
from harddaysengine.sim.shop_types import ShopRules, StockRules

if TYPE_CHECKING:
    from harddaysengine.assets.hunk import DecodedHunkExecutable


def _price(mog: MogCode, compare: int, debit: int) -> int:
    price = mog.operand(compare, 0x0C68, "smith price", displacement=74)
    if price != mog.operand(debit, 0x0468, "smith debit", displacement=74):
        raise MogVersionError("mog: smith comparison and debit disagree")
    return price


def _long(mog: MogCode, pos: int, opcode: int, field: int) -> int:
    mog.expect(pos, (opcode,), "smith equipment")
    if mog.word(pos + 6) != field or mog.reloc(pos + 2) is not None:
        raise MogVersionError("mog: unexpected smith equipment store or relocation")
    return mog.long(pos + 2)


def _prices(mog: MogCode) -> tuple[int, ...]:
    target = mog.lea(0xCBF4, 0x41F9, "market price initialization")
    if target.hunk != 1 or mog.lea(0xC3F6, 0x47F9, "market prices") != target:
        raise MogVersionError("mog: market price addresses disagree")
    if target.offset < 0 or target.offset + 24 > mog.executable.sections[1].alloc_size:
        raise MogVersionError("mog: market prices outside their allocation")
    return tuple(
        mog.operand(0xCBFA + index * 6, 0x317C, "market price", displacement=index * 2)
        for index in range(12)
    )


def _shop_rules(mog: MogCode) -> ShopRules:
    armour = tuple(
        (
            _price(mog, compare, debit),
            _long(mog, store, 0x217C, 92),
            mog.operand(gain, 0x0668, "smith HP gain", displacement=80),
        )
        for compare, debit, store, gain in (
            (0xC2C4, 0xC2CE, 0xC2D4, 0xC2DC),
            (0xC2F8, 0xC302, 0xC308, 0xC310),
            (0xC32C, 0xC336, 0xC33C, 0xC344),
        )
    )
    swords = []
    for compare, debit, item_compare, store in (
        (0xC360, 0xC376, 0xC36A, 0xC37C),
        (0xC394, 0xC3AA, 0xC39E, 0xC3B0),
    ):
        item = _long(mog, store, 0x217C, 88)
        if _long(mog, item_compare, 0x0CA8, 88) != item:
            raise MogVersionError("mog: smith sword comparison/store disagree")
        swords.append((_price(mog, compare, debit), item))
    cap = mog.operand(0xC492, 0x0C6A, "market gold cap", displacement=74)
    if cap != mog.operand(0xC49A, 0x357C, "market gold clamp", displacement=74):
        raise MogVersionError("mog: market gold caps disagree")
    mog.expect(0xC48C, (0xE24F,), "market sell shift")
    return ShopRules(
        armour,
        tuple(swords),
        _price(mog, 0xC3C0, 0xC3D0),
        mog.operand(0xC3C8, 0x0C28, "smith dagger cap", displacement=76),
        _prices(mog),
        cap,
        1,
        mog.operand(0xC3FC, 0x0C79, "market pointer split"),
    )


def parse_shop_rules(executable: DecodedHunkExecutable) -> ShopRules:
    """Read human smith operands and all twelve market prices (§12.29)."""
    return _shop_rules(MogCode(executable))


def _stock_address(mog: MogCode) -> None:
    stock = mog.lea(0x4DBC, 0x41F9, "market stock clear")
    if stock.hunk != 1 or any(
        mog.lea(pos, 0x41F9, "market stock") != stock for pos in (0x4DDC, 0x9AEE)
    ):
        raise MogVersionError("mog: market stock addresses disagree")
    if stock.offset < 0 or stock.offset + 24 > mog.executable.sections[1].alloc_size:
        raise MogVersionError("mog: market stock outside its allocation")


def _stock_rolls(mog: MogCode) -> int:
    for call in (0x4D9C, 0x4DA2, 0x4DA8, 0x4DAE, 0x4DD0):
        if mog.call_target(call, "initial item roll") != Pointer(0, 0x9A9A):
            raise MogVersionError("mog: unexpected initial item roll target")
    if mog.call_target(0x4DB6, "initial gold roll") != Pointer(0, 0x9A1C):
        raise MogVersionError("mog: unexpected initial gold roll target")
    op = mog.word(0x4DCA)
    if op & 0xFF80 != 0x7000:
        raise MogVersionError("mog: invalid market stock loop count")
    mog.expect(0x4DD8, (0x51C8,), "market stock loop")
    mog.expect(0x4DDA, (0xFFF2,), "market stock loop back edge")
    return (op & 127) + 1


def _stock_rules(mog: MogCode) -> StockRules:
    _stock_address(mog)
    rolls = _stock_rolls(mog)
    extra = mog.operand(0x4DE2, 0x0628, "market extra stock", displacement=8)
    bands = mog.lea(0x4F1A, 0x41F9, "lair loot odds")
    rows = tuple(struct.iter_unpack(">HH", _bytes(mog, bands, 16)))
    if rows[-1][0] < 100 or any(kind not in range(1, 5) for _, kind in rows):
        raise MogVersionError("mog: invalid lair loot odds")
    mog.expect(0x4E48, (0x0280,), "key draw mask")
    return StockRules(
        4,
        rolls,
        8,
        extra,
        mog.long(0x4E4A),
        mog.operand(0x4E4E, 0xB03C, "key draw limit"),
        tuple(
            mog.operand(pos, 0x137C, "lair key mask", displacement=20)
            for pos in (0x4E62, 0x4E72, 0x4E82, 0x4E92)
        ),
        rows,
    )


def parse_stock_rules(executable: DecodedHunkExecutable) -> StockRules:
    """Decode original hoard, stock and lair initialization operands (§12.29)."""
    return _stock_rules(MogCode(executable))
