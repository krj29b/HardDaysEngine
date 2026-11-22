"""Real pygame-ce + ModernGL 3.3 window fullscreen round-trip tests (T13.3)."""

from __future__ import annotations

import moderngl
import numpy as np
import pygame
import pytest

from harddaysengine.app.display_preview import DisplayPreview
from harddaysengine.app.window import WindowController
from harddaysengine.render.display_panel import DisplayPanel
from harddaysengine.render.presentation import DisplaySettings, FilterPreset, FullscreenMode
from harddaysengine.render.renderer import CANVAS_HEIGHT, CANVAS_WIDTH, HardDaysRenderer
from harddaysengine.render.scene import SceneRenderer, default_bitmap_font

pytestmark = pytest.mark.gl


@pytest.mark.parametrize("target_mode", [FullscreenMode.DESKTOP, FullscreenMode.EXCLUSIVE])
def test_fullscreen_toggle_round_trip_preserves_gl_context_and_textures(
    target_mode: FullscreenMode,
) -> None:
    pygame.init()
    try:
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 3)
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 3)
        pygame.display.gl_set_attribute(
            pygame.GL_CONTEXT_PROFILE_MASK, pygame.GL_CONTEXT_PROFILE_CORE
        )
        window = WindowController((640, 512), FullscreenMode.OFF)
        window.open()

        ctx = moderngl.create_context(require=330)
        renderer = HardDaysRenderer(ctx)
        scene = SceneRenderer(renderer)
        try:
            palette = [(0, 0, 0)] * 32
            palette[1] = (204, 51, 102)
            palette[2] = (34, 187, 238)
            scene.set_palette(palette)
            yy, xx = np.indices((CANVAS_HEIGHT, CANVAS_WIDTH))
            scene.canvas[:, :] = np.where((xx + yy) % 2 == 0, 1, 2).astype(np.uint8)
            scene.flush_to_fbo()

            expected_canvas = renderer.read_canvas_rgba().copy()
            settings = DisplaySettings(scale=2, fullscreen=target_mode)

            # Enter target fullscreen mode directly via controller
            window = WindowController((640, 512), target_mode)
            window.open()
            pygame.event.pump()
            fs_w, fs_h = pygame.display.get_window_size()
            assert fs_w > 0 and fs_h > 0
            renderer.present_to_screen((0, 0, fs_w, fs_h), settings=settings)
            pygame.display.flip()
            assert np.array_equal(renderer.read_canvas_rgba(), expected_canvas)

            # Round-trip back to windowed mode
            assert window.toggle_fullscreen() is FullscreenMode.OFF
            pygame.event.pump()
            win_w, win_h = pygame.display.get_window_size()
            assert (win_w, win_h) == (640, 512)
            renderer.present_to_screen((0, 0, win_w, win_h), settings=settings)
            pygame.display.flip()
            assert np.array_equal(renderer.read_canvas_rgba(), expected_canvas)
        finally:
            scene.release()
            renderer.release()
    finally:
        pygame.quit()


@pytest.mark.parametrize("size", [(640, 512), (1920, 1080)])
def test_display_panel_draws_after_crt_without_changing_the_canvas(size: tuple[int, int]) -> None:
    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    scene = SceneRenderer(renderer)
    panel = DisplayPanel(renderer, default_bitmap_font())
    output = ctx.simple_framebuffer(size)
    try:
        scene.clear_canvas(7)
        scene.flush_to_fbo()
        original_canvas = renderer.read_canvas_rgba()
        settings = DisplaySettings(filter=FilterPreset.CRT)
        preview = DisplayPreview(settings.filter, settings.crt)
        renderer.present_to_framebuffer(output, size, settings=settings)
        without_panel = np.frombuffer(output.read(components=3), dtype=np.uint8).reshape(
            size[1], size[0], 3
        )

        panel.draw(output, size, preview.rows(), preview.selected)
        with_panel = np.frombuffer(output.read(components=3), dtype=np.uint8).reshape(
            size[1], size[0], 3
        )
        np.testing.assert_array_equal(
            without_panel[:, : size[0] // 2], with_panel[:, : size[0] // 2]
        )
        assert np.any(np.all(with_panel == (255, 214, 131), axis=2))
        np.testing.assert_array_equal(renderer.read_canvas_rgba(), original_canvas)
    finally:
        output.release()
        panel.release()
        scene.release()
        renderer.release()
        ctx.release()
