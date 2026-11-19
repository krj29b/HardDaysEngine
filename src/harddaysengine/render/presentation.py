"""Host presentation settings (ARCHITECTURE.md §13, ADR-053).

These describe how the RGB image of the canvas reaches the window. They are not original
behaviour and never affect the simulation or the indexed canvas.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

DEFAULT_WINDOW_SCALE = 3
MIN_WINDOW_SCALE = 1
MAX_WINDOW_SCALE = 8


class FilterPreset(StrEnum):
    """Post-process preset applied after the palette lookup (§13.4)."""

    FAITHFUL = "faithful"
    SCANLINES = "scanlines"


@dataclass(slots=True, frozen=True)
class DisplaySettings:
    """Resolved ``[display]`` settings (§13.7)."""

    scale: int = DEFAULT_WINDOW_SCALE
    filter: FilterPreset = FilterPreset.FAITHFUL

    @property
    def scanlines(self) -> bool:
        """Whether the preset darkens alternate halves of each source row (§13.3)."""
        return self.filter is FilterPreset.SCANLINES
