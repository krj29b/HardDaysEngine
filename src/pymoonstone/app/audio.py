"""Paula 4-voice 50 Hz VBL synth, 8SVX mixer, and pygame audio backend (ARCHITECTURE.md §6)."""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pygame

from pymoonstone.assets.audio import (
    DEFAULT_PAULA_PERIOD,
    DEFAULT_SAMPLE_RATE,
    PAL_PAULA_CLOCK,
    decode_iff_8svx,
    paula_period_to_hz,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from numpy.typing import NDArray

    from pymoonstone.assets.audio import DecodedModule, DecodedSampleBank
    from pymoonstone.assets.hunk import SynthInstrumentRecord
    from pymoonstone.assets.tables import SynthSequenceEntry, SynthSequenceTables

PAL_VBL_HZ = 50
DEFAULT_MIX_RATE = 22050
PAULA_CHANNEL_COUNT = 4
PAULA_MAX_VOLUME = 64
PAULA_MIN_PERIOD = 113
PAULA_MAX_PERIOD = 856
PAULA_CHANNEL_PAN: tuple[tuple[float, float], ...] = (
    (1.0, 0.0),
    (0.0, 1.0),
    (0.0, 1.0),
    (1.0, 0.0),
)

_MAX_OPS_PER_TICK = 64
_NOTE_OP_MAX = 0x7F
_OP_END = 0x80
_OP_SET_INSTRUMENT = 0x81
_OP_SET_VIBRATO = 0x82
_OP_SET_PORTAMENTO = 0x83
_OP_SET_VOL_SLIDE = 0x84
_OP_SET_VOLUME = 0x85
_OP_SET_ARPEGGIO = 0x86
_OP_SET_TRANSPOSE = 0x88
_OP_RELEASE = 0x8B


@dataclass(slots=True, frozen=True)
class SampleAsset:
    """Decoded mono 8-bit signed PCM sample registered for mixer playback (ARCHITECTURE.md §6.1)."""

    name: str
    pcm8: NDArray[np.int8]
    sample_rate: int = DEFAULT_SAMPLE_RATE
    loop_start: int = 0
    loop_length: int = 0


@dataclass(slots=True)
class VoiceState:
    """Mutable per-channel Paula voice state updated at 50 Hz VBL (ARCHITECTURE.md §6.2)."""

    channel: int
    active: bool = False
    stream: bytes = b""
    pc: int = 0
    wait_ticks: int = 0
    instrument_idx: int = 0
    note: int = 24
    transpose: int = 0
    period: int = DEFAULT_PAULA_PERIOD
    volume: int = PAULA_MAX_VOLUME
    volume_slide: int = 0
    vibrato_idx: int = 0
    vibrato_phase: int = 0
    portamento_step: int = 0
    arpeggio_offsets: tuple[int, int, int] = (0, 0, 0)
    arpeggio_phase: int = 0
    releasing: bool = False
    pcm8: NDArray[np.int8] | None = None
    phase: float = 0.0
    sample_rate: int = 0
    loop_start: int = 0
    loop_length: int = 0
    pan_left: float = 1.0
    pan_right: float = 1.0
    age: int = 0


def _clamp_period(period: int) -> int:
    return max(PAULA_MIN_PERIOD, min(PAULA_MAX_PERIOD, period))


def _clamp_volume(volume: int) -> int:
    return max(0, min(PAULA_MAX_VOLUME, volume))


def _signed_byte(value: int) -> int:
    return value - 256 if value >= 128 else value


def _pan_to_gains(pan: float) -> tuple[float, float]:
    clamped = max(-1.0, min(1.0, pan))
    left = min(1.0, 1.0 - clamped) * 0.5 + 0.5 * (1.0 - max(0.0, clamped))
    right = min(1.0, 1.0 + clamped) * 0.5 + 0.5 * (1.0 + min(0.0, clamped))
    return (max(0.0, min(1.0, left)), max(0.0, min(1.0, right)))


def _note_to_period(
    note: int,
    inst: SynthInstrumentRecord | None,
    tables: SynthSequenceTables | None,
) -> int:
    idx = max(0, min(59, note))
    if inst is not None and tables is not None and inst.pitch_offset >= tables.blob_base:
        rel = inst.pitch_offset - tables.blob_base + idx * 2
        if rel >= 0 and rel + 2 <= len(tables.blob):
            (period,) = struct.unpack_from(">H", tables.blob, rel)
            if period > 0:
                return _clamp_period(period)
    hz = DEFAULT_SAMPLE_RATE * (2.0 ** ((idx - 24) / 12.0))
    return _clamp_period(round(PAL_PAULA_CLOCK / max(1.0, hz)))


def _resolve_voice_stream(
    seq: SynthSequenceEntry,
    stream_offset: int,
    tables: SynthSequenceTables | None,
) -> bytes:
    if seq.s44_offset > 0 and stream_offset >= seq.s44_offset:
        rel = stream_offset - seq.s44_offset
        if rel < len(seq.stream):
            return seq.stream[rel:]
    if tables is not None and stream_offset >= tables.blob_base:
        rel = stream_offset - tables.blob_base
        if rel < len(tables.blob):
            return tables.blob[rel:]
    if 0 <= stream_offset < len(seq.stream):
        return seq.stream[stream_offset:]
    return b""


def _fallback_waveform() -> NDArray[np.int8]:
    angles = np.linspace(0.0, 2.0 * np.pi, 32, endpoint=False)
    return (np.sin(angles) * 96.0).astype(np.int8)


def _slice_internal_waveform(
    waveforms: bytes,
    offset: int,
    byte_len: int,
) -> NDArray[np.int8] | None:
    if not waveforms:
        return None
    start = max(0, min(offset, len(waveforms)))
    end = min(len(waveforms), start + byte_len) if byte_len > 0 else len(waveforms)
    if end <= start:
        return None
    return np.frombuffer(waveforms[start:end], dtype=np.int8).copy()


def _find_bank_pcm(
    offset: int,
    byte_len: int,
    banks: Sequence[SampleAsset],
) -> NDArray[np.int8] | None:
    if byte_len <= 0:
        return None
    for bank in banks:
        if offset + byte_len <= len(bank.pcm8):
            return bank.pcm8[offset : offset + byte_len]
    return None


def _extract_instrument_pcm(
    inst: SynthInstrumentRecord | None,
    tables: SynthSequenceTables | None,
    banks: Sequence[SampleAsset],
) -> tuple[NDArray[np.int8], int, int]:
    if inst is None:
        wave = _fallback_waveform()
        return wave, 0, len(wave)

    byte_len = max(0, inst.length_words * 2)
    pcm8: NDArray[np.int8] | None = None
    if tables is not None and inst.index < 9:
        pcm8 = _slice_internal_waveform(tables.waveforms, inst.sample_offset, byte_len)
    if pcm8 is None:
        pcm8 = _find_bank_pcm(inst.sample_offset, byte_len, banks)
    if pcm8 is None or len(pcm8) == 0:
        pcm8 = _fallback_waveform()

    if inst.loop_flag < 0:
        return pcm8, 0, 0
    loop_start = max(0, min(inst.loop_offset, len(pcm8) - 1))
    loop_length = max(1, len(pcm8) - loop_start)
    return pcm8, loop_start, loop_length


class PaulaSynthMixer:
    """Headless-safe 4-voice 50 Hz Paula synth & 8SVX sample mixer (ARCHITECTURE.md §6)."""

    def __init__(
        self,
        sample_rate: int = DEFAULT_MIX_RATE,
        synth_tables: SynthSequenceTables | None = None,
    ) -> None:
        if sample_rate <= 0:
            msg = f"Sample rate must be positive, got {sample_rate}"
            raise ValueError(msg)
        self.sample_rate = sample_rate
        self._synth_tables = synth_tables
        self._samples: dict[str, SampleAsset] = {}
        self._banks: list[SampleAsset] = []
        self._frame_remainder = 0
        self._age_counter = 0
        self._voices: tuple[VoiceState, ...] = tuple(
            VoiceState(
                channel=ch,
                pan_left=PAULA_CHANNEL_PAN[ch][0],
                pan_right=PAULA_CHANNEL_PAN[ch][1],
            )
            for ch in range(PAULA_CHANNEL_COUNT)
        )

    @property
    def voices(self) -> tuple[VoiceState, ...]:
        """Read-only view of the 4 Paula voice states."""
        return self._voices

    def set_synth_tables(self, synth_tables: SynthSequenceTables) -> None:
        """Attach runtime `S_44`/`S_45` synth tables decoded from `mog`."""
        self._synth_tables = synth_tables

    def register_sample_bank(self, bank: DecodedSampleBank) -> None:
        """Register a decoded `.a` sample bank by its filename stem and full name."""
        pcm8 = np.frombuffer(bank.samples_pcm8, dtype=np.int8).copy()
        asset = SampleAsset(name=bank.name, pcm8=pcm8, sample_rate=DEFAULT_SAMPLE_RATE)
        self._banks.append(asset)
        self._samples[bank.name.lower()] = asset

    def register_sample(
        self,
        name: str,
        pcm8: bytes | NDArray[np.int8],
        *,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        loop_start: int = 0,
        loop_length: int = 0,
    ) -> None:
        """Register a named mono 8-bit signed PCM sample for direct playback."""
        arr = (
            pcm8.astype(np.int8, copy=False)
            if isinstance(pcm8, np.ndarray)
            else np.frombuffer(pcm8, dtype=np.int8).copy()
        )
        self._samples[name.lower()] = SampleAsset(
            name=name,
            pcm8=arr,
            sample_rate=max(1, sample_rate),
            loop_start=max(0, loop_start),
            loop_length=max(0, loop_length),
        )

    def register_8svx(self, name: str, data: bytes | bytearray | memoryview) -> SampleAsset:
        """Decode an IFF `FORM....8SVX` payload and register it by `name`."""
        raw_pcm, rate = decode_iff_8svx(data)
        self.register_sample(name, raw_pcm, sample_rate=rate)
        return self._samples[name.lower()]

    def active_channels(self) -> tuple[int, ...]:
        """Return indices (`0..3`) of currently active Paula voices."""
        return tuple(v.channel for v in self._voices if v.active)

    def stop_channel(self, channel: int) -> None:
        """Silence a single Paula channel (`0..3`)."""
        voice = self._voices[channel % PAULA_CHANNEL_COUNT]
        voice.active = False
        voice.stream = b""
        voice.pcm8 = None

    def stop_all(self) -> None:
        """Silence all 4 Paula channels."""
        for voice in self._voices:
            self.stop_channel(voice.channel)

    def _allocate_channel(self, preferred: int | None) -> int:
        if preferred is not None:
            return preferred % PAULA_CHANNEL_COUNT
        for voice in self._voices:
            if not voice.active:
                return voice.channel
        oldest = min(self._voices, key=lambda v: v.age)
        return oldest.channel

    def play_sample(
        self,
        name: str,
        *,
        volume: int = PAULA_MAX_VOLUME,
        pan: float | None = None,
        channel: int | None = None,
        sample_rate: int | None = None,
        offset: int = 0,
        length: int | None = None,
    ) -> int:
        """Play a registered `.a` or 8SVX sample on an allocated Paula channel (`0..3`)."""
        key = name.lower()
        if key not in self._samples:
            msg = f"Unknown audio sample {name!r}"
            raise KeyError(msg)
        asset = self._samples[key]
        ch = self._allocate_channel(channel)
        voice = self._voices[ch]
        self._age_counter += 1

        pcm = asset.pcm8[max(0, offset) :]
        if length is not None:
            pcm = pcm[: max(0, length)]
        pan_l, pan_r = PAULA_CHANNEL_PAN[ch] if pan is None else _pan_to_gains(pan)

        voice.active = len(pcm) > 0
        voice.stream = b""
        voice.pc = 0
        voice.wait_ticks = 0
        voice.volume = _clamp_volume(volume)
        voice.volume_slide = 0
        voice.portamento_step = 0
        voice.releasing = False
        voice.pcm8 = pcm
        voice.phase = 0.0
        voice.sample_rate = max(1, sample_rate if sample_rate is not None else asset.sample_rate)
        voice.loop_start = asset.loop_start if offset == 0 and length is None else 0
        voice.loop_length = asset.loop_length if offset == 0 and length is None else 0
        voice.pan_left = pan_l
        voice.pan_right = pan_r
        voice.age = self._age_counter
        return ch

    def play_sfx(self, sound_id: int) -> bool:
        """Trigger a 4-voice synth sound effect from `S_44` (`sound_id` in `1..168`)."""
        tables = self._synth_tables
        if tables is None or sound_id <= 0 or sound_id > len(tables.sequences):
            return False
        seq = tables.sequences[sound_id - 1]
        if len(seq.stream) < 6:
            return False

        voice_count = (seq.stream[0] & 0x03) + 1
        pos = 1
        armed = False
        for _ in range(voice_count):
            if pos + 5 > len(seq.stream):
                break
            ch = seq.stream[pos] & 0x03
            (stream_ofs,) = struct.unpack_from(">I", seq.stream, pos + 1)
            pos += 5
            voice_stream = _resolve_voice_stream(seq, stream_ofs, tables)
            if not voice_stream:
                continue
            self._arm_synth_voice(ch, voice_stream)
            armed = True
        return armed

    def _arm_synth_voice(self, channel: int, stream: bytes) -> None:
        voice = self._voices[channel]
        self._age_counter += 1
        voice.active = True
        voice.stream = stream
        voice.pc = 0
        voice.wait_ticks = 0
        voice.instrument_idx = 0
        voice.note = 24
        voice.transpose = 0
        voice.period = DEFAULT_PAULA_PERIOD
        voice.volume = PAULA_MAX_VOLUME
        voice.volume_slide = 0
        voice.vibrato_idx = 0
        voice.vibrato_phase = 0
        voice.portamento_step = 0
        voice.arpeggio_offsets = (0, 0, 0)
        voice.arpeggio_phase = 0
        voice.releasing = False
        voice.sample_rate = 0
        voice.phase = 0.0
        voice.pan_left, voice.pan_right = PAULA_CHANNEL_PAN[channel]
        voice.age = self._age_counter
        self._load_voice_instrument(voice)

    def _load_voice_instrument(self, voice: VoiceState) -> None:
        tables = self._synth_tables
        inst: SynthInstrumentRecord | None = None
        if tables is not None and 0 <= voice.instrument_idx < len(tables.instruments):
            inst = tables.instruments[voice.instrument_idx]
        pcm8, loop_start, loop_length = _extract_instrument_pcm(inst, tables, self._banks)
        voice.pcm8 = pcm8
        voice.loop_start = loop_start
        voice.loop_length = loop_length

    def handle_fight_sounds(self, sounds: Sequence[int]) -> list[int]:
        """Trigger sound IDs emitted by persistent fight jobs (ARCHITECTURE.md §10.17)."""
        triggered: list[int] = []
        for sound_id in sounds:
            if self.play_sfx(sound_id):
                triggered.append(sound_id)
        return triggered

    def _advance_voice_stream(self, voice: VoiceState) -> None:
        if not voice.stream:
            return
        if voice.wait_ticks > 0:
            voice.wait_ticks -= 1
        ops = 0
        while voice.active and voice.wait_ticks == 0 and ops < _MAX_OPS_PER_TICK:
            if voice.pc >= len(voice.stream):
                voice.active = False
                return
            opcode = voice.stream[voice.pc]
            ops += 1
            if opcode <= _NOTE_OP_MAX:
                self._exec_note_op(voice, opcode)
            else:
                self._exec_control_op(voice, opcode)

    def _exec_note_op(self, voice: VoiceState, note_byte: int) -> None:
        duration = voice.stream[voice.pc + 1] if voice.pc + 1 < len(voice.stream) else 1
        voice.pc += 2
        voice.wait_ticks = max(1, duration)
        if note_byte == 0:
            voice.volume = 0
            return
        voice.note = note_byte + voice.transpose
        inst = self._current_instrument(voice)
        voice.period = _note_to_period(voice.note, inst, self._synth_tables)
        voice.phase = 0.0
        voice.releasing = False

    def _exec_param_op(self, voice: VoiceState, opcode: int, arg: int) -> bool:
        if opcode == _OP_SET_INSTRUMENT:
            voice.instrument_idx = arg
            self._load_voice_instrument(voice)
        elif opcode == _OP_SET_VIBRATO:
            voice.vibrato_idx = arg
            voice.vibrato_phase = 0
        elif opcode == _OP_SET_PORTAMENTO:
            voice.portamento_step = _signed_byte(arg)
        elif opcode == _OP_SET_VOL_SLIDE:
            voice.volume_slide = _signed_byte(arg)
        elif opcode == _OP_SET_VOLUME:
            voice.volume = _clamp_volume(arg)
        elif opcode == _OP_SET_TRANSPOSE:
            voice.transpose = _signed_byte(arg)
        else:
            return False
        voice.pc += 2
        return True

    def _exec_control_op(self, voice: VoiceState, opcode: int) -> None:
        arg = voice.stream[voice.pc + 1] if voice.pc + 1 < len(voice.stream) else 0
        if self._exec_param_op(voice, opcode, arg):
            return
        if opcode == _OP_END:
            voice.active = False
            voice.pc += 1
        elif opcode == _OP_SET_ARPEGGIO:
            arg2 = voice.stream[voice.pc + 2] if voice.pc + 2 < len(voice.stream) else 0
            voice.arpeggio_offsets = (0, arg, arg2)
            voice.arpeggio_phase = 0
            voice.pc += 3
        elif opcode == _OP_RELEASE:
            voice.releasing = True
            voice.pc += 1
        else:
            voice.pc += 1

    def _current_instrument(self, voice: VoiceState) -> SynthInstrumentRecord | None:
        tables = self._synth_tables
        if tables is not None and 0 <= voice.instrument_idx < len(tables.instruments):
            return tables.instruments[voice.instrument_idx]
        return None

    def _modulate_voice(self, voice: VoiceState) -> int:
        if voice.stream:
            voice.volume = _clamp_volume(voice.volume + voice.volume_slide)
            if voice.releasing:
                voice.volume = max(0, voice.volume - 4)
                if voice.volume == 0:
                    voice.active = False
            if voice.portamento_step != 0:
                voice.period = _clamp_period(voice.period + voice.portamento_step)

        eff_period = voice.period
        if voice.arpeggio_offsets != (0, 0, 0):
            arp_note = voice.note + voice.arpeggio_offsets[voice.arpeggio_phase % 3]
            voice.arpeggio_phase += 1
            eff_period = _note_to_period(
                arp_note, self._current_instrument(voice), self._synth_tables
            )
        if voice.vibrato_idx > 0:
            delta = 4 if (voice.vibrato_phase & 2) == 0 else -4
            voice.vibrato_phase += 1
            eff_period = _clamp_period(eff_period + delta)
        return eff_period

    def _render_voice(
        self,
        voice: VoiceState,
        num_frames: int,
        eff_period: int,
        mix: NDArray[np.float32],
    ) -> None:
        if not voice.active or voice.pcm8 is None or len(voice.pcm8) == 0 or voice.volume <= 0:
            return

        pcm = voice.pcm8
        pcm_len = len(pcm)
        hz = voice.sample_rate if voice.sample_rate > 0 else paula_period_to_hz(eff_period)
        step = hz / float(self.sample_rate)
        indices = voice.phase + np.arange(num_frames, dtype=np.float64) * step

        if voice.loop_length > 0:
            loop_end = voice.loop_start + voice.loop_length
            wrapped = np.where(
                indices >= voice.loop_start,
                voice.loop_start + ((indices - voice.loop_start) % voice.loop_length),
                indices,
            )
            idx_int = wrapped.astype(np.int64) % pcm_len
            samples = pcm[idx_int].astype(np.float32)
            next_phase = float(indices[-1] + step)
            if next_phase >= loop_end:
                next_phase = voice.loop_start + (
                    (next_phase - voice.loop_start) % voice.loop_length
                )
            voice.phase = next_phase
        else:
            valid = indices < pcm_len
            idx_int = np.minimum(indices.astype(np.int64), pcm_len - 1)
            samples = np.where(valid, pcm[idx_int].astype(np.float32), 0.0)
            next_phase = float(indices[-1] + step)
            voice.phase = next_phase
            if next_phase >= pcm_len:
                voice.active = False

        gain = (voice.volume / float(PAULA_MAX_VOLUME)) * 256.0 * 0.5
        scaled = samples * gain
        mix[:, 0] += scaled * voice.pan_left
        mix[:, 1] += scaled * voice.pan_right

    def _next_vbl_frame_count(self) -> int:
        total = self.sample_rate + self._frame_remainder
        frames = total // PAL_VBL_HZ
        self._frame_remainder = total % PAL_VBL_HZ
        return frames

    def step_vbl(self) -> NDArray[np.int16]:
        """Advance the 4-voice mixer by 1 PAL VBL (20 ms) and return stereo int16 PCM."""
        num_frames = self._next_vbl_frame_count()
        mix = np.zeros((num_frames, 2), dtype=np.float32)
        for voice in self._voices:
            self._advance_voice_stream(voice)
            eff_period = self._modulate_voice(voice)
            self._render_voice(voice, num_frames, eff_period, mix)
        np.clip(mix, -32768.0, 32767.0, out=mix)
        return mix.astype(np.int16)

    def render_ticks(self, tick_count: int) -> NDArray[np.int16]:
        """Render `tick_count` consecutive 50 Hz VBL ticks into a single `(N, 2)` int16 buffer."""
        if tick_count <= 0:
            return np.zeros((0, 2), dtype=np.int16)
        chunks = [self.step_vbl() for _ in range(tick_count)]
        return np.concatenate(chunks, axis=0)


class AudioEngine:
    """High-level audio coordinator wrapping `PaulaSynthMixer` and `pygame.mixer`."""

    def __init__(
        self,
        sample_rate: int = DEFAULT_MIX_RATE,
        synth_tables: SynthSequenceTables | None = None,
        *,
        headless: bool = False,
    ) -> None:
        self._mixer = PaulaSynthMixer(sample_rate=sample_rate, synth_tables=synth_tables)
        self._headless = headless
        self._mixer_initialized = False
        self._output_channel: pygame.mixer.Channel | None = None
        self._current_music_title: str | None = None
        self._is_music_playing = False

        if not headless:
            self._init_pygame_mixer(sample_rate)

    def _init_pygame_mixer(self, sample_rate: int) -> None:
        try:
            if pygame.mixer.get_init() is None:
                pygame.mixer.init(frequency=sample_rate, size=-16, channels=2, buffer=1024)
            self._output_channel = pygame.mixer.Channel(0)
            self._mixer_initialized = True
        except pygame.error:
            self._headless = True
            self._mixer_initialized = False
            self._output_channel = None

    @property
    def mixer(self) -> PaulaSynthMixer:
        """Underlying deterministic 4-voice Paula synthesizer and sample mixer."""
        return self._mixer

    @property
    def headless(self) -> bool:
        """Whether the engine is running without an active `pygame.mixer` device."""
        return self._headless

    @property
    def mixer_initialized(self) -> bool:
        """Whether `pygame.mixer` was successfully initialized."""
        return self._mixer_initialized

    @property
    def current_music_title(self) -> str | None:
        """Title of the currently loaded `.cmp` ProTracker module, if any."""
        return self._current_music_title

    @property
    def is_music_playing(self) -> bool:
        """Whether ProTracker module playback is active."""
        return self._is_music_playing

    def play_sfx(self, sound_id: int) -> bool:
        """Trigger a 4-voice synth sound effect (`S_44`) by `sound_id`."""
        return self._mixer.play_sfx(sound_id)

    def play_sample(
        self,
        name: str,
        *,
        volume: int = PAULA_MAX_VOLUME,
        pan: float | None = None,
        channel: int | None = None,
        sample_rate: int | None = None,
        offset: int = 0,
        length: int | None = None,
    ) -> int:
        """Trigger a registered `.a` or 8SVX sample and return the allocated channel."""
        return self._mixer.play_sample(
            name,
            volume=volume,
            pan=pan,
            channel=channel,
            sample_rate=sample_rate,
            offset=offset,
            length=length,
        )

    def handle_fight_sounds(self, sounds: Sequence[int]) -> list[int]:
        """Dispatch the supplied engine and handler sound IDs in order."""
        return self._mixer.handle_fight_sounds(sounds)

    def step_vbl(self) -> NDArray[np.int16]:
        """Advance 1 PAL VBL tick, returning stereo int16 PCM and queuing audio if active."""
        pcm = self._mixer.step_vbl()
        if self._mixer_initialized and self._output_channel is not None and len(pcm) > 0:
            try:
                sound = pygame.sndarray.make_sound(pcm)
                if not self._output_channel.get_busy():
                    self._output_channel.play(sound)
                else:
                    self._output_channel.queue(sound)
            except pygame.error:
                self._mixer_initialized = False
                self._headless = True
        return pcm

    def play_music(self, module: DecodedModule, *, loops: int = -1) -> bool:
        """Load and play a decompressed `.cmp` ProTracker module via `pygame.mixer.music`."""
        self._current_music_title = module.title
        if not self._mixer_initialized:
            self._is_music_playing = False
            return False
        try:
            pygame.mixer.music.load(io.BytesIO(module.raw_mod))
            pygame.mixer.music.play(loops=loops)
        except pygame.error:
            self._is_music_playing = False
            return False
        self._is_music_playing = True
        return True

    def stop_music(self) -> None:
        """Stop ProTracker module playback if active."""
        self._is_music_playing = False
        if self._mixer_initialized:
            try:
                pygame.mixer.music.stop()
            except pygame.error:
                return

    def close(self) -> None:
        """Stop all voices and shut down `pygame.mixer` if initialized."""
        self._mixer.stop_all()
        self.stop_music()
        if self._mixer_initialized:
            try:
                pygame.mixer.quit()
            except pygame.error:
                self._mixer_initialized = False
                self._output_channel = None
                return
            self._mixer_initialized = False
            self._output_channel = None
