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


class ScaleMode(StrEnum):
    """How the cropped canvas scales into the window (§13.2)."""

    INTEGER = "integer"
    SHARP_BILINEAR = "sharp-bilinear"
    FIT = "fit"


class AspectMode(StrEnum):
    """Shape of one source pixel (§13.1)."""

    SQUARE = "square"
    PAL = "pal"


class CropMode(StrEnum):
    """Region of the 320x256 canvas shown in the window (§13.1)."""

    FULL = "full"
    PLAYFIELD = "playfield"


class FullscreenMode(StrEnum):
    """Host window fullscreen mode (§13.7)."""

    OFF = "off"
    DESKTOP = "desktop"
    EXCLUSIVE = "exclusive"


@dataclass(slots=True, frozen=True)
class DisplaySettings:
    """Resolved ``[display]`` settings (§13.7)."""

    scale: int = DEFAULT_WINDOW_SCALE
    filter: FilterPreset = FilterPreset.FAITHFUL
    scale_mode: ScaleMode = ScaleMode.INTEGER
    aspect: AspectMode = AspectMode.SQUARE
    crop: CropMode = CropMode.FULL
    fullscreen: FullscreenMode = FullscreenMode.OFF

    @property
    def scanlines(self) -> bool:
        """Whether the preset darkens alternate halves of each source row (§13.3)."""
        return self.filter is FilterPreset.SCANLINES
