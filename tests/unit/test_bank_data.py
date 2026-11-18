"""Synthetic executable bank loaders and symbolic instrument fixups (§6.2.10)."""

from __future__ import annotations

import zlib

import pytest

from harddaysengine.assets.bank_data import parse_banks
from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.hunk import HUNK_CODE, DecodedHunkExecutable, HunkSection
from harddaysengine.assets.mog_code import MogCode
from tests.unit.synth_setup import synth_data
from tests.unit.test_synth_data import _Hunk


def _mog(*, operation: int = 0xD3A8, destination: int = 0x144) -> MogCode:
    hunks = {
        0: _Hunk(bytearray(16)),
        1: _Hunk(bytearray(0x200)),
        4: _Hunk(bytearray(b"invented.a\0")),
        16: _Hunk(bytearray(0x294)),
        44: _Hunk(bytearray(0x1000)),
        45: _Hunk(bytearray(0x100)),
    }
    front, synth = hunks[16], hunks[44]
    front.at(0x10A).w(0x4EB9).ref(44, 0x800)
    front.at(0x274).w(0x203C).long(12)
    for pos in (0xF2, 0x134, *range(0x14C, 0x26A, 26)):
        front.at(pos).w(0x41F9).ref(4, 0).w(0x2279).ref(1, destination).w(0x203C).long(42)
        if pos == 0xF2:
            continue
        if pos == 0x134:
            front.w(0x6100, 0x26A - (pos + 20))
        else:
            front.w(0x4EB9).ref(16, 0x26A)
        front.w(0x4E75)
    synth.at(0x712).ref(45, 0x40)
    synth.at(0x800).w(0x41F9).ref(45, 0x4E)
    synth.w(0x2239).ref(1, destination).w(operation, 6).w(0xD1FC).long(14)
    synth.w(0xB1FC).ref(45, 0x6A).w(0x66E8)
    synth.w(0x41F9).ref(45, 0x5C).w(0x2179).ref(1, 0x140).w(6)
    synth.w(0x06A8).long(0x72).w(6, 0x4E75)
    sections = []
    for index in range(46):
        h = hunks.get(index, _Hunk(bytearray()))
        relocs = {target: tuple(values) for target, values in h.relocs.items()}
        sections.append(HunkSection(index, HUNK_CODE, len(h.data), bytes(h.data), relocs))
    return MogCode(
        DecodedHunkExecutable("invented", tuple(sections)), supported=(zlib.crc32(bytes(16)),)
    )


def test_banks_come_from_operands_and_replacements_overwrite_the_original_offset() -> None:
    result = parse_banks(_mog(), synth_data({}))
    assert (result.startup.name, result.startup.slot, result.startup.size) == ("invented.a", 2, 42)
    assert len(result.loaders) == 12
    assert result.header_bytes == 12
    assert result.samples == ((0, 8), (2, 64), (1, 0x72))
    assert result.named("INVENTED.A") == result.startup


@pytest.mark.parametrize(("operation", "destination"), ((0x4E71, 0x144), (0xD3A8, 0x154)))
def test_unknown_instructions_and_nonbank_cells_are_rejected(
    operation: int, destination: int
) -> None:
    with pytest.raises(MogVersionError):
        parse_banks(_mog(operation=operation, destination=destination), synth_data({}))
