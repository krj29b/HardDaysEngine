"""Checked computer-map operands from mog (ARCHITECTURE.md §11.20)."""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

from pymoonstone.assets.map_rules import _bytes, _pointer
from pymoonstone.assets.mog_code import MogCode
from pymoonstone.sim.ai_map_state import MapAiRules

if TYPE_CHECKING:
    from pymoonstone.assets.hunk import DecodedHunkExecutable


def parse_ai_map_rules(executable: DecodedHunkExecutable) -> MapAiRules:
    """Read prices, equipment and town coordinates from checked instructions (§11.20)."""
    mog = MogCode(executable)

    def word(pos: int, opcode: int) -> int:
        return mog.operand(pos, opcode, "computer map", hunk=36)

    def long(pos: int, opcode: int) -> int:
        mog.operand(pos, opcode, "computer map long", hunk=36)
        value: int = struct.unpack_from(">I", mog.hunk_data(36), pos + 2)[0]
        return value

    towns = []
    for x, y, target in ((0x10D4, 0x10D8, 0x1108), (0x10EE, 0x10F2, 0x1114)):
        packed = long(target, 0x23FC)
        towns.append((word(x, 0x343C), word(y, 0x363C), packed >> 16, packed & 65535))
    return MapAiRules(
        engage=word(0xAD2, 0xB07C),
        stat_price=struct.unpack(">H", _bytes(mog, _pointer(mog, 36, 0xF3C, 0x3039, 4), 2))[0],
        rest_lives=word(0xE36, 0x0C28),
        rest_cap_test=mog.operand(0xBEA6, 0x0C28, "rest life test", displacement=73),
        rest_cap=mog.operand(0xBEAE, 0x117C, "rest life cap", displacement=73),
        minimum_gold=word(0xF7C, 0x0C68),
        life_price=word(0xF98, 0x33FC),
        life_threshold=word(0xF8E, 0x0C28),
        armour=tuple(
            (word(price, 0x33FC), long(item, 0x23FC))
            for price, item in ((0xFC0, 0xFD0), (0xFF2, 0x1002), (0x1024, 0x1034))
        ),
        claymore=(word(0x1056, 0x33FC), long(0x1066, 0x23FC)),
        broad=(word(0x1082, 0x33FC), long(0x1078, 0x0CA8)),
        dagger_price=word(0x10A8, 0x33FC),
        dagger_threshold=word(0x109E, 0x0C28),
        dagger_cap=word(0x1190, 0x0C68),
        towns=tuple(towns),
    )
