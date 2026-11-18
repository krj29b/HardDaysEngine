"""The Paula model against closed-form expectations (ARCHITECTURE.md §6.2.9)."""

from __future__ import annotations

import numpy as np
import pytest

from harddaysengine.app.paula import PAL_CLOCK, SET, Paula, SampleMemory, address

RATE = 22050
FRAME = RATE // 50
BYTES = bytes((10, 20, 30, 40, 50, 60, 70, 80))


def _paula(data: bytes = BYTES) -> Paula:
    memory = SampleMemory()
    memory.load(1, data)
    return Paula(memory, RATE)


def _play(paula: Paula, channel: int, period: int, *, words: int = 4, volume: int = 64) -> None:
    paula.location(channel, address(1, 0))
    paula.length(channel, words)
    paula.period(channel, period)
    paula.volume(channel, volume)
    paula.dmacon(SET | (1 << channel))
    paula.commit()


def _expected(
    data: bytes, period: int, samples: int, *, volume: int = 64, first: int = 0
) -> list[int]:
    """Byte ``(k * clock) // (period * rate)`` of a whole-buffer loop, scaled like the mixer."""
    k = np.arange(first, first + samples, dtype=np.int64)
    index = (k * PAL_CLOCK) // (period * RATE) % len(data)
    return [int(v) for v in np.frombuffer(data, dtype=np.int8)[index].astype(np.int32) * 64 * 2]


def test_dma_edge_latches_the_buffer_raises_its_request_and_loops_without_interrupts() -> None:
    paula = _paula()
    _play(paula, 0, 400)
    assert paula.requests == 0x80
    pcm = paula.render_frame()
    assert pcm.shape == (FRAME, 2)
    assert pcm[:, 0].tolist() == _expected(BYTES, 400, FRAME)
    assert not pcm[:, 1].any()


def test_a_loop_continues_across_frames() -> None:
    paula = _paula()
    _play(paula, 0, 300)
    first = paula.render_frame()[:, 0].tolist()
    second = paula.render_frame()[:, 0].tolist()
    assert first + second == _expected(BYTES, 300, 2 * FRAME)


def test_off_then_on_restarts_and_on_then_off_does_nothing() -> None:
    paula = _paula()
    _play(paula, 0, 3000)
    paula.render_frame()
    paula.requests = 0
    paula.dmacon(1)
    paula.dmacon(SET | 1)
    paula.commit()
    assert paula.requests == 0x80
    assert paula.render_frame()[:, 0].tolist() == _expected(BYTES, 3000, FRAME)
    paula.requests = 0
    paula.dmacon(SET | 2)
    paula.dmacon(2)
    paula.commit()
    assert paula.requests == 0
    assert not paula.render_frame()[:, 1].any()


def test_reloads_call_the_handler_and_new_registers_start_with_the_next_buffer() -> None:
    data = BYTES + bytes((1, 2, 3, 4, 5, 6, 7, 8))
    paula = _paula(data)
    calls: list[int] = []

    def handler() -> None:
        calls.append(paula.requests)
        paula.intreq(0x80)
        paula.location(0, address(1, 8))
        paula.intena(0x80)

    paula.handler = handler
    paula.intena(SET | 0x80)
    _play(paula, 0, 200)
    assert calls == [0x80]
    pcm = paula.render_frame()[:, 0].tolist()
    span = 8 * 200 * RATE
    switch = -(-span // PAL_CLOCK)
    assert pcm[:switch] == _expected(BYTES, 200, switch)
    second = data[8:]
    assert pcm[switch:] == _expected(second, 200, FRAME - switch, first=switch)[: FRAME - switch]


def test_dma_off_from_the_handler_ends_the_output_at_the_reload() -> None:
    paula = _paula()

    def handler() -> None:
        paula.intreq(0x80)
        if paula.requests == 0 and calls:
            paula.dmacon(1)
        calls.append(1)

    calls: list[int] = []
    paula.handler = handler
    paula.intena(SET | 0x80)
    _play(paula, 0, 200)
    pcm = paula.render_frame()[:, 0]
    switch = -(-(8 * 200 * RATE) // PAL_CLOCK)
    assert pcm[:switch].any()
    assert not pcm[switch:].any()


def test_a_period_change_keeps_the_playing_position() -> None:
    paula = _paula()
    _play(paula, 0, 20000)
    last = paula.render_frame()[-1, 0]
    paula.period(0, 30000)
    assert paula.render_frame()[0, 0] == last


def test_volume_is_capped_unloaded_memory_is_silent_and_channels_mix_in_stereo() -> None:
    paula = _paula()
    _play(paula, 3, 400, volume=100)
    _play(paula, 1, 400)
    paula.location(2, address(5, 0))
    paula.length(2, 4)
    paula.period(2, 400)
    paula.volume(2, 64)
    paula.dmacon(SET | 4)
    paula.commit()
    pcm = paula.render_frame()
    assert pcm[:, 0].tolist() == _expected(BYTES, 400, FRAME)
    assert pcm[:, 1].tolist() == _expected(BYTES, 400, FRAME)


@pytest.mark.parametrize("rate", [22050, 22051, 44100])
def test_frames_add_up_to_the_rate(rate: int) -> None:
    paula = Paula(SampleMemory(), rate)
    assert sum(len(paula.render_frame()) for _ in range(50)) == rate


def test_rejects_a_bad_rate() -> None:
    with pytest.raises(ValueError, match="sample rate"):
        Paula(SampleMemory(), 0)


def test_a_write_to_a_looping_channel_takes_effect_at_its_next_repetition() -> None:
    data = BYTES + bytes((1, 2, 3, 4, 5, 6, 7, 8))
    paula = _paula(data)
    calls: list[int] = []

    def handler() -> None:
        paula.intreq(0x100)
        if calls:
            paula.location(0, address(1, 8))
            paula.intena(0x100)
        calls.append(1)

    paula.handler = handler
    _play(paula, 0, 200)
    paula.intena(SET | 0x100)
    _play(paula, 1, 1100)
    pcm = paula.render_frame()[:, 0].tolist()
    switch = -(-(6 * 8 * 200 * RATE) // PAL_CLOCK)
    assert len(calls) == 2
    assert pcm[:switch] == _expected(BYTES, 200, switch)
    assert pcm[switch:] == _expected(data[8:], 200, FRAME - switch, first=switch)
