"""Paula audio model: DMA channels, buffer reloads and audio interrupts (ARCHITECTURE.md §6.2.9).

Times inside a frame are integers in units of one output sample times one Paula clock
(``clock * rate``), so output sample ``k`` of the frame lies at ``k * PAL_CLOCK`` and one byte at
period ``p`` lasts ``p * rate``. Register writes happen at the current time ``now``: 0 for a CPU
batch at the start of a frame, or the moment of a buffer reload while the interrupt routine runs.
Each channel's output is rendered with numpy up to the moment its registers change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Callable

    from numpy.typing import NDArray

PAL_CLOCK = 3_546_895
VBL_HZ = 50
CHANNELS = 4
MIN_PERIOD = 124
MAX_VOLUME = 64
REGION_SHIFT = 24
OFFSET_MASK = (1 << REGION_SHIFT) - 1
SET = 0x8000
_WORDS_WRAP = 0x10000
_VOLUME_BITS = 0x7F
_LEFT = (0, 3)
_RIGHT = (1, 2)
_OUTPUT_GAIN = 2


def address(region: int, offset: int) -> int:
    """The address of ``offset`` inside sample memory region ``region``."""
    return (region << REGION_SHIFT) | (offset & OFFSET_MASK)


def channel_dma(channel: int) -> int:
    """DMACON bit of ``channel``."""
    return 1 << channel


def channel_interrupt(channel: int) -> int:
    """INTENA / INTREQ bit of ``channel``."""
    return 0x80 << channel


class SampleMemory:
    """Separate regions of signed 8-bit sample memory; anything else reads silence (§6.2.9)."""

    def __init__(self) -> None:
        self._regions: dict[int, NDArray[np.int8]] = {}

    def load(self, region: int, data: bytes) -> None:
        """Place ``data`` at offset 0 of ``region``."""
        self._regions[region] = np.frombuffer(data, dtype=np.int8).copy()

    def clear(self) -> None:
        """Release sample buffers when a fresh executable takes over Paula (§6.3.1)."""
        self._regions.clear()

    def read(self, location: int, count: int) -> NDArray[np.int8]:
        """``count`` bytes from ``location``; bytes outside a loaded region are 0."""
        out = np.zeros(count, dtype=np.int8)
        data = self._regions.get(location >> REGION_SHIFT)
        offset = location & OFFSET_MASK
        if data is not None and offset < len(data):
            end = min(len(data), offset + count)
            out[: end - offset] = data[offset:end]
        return out

    def write(self, region: int, data: bytes, capacity: int) -> None:
        """DOS read into a bank: retain the unwritten tail after a short read (§6.2.10)."""
        previous = self._regions.get(region)
        size = max(capacity, len(previous) if previous is not None else 0)
        out = np.zeros(size, dtype=np.int8)
        if previous is not None:
            out[: len(previous)] = previous
        out[: len(data)] = np.frombuffer(data, dtype=np.int8)
        self._regions[region] = out


def _effective_period(period: int) -> int:
    return max(MIN_PERIOD, period or _WORDS_WRAP)


def _effective_volume(volume: int) -> int:
    return min(MAX_VOLUME, volume & _VOLUME_BITS)


@dataclass(slots=True)
class _Channel:
    """Registers and playing state of one channel."""

    location: int = 0
    length: int = 0
    period: int = MIN_PERIOD
    volume: int = 0
    dma: bool = False
    restart: bool = False
    playing: bool = False
    periodic: bool = False
    start: int = 0
    buffer: NDArray[np.int8] = field(default_factory=lambda: np.zeros(2, dtype=np.int8))


class Paula:
    """Four audio channels with DMA edges, buffer reloads and interrupt requests (§6.2.9)."""

    def __init__(self, memory: SampleMemory, rate: int) -> None:
        """Render at ``rate`` output samples per second from ``memory``."""
        if rate <= 0:
            raise ValueError(f"sample rate must be positive, got {rate}")
        self.memory = memory
        self.rate = rate
        self.handler: Callable[[], None] | None = None
        self.requests = 0
        self.enabled = 0
        self._channels = tuple(_Channel() for _ in range(CHANNELS))
        self._remainder = 0
        self._now = 0
        self._samples = 0
        self._out = np.zeros((CHANNELS, 0), dtype=np.int32)
        self._done = [0] * CHANNELS
        self._begin_frame()

    def dmacon(self, value: int) -> None:
        """Set (bit 15) or clear the audio DMA bits 0..3."""
        for channel in range(CHANNELS):
            if not value & channel_dma(channel):
                continue
            state = self._channels[channel]
            self._sync(channel)
            if value & SET:
                state.restart |= not state.dma
                state.dma = True
            else:
                state.dma = False
                state.playing = False

    def intena(self, value: int) -> None:
        """Set (bit 15) or clear the audio interrupt enable bits."""
        bits = value & self._audio_bits()
        if value & SET:
            for channel in range(CHANNELS):
                if bits & channel_interrupt(channel):
                    self._unwrap(channel)
            self.enabled |= bits
        else:
            self.enabled &= ~bits

    def intreq(self, value: int) -> None:
        """Set (bit 15) or clear audio interrupt requests."""
        bits = value & self._audio_bits()
        self.requests = self.requests | bits if value & SET else self.requests & ~bits

    def location(self, channel: int, value: int) -> None:
        """Write the location register; it takes effect at the next buffer start."""
        self._unwrap(channel)
        self._channels[channel].location = value & ~1

    def length(self, channel: int, words: int) -> None:
        """Write the length register (words); it takes effect at the next buffer start."""
        self._unwrap(channel)
        self._channels[channel].length = words & 0xFFFF

    def period(self, channel: int, value: int) -> None:
        """Write the period register; the playing position is kept."""
        self._sync(channel)
        state = self._channels[channel]
        old = _effective_period(state.period)
        state.period = value & 0xFFFF
        new = _effective_period(state.period)
        if state.playing and new != old:
            state.start = self._now - (self._now - state.start) * new // old

    def volume(self, channel: int, value: int) -> None:
        """Write the volume register."""
        self._sync(channel)
        self._channels[channel].volume = value & 0xFFFF

    def commit(self) -> None:
        """End of a CPU batch: start channels whose DMA rose, then serve interrupts."""
        self._latch_restarts()
        self._dispatch()

    def render_frame(self) -> NDArray[np.int16]:
        """Play one fiftieth of a second and return stereo ``int16`` samples."""
        frame = self._samples * PAL_CLOCK
        while (event := self._next_event(frame)) is not None:
            self._now, channel = event
            self._sync(channel)
            self._reload(channel)
            self._dispatch()
        self._now = frame
        for channel in range(CHANNELS):
            self._sync(channel)
        out = self._out
        left = out[_LEFT[0]] + out[_LEFT[1]]
        right = out[_RIGHT[0]] + out[_RIGHT[1]]
        pcm = (np.stack((left, right), axis=1) * _OUTPUT_GAIN).astype(np.int16)
        self._end_frame(frame)
        return pcm

    def _audio_bits(self) -> int:
        return sum(channel_interrupt(channel) for channel in range(CHANNELS))

    def _begin_frame(self) -> None:
        total = self.rate + self._remainder
        self._samples = total // VBL_HZ
        self._remainder = total % VBL_HZ
        self._out = np.zeros((CHANNELS, self._samples), dtype=np.int32)
        self._done = [0] * CHANNELS
        self._now = 0

    def _end_frame(self, frame: int) -> None:
        for state in self._channels:
            state.start -= frame
            if state.playing and state.periodic:
                span = self._span(state)
                state.start += (-state.start) // span * span
            state.periodic = False
        self._begin_frame()

    def _span(self, state: _Channel) -> int:
        return len(state.buffer) * _effective_period(state.period) * self.rate

    def _next_event(self, frame: int) -> tuple[int, int] | None:
        best: tuple[int, int] | None = None
        for channel, state in enumerate(self._channels):
            if not state.playing or state.periodic:
                continue
            end = state.start + self._span(state)
            if end <= frame and (best is None or end < best[0]):
                best = (end, channel)
        return best

    def _latch(self, channel: int) -> None:
        state = self._channels[channel]
        words = state.length or _WORDS_WRAP
        state.buffer = self.memory.read(state.location, 2 * words)
        state.start = self._now
        self.requests |= channel_interrupt(channel)

    def _reload(self, channel: int) -> None:
        self._latch(channel)
        self._channels[channel].periodic = not self.enabled & channel_interrupt(channel)

    def _latch_restarts(self) -> None:
        for channel, state in enumerate(self._channels):
            if state.restart and state.dma:
                state.playing = True
                state.periodic = False
                self._latch(channel)
            state.restart = False

    def _dispatch(self) -> None:
        while self.handler is not None and self.requests & self.enabled:
            self.handler()
            self._latch_restarts()

    def _unwrap(self, channel: int) -> None:
        """Leave the periodic shortcut: the current repetition becomes the playing buffer."""
        state = self._channels[channel]
        if state.periodic:
            self._sync(channel)
            span = self._span(state)
            state.start += (self._now - state.start) // span * span
            state.periodic = False

    def _sync(self, channel: int) -> None:
        """Render ``channel`` up to ``now`` with the registers in force until now."""
        upto = min(self._samples, -(-self._now // PAL_CLOCK))
        first = self._done[channel]
        if upto <= first:
            return
        self._done[channel] = upto
        state = self._channels[channel]
        volume = _effective_volume(state.volume)
        if not state.playing or volume == 0:
            return
        k = np.arange(first, upto, dtype=np.int64)
        index = (k * PAL_CLOCK - state.start) // (_effective_period(state.period) * self.rate)
        if state.periodic:
            index %= len(state.buffer)
        self._out[channel, first:upto] = state.buffer[index].astype(np.int32) * volume
