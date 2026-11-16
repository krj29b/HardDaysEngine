"""Synthetic executable operands exercise the flight decoder independently (§10.23)."""

import struct
import zlib

import pytest

from pymoonstone.assets.caster_rules import _flight, _moveq
from pymoonstone.assets.fight_palette import MogVersionError
from pymoonstone.assets.hunk import HUNK_CODE, HUNK_DATA, DecodedHunkExecutable, HunkSection
from pymoonstone.assets.mog_code import MogCode


def _mog(*, step_count: int = 0x7E02, aim_opcode: int = 0x0C79) -> MogCode:
    data = bytearray(0x7500)
    for pos, opcode, value in (
        (0x7388, 0x7E02, 0),
        (0x747E, step_count, 0),
        (0x73D4, 0xB07C, 9),
        (0x7180, aim_opcode, 6),
        (0x718A, 0x33FC, 7),
        (0x7192, 0x0C79, 8),
        (0x719C, 0x33FC, 9),
        (0x71A4, 0x33FC, 13),
    ):
        struct.pack_into(">HH", data, pos, opcode, value)
    code = bytes(data)
    section = HunkSection(0, HUNK_CODE, len(code), code, {})
    sections = (section, *(HunkSection(i, HUNK_DATA, 0, b"", {}) for i in range(1, 5)))
    return MogCode(DecodedHunkExecutable("mog", sections), supported=(zlib.crc32(code),))


def test_flight_pool_and_tunables_are_read_from_synthetic_operands() -> None:
    rules = _flight(_mog())
    assert (rules.slots, rules.level_threshold, rules.minimum_arc, rules.fallback_arc) == (
        3,
        9,
        7,
        13,
    )


def test_disagreeing_slot_counts_or_wrong_instruction_are_rejected() -> None:
    with pytest.raises(MogVersionError, match="pool size"):
        _flight(_mog(step_count=0x7E03))
    with pytest.raises(MogVersionError, match="expected"):
        _flight(_mog(aim_opcode=0xB07C))
    with pytest.raises(MogVersionError, match="MOVEQ"):
        _moveq(_mog(), 0x7388, 3)
