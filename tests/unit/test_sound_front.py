"""The hunk-16 front end and the audio engine's tick order (ARCHITECTURE.md §6.2.1, §6.2.9)."""

from __future__ import annotations

import pytest

from harddaysengine.app.audio import AudioEngine
from harddaysengine.app.sound_front import SoundFront
from harddaysengine.assets.synth_data import FrontEndData
from harddaysengine.sim.sound import SoundKind, SoundRequest
from tests.unit.synth_setup import STREAMS, synth_data

FRONT = FrontEndData(
    busy=0,
    cursor=0,
    full=0x0F,
    cursor_mask=3,
    silence=0xA7,
    fixed_bits=(1, 2, 4, 8),
    release_masks=(0x0E, 0x0D, 0x0B, 0x07),
)


def _front(**cells: int) -> tuple[SoundFront, list[tuple[int, int]]]:
    starts: list[tuple[int, int]] = []
    front = SoundFront(FRONT, lambda sequence, channel: starts.append((sequence, channel)))
    for name, value in cells.items():
        setattr(front, name, value)
    return front, starts


def test_play_steps_the_cursor_over_free_channels_without_marking_them() -> None:
    front, starts = _front(busy=0b0101)
    assert [front.play(sequence) for sequence in (10, 11, 12)] == [1, 3, 1]
    assert starts == [(10, 1), (11, 3), (12, 1)]
    assert front.busy == 0b0101


def test_play_does_nothing_when_every_channel_is_busy() -> None:
    front, starts = _front(busy=0x1F, cursor=2)
    assert front.play(10) is None
    assert (starts, front.cursor) == ([], 2)


def test_fixed_marks_its_channel_and_release_plays_the_silence_on_the_next_free_one() -> None:
    front, starts = _front(cursor=3)
    front.fixed(20, 2)
    assert front.busy == 4
    front.busy |= 0xF0
    front.release(0x0B, 0xA7)
    assert front.busy == 0
    assert starts == [(20, 2), (0xA7, 0)]


def test_stop_all_clears_the_mask_and_silences_every_channel() -> None:
    front, starts = _front(busy=0x0F, cursor=1)
    front.stop_all()
    assert front.busy == 0
    assert sorted(channel for _, channel in starts) == [0, 1, 2, 3]
    assert {sequence for sequence, _ in starts} == {0xA7}


def test_requests_dispatch_by_kind_and_load_restores_the_file_cells() -> None:
    front, starts = _front(cursor=1)
    for request in (
        SoundRequest(SoundKind.PLAY, 5),
        SoundRequest(SoundKind.FIXED, 6, 3),
        SoundRequest(SoundKind.RELEASE, 0xA7, 3, 0x07),
        SoundRequest(SoundKind.START, 8, 1),
    ):
        front.request(request)
    assert starts == [(5, 2), (6, 3), (0xA7, 3), (8, 1)]
    assert front.busy == 0
    front.busy, front.cursor = 3, 2
    front.clear_busy()
    assert (front.busy, front.cursor) == (0, 2)
    front.load()
    assert (front.busy, front.cursor) == (0, 0)


def test_engine_runs_the_tick_before_the_requests_of_the_frame() -> None:
    engine = AudioEngine(headless=True)
    data = synth_data({1: bytes((0x94, 10, 0x8C, 4, 0x80, 40, 0xD0, 0, 5))})
    engine.attach_synth(data, FRONT)
    engine.handle_requests([SoundRequest(SoundKind.PLAY, 1)])
    synth = engine.synth
    assert synth is not None
    assert synth.voices[1].position == 0
    engine.step_vbl()
    assert synth.voices[1].position == STREAMS
    engine.step_vbl()
    assert synth.voices[1].note == 5
    assert engine.paula.requests & 0x100


def test_engine_queues_stop_all_and_passes_fade_steps_through() -> None:
    engine = AudioEngine(headless=True)
    engine.attach_synth(synth_data({}), FRONT)
    front, synth = engine.front, engine.synth
    assert front is not None and synth is not None
    front.busy = 0x0F
    engine.stop_all()
    assert front.busy == 0x0F
    engine.step_vbl()
    assert front.busy == 0
    engine.fade_step(True)
    assert {voice.fade for voice in synth.voices} == {16}


def test_engine_without_synth_data_renders_silence() -> None:
    engine = AudioEngine(headless=True)
    engine.handle_requests([SoundRequest(SoundKind.PLAY, 1)])
    engine.stop_all()
    engine.fade_step(True)
    pcm = engine.step_vbl()
    assert pcm.shape == (441, 2) and not pcm.any()


def test_engine_start_without_synth_is_an_error() -> None:
    engine = AudioEngine(headless=True)
    with pytest.raises(ValueError, match="without synth data"):
        engine._start(1, 0)
