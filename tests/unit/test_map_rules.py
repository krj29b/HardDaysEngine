"""Malformed map tables fail explicitly on synthetic executable data (§11.18)."""

import struct
import zlib

import pytest

from pymoonstone.assets.fight_palette import MogVersionError
from pymoonstone.assets.hunk import HUNK_CODE, DecodedHunkExecutable, HunkSection
from pymoonstone.assets.map_rules import _bytes, _nodes, _palette_jobs, _pointer
from pymoonstone.assets.mog_code import MogCode, Pointer


def _mog(*, opcode: int = 0x41F9, target: int = 2, terminated: bool = True) -> MogCode:
    code = bytearray(0xE84)
    struct.pack_into(">HI", code, 0xE7E, opcode, 0)
    node_data = struct.pack(">hHHhHH", 25, 20, 30, -1 if terminated else 26, 40, 50)
    palette = bytearray(0x614)
    struct.pack_into(">HH", palette, 0x5F4, 0x7402, 0x7600)
    struct.pack_into(">HHI", palette, 0x60C, 0x7401, 0x263C, 7)
    sections = tuple(
        HunkSection(
            index=i,
            hunk_type=HUNK_CODE,
            alloc_size=len(data),
            data=data,
            relocs={target: (0xE80,)} if i == 0 else {},
        )
        for i, data in enumerate(
            (bytes(code), bytes(4), node_data, *(bytes(4) for _ in range(33)), bytes(palette))
        )
    )
    return MogCode(
        DecodedHunkExecutable(name="synthetic", sections=sections), supported=(zlib.crc32(code),)
    )


def test_node_terminator_and_pointer_checks() -> None:
    mog = _mog()
    assert [(n.kind, n.x, n.y) for n in _nodes(mog)] == [(25, 20, 30)]
    with pytest.raises(MogVersionError, match="negative terminator"):
        _nodes(_mog(terminated=False))
    with pytest.raises(MogVersionError, match="unexpected map instruction"):
        _nodes(_mog(opcode=0x4E71))
    with pytest.raises(MogVersionError, match="another hunk"):
        _nodes(_mog(target=3))
    with pytest.raises(MogVersionError, match="not relocated"):
        _pointer(mog, 0, 0, 0, 2)


@pytest.mark.parametrize(("offset", "count"), [(-1, 1), (12, 1), (0, 13)])
def test_table_reads_are_bounded(offset: int, count: int) -> None:
    with pytest.raises(MogVersionError, match="outside its data hunk"):
        _bytes(_mog(), Pointer(2, offset), count)


def test_job_periods_are_read_from_checked_forms() -> None:
    assert _palette_jobs(_mog()) == (2, 7)
