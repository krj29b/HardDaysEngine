"""Synth tables and sound front-end cells decoded from ``mog`` (ARCHITECTURE.md §6.2).

The synth (hunk 44) and the front end (hunk 16) are implemented in ``app``; this module ties them
to the supported code with a CRC-32 of each range, then reads every table address, cell value
and operand at a checked position. Pointers must be relocated into the expected hunk.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from harddaysengine.assets.fight_palette import MogVersionError

if TYPE_CHECKING:
    from harddaysengine.assets.mog_code import MogCode

SYNTH_HUNK = 44
WAVE_HUNK = 45
FRONT_HUNK = 16
CHANNELS = 4

SYNTH_CODE_CRC32 = 0x59D1AB36
SYNTH_CODE_END = 0x0BE8
FRONT_CODE_CRC32 = 0xEBA59B10
FRONT_CODE_END = 0x0134

_OP_LEA_PC_A0 = 0x41FA
_OP_LEA_PC_A6 = 0x4DFA
_OP_LEA_A0 = 0x41F9
_OP_ADDI_L_D0 = 0x0680
_OP_MULU_IMM_D0 = 0xC0FC
_OP_ASL3_D0 = 0xE740
_OP_MOVE_L_IMM_D1 = 0x223C
_OP_MOVE_L_IMM_A0 = 0x217C
_OP_MOVE_L_IMM_A2 = 0x257C
_OP_MOVE_L_IMM_A3 = 0x277C
_OP_MOVE_W_IMM_D1 = 0x323C
_OP_MOVE_W_IMM_A0 = 0x317C
_OP_MOVE_W_IMM_A4 = 0x397C
_OP_TST_W_ABS = 0x4A79
_OP_MOVE_W_IMM_D0 = 0x303C
_OP_ORI_B_ABS = 0x0039
_OP_ANDI_B_ABS = 0x0239
_OP_ANDI_B_D1 = 0x0201
_OP_ANDI_W_ABS = 0x0279
_OP_MOVE_W_ABS_IMM = 0x33FC
_OP_BSR_W = 0x6100
_OP_JMP = 0x4EF9
_OP_MOVEQ_D1 = 0x7200
_OP_ADDI_W_ABS = 0x0679

_SEQUENCE_LEA = 0x06FA
_SEQUENCE_ABS = (0x0A84, 0x0B34)
_INSTRUMENT_LEA = 0x0710
_INSTRUMENT_ROW = 0x0B0A
_INSTRUMENT_ADD = 0x0B0E
_VIBRATO_ROW = 0x0A06
_VIBRATO_ADD = 0x0A0A
_VIBRATO_COUNT = 0x0A14
_ENVELOPE_ROW = 0x0AF0
_ENVELOPE_ADD = 0x0AF2
_TEMPO_LEA = 0x077E
_TEMPO_DIVIDEND = 0x09CE
_FADE_TEST = 0x0B72
_FADE_STORE = 0x0B84
_REST_STORE = 0x07BA
_PARK_STORES = ((0x0328, _OP_MOVE_L_IMM_A2), (0x06DC, _OP_MOVE_L_IMM_A3))
_START_PERIOD = 0x072A
_INIT_PERIOD = 0x05C0
_INIT_LENGTH = 0x058C
_OP_MOVEQ_D0 = 0x7000
_WIPE_LEA = 0x04F6
_WIPE_COUNT = 0x0500
_STACK_TOPS = 0x075A
_SEQUENCE_COUNT = 168
_VIBRATO_ROW_WORDS = 15
_ROW_BYTES = 14
_WIPE_HEAD = 4
_ATTENUATION_FIELD = 0x008E
_PERIOD_FIELD = 0x0046

_FIXED_STRIDE = 0x10
_RELEASE = 0x0040
_RELEASE_STRIDE = 0x12
_PLAY = 0x0088
_PLAY_JUMP = 0x00B6
_STOP_ALL = 0x011A
_CURSOR_MASK = 3


@dataclass(slots=True, frozen=True)
class SynthInstrument:
    """One instrument row (§6.2.2).

    ``wave`` is True when ``sample`` is a hunk-45 offset (a waveform); otherwise ``sample`` is an
    offset inside a sound bank, relocated when the bank is loaded (T11.23c).
    """

    loop: int
    loop_offset: int
    words: int
    sample: int
    wave: bool
    pitch: int


@dataclass(slots=True, frozen=True)
class SynthData:
    """The synth's hunks and checked table addresses (§6.2.2); references are hunk-44 offsets."""

    memory: bytes
    waves: bytes
    sequence_table: int
    sequence_count: int
    instruments: tuple[SynthInstrument, ...]
    vibrato_base: int
    vibrato_row: int
    envelope_base: int
    envelope_row: int
    tempo: int
    tempo_dividend: int
    attenuation: int
    rest: int
    park: int
    start_period: int
    init_period: int
    init_length: int
    wipe_start: int
    wipe_bytes: int
    stack_tops: tuple[int, ...]


@dataclass(slots=True, frozen=True)
class FrontEndData:
    """Hunk-16 front-end cells and operands (§6.2.1)."""

    busy: int
    cursor: int
    full: int
    cursor_mask: int
    silence: int
    fixed_bits: tuple[int, ...]
    release_masks: tuple[int, ...]


@dataclass(slots=True, frozen=True)
class _Hunk:
    """Checked reads in one hunk of ``mog``."""

    mog: MogCode
    hunk: int

    def word(self, pos: int) -> int:
        return self.mog.hunk_word(self.hunk, pos)

    def long(self, pos: int) -> int:
        value: int = struct.unpack_from(">I", self.mog.hunk_data(self.hunk), pos)[0]
        return value

    def expect(self, pos: int, opcode: int, what: str) -> None:
        found = self.word(pos)
        if found != opcode:
            raise MogVersionError(
                f"mog: {what}: expected ${opcode:04X} at hunk {self.hunk} offset {pos}, "
                f"found ${found:04X}"
            )

    def immediate(self, pos: int, opcode: int, what: str) -> int:
        """The word after the instruction word ``opcode`` at ``pos``."""
        self.expect(pos, opcode, what)
        return self.word(pos + 2)

    def pointer(self, pos: int, target: int, what: str) -> int:
        """The long at ``pos``, which must be relocated into ``target``; its offset there."""
        pointer = self.mog.pointer(pos, what, self.hunk)
        if pointer.hunk != target:
            raise MogVersionError(
                f"mog: {what}: the long at hunk {self.hunk} offset {pos} "
                f"points into hunk {pointer.hunk}, not {target}"
            )
        return pointer.offset

    def relocated(self, pos: int, opcode: int, target: int, what: str) -> int:
        """Target offset of the relocated long operand of the instruction at ``pos``."""
        self.expect(pos, opcode, what)
        return self.pointer(pos + 2, target, what)

    def branch(self, pos: int, what: str) -> int:
        """Target of the ``BSR.W`` at ``pos``."""
        return self.pc_relative(pos, _OP_BSR_W, what)

    def pc_relative(self, pos: int, opcode: int, what: str) -> int:
        """Target of the ``(d16,PC)`` operand of the instruction at ``pos``."""
        self.expect(pos, opcode, what)
        displacement: int = struct.unpack_from(">h", self.mog.hunk_data(self.hunk), pos + 2)[0]
        return pos + 2 + displacement


def _check_crc(mog: MogCode, hunk: int, end: int, supported: int) -> None:
    data = mog.hunk_data(hunk)
    crc = zlib.crc32(data[:end]) if len(data) >= end else None
    if crc != supported:
        found = "missing" if crc is None else f"${crc:08X}"
        raise MogVersionError(
            f"mog: unsupported sound code in hunk {hunk} (CRC-32 {found}, supported "
            f"${supported:08X})"
        )


def _sequence_table(h: _Hunk) -> int:
    table = h.pc_relative(_SEQUENCE_LEA, _OP_LEA_PC_A0, "synth sequence table")
    for pos in _SEQUENCE_ABS:
        if h.relocated(pos, _OP_LEA_A0, SYNTH_HUNK, "synth sequence call") != table:
            raise MogVersionError("mog: synth sequence calls disagree on the table")
    end = table + 4 * _SEQUENCE_COUNT
    if end > len(h.mog.hunk_data(SYNTH_HUNK)) or h.long(table) != 0:
        raise MogVersionError("mog: synth sequence table: entry 0 must be null")
    for index in range(1, _SEQUENCE_COUNT):
        h.pointer(table + 4 * index, SYNTH_HUNK, f"synth sequence {index}")
    return table


def _instrument(waves: _Hunk, relocs: dict[int, int], pos: int) -> SynthInstrument | None:
    loop, loop_offset, words = struct.unpack_from(">hHH", waves.mog.hunk_data(WAVE_HUNK), pos)
    if pos + 10 not in relocs:
        if waves.long(pos + 6) != 0xFFFFFFFF or waves.long(pos + 10) != 0xFFFFFFFF:
            raise MogVersionError(f"mog: synth instrument row at {pos} is not relocated")
        return None
    pitch = waves.pointer(pos + 10, SYNTH_HUNK, "synth instrument pitch table")
    wave = pos + 6 in relocs
    sample = waves.pointer(pos + 6, WAVE_HUNK, "synth waveform") if wave else waves.long(pos + 6)
    return SynthInstrument(loop, loop_offset, words, sample, wave, pitch)


def _instruments(synth: _Hunk, waves: _Hunk) -> tuple[SynthInstrument, ...]:
    base = synth.relocated(_INSTRUMENT_LEA, _OP_LEA_A0, WAVE_HUNK, "synth instrument table")
    if synth.relocated(_INSTRUMENT_ADD, _OP_ADDI_L_D0, WAVE_HUNK, "synth instrument") != base:
        raise MogVersionError("mog: synth instrument command disagrees on the table")
    row = synth.immediate(_INSTRUMENT_ROW, _OP_MULU_IMM_D0, "synth instrument row")
    if row != _ROW_BYTES:
        raise MogVersionError(f"mog: synth instrument rows are {row} bytes, not {_ROW_BYTES}")
    rows: list[SynthInstrument] = []
    end = len(waves.mog.hunk_data(WAVE_HUNK))
    relocs = waves.mog.relocations(WAVE_HUNK)
    for pos in range(base, end - row + 1, row):
        instrument = _instrument(waves, relocs, pos)
        if instrument is None:
            return tuple(rows)
        rows.append(instrument)
    raise MogVersionError("mog: synth instrument table has no terminator row")


def _stack_tops(h: _Hunk) -> tuple[int, ...]:
    return tuple(
        h.pointer(_STACK_TOPS + 4 * channel, SYNTH_HUNK, "synth stack top")
        for channel in range(CHANNELS)
    )


def _moveq(h: _Hunk, pos: int, what: str) -> int:
    word = h.word(pos)
    if word & 0xFF00 != _OP_MOVEQ_D0:
        raise MogVersionError(f"mog: {what}: expected MOVEQ #n,D0 at hunk {h.hunk} offset {pos}")
    return word & 0xFF


def _long_immediate(h: _Hunk, pos: int, what: str) -> int:
    h.expect(pos, _OP_MOVE_L_IMM_D1, what)
    return h.long(pos + 2)


def _park(h: _Hunk) -> int:
    parks = {h.relocated(pos, op, WAVE_HUNK, "synth park buffer") for pos, op in _PARK_STORES}
    if len(parks) != 1:
        raise MogVersionError("mog: synth park buffers disagree")
    return parks.pop()


def parse_synth(mog: MogCode, *, crc: int = SYNTH_CODE_CRC32) -> SynthData:
    """Decode the synth tables of hunks 44 and 45 (§6.2.2); ``crc`` names the supported code."""
    _check_crc(mog, SYNTH_HUNK, SYNTH_CODE_END, crc)
    synth = _Hunk(mog, SYNTH_HUNK)
    waves = _Hunk(mog, WAVE_HUNK)
    vibrato_row = synth.immediate(_VIBRATO_ROW, _OP_MULU_IMM_D0, "synth vibrato row")
    if synth.immediate(_VIBRATO_COUNT, _OP_MOVE_W_IMM_D1, "synth vibrato loop") + 1 != (
        _VIBRATO_ROW_WORDS
    ):
        raise MogVersionError("mog: synth vibrato rows are not 15 words")
    synth.expect(_ENVELOPE_ROW, _OP_ASL3_D0, "synth envelope row")
    tempo = synth.pc_relative(_TEMPO_LEA, _OP_LEA_PC_A6, "synth tempo cell")
    fade = synth.relocated(_FADE_TEST, _OP_TST_W_ABS, SYNTH_HUNK, "synth fade flag")
    attenuation = synth.immediate(_FADE_STORE, _OP_MOVE_W_IMM_A0, "synth fade attenuation")
    synth.expect(_FADE_STORE + 4, _ATTENUATION_FIELD, "synth fade attenuation field")
    if synth.word(fade) != 0:
        raise MogVersionError("mog: synth fade flag is not clear in the file")
    synth.expect(_START_PERIOD + 4, _PERIOD_FIELD, "synth start period field")
    return SynthData(
        memory=mog.hunk_data(SYNTH_HUNK),
        waves=mog.hunk_data(WAVE_HUNK),
        sequence_table=_sequence_table(synth),
        sequence_count=_SEQUENCE_COUNT,
        instruments=_instruments(synth, waves),
        vibrato_base=synth.relocated(_VIBRATO_ADD, _OP_ADDI_L_D0, SYNTH_HUNK, "synth vibrato"),
        vibrato_row=vibrato_row,
        envelope_base=synth.relocated(_ENVELOPE_ADD, _OP_ADDI_L_D0, SYNTH_HUNK, "synth envelope"),
        envelope_row=1 << 3,
        tempo=synth.word(tempo),
        tempo_dividend=_long_immediate(synth, _TEMPO_DIVIDEND, "synth tempo dividend"),
        attenuation=attenuation,
        rest=synth.relocated(_REST_STORE, _OP_MOVE_L_IMM_A0, WAVE_HUNK, "synth rest buffer"),
        park=_park(synth),
        start_period=synth.immediate(_START_PERIOD, _OP_MOVE_W_IMM_A4, "synth start period"),
        init_period=synth.immediate(_INIT_PERIOD, _OP_MOVE_W_IMM_D0, "synth init period"),
        init_length=_moveq(synth, _INIT_LENGTH, "synth init length"),
        wipe_start=synth.relocated(_WIPE_LEA, _OP_LEA_A0, SYNTH_HUNK, "synth init wipe"),
        wipe_bytes=_WIPE_HEAD
        + 4 * (synth.immediate(_WIPE_COUNT, _OP_MOVE_W_IMM_D1, "synth init wipe count") + 1),
        stack_tops=_stack_tops(synth),
    )


def _jump(h: _Hunk, pos: int, start: int, what: str) -> None:
    if h.relocated(pos, _OP_JMP, SYNTH_HUNK, what) != start:
        raise MogVersionError(f"mog: {what} does not jump to the synth start")


def _fixed_bit(h: _Hunk, busy: int, start: int, channel: int) -> int:
    pos = channel * _FIXED_STRIDE
    if h.word(pos) != _OP_MOVEQ_D1 | channel:
        raise MogVersionError(f"mog: front-end fixed start {channel}: wrong channel")
    bit = h.immediate(pos + 2, _OP_ORI_B_ABS, "front-end fixed start") & 0xFF
    if h.pointer(pos + 6, FRONT_HUNK, "front-end busy") != busy:
        raise MogVersionError("mog: front-end fixed start marks another cell")
    _jump(h, pos + 10, start, "front-end fixed start")
    return bit


def _release(h: _Hunk, busy: int, channel: int) -> tuple[int, int]:
    pos = _RELEASE + channel * _RELEASE_STRIDE
    mask = h.immediate(pos, _OP_ANDI_B_ABS, "front-end release") & 0xFF
    if h.pointer(pos + 4, FRONT_HUNK, "front-end release") != busy:
        raise MogVersionError("mog: front-end release clears another cell")
    silence = h.immediate(pos + 8, _OP_MOVE_W_IMM_D0, "front-end silence")
    if h.branch(pos + 12, "front-end release play") != _PLAY:
        raise MogVersionError("mog: front-end release does not play")
    return mask, silence


def _stop_all(h: _Hunk, busy: int) -> None:
    if h.immediate(_STOP_ALL, _OP_MOVE_W_ABS_IMM, "front-end stop all") != 0 or (
        h.pointer(_STOP_ALL + 4, FRONT_HUNK, "front-end stop all") != busy
    ):
        raise MogVersionError("mog: front-end stop all does not clear the busy word")
    for channel in range(CHANNELS):
        target = h.branch(_STOP_ALL + 8 + 4 * channel, "front-end stop all release")
        if target != _RELEASE + channel * _RELEASE_STRIDE:
            raise MogVersionError(f"mog: front-end stop all skips release {channel}")


def _cursor(h: _Hunk) -> tuple[int, int]:
    step_at = _PLAY + 16
    if h.immediate(step_at, _OP_ADDI_W_ABS, "front-end cursor step") != 1:
        raise MogVersionError("mog: front-end cursor does not step by one")
    cursor = h.pointer(step_at + 4, FRONT_HUNK, "front-end cursor")
    mask = h.immediate(step_at + 8, _OP_ANDI_W_ABS, "front-end cursor mask")
    if mask != _CURSOR_MASK or h.pointer(step_at + 12, FRONT_HUNK, "cursor") != cursor:
        raise MogVersionError("mog: front-end cursor does not wrap over four channels")
    return cursor, mask


def parse_front_end(mog: MogCode, *, crc: int = FRONT_CODE_CRC32) -> FrontEndData:
    """Decode the hunk-16 front end's cells and operands (§6.2.1); ``crc`` names the code."""
    _check_crc(mog, FRONT_HUNK, FRONT_CODE_END, crc)
    h = _Hunk(mog, FRONT_HUNK)
    busy = h.pointer(_PLAY + 2, FRONT_HUNK, "front-end busy byte")
    full = h.immediate(_PLAY + 6, _OP_ANDI_B_D1, "front-end channel mask") & 0xFF
    cursor, cursor_mask = _cursor(h)
    start = h.mog.pointer(_PLAY_JUMP + 2, "front-end play", FRONT_HUNK).offset
    _jump(h, _PLAY_JUMP, start, "front-end play")
    releases = [_release(h, busy, channel) for channel in range(CHANNELS)]
    silences = {silence for _, silence in releases}
    if len(silences) != 1:
        raise MogVersionError("mog: front-end releases play different silences")
    _stop_all(h, busy)
    return FrontEndData(
        busy=h.word(busy) >> 8,
        cursor=h.word(cursor),
        full=full,
        cursor_mask=cursor_mask,
        silence=silences.pop(),
        fixed_bits=tuple(_fixed_bit(h, busy, start, channel) for channel in range(CHANNELS)),
        release_masks=tuple(mask for mask, _ in releases),
    )
