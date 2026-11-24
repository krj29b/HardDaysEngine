"""Independent sequential reconstruction of original town records (§12.27)."""

from __future__ import annotations

import struct
from dataclasses import astuple
from typing import TYPE_CHECKING

import pytest

from harddaysengine.assets.adf import DiskSet
from harddaysengine.assets.hunk import parse_hunk_executable
from harddaysengine.assets.town_data import parse_town_data

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = pytest.mark.adf


def _walk_buttons(code: bytes, start: int) -> list[tuple[int, ...]]:
    """Execute sequential stores into a byte record, snapshotting registration calls."""
    pc, rows, record = start, [], bytearray(24)
    while True:
        op = struct.unpack_from(">H", code, pc)[0]
        if op == 0x4E75:
            return rows
        if op == 0x41F9:
            pc += 6
        elif op == 0x4EB9:
            target = struct.unpack_from(">I", code, pc + 2)[0]
            if target == 0x9480:
                width, height, _, x, y, identity, kind, field = struct.unpack_from(
                    ">HHIHHIHH", record, 4
                )
                rows.append((identity, x, y, width, height, kind, field))
            else:
                assert target == 0x94BA
            pc += 6
        elif op == 0x317C:
            value, offset = struct.unpack_from(">HH", code, pc + 2)
            struct.pack_into(">H", record, offset, value)
            pc += 6
        else:
            assert op == 0x217C
            value, offset = struct.unpack_from(">IH", code, pc + 2)
            struct.pack_into(">I", record, offset, value)
            pc += 8


def test_towns_match_independent_instruction_walker_and_string_reads(adf_dir: Path) -> None:
    executable = parse_hunk_executable("mog", DiskSet.from_directory(adf_dir).read_file("mog"))
    code, strings = executable.sections[0].data, executable.sections[4].data
    towns = parse_town_data(executable)
    for town, builder, cursor, name, picture in zip(
        towns, (0x153C, 0x15FE), (0x13F4, 0x12AE), (0x2D98, 0x2DD4), (0x2DC4, 0x2E00), strict=True
    ):
        expected = _walk_buttons(code, builder)
        assert len(expected) == 5
        assert [astuple(button) for button in town.buttons] == expected
        assert (town.pointer_x, town.pointer_y) == tuple(
            struct.unpack_from(">H", code, cursor + offset)[0] for offset in (2, 10)
        )
        pointer_cell = struct.unpack_from(">I", code, cursor + 4)[0]
        for actual, offset in (
            (town.name, struct.unpack_from(">I", code, name)[0]),
            (town.picture, struct.unpack_from(">I", code, picture)[0]),
            (town.pointer, pointer_cell + 10),
        ):
            assert actual == strings[offset : strings.index(b"\0", offset)].decode("ascii")
        # The real picture and pointer names must resolve without fallback aliases.
        disks = DiskSet.from_directory(adf_dir)
        assert disks.has_file(town.picture) and disks.has_file(town.pointer)
    expected_sheet = struct.unpack_from(">I", code, 0x14C4)[0]
    assert expected_sheet == 9
    assert towns[0].space_sheet == expected_sheet
    assert towns[1].space_sheet == expected_sheet
