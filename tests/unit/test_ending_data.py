"""Synthetic checked reads of the program ending pass (ARCHITECTURE.md §12.39)."""

from __future__ import annotations

import struct
import zlib

import pytest

from harddaysengine.assets.ending_data import (
    _buffer,
    _class_tweaks,
    _palette,
    _Program,
    _Scene,
    _tweak_blocks,
)
from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.hunk import HUNK_CODE, DecodedHunkExecutable, HunkSection
from harddaysengine.assets.mog_code import Pointer
from harddaysengine.sim.ending_types import (
    Buffer,
    Call,
    Cell,
    ClassTweak,
    Invoke,
    Pairs,
    Spawn,
    SpawnKind,
    StageTable,
    Store,
    Ticks,
)

_SIZE = 0x1000


class _Image:
    """Hunk images with relocated longs for the synthetic program."""

    def __init__(self) -> None:
        self.data = [bytearray(_SIZE) for _ in range(34)]
        self.relocs: list[dict[int, list[int]]] = [{} for _ in range(34)]

    def words(self, hunk: int, pos: int, *values: int) -> int:
        for value in values:
            struct.pack_into(">H", self.data[hunk], pos, value)
            pos += 2
        return pos

    def long(self, hunk: int, pos: int, value: int) -> int:
        struct.pack_into(">I", self.data[hunk], pos, value)
        return pos + 4

    def ref(self, hunk: int, pos: int, target: int, offset: int) -> int:
        self.relocs[hunk].setdefault(target, []).append(pos)
        return self.long(hunk, pos, offset)

    def program(self) -> _Program:
        sections = tuple(
            HunkSection(
                index=i,
                hunk_type=HUNK_CODE,
                alloc_size=_SIZE,
                data=bytes(self.data[i]),
                relocs={t: tuple(p) for t, p in self.relocs[i].items()},
            )
            for i in range(34)
        )
        executable = DecodedHunkExecutable("synthetic", sections)
        return _Program(executable, supported=(zlib.crc32(bytes(self.data[0])),))


def _jsr(image: _Image, pos: int, hunk: int, offset: int) -> int:
    return image.ref(0, image.words(0, pos, 0x4EB9), hunk, offset)


def _scene(image: _Image) -> int:
    pos = image.words(0, 0x100, 0x33FC, 1)
    pos = image.ref(0, pos, 2, 0x246)
    pos = image.long(0, image.words(0, pos, 0x23FC), 6)
    pos = image.ref(0, pos, 2, 0x23E)
    pos = image.ref(0, image.words(0, pos, 0x23FC), 9, 0x1C6)
    pos = image.ref(0, pos, 3, 0x84)
    pos = image.ref(0, image.words(0, pos, 0x207C), 2, 0x221C)
    pos = _jsr(image, pos, 0, 0x30A)
    loop = image.words(0, pos, 0x7028, 0x2F00)
    body = loop
    for hunk, offset in ((0, 0x330), (10, 0x2BE), (10, 0x32A), (10, 0xC92), (10, 0xA18)):
        body = _jsr(image, body, hunk, offset)
    body = _jsr(image, body, 0, 0x33C)
    body = image.words(0, body, 0x201F, 0x51C8, (loop - 2 - (body + 4)) & 0xFFFF)
    pos = _jsr(image, body, 0, 0x1BA)
    image.words(0, pos, 0x4E75)
    return pos


def test_scene_walk_names_stores_spawns_ticks_and_calls() -> None:
    image = _Image()
    _scene(image)
    scene = _Scene(image.program())
    scene.run(0x100)
    assert scene.steps == [
        Store(Cell.CREDITS, 1),
        Store(Cell.SPEED, 6),
        StageTable(7),
        Spawn(SpawnKind.RIGHT, 0x221C),
        Ticks(41),
        Invoke(Call.FRAMES),
    ]


def test_scene_walk_refuses_unknown_instructions_and_routines() -> None:
    image = _Image()
    end = _scene(image)
    image.words(0, end, 0x4E71)
    with pytest.raises(MogVersionError, match="ending scene"):
        _Scene(image.program()).run(0x100)
    image = _Image()
    end = _scene(image)
    image.relocs[0][0] = [p for p in image.relocs[0][0] if p != end - 4]
    image.ref(0, end - 4, 0, 0x777)
    with pytest.raises(MogVersionError, match="unknown routine"):
        _Scene(image.program()).run(0x100)


def test_pairs_need_their_complete_set_up() -> None:
    image = _Image()
    pos = image.words(0, 0x100, 0x33FC, 0)
    pos = image.ref(0, pos, 0, 0x602)
    pos = image.ref(0, image.words(0, pos, 0x23FC), 0, 0xD06)
    pos = image.ref(0, pos, 0, 0x60A)
    pos = image.ref(0, image.words(0, pos, 0x33FC, 2), 0, 0x608)
    pos = image.ref(0, image.words(0, pos, 0x33FC, 0), 0, 0x60E)
    bsr = pos
    pos = image.words(0, pos, 0x6100, (0x508 - (bsr + 2)) & 0xFFFF, 0x4E75)
    image.ref(0, 0xD06, 2, 0x22BE)
    image.ref(0, 0xD0A, 2, 0x1234)
    scene = _Scene(image.program())
    scene.run(0x100)
    assert scene.steps == [Pairs((0x22BE, 0x1234))]
    image.words(0, 0x102, 1)
    with pytest.raises(MogVersionError, match="complete set-up"):
        _Scene(image.program()).run(0x100)


def test_buffers_and_palettes_map_only_known_cells() -> None:
    assert _buffer(Pointer(2, 0x212), "x") == Buffer.SHOWN
    assert _buffer(Pointer(2, 0x236), "x") == Buffer.P9
    assert _buffer(Pointer(30, 4), "x") == Buffer.WORK
    assert _palette(Pointer(9, 0x1C6), "x") == 7
    for bad in (Pointer(2, 0x214), Pointer(2, 0x23A), Pointer(3, 0x212)):
        with pytest.raises(MogVersionError, match="picture buffer"):
            _buffer(bad, "x")
    with pytest.raises(MogVersionError, match="palette table"):
        _palette(Pointer(9, 0x206), "x")


def _class_block(image: _Image, pos: int, bit: int, colour: int, *, last: bool) -> int:
    pos = image.words(8, pos, 0x0800, bit, 0x6700 | (16 if last else 20))
    for opcode in (0x30FC, 0x30FC, 0x30FC, 0x30BC):
        pos = image.words(8, pos, opcode, colour)
        colour += 1
    if not last:
        pos = image.words(8, pos, 0x6000, 0)
    return pos


def test_class_tweaks_read_bits_colours_and_first_entry() -> None:
    image = _Image()
    image.words(8, 0x9DE, 0xD1FC)
    image.long(8, 0x9E0, 16)
    image.ref(8, image.words(8, 0x9E4, 0x3039), 0, 0x190)
    pos = _class_block(image, 0x9EA, 4, 0x100, last=False)
    pos = _class_block(image, pos, 6, 0x200, last=True)
    image.words(8, pos, 0x4E75)
    rows, first = _class_tweaks(image.program())
    assert first == 8
    assert rows == (
        ClassTweak(4, (0x100, 0x101, 0x102, 0x103)),
        ClassTweak(6, (0x200, 0x201, 0x202, 0x203)),
    )


def test_tweak_blocks_reject_a_wrong_skip_and_a_missing_end() -> None:
    image = _Image()
    pos = _class_block(image, 0x9EA, 4, 0x100, last=True)
    image.words(8, 0x9EE, 0x6712)
    image.words(8, pos, 0x4E75)
    with pytest.raises(MogVersionError, match="malformed"):
        _tweak_blocks(image.program(), 0x9EA, 16, "class tweak")
    image.words(8, 0x9EE, 0x6710, 0x30FC)
    image.words(8, pos, 0x4E71)
    with pytest.raises(MogVersionError, match="class tweak end"):
        _tweak_blocks(image.program(), 0x9EA, 16, "class tweak")
