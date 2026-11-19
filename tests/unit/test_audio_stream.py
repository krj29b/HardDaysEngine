"""Host PCM ring and callback-driven audio output (§6.2.9, ADR-051)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pytest

from harddaysengine.app import audio as audio_module
from harddaysengine.app.audio import DEVICE_CHUNK, AudioEngine
from harddaysengine.app.audio_stream import PcmRing

if TYPE_CHECKING:
    from collections.abc import Callable

    from numpy.typing import NDArray

RATE = 22050
VBL = RATE // 50


def _vbl(value: int, frames: int = VBL) -> NDArray[np.int16]:
    return np.full((frames, 2), value, dtype=np.int16)


def test_ring_stays_silent_until_prebuffered_then_preserves_order() -> None:
    ring = PcmRing(prebuffer=4, limit=10)
    ring.write(_vbl(1, 2))
    assert not ring.read(2).any() and ring.buffered == 2
    ring.write(np.array([[2, 2], [3, 3]], dtype=np.int16))
    assert ring.read(3)[:, 0].tolist() == [1, 1, 2]
    assert ring.read(1)[:, 0].tolist() == [3] and ring.underruns == 0


def test_ring_underrun_pads_silence_and_primes_again() -> None:
    ring = PcmRing(prebuffer=2, limit=8)
    ring.write(_vbl(5, 3))
    assert ring.read(4)[:, 0].tolist() == [5, 5, 5, 0] and ring.underruns == 1
    ring.write(_vbl(6, 1))
    assert not ring.read(1).any() and ring.buffered == 1


def test_ring_overflow_keeps_the_newest_frames_in_the_middle_of_the_window() -> None:
    ring = PcmRing(prebuffer=2, limit=6)
    ring.write(np.arange(14, dtype=np.int16).reshape(7, 2))
    assert ring.buffered == 4 and ring.dropped == 3
    assert ring.read(4)[:, 0].tolist() == [6, 8, 10, 12]


@pytest.mark.parametrize(("prebuffer", "limit"), [(0, 4), (4, 4), (5, 4)])
def test_ring_rejects_an_empty_window(prebuffer: int, limit: int) -> None:
    with pytest.raises(ValueError, match="prebuffer"):
        PcmRing(prebuffer, limit)


def test_display_rate_bursts_play_gaplessly() -> None:
    """VBL bursts of 0/1/2 per 60 Hz frame against a steady device pull never underrun."""
    ring = PcmRing.for_device(RATE, DEVICE_CHUNK)
    produced = consumed = 0
    accumulator = 0
    for frame in range(60 * 20):
        accumulator += 50
        while accumulator >= 60:
            accumulator -= 60
            ring.write(_vbl(1))
            produced += VBL
        target = (frame + 1) * RATE // 60
        while consumed + DEVICE_CHUNK <= target:
            block = ring.read(DEVICE_CHUNK)
            consumed += DEVICE_CHUNK
            assert produced < ring.prebuffer or block.all()
    assert ring.underruns == 0 and ring.dropped == 0


class _FakeDevice:
    def __init__(self, **kwargs: object) -> None:
        callback = kwargs["callback"]
        assert callable(callback) and kwargs["allowed_changes"] == 0
        self.callback: Callable[[object, memoryview], None] = callback
        self.frequency = kwargs["frequency"]
        self.closed = False
        self.paused = 1

    def pause(self, value: int) -> None:
        self.paused = value

    def close(self) -> None:
        self.closed = True


def test_engine_streams_vbls_through_the_device_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    devices: list[_FakeDevice] = []

    def make(**kwargs: object) -> _FakeDevice:
        devices.append(_FakeDevice(**kwargs))
        return devices[-1]

    monkeypatch.setattr(audio_module.sdl_audio, "AudioDevice", make)
    monkeypatch.setattr(audio_module.pygame.mixer, "get_init", lambda: (RATE, -16, 2))
    monkeypatch.setattr(audio_module.pygame.mixer, "quit", lambda: None)
    engine = AudioEngine(RATE)
    device = devices[0]
    assert engine.mixer_initialized and device.paused == 0 and device.frequency == RATE
    rendered = [engine.step_vbl() for _ in range(8)]
    stream = memoryview(bytearray(DEVICE_CHUNK * 4))
    device.callback(device, stream)
    expected = np.concatenate(rendered)[:DEVICE_CHUNK].tobytes()
    assert bytes(stream) == expected
    engine.close()
    assert device.closed and not engine.mixer_initialized
