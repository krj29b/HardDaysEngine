"""Core integer value types for the deterministic 50 Hz simulation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import NewType

EntityId = NewType("EntityId", int)
TickCount = NewType("TickCount", int)


class KnightId(IntEnum):
    """Playable knight indices matching palette tunic slots (ARCHITECTURE.md §8.1)."""

    RED = 0
    BLUE = 1
    GREEN = 2
    YELLOW = 3


class Direction(IntEnum):
    """Actor horizontal facing direction (ARCHITECTURE.md §7.3).

    Matches ``Knight.ubFacing`` where ``1`` faces right and ``3`` faces left
    (bit 1 is the left-facing flag).
    """

    RIGHT = 1
    LEFT = 3

    @property
    def is_left(self) -> bool:
        """True when facing left (bit 1 set)."""
        return bool(self & 0x02)

    @property
    def sign(self) -> int:
        """Horizontal unit sign: ``+1`` when facing right, ``-1`` when facing left."""
        return -1 if self.is_left else 1

    def flipped(self) -> Direction:
        """Return the opposite facing direction."""
        return Direction.RIGHT if self.is_left else Direction.LEFT


@dataclass(slots=True, frozen=True)
class Pos25D:
    """2.5D arena position in integer pixels (ARCHITECTURE.md §4.1).

    ``x`` is horizontal position, ``y`` is ground depth (baseline), and ``z`` is
    altitude above ground. Screen projection is ``screen_y = y - z``.
    """

    x: int
    y: int
    z: int = 0

    @property
    def screen_y(self) -> int:
        """Projected 2D screen Y coordinate (``y - z``)."""
        return self.y - self.z

    def translate(self, dx: int = 0, dy: int = 0, dz: int = 0) -> Pos25D:
        """Return a new position offset by ``(dx, dy, dz)``."""
        return Pos25D(self.x + dx, self.y + dy, self.z + dz)

    def depth_distance(self, other: Pos25D) -> int:
        """Absolute ground-depth separation ``|self.y - other.y|``."""
        return abs(self.y - other.y)


@dataclass(slots=True, frozen=True)
class Rect2D:
    """Axis-aligned 2D integer rectangle in screen or cel coordinates."""

    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        """Exclusive right edge (``x + width``)."""
        return self.x + self.width

    @property
    def bottom(self) -> int:
        """Exclusive bottom edge (``y + height``)."""
        return self.y + self.height

    @classmethod
    def from_bounds(cls, min_x: int, min_y: int, max_x: int, max_y: int) -> Rect2D:
        """Construct a rectangle from ``[min_x, max_x) x [min_y, max_y)`` bounds."""
        return cls(min_x, min_y, max(0, max_x - min_x), max(0, max_y - min_y))

    def contains(self, px: int, py: int) -> bool:
        """True if ``(px, py)`` lies inside ``[x, right) x [y, bottom)``."""
        return self.x <= px < self.right and self.y <= py < self.bottom

    def intersects(self, other: Rect2D) -> bool:
        """True if both rectangles have positive area and overlap."""
        if self.width <= 0 or self.height <= 0 or other.width <= 0 or other.height <= 0:
            return False
        return (
            self.x < other.right
            and other.x < self.right
            and self.y < other.bottom
            and other.y < self.bottom
        )
