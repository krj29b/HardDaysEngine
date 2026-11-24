"""Synthetic sound routines with invented values prove the decoder (ARCHITECTURE.md §10.32)."""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field

import pytest

from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.hunk import HUNK_CODE, HUNK_DATA, DecodedHunkExecutable, HunkSection
from harddaysengine.assets.mog_code import MogCode
from harddaysengine.assets.sound_rules import FRONT_HUNK, GUARDIAN_HUNK, sound_rules
from harddaysengine.sim.sound import (
    CounterStep,
    CycleStep,
    DrawPick,
    Emit,
    ListStep,
    SoundCells,
    SoundKind,
    SoundRequest,
    SoundStep,
)

JSR, JMP, RTS = 0x4EB9, 0x4EF9, 0x4E75
PLAY = 0x88
GEN = 0xA2B0
BUSY = 0xC2


@dataclass
class _Hunk:
    """A hunk being written: data, cursor and relocations by target hunk."""

    data: bytearray
    pos: int = 0
    relocs: dict[int, list[int]] = field(default_factory=dict)

    def at(self, pos: int) -> _Hunk:
        self.pos = pos
        return self

    def w(self, *words: int) -> _Hunk:
        for word in words:
            struct.pack_into(">H", self.data, self.pos, word & 0xFFFF)
            self.pos += 2
        return self

    def ref(self, hunk: int, offset: int) -> _Hunk:
        self.relocs.setdefault(hunk, []).append(self.pos)
        struct.pack_into(">I", self.data, self.pos, offset)
        self.pos += 4
        return self

    def call(self, opcode: int, offset: int = PLAY) -> _Hunk:
        return self.w(opcode).ref(FRONT_HUNK, offset)

    def draw(self) -> _Hunk:
        return self.w(JSR).ref(0, GEN)

    def branch_to(self, opcode: int, target: int) -> _Hunk:
        return self.w(opcode, target - (self.pos + 2))


@dataclass
class _Options:
    fixed_bit: int = 1
    release_mask: int = 0x0E
    loop_back: int = 0x684
    list_opcode: int = 0x0679


def _front(h: _Hunk, opts: _Options) -> None:
    h.at(PLAY).w(0x1239).ref(FRONT_HUNK, BUSY)
    for ch in range(4):
        bit = opts.fixed_bit << ch if ch == 0 else 1 << ch
        h.at(ch * 0x10).w(0x7200 | ch, 0x0039, bit).ref(FRONT_HUNK, BUSY).w(JMP).ref(44, 0)
    for entry, mask in ((0x40, opts.release_mask), (0x52, 0x0D)):
        h.at(entry).w(0x0239, mask).ref(FRONT_HUNK, BUSY).w(0x303C, 300)
        h.branch_to(0x6100, PLAY).w(RTS)


def _masked(h: _Hunk, pos: int, mask: int, base: int, *, fold: bool, tail: bool) -> None:
    if pos:
        h.at(pos)
    h.draw().w(0x0280, 0, mask)
    if fold:
        h.w(0x6702, 0x5340)
    if fold:
        h.w(0x0640, base)
    else:
        h.w(0x0680, 0, base)
    h.call(JMP if tail else JSR)


def _code(h: _Hunk, opts: _Options) -> None:
    h.at(GEN).w(0x48E7)
    for pos, mask, base, fold in (
        (0x71D2, 3, 100, True),
        (0x721C, 1, 110, False),
        (0x7234, 1, 120, False),
        (0x724C, 3, 130, False),
        (0x7264, 1, 140, False),
        (0x727C, 3, 150, True),
    ):
        _masked(h, pos, mask, base, fold=fold, tail=True)
    h.at(0x71EC)
    _masked(h, 0, 3, 160, fold=True, tail=False)
    h.draw().w(0x0280, 0, 3, 0x0640, 170).call(JMP)
    plain = 0x72F8
    h.at(0x7296).w(0x0679, 1).ref(0, plain).w(0x0C79, 6).ref(0, plain).w(0x6D08)
    h.w(0x33FC, 0).ref(0, plain).w(0x3039).ref(0, plain).w(0x0640, 40).call(JMP)
    h.at(0x72C0).w(0x41F9).ref(0, 0x7304).w(0x6006).w(0x41F9).ref(0, 0x72FC)
    h.w(opts.list_opcode, 2).ref(0, 0x72FA).w(0x3039).ref(0, 0x72FA).w(0x0C70, 0xFFFF, 0)
    h.w(0x660A, 0x33FC, 0).ref(0, 0x72FA).w(0x7000, 0x3030, 0).call(JMP)
    h.at(plain).w(2, 4, 61, 62, 0xFFFF, 0xFFFF, 71, 72, 73, 0xFFFF)
    h.at(0x6D28).w(0x303C, 33).call(JMP, 0x00).w(0x303C, 34).call(JMP, 0x10)
    h.at(0xA8C2).draw().w(0x0240, 3, 0x41F9).ref(0, 0xA8E0).w(0x7000, 0x1030, 0)
    h.call(JSR, 0x30).w(RTS)
    h.data[0xA8E0:0xA8E4] = bytes((101, 102, 103, 104))


def _pairs(h: _Hunk, pairs: list[tuple[int, int]]) -> list[int]:
    starts = []
    for a, b in pairs:
        starts.append(h.pos)
        h.w(0x303C, a).call(JSR).w(0x303C, b).call(JSR).w(RTS)
    return starts


def _guardian(h: _Hunk, opts: _Options) -> None:
    h.at(0x41C).w(0x303C, 80).call(JSR, 0x00).w(RTS)
    h.at(0x428).draw().w(0x0240, 3)
    branches = h.pos
    h.at(branches + 20)
    starts = _pairs(h, [(1, 2), (3, 4), (5, 6), (7, 8)])
    h.at(branches).branch_to(0x6700, starts[1]).w(0xB07C, 1).branch_to(0x6700, starts[2])
    h.w(0xB07C, 2).branch_to(0x6700, starts[3])
    h.at(0x49E).draw().w(0x0240, 3, 0x41F9).ref(GUARDIAN_HUNK, 0x4DA)
    h.w(0x1030, 0, 0x4880).call(JSR).w(RTS)
    h.data[0x4DA:0x4DE] = bytes((10, 0x90, 12, 13))
    h.at(0x4DE).w(1)
    h.at(0x4BC).w(0x0679, 1).ref(GUARDIAN_HUNK, 0x4DE).w(0x0279, 1).ref(GUARDIAN_HUNK, 0x4DE)
    h.w(0x660A, 0x303C, 90).call(JSR).w(RTS)
    h.data[0x674:0x67C] = bytes(range(200, 208))
    h.at(0x682).w(0x7E02).draw().w(0x0240, 7, 0x41F9).ref(GUARDIAN_HUNK, 0x674)
    h.w(0x1030, 0, 0x0240, 0x00FF, 0x2F07).call(JSR).w(0x2E1F).branch_to(0x51CF, opts.loop_back)
    h.w(RTS)
    h.at(0xA84)
    for ch in range(4):
        h.w(0x303C, 20 + ch).call(JSR, ch * 0x10)
    h.w(RTS).draw().w(0x0240, 3, 0xB07C, 1)
    short = h.pos
    h.at(short + 8)
    starts = _pairs(h, [(41, 42), (43, 44), (45, 46)])
    h.at(short).w(0x6700 | (starts[1] - short - 2), 0xB07C, 2, 0x6700 | (starts[2] - short - 8))


def _mog(opts: _Options | None = None) -> MogCode:
    opts = opts or _Options()
    hunks = {0: _Hunk(bytearray(0xA900)), FRONT_HUNK: _Hunk(bytearray(0x100))}
    hunks[GUARDIAN_HUNK] = _Hunk(bytearray(0xC00))
    _front(hunks[FRONT_HUNK], opts)
    _code(hunks[0], opts)
    _guardian(hunks[GUARDIAN_HUNK], opts)
    sections = []
    for i in range(GUARDIAN_HUNK + 5):
        h = hunks.get(i)
        if h is None:
            sections.append(HunkSection(i, HUNK_DATA, 0, b"", {}))
            continue
        relocs = {target: tuple(positions) for target, positions in h.relocs.items()}
        sections.append(HunkSection(i, HUNK_CODE, len(h.data), bytes(h.data), relocs))
    code = bytes(hunks[0].data)
    return MogCode(
        DecodedHunkExecutable("synthetic", tuple(sections)), supported=(zlib.crc32(code),)
    )


def _routines(opts: _Options | None = None) -> dict[str, tuple[SoundStep, ...]]:
    return dict(sound_rules(_mog(opts)).routines)


def _pick(steps: tuple[SoundStep, ...]) -> tuple[tuple[int, ...], ...]:
    (step,) = steps
    assert isinstance(step, DrawPick)
    return step.choices


def test_masked_adds_read_masks_folds_and_bases() -> None:
    r = _routines()
    assert _pick(r["sound_pick_1"]) == ((100,), (100,), (101,), (102,))
    assert _pick(r["sound_pick_2"]) == ((110,), (111,))
    assert _pick(r["sound_pick_9"]) == ((130,), (131,), (132,), (133,))
    first, second = r["sound_pick_14"]
    assert isinstance(first, DrawPick) and isinstance(second, DrawPick)
    assert first.choices[:2] == ((160,), (160,)) and second.choices[0] == (170,)


def test_cycles_counters_and_initial_cells_come_from_the_executable() -> None:
    rules = sound_rules(_mog())
    r = dict(rules.routines)
    assert r["sound_pick_5"] == (CycleStep(1, 6, 0, 40),)
    assert r["sound_pick_11"] == (CounterStep(1, 1, 90),)
    assert rules.cells == SoundCells(plain=2, list_offsets=[4, 4], counter=1)
    (a,), (b,) = r["sound_pick_7"], r["sound_pick_6"]
    assert isinstance(a, ListStep) and isinstance(b, ListStep)
    assert (a.start, a.slot, a.words) == (0, 1, (61, 62, 0xFFFF))
    assert (b.start, b.slot, b.words) == (0, 0, (71, 72, 73, 0xFFFF))


def test_fixed_release_and_tables_keep_channels_signs_and_loop_counts() -> None:
    r = _routines()
    assert r["guardian_fixed_sound"] == (Emit(SoundRequest(SoundKind.FIXED, 80, 0)),)
    assert r["dragon_sound_1"] == (Emit(SoundRequest(SoundKind.FIXED, 34, 1)),)
    assert [s.request.channel for s in r["guardian_fixed_sounds"] if isinstance(s, Emit)] == [
        0,
        1,
        2,
        3,
    ]
    assert r["sound_release_0"] == (Emit(SoundRequest(SoundKind.RELEASE, 300, 0, 0x0E)),)
    assert r["sound_release_1"] == (Emit(SoundRequest(SoundKind.RELEASE, 300, 1, 0x0D)),)
    assert _pick(r["guardian_sound"]) == ((10,), (0xFF90,), (12,), (13,))
    assert r["sound_pick_dice"] == (Emit(SoundRequest(SoundKind.FIXED, 101, 3)),)
    loop = r["sound_pick_12"]
    assert len(loop) == 3 and _pick(loop[:1])[7] == (207,)


def test_branch_chains_map_each_value_to_its_pair() -> None:
    r = _routines()
    assert _pick(r["sound_pick_10"]) == ((3, 4), (5, 6), (7, 8), (1, 2))
    assert _pick(r["sound_pick_13"]) == ((41, 42), (43, 44), (45, 46), (41, 42))


@pytest.mark.parametrize(
    ("opts", "match"),
    [
        (_Options(fixed_bit=2), "marks another channel"),
        (_Options(release_mask=0x0C), "one channel"),
        (_Options(loop_back=0x686), "does not close"),
        (_Options(list_opcode=0x0479), "expected"),
    ],
)
def test_rejects_unexpected_code(opts: _Options, match: str) -> None:
    with pytest.raises(MogVersionError, match=match):
        sound_rules(_mog(opts))
