"""The checked reads of the creature-rule decoder on synthetic code (ARCHITECTURE.md §10.20)."""

from __future__ import annotations

import struct
import zlib

import pytest

from harddaysengine.assets.creature_rules import HELPER_HUNK, _check_helper, _moveq
from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.hunk import HUNK_CODE, HUNK_DATA, DecodedHunkExecutable, HunkSection
from harddaysengine.assets.mog_code import DATA_HUNK, MogCode

_CODE = struct.pack(
    ">3H 3H H H H 3H",
    *(0x4EB9, 0x0000, 0x0010),  # 0: JSR $10 (helper hunk)
    *(0x4EB9, 0x0000, 0x0012),  # 6: JSR $12 (helper hunk)
    0x7003,  # 12: MOVEQ #3,D0
    0x70FE,  # 14: MOVEQ #-2,D0
    0x7203,  # 16: MOVEQ #3,D1
    *(0x23FC, 0x0000, 0x0040),  # 18: MOVE.L #$40,... (data hunk)
)
_HELPERS = bytes(0x10) + struct.pack(">2H 3H", 0x7E01, 0x4E75, 0x33FC, 0xFFFB, 0x0000)


def _section(index: int, data: bytes, relocs: dict[int, tuple[int, ...]]) -> HunkSection:
    kind = HUNK_CODE if index in (0, HELPER_HUNK) else HUNK_DATA
    return HunkSection(index=index, hunk_type=kind, alloc_size=len(data), data=data, relocs=relocs)


def _mog() -> MogCode:
    sections = [_section(i, bytes(64), {}) for i in range(HELPER_HUNK + 1)]
    sections[0] = _section(0, _CODE, {HELPER_HUNK: (2, 8), DATA_HUNK: (20,)})
    sections[HELPER_HUNK] = _section(HELPER_HUNK, _HELPERS, {})
    return MogCode(DecodedHunkExecutable("mog", tuple(sections)), supported=(zlib.crc32(_CODE),))


def test_a_helper_is_checked_by_its_call_and_its_first_word() -> None:
    mog = _mog()
    _check_helper(mog, 0, 0x10, 0x7E01, "t")
    with pytest.raises(MogVersionError, match="another routine"):
        _check_helper(mog, 6, 0x10, 0x7E01, "t")
    with pytest.raises(MogVersionError, match=r"expected \$7E01"):
        _check_helper(mog, 6, 0x12, 0x7E01, "t")


def test_moveq_is_sign_extended_and_checked() -> None:
    mog = _mog()
    assert _moveq(mog, 12, "t") == 3
    assert _moveq(mog, 14, "t") == 0xFFFE
    with pytest.raises(MogVersionError, match="MOVEQ"):
        _moveq(mog, 16, "t")


def test_operands_of_another_hunk_and_relocated_operands() -> None:
    mog = _mog()
    assert mog.operand(0x14, 0x33FC, "t", hunk=HELPER_HUNK) == 0xFFFB
    with pytest.raises(MogVersionError, match=f"hunk {HELPER_HUNK}"):
        mog.operand(0x14, 0x303C, "t", hunk=HELPER_HUNK)
    assert mog.relocated(18, 0x23FC, DATA_HUNK, "t") == 0x40
    with pytest.raises(MogVersionError, match="does not point into hunk 0"):
        mog.relocated(18, 0x23FC, 0, "t")
