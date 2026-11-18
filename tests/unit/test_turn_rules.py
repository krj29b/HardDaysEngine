"""Calendar text-list validation on a synthetic executable (§11.19)."""

import struct
import zlib

import pytest

from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.hunk import HUNK_CODE, DecodedHunkExecutable, HunkSection
from harddaysengine.assets.mog_code import MogCode, Pointer
from harddaysengine.assets.turn_rules import text_list
from harddaysengine.sim.turn_rules import TextLine


def _mog(*, cycle: bool = False, zero_cycle: bool = False) -> MogCode:
    data = bytearray(64)
    struct.pack_into(">IHHHI", data, 0, 32, 5, 7, 1, 14 if cycle else 0)
    struct.pack_into(">IHHHI", data, 14, 32, 8, 9, 0, 14 if cycle and not zero_cycle else 0)
    data[32:38] = b"Hello\0"
    code = bytes(8)
    sections = tuple(
        HunkSection(
            index=i,
            hunk_type=HUNK_CODE,
            alloc_size=len(part),
            data=part,
            relocs={4: (0, 10, 14, 24) if cycle else (0, 14)} if i == 4 else {},
        )
        for i, part in enumerate((code, bytes(4), bytes(4), bytes(4), bytes(data)))
    )
    return MogCode(
        DecodedHunkExecutable(name="synthetic", sections=sections), supported=(zlib.crc32(code),)
    )


def test_calendar_text_fields_are_loaded_without_assuming_strings_follow_records() -> None:
    assert text_list(_mog(), Pointer(4, 0)) == (TextLine("Hello", 5, 7, 1),)


@pytest.mark.parametrize("zero_cycle", [False, True])
def test_calendar_text_cycle_and_truncated_record_raise(zero_cycle: bool) -> None:
    with pytest.raises(MogVersionError, match="cyclic"):
        text_list(_mog(cycle=True, zero_cycle=zero_cycle), Pointer(4, 0))
    with pytest.raises(MogVersionError, match="outside its data hunk"):
        text_list(_mog(), Pointer(4, 60))
