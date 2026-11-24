"""Synthetic town record inheritance and checked-read failures (§12.27)."""

from __future__ import annotations

import struct
import zlib
from dataclasses import replace

import pytest

from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.hunk import HUNK_BSS, HUNK_CODE, DecodedHunkExecutable, HunkSection
from harddaysengine.assets.mog_code import MogCode, Pointer
from harddaysengine.assets.town_data import (
    _buttons,
    _cursor,
    _space_sheet,
    _string,
    parse_town_data,
)
from harddaysengine.sim.town_types import TownButton


def _mog(code: bytes, relocs: dict[int, tuple[int, ...]], data: bytes = bytes(64)) -> MogCode:
    sections = tuple(
        HunkSection(
            index=i,
            hunk_type=HUNK_BSS if i == 11 else HUNK_CODE,
            alloc_size=64,
            data=code if i == 0 else data if i == 4 else b"",
            relocs=relocs if i == 0 else {},
        )
        for i in range(12)
    )
    return MogCode(DecodedHunkExecutable("synthetic", sections), supported=(zlib.crc32(code),))


def _builder() -> tuple[bytearray, dict[int, tuple[int, ...]]]:
    code = bytearray()
    relocs: dict[int, list[int]] = {0: [], 11: []}

    def call(target: int) -> None:
        relocs[0].append(len(code) + 2)
        code.extend(struct.pack(">HI", 0x4EB9, target))

    def load() -> None:
        relocs[11].append(len(code) + 2)
        code.extend(struct.pack(">HI", 0x41F9, 4))

    def word(value: int, offset: int) -> None:
        code.extend(struct.pack(">HHH", 0x317C, value, offset))

    def long(value: int, offset: int) -> None:
        code.extend(struct.pack(">HIH", 0x217C, value, offset))

    call(0x94BA)
    load()
    for value, offset in ((12, 12), (10, 14), (9, 4), (6, 6)):
        word(value, offset)
    long(0, 8)
    long(1, 16)
    word(7, 20)
    word(42, 22)
    call(0x9480)
    for index in range(2, 6):
        load()
        word(index * 10, 14)
        if index == 4:
            word(13, 4)
            word(7, 6)
        elif index == 5:
            word(3, 6)
        long(index, 16)
        call(0x9480)
    code.extend(struct.pack(">H", 0x4E75))
    return code, {target: tuple(offsets) for target, offsets in relocs.items()}


def test_buttons_retain_fields_between_registration_calls() -> None:
    code, relocs = _builder()
    assert _buttons(_mog(bytes(code), relocs), 0) == tuple(
        TownButton(i, 12, i * 10, width, height, 7, 42)
        for i, width, height in ((1, 9, 6), (2, 9, 6), (3, 9, 6), (4, 13, 7), (5, 13, 3))
    )


@pytest.mark.parametrize(
    ("offset", "value", "message"),
    (
        (12, 0, "expected"),
        (16, 4, "displacement"),
        (0xC0, 0, "end|return"),
        (14, 320, "outside the playfield"),
        (26, 0, "nonpositive"),
        (48, 2, "registration order"),
        (40, 1, "not cleared"),
    ),
)
def test_builder_rejects_invalid_instructions_and_records(
    offset: int, value: int, message: str
) -> None:
    code, relocs = _builder()
    struct.pack_into(">H", code, offset, value)
    with pytest.raises(MogVersionError, match=message):
        _buttons(_mog(bytes(code), relocs), 0)


def test_builder_rejects_unrelated_scratch_reload_and_call() -> None:
    code, relocs = _builder()
    struct.pack_into(">I", code, relocs[11][1], 8)
    with pytest.raises(MogVersionError, match="another scratch record"):
        _buttons(_mog(bytes(code), relocs), 0)
    code, relocs = _builder()
    struct.pack_into(">I", code, relocs[0][1], 0)
    with pytest.raises(MogVersionError, match="call target"):
        _buttons(_mog(bytes(code), relocs), 0)


def test_builder_rejects_missing_relocation_and_small_allocation() -> None:
    code, relocs = _builder()
    with pytest.raises(MogVersionError, match="not relocated"):
        _buttons(_mog(bytes(code), {0: relocs[0]}), 0)
    mog = _mog(bytes(code), relocs)
    sections = (*mog.executable.sections[:11], replace(mog.executable.sections[11], alloc_size=16))
    with pytest.raises(MogVersionError, match="BSS allocation"):
        _buttons(
            MogCode(replace(mog.executable, sections=sections), supported=(zlib.crc32(code),)), 0
        )


def test_cursor_requires_adjacent_relocated_data_cells() -> None:
    code = bytearray(struct.pack(">HHIHHI", 0x33FC, 13, 8, 0x33FC, 17, 10))
    assert _cursor(_mog(bytes(code), {4: (4, 12)}), 0) == (13, 17, Pointer(4, 8))
    struct.pack_into(">I", code, 12, 12)
    with pytest.raises(MogVersionError, match="adjacent"):
        _cursor(_mog(bytes(code), {4: (4, 12)}), 0)


@pytest.mark.parametrize(
    ("pointer", "data", "message"),
    (
        (Pointer(0, 0), b"a\0", "data hunk"),
        (Pointer(4, 3), b"a\0", "allocation"),
        (Pointer(4, 0), b"\0", "empty"),
        (Pointer(4, 0), b"a", "unterminated"),
        (Pointer(4, 0), b"\xff\0", "ASCII"),
    ),
)
def test_strings_reject_wrong_hunks_bounds_and_invalid_text(
    pointer: Pointer, data: bytes, message: str
) -> None:
    with pytest.raises(MogVersionError, match=message):
        _string(_mog(b"", {}, data), pointer)


@pytest.mark.parametrize("highwood", [False, True])
def test_space_sheet_uses_highwood_status_sheet_for_both_towns(highwood: bool) -> None:
    start = 0x14AE if highwood else 0x1366
    code = bytearray(0x14AE + 32)
    struct.pack_into(">HHHH", code, start, 0xB07C, 32, 0x6600, 0)
    calls = []
    pos = start + 8
    for target in (0x8B26, 0xC5F4):
        calls.append(pos + 2)
        struct.pack_into(">HI", code, pos, 0x4EB9, target)
        pos += 6
    if highwood:
        pos += 6
    calls.append(pos + 2)
    struct.pack_into(">HI", code, pos, 0x4EB9, 0xABEC)
    struct.pack_into(">HI", code, 0x14AE + 20, 0x203C, 9)
    assert _space_sheet(_mog(bytes(code), {0: tuple(calls)}), highwood) == 9
    code[start + 3] = 31
    with pytest.raises(MogVersionError, match="not Space"):
        _space_sheet(_mog(bytes(code), {0: tuple(calls)}), highwood)


def test_public_decoder_rejects_unrecognized_code_version() -> None:
    code, relocs = _builder()
    with pytest.raises(MogVersionError, match="unsupported version"):
        parse_town_data(_mog(bytes(code), relocs).executable)
