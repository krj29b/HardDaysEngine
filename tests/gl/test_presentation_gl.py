"""Standalone OpenGL 3.3 presentation tests for letterbox, scaling, and scanlines (T13.2)."""

from __future__ import annotations

import moderngl
import numpy as np
import numpy.typing as npt
import pytest

from harddaysengine.app.audio import AudioEngine
from harddaysengine.app.game import GameSession
from harddaysengine.app.input import InputFrame
from harddaysengine.render.presentation import (
    AspectMode,
    CropMode,
    DisplaySettings,
    FilterPreset,
    ScaleMode,
)
from harddaysengine.render.renderer import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    PLAYFIELD_HEIGHT,
    PLAYFIELD_Y_OFFSET,
    HardDaysRenderer,
)
from harddaysengine.render.scene import SceneRenderer, default_bitmap_font

pytestmark = pytest.mark.gl


def _render_presented(
    ctx: moderngl.Context,
    renderer: HardDaysRenderer,
    window_size: tuple[int, int],
    settings: DisplaySettings,
) -> tuple[npt.NDArray[np.uint8], tuple[int, int, int, int]]:
    win_w, win_h = window_size
    out_tex = ctx.texture((win_w, win_h), 4)
    out_fbo = ctx.framebuffer(color_attachments=[out_tex])
    try:
        pv = renderer.present_to_framebuffer(out_fbo, window_size, settings=settings)
        raw = out_fbo.read(components=4)
        arr = np.frombuffer(raw, dtype=np.uint8).reshape((win_h, win_w, 4))
        top_down = np.ascontiguousarray(arr[::-1, :, :])
        return top_down, pv.gl_viewport
    finally:
        out_fbo.release()
        out_tex.release()


def _fill_checkerboard_canvas(scene: SceneRenderer) -> None:
    palette = [(0, 0, 0)] * 32
    palette[1] = (255, 255, 255)
    scene.set_palette(palette)
    yy, xx = np.indices((CANVAS_HEIGHT, CANVAS_WIDTH))
    scene.canvas[:, :] = ((xx + yy) & 1).astype(np.uint8)
    scene.flush_to_fbo()


def _run_lengths(binary_line: npt.NDArray[np.bool_]) -> list[int]:
    if binary_line.size == 0:
        return []
    changes = np.flatnonzero(binary_line[1:] != binary_line[:-1]) + 1
    starts = np.concatenate(([0], changes))
    ends = np.concatenate((changes, [binary_line.size]))
    return [int(e - s) for s, e in zip(starts, ends, strict=True)]


def _crop_dest_top_down(
    img: npt.NDArray[np.uint8], gl_viewport: tuple[int, int, int, int]
) -> npt.NDArray[np.uint8]:
    win_h = img.shape[0]
    dx, dy, dw, dh = gl_viewport
    top = win_h - (dy + dh)
    return img[top : top + dh, dx : dx + dw, :]


@pytest.mark.parametrize(
    ("window_size", "expected_width"),
    [
        ((1920, 1080), 4),
        ((1366, 768), 3),
    ],
)
def test_checkerboard_integer_square_has_uniform_column_widths(
    window_size: tuple[int, int], expected_width: int
) -> None:
    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    scene = SceneRenderer(renderer)
    try:
        _fill_checkerboard_canvas(scene)
        settings = DisplaySettings(
            filter=FilterPreset.FAITHFUL,
            scale_mode=ScaleMode.INTEGER,
            aspect=AspectMode.SQUARE,
            crop=CropMode.FULL,
        )
        img, vp = _render_presented(ctx, renderer, window_size, settings)
        dx, dy, dw, dh = vp
        win_h = window_size[1]
        top = win_h - (dy + dh)

        # Letterbox bars outside destination are pure black
        assert int(img[:, :dx, :3].max()) == 0
        assert int(img[:, dx + dw :, :3].max()) == 0
        if top > 0:
            assert int(img[:top, :, :3].max()) == 0
        if dy > 0:
            assert int(img[top + dh :, :, :3].max()) == 0

        dest = _crop_dest_top_down(img, vp)
        mid_row = dest[expected_width // 2, :, 0]
        # Pure nearest: every pixel is 0 or 255
        assert set(np.unique(mid_row).tolist()) == {0, 255}
        widths = _run_lengths(mid_row > 127)
        assert len(widths) == CANVAS_WIDTH
        assert min(widths) == expected_width
        assert max(widths) == expected_width
    finally:
        scene.release()
        renderer.release()
        ctx.release()


@pytest.mark.parametrize(
    ("window_size", "expected_row_height"),
    [
        ((1920, 1080), 4),
        ((1366, 768), 3),
    ],
)
def test_checkerboard_integer_pal_has_exact_rows_and_even_columns(
    window_size: tuple[int, int], expected_row_height: int
) -> None:
    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    scene = SceneRenderer(renderer)
    try:
        _fill_checkerboard_canvas(scene)
        settings = DisplaySettings(
            filter=FilterPreset.FAITHFUL,
            scale_mode=ScaleMode.INTEGER,
            aspect=AspectMode.PAL,
            crop=CropMode.FULL,
        )
        img, vp = _render_presented(ctx, renderer, window_size, settings)
        dest = _crop_dest_top_down(img, vp)

        # Horizontal: sharp-bilinear on X gives all 320 columns with max-min <= 1
        row = dest[expected_row_height // 2, :, 0]
        widths = _run_lengths(row > 127)
        assert len(widths) == CANVAS_WIDTH
        assert max(widths) - min(widths) <= 1

        # Vertical: nearest on Y gives exact uniform integer row heights (zero vertical bleed)
        col = dest[:, 1, 0]
        assert set(np.unique(col).tolist()) == {0, 255}
        heights = _run_lengths(col > 127)
        assert len(heights) == CANVAS_HEIGHT
        assert min(heights) == expected_row_height
        assert max(heights) == expected_row_height
    finally:
        scene.release()
        renderer.release()
        ctx.release()


@pytest.mark.parametrize(
    ("window_size", "aspect", "crop"),
    [
        ((1920, 1080), AspectMode.SQUARE, CropMode.FULL),
        ((1920, 1080), AspectMode.PAL, CropMode.FULL),
        ((1366, 768), AspectMode.PAL, CropMode.FULL),
        ((1366, 768), AspectMode.SQUARE, CropMode.PLAYFIELD),
        ((1366, 768), AspectMode.PAL, CropMode.PLAYFIELD),
    ],
)
def test_checkerboard_sharp_bilinear_even_columns_no_missing(
    window_size: tuple[int, int], aspect: AspectMode, crop: CropMode
) -> None:
    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    scene = SceneRenderer(renderer)
    try:
        _fill_checkerboard_canvas(scene)
        settings = DisplaySettings(
            filter=FilterPreset.FAITHFUL,
            scale_mode=ScaleMode.SHARP_BILINEAR,
            aspect=aspect,
            crop=crop,
        )
        img, vp = _render_presented(ctx, renderer, window_size, settings)
        dest = _crop_dest_top_down(img, vp)

        # Sample a row inside the first source row (away from vertical seam)
        sample_row = dest[1, :, 0]
        widths = _run_lengths(sample_row > 127)
        assert len(widths) == CANVAS_WIDTH
        assert max(widths) - min(widths) <= 1
        # Interior pixels remain crisp (both pure black and pure white present)
        assert int(sample_row.min()) == 0
        assert int(sample_row.max()) == 255
    finally:
        scene.release()
        renderer.release()
        ctx.release()


@pytest.mark.parametrize(
    ("window_size", "crop", "expected_period"),
    [
        ((1920, 1080), CropMode.FULL, 4),
        ((1920, 1080), CropMode.PLAYFIELD, 5),
        ((1366, 768), CropMode.FULL, 3),
        ((1366, 768), CropMode.PLAYFIELD, 3),
    ],
)
def test_output_grid_scanlines_have_exact_period_without_banding(
    window_size: tuple[int, int], crop: CropMode, expected_period: int
) -> None:
    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    scene = SceneRenderer(renderer)
    try:
        palette = [(0, 0, 0)] * 32
        palette[1] = (255, 255, 255)
        scene.set_palette(palette)
        scene.canvas[:, :] = 1
        scene.flush_to_fbo()

        settings = DisplaySettings(
            filter=FilterPreset.SCANLINES,
            scale_mode=ScaleMode.INTEGER,
            aspect=AspectMode.SQUARE,
            crop=crop,
        )
        img, vp = _render_presented(ctx, renderer, window_size, settings)
        dest = _crop_dest_top_down(img, vp)
        row_luma = dest[:, dest.shape[1] // 2, 0]

        pattern = row_luma[:expected_period]
        assert int(pattern[0]) == 255
        assert abs(int(pattern[-1]) - round(255 * 0.82)) <= 1
        # Every single row repeats with exact period == scale (zero banding)
        tiled = np.tile(pattern, row_luma.size // expected_period)
        assert np.array_equal(row_luma, tiled)
    finally:
        scene.release()
        renderer.release()
        ctx.release()


def test_playfield_crop_does_not_bleed_border_pixels() -> None:
    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    scene = SceneRenderer(renderer)
    try:
        palette = [(0, 0, 0)] * 32
        palette[1] = (255, 0, 0)  # Bright red border
        palette[2] = (0, 0, 255)  # Pure blue playfield
        scene.set_palette(palette)
        scene.canvas[:, :] = 1
        scene.canvas[PLAYFIELD_Y_OFFSET : PLAYFIELD_Y_OFFSET + PLAYFIELD_HEIGHT, :] = 2
        scene.flush_to_fbo()

        for mode in (ScaleMode.INTEGER, ScaleMode.SHARP_BILINEAR, ScaleMode.FIT):
            settings = DisplaySettings(
                filter=FilterPreset.FAITHFUL,
                scale_mode=mode,
                aspect=AspectMode.PAL,
                crop=CropMode.PLAYFIELD,
            )
            img, vp = _render_presented(ctx, renderer, (1366, 768), settings)
            dest = _crop_dest_top_down(img, vp)
            assert int(dest[:, :, 0].max()) == 0
            assert int(dest[:, :, 2].min()) == 255
    finally:
        scene.release()
        renderer.release()
        ctx.release()


def test_state_hash_unchanged_across_all_presentation_modes() -> None:
    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    scene = SceneRenderer(renderer)
    audio = AudioEngine(headless=True)
    font = default_bitmap_font()
    try:
        session = GameSession(audio=audio)
        for _ in range(10):
            session.step_tick(InputFrame())
        expected_hash = session.state_hash()

        for mode in ScaleMode:
            for aspect in AspectMode:
                for crop in CropMode:
                    for preset in FilterPreset:
                        settings = DisplaySettings(
                            filter=preset,
                            scale_mode=mode,
                            aspect=aspect,
                            crop=crop,
                        )
                        session.render_to_scene(scene, font)
                        _render_presented(ctx, renderer, (1366, 768), settings)
                        assert session.state_hash() == expected_hash
    finally:
        audio.close()
        scene.release()
        renderer.release()
        ctx.release()
