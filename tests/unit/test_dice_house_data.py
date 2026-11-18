"""Synthetic dice-house code with invented values proves the decoder (ARCHITECTURE.md §12.32)."""

from __future__ import annotations

import zlib
from dataclasses import dataclass

import pytest

from harddaysengine.assets.dice_house_data import SCRIPT_HUNK, dice_house
from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.hunk import HUNK_CODE, HUNK_DATA, DecodedHunkExecutable, HunkSection
from harddaysengine.assets.mog_code import MogCode
from harddaysengine.sim.dice_house import DiceLine, DiceRegion, Thrower
from tests.unit.test_sound_rules_decoder import _Hunk

JSR, RTS = 0x4EB9, 0x4E75
H = SCRIPT_HUNK


@dataclass
class _Options:
    fixed_entry: int = 0x10
    spawn_script: int = 0x116
    leave_kind: int = 2
    suffix_end: int = 0x14BC


class _Strings:
    def __init__(self, hunk: _Hunk) -> None:
        self.hunk, self.free = hunk, 0x10

    def put(self, text: str) -> int:
        at = self.free
        self.hunk.data[at : at + len(text)] = text.encode()
        self.free += (len(text) + 2) & ~1
        return at


def _file(
    code: _Hunk,
    s: _Strings,
    pos: int,
    name: str,
    register: tuple[int, int],
    loader: tuple[int, int],
) -> None:
    code.at(pos).w(0x41F9).ref(H, s.put(name)).w(0x2279).ref(*register).w(JSR).ref(*loader)


def _copy(code: _Hunk, pos: int, into: int) -> None:
    code.at(pos).w(0x41F9).ref(31, 0x146).w(0x43F9).ref(1, into).w(JSR).ref(0, 0x907C)


def _entry(code: _Hunk, s: _Strings, opts: _Options) -> None:
    code.at(0xA318).w(0x2079).ref(1, 0xCE4).w(0x4A68, 0x4A, 0x6E02, RTS)
    _file(code, s, 0xA34C, "room.p", (35, 4), (25, 0x186))
    _copy(code, 0xA35E, 0x1E6)
    _file(code, s, 0xA38A, "table.p", (35, 4), (25, 0x186))
    _copy(code, 0xA39C, 0x226)
    _file(code, s, 0xA3C6, "d.cel", (H, 0x3DE), (26, 0x6FC))
    code.at(0xA3FA).w(0x41F9).ref(11, 4)
    for disp, value in ((12, 200), (14, 30), (4, 20), (6, 10), (20, 2)):
        code.w(0x317C, value, disp)
    code.w(0x217C, 0, 1, 16, JSR).ref(0, 0x9480)
    code.w(0x41F9).ref(11, 4).w(0x317C, 60, 14, 0x217C, 0, opts.leave_kind, 16, JSR).ref(0, 0x9480)
    code.w(JSR).ref(0, 0x8B26)
    code.at(0xA4DA).w(0x41F9).ref(1, 0x1E6).w(0x203C, 0, 0x10, JSR).ref(0, 0xAADE)
    for pos, seq, entry in ((0xA4EC, 90, 0), (0xA4F6, 91, opts.fixed_entry), (0xA500, 92, 0x20)):
        code.at(pos).w(0x303C, seq, JSR).ref(16, entry)
    code.at(0xA510).w(0x33FC, 250).ref(4, 0x9030)
    code.at(0xA5BE).w(0x303C, 7, 0x323C, 8, 0x7400, 0x08C2, 1)


def _thrower(code: _Hunk, opts: _Options) -> None:
    code.at(0xA60C).w(0x41F9).ref(H, opts.spawn_script).w(0x45F9).ref(H, 0x3DE)
    code.w(0x303C, 150, 0x323C, 0, 0x343C, 90, 0x163C, 1, 0x7A44, JSR).ref(0, 0x7084)
    code.w(0x33FC, 4).ref(H, 0x3F8).w(RTS)
    code.at(0xA65E).w(0x23FC).ref(H, 0x116).ref(1, 0xC0C).w(0x33FC, 7).ref(H, 0x3F8)
    code.at(0xA690).w(0x0CA8, 0, 1, 16)
    code.at(0xA69A).w(0x0CA8, 0, 2, 16)
    code.at(0xA6B0).w(0x33E8, 20).ref(H, 0x402)
    code.at(0xA6D8).w(0x23FC).ref(H, 0x126).ref(1, 0xC0C).w(0x33FC, 2).ref(H, 0x3F8)


def _roll(code: _Hunk, data: _Hunk, s: _Strings, opts: _Options) -> None:
    code.at(0xA728).w(0x7E01)
    for i, (x, y) in enumerate(((11, 12), (13, 14))):
        code.at(0xA76A + 0x1C * i).w(0x2079).ref(H, 0x3DE).w(0x7000, 0x1039).ref(H, 0x3FE + i)
        code.w(0x323C, x, 0x343C, y, JSR).ref(28, 0x22C)
    lost_text, have_text = s.put("Lost XXX bits"), s.put("Have XXX")
    won_text = s.put("Won XXX")
    fields = {"bet": lost_text + 5, "gold": have_text + 5, "won": won_text + 4}
    code.at(0xA7E6).w(0x3039).ref(H, 0x402).w(0x45F9).ref(H, fields["bet"])
    code.at(0xA802).w(0x3028, 0x4A, 0x45F9).ref(H, fields["gold"])
    code.at(0xA846).w(0xC0C1)
    code.at(0xA852).w(0x45F9).ref(H, fields["won"])
    have = s.free
    data.at(have).ref(H, have_text).w(5, 40, 0, 0, 0)
    s.free += 14
    for pos, text in ((0xA816, lost_text), (0xA87C, won_text)):
        record = s.free
        s.free += 14
        data.at(record).ref(H, text).w(6, 30, 1).ref(H, have)
        code.at(pos).w(0x41F9).ref(H, record)
    code.at(0xA826).w(0x14FC, 0x20, 0x14FC, 0x21, opts.suffix_end, 0, RTS)
    code.at(0xA8B2).w(0x7019)


def _ramp(code: _Hunk) -> None:
    code.at(0xAADE).w(0x2279).ref(1, 0xCE4).w(0xD1C0)
    code.w(0x0CA9, 0, 0, 0x36, 0x6618, *(w for v in (1, 2, 3, 4) for w in (0x30FC, v)), 0x30BC, 5)
    code.w(0x6000, 0x18)
    code.w(0x0CA9, 0, 3, 0x36, 0x6614, *(w for v in (6, 7, 8, 9) for w in (0x30FC, v)), 0x30BC, 10)
    code.w(RTS)


def _mog(opts: _Options | None = None) -> MogCode:
    opts = opts or _Options()
    code, data = _Hunk(bytearray(0xAB80)), _Hunk(bytearray(0x500))
    s = _Strings(data)
    _entry(code, s, opts)
    _thrower(code, opts)
    _roll(code, data, s, opts)
    _ramp(code)
    hunks = {0: code, H: data}
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


def test_files_regions_ramps_and_sounds() -> None:
    house = dice_house(_mog())
    assert (house.tavern, house.table, house.cel_file) == ("room.p", "table.p", "d.cel")
    assert house.regions == (DiceRegion(200, 30, 20, 10, 1, 2), DiceRegion(200, 60, 20, 10, 2, 2))
    assert house.ramp_entry == 8 and house.ramps == ((0, (1, 2, 3, 4, 5)), (3, (6, 7, 8, 9, 10)))
    assert house.sounds == ((90, 0), (91, 1), (92, 2)) and house.pointer_x == 250


def test_thrower_gold_dice_texts_and_waits() -> None:
    house = dice_house(_mog())
    assert house.thrower == Thrower(150, 0, 90, 1, 0x44, 0x116, 0x126, 4, 7, 2, 1, 2)
    assert house.gold_at == (7, 8, 2) and house.dice_at == ((11, 12), (13, 14))
    assert house.lost == (DiceLine("Lost ", 6, 30, 1, "bet"), DiceLine("Have ", 5, 40, 0, "gold"))
    assert house.won[0] == DiceLine("Won ", 6, 30, 1, "won")
    assert house.suffix == " !" and house.roll_wait == 25


@pytest.mark.parametrize(
    ("opts", "match"),
    [
        (_Options(fixed_entry=0x88), "fixed-channel"),
        (_Options(spawn_script=0x200), "another script"),
        (_Options(suffix_end=0x14FD), "unexpected code"),
    ],
)
def test_rejects_unexpected_code(opts: _Options, match: str) -> None:
    with pytest.raises(MogVersionError, match=match):
        dice_house(_mog(opts))
