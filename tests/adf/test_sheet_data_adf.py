"""Independent reads of original sheet initializers and panel lists (§12.25)."""

from __future__ import annotations

import struct
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from pymoonstone.assets.adf import DiskSet
from pymoonstone.assets.fight_palette import MogVersionError
from pymoonstone.assets.hunk import parse_hunk_executable
from pymoonstone.assets.sheet_data import parse_sheet_data, parse_sheet_rules

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.adf


def test_sheet_tables_match_independent_store_walker(adf_dir: Path) -> None:
    executable = parse_hunk_executable("mog", DiskSet.from_directory(adf_dir).read_file("mog"))
    decoded = parse_sheet_data(executable)
    code, strings = executable.sections[0].data, executable.sections[4].data
    tables: dict[int, dict[int, int]] = {}
    pc, target = 0xC6E0, 0
    while pc < 0xCBF4:
        op = int.from_bytes(code[pc : pc + 2], "big")
        if op == 0x41F9:
            target = int.from_bytes(code[pc + 2 : pc + 6], "big")
            tables.setdefault(target, {})
            pc += 6
        elif op == 0x217C:
            value, offset = struct.unpack_from(">IH", code, pc + 2)
            tables[target][offset // 4] = value
            pc += 8
        else:
            # The only counted fill is MOVEQ, a relocated push, then DBRA.
            assert op == 0x701A
            assert int.from_bytes(code[pc + 2 : pc + 4], "big") == 0x20FC
            value = int.from_bytes(code[pc + 4 : pc + 8], "big")
            assert code[pc + 8 : pc + 12] == bytes.fromhex("51c8fff8")
            tables[target].update(dict.fromkeys(range(27), value))
            pc += 12
    expected = []
    for _, values in sorted(tables.items()):
        expected.append(
            tuple(
                strings[values[i] : strings.index(b"\0", values[i])].decode("ascii")
                if i in values
                else None
                for i in range(27)
            )
        )
    assert decoded.buttons == tuple(expected)
    palette_at = int.from_bytes(code[0xAE58:0xAE5C], "big")
    assert decoded.palette == struct.unpack_from(">32H", strings, palette_at)
    assert decoded.class_pairs == tuple(
        (
            int.from_bytes(code[pos + 2 : pos + 4], "big"),
            int.from_bytes(code[pos + 6 : pos + 8], "big"),
        )
        for pos in (0xAE88, 0xAE9C, 0xAEC4, 0xAEB0, 0xAECE)
    )
    for index, load in enumerate((0xAF32, 0xAF4A)):
        position = int.from_bytes(code[load + 2 : load + 6], "big")
        expected_pieces = []
        while int.from_bytes(code[position : position + 2], "big", signed=True) >= 0:
            expected_pieces.append(struct.unpack_from(">HhhH", code, position))
            position += 8
        assert [
            (p.frame, p.x, p.y, p.orientation) for p in decoded.panels[index]
        ] == expected_pieces


def test_sheet_decoder_rejects_unsupported_code(adf_dir: Path) -> None:
    executable = parse_hunk_executable("mog", DiskSet.from_directory(adf_dir).read_file("mog"))
    first = executable.sections[0]
    changed = replace(first, data=first.data[:0xC6E0] + bytes(2) + first.data[0xC6E2:])
    with pytest.raises(MogVersionError, match="unsupported version"):
        parse_sheet_data(replace(executable, sections=(changed, *executable.sections[1:])))


def test_sheet_rule_operands_and_armour_table_match_independent_reads(adf_dir: Path) -> None:
    executable = parse_hunk_executable("mog", DiskSet.from_directory(adf_dir).read_file("mog"))
    rules = parse_sheet_rules(executable, parse_sheet_data(executable), ((8, 8),) * 50)
    code = executable.sections[0].data
    assert (rules.gold_cap, rules.haste_limit, rules.hawk_limit, rules.protection_limit) == tuple(
        int.from_bytes(code[pos : pos + 2], "big") for pos in (0xC266, 0xBECA, 0xBF4E, 0xBFB8)
    )
    table = int.from_bytes(code[0xC19C:0xC1A0], "big")
    assert rules.armour_hp == code[table : table + 8]
