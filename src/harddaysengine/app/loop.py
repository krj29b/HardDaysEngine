"""Deterministic 50 Hz PAL VBL fixed-timestep accumulator and refresh pacing (§7.1, §13.6)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

PAL_HZ = 50
TICK_NS = 1_000_000_000 // PAL_HZ  # 20,000,000 ns (20 ms per PAL VBL)
MAX_FRAME_DELTA_NS = TICK_NS * 10  # Cap at 200 ms (10 VBLs) per host frame
FIGHT_FRAME_DIVISOR_NORMAL = 6  # 6 VBLs per combat frame (~8.33 Hz)
FIGHT_FRAME_DIVISOR_FAST = 5  # 5 VBLs per combat frame (10.0 Hz)
_STALL_THRESHOLD_NS = TICK_NS * 2


class Accumulator50Hz:
    """Integer nanosecond fixed-timestep accumulator for the 50 Hz PAL simulation clock."""

    def __init__(self) -> None:
        self.accumulated_ns: int = 0
        self.total_ticks: int = 0

    def advance(self, delta_ns: int) -> int:
        """Add ``delta_ns`` elapsed host time and return the number of 50 Hz ticks to run."""
        if delta_ns <= 0:
            return 0
        clamped = min(delta_ns, MAX_FRAME_DELTA_NS)
        self.accumulated_ns += clamped
        ticks = self.accumulated_ns // TICK_NS
        self.accumulated_ns %= TICK_NS
        self.total_ticks += ticks
        return ticks

    @property
    def alpha_permille(self) -> int:
        """Sub-tick interpolation fraction in integer permille (0..999)."""
        return (self.accumulated_ns * 1000) // TICK_NS


def multiple_of_50_hz(refresh_hz: int) -> int:
    """Return ``n`` when ``refresh_hz == n * 50`` (50, 100, 150, 200, ...), else ``0`` (§13.6)."""
    if refresh_hz > 0 and refresh_hz % PAL_HZ == 0:
        return refresh_hz // PAL_HZ
    return 0


@dataclass(slots=True, frozen=True)
class PacingStep:
    """Result of one host-loop iteration: how many 50 Hz ticks to run and whether to present."""

    ticks: int
    should_present: bool


class PacingHistogram:
    """Measures how many host refreshes display each completed simulation tick (T13.4)."""

    def __init__(self) -> None:
        self._tick_counts: Counter[int] = Counter()
        self.total_presents: int = 0

    def record_present(self, tick: int) -> None:
        """Record that a presented frame displayed simulation ``tick``."""
        self._tick_counts[tick] += 1
        self.total_presents += 1

    @property
    def distinct_ticks_shown(self) -> int:
        """Number of distinct simulation ticks that were presented at least once."""
        return len(self._tick_counts)

    def refreshes_per_tick(self) -> dict[int, int]:
        """Return ``{refresh_count: number_of_ticks}`` sorted by refresh count."""
        hist: Counter[int] = Counter(self._tick_counts.values())
        return dict(sorted(hist.items()))


class RefreshPacer:
    """Host refresh pacing schedule for 50 Hz multiples, 60 Hz, and vsync-off/VRR (§13.6).

    - ``vsync=False`` (or VRR): present once per completed simulation tick.
    - ``vsync=True`` on a 50 Hz multiple (``n * 50`` Hz): advance 1 tick every ``n`` refreshes
      so every tick is shown for exactly ``n`` refreshes, absorbing minor VBL jitter while
      still catching up via the accumulator after stalls (>= 40 ms).
    - ``vsync=True`` on non-multiples (e.g. 60 Hz): advance via :class:`Accumulator50Hz` and
      present every refresh (1 or 2 refreshes per tick, no blending).
    """

    def __init__(
        self,
        *,
        refresh_hz: int = 0,
        vsync: bool = True,
        accumulator: Accumulator50Hz | None = None,
    ) -> None:
        self.accumulator = accumulator if accumulator is not None else Accumulator50Hz()
        self.vsync = vsync
        self.refresh_hz = max(0, refresh_hz)
        self._lockstep_n = multiple_of_50_hz(self.refresh_hz) if vsync else 0
        self._sub_refresh = 0
        self._presented_once = False

    @property
    def lockstep_refreshes(self) -> int:
        """Exact refreshes per tick in 50 Hz-multiple lockstep mode, or ``0`` if inactive."""
        return self._lockstep_n

    def set_refresh_rate(self, refresh_hz: int) -> None:
        """Update the active display refresh rate (e.g. after a fullscreen mode change)."""
        self.refresh_hz = max(0, refresh_hz)
        self._lockstep_n = multiple_of_50_hz(self.refresh_hz) if self.vsync else 0
        self._sub_refresh = 0

    def step(self, delta_ns: int) -> PacingStep:
        """Advance pacing for one host frame of ``delta_ns`` nanoseconds."""
        if not self.vsync:
            ticks = self.accumulator.advance(delta_ns)
            should_present = ticks > 0 or not self._presented_once
            if should_present:
                self._presented_once = True
            return PacingStep(ticks=ticks, should_present=should_present)

        if self._lockstep_n > 0:
            if delta_ns >= _STALL_THRESHOLD_NS:
                ticks = self.accumulator.advance(delta_ns)
                self._sub_refresh = 0
                self._presented_once = True
                return PacingStep(ticks=ticks, should_present=True)
            if self._sub_refresh == 0:
                ticks = 1
                self.accumulator.total_ticks += 1
                self.accumulator.accumulated_ns = 0
            else:
                ticks = 0
            self._sub_refresh = (self._sub_refresh + 1) % self._lockstep_n
            self._presented_once = True
            return PacingStep(ticks=ticks, should_present=True)

        ticks = self.accumulator.advance(delta_ns)
        self._presented_once = True
        return PacingStep(ticks=ticks, should_present=True)
