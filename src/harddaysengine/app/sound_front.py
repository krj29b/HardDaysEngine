"""The hunk-16 sound front end: busy mask and channel cursor (ARCHITECTURE.md §6.2.1)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harddaysengine.sim.sound import SoundKind, SoundRequest

if TYPE_CHECKING:
    from collections.abc import Callable

    from harddaysengine.assets.synth_data import FrontEndData

CHANNELS = 4


class SoundFront:
    """Chooses channels for sound requests and starts their sequences (§6.2.1)."""

    def __init__(self, data: FrontEndData, start: Callable[[int, int], None]) -> None:
        """``start(sequence, channel)`` is the synth start."""
        self.data = data
        self._start = start
        self.busy = data.busy
        self.cursor = data.cursor

    def load(self) -> None:
        """A fresh load of the hunk: the cells take their values from the file."""
        self.busy = self.data.busy
        self.cursor = self.data.cursor

    def clear_busy(self) -> None:
        """The init tail: nothing busy, cursor kept."""
        self.busy = 0

    def play(self, sequence: int) -> int | None:
        """Start ``sequence`` on the next channel that is not busy; the channel, or ``None``."""
        data = self.data
        if self.busy & data.full == data.full:
            return None
        self.cursor = (self.cursor + 1) & data.cursor_mask
        while self.busy & (1 << self.cursor):
            self.cursor = (self.cursor + 1) & data.cursor_mask
        self._start(sequence, self.cursor)
        return self.cursor

    def fixed(self, sequence: int, channel: int) -> None:
        """Mark ``channel`` busy and start ``sequence`` on it."""
        self.busy |= self.data.fixed_bits[channel]
        self._start(sequence, channel)

    def release(self, mask: int, silence: int) -> None:
        """AND the busy byte with ``mask``, then play ``silence``."""
        self.busy &= mask
        self.play(silence)

    def stop_all(self) -> None:
        """Clear the busy byte, then release the four channels in order."""
        self.clear_busy()
        for mask in self.data.release_masks:
            self.release(mask, self.data.silence)

    def request(self, request: SoundRequest) -> None:
        """Carry out one request reported by ``sim`` (§10.32)."""
        match request.kind:
            case SoundKind.PLAY:
                self.play(request.sound)
            case SoundKind.FIXED:
                self.fixed(request.sound, request.channel)
            case SoundKind.RELEASE:
                self.release(request.mask, request.sound)
            case SoundKind.START:
                self._start(request.sound, request.channel)
