"""Invented executable text and instruction fixtures; no disk-derived strings (§12.35)."""

import struct
import zlib
from collections.abc import Callable
from dataclasses import replace

from harddaysengine.assets.hunk import HUNK_CODE, DecodedHunkExecutable, HunkSection
from harddaysengine.assets.mog_code import MogCode


def synthetic_mog() -> MogCode:
    code, data = bytearray(0xA300), bytearray(4096)
    relocs: dict[int, list[int]] = {0: [], 1: [], 4: []}
    data_relocs: list[int] = []

    def pointer(pos: int, target: int, value: int) -> None:
        struct.pack_into(">I", code, pos, value)
        relocs[target].append(pos)

    def load(pos: int, opcode: int, value: int) -> None:
        struct.pack_into(">H", code, pos, opcode)
        pointer(pos + 2, 4, value)

    def speech(pos: int, label: str) -> None:
        encoded = b"\2" + label.encode("ascii") + b"\0Field:          \0"
        data[pos : pos + len(encoded)] = encoded

    for inc, test, read, cell, opcode in (
        (0x98F6, 0x98FE, 0x9910, 16, 0x0C79),
        (0x9930, 0x9938, 0x9940, 24, 0x0279),
    ):
        struct.pack_into(">HH", code, inc, 0x0679, 1)
        pointer(inc + 4, 4, cell)
        struct.pack_into(">HH", code, test, opcode, 3)
        pointer(test + 4, 4, cell)
        struct.pack_into(">H", code, read, 0x3039)
        pointer(read + 2, 4, cell)
    struct.pack_into(">HHH", code, 0x9906, 0x6D08, 0x33FC, 0)
    pointer(0x990C, 4, 16)
    struct.pack_into(">H", code, 0x98E2, 0x23FC)
    pointer(0x98E4, 4, 1800)
    pointer(0x98E8, 4, 32)
    for pos in (0x991E, 0x994E, 0x9968):
        struct.pack_into(">H", code, pos, 0x23F0)
        pointer(pos + 4, 4, 32)
    for pos, table, count, stride in (
        (0x9918, 64, 3, 4),
        (0x9948, 96, 4, 4),
        (0x99E4, 128, 9, 8),
    ):
        load(pos, 0x41F9, table)
        for i in range(count):
            target = {64: 256, 96: 512, 128: 800}[table] + i * 60
            entry = table + i * stride
            if stride == 8:
                struct.pack_into(">I", data, entry, 70 + i % 3)
                entry += 4
            struct.pack_into(">I", data, entry, target)
            data_relocs.append(entry)
            speech(target, f"Table {table} row {i}")
    struct.pack_into(">HI", code, 0x99F6, 0xB0BC, 8)
    _farewells(code, pointer, load, speech)
    load(0x9602, 0x41F9, 1600)
    load(0x97D0, 0x207C, 1700)
    for pos in (1600, 1700, 1800):
        speech(pos, f"Speech {pos}")
    struct.pack_into(">HH", code, 0xA2A2, 0x0679, 12)
    struct.pack_into(">3H", code, 0x9876, 0x117C, 55, 83)
    return _mog(code, data, relocs, data_relocs)


def _farewells(
    code: bytearray,
    pointer: Callable[[int, int, int], None],
    load: Callable[[int, int, int], None],
    speech: Callable[[int, str], None],
) -> None:
    for pos, key in zip((0x97D6, 0x97E6, 0x97F6, 0x9806), (2, 3, 1, 4), strict=True):
        target = 2000 + key * 100
        struct.pack_into(">HH", code, pos, 0x0C79, key)
        pointer(pos + 4, 4, 40)
        struct.pack_into(">H", code, pos + 8, 0x6606)
        load(pos + 10, 0x207C, target)
        label = f"Farewell {key}"
        speech(target, label)
        if key in (1, 2):
            field = target + 1 + len(label) + 1 + len("Field: ")
            if key == 1:
                load(0x9B52, 0x4BF9, field)
            else:
                load(0x9A4C, 0x45F9, field)
                load(0x9A58, 0x45F9, field)


def _mog(
    code: bytearray, data: bytearray, relocs: dict[int, list[int]], data_relocs: list[int]
) -> MogCode:
    sections = tuple(
        HunkSection(
            i,
            HUNK_CODE,
            len(part),
            bytes(part),
            {key: tuple(positions) for key, positions in relocs.items()}
            if i == 0
            else {4: tuple(data_relocs)}
            if i == 4
            else {},
        )
        for i, part in enumerate((code, bytes(64), b"", b"", data))
    )
    return MogCode(
        DecodedHunkExecutable("synthetic wizard", sections), supported=(zlib.crc32(code),)
    )


def changed(mog: MogCode, hunk: int, offset: int, content: bytes) -> MogCode:
    sections = list(mog.executable.sections)
    data = bytearray(sections[hunk].data)
    data[offset : offset + len(content)] = content
    sections[hunk] = replace(sections[hunk], data=bytes(data))
    return MogCode(
        replace(mog.executable, sections=tuple(sections)),
        supported=(zlib.crc32(sections[0].data),),
    )
