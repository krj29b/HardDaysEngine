"""Synthetic synth and front-end code with invented values prove the decoder (§6.2)."""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field, replace

import pytest

from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.hunk import HUNK_CODE, HUNK_DATA, DecodedHunkExecutable, HunkSection
from harddaysengine.assets.mog_code import MogCode
from harddaysengine.assets.synth_data import (
    FRONT_CODE_END,
    FRONT_HUNK,
    SYNTH_CODE_END,
    SYNTH_HUNK,
    WAVE_HUNK,
    FrontEndData,
    SynthData,
    parse_front_end,
    parse_synth,
)

TABLE = 0x2000
INSTRUMENTS = 0x40
VIBRATO = 0x1800
ENVELOPE = 0x1880
TEMPO_CELL = 0x0BEE
FADE_CELL = 0x0BE8
WIPE = 0x0BF0
STACKS = (0x0C10, 0x0C90, 0x0D10, 0x0D90)
START = 0x0698
BUSY = 0xC2
CURSOR = 0xBE


@dataclass
class _Hunk:
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

    def long(self, value: int) -> _Hunk:
        struct.pack_into(">I", self.data, self.pos, value)
        self.pos += 4
        return self

    def ref(self, hunk: int, offset: int) -> _Hunk:
        self.relocs.setdefault(hunk, []).append(self.pos)
        return self.long(offset)


@dataclass
class _Options:
    vibrato_row: int = 15
    vibrato_words: int = 14
    tempo: int = 7
    dividend: int = 600
    attenuation: int = 12
    start_period: int = 0x123
    init_period: int = 0x111
    init_length: int = 6
    park: tuple[int, int] = (0, 0)
    null_entry: int = 0
    terminator: bool = True
    silence: int = 0x55
    masks: tuple[int, ...] = (0x3E, 0x3D, 0x3B, 0x37)
    fixed: tuple[int, ...] = (1, 2, 4, 8)
    cursor_mask: int = 3
    busy: int = 0x0500
    cursor: int = 2
    stop_targets: tuple[int, ...] = (0, 1, 2, 3)


def _synth(h: _Hunk, opts: _Options) -> None:
    h.at(0x06FA).w(0x41FA, TABLE - 0x06FC)
    for pos in (0x0A84, 0x0B34):
        h.at(pos).w(0x41F9).ref(SYNTH_HUNK, TABLE)
    h.at(0x0710).w(0x41F9).ref(WAVE_HUNK, INSTRUMENTS)
    h.at(0x0B0A).w(0xC0FC, 14)
    h.at(0x0B0E).w(0x0680).ref(WAVE_HUNK, INSTRUMENTS)
    h.at(0x0A06).w(0xC0FC, opts.vibrato_row)
    h.at(0x0A0A).w(0x0680).ref(SYNTH_HUNK, VIBRATO)
    h.at(0x0A14).w(0x323C, opts.vibrato_words)
    h.at(0x0AF0).w(0xE740)
    h.at(0x0AF2).w(0x0680).ref(SYNTH_HUNK, ENVELOPE)
    h.at(0x077E).w(0x4DFA, TEMPO_CELL - 0x0780)
    h.at(TEMPO_CELL).w(opts.tempo)
    h.at(0x09CE).w(0x223C).long(opts.dividend)
    h.at(0x0B72).w(0x4A79).ref(SYNTH_HUNK, FADE_CELL)
    h.at(0x0B84).w(0x317C, opts.attenuation, 0x008E)
    h.at(0x07BA).w(0x217C).ref(WAVE_HUNK, 0x40)
    h.at(0x0328).w(0x257C).ref(WAVE_HUNK, opts.park[0])
    h.at(0x06DC).w(0x277C).ref(WAVE_HUNK, opts.park[1])
    h.at(0x072A).w(0x397C, opts.start_period, 0x0046)
    h.at(0x05C0).w(0x303C, opts.init_period)
    h.at(0x058C).w(0x7000 | opts.init_length)
    h.at(0x04F6).w(0x41F9).ref(SYNTH_HUNK, WIPE)
    h.at(0x0500).w(0x323C, 0x3F)
    h.at(0x075A)
    for top in STACKS:
        h.ref(SYNTH_HUNK, top)
    h.at(TABLE).long(opts.null_entry)
    for index in range(1, 168):
        h.ref(SYNTH_HUNK, 0x1900 + index)


def _waves(h: _Hunk, opts: _Options) -> None:
    h.at(INSTRUMENTS).w(0xFFFF, 0, 4).ref(WAVE_HUNK, 8).ref(SYNTH_HUNK, 0x1700)
    h.w(0xFFFF, 6, 12).ref(WAVE_HUNK, 16).ref(SYNTH_HUNK, 0x1710)
    h.w(3, 0, 900).long(0x1234).ref(SYNTH_HUNK, 0x1720)
    if opts.terminator:
        h.w(0, 0, 0).long(0xFFFFFFFF).long(0xFFFFFFFF)


def _front(h: _Hunk, opts: _Options) -> None:
    for channel in range(4):
        h.at(channel * 16).w(0x7200 | channel, 0x0039, opts.fixed[channel]).ref(FRONT_HUNK, BUSY)
        h.w(0x4EF9).ref(SYNTH_HUNK, START)
    for channel in range(4):
        pos = 0x40 + 18 * channel
        h.at(pos).w(0x0239, opts.masks[channel]).ref(FRONT_HUNK, BUSY)
        h.w(0x303C, opts.silence if channel else 0x55, 0x6100, 0x88 - (pos + 14), 0x4E75)
    h.at(0x88).w(0x1239).ref(FRONT_HUNK, BUSY).w(0x0201, 0x0F, 0xB23C, 0x0F, 0x6724)
    h.w(0x0679, 1).ref(FRONT_HUNK, CURSOR).w(0x0279, opts.cursor_mask).ref(FRONT_HUNK, CURSOR)
    h.w(0x3239).ref(FRONT_HUNK, CURSOR).w(0x0339).ref(FRONT_HUNK, BUSY).w(0x66E2)
    h.w(0x4EF9).ref(SYNTH_HUNK, START).w(0x4E75)
    h.at(CURSOR).w(opts.cursor)
    h.at(BUSY).w(opts.busy)
    h.at(0x011A).w(0x33FC, 0).ref(FRONT_HUNK, BUSY)
    for channel, target in enumerate(opts.stop_targets):
        pos = 0x0122 + 4 * channel
        h.at(pos).w(0x6100, 0x40 + 18 * target - (pos + 2))


def _mog(opts: _Options | None = None) -> MogCode:
    opts = opts or _Options()
    hunks = {
        0: _Hunk(bytearray(0x10)),
        FRONT_HUNK: _Hunk(bytearray(0x140)),
        SYNTH_HUNK: _Hunk(bytearray(TABLE + 168 * 4)),
        WAVE_HUNK: _Hunk(bytearray(INSTRUMENTS + (4 if opts.terminator else 3) * 14)),
    }
    _synth(hunks[SYNTH_HUNK], opts)
    _waves(hunks[WAVE_HUNK], opts)
    _front(hunks[FRONT_HUNK], opts)
    sections = []
    for index in range(WAVE_HUNK + 1):
        h = hunks.get(index)
        if h is None:
            sections.append(HunkSection(index, HUNK_DATA, 0, b"", {}))
            continue
        relocs = {target: tuple(positions) for target, positions in h.relocs.items()}
        sections.append(HunkSection(index, HUNK_CODE, len(h.data), bytes(h.data), relocs))
    executable = DecodedHunkExecutable("synthetic", tuple(sections))
    return MogCode(executable, supported=(zlib.crc32(bytes(hunks[0].data)),))


def _crc(mog: MogCode, hunk: int, end: int) -> int:
    return zlib.crc32(mog.hunk_data(hunk)[:end])


def _synth_data(opts: _Options | None = None) -> SynthData:
    mog = _mog(opts)
    return parse_synth(mog, crc=_crc(mog, SYNTH_HUNK, SYNTH_CODE_END))


def _front_data(opts: _Options | None = None) -> FrontEndData:
    mog = _mog(opts)
    return parse_front_end(mog, crc=_crc(mog, FRONT_HUNK, FRONT_CODE_END))


def test_synth_tables_and_operands_come_from_the_code() -> None:
    data = _synth_data()
    assert (data.sequence_table, data.sequence_count) == (TABLE, 168)
    assert (data.vibrato_base, data.vibrato_row) == (VIBRATO, 15)
    assert (data.envelope_base, data.envelope_row) == (ENVELOPE, 8)
    assert (data.tempo, data.tempo_dividend, data.attenuation) == (7, 600, 12)
    assert (data.rest, data.park, data.start_period) == (0x40, 0, 0x123)
    assert (data.init_period, data.init_length) == (0x111, 6)
    assert (data.wipe_start, data.wipe_bytes) == (WIPE, 4 + 4 * 0x40)
    assert data.stack_tops == STACKS
    assert len(data.memory) == TABLE + 168 * 4


def test_instrument_rows_end_at_the_terminator_and_name_their_sample_space() -> None:
    rows = _synth_data().instruments
    assert len(rows) == 3
    assert (rows[0].loop, rows[0].words, rows[0].sample, rows[0].wave) == (-1, 4, 8, True)
    assert (rows[1].loop_offset, rows[1].pitch) == (6, 0x1710)
    assert (rows[2].loop, rows[2].sample, rows[2].wave, rows[2].pitch) == (3, 0x1234, False, 0x1720)


def test_front_end_cells_masks_and_silence_come_from_the_code() -> None:
    front = _front_data()
    assert (front.busy, front.cursor, front.full, front.cursor_mask) == (5, 2, 0x0F, 3)
    assert front.silence == 0x55
    assert front.fixed_bits == (1, 2, 4, 8)
    assert front.release_masks == (0x3E, 0x3D, 0x3B, 0x37)


def test_unsupported_code_is_rejected_by_its_crc() -> None:
    mog = _mog()
    with pytest.raises(MogVersionError, match="hunk 44"):
        parse_synth(mog, crc=0)
    with pytest.raises(MogVersionError, match="hunk 16"):
        parse_front_end(mog, crc=0)


@pytest.mark.parametrize(
    ("opts", "match"),
    [
        (_Options(vibrato_words=13), "15 words"),
        (_Options(park=(0, 2)), "park buffers"),
        (_Options(null_entry=4), "entry 0"),
        (_Options(terminator=False), "terminator"),
    ],
)
def test_synth_decoder_rejects_unexpected_code(opts: _Options, match: str) -> None:
    with pytest.raises(MogVersionError, match=match):
        _synth_data(opts)


@pytest.mark.parametrize(
    ("opts", "match"),
    [
        (_Options(silence=0x56), "different silences"),
        (_Options(cursor_mask=7), "four channels"),
        (_Options(stop_targets=(0, 1, 3, 3)), "skips release 2"),
    ],
)
def test_front_end_decoder_rejects_unexpected_code(opts: _Options, match: str) -> None:
    with pytest.raises(MogVersionError, match=match):
        _front_data(opts)


def test_unrelocated_instrument_rows_are_rejected() -> None:
    mog = _mog()
    sections = list(mog.executable.sections)
    waves = sections[WAVE_HUNK]
    relocs = {
        target: tuple(pos for pos in positions if pos != INSTRUMENTS + 14 + 10)
        for target, positions in waves.relocs.items()
    }
    sections[WAVE_HUNK] = replace(waves, relocs=relocs)
    broken = MogCode(
        replace(mog.executable, sections=tuple(sections)),
        supported=(zlib.crc32(mog.hunk_data(0)),),
    )
    with pytest.raises(MogVersionError, match="not relocated"):
        parse_synth(broken, crc=_crc(broken, SYNTH_HUNK, SYNTH_CODE_END))
