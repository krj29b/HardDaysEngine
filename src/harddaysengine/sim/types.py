"""Core integer value types for the deterministic 50 Hz simulation."""

from __future__ import annotations

from enum import IntEnum


class KnightId(IntEnum):
    """Playable knight indices matching palette tunic slots (ARCHITECTURE.md §8.1)."""

    RED = 0
    BLUE = 1
    GREEN = 2
    YELLOW = 3
