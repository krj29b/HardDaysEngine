"""Standalone OpenGL 3.3 tests for optional widescreen surround (T13.8, ARCHITECTURE.md §13.5)."""

from __future__ import annotations

from typing import TYPE_CHECKING

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
    SurroundMode,
    SurroundSettings,
)
from harddaysengine.render.renderer import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    PLAYFIELD_HEIGHT,
    PLAYFIELD_Y_OFFSET,
    HardDaysRenderer,
)
from harddaysengine.render.scene import SceneRenderer, default_bitmap_font

if TYPE_CHECKING:
    from harddaysengine.render.viewport import PresentationViewport

pytestmark = pytest.mark.gl


def _render_present(
    ctx: moderngl.Context,
    renderer: HardDaysRenderer,
    window_size: tuple[int, int],
    settings: DisplaySettings,
) -> tuple[npt.NDArray[np.uint8], PresentationViewport]:
    win_w, win_h = window_size
    out_tex = ctx.texture((win_w, win_h), 4)
    out_fbo = ctx.framebuffer(color_attachments=[out_tex])
    try:
        pv = renderer.present_to_framebuffer(out_fbo, window_size, settings=settings)
        raw = out_fbo.read(components=4)
        arr = np.frombuffer(raw, dtype=np.uint8).reshape((win_h, win_w, 4))
        return np.ascontiguousarray(arr[::-1, :, :]), pv
    finally:
        out_fbo.release()
        out_tex.release()


def _game_rect_slice(
    img: npt.NDArray[np.uint8],
    pv: PresentationViewport,
) -> npt.NDArray[np.uint8]:
    top_y = pv.window_height - (pv.dest_y + pv.dest_height)
    return img[top_y : top_y + pv.dest_height, pv.dest_x : pv.dest_x + pv.dest_width, :3]


def _fill_split_pattern(scene: SceneRenderer) -> None:
    palette = [(0, 0, 0)] * 32
    palette[1] = (240, 20, 20)
    palette[2] = (180, 10, 10)
    palette[3] = (20, 60, 240)
    palette[4] = (10, 30, 180)
    scene.set_palette(palette)
    ys, xs = np.indices((CANVAS_HEIGHT, CANVAS_WIDTH))
    checker = (xs + ys) & 1
    scene.canvas[:, : CANVAS_WIDTH // 2] = np.where(
        checker[:, : CANVAS_WIDTH // 2] == 0, 1, 2
    ).astype(np.uint8)
    scene.canvas[:, CANVAS_WIDTH // 2 :] = np.where(
        checker[:, CANVAS_WIDTH // 2 :] == 0, 3, 4
    ).astype(np.uint8)
    scene.flush_to_fbo()


def test_surround_off_is_byte_identical_and_game_image_never_changes() -> None:
    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    scene = SceneRenderer(renderer)
    try:
        _fill_split_pattern(scene)

        for preset, scale_mode, crop, aspect in (
            (FilterPreset.FAITHFUL, ScaleMode.INTEGER, CropMode.FULL, AspectMode.SQUARE),
            (FilterPreset.SCANLINES, ScaleMode.SHARP_BILINEAR, CropMode.PLAYFIELD, AspectMode.PAL),
            (FilterPreset.CRT, ScaleMode.FIT, CropMode.PLAYFIELD, AspectMode.PAL),
            (FilterPreset.SMOOTH, ScaleMode.INTEGER, CropMode.FULL, AspectMode.SQUARE),
        ):
            default_cfg = DisplaySettings(
                filter=preset, scale_mode=scale_mode, crop=crop, aspect=aspect
            )
            off_cfg = DisplaySettings(
                filter=preset,
                scale_mode=scale_mode,
                crop=crop,
                aspect=aspect,
                surround=SurroundSettings(mode=SurroundMode.OFF),
            )
            solid_cfg = DisplaySettings(
                filter=preset,
                scale_mode=scale_mode,
                crop=crop,
                aspect=aspect,
                surround=SurroundSettings(mode=SurroundMode.SOLID, color=(20, 32, 48)),
            )
            blur_cfg = DisplaySettings(
                filter=preset,
                scale_mode=scale_mode,
                crop=crop,
                aspect=aspect,
                surround=SurroundSettings(mode=SurroundMode.BLUR, brightness=0.30),
            )

            img_default, pv = _render_present(ctx, renderer, (2560, 1080), default_cfg)
            img_off, pv_off = _render_present(ctx, renderer, (2560, 1080), off_cfg)
            assert pv == pv_off
            assert pv.dest_x > 0
            assert np.array_equal(img_default, img_off)
            # Side bars are pure black in off mode
            assert int(img_off[:, : pv.dest_x, :3].max()) == 0
            assert int(img_off[:, pv.dest_x + pv.dest_width :, :3].max()) == 0
            assert renderer.surround.active_texture_count == 0

            # Game image inside the letterbox is byte-identical under solid and blur surround
            img_solid, _ = _render_present(ctx, renderer, (2560, 1080), solid_cfg)
            img_blur, _ = _render_present(ctx, renderer, (2560, 1080), blur_cfg)
            assert np.array_equal(_game_rect_slice(img_off, pv), _game_rect_slice(img_solid, pv))
            assert np.array_equal(_game_rect_slice(img_off, pv), _game_rect_slice(img_blur, pv))
    finally:
        scene.release()
        renderer.release()
        ctx.release()


def test_surround_solid_fills_side_bars_with_exact_rgb_and_zero_textures() -> None:
    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    scene = SceneRenderer(renderer)
    try:
        _fill_split_pattern(scene)
        cfg = DisplaySettings(
            filter=FilterPreset.FAITHFUL,
            scale_mode=ScaleMode.FIT,
            crop=CropMode.PLAYFIELD,
            aspect=AspectMode.PAL,
            surround=SurroundSettings(mode=SurroundMode.SOLID, color=(24, 48, 96)),
        )
        img, pv = _render_present(ctx, renderer, (2560, 1080), cfg)
        assert pv.dest_x > 100
        assert renderer.surround.active_texture_count == 0
        assert renderer.surround.total_textures_created == 0

        left_bar = img[:, : pv.dest_x, :3]
        right_bar = img[:, pv.dest_x + pv.dest_width :, :3]
        assert np.all(left_bar == np.array([24, 48, 96], dtype=np.uint8))
        assert np.all(right_bar == np.array([24, 48, 96], dtype=np.uint8))
    finally:
        scene.release()
        renderer.release()
        ctx.release()


def test_surround_blur_fills_side_bars_smoothly_and_scales_with_brightness() -> None:
    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    scene = SceneRenderer(renderer)
    try:
        _fill_split_pattern(scene)
        base_kwargs = {
            "filter": FilterPreset.FAITHFUL,
            "scale_mode": ScaleMode.FIT,
            "crop": CropMode.PLAYFIELD,
            "aspect": AspectMode.PAL,
        }
        img_low, pv = _render_present(
            ctx,
            renderer,
            (2560, 1080),
            DisplaySettings(
                **base_kwargs,
                surround=SurroundSettings(mode=SurroundMode.BLUR, brightness=0.25),
            ),
        )
        assert renderer.surround.active_texture_count == 2
        mid_y = pv.window_height // 2
        left_x = pv.dest_x // 2
        right_x = pv.dest_x + pv.dest_width + (pv.window_width - (pv.dest_x + pv.dest_width)) // 2

        left_px = img_low[mid_y, left_x, :3].astype(int)
        right_px = img_low[mid_y, right_x, :3].astype(int)
        # Left side bar picks up red from left half; right side bar picks up blue from right half
        assert left_px[0] > left_px[2] + 20
        assert right_px[2] > right_px[0] + 20
        # Darkened well below source peak (240)
        assert int(img_low[:, : pv.dest_x, :3].max()) < 90
        assert int(img_low[:, pv.dest_x + pv.dest_width :, :3].max()) < 90

        # Smoothness: no high-frequency checkerboard remaining in blurred side bar
        patch = img_low[mid_y - 40 : mid_y + 40, left_x - 40 : left_x + 40, 0].astype(int)
        assert int(np.max(np.abs(np.diff(patch, axis=1)))) <= 3
        assert int(np.max(np.abs(np.diff(patch, axis=0)))) <= 3

        # Monotonic brightness scaling, and exact zero at brightness = 0.0
        img_high, _ = _render_present(
            ctx,
            renderer,
            (2560, 1080),
            DisplaySettings(
                **base_kwargs,
                surround=SurroundSettings(mode=SurroundMode.BLUR, brightness=0.60),
            ),
        )
        assert int(img_high[mid_y, left_x, 0]) > int(img_low[mid_y, left_x, 0]) + 25

        img_zero, _ = _render_present(
            ctx,
            renderer,
            (2560, 1080),
            DisplaySettings(
                **base_kwargs,
                surround=SurroundSettings(mode=SurroundMode.BLUR, brightness=0.0),
            ),
        )
        assert int(img_zero[:, : pv.dest_x, :3].max()) == 0
        assert int(img_zero[:, pv.dest_x + pv.dest_width :, :3].max()) == 0

        # Pure black frame produces exact black side bars (no dither noise)
        scene.set_palette([(0, 0, 0)] * 32)
        scene.canvas[:, :] = 0
        scene.flush_to_fbo()
        img_black, _ = _render_present(
            ctx,
            renderer,
            (2560, 1080),
            DisplaySettings(
                **base_kwargs,
                surround=SurroundSettings(mode=SurroundMode.BLUR, brightness=0.35),
            ),
        )
        assert int(img_black[:, : pv.dest_x, :3].max()) == 0
        assert int(img_black[:, pv.dest_x + pv.dest_width :, :3].max()) == 0
    finally:
        scene.release()
        renderer.release()
        ctx.release()


def test_surround_blur_respects_playfield_crop_without_overscan_bleed() -> None:
    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    scene = SceneRenderer(renderer)
    try:
        palette = [(0, 0, 0)] * 32
        palette[1] = (255, 0, 255)  # bright magenta in overscan borders
        palette[2] = (0, 210, 0)  # pure green in playfield
        scene.set_palette(palette)
        scene.canvas[:, :] = 1
        scene.canvas[PLAYFIELD_Y_OFFSET : PLAYFIELD_Y_OFFSET + PLAYFIELD_HEIGHT, :] = 2
        scene.flush_to_fbo()

        cfg = DisplaySettings(
            filter=FilterPreset.FAITHFUL,
            scale_mode=ScaleMode.FIT,
            crop=CropMode.PLAYFIELD,
            aspect=AspectMode.PAL,
            surround=SurroundSettings(mode=SurroundMode.BLUR, brightness=0.35),
        )
        img, pv = _render_present(ctx, renderer, (2560, 1080), cfg)
        left_bar = img[:, : pv.dest_x, :3]
        assert int(left_bar[:, :, 0].max()) <= 1
        assert int(left_bar[:, :, 2].max()) <= 1
        assert int(left_bar[:, :, 1].max()) > 30
    finally:
        scene.release()
        renderer.release()
        ctx.release()


def test_surround_inactive_when_window_not_wider_than_letterbox() -> None:
    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    scene = SceneRenderer(renderer)
    try:
        _fill_split_pattern(scene)
        # First activate blur on an ultrawide window to allocate buffers
        _render_present(
            ctx,
            renderer,
            (2560, 1080),
            DisplaySettings(surround=SurroundSettings(mode=SurroundMode.BLUR)),
        )
        assert renderer.surround.active_texture_count == 2

        # Narrow/tall window (640x800): dest_width == 640 (no side bars, only top/bottom letterbox)
        for mode in (SurroundMode.SOLID, SurroundMode.BLUR):
            cfg = DisplaySettings(
                scale_mode=ScaleMode.FIT,
                crop=CropMode.FULL,
                aspect=AspectMode.SQUARE,
                surround=SurroundSettings(mode=mode, color=(80, 90, 100), brightness=0.5),
            )
            img, pv = _render_present(ctx, renderer, (640, 800), cfg)
            assert pv.dest_width == 640
            assert pv.dest_y > 0
            assert renderer.surround.active_texture_count == 0
            top_y = pv.window_height - (pv.dest_y + pv.dest_height)
            assert int(img[:top_y, :, :3].max()) == 0
            assert int(img[top_y + pv.dest_height :, :, :3].max()) == 0
    finally:
        scene.release()
        renderer.release()
        ctx.release()


def test_surround_switches_leak_zero_textures_and_preserve_state_hash() -> None:
    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    scene = SceneRenderer(renderer)
    audio = AudioEngine(headless=True)
    font = default_bitmap_font()
    try:
        session = GameSession(audio=audio)
        for _ in range(6):
            session.step_tick(InputFrame())
        expected_hash = session.state_hash()
        session.render_to_scene(scene, font)

        configs = (
            (
                (2560, 1080),
                DisplaySettings(
                    filter=FilterPreset.FAITHFUL,
                    surround=SurroundSettings(mode=SurroundMode.OFF),
                ),
            ),
            (
                (2560, 1080),
                DisplaySettings(
                    filter=FilterPreset.CRT,
                    surround=SurroundSettings(mode=SurroundMode.BLUR, brightness=0.3),
                ),
            ),
            (
                (2560, 1080),
                DisplaySettings(
                    filter=FilterPreset.SMOOTH,
                    surround=SurroundSettings(mode=SurroundMode.SOLID, color=(20, 24, 32)),
                ),
            ),
            (
                (640, 800),
                DisplaySettings(
                    filter=FilterPreset.FAITHFUL,
                    surround=SurroundSettings(mode=SurroundMode.BLUR),
                ),
            ),
        )
        surround = renderer.surround
        for idx in range(100):
            win_size, cfg = configs[idx % len(configs)]
            _render_present(ctx, renderer, win_size, cfg)
            assert (
                surround.total_textures_created - surround.total_textures_released
                == surround.active_texture_count
            )
            assert session.state_hash() == expected_hash

        _render_present(
            ctx,
            renderer,
            (2560, 1080),
            DisplaySettings(surround=SurroundSettings(mode=SurroundMode.OFF)),
        )
        assert surround.active_texture_count == 0
        assert surround.total_textures_created == surround.total_textures_released
    finally:
        audio.close()
        scene.release()
        renderer.release()
        ctx.release()
