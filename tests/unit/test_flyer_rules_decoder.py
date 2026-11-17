"""Synthetic Flyer alternation word, step reads and relocation validation (§10.26)."""

import struct
import zlib

import pytest

from pymoonstone.assets.fight_palette import MogVersionError
from pymoonstone.assets.flyer_rules import _tables
from pymoonstone.assets.hunk import HUNK_CODE, HUNK_DATA, DecodedHunkExecutable, HunkSection
from pymoonstone.assets.mog_code import MogCode


def _mog(*, toggle: int = 0x5C1E, target: int = 0, opcode: int = 0x45F9) -> MogCode:
    code = bytearray(0x5C28)
    struct.pack_into(">I", code, 0x5AEA, toggle)
    struct.pack_into(">HI", code, 0x5B62, opcode, 0x5C20)
    struct.pack_into(">5H", code, 0x5C1E, 9, 5, 11, 7, 13)
    data = bytes(code)
    section = HunkSection(0, HUNK_CODE, len(data), data, {target: (0x5AEA, 0x5B64)})
    sections = (section, *(HunkSection(i, HUNK_DATA, 0, b"", {}) for i in range(1, 5)))
    return MogCode(DecodedHunkExecutable("synthetic", sections), supported=(zlib.crc32(data),))


def test_initial_word_and_steps_are_read_from_the_synthetic_executable() -> None:
    assert _tables(_mog(), 4) == (9, (5, 11, 7, 13))


@pytest.mark.parametrize(
    ("toggle", "target", "opcode", "phases"),
    [
        (0x5C1C, 0, 0x45F9, 4),
        (0x5C1E, 4, 0x45F9, 4),
        (0x5C1E, 0, 0x43F9, 4),
        (0x5C1E, 0, 0x45F9, 3),
    ],
)
def test_wrong_table_identity_adjacency_extent_or_instruction_is_rejected(
    toggle: int, target: int, opcode: int, phases: int
) -> None:
    with pytest.raises(MogVersionError):
        _tables(_mog(toggle=toggle, target=target, opcode=opcode), phases)
