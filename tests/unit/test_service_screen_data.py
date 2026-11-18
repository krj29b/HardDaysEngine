"""Synthetic healer and mystic routines with invented values prove the decoder (§12.31)."""

from __future__ import annotations

import zlib
from dataclasses import dataclass

import pytest

from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.hunk import HUNK_CODE, HUNK_DATA, DecodedHunkExecutable, HunkSection
from harddaysengine.assets.mog_code import MogCode
from harddaysengine.assets.service_screen_data import service_screens
from harddaysengine.sim.service_screen_types import CelDraw, LabelDraw, NumberDraw, ScreenRegion
from harddaysengine.sim.turn_rules import TextLine
from tests.unit.test_sound_rules_decoder import _Hunk

JSR, BSR, RTS = 0x4EB9, 0x6100, 0x4E75
DATA = 4


@dataclass
class _Options:
    second_region_width: int | None = None
    healer_curse_bit: int = 0
    mystic_call: int = 0x9F4E
    store_cell: int = 0x9030


class _Data:
    """Strings and linked text records placed one after another in the data hunk."""

    def __init__(self, hunk: _Hunk) -> None:
        self.hunk, self.free = hunk, 0x100

    def string(self, text: str) -> int:
        at = self.free
        self.hunk.data[at : at + len(text)] = text.encode()
        self.free += (len(text) + 2) & ~1
        return at

    def lines(self, *texts: str) -> int:
        records = [self.free + 14 * i for i in range(len(texts))]
        self.free += 14 * len(texts)
        for i, (at, text) in enumerate(zip(records, texts, strict=True)):
            self.hunk.at(at).ref(DATA, self.string(text)).w(0, 50 + 10 * i, 1)
            if i + 1 < len(texts):
                self.hunk.ref(DATA, records[i + 1])
            else:
                self.hunk.w(0, 0)
        return records[0]


def _call(h: _Hunk, hunk: int, offset: int) -> None:
    h.w(JSR).ref(hunk, offset)


def _opening(h: _Hunk, d: _Data, name: str, portrait: int, opts: _Options) -> None:
    h.w(0x41F9).ref(1, 0x1D0).w(0x43F9).ref(1, 0x1BC).w(0x2169, 0, 0x0A)
    h.w(0x2039).ref(1, 0x124)
    _call(h, 0, 0x908E)
    h.w(0x41F9).ref(DATA, d.string(name)).w(0x2279).ref(1, 0x12C)
    _call(h, 25, 0x186)
    h.branch_to(BSR, 0xA23A)
    _call(h, 0, 0x8B26)
    h.w(0x33FC, 100 + portrait).ref(DATA, opts.store_cell)
    h.w(0x33FC, 120 + portrait).ref(DATA, 0x9032)
    _call(h, 0, 0xC586)
    _call(h, 30, 0x41A)
    h.w(0x41F9).ref(DATA, d.lines(f"hi {name}", "second"))
    _call(h, 0, 0x9196)
    for i in range(2):
        h.w(0x303C, 40 + portrait * 10 + i, 0x323C, i * 2)
        _call(h, 44, 0x698)
    h.w(0x41F9).ref(31, 0x146)
    _call(h, 0, 0x8B7C)
    _call(h, 0, 0x1FD0)
    h.w(0x7000 | portrait)


def _replies(h: _Hunk, d: _Data, opts: _Options) -> None:
    for pos, opcode, target in ((0x9C64, 0x6700, 0x9D34), (0x9C6C, 0x6600, 0x9D42)):
        h.at(pos).branch_to(opcode, target)
    for pos, text in ((0x9C8C, "no stat"), (0x9D34, "nothing"), (0x9D42, "no"), (0x9D50, "same")):
        h.at(pos).w(0x41F9).ref(DATA, d.lines(text))
        _call(h, 0, 0x9196)
    for pos, sign in ((0x9CC0, "+"), (0x9D06, "-")):
        table = d.free
        d.free += 16
        for row, stat in enumerate((70, 72)):
            text = d.lines(f"{sign}{stat}")
            d.hunk.at(table + row * 8).w(0, stat).ref(DATA, text)
        h.at(pos).w(0x23FC).ref(DATA, table).ref(0, 0x9D82)
    h.at(0x9D10).w(0x7001)
    h.at(0x9D1C).w(0xD1FC, 0, 8)
    h.at(0x9D5C)
    _call(h, 0, 0x8F88)
    h.w(0x203C, 0, 33)
    for pos, opcode, target in ((0x9EB6, 0x6600, 0x9D42), (0x9EC2, 0x6700, 0x9D34)):
        h.at(pos).branch_to(opcode, target)
    h.at(0x9F2A).w(0x41F9).ref(DATA, d.lines("small", "coin")).w(0x6012)
    h.w(0x41F9).ref(DATA, d.lines("plain")).w(0x0802, opts.healer_curse_bit, 0x6706)
    h.w(0x41F9).ref(DATA, d.lines("cured"))
    _call(h, 0, 0x9196)
    h.branch_to(0x6000, 0x9D5C)


def _donation(h: _Hunk, d: _Data, opts: _Options) -> None:
    h.at(0x9F4E).w(0x2079).ref(1, 0xCE4).w(0x33E8, 0x4A).ref(DATA, 0x8F2A)
    h.w(0x33FC, 0).ref(DATA, 0x8F28).w(0x33C0).ref(DATA, 0x7DA6)
    _call(h, 0, 0x94BA)
    h.w(0x41F9).ref(11, 4)
    for disp, value in ((12, 30), (14, 40), (4, 9), (6, 7), (20, 5)):
        h.w(0x317C, value, disp)
    h.w(0x217C, 0, 0, 8)
    _call(h, 0, 0x9480)
    h.w(0x41F9).ref(11, 4).w(0x317C, 50, 12, 0x317C, 3, 20)
    if opts.second_region_width is not None:
        h.w(0x317C, opts.second_region_width, 4)
    _call(h, 0, 0x9480)
    h.w(0x41F9).ref(1, 0x1D0)
    for pos in (0xA0CC, 0xA0F4):
        h.at(pos).w(0x7003)
    h.at(0xA100).branch_to(BSR, 0xA24E)
    h.w(0x33FC, 1).ref(29, 0x18).w(0x2039).ref(35, 4)
    _call(h, 0, 0x908E)
    h.w(0x2079).ref(1, 0x12C).w(0x3039).ref(DATA, 0x7DA6).w(0x323C, 5, 0x343C, 6)
    _call(h, 28, 0x22C)
    h.w(0x2079).ref(1, 0x12C).w(0x303C, 4, 0x323C, 7, 0x343C, 8)
    _call(h, 28, 0x22C)
    h.w(0x41F9).ref(DATA, d.string("Gift")).w(0x303C, 9, 0x323C, 10, 0x343C, 4)
    _call(h, 0, 0x917C)
    h.w(0x45F9).ref(DATA, 0x8F20).w(0x3039).ref(DATA, 0x8F2A)
    _call(h, 0, 0x93CA)
    h.w(0x41F9).ref(DATA, 0x8F20).w(0x303C, 11, 0x323C, 12, 0xB582)
    _call(h, 0, 0x917C)
    _call(h, 0, 0x8F88)
    h.w(RTS)
    h.at(0xA23A).w(0x41F9).ref(DATA, d.string("c.cel")).w(0x2279).ref(1, 0x12C)
    _call(h, 26, 0x6FC)
    h.w(RTS)


def _mog(opts: _Options | None = None) -> MogCode:
    opts = opts or _Options()
    code, data = _Hunk(bytearray(0xA300)), _Hunk(bytearray(0x9100))
    d = _Data(data)
    code.at(0x9B66)
    _opening(code, d, "M.PIC", 0, opts)
    _call(code, 0, opts.mystic_call)
    code.at(0x9DD0)
    _opening(code, d, "H.PIC", 1, opts)
    code.branch_to(BSR, 0x9F4E)
    _replies(code, d, opts)
    _donation(code, d, opts)
    hunks = {0: code, DATA: data}
    sections = []
    for i in range(45):
        h = hunks.get(i)
        if h is None:
            sections.append(HunkSection(i, HUNK_DATA, 0, b"", {}))
            continue
        relocs = {target: tuple(positions) for target, positions in h.relocs.items()}
        sections.append(HunkSection(i, HUNK_CODE, len(h.data), bytes(h.data), relocs))
    exe = DecodedHunkExecutable("synthetic", tuple(sections))
    return MogCode(exe, supported=(zlib.crc32(bytes(code.data)),))


def test_openings_read_pictures_pointer_greetings_starts_and_portraits() -> None:
    screens = service_screens(_mog())
    mystic, healer = screens.mystic, screens.healer
    assert (mystic.picture, healer.picture) == ("M.PIC", "H.PIC")
    assert (mystic.pointer_x, mystic.pointer_y, healer.pointer_x) == (100, 120, 101)
    assert mystic.sounds == ((40, 0), (41, 2)) and healer.sounds == ((50, 0), (51, 2))
    assert (mystic.portrait, healer.portrait) == (0, 1)
    assert mystic.greeting == (TextLine("hi M.PIC", 0, 50, 1), TextLine("second", 0, 60, 1))


def test_regions_inherit_fields_and_the_redraw_keeps_its_order() -> None:
    donation = service_screens(_mog()).donation
    assert donation.cel_file == "c.cel" and donation.repeat_wait == 3
    assert donation.regions == (ScreenRegion(30, 40, 9, 7, 5), ScreenRegion(50, 40, 9, 7, 3))
    assert donation.draws == (
        CelDraw(None, 5, 6),
        CelDraw(4, 7, 8),
        LabelDraw("Gift", 9, 10, 4),
        NumberDraw(True, 11, 12, 0),
    )


def test_replies_tables_and_close_wait() -> None:
    screens = service_screens(_mog())
    assert screens.close_wait == 33 and screens.refused[0].text == "no"
    assert [line.text for line in screens.healer_small] == ["small", "coin"]
    assert screens.healer_healed[0].text == "cured" and screens.mystic_nothing[0].text == "no stat"
    assert [(s, lines[0].text) for s, lines in screens.mystic_raise] == [(70, "+70"), (72, "+72")]
    assert [(s, lines[0].text) for s, lines in screens.mystic_lower] == [(70, "-70"), (72, "-72")]


@pytest.mark.parametrize(
    ("opts", "match"),
    [
        (_Options(healer_curse_bit=1), "expected"),
        (_Options(mystic_call=0x9F50), "points elsewhere"),
        (_Options(store_cell=0x9034), "pointer stores"),
    ],
)
def test_rejects_unexpected_code(opts: _Options, match: str) -> None:
    with pytest.raises(MogVersionError, match=match):
        service_screens(_mog(opts))
