"""Synthetic checked Stonehenge comparisons, fades, spawns and palette rows (§12.34)."""

import struct
import zlib

import pytest

from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.hunk import HUNK_CODE, DecodedHunkExecutable, HunkSection
from harddaysengine.assets.mog_code import MogCode
from harddaysengine.assets.stonehenge_data import _ending_rows, _fade, _match_rows, _ramps, _spawn


def _mog(code: bytearray, relocs: dict[int, tuple[int, ...]] | None = None) -> MogCode:
    sections = tuple(
        HunkSection(
            index=i,
            hunk_type=HUNK_CODE,
            alloc_size=len(code) if i == 0 else 2048,
            data=bytes(code) if i == 0 else bytes(2048),
            relocs=relocs or {} if i == 0 else {},
        )
        for i in range(43)
    )
    return MogCode(DecodedHunkExecutable("synthetic", sections), supported=(zlib.crc32(code),))


def test_match_rows_are_runtime_operands_with_checked_branches() -> None:
    code = bytearray(0x1900)
    for bit, pos in enumerate((0x177E, 0x178C, 0x179A, 0x17A8)):
        struct.pack_into(
            ">7H", code, pos, 0x0801, bit, 0x6708, 0xB07C, 61 + bit, 0x6700, 0x1820 - pos - 12
        )
    assert _match_rows(_mog(code)) == tuple((i, 61 + i) for i in range(4))
    struct.pack_into(">H", code, 0x178A, 0)
    with pytest.raises(MogVersionError, match="match target"):
        _match_rows(_mog(code))
    struct.pack_into(">H", code, 0x178A, 0x1820 - 0x178A)
    struct.pack_into(">H", code, 0x1780, 8)
    with pytest.raises(MogVersionError, match="outside inventory"):
        _match_rows(_mog(code))


def test_ending_bits_preserve_decoded_frame_and_class_order() -> None:
    code = bytearray(0x1900)
    for i, pos in enumerate((0x1822, 0x182C, 0x1836)):
        struct.pack_into(">5H", code, pos, 0xB07C, 65 + i, 0x6604, 0x08C7, i)
    for i, pos in enumerate((0x1844, 0x1850, 0x185C, 0x1868)):
        struct.pack_into(">HI3H", code, pos, 0xB0BC, i + 10, 0x6604, 0x08C7, i + 4)
    assert _ending_rows(_mog(code), classes=False) == ((65, 0), (66, 1), (67, 2))
    assert _ending_rows(_mog(code), classes=True) == tuple((i + 10, i + 4) for i in range(4))
    struct.pack_into(">H", code, 0x1872, 8)
    with pytest.raises(MogVersionError, match="outside boot"):
        _ending_rows(_mog(code), classes=True)


def test_blocking_fades_read_waits_and_reject_disagreement() -> None:
    code = bytearray(0x8C00)
    for pos, wait in ((0x8B2E, 0x8B36), (0x8B4E, 0x8B5E), (0x8B7C, 0x8B86)):
        struct.pack_into(">H", code, pos, 0x7002)
        struct.pack_into(">HI", code, wait, 0x203C, 21)
    assert _fade(_mog(code)) == (2, 21)
    struct.pack_into(">I", code, 0x8B88, 22)
    with pytest.raises(MogVersionError, match="disagree"):
        _fade(_mog(code))


def test_ramp_uses_four_runtime_words_per_class_and_bounds_rgb12() -> None:
    code = bytearray(0xAC00)
    for kind, pos in enumerate((0xAB74, 0xAB92, 0xABB0, 0xABCE)):
        struct.pack_into(">HIHH", code, pos, 0x0CA9, kind + 8, 54, 0x6614)
        for i in range(3):
            struct.pack_into(">HH", code, pos + 10 + i * 4, 0x30FC, kind * 5 + i)
        struct.pack_into(">HH", code, pos + 22, 0x30BC, kind * 5 + 3)
    struct.pack_into(">H", code, 0xABE8, 0x4E75)
    assert _ramps(_mog(code)) == tuple(
        (i + 8, tuple(i * 5 + n for n in range(4))) for i in range(4)
    )
    struct.pack_into(">H", code, 0xAB80, 4096)
    with pytest.raises(MogVersionError, match="outside RGB12"):
        _ramps(_mog(code))


def test_spawn_reads_script_position_facing_and_type_and_checks_cel_table() -> None:
    code = bytearray(64)
    struct.pack_into(">HIHI", code, 0, 0x41F9, 2, 0x45F9, 0x3DE)
    for pos, op, value in ((12, 0x303C, 71), (16, 0x323C, 0), (20, 0x343C, 83), (24, 0x163C, 0)):
        struct.pack_into(">HH", code, pos, op, value)
    struct.pack_into(">HHI", code, 28, 0x7A28, 0x4EB9, 0x7084)
    relocs = {42: (2, 8), 0: (32,)}
    spawn = _spawn(_mog(code, relocs), 0)
    assert (spawn.script, spawn.position, spawn.facing, spawn.kind) == (2, (71, 0, 83), 0, 40)
    struct.pack_into(">I", code, 8, 100)
    with pytest.raises(MogVersionError, match="another cel table"):
        _spawn(_mog(code, relocs), 0)
