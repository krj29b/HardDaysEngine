"""Unit tests for the headless Paula 4-voice VBL synth & 8SVX mixer (`app/audio.py`)."""

from __future__ import annotations

import struct

import numpy as np
import pygame
import pytest

from pymoonstone.app.audio import (
    DEFAULT_MIX_RATE,
    PAL_VBL_HZ,
    PAULA_CHANNEL_COUNT,
    AudioEngine,
    PaulaSynthMixer,
)
from pymoonstone.assets.audio import DecodedModule, DecodedSampleBank, ModSampleHeader
from pymoonstone.assets.hunk import SynthInstrumentRecord
from pymoonstone.assets.tables import (
    MOG_SYNTH_BLOB_BASE,
    SynthSequenceEntry,
    SynthSequenceTables,
)
from pymoonstone.sim.combat_vm import ScriptEndedEvent, SoundEvent


def _build_synthetic_8svx(pcm8: bytes, sample_rate: int = 11025) -> bytes:
    """Build a minimal valid IFF `FORM....8SVX` container with VHDR and BODY chunks."""
    vhdr_payload = struct.pack(">IIIHHBB", len(pcm8), 0, 0, sample_rate, 1, 0, 0)
    # Pad VHDR to 20 bytes (standard 8SVX VHDR size)
    vhdr_payload = vhdr_payload.ljust(20, b"\x00")
    vhdr = b"VHDR" + struct.pack(">I", len(vhdr_payload)) + vhdr_payload
    body_pad = b"\x00" if len(pcm8) & 1 else b""
    body = b"BODY" + struct.pack(">I", len(pcm8)) + pcm8 + body_pad
    form_size = 4 + len(vhdr) + len(body)
    return b"FORM" + struct.pack(">I", form_size) + b"8SVX" + vhdr + body


def _build_synthetic_synth_tables() -> SynthSequenceTables:
    """Construct synthetic `SynthSequenceTables` (`S_44`/`S_45`) for headless VBL tests."""
    # 32-byte square-ish single-cycle waveform in S_45 (internal instrument 0)
    wave0 = bytes([64] * 16 + [192] * 16)  # +64 and -64 in signed int8
    # 32-byte triangle-ish waveform for instrument 1
    wave1 = bytes([((i * 8) & 0xFF) for i in range(32)])
    waveforms = (wave0 + wave1).ljust(170, b"\x00")

    # 60-entry big-endian period table placed inside blob at MOG_SYNTH_BLOB_BASE
    periods = b"".join(struct.pack(">H", max(124, 856 - i * 10)) for i in range(60))
    blob = periods.ljust(256, b"\x00")

    inst0 = SynthInstrumentRecord(
        index=0,
        loop_flag=0,
        loop_offset=0,
        length_words=16,
        sample_offset=0,
        pitch_offset=MOG_SYNTH_BLOB_BASE,
    )
    inst1 = SynthInstrumentRecord(
        index=1,
        loop_flag=0,
        loop_offset=0,
        length_words=16,
        sample_offset=32,
        pitch_offset=MOG_SYNTH_BLOB_BASE,
    )

    # Sequence 1 (sound_id=1): 2 voices (ch 0 and ch 1)
    # Header: voice_count_minus_1 = 1 (2 voices), followed by two 5-byte descriptors
    # Voice 0 stream starts at offset 11, voice 1 stream starts at offset 28
    v0_stream = bytes(
        [
            0x81,
            0x00,  # select instrument 0
            0x85,
            0x40,  # volume = 64
            0x18,
            0x02,  # note 24 for 2 ticks
            0x8B,  # release envelope
            0x80,  # end
        ]
    )
    v1_stream = bytes(
        [
            0x81,
            0x01,  # select instrument 1
            0x85,
            0x30,  # volume = 48
            0x86,
            0x04,
            0x07,  # arpeggio (0, 4, 7)
            0x82,
            0x01,  # vibrato 1
            0x83,
            0xFE,  # portamento -2
            0x84,
            0xFC,  # volume slide -4
            0x88,
            0x02,  # transpose +2
            0x1E,
            0x03,  # note 30 for 3 ticks
            0x00,
            0x01,  # rest 1 tick
            0x80,  # end
        ]
    )
    v0_ofs = 1 + 2 * 5
    v1_ofs = v0_ofs + len(v0_stream)
    seq1_bytes = bytes([1, 0]) + struct.pack(">I", v0_ofs) + bytes([1]) + struct.pack(">I", v1_ofs)
    seq1_bytes += v0_stream + v1_stream

    seq_empty = SynthSequenceEntry(index=0, s44_offset=0, stream=b"")
    seq_one = SynthSequenceEntry(index=1, s44_offset=0, stream=seq1_bytes)
    # sound_id=1 indexes sequences[0], sound_id=2 indexes sequences[1]
    return SynthSequenceTables(
        blob_base=MOG_SYNTH_BLOB_BASE,
        blob=blob,
        vibrato_rows=(b"\x01\x02\x03" * 5,),
        envelope_rows=(b"\x40\x30\x20\x10\x00\x00\x00\x00",),
        sequences=(seq_one, seq_empty),
        waveforms=waveforms,
        instruments=(inst0, inst1),
    )


def test_vbl_frame_counts_and_silence() -> None:
    mixer = PaulaSynthMixer(sample_rate=DEFAULT_MIX_RATE)
    buf = mixer.step_vbl()
    assert buf.shape == (DEFAULT_MIX_RATE // PAL_VBL_HZ, 2)
    assert buf.dtype == np.int16
    assert np.all(buf == 0)

    mixer_44k = PaulaSynthMixer(sample_rate=44100)
    buf_44k = mixer_44k.render_ticks(3)
    assert buf_44k.shape == (882 * 3, 2)
    assert mixer_44k.render_ticks(0).shape == (0, 2)

    with pytest.raises(ValueError, match="positive"):
        PaulaSynthMixer(sample_rate=0)


def test_synth_4voice_vbl_playback_and_envelopes() -> None:
    tables = _build_synthetic_synth_tables()
    mixer = PaulaSynthMixer(sample_rate=DEFAULT_MIX_RATE, synth_tables=tables)

    assert not mixer.play_sfx(0)
    assert not mixer.play_sfx(999)
    assert mixer.play_sfx(1)
    assert mixer.active_channels() == (0, 1)

    tick1 = mixer.step_vbl()
    assert tick1.shape == (441, 2)
    # Channel 0 is panned left, channel 1 is panned right -> both channels have energy
    assert np.max(np.abs(tick1[:, 0])) > 0
    assert np.max(np.abs(tick1[:, 1])) > 0

    # Advance enough VBLs for both voices to finish their sequences
    _ = mixer.render_ticks(10)
    assert mixer.active_channels() == ()


def test_sample_bank_and_8svx_mixing_and_channel_allocation() -> None:
    mixer = PaulaSynthMixer(sample_rate=DEFAULT_MIX_RATE)

    # Register a `.a` sample bank with constant +80 PCM8
    bank = DecodedSampleBank(name="kn.a", packed_size=64, samples_pcm8=bytes([80] * 128))
    mixer.register_sample_bank(bank)

    # Register an IFF 8SVX sample with -60 PCM8
    svx_bytes = _build_synthetic_8svx(bytes([196] * 128), sample_rate=8287)
    asset = mixer.register_8svx("clang", svx_bytes)
    assert asset.sample_rate == 8287
    assert len(asset.pcm8) == 128

    # Allocate 4 channels automatically (0, 1, 2, 3)
    ch0 = mixer.play_sample("kn.a", volume=64, pan=-1.0)
    ch1 = mixer.play_sample("clang", volume=32, pan=1.0)
    ch2 = mixer.play_sample("kn.a", volume=16, pan=0.0, offset=10, length=40)
    ch3 = mixer.play_sample("clang", volume=16, pan=0.0)
    assert (ch0, ch1, ch2, ch3) == (0, 1, 2, 3)
    assert len(mixer.active_channels()) == PAULA_CHANNEL_COUNT

    # 5th trigger steals oldest channel (0)
    ch_stolen = mixer.play_sample("kn.a", volume=64, pan=-1.0)
    assert ch_stolen == 0

    mixed = mixer.step_vbl()
    assert mixed.shape == (441, 2)
    assert np.max(np.abs(mixed[:, 0])) > 0
    assert np.max(np.abs(mixed[:, 1])) > 0

    # One-shot samples finish after enough ticks
    _ = mixer.render_ticks(5)
    assert mixer.active_channels() == ()

    with pytest.raises(KeyError, match="Unknown audio sample"):
        mixer.play_sample("missing_sfx")


def test_looping_sample_persists_and_stop_channel_works() -> None:
    mixer = PaulaSynthMixer(sample_rate=DEFAULT_MIX_RATE)
    loop_pcm = np.array([40, -40] * 32, dtype=np.int8)
    mixer.register_sample("wind", loop_pcm, sample_rate=8287, loop_start=0, loop_length=64)

    ch = mixer.play_sample("wind", channel=2, pan=0.0)
    assert ch == 2
    pcm = mixer.render_ticks(10)
    assert np.max(np.abs(pcm)) > 0
    assert mixer.active_channels() == (2,)

    mixer.stop_channel(2)
    assert mixer.active_channels() == ()


def test_combat_event_sound_dispatch() -> None:
    tables = _build_synthetic_synth_tables()
    engine = AudioEngine(sample_rate=DEFAULT_MIX_RATE, synth_tables=tables, headless=True)
    events = (
        ScriptEndedEvent(job_id=0, pc=10),
        SoundEvent(job_id=0, sound_id=1),
        SoundEvent(job_id=1, sound_id=2),  # empty stream -> not triggered
    )
    triggered = engine.handle_combat_events(events)
    assert triggered == [1]
    assert engine.mixer.active_channels() == (0, 1)
    pcm = engine.step_vbl()
    assert pcm.shape == (441, 2)
    engine.close()


def test_audio_engine_headless_fallback_and_cmp_music(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail_init(*_args: object, **_kwargs: object) -> None:
        raise pygame.error("No audio device in CI")

    monkeypatch.setattr(pygame.mixer, "get_init", lambda: None)
    monkeypatch.setattr(pygame.mixer, "init", _fail_init)

    engine = AudioEngine(sample_rate=DEFAULT_MIX_RATE, headless=False)
    assert engine.headless is True
    assert engine.mixer_initialized is False

    dummy_mod = DecodedModule(
        name="music.cmp",
        title="introx5",
        signature="M.K.",
        song_length=1,
        pattern_count=1,
        order_table=(0,),
        samples=(
            ModSampleHeader(
                index=1,
                name="s1",
                length_bytes=0,
                finetune=0,
                volume=64,
                repeat_start_bytes=0,
                repeat_length_bytes=0,
            ),
        ),
        raw_mod=b"M.K." * 300,
    )
    assert engine.play_music(dummy_mod) is False
    assert engine.current_music_title == "introx5"
    assert engine.is_music_playing is False
    engine.stop_music()
    engine.close()
