"""Aspect-correct destination viewport and scaling maths (ARCHITECTURE.md §13.1-§13.2).

Pure geometry calculations for mapping the 320x256 canvas (or its 320x200 playfield) into a
window of arbitrary size with square or PAL 4:3 pixels and integer, sharp-bilinear, or fit
scaling. No OpenGL or pygame dependencies.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import IntEnum

from harddaysengine.render.presentation import AspectMode, CropMode, DisplaySettings, ScaleMode

CANVAS_WIDTH = 320
CANVAS_HEIGHT = 256
PLAYFIELD_HEIGHT = 200
PLAYFIELD_Y_OFFSET = 28

SQUARE_PIXEL_ASPECT = 1.0
PAL_PIXEL_ASPECT = 16.0 / 15.0
"""Horizontal scale per unit of vertical scale so 320x256 fills 4:3 (§13.1)."""


class AxisSampling(IntEnum):
    """Per-axis texture sampling mode passed to the presentation shader (§13.2)."""

    NEAREST = 0
    LINEAR = 1
    SHARP_BILINEAR = 2


@dataclass(slots=True, frozen=True)
class SourceRect:
    """Top-down source crop rectangle within the 320x256 canvas (§13.1)."""

    x: int
    y: int
    width: int
    height: int


@dataclass(slots=True, frozen=True)
class PresentationViewport:
    """Letterboxed destination rectangle and per-axis sampling parameters (§13.2)."""

    window_width: int
    window_height: int
    source: SourceRect
    dest_x: int
    dest_y: int
    dest_width: int
    dest_height: int
    scale_x: float
    scale_y: float
    prescale_x: int
    prescale_y: int
    sampling_x: AxisSampling
    sampling_y: AxisSampling

    @property
    def gl_viewport(self) -> tuple[int, int, int, int]:
        """Destination rectangle as an OpenGL ``(x, y, width, height)`` tuple."""
        return (self.dest_x, self.dest_y, self.dest_width, self.dest_height)

    @property
    def uses_linear_filter(self) -> bool:
        """Whether the canvas texture should use ``GL_LINEAR`` sampling."""
        return self.sampling_x is not AxisSampling.NEAREST or (
            self.sampling_y is not AxisSampling.NEAREST
        )


def source_rect(crop: CropMode) -> SourceRect:
    """Return the top-down source crop rectangle for ``crop`` (§13.1)."""
    if crop is CropMode.PLAYFIELD:
        return SourceRect(0, PLAYFIELD_Y_OFFSET, CANVAS_WIDTH, PLAYFIELD_HEIGHT)
    return SourceRect(0, 0, CANVAS_WIDTH, CANVAS_HEIGHT)


def pixel_aspect(aspect: AspectMode) -> float:
    """Return the horizontal-to-vertical pixel aspect ratio for ``aspect`` (§13.1)."""
    if aspect is AspectMode.PAL:
        return PAL_PIXEL_ASPECT
    return SQUARE_PIXEL_ASPECT


def initial_window_size(settings: DisplaySettings) -> tuple[int, int]:
    """Return the initial window ``(width, height)`` for ``settings`` (§13.1, §13.7)."""
    src = source_rect(settings.crop)
    aspect_ratio = pixel_aspect(settings.aspect)
    width = max(1, round(src.width * aspect_ratio * settings.scale))
    height = max(1, src.height * settings.scale)
    return (width, height)


def _integer_dest(
    raw_scale: float, src: SourceRect, aspect: AspectMode, aspect_ratio: float
) -> tuple[int, int, AxisSampling, AxisSampling]:
    whole = max(1, math.floor(raw_scale))
    dest_h = whole * src.height
    if aspect is AspectMode.PAL:
        dest_w = max(1, round(whole * aspect_ratio * src.width))
        return (dest_w, dest_h, AxisSampling.SHARP_BILINEAR, AxisSampling.NEAREST)
    return (whole * src.width, dest_h, AxisSampling.NEAREST, AxisSampling.NEAREST)


def _fit_dest(
    window_width: int,
    window_height: int,
    raw_scale: float,
    src: SourceRect,
    aspect_ratio: float,
    sampling: AxisSampling,
) -> tuple[int, int, AxisSampling, AxisSampling]:
    dest_w = max(1, min(window_width, round(src.width * raw_scale * aspect_ratio)))
    dest_h = max(1, min(window_height, round(src.height * raw_scale)))
    return (dest_w, dest_h, sampling, sampling)


def compute_viewport(
    window_width: int,
    window_height: int,
    *,
    crop: CropMode = CropMode.FULL,
    aspect: AspectMode = AspectMode.SQUARE,
    scale_mode: ScaleMode = ScaleMode.INTEGER,
) -> PresentationViewport:
    """Map ``(window size, crop, aspect, scale_mode)`` to a centred letterbox viewport (§13.2)."""
    if window_width <= 0 or window_height <= 0:
        msg = f"window dimensions must be positive, got {window_width}x{window_height}"
        raise ValueError(msg)

    src = source_rect(crop)
    aspect_ratio = pixel_aspect(aspect)
    raw_scale = min(window_height / src.height, window_width / (src.width * aspect_ratio))

    if scale_mode is ScaleMode.INTEGER:
        dest_w, dest_h, sampling_x, sampling_y = _integer_dest(raw_scale, src, aspect, aspect_ratio)
    elif scale_mode is ScaleMode.SHARP_BILINEAR:
        dest_w, dest_h, sampling_x, sampling_y = _fit_dest(
            window_width,
            window_height,
            raw_scale,
            src,
            aspect_ratio,
            AxisSampling.SHARP_BILINEAR,
        )
    else:
        dest_w, dest_h, sampling_x, sampling_y = _fit_dest(
            window_width, window_height, raw_scale, src, aspect_ratio, AxisSampling.LINEAR
        )

    dest_x = (window_width - dest_w) // 2
    dest_y = (window_height - dest_h) // 2
    scale_x = dest_w / src.width
    scale_y = dest_h / src.height
    return PresentationViewport(
        window_width=window_width,
        window_height=window_height,
        source=src,
        dest_x=dest_x,
        dest_y=dest_y,
        dest_width=dest_w,
        dest_height=dest_h,
        scale_x=scale_x,
        scale_y=scale_y,
        prescale_x=max(1, math.floor(scale_x)),
        prescale_y=max(1, math.floor(scale_y)),
        sampling_x=sampling_x,
        sampling_y=sampling_y,
    )
