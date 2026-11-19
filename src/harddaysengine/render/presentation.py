"""Host presentation settings (ARCHITECTURE.md §13, ADR-053).

These describe how the RGB image of the canvas reaches the window. They are not original
behaviour and never affect the simulation or the indexed canvas.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

DEFAULT_WINDOW_SCALE = 3
MIN_WINDOW_SCALE = 1
MAX_WINDOW_SCALE = 8

MIN_CRT_STRENGTH = 0.0
MAX_CRT_STRENGTH = 1.0
MIN_CRT_CURVATURE = 0.0
MAX_CRT_CURVATURE = 0.3

DEFAULT_CRT_MASK_STRENGTH = 0.25
DEFAULT_CRT_SCANLINES = 0.65
DEFAULT_CRT_CURVATURE = 0.08
DEFAULT_CRT_BLOOM = 0.22

MIN_SURROUND_BRIGHTNESS = 0.0
MAX_SURROUND_BRIGHTNESS = 1.0
DEFAULT_SURROUND_BRIGHTNESS = 0.28
DEFAULT_SURROUND_COLOR: tuple[int, int, int] = (16, 16, 20)
DEFAULT_SURROUND_COLOR_HEX = "#101014"


class FilterPreset(StrEnum):
    """Post-process preset applied after the palette lookup (§13.4)."""

    FAITHFUL = "faithful"
    SCANLINES = "scanlines"
    CRT = "crt"
    SMOOTH = "smooth"


class CrtMaskMode(StrEnum):
    """Phosphor mask pattern used by the ``crt`` preset (§13.4.1)."""

    APERTURE = "aperture"
    SHADOW = "shadow"
    NONE = "none"


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


class SurroundMode(StrEnum):
    """Decorative side-bar fill for windows wider than the letterbox (§13.5)."""

    OFF = "off"
    BLUR = "blur"
    SOLID = "solid"


def _validate_float_range(value: float, name: str, lo: float, hi: float) -> None:
    if not math.isfinite(value) or not lo <= value <= hi:
        msg = f"{name} must be a finite float in {lo}..{hi}, got {value!r}"
        raise ValueError(msg)


def parse_hex_rgb(value: str) -> tuple[int, int, int]:
    """Parse a strict ``#RRGGBB`` hex colour string into an ``(r, g, b)`` tuple (§13.7)."""
    if len(value) != 7 or not value.startswith("#"):
        msg = f"colour must be a #RRGGBB hex string, got {value!r}"
        raise ValueError(msg)
    digits = value[1:]
    if not all(ch in "0123456789abcdefABCDEF" for ch in digits):
        msg = f"colour must be a #RRGGBB hex string, got {value!r}"
        raise ValueError(msg)
    return (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16))


@dataclass(slots=True, frozen=True)
class CrtSettings:
    """Tunable parameters for the ``crt`` presentation preset (§13.4.1, §13.7)."""

    mask: CrtMaskMode = CrtMaskMode.APERTURE
    mask_strength: float = DEFAULT_CRT_MASK_STRENGTH
    scanlines: float = DEFAULT_CRT_SCANLINES
    curvature: float = DEFAULT_CRT_CURVATURE
    bloom: float = DEFAULT_CRT_BLOOM

    def __post_init__(self) -> None:
        _validate_float_range(
            self.mask_strength, "crt_mask_strength", MIN_CRT_STRENGTH, MAX_CRT_STRENGTH
        )
        _validate_float_range(self.scanlines, "crt_scanlines", MIN_CRT_STRENGTH, MAX_CRT_STRENGTH)
        _validate_float_range(self.curvature, "crt_curvature", MIN_CRT_CURVATURE, MAX_CRT_CURVATURE)
        _validate_float_range(self.bloom, "crt_bloom", MIN_CRT_STRENGTH, MAX_CRT_STRENGTH)


@dataclass(slots=True, frozen=True)
class SurroundSettings:
    """Tunable parameters for optional widescreen surround side bars (§13.5, §13.7)."""

    mode: SurroundMode = SurroundMode.OFF
    brightness: float = DEFAULT_SURROUND_BRIGHTNESS
    color: tuple[int, int, int] = DEFAULT_SURROUND_COLOR

    def __post_init__(self) -> None:
        _validate_float_range(
            self.brightness,
            "surround_brightness",
            MIN_SURROUND_BRIGHTNESS,
            MAX_SURROUND_BRIGHTNESS,
        )
        if len(self.color) != 3 or any(
            isinstance(ch, bool) or not isinstance(ch, int) or not 0 <= ch <= 255
            for ch in self.color
        ):
            msg = f"surround_color must be three 0..255 integers, got {self.color!r}"
            raise ValueError(msg)


@dataclass(slots=True, frozen=True)
class DisplaySettings:
    """Resolved ``[display]`` settings (§13.7)."""

    scale: int = DEFAULT_WINDOW_SCALE
    filter: FilterPreset = FilterPreset.FAITHFUL
    scale_mode: ScaleMode = ScaleMode.INTEGER
    aspect: AspectMode = AspectMode.SQUARE
    crop: CropMode = CropMode.FULL
    fullscreen: FullscreenMode = FullscreenMode.OFF
    vsync: bool = True
    crt: CrtSettings = CrtSettings()
    surround: SurroundSettings = SurroundSettings()

    @property
    def scanlines(self) -> bool:
        """Whether the preset darkens alternate halves of each source row (§13.3)."""
        return self.filter is FilterPreset.SCANLINES
