"""Standalone OpenGL 3.3 tests for the clean-room CRT presentation preset (T13.6, §13.4.1)."""

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
    CrtMaskMode,
    CrtSettings,
    DisplaySettings,
    FilterPreset,
    ScaleMode,
)
from harddaysengine.render.renderer import (
    CANVAS_WIDTH,
    HardDaysRenderer,
)
from harddaysengine.render.scene import SceneRenderer, default_bitmap_font

if TYPE_CHECKING:
    from harddaysengine.render.viewport import PresentationViewport

pytestmark = pytest.mark.gl


def _render_crt(
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


def _fill_solid(scene: SceneRenderer, rgb: tuple[int, int, int]) -> None:
    palette = [(0, 0, 0)] * 32
    palette[1] = rgb
    scene.set_palette(palette)
    scene.canvas[:, :] = 1
    scene.flush_to_fbo()


def test_aperture_grille_shadow_mask_and_none_patterns() -> None:
    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    scene = SceneRenderer(renderer)
    try:
        _fill_solid(scene, (180, 180, 180))

        # 1. Aperture grille: 3-column RGB stripes across consecutive output columns
        ap_settings = DisplaySettings(
            filter=FilterPreset.CRT,
            scale_mode=ScaleMode.INTEGER,
            crt=CrtSettings(
                mask=CrtMaskMode.APERTURE,
                mask_strength=0.45,
                scanlines=0.0,
                curvature=0.0,
                bloom=0.0,
            ),
        )
        ap_img, pv = _render_crt(ctx, renderer, (1920, 1080), ap_settings)
        cy = pv.window_height // 2
        cx0 = pv.dest_x + pv.dest_width // 2
        # Align cx0 to column where (x % 3) == 0
        cx0 += (3 - (cx0 % 3)) % 3
        p_r = ap_img[cy, cx0, :3].astype(int)
        p_g = ap_img[cy, cx0 + 1, :3].astype(int)
        p_b = ap_img[cy, cx0 + 2, :3].astype(int)
        assert p_r[0] > p_r[1] + 20 and p_r[1] == p_r[2]
        assert p_g[1] > p_g[0] + 20 and p_g[0] == p_g[2]
        assert p_b[2] > p_b[0] + 20 and p_b[0] == p_b[1]
        # Repeats vertically identically for aperture grille
        assert np.array_equal(ap_img[cy, cx0 : cx0 + 6, :3], ap_img[cy + 1, cx0 : cx0 + 6, :3])

        # 2. Shadow / slot mask: staggered phase shifts every 2 output rows + slot bridge
        sh_settings = DisplaySettings(
            filter=FilterPreset.CRT,
            scale_mode=ScaleMode.INTEGER,
            crt=CrtSettings(
                mask=CrtMaskMode.SHADOW,
                mask_strength=0.45,
                scanlines=0.0,
                curvature=0.0,
                bloom=0.0,
            ),
        )
        sh_img, _ = _render_crt(ctx, renderer, (1920, 1080), sh_settings)
        # Check two rows separated by 2 pixels have shifted RGB peaks
        row_a = sh_img[cy, cx0 : cx0 + 3, :3].astype(int)
        row_b = sh_img[cy + 2, cx0 : cx0 + 3, :3].astype(int)
        peak_a = [int(np.argmax(row_a[i])) for i in range(3)]
        peak_b = [int(np.argmax(row_b[i])) for i in range(3)]
        assert len(set(peak_a)) == 3
        assert len(set(peak_b)) == 3
        assert peak_a != peak_b

        # 3. Mask NONE: uniform across adjacent columns
        none_settings = DisplaySettings(
            filter=FilterPreset.CRT,
            scale_mode=ScaleMode.INTEGER,
            crt=CrtSettings(
                mask=CrtMaskMode.NONE,
                mask_strength=0.45,
                scanlines=0.0,
                curvature=0.0,
                bloom=0.0,
            ),
        )
        none_img, _ = _render_crt(ctx, renderer, (1920, 1080), none_settings)
        strip = none_img[cy, cx0 : cx0 + 6, :3]
        assert int(strip.max() - strip.min()) == 0
    finally:
        scene.release()
        renderer.release()
        ctx.release()


def test_barrel_curvature_and_corner_mask_are_optional() -> None:
    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    scene = SceneRenderer(renderer)
    try:
        _fill_solid(scene, (200, 200, 200))

        curved_settings = DisplaySettings(
            filter=FilterPreset.CRT,
            scale_mode=ScaleMode.FIT,
            aspect=AspectMode.PAL,
            crt=CrtSettings(
                mask=CrtMaskMode.NONE,
                scanlines=0.0,
                curvature=0.10,
                bloom=0.0,
            ),
        )
        curved_img, pv = _render_crt(ctx, renderer, (1920, 1080), curved_settings)
        top_y = pv.window_height - (pv.dest_y + pv.dest_height)
        bot_y = pv.window_height - pv.dest_y - 1
        left_x = pv.dest_x
        right_x = pv.dest_x + pv.dest_width - 1

        # All four extreme corners inside the viewport are masked black by curvature + bezel
        assert np.all(curved_img[top_y + 1, left_x + 1, :3] == 0)
        assert np.all(curved_img[top_y + 1, right_x - 1, :3] == 0)
        assert np.all(curved_img[bot_y - 1, left_x + 1, :3] == 0)
        assert np.all(curved_img[bot_y - 1, right_x - 1, :3] == 0)
        # Centre remains bright
        assert int(curved_img[pv.window_height // 2, pv.window_width // 2, 0]) > 180

        # Flat glass (curvature = 0.0): corners are not masked
        flat_settings = DisplaySettings(
            filter=FilterPreset.CRT,
            scale_mode=ScaleMode.FIT,
            aspect=AspectMode.PAL,
            crt=CrtSettings(
                mask=CrtMaskMode.NONE,
                scanlines=0.0,
                curvature=0.0,
                bloom=0.0,
            ),
        )
        flat_img, _ = _render_crt(ctx, renderer, (1920, 1080), flat_settings)
        assert int(flat_img[top_y + 1, left_x + 1, 0]) > 180
        assert int(flat_img[bot_y - 1, right_x - 1, 0]) > 180
    finally:
        scene.release()
        renderer.release()
        ctx.release()


def test_scanline_beam_widens_with_brightness_and_has_no_banding() -> None:
    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    scene = SceneRenderer(renderer)
    try:
        palette = [(0, 0, 0)] * 32
        palette[1] = (240, 240, 240)  # bright half
        palette[2] = (90, 90, 90)  # darker half
        scene.set_palette(palette)
        scene.canvas[:, : CANVAS_WIDTH // 2] = 1
        scene.canvas[:, CANVAS_WIDTH // 2 :] = 2
        scene.flush_to_fbo()

        settings = DisplaySettings(
            filter=FilterPreset.CRT,
            scale_mode=ScaleMode.INTEGER,
            crt=CrtSettings(
                mask=CrtMaskMode.NONE,
                scanlines=0.75,
                curvature=0.0,
                bloom=0.0,
            ),
        )
        img, pv = _render_crt(ctx, renderer, (1920, 1080), settings)
        top_y = pv.window_height - (pv.dest_y + pv.dest_height)
        scale_y = round(pv.scale_y)
        assert scale_y == 4

        bright_x = pv.dest_x + pv.dest_width // 4
        dark_x = pv.dest_x + (3 * pv.dest_width) // 4
        # Sample 16 consecutive source rows in the vertical middle
        y0 = top_y + 80 * scale_y
        bright_col = img[y0 : y0 + 16 * scale_y, bright_x, 0].astype(float)
        dark_col = img[y0 : y0 + 16 * scale_y, dark_x, 0].astype(float)

        # Periodicity: every source row repeats the same 4-pixel scanline pattern (no banding)
        bright_rows = bright_col.reshape(16, scale_y)
        dark_rows = dark_col.reshape(16, scale_y)
        assert float(np.max(np.ptp(bright_rows, axis=0))) <= 1.0
        assert float(np.max(np.ptp(dark_rows, axis=0))) <= 1.0

        # Scanline modulation is clearly present and deeper on dark pixels than bright pixels
        bright_ratio = float(bright_rows[0].min() / bright_rows[0].max())
        dark_ratio = float(dark_rows[0].min() / dark_rows[0].max())
        assert bright_ratio < 0.92
        assert dark_ratio < bright_ratio
    finally:
        scene.release()
        renderer.release()
        ctx.release()


def test_bloom_creates_halo_and_disables_cleanly_at_zero() -> None:
    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    scene = SceneRenderer(renderer)
    try:
        palette = [(0, 0, 0)] * 32
        palette[1] = (255, 255, 255)
        scene.set_palette(palette)
        scene.canvas[:, :] = 0
        # Bright 24x24 square in the centre of the 320x256 canvas
        scene.canvas[116:140, 148:172] = 1
        scene.flush_to_fbo()

        bloom_on = DisplaySettings(
            filter=FilterPreset.CRT,
            scale_mode=ScaleMode.INTEGER,
            crt=CrtSettings(
                mask=CrtMaskMode.NONE,
                scanlines=0.0,
                curvature=0.0,
                bloom=0.35,
            ),
        )
        img_on, pv = _render_crt(ctx, renderer, (1920, 1080), bloom_on)
        assert renderer.post_chain.active_texture_count == 2
        cy = pv.window_height // 2
        # Probe 2 source pixels (= 8 output pixels) outside the right edge of the white square
        probe_x = pv.dest_x + round((172 + 2) * pv.scale_x)
        assert int(img_on[cy, probe_x, 0]) > 15

        bloom_off = DisplaySettings(
            filter=FilterPreset.CRT,
            scale_mode=ScaleMode.INTEGER,
            crt=CrtSettings(
                mask=CrtMaskMode.NONE,
                scanlines=0.0,
                curvature=0.0,
                bloom=0.0,
            ),
        )
        img_off, _ = _render_crt(ctx, renderer, (1920, 1080), bloom_off)
        assert renderer.post_chain.active_texture_count == 0
        assert int(img_off[cy, probe_x, 0]) == 0
    finally:
        scene.release()
        renderer.release()
        ctx.release()


def test_crt_preset_switches_leak_zero_textures_and_preserve_state_hash() -> None:
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
            DisplaySettings(filter=FilterPreset.FAITHFUL),
            DisplaySettings(filter=FilterPreset.SCANLINES),
            DisplaySettings(
                filter=FilterPreset.CRT,
                crt=CrtSettings(mask=CrtMaskMode.APERTURE, bloom=0.22),
            ),
            DisplaySettings(
                filter=FilterPreset.CRT,
                crop=CropMode.PLAYFIELD,
                aspect=AspectMode.PAL,
                crt=CrtSettings(mask=CrtMaskMode.SHADOW, bloom=0.0),
            ),
        )
        chain = renderer.post_chain
        for idx in range(100):
            cfg = configs[idx % len(configs)]
            _render_crt(ctx, renderer, (1366, 768), cfg)
            assert (
                chain.total_textures_created - chain.total_textures_released
                == chain.active_texture_count
            )
            assert session.state_hash() == expected_hash

        # Switching back to faithful releases all bloom textures
        _render_crt(ctx, renderer, (1366, 768), DisplaySettings(filter=FilterPreset.FAITHFUL))
        assert chain.active_texture_count == 0
        assert chain.total_textures_created == chain.total_textures_released
    finally:
        audio.close()
        scene.release()
        renderer.release()
        ctx.release()
