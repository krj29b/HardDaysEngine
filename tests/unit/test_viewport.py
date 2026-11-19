"""Table tests for aspect-correct viewport maths (ARCHITECTURE.md §13.1-§13.2, T13.2)."""

from __future__ import annotations

import pytest

from harddaysengine.render.presentation import (
    AspectMode,
    CropMode,
    DisplaySettings,
    ScaleMode,
)
from harddaysengine.render.viewport import (
    AxisSampling,
    SourceRect,
    compute_viewport,
    initial_window_size,
    pixel_aspect,
    source_rect,
)


def test_source_rect_and_pixel_aspect() -> None:
    assert source_rect(CropMode.FULL) == SourceRect(0, 0, 320, 256)
    assert source_rect(CropMode.PLAYFIELD) == SourceRect(0, 28, 320, 200)
    assert pixel_aspect(AspectMode.SQUARE) == pytest.approx(1.0)
    assert pixel_aspect(AspectMode.PAL) == pytest.approx(16.0 / 15.0)


@pytest.mark.parametrize(
    ("window", "crop", "aspect", "mode", "expected_rect", "expected_sampling", "expected_prescale"),
    [
        # 1920x1080 full canvas
        (
            (1920, 1080),
            CropMode.FULL,
            AspectMode.SQUARE,
            ScaleMode.INTEGER,
            (320, 28, 1280, 1024),
            (AxisSampling.NEAREST, AxisSampling.NEAREST),
            (4, 4),
        ),
        (
            (1920, 1080),
            CropMode.FULL,
            AspectMode.PAL,
            ScaleMode.INTEGER,
            (277, 28, 1365, 1024),
            (AxisSampling.SHARP_BILINEAR, AxisSampling.NEAREST),
            (4, 4),
        ),
        (
            (1920, 1080),
            CropMode.FULL,
            AspectMode.SQUARE,
            ScaleMode.SHARP_BILINEAR,
            (285, 0, 1350, 1080),
            (AxisSampling.SHARP_BILINEAR, AxisSampling.SHARP_BILINEAR),
            (4, 4),
        ),
        (
            (1920, 1080),
            CropMode.FULL,
            AspectMode.PAL,
            ScaleMode.SHARP_BILINEAR,
            (240, 0, 1440, 1080),
            (AxisSampling.SHARP_BILINEAR, AxisSampling.SHARP_BILINEAR),
            (4, 4),
        ),
        (
            (1920, 1080),
            CropMode.FULL,
            AspectMode.PAL,
            ScaleMode.FIT,
            (240, 0, 1440, 1080),
            (AxisSampling.LINEAR, AxisSampling.LINEAR),
            (4, 4),
        ),
        # 1920x1080 playfield crop
        (
            (1920, 1080),
            CropMode.PLAYFIELD,
            AspectMode.SQUARE,
            ScaleMode.INTEGER,
            (160, 40, 1600, 1000),
            (AxisSampling.NEAREST, AxisSampling.NEAREST),
            (5, 5),
        ),
        (
            (1920, 1080),
            CropMode.PLAYFIELD,
            AspectMode.PAL,
            ScaleMode.INTEGER,
            (106, 40, 1707, 1000),
            (AxisSampling.SHARP_BILINEAR, AxisSampling.NEAREST),
            (5, 5),
        ),
        (
            (1920, 1080),
            CropMode.PLAYFIELD,
            AspectMode.PAL,
            ScaleMode.SHARP_BILINEAR,
            (38, 0, 1843, 1080),
            (AxisSampling.SHARP_BILINEAR, AxisSampling.SHARP_BILINEAR),
            (5, 5),
        ),
        # 1366x768 laptop display
        (
            (1366, 768),
            CropMode.FULL,
            AspectMode.SQUARE,
            ScaleMode.INTEGER,
            (203, 0, 960, 768),
            (AxisSampling.NEAREST, AxisSampling.NEAREST),
            (3, 3),
        ),
        (
            (1366, 768),
            CropMode.FULL,
            AspectMode.PAL,
            ScaleMode.INTEGER,
            (171, 0, 1024, 768),
            (AxisSampling.SHARP_BILINEAR, AxisSampling.NEAREST),
            (3, 3),
        ),
        (
            (1366, 768),
            CropMode.PLAYFIELD,
            AspectMode.SQUARE,
            ScaleMode.SHARP_BILINEAR,
            (68, 0, 1229, 768),
            (AxisSampling.SHARP_BILINEAR, AxisSampling.SHARP_BILINEAR),
            (3, 3),
        ),
        (
            (1366, 768),
            CropMode.PLAYFIELD,
            AspectMode.PAL,
            ScaleMode.SHARP_BILINEAR,
            (27, 0, 1311, 768),
            (AxisSampling.SHARP_BILINEAR, AxisSampling.SHARP_BILINEAR),
            (4, 3),
        ),
        # Odd window sizes (width-limited and height-limited)
        (
            (641, 513),
            CropMode.FULL,
            AspectMode.SQUARE,
            ScaleMode.INTEGER,
            (0, 0, 640, 512),
            (AxisSampling.NEAREST, AxisSampling.NEAREST),
            (2, 2),
        ),
        (
            (641, 513),
            CropMode.FULL,
            AspectMode.PAL,
            ScaleMode.INTEGER,
            (150, 128, 341, 256),
            (AxisSampling.SHARP_BILINEAR, AxisSampling.NEAREST),
            (1, 1),
        ),
        (
            (999, 555),
            CropMode.FULL,
            AspectMode.SQUARE,
            ScaleMode.FIT,
            (152, 0, 694, 555),
            (AxisSampling.LINEAR, AxisSampling.LINEAR),
            (2, 2),
        ),
        (
            (999, 999),
            CropMode.FULL,
            AspectMode.PAL,
            ScaleMode.SHARP_BILINEAR,
            (0, 125, 999, 749),
            (AxisSampling.SHARP_BILINEAR, AxisSampling.SHARP_BILINEAR),
            (3, 2),
        ),
        # Smaller-than-1x window: integer clamps k >= 1, fit downscales cleanly
        (
            (300, 200),
            CropMode.FULL,
            AspectMode.SQUARE,
            ScaleMode.INTEGER,
            (-10, -28, 320, 256),
            (AxisSampling.NEAREST, AxisSampling.NEAREST),
            (1, 1),
        ),
        (
            (300, 200),
            CropMode.FULL,
            AspectMode.SQUARE,
            ScaleMode.FIT,
            (25, 0, 250, 200),
            (AxisSampling.LINEAR, AxisSampling.LINEAR),
            (1, 1),
        ),
    ],
)
def test_compute_viewport_table(
    window: tuple[int, int],
    crop: CropMode,
    aspect: AspectMode,
    mode: ScaleMode,
    expected_rect: tuple[int, int, int, int],
    expected_sampling: tuple[AxisSampling, AxisSampling],
    expected_prescale: tuple[int, int],
) -> None:
    pv = compute_viewport(window[0], window[1], crop=crop, aspect=aspect, scale_mode=mode)
    assert pv.gl_viewport == expected_rect
    assert (pv.sampling_x, pv.sampling_y) == expected_sampling
    assert (pv.prescale_x, pv.prescale_y) == expected_prescale
    assert pv.uses_linear_filter is (
        expected_sampling != (AxisSampling.NEAREST, AxisSampling.NEAREST)
    )


@pytest.mark.parametrize(
    ("settings", "expected_size"),
    [
        (DisplaySettings(scale=3, crop=CropMode.FULL, aspect=AspectMode.SQUARE), (960, 768)),
        (DisplaySettings(scale=3, crop=CropMode.FULL, aspect=AspectMode.PAL), (1024, 768)),
        (DisplaySettings(scale=3, crop=CropMode.PLAYFIELD, aspect=AspectMode.SQUARE), (960, 600)),
        (DisplaySettings(scale=3, crop=CropMode.PLAYFIELD, aspect=AspectMode.PAL), (1024, 600)),
    ],
)
def test_initial_window_size(settings: DisplaySettings, expected_size: tuple[int, int]) -> None:
    assert initial_window_size(settings) == expected_size


@pytest.mark.parametrize(("width", "height"), [(0, 768), (1024, 0), (-1, 100), (100, -5)])
def test_compute_viewport_rejects_nonpositive_window(width: int, height: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        compute_viewport(width, height)
