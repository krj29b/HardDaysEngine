"""Synthetic Troll table reads retain adjacent bytes; shake operands are data (§10.27)."""

import struct
import zlib

import pytest

from pymoonstone.assets.fight_palette import MogVersionError
from pymoonstone.assets.hunk import HUNK_CODE, HUNK_DATA, DecodedHunkExecutable, HunkSection
from pymoonstone.assets.mog_code import MogCode
from pymoonstone.assets.screen_fx import shake_rules
from pymoonstone.assets.stalker_rules import _steps


def _mog(*, right: int = 0x800, reload: int = 2, last: int = 0xFFFF) -> MogCode:
    code = bytearray(0x917C)
    struct.pack_into(">HH", code, 0x90D0, 0x33FC, 2)
    struct.pack_into(">HH", code, 0x90D8, 0x33FC, reload)
    struct.pack_into(">HI", code, 0x90E0, 0x23FC, 0x9162)
    struct.pack_into(">3H", code, 0x9162, 0x0300, 0xFE00, last)
    data = bytes(code)
    code_section = HunkSection(0, HUNK_CODE, len(data), data, {0: (0x90E2,)})
    creature = bytearray(0x900)
    struct.pack_into(">HI", creature, 0x58A, 0x43F9, 0x800)
    struct.pack_into(">HI", creature, 0x5B4, 0x43F9, right)
    struct.pack_into(">16H", creature, 0x800, *range(100, 116))
    sections = [code_section]
    sections.extend(HunkSection(i, HUNK_DATA, 0, b"", {}) for i in range(1, 40))
    sections.append(
        HunkSection(40, HUNK_CODE, len(creature), bytes(creature), {40: (0x58C, 0x5B6)})
    )
    return MogCode(
        DecodedHunkExecutable("synthetic", tuple(sections)), supported=(zlib.crc32(data),)
    )


def test_steps_read_eight_pairs_including_the_original_overread_extent() -> None:
    assert _steps(_mog()) == tuple(range(100, 116))
    with pytest.raises(MogVersionError, match="tables differ"):
        _steps(_mog(right=0x802))


def test_shake_period_and_signed_offsets_are_read_from_operands_and_table() -> None:
    rules = shake_rules(_mog())
    assert rules.period == 2 and rules.offsets == (3, -2)


@pytest.mark.parametrize(("reload", "last"), [(3, 0xFFFF), (2, 0x0001), (2, 0)])
def test_shake_rejects_inconsistent_period_bad_words_or_missing_terminator(
    reload: int, last: int
) -> None:
    with pytest.raises(MogVersionError):
        shake_rules(_mog(reload=reload, last=last))
