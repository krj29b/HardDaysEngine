"""The ``mog`` four-voice synth sequencer (ARCHITECTURE.md §6.2.3 to §6.2.8).

One :class:`Voice` per Paula channel interprets a sequence byte stream once per VBL. All chip
access goes through a :class:`PaulaPort`, in the original's write order. The synth keeps a
working copy of hunk 44, so references, the voice stacks and the init wipe act on the same
bytes as in the original.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from harddaysengine.app.paula import SET, address, channel_dma, channel_interrupt
from harddaysengine.sim.m68k import s8, s16, u8, u16, u32

if TYPE_CHECKING:
    from collections.abc import Callable

    from harddaysengine.assets.synth_data import SynthData, SynthInstrument

VOICES = 4
RAW_REGION = 0
WAVE_REGION = 1
COMMAND = 0x80
REST_PERIOD = 1
INIT_INTERRUPTS = 2
LOOP_INTERRUPTS = 1
VOLUME_MASK = 0x3F
DECAY_LIMIT = 0x40
_INIT_DMA = 0x800F
_ALL_DMA = 0x000F
_AUDIO_INTERRUPTS = 0x0780
_TREMOLO_STAGES = 2
_VIBRATO_STAGES = 3
_ROW_WORDS = 15
_TREMOLO_DELTA = 5
_VIBRATO_DELTA = 7
_TREMOLO_DELAY = 10
_VIBRATO_DELAY = 12
_VIBRATO_STEPS = 2
_REPEAT_TREMOLO = 1
_REPEAT_VIBRATO = 2
_REPEAT_BITS = _REPEAT_TREMOLO | _REPEAT_VIBRATO
_HIGH_BYTE = 0xFF00


class SynthError(ValueError):
    """The synth reached a state the original would trap on or read garbage in."""


class PaulaPort(Protocol):
    """The chip registers the synth writes (§6.2.9)."""

    requests: int
    enabled: int

    def dmacon(self, value: int) -> None: ...
    def intena(self, value: int) -> None: ...
    def intreq(self, value: int) -> None: ...
    def location(self, channel: int, value: int) -> None: ...
    def length(self, channel: int, words: int) -> None: ...
    def period(self, channel: int, value: int) -> None: ...
    def volume(self, channel: int, value: int) -> None: ...


@dataclass(slots=True)
class Voice:
    """One voice record (§6.2.3). Words are unsigned 16-bit; ``loop`` is the signed loop word."""

    interrupts: int = 0
    pitch: int = 0
    envelope: int = 0
    stack: int = 0
    sample: int = 0
    loop: int = 0
    loop_offset: int = 0
    words: int = 0
    note: int = 0
    volume: int = 0
    retrigger: bool = False
    envelope_timer: int = 0
    envelope_on: bool = False
    envelope_phase: int = 0
    sequence: int = 0
    position: int = 0
    left: int = 0
    length: int = 0
    base_volume: int = 0
    base_period: int = 0
    period: int = 0
    tremolo_delays: list[int] = field(default_factory=lambda: [0] * _TREMOLO_STAGES)
    tremolo_steps: list[int] = field(default_factory=lambda: [0] * _TREMOLO_STAGES)
    vibrato_delays: list[int] = field(default_factory=lambda: [0] * _VIBRATO_STAGES)
    vibrato_steps: list[int] = field(default_factory=lambda: [0] * _VIBRATO_STAGES)
    row: list[int] = field(default_factory=lambda: [0] * _ROW_WORDS)
    tremolo: int = 0
    vibrato: int = 0
    flags: int = 0
    transpose: int = 0
    fade: int = 0


def _reload_tremolo(voice: Voice) -> None:
    for stage in range(_TREMOLO_STAGES):
        voice.tremolo_delays[stage] = voice.row[_TREMOLO_DELAY + stage]
        voice.tremolo_steps[stage] = voice.row[stage]


def _reload_vibrato(voice: Voice) -> None:
    for stage in range(_VIBRATO_STAGES):
        voice.vibrato_delays[stage] = voice.row[_VIBRATO_DELAY + stage]
        voice.vibrato_steps[stage] = voice.row[_VIBRATO_STEPS + stage]


def _stage(
    delays: list[int], steps: list[int], deltas: list[int], reloads: list[int]
) -> int | None:
    """The first stage that acts: its delta (0 while it waits), or ``None`` if none acts."""
    for stage, delay in enumerate(delays):
        if delay:
            delays[stage] = u16(delay - 1)
            return 0
        if steps[stage]:
            steps[stage] = u16(steps[stage] - 1)
            delays[stage] = reloads[stage]
            return deltas[stage]
    return None


def _tremolo(voice: Voice) -> None:
    row = voice.row
    delta = _stage(
        voice.tremolo_delays,
        voice.tremolo_steps,
        row[_TREMOLO_DELTA : _TREMOLO_DELTA + _TREMOLO_STAGES],
        row[_TREMOLO_DELAY : _TREMOLO_DELAY + _TREMOLO_STAGES],
    )
    if delta is not None:
        voice.tremolo = u16(voice.tremolo + delta)
        return
    voice.tremolo = 0
    if voice.flags & _REPEAT_TREMOLO:
        _reload_tremolo(voice)


def _vibrato(voice: Voice) -> None:
    row = voice.row
    deltas = [u16(s8(u8(word))) for word in row[_VIBRATO_DELTA : _VIBRATO_DELTA + _VIBRATO_STAGES]]
    delta = _stage(
        voice.vibrato_delays,
        voice.vibrato_steps,
        deltas,
        row[_VIBRATO_DELAY : _VIBRATO_DELAY + _VIBRATO_STAGES],
    )
    if delta is not None:
        voice.vibrato = u16(voice.vibrato + delta)
    elif voice.flags & _REPEAT_VIBRATO:
        _reload_vibrato(voice)


def _modulate(voice: Voice) -> None:
    """Count the note down, step tremolo and vibrato, and set volume and period (§6.2.6)."""
    voice.left = u16(voice.left - 1)
    if not voice.envelope:
        _tremolo(voice)
    _vibrato(voice)
    if not voice.envelope:
        voice.volume = u16(voice.base_volume + voice.tremolo) & VOLUME_MASK
    voice.period = u16(voice.base_period + voice.vibrato)


def _with_low(word: int, low: int) -> int:
    return (word & _HIGH_BYTE) | u8(low)


def _release(voice: Voice, rows: bytes) -> None:
    voice.envelope_phase = 0
    if voice.volume:
        voice.envelope_timer = rows[7]
        low = voice.volume & 0xFF
        voice.volume = 0 if low < rows[6] else _with_low(voice.volume, low - rows[6])


def _sustain(voice: Voice, rows: bytes) -> None:
    voice.volume = rows[4]
    if not s8(u8(voice.left)) > s8(rows[5]):
        voice.envelope_on = False


def _attack(voice: Voice, rows: bytes) -> None:
    voice.envelope_timer = rows[1]
    value = _with_low(voice.volume, voice.volume + rows[0])
    if value >= voice.base_volume:
        voice.envelope_phase = u16(voice.envelope_phase + 1)
        value = voice.base_volume
    voice.volume = value


def _decay(voice: Voice, rows: bytes) -> None:
    voice.envelope_timer = rows[3]
    value = _with_low(voice.volume, voice.volume - rows[2])
    if value & 0xFF >= DECAY_LIMIT:
        value = 0
    if value & 0xFF >= rows[4]:
        voice.volume = value
        return
    voice.envelope_phase = u16(voice.envelope_phase + 1)
    _sustain(voice, rows)


def _envelope_stage(voice: Voice, rows: bytes) -> None:
    if not voice.envelope_on:
        _release(voice, rows)
    elif voice.envelope_phase == 0:
        _attack(voice, rows)
    elif voice.envelope_phase == 1:
        _decay(voice, rows)
    else:
        _sustain(voice, rows)


type _Command = Callable[[int, Voice, int], tuple[int, bool]]


class Synth:
    """The synth of hunk 44 driving a :class:`PaulaPort` (§6.2.4 to §6.2.8)."""

    def __init__(self, data: SynthData, paula: PaulaPort) -> None:
        """Bind to ``data`` and ``paula``; call :meth:`init` before use."""
        self.data = data
        self.paula = paula
        self.memory = bytearray(data.memory)
        self.voices = [Voice() for _ in range(VOICES)]
        self.tempo = data.tempo
        self.samples = [self._sample_address(row) for row in data.instruments]
        self._commands: dict[int, _Command] = self._command_table()

    @staticmethod
    def _sample_address(row: SynthInstrument) -> int:
        return address(WAVE_REGION if row.wave else RAW_REGION, row.sample)

    def init(self) -> None:
        """A fresh load of the hunk followed by the init routine (§6.2.4)."""
        data, paula = self.data, self.paula
        self.memory = bytearray(data.memory)
        self.voices = [Voice() for _ in range(VOICES)]
        self.tempo = data.tempo
        paula.dmacon(_INIT_DMA)
        paula.intena(_AUDIO_INTERRUPTS)
        paula.intreq(_AUDIO_INTERRUPTS)
        end = data.wipe_start + data.wipe_bytes
        self.memory[data.wipe_start : end] = bytes(data.wipe_bytes)
        park = address(WAVE_REGION, data.park)
        self._each(paula.volume, 0)
        self._each(paula.length, 1)
        self._each(paula.period, REST_PERIOD)
        for voice in self.voices:
            voice.sample = park
        self._each(paula.location, park)
        for voice in self.voices:
            voice.words = data.init_length
        self._each(paula.length, data.init_length)
        for voice, top in zip(self.voices, data.stack_tops, strict=True):
            voice.interrupts = INIT_INTERRUPTS
            voice.stack = top
        self._each(paula.period, data.init_period)
        paula.dmacon(_ALL_DMA)

    @staticmethod
    def _each(write: Callable[[int, int], None], value: int) -> None:
        for channel in range(VOICES):
            write(channel, value)

    def start(self, sequence: int, channel: int) -> None:
        """Start ``sequence`` on ``channel`` (§6.2.5)."""
        if not 0 <= channel < VOICES:
            raise SynthError(f"synth start on channel {channel}")
        paula, data = self.paula, self.data
        voice = Voice(interrupts=self.voices[channel].interrupts, stack=data.stack_tops[channel])
        self.voices[channel] = voice
        paula.intena(channel_interrupt(channel))
        paula.intreq(channel_interrupt(channel))
        paula.dmacon(channel_dma(channel))
        paula.volume(channel, 0)
        paula.location(channel, address(WAVE_REGION, data.park))
        paula.length(channel, 1)
        paula.period(channel, REST_PERIOD)
        paula.dmacon(SET | channel_dma(channel))
        voice.sequence = voice.position = self._table_entry(sequence)
        self._instrument(voice, 0)
        voice.base_period = voice.period = data.start_period
        paula.location(channel, voice.sample)
        paula.length(channel, voice.words)
        paula.period(channel, data.start_period)
        paula.dmacon(channel_dma(channel))

    def tick(self) -> None:
        """One VBL: voice steps, envelope steps, then the register pass (§6.2.6)."""
        for channel in range(VOICES):
            self._voice_step(channel)
        for voice in self.voices:
            self._envelope(voice)
        for channel in range(VOICES):
            self._registers(channel)

    def interrupt(self) -> None:
        """The audio interrupt routine: serve every pending request (§6.2.7)."""
        paula = self.paula
        while True:
            pending = [ch for ch in range(VOICES) if paula.requests & channel_interrupt(ch)]
            if not pending:
                return
            channel = pending[0]
            paula.intreq(channel_interrupt(channel))
            if paula.enabled & channel_interrupt(channel):
                self._serve(channel)

    def fade(self, flag: bool) -> None:
        """One palette fade step: attenuate every voice while ``flag`` is set (§6.2.8)."""
        for voice in self.voices:
            voice.fade = self.data.attenuation if flag else 0

    def _serve(self, channel: int) -> None:
        paula, voice = self.paula, self.voices[channel]
        voice.interrupts = u16(voice.interrupts - 1)
        if voice.loop < 0:
            paula.location(channel, voice.sample + voice.loop_offset)
            paula.length(channel, u16(voice.words - (voice.loop_offset >> 1)))
            paula.intena(channel_interrupt(channel))
        elif voice.interrupts == 0:
            paula.intena(channel_interrupt(channel))
            paula.dmacon(channel_dma(channel))
        else:
            paula.location(channel, address(WAVE_REGION, self.data.park))
            paula.length(channel, 1)
        paula.intreq(channel_interrupt(channel))

    def _registers(self, channel: int) -> None:
        paula, voice = self.paula, self.voices[channel]
        paula.period(channel, voice.period)
        paula.volume(channel, max(0, s16(voice.volume - voice.fade)))
        if not voice.retrigger:
            return
        voice.retrigger = False
        paula.location(channel, voice.sample)
        paula.length(channel, voice.words)
        voice.interrupts = INIT_INTERRUPTS
        paula.dmacon(SET | channel_dma(channel))
        paula.intreq(channel_interrupt(channel))
        if voice.loop < 0:
            if voice.loop_offset == 0:
                paula.intena(channel_interrupt(channel))
                return
            voice.interrupts = LOOP_INTERRUPTS
        paula.intena(SET | channel_interrupt(channel))

    def _envelope(self, voice: Voice) -> None:
        if not voice.position or not voice.envelope:
            return
        if voice.envelope_timer:
            voice.envelope_timer = u8(voice.envelope_timer - 1)
            if voice.envelope_timer:
                return
        _envelope_stage(voice, self._bytes(voice.envelope, self.data.envelope_row))

    def _voice_step(self, channel: int) -> None:
        voice = self.voices[channel]
        if not voice.position:
            return
        if not voice.left:
            if not voice.loop_offset:
                self._rest(channel, restart=True)
            self._read(channel, voice)
        _modulate(voice)

    def _rest(self, channel: int, *, restart: bool) -> None:
        paula = self.paula
        paula.dmacon(channel_dma(channel))
        paula.intena(channel_interrupt(channel))
        paula.intreq(channel_interrupt(channel))
        paula.volume(channel, 0)
        paula.location(channel, address(WAVE_REGION, self.data.rest))
        paula.length(channel, 1)
        paula.period(channel, REST_PERIOD)
        if restart:
            paula.dmacon(SET | channel_dma(channel))

    def _read(self, channel: int, voice: Voice) -> None:
        position = voice.position
        while True:
            byte = self._byte(position)
            position += 1
            if byte < COMMAND:
                self._note(channel, voice, byte)
                break
            command = self._commands.get(byte)
            if command is None:
                raise SynthError(f"synth: byte ${byte:02X} at {position - 1} is not a command")
            position, done = command(channel, voice, position)
            if done:
                break
        voice.position = position
        voice.left = voice.length

    def _note(self, channel: int, voice: Voice, note: int) -> None:
        voice.note = note
        self._rest(channel, restart=False)
        if voice.envelope:
            voice.envelope_on = True
            voice.envelope_phase = voice.volume = voice.envelope_timer = 0
        index = u16(u16(note * 4 + voice.transpose) * 2)
        voice.base_period = self._word(voice.pitch + s16(index))
        voice.retrigger = True
        _reload_tremolo(voice)
        voice.tremolo = 0
        _reload_vibrato(voice)
        voice.vibrato = 0

    def _instrument(self, voice: Voice, index: int) -> None:
        if index >= len(self.data.instruments):
            raise SynthError(f"synth: instrument {index} is past the table")
        row = self.data.instruments[index]
        voice.loop, voice.loop_offset, voice.words = row.loop, row.loop_offset, row.words
        voice.sample = self.samples[index]
        voice.pitch = row.pitch

    def _table_entry(self, index: int) -> int:
        return self._long(self.data.sequence_table + s16(u16(index * 4)))

    def _check(self, position: int, size: int) -> None:
        if position < 0 or position + size > len(self.memory):
            raise SynthError(f"synth: read of {size} bytes at {position} is outside hunk 44")

    def _byte(self, position: int) -> int:
        self._check(position, 1)
        return self.memory[position]

    def _bytes(self, position: int, size: int) -> bytes:
        self._check(position, size)
        return bytes(self.memory[position : position + size])

    def _word(self, position: int) -> int:
        self._check(position, 2)
        value: int = struct.unpack_from(">H", self.memory, position)[0]
        return value

    def _long(self, position: int) -> int:
        self._check(position, 4)
        value: int = struct.unpack_from(">I", self.memory, position)[0]
        return value

    def _put(self, position: int, fmt: str, value: int) -> None:
        self._check(position, struct.calcsize(fmt))
        struct.pack_into(fmt, self.memory, position, value)

    def _push(self, voice: Voice, fmt: str, value: int) -> None:
        voice.stack -= struct.calcsize(fmt)
        self._put(voice.stack, fmt, value)

    def _command_table(self) -> dict[int, _Command]:
        return {
            0x80: self._set_volume,
            0x84: self._skip,
            0x88: self._restart,
            0x8C: self._set_length,
            0x90: self._end,
            0x94: self._set_tempo,
            0x98: self._sum_length,
            0x9C: self._load_row,
            0xA0: self._skip,
            0xA4: self._skip,
            0xA8: self._set_flags,
            0xAC: self._stop,
            0xB0: self._call,
            0xB4: self._return,
            0xB8: self._add_transpose,
            0xBC: self._set_transpose,
            0xC0: self._loop_start,
            0xC4: self._loop_end,
            0xC8: self._set_envelope,
            0xCC: self._clear_envelope,
            0xD0: self._set_instrument,
            0xD4: self._jump,
        }

    def _set_volume(self, _channel: int, voice: Voice, position: int) -> tuple[int, bool]:
        voice.base_volume = self._byte(position)
        return position + 1, False

    def _skip(self, _channel: int, _voice: Voice, position: int) -> tuple[int, bool]:
        return position + 1, False

    def _restart(self, _channel: int, voice: Voice, _position: int) -> tuple[int, bool]:
        voice.position = voice.sequence
        return voice.sequence, False

    def _set_length(self, _channel: int, voice: Voice, position: int) -> tuple[int, bool]:
        voice.length = u16(self._byte(position) * self.tempo)
        return position + 1, False

    def _end(self, _channel: int, _voice: Voice, position: int) -> tuple[int, bool]:
        return position, True

    def _set_tempo(self, _channel: int, _voice: Voice, position: int) -> tuple[int, bool]:
        divisor = self._byte(position)
        if divisor == 0:
            raise SynthError(f"synth: tempo divisor 0 at {position}")
        self.tempo = u16(self.data.tempo_dividend // divisor)
        return position + 1, False

    def _sum_length(self, _channel: int, voice: Voice, position: int) -> tuple[int, bool]:
        count = u16(self._byte(position) - 1) + 1
        total = sum(self._bytes(position + 1, count))
        voice.length = u16(u16(total) * self.tempo)
        return position + 1 + count, False

    def _load_row(self, _channel: int, voice: Voice, position: int) -> tuple[int, bool]:
        data = self.data
        voice.flags &= ~_REPEAT_BITS & 0xFF
        start = data.vibrato_base + u32(self._byte(position) * data.vibrato_row)
        voice.row = [u16(s8(byte)) for byte in self._bytes(start, _ROW_WORDS)]
        voice.tremolo_delays = [0] * _TREMOLO_STAGES
        voice.vibrato_delays = [0] * _VIBRATO_STAGES
        voice.tremolo = voice.vibrato = 0
        return position + 1, False

    def _set_flags(self, _channel: int, voice: Voice, position: int) -> tuple[int, bool]:
        voice.flags |= self._byte(position)
        return position + 1, False

    def _stop(self, channel: int, voice: Voice, _position: int) -> tuple[int, bool]:
        paula = self.paula
        voice.left = 0
        voice.sequence = 0
        paula.intena(channel_interrupt(channel))
        paula.intreq(channel_interrupt(channel))
        paula.dmacon(channel_dma(channel))
        voice.retrigger = False
        return 0, True

    def _call(self, _channel: int, voice: Voice, position: int) -> tuple[int, bool]:
        index = self._byte(position)
        self._push(voice, ">I", position + 1)
        return self._table_entry(index), False

    def _return(self, _channel: int, voice: Voice, _position: int) -> tuple[int, bool]:
        position = self._long(voice.stack)
        voice.stack += 4
        return position, False

    def _add_transpose(self, _channel: int, voice: Voice, position: int) -> tuple[int, bool]:
        step = self._byte(position)
        voice.transpose = u16(voice.transpose + s8(step)) if step else 0
        return position + 1, False

    def _set_transpose(self, _channel: int, voice: Voice, position: int) -> tuple[int, bool]:
        voice.transpose = u16(s8(self._byte(position)))
        return position + 1, False

    def _loop_start(self, _channel: int, voice: Voice, position: int) -> tuple[int, bool]:
        count = self._byte(position)
        self._push(voice, ">I", position + 1 if count else 0)
        self._push(voice, ">H", count)
        return position + 1, False

    def _loop_end(self, _channel: int, voice: Voice, position: int) -> tuple[int, bool]:
        target = self._long(voice.stack + 2)
        if not target:
            voice.stack += 6
            return position, False
        count = u16(self._word(voice.stack) - 1)
        self._put(voice.stack, ">H", count)
        if not count:
            self._put(voice.stack + 2, ">I", 0)
        return target, False

    def _set_envelope(self, _channel: int, voice: Voice, position: int) -> tuple[int, bool]:
        voice.envelope = self.data.envelope_base + u16(
            self._byte(position) * self.data.envelope_row
        )
        return position + 1, False

    def _clear_envelope(self, _channel: int, voice: Voice, position: int) -> tuple[int, bool]:
        voice.envelope = 0
        return position, False

    def _set_instrument(self, _channel: int, voice: Voice, position: int) -> tuple[int, bool]:
        self._instrument(voice, self._byte(position))
        return position + 1, False

    def _jump(self, _channel: int, _voice: Voice, position: int) -> tuple[int, bool]:
        return self._table_entry(self._byte(position)), False
