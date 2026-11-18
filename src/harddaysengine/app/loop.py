"""Deterministic 50 Hz PAL VBL fixed-timestep accumulator (ARCHITECTURE.md §7.1)."""

from __future__ import annotations

PAL_HZ = 50
TICK_NS = 1_000_000_000 // PAL_HZ  # 20,000,000 ns (20 ms per PAL VBL)
MAX_FRAME_DELTA_NS = TICK_NS * 10  # Cap at 200 ms (10 VBLs) per host frame
FIGHT_FRAME_DIVISOR_NORMAL = 6  # 6 VBLs per combat frame (~8.33 Hz)
FIGHT_FRAME_DIVISOR_FAST = 5  # 5 VBLs per combat frame (10.0 Hz)


class Accumulator50Hz:
    """Integer nanosecond fixed-timestep accumulator for the 50 Hz PAL simulation clock."""

    def __init__(self) -> None:
        self.accumulated_ns: int = 0
        self.total_ticks: int = 0

    def advance(self, delta_ns: int) -> int:
        """Add `delta_ns` elapsed host time and return the number of 50 Hz ticks to run."""
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
