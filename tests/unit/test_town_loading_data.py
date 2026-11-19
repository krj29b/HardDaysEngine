"""Synthetic town-name substitution and checked loading paths (§12.33)."""

import struct
import zlib

import pytest

from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.hunk import HUNK_CODE, DecodedHunkExecutable, HunkSection
from harddaysengine.assets.mog_code import MogCode
from harddaysengine.assets.town_loading_data import _read_welcome, parse_town_welcome


def _image() -> tuple[bytearray, bytearray, dict[int, tuple[int, ...]]]:
    code, data = bytearray(0x9360), bytearray(512)
    struct.pack_into(">HHH", code, 0x9350, 0x002A, 8, 9)
    relocs: dict[int, list[int]] = {0: [], 4: []}
    for start, name in ((0x2D96, 100), (0x2DD2, 120)):
        struct.pack_into(">HIIHIHI", code, start, 0x23FC, name, 14, 0x41F9, 0, 0x4EB9, 0x2FB4)
        relocs[4].extend((start + 2, start + 6, start + 12))
        relocs[0].append(start + 18)
    for pos, target in ((0x2FD6, 0x9196), (0x2FE2, 0x8B7C), (0x2E22, 0x8B26)):
        struct.pack_into(">HI", code, pos, 0x4EB9, target)
        relocs[0].append(pos + 2)
    struct.pack_into(">HH", code, 0x2FBA, 0x6100, 0x88)
    struct.pack_into(">HH", code, 0x2C32, 0x2149, 16)
    struct.pack_into(">HHH", code, 0x2FCA, 0x2169, 16, 10)
    for pos, text in ((0x2BFA, 140), (0x2C36, 160)):
        struct.pack_into(">HI", code, pos, 0x41F9, text)
        relocs[4].append(pos + 2)
    for offset, text in (
        (80, b"Welcome fixture"),
        (100, b"First"),
        (120, b"Second"),
        (140, b"message.fixture"),
        (160, b"font.fixture"),
    ):
        data[offset : offset + len(text) + 1] = text + b"\0"
    struct.pack_into(">IHHHI", data, 0, 80, 5, 61, 1, 14)
    struct.pack_into(">IHHHI", data, 14, 100, 7, 79, 4, 0)
    return code, data, {key: tuple(values) for key, values in relocs.items()}


def _mog(code: bytearray, data: bytearray, relocs: dict[int, tuple[int, ...]]) -> MogCode:
    sections = tuple(
        HunkSection(
            index=i,
            hunk_type=HUNK_CODE,
            alloc_size=len(code) if i == 0 else len(data),
            data=bytes(code) if i == 0 else bytes(data),
            relocs=relocs if i == 0 else {4: (0, 10, 14)} if i == 4 else {},
        )
        for i in range(5)
    )
    return MogCode(DecodedHunkExecutable("synthetic", sections), supported=(zlib.crc32(code),))


def test_each_loader_substitutes_only_its_name_without_changing_the_executable() -> None:
    mog = _mog(*_image())
    original = mog.hunk_data(4)
    first, second = (_read_welcome(mog, waterdeep=flag) for flag in (False, True))
    assert [row.text for row in first.lines] == ["Welcome fixture", "First"]
    assert [row.text for row in second.lines] == ["Welcome fixture", "Second"]
    assert [(row.x, row.y, row.style) for row in second.lines] == [(5, 61, 9), (7, 79, 12)]
    assert (first.picture, first.font) == ("message.fixture", "font.fixture")
    assert mog.hunk_data(4) == original
    with pytest.raises(MogVersionError, match="unsupported version"):
        parse_town_welcome(mog.executable, waterdeep=False)


@pytest.mark.parametrize(
    "offset,value,message",
    (
        (0x2D96, 0, "name store"),
        (0x2DAA, 0, "call target"),
        (0x2FBC, 0, "unpack target"),
        (0x2C34, 0, "slot offset"),
        (0x2FCC, 0, "font slot"),
        (0x2FCE, 0, "active font"),
        (0x2DE0, 28, "disagree"),
        (0x9350, 0, "bold-font style"),
        (0x9352, 0, "bold-font style"),
    ),
)
def test_changed_instructions_and_destinations_fail(offset: int, value: int, message: str) -> None:
    code, data, relocs = _image()
    struct.pack_into(">H", code, offset, value)
    with pytest.raises(MogVersionError, match=message):
        _read_welcome(_mog(code, data, relocs), waterdeep=True)


def test_substitution_must_be_in_the_linked_list() -> None:
    code, data, relocs = _image()
    for pos in (0x2D9C, 0x2DD8):
        struct.pack_into(">I", code, pos, 42)
    with pytest.raises(MogVersionError, match="substitution is outside"):
        _read_welcome(_mog(code, data, relocs), waterdeep=False)


def test_missing_and_wrong_hunk_relocations_fail() -> None:
    code, data, relocs = _image()
    relocs[4] = tuple(pos for pos in relocs[4] if pos != 0x2D98)
    with pytest.raises(MogVersionError, match="not relocated"):
        _read_welcome(_mog(code, data, relocs), waterdeep=False)
    relocs[0] += (0x2D98,)
    with pytest.raises(MogVersionError, match="outside the data hunk"):
        _read_welcome(_mog(code, data, relocs), waterdeep=False)
