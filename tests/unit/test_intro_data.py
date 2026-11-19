"""Synthetic checked reads of the ``program`` intro pass and ``mog`` menu layouts (§12.42)."""

from __future__ import annotations

import struct
import zlib

import pytest

from harddaysengine.assets.ending_data import _Scene
from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.hunk import HUNK_CODE, DecodedHunkExecutable, HunkSection
from harddaysengine.assets.intro_data import (
    _forward_wipe,
    _knight_select_layout,
    _mog_char_map,
    _mog_span,
    _palette_pokes,
    _picture_setup,
    _title_menu_layout,
)
from harddaysengine.assets.mog_code import MogCode
from harddaysengine.assets.program_code import ProgramCode
from harddaysengine.sim.ending_types import Buffer, Series, Spawn, SpawnKind
from harddaysengine.sim.intro_types import ForwardWipe, PictureCopy, PictureSetup, SpriteDraw

_SIZE = 0x1000


class _ProgImage:
    """Synthetic ``program`` hunk image builder."""

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

    def jsr(self, hunk: int, pos: int, target_hunk: int, target_offset: int) -> int:
        return self.ref(hunk, self.words(hunk, pos, 0x4EB9), target_hunk, target_offset)

    def program(self) -> ProgramCode:
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
        executable = DecodedHunkExecutable("synthetic_program", sections)
        return ProgramCode(executable, supported=(zlib.crc32(bytes(self.data[0])),))


class _MogImage:
    """Synthetic ``mog`` hunk image builder."""

    def __init__(self) -> None:
        self.data = [bytearray(0xA000) for _ in range(37)]
        self.relocs: list[dict[int, list[int]]] = [{} for _ in range(37)]

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

    def cstr(self, hunk: int, pos: int, text: str) -> None:
        raw = text.encode("ascii") + b"\x00"
        self.data[hunk][pos : pos + len(raw)] = raw

    def mog(self) -> MogCode:
        sections = tuple(
            HunkSection(
                index=i,
                hunk_type=HUNK_CODE,
                alloc_size=len(self.data[i]),
                data=bytes(self.data[i]),
                relocs={t: tuple(p) for t, p in self.relocs[i].items()},
            )
            for i in range(len(self.data))
        )
        executable = DecodedHunkExecutable("synthetic_mog", sections)
        return MogCode(executable, supported=(zlib.crc32(bytes(self.data[0])),))


def test_scene_walks_series_bsr_spawns_and_subscene_0818() -> None:
    image = _ProgImage()
    # Subscene 0x818: one left spawn (MOVEA.L #h2:0x100,A0 ; BSR.W 0x2E4 ; RTS)
    sub = 0x818
    sub_pos = image.ref(0, image.words(0, sub, 0x207C), 2, 0x100)
    image.words(0, sub_pos, 0x6100, (0x2E4 - (sub_pos + 2)) & 0xFFFF, 0x4E75)

    # Main scene at 0x100: BSR 0x818, LEA h2:0x200 + BSR 0x2E4, SERIES setup + BSR 0x4C8
    pos = 0x100
    pos = image.words(0, pos, 0x6100, (0x818 - (pos + 2)) & 0xFFFF)
    pos = image.ref(0, image.words(0, pos, 0x41F9), 2, 0x200)
    pos = image.words(0, pos, 0x6100, (0x2E4 - (pos + 2)) & 0xFFFF)
    pos = image.ref(0, image.words(0, pos, 0x33FC, 4), 0, 0x60E)
    pos = image.ref(0, image.words(0, pos, 0x33FC, 0), 0, 0x602)
    pos = image.ref(0, image.words(0, pos, 0x23FC), 0, 0x570)
    pos = image.ref(0, pos, 0, 0x60A)
    pos = image.ref(0, image.words(0, pos, 0x33FC, 2), 0, 0x608)
    pos = image.words(0, pos, 0x6100, (0x4C8 - (pos + 2)) & 0xFFFF, 0x4E75)
    image.ref(0, 0x570, 2, 0x300)
    image.ref(0, 0x574, 2, 0x400)

    scene = _Scene(image.program())
    scene.run(0x100)
    assert scene.steps == [
        Spawn(SpawnKind.LEFT, 0x100),
        Spawn(SpawnKind.LEFT, 0x200),
        Series((0x300, 0x400), 4),
    ]


def test_picture_setup_reads_copies_and_rejects_bad_calls() -> None:
    image = _ProgImage()
    pos = 0x8E
    pos = image.ref(8, image.words(8, pos, 0x2079), 2, 0x226)  # P5
    pos = image.ref(8, image.words(8, pos, 0x2279), 2, 0x21A)  # P2
    pos = image.jsr(8, pos, 10, 0xD6A)
    pos = image.ref(8, image.words(8, pos, 0x41F9), 9, 0xC6)  # table 3
    pos = image.ref(8, image.words(8, pos, 0x43F9), 9, 0x06)  # table 0
    pos = image.jsr(8, pos, 8, 0x92E)
    image.words(8, pos, 0x4E75)

    setup = _picture_setup(image.program())
    assert setup == PictureSetup((PictureCopy(Buffer.P5, Buffer.P2, 3, 0),))

    # Empty setup (immediate RTS) must raise
    image.words(8, 0x8E, 0x4E75)
    with pytest.raises(MogVersionError, match="no copies"):
        _picture_setup(image.program())


def test_forward_wipe_reads_entries_thresholds_steps_and_triggers() -> None:
    image = _ProgImage()
    image.jsr(31, 0x5AA, 10, 0xB46)
    pos = image.ref(31, image.words(31, 0x5B0, 0x41F9), 9, 0x06)
    for idx, word in ((9, 0x0A00), (10, 0x0600), (11, 0x0300), (12, 0x0FC6)):
        pos = image.words(31, pos, 0x317C, word, idx * 2)
    pos = image.ref(31, image.words(31, pos, 0x41F9), 9, 0x06)
    image.jsr(31, pos, 29, 0x788)
    image.ref(31, image.words(31, 0x5DA, 0x41F9), 31, 0x65E)
    image.ref(31, image.words(31, 0x5E0, 0x43F9), 31, 0x664)
    image.words(31, 0x65E, 10, 50, 1000)
    image.words(31, 0x664, 1, 4, 2)
    pos = image.words(31, 0x618, 0x0C79, 4)
    image.ref(31, pos, 31, 0x850)
    image.jsr(31, 0x622, 1, 0x000)
    pos = image.words(31, 0x62E, 0x0C79, 1000)
    image.ref(31, pos, 31, 0x84E)

    wipe = _forward_wipe(image.program())
    assert wipe == ForwardWipe(
        entries=((9, 0x0A00), (10, 0x0600), (11, 0x0300), (12, 0x0FC6)),
        thresholds=(10, 50, 1000),
        steps=(1, 4, 2),
        music_step=4,
        limit=1000,
    )

    # Invalid RGB12 word (> 0xFFF) in palette poke must raise
    image.words(31, 0x5B8, 0x1234)
    with pytest.raises(MogVersionError, match="out of range"):
        _palette_pokes(image.program(), 31, 0x5B0, 4, "wipe")


def test_mog_title_and_knight_select_decoders_check_all_operands() -> None:
    image = _MogImage()
    # Bold style operand at 0x9350
    image.words(0, 0x9350, 0x002A, 8, 9)
    # Span and margin at 0x91F0 / 0x91F6
    image.ref(0, image.words(0, 0x91F0, 0x3239), 4, 0x700)
    image.ref(0, image.words(0, 0x91F6, 0x9279), 4, 0x702)
    image.words(4, 0x700, 320, 1)
    # Char map at 0x9252
    image.ref(0, image.words(0, 0x9252, 0x43F9), 4, 0x800)
    for i in range(104):
        image.data[4][0x800 + i] = i

    # Title menu at 0x18FA..0x1B2E
    image.ref(0, image.words(0, 0x18FA, 0x33FC, 50), 1, 0x172)
    image.ref(0, image.words(0, 0x1A9C, 0x41F9), 4, 0x27C)
    image.words(4, 0x27C, 85, 110, 148, 168)
    image.words(0, 0x1AC4, 0x303C, 0x49, 0x323C, 5, 0x343C, 10)
    image.words(0, 0x1ADC, 0x303C, 0)
    image.ref(0, image.words(0, 0x1B00, 0x45F9), 4, 0xB8)
    pos = image.ref(0, image.words(0, 0x1B0C, 0x23FC), 4, 0xC7)
    image.ref(0, pos, 4, 0x16)
    pos = image.ref(0, image.words(0, 0x1B1E, 0x23FC), 4, 0xC4)
    image.ref(0, pos, 4, 0x16)
    image.ref(0, image.words(0, 0x1B28, 0x41F9), 4, 0x08)

    # Two text records at h4:0x08 (players buffer) and h4:0x16 (gore value)
    image.cstr(4, 0xB8, "1   ")
    image.cstr(4, 0xC4, "On")
    image.cstr(4, 0xC7, "Off")
    image.ref(4, 0x08, 4, 0xB8)
    image.words(4, 0x0C, 214, 83, 2)
    image.ref(4, 0x12, 4, 0x16)
    image.ref(4, 0x16, 4, 0xC4)
    image.words(4, 0x1A, 214, 108, 2)
    image.long(4, 0x20, 0)

    # Knight select at 0x1D2C..0x1FBC
    image.ref(0, image.words(0, 0x1D2C, 0x41F9), 4, 0x476)
    for i in range(32):
        image.words(4, 0x476 + 2 * i, i)
    image.ref(0, image.words(0, 0x1E4E, 0x41F9), 4, 0x304)
    image.cstr(4, 0x320, "Select a Knight")
    image.ref(4, 0x304, 4, 0x320)
    image.words(4, 0x308, 0, 5, 1)
    image.long(4, 0x30E, 0)
    image.ref(0, image.words(0, 0x1E68, 0x41F9), 4, 0x4BE)
    image.words(4, 0x4BE, 12, 88, 164, 240)
    image.words(0, 0x1E7A, 0x5440, 0x343C, 80)
    image.words(0, 0x1EAA, 0x303C, 1, 0x343C, 80)
    image.words(0, 0x1EC6, 0x303C, 50, 0x323C, 50, 0xB582)

    name_offs = (0x76, 0x60, 0x8C, 0xA2)
    raw_names = ("SIR_GODBER  ", "SIR_RICHARD ", "SIR_JEFFREY ", "SIR_EDWARD  ")
    for idx, (pos, noff, rname) in enumerate(
        zip((0x1EFE, 0x1F2E, 0x1F5E, 0x1F8E), name_offs, raw_names, strict=True)
    ):
        image.cstr(4, noff, rname)
        image.words(0, pos, 0xB07C, idx, 0x662A, 0x23FC)
        image.ref(0, pos + 8, 4, noff)
        image.ref(0, pos + 12, 4, 0x5C)
        image.words(0, pos + 32, 0x237C)
        image.long(0, pos + 34, idx)
        image.words(0, pos + 38, 0x36)

    mog = image.mog()
    span = _mog_span(mog)
    assert span == 319
    assert len(_mog_char_map(mog)) == 104
    title = _title_menu_layout(mog, span)
    assert title.banner == SpriteDraw(0x49, 5, 10)
    assert title.cursor_x == 50 and title.cursor_y == (85, 110, 148, 168)
    assert title.players_line_index == 0 and title.gore_line_index == 1
    assert title.gore_on_text == "On" and title.gore_off_text == "Off"

    ks = _knight_select_layout(mog, span)
    assert ks.knight_names == ("SIR GODBER", "SIR RICHARD", "SIR JEFFREY", "SIR EDWARD")
    assert ks.portrait_x == (12, 88, 164, 240) and ks.portrait_y == 80
    assert ks.portrait_first_frame == 2 and ks.highlight_frame == 1

    # Corrupting a portrait actor kind must raise
    image.long(0, 0x1EFE + 34, 9)
    with pytest.raises(MogVersionError, match="actor kind mismatch"):
        _knight_select_layout(image.mog(), span)
