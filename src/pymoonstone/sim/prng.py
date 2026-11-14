"""Deterministic 68000-compatible integer PRNG for the simulation core."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pymoonstone.sim.m68k import mulu_w, u16, u32

if TYPE_CHECKING:
    from collections.abc import Mapping

_LCG_MULTIPLIER = 1664525
_LCG_INCREMENT = 1013904223
DEFAULT_SEED = 0x1234_5678


class SimPrng:
    """Deterministic 32-bit LCG PRNG using 68000-width integer arithmetic.

    Advances a 32-bit state modulo ``2**32`` and extracts the upper 16 bits for
    ``rand_u16``. Supports exact state serialization for deterministic replays.
    """

    __slots__ = ("_state",)

    def __init__(self, seed: int = DEFAULT_SEED) -> None:
        self._state: int = u32(seed)

    @property
    def state(self) -> int:
        """Current 32-bit unsigned PRNG state."""
        return self._state

    def seed(self, value: int) -> None:
        """Reset the PRNG state to ``u32(value)``."""
        self._state = u32(value)

    def rand_u16(self) -> int:
        """Advance the generator and return a 16-bit unsigned integer ``0..65535``."""
        self._state = u32(self._state * _LCG_MULTIPLIER + _LCG_INCREMENT)
        return u16(self._state >> 16)

    def rand_u32(self) -> int:
        """Advance the generator twice and return a 32-bit unsigned integer."""
        high = self.rand_u16()
        low = self.rand_u16()
        return u32((high << 16) | low)

    def rand_range(self, low: int, high: int | None = None) -> int:
        """Return a deterministic integer in the half-open interval ``[low, high)``.

        When ``high`` is omitted, returns an integer in ``[0, low)``.
        Raises ``ValueError`` if the range is empty (``stop <= start``).
        """
        if high is None:
            start = 0
            stop = low
        else:
            start = low
            stop = high
        if stop <= start:
            raise ValueError(f"empty range: [{start}, {stop})")
        span = stop - start
        if span == 0x1_0000:
            offset = self.rand_u16()
        elif span < 0x1_0000:
            offset = mulu_w(self.rand_u16(), span) >> 16
        else:
            offset = (self.rand_u32() * span) >> 32
        return start + offset

    def rand_bool(self, numerator: int = 1, denominator: int = 2) -> bool:
        """Return ``True`` with integer probability ``numerator / denominator``."""
        if denominator <= 0:
            raise ValueError(f"denominator must be positive, got {denominator}")
        return self.rand_range(denominator) < numerator

    def clone(self) -> SimPrng:
        """Return an independent copy with identical internal state."""
        return SimPrng(self._state)

    def to_bytes(self) -> bytes:
        """Serialize the PRNG state as 4 big-endian bytes."""
        return self._state.to_bytes(4, "big")

    @classmethod
    def from_bytes(cls, data: bytes) -> SimPrng:
        """Restore a ``SimPrng`` from 4 big-endian state bytes."""
        if len(data) != 4:
            raise ValueError(f"expected 4 state bytes, got {len(data)}")
        return cls(int.from_bytes(data, "big"))

    def to_dict(self) -> dict[str, int]:
        """Serialize the PRNG state to a JSON-safe integer mapping."""
        return {"state": self._state}

    @classmethod
    def from_dict(cls, data: Mapping[str, int]) -> SimPrng:
        """Restore a ``SimPrng`` from a mapping produced by :meth:`to_dict`."""
        if "state" not in data:
            raise ValueError("missing 'state' key")
        raw = data["state"]
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise TypeError(f"expected int state, got {type(raw).__name__}")
        return cls(raw)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SimPrng):
            return NotImplemented
        return self._state == other._state

    def __repr__(self) -> str:
        return f"SimPrng(state=0x{self._state:08X})"
