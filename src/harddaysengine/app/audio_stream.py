"""PCM ring between the 50 Hz simulation and the audio device callback (ARCHITECTURE.md §6.2.9).

The simulation produces one fiftieth of a second of PCM per VBL, but the host runs VBLs in
bursts that follow the display rate (0, 1 or 2 per frame at 60 Hz). The device consumes
samples at its own steady rate on another thread. This ring decouples the two:

* playback waits until ``prebuffer`` frames are queued, and waits again after any underrun,
  so a late burst causes one short silence instead of many clicks;
* when the host clock runs ahead of the device clock and more than ``limit`` frames are
  queued, the oldest frames are dropped down to the middle of the window, bounding latency.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from numpy.typing import NDArray

STEREO = 2


class PcmRing:
    """Thread-safe stereo ``int16`` FIFO with prebuffering and a latency bound."""

    def __init__(self, prebuffer: int, limit: int) -> None:
        """Start playback at ``prebuffer`` queued frames; trim when above ``limit``."""
        if prebuffer <= 0 or limit <= prebuffer:
            raise ValueError("PCM ring needs 0 < prebuffer < limit")
        self.prebuffer = prebuffer
        self.limit = limit
        self.underruns = 0
        self.dropped = 0
        self._lock = threading.Lock()
        self._frames: NDArray[np.int16] = np.zeros((0, STEREO), dtype=np.int16)
        self._primed = False

    @classmethod
    def for_device(cls, rate: int, chunk: int) -> PcmRing:
        """Size the window for a device of ``rate`` Hz pulling ``chunk`` frames per callback.

        Prebuffer one device chunk plus two VBLs; allow six more VBLs before trimming.
        """
        vbl = max(1, rate // 50)
        prebuffer = chunk + 2 * vbl
        return cls(prebuffer, prebuffer + 6 * vbl)

    @property
    def buffered(self) -> int:
        """Frames queued and not yet read."""
        with self._lock:
            return len(self._frames)

    def write(self, pcm: NDArray[np.int16]) -> None:
        """Append one VBL of stereo PCM, trimming the oldest frames above ``limit``."""
        with self._lock:
            frames = np.concatenate((self._frames, pcm.reshape(-1, STEREO)))
            if len(frames) > self.limit:
                keep = (self.prebuffer + self.limit) // 2
                self.dropped += len(frames) - keep
                frames = frames[len(frames) - keep :]
            self._frames = frames

    def read(self, count: int) -> NDArray[np.int16]:
        """Remove ``count`` frames; silence while priming and for any missing tail."""
        out = np.zeros((count, STEREO), dtype=np.int16)
        with self._lock:
            if not self._primed:
                if len(self._frames) < self.prebuffer:
                    return out
                self._primed = True
            taken = self._frames[:count]
            self._frames = self._frames[len(taken) :]
            if len(taken) < count:
                self.underruns += 1
                self._primed = False
        out[: len(taken)] = taken
        return out
