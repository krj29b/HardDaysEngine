"""The program's PAL module player on Paula (ARCHITECTURE.md §6.3.1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from harddaysengine.app.paula import SET, address, channel_dma
from harddaysengine.sim.m68k import s8, u8, u16

if TYPE_CHECKING:
    from harddaysengine.app.paula import Paula
    from harddaysengine.assets.audio import DecodedModule
    from harddaysengine.assets.music_data import MusicData

MODULE_REGION = 7
PATTERNS = 1084
ROW_BYTES = 16
PATTERN_BYTES = 1024


@dataclass(slots=True)
class ModuleVoice:
    """Retained sample and effect fields of one original module channel."""

    sample: int = 0
    words: int = 0
    loop: int = 0
    loop_words: int = 0
    period: int = 0
    volume: int = 0
    effect: int = 0
    argument: int = 0
    target: int = 0
    slide_up: bool = False
    slide_speed: int = 0
    vibrato: int = 0
    phase: int = 0


class ModulePlayer:
    """Advance one original music interrupt per tick, without a generic MOD library."""

    def __init__(self, module: DecodedModule, data: MusicData, paula: Paula) -> None:
        self.module, self.data, self.paula = module, data, paula
        self.voices = [ModuleVoice() for _ in range(4)]
        self.speed, self.counter = data.speed, 0
        self.position = self.row = 0
        self.break_row = False
        self.running = True
        memory = bytearray(module.raw_mod)
        offset = PATTERNS + module.pattern_count * PATTERN_BYTES
        starts = []
        for sample in module.samples:
            starts.append(offset)
            if offset + sample.length_bytes > len(memory):
                raise ValueError("truncated module samples")
            if offset + 4 > len(memory):
                memory.extend(bytes(offset + 4 - len(memory)))
            memory[offset : offset + 4] = bytes(4)
            offset += sample.length_bytes
        self.starts = tuple(starts)
        paula.memory.load(MODULE_REGION, bytes(memory))
        paula.intena(0x0780)
        paula.intreq(0x0780)
        for channel in range(4):
            paula.volume(channel, 0)

    def tick(self) -> None:
        """One PAL tick; first row after the initial speed count (§6.3.1)."""
        if not self.running:
            return
        self.counter = u8(self.counter + 1)
        if s8(self.counter) >= s8(self.speed):
            self.counter = 0
            self._row()
        else:
            for channel, voice in enumerate(self.voices):
                self._effect(channel, voice)

    def _sample(self, channel: int, voice: ModuleVoice, sample: int) -> None:
        if not 1 <= sample <= len(self.starts):
            raise ValueError(f"invalid module sample {sample}")
        header = self.module.samples[sample - 1]
        voice.sample = address(MODULE_REGION, self.starts[sample - 1])
        voice.words, voice.volume = header.length_bytes // 2, header.volume
        voice.loop = voice.sample + header.repeat_start_bytes
        voice.loop_words = header.repeat_length_bytes // 2
        if header.repeat_start_bytes:
            voice.words = (header.repeat_start_bytes + header.repeat_length_bytes) // 2
        self.paula.volume(channel, voice.volume)

    def _note(self, channel: int, voice: ModuleVoice, period: int) -> int:
        if not period:
            return 0
        if voice.effect == 3:
            voice.target = period if period != voice.period else 0
            voice.slide_up = period < voice.period
            return 0
        voice.period, voice.phase = period, 0
        bit = channel_dma(channel)
        self.paula.dmacon(bit)
        self.paula.location(channel, voice.sample)
        self.paula.length(channel, voice.words)
        self.paula.period(channel, period)
        return bit

    def _row(self) -> None:
        pattern = self.module.order_table[self.position]
        offset = PATTERNS + pattern * PATTERN_BYTES + self.row * ROW_BYTES
        dma = 0
        for channel, voice in enumerate(self.voices):
            start = offset + channel * 4
            event = self.module.raw_mod[start : start + 4]
            if len(event) != 4:
                raise ValueError("module row exceeds pattern data")
            a, b, c, d = event
            sample = (a & 0xF0) | (c >> 4)
            voice.effect, voice.argument = c & 15, d
            if sample:
                self._sample(channel, voice, sample)
            dma |= self._note(channel, voice, ((a & 15) << 8) | b)
            self._row_effect(channel, voice)
        self.paula.dmacon(SET | dma)
        self.paula.commit()
        for channel in reversed(range(4)):
            voice = self.voices[channel]
            self.paula.location(channel, voice.loop)
            self.paula.length(channel, voice.loop_words)
        self._next_row()

    def _row_effect(self, channel: int, voice: ModuleVoice) -> None:
        arg = voice.argument
        if voice.effect == 0xB:
            self.position = u8(arg - 1)
            self.break_row = not self.break_row
        elif voice.effect == 0xD:
            self.break_row = not self.break_row
        elif voice.effect == 0xC:
            self.paula.volume(channel, arg if s8(arg) <= 64 else 64)
        elif voice.effect == 0xF and arg & 31:
            self.speed, self.counter = arg & 31, 0

    def _next_row(self) -> None:
        self.row += 1
        if self.row == 64 or self.break_row:
            self.row, self.break_row = 0, False
            self.position = (self.position + 1) & 127
            if self.position == self.module.song_length:
                self.position = 0

    def _arpeggio(self, voice: ModuleVoice) -> int:
        phase = self.counter % 3
        if not phase:
            return voice.period
        offset = voice.argument >> 4 if phase == 1 else voice.argument & 15
        index = next((i for i, value in enumerate(self.data.periods) if voice.period >= value), 36)
        target = index + offset
        if target >= len(self.data.periods):
            raise ValueError("module arpeggio exceeds the original period table")
        return self.data.periods[target]

    def _slide(self, voice: ModuleVoice) -> None:
        if voice.argument:
            voice.slide_speed, voice.argument = voice.argument, 0
        if not voice.target:
            return
        step = -voice.slide_speed if voice.slide_up else voice.slide_speed
        voice.period = u16(voice.period + step)
        reached = voice.period <= voice.target if voice.slide_up else voice.period >= voice.target
        if reached:
            voice.period, voice.target = voice.target, 0

    def _vibrato(self, voice: ModuleVoice) -> int:
        if voice.argument:
            voice.vibrato = voice.argument
        wave = self.data.vibrato[(voice.phase >> 2) & 31]
        delta = (wave * (voice.vibrato & 15)) >> 6
        period = voice.period + (-delta if s8(voice.phase) < 0 else delta)
        voice.phase = u8(voice.phase + ((voice.vibrato >> 2) & 60))
        return period

    def _effect(self, channel: int, voice: ModuleVoice) -> None:
        period = voice.period
        if voice.effect == 0 and voice.argument:
            period = self._arpeggio(voice)
        elif voice.effect in (1, 2):
            period = self._portamento(voice)
        elif voice.effect == 3:
            self._slide(voice)
            period = voice.period
        elif voice.effect == 4:
            period = self._vibrato(voice)
        self.paula.period(channel, period)
        if voice.effect == 10:
            delta = voice.argument >> 4 or -(voice.argument & 15)
            voice.volume = max(0, min(64, voice.volume + delta))
            self.paula.volume(channel, voice.volume)

    @staticmethod
    def _portamento(voice: ModuleVoice) -> int:
        delta = -voice.argument if voice.effect == 1 else voice.argument
        clipped = max(113, min(856, voice.period + delta))
        voice.period = clipped
        return clipped
