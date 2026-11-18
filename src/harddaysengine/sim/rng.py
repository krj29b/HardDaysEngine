"""The original random number generator (ARCHITECTURE.md §11.2).

One 32-bit state word. A draw runs eight steps of a rotate-and-XOR feedback; callers mask the
bits they want from the low end. The fight (§10.20) and the map (§11) share one generator, so the
order and the number of draws are part of the behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass

from harddaysengine.sim.m68k import u32

DEFAULT_SEED = 0x1234_5678

_STEPS = 8
_PERCENT_MASK = 0x7F
_PERCENT_LIMIT = 100
_PERCENT_FOLD = 27


def _rotate_right_3(value: int) -> int:
    return u32((value >> 3) | (value << 29))


@dataclass(slots=True)
class Rng:
    """The generator state; ``draw`` and ``percent`` advance it."""

    state: int

    def draw(self) -> int:
        """One draw: eight feedback steps; the new state is the result (§11.2)."""
        state = u32(self.state)
        for _ in range(_STEPS):
            feedback = _rotate_right_3(state) ^ state
            state = (state >> 1) | ((feedback >> 1 & 1) << 31)
        self.state = state
        return state

    def percent(self) -> int:
        """One draw folded to 0..100: the low 7 bits, minus 27 when 100 or more (§11.2)."""
        value = self.draw() & _PERCENT_MASK
        return value - _PERCENT_FOLD if value >= _PERCENT_LIMIT else value
