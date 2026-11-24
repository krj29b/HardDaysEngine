"""Invented computer-knight code operands and overlapping tables (§10.31)."""

import struct
import zlib

import pytest

from harddaysengine.assets.computer_knight_rules import _RANGES, HUNK, _rules
from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.hunk import HUNK_CODE, HUNK_DATA, DecodedHunkExecutable, HunkSection
from harddaysengine.assets.mog_code import MogCode


def _mog(*, corrupt: str = "") -> MogCode:
    code, body, data = bytes(64), bytearray(0x1130), bytes(range(128))
    for i, (name, pos) in enumerate(_RANGES.items()):
        struct.pack_into(">HH", body, pos, 0xB07C if name == "guard_range" else 0xB27C, 13 + i)
    struct.pack_into(">HH", body, 0xD94, 0xB27C, 13 + int(corrupt == "range"))
    struct.pack_into(">HH", body, 0xE36, 0xB07C, 16)
    relocations = {4: [], 41: []}
    for pos, opcode, hunk, offset in (
        (0xD5C, 0x43F9, 41, 0),
        (0xD6E, 0x43F9, 41, 0),
        (0xD38, 0x43F9, 41, 20),
        (0xD4A, 0x43F9, 41, 36),
        (0xDD2, 0x4BF9, 4, 7),
        (0xE60, 0x4BF9, 4, 7 + int(corrupt == "odds")),
    ):
        struct.pack_into(">HI", body, pos, opcode, offset)
        relocations[hunk].append(pos + 2)
    struct.pack_into(">HH", body, 0x1072, 0x33FC, 65533)
    struct.pack_into(">HH", body, 0x1094, 0x33FC if corrupt != "instruction" else 0, 3)
    sections = [HunkSection(0, HUNK_CODE, len(code), code, {})]
    sections.extend(HunkSection(i, HUNK_DATA, len(data), data, {}) for i in range(1, HUNK))
    sections.append(
        HunkSection(
            HUNK, HUNK_CODE, len(body), bytes(body), {h: tuple(p) for h, p in relocations.items()}
        )
    )
    steps = struct.pack(">38H", *range(101, 139))
    sections.append(HunkSection(41, HUNK_DATA, len(steps), steps, {}))
    return MogCode(
        DecodedHunkExecutable("synthetic", tuple(sections)), supported=(zlib.crc32(code),)
    )


def test_decodes_synthetic_ranges_bounded_odds_and_four_pair_step_tables() -> None:
    rules = _rules(_mog())
    assert (rules.strike8_range, rules.heavy_range, rules.strike4_range, rules.guard_range) == (
        13,
        14,
        15,
        16,
    )
    assert rules.steps.side == tuple(range(101, 109))
    assert rules.steps.up == tuple(range(111, 119))
    assert rules.steps.down == tuple(range(119, 127))
    assert rules.steps.up_override == 65533 and rules.steps.down_override == 3
    assert rules.odds == bytes(range(7, 27))


@pytest.mark.parametrize("corrupt", ["range", "odds", "instruction"])
def test_corrupt_instructions_and_paired_operands_raise(corrupt: str) -> None:
    with pytest.raises(MogVersionError):
        _rules(_mog(corrupt=corrupt))


def test_truncated_odds_hunk_raises() -> None:
    mog = _mog()
    sections = list(mog.executable.sections)
    sections[4] = HunkSection(4, HUNK_DATA, 20, bytes(20), {})
    truncated = MogCode(
        DecodedHunkExecutable("synthetic", tuple(sections)),
        supported=(zlib.crc32(sections[0].data),),
    )
    with pytest.raises(MogVersionError, match="outside its data hunk"):
        _rules(truncated)
