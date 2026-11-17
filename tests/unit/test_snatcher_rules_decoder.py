"""Synthetic relocated snatcher reads reject wrong instructions and hunks (§10.25)."""

import struct
import zlib

import pytest

from pymoonstone.assets.fight_palette import MogVersionError
from pymoonstone.assets.hunk import HUNK_CODE, HUNK_DATA, DecodedHunkExecutable, HunkSection
from pymoonstone.assets.mog_code import MogCode
from pymoonstone.assets.snatcher_rules import _pointer


def _mog(*, opcode: int = 0x23FC, target: int = 4) -> MogCode:
    code = b"\x4e\x75"
    data = struct.pack(">HI", opcode, 1234)
    sections = [HunkSection(0, HUNK_CODE, len(code), code, {})]
    sections.extend(HunkSection(i, HUNK_DATA, 0, b"", {}) for i in range(1, 40))
    sections.append(HunkSection(40, HUNK_CODE, len(data), data, {target: (2,)}))
    return MogCode(
        DecodedHunkExecutable("synthetic", tuple(sections)), supported=(zlib.crc32(code),)
    )


def test_script_operand_is_read_from_relocated_synthetic_hunk() -> None:
    assert _pointer(_mog(), 0, 0x23FC, 4) == 1234


@pytest.mark.parametrize(("opcode", "target"), [(0x33FC, 4), (0x23FC, 0)])
def test_script_pointer_requires_instruction_and_target_hunk(opcode: int, target: int) -> None:
    with pytest.raises(MogVersionError):
        _pointer(_mog(opcode=opcode, target=target), 0, 0x23FC, 4)
