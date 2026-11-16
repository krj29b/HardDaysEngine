"""The 24-byte inventory record: twelve 2-byte slots (ARCHITECTURE.md §9.4).

A slot is addressed by its byte offset (``$00``, ``$02``, ... ``$16``) as in the original; the
count is the byte at that even offset. Slots ``$14`` and ``$16`` are bit masks.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pymoonstone.sim.m68k import u8

INVENTORY_SLOTS = 12
SLOT_PROTECTION = 0x08
SLOT_MOONSTONES = 0x16


@dataclass(slots=True)
class Inventory:
    """One inventory (§9.4): ``counts[i]`` is the byte at slot offset ``2 * i``."""

    counts: list[int] = field(default_factory=lambda: [0] * INVENTORY_SLOTS)

    def byte(self, slot: int) -> int:
        """The count (or mask) at the even slot offset ``slot``."""
        if slot % 2 or not 0 <= slot // 2 < INVENTORY_SLOTS:
            raise IndexError(f"no inventory slot at offset {slot}")
        return u8(self.counts[slot // 2])
