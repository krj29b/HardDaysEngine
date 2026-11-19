"""Standalone OpenGL 3.3 tests for the post-process shader pass chain (T13.5)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import moderngl
import numpy as np
import numpy.typing as npt
import pytest

from harddaysengine.app.audio import AudioEngine
from harddaysengine.app.game import GameSession
from harddaysengine.app.input import InputFrame
from harddaysengine.render.postfx import (
    IDENTITY_PASS,
    PassSampling,
    PostPassSpec,
    make_identity_pass,
)
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
    HardDaysRenderer,
)
from harddaysengine.render.scene import SceneRenderer, default_bitmap_font

if TYPE_CHECKING:
    from collections.abc import Sequence

pytestmark = pytest.mark.gl

_TINT_FRAG = """
#version 330 core
uniform sampler2D u_input_tex;
uniform vec2 u_in_origin;
uniform vec2 u_in_size;
uniform vec2 u_in_tex_size;
uniform vec3 u_gain;
in vec2 v_uv;
out vec4 f_color;
void main() {
    vec2 top_down = vec2(v_uv.x, 1.0 - v_uv.y) * u_in_size;
    vec2 clamped = clamp(top_down, vec2(0.5), u_in_size - vec2(0.5));
    vec2 uv = vec2(
        (u_in_origin.x + clamped.x) / u_in_tex_size.x,
        1.0 - (u_in_origin.y + clamped.y) / u_in_tex_size.y
    );
    vec4 c = texture(u_input_tex, uv);
    f_color = vec4(c.rgb * u_gain, 1.0);
}
"""


def _render_presented(
    ctx: moderngl.Context,
    renderer: HardDaysRenderer,
    window_size: tuple[int, int],
    *,
    settings: DisplaySettings,
    passes: Sequence[PostPassSpec] | None = None,
) -> npt.NDArray[np.uint8]:
    win_w, win_h = window_size
    out_tex = ctx.texture((win_w, win_h), 4)
    out_fbo = ctx.framebuffer(color_attachments=[out_tex])
    try:
        renderer.present_to_framebuffer(
            out_fbo,
            window_size,
            settings=settings,
            passes=passes,
        )
        raw = out_fbo.read(components=4)
        arr = np.frombuffer(raw, dtype=np.uint8).reshape((win_h, win_w, 4))
        return np.ascontiguousarray(arr[::-1, :, :])
    finally:
        out_fbo.release()
        out_tex.release()


def _fill_test_pattern(scene: SceneRenderer) -> None:
    palette = [((i * 37) & 0xFF, (i * 73) & 0xFF, (i * 139) & 0xFF) for i in range(32)]
    palette[0] = (0, 0, 0)
    palette[1] = (255, 255, 255)
    scene.set_palette(palette)
    yy, xx = np.indices((CANVAS_HEIGHT, CANVAS_WIDTH))
    scene.canvas[:, :] = ((xx * 3 + yy * 5 + (xx ^ yy)) & 31).astype(np.uint8)
    scene.flush_to_fbo()


@pytest.mark.parametrize(
    ("window_size", "scale_mode", "aspect", "crop", "preset"),
    [
        ((1920, 1080), ScaleMode.INTEGER, AspectMode.SQUARE, CropMode.FULL, FilterPreset.FAITHFUL),
        ((1920, 1080), ScaleMode.INTEGER, AspectMode.PAL, CropMode.FULL, FilterPreset.FAITHFUL),
        (
            (1366, 768),
            ScaleMode.SHARP_BILINEAR,
            AspectMode.PAL,
            CropMode.FULL,
            FilterPreset.FAITHFUL,
        ),
        (
            (1366, 768),
            ScaleMode.INTEGER,
            AspectMode.SQUARE,
            CropMode.PLAYFIELD,
            FilterPreset.FAITHFUL,
        ),
        ((1366, 768), ScaleMode.FIT, AspectMode.SQUARE, CropMode.FULL, FilterPreset.SCANLINES),
    ],
)
def test_faithful_and_single_identity_pass_are_byte_identical(
    window_size: tuple[int, int],
    scale_mode: ScaleMode,
    aspect: AspectMode,
    crop: CropMode,
    preset: FilterPreset,
) -> None:
    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    scene = SceneRenderer(renderer)
    try:
        _fill_test_pattern(scene)
        settings = DisplaySettings(
            filter=preset,
            scale_mode=scale_mode,
            aspect=aspect,
            crop=crop,
        )

        # Direct preset (0 intermediate passes)
        direct_img = _render_presented(ctx, renderer, window_size, settings=settings)
        assert renderer.post_chain.active_preset is preset
        assert renderer.post_chain.active_passes == ()
        assert renderer.post_chain.active_texture_count == 0

        # Explicit empty pass list (T13.2 baseline)
        zero_pass_img = _render_presented(ctx, renderer, window_size, settings=settings, passes=())
        assert np.array_equal(direct_img, zero_pass_img)

        # Single identity pass through intermediate FBO/texture
        identity_img = _render_presented(
            ctx,
            renderer,
            window_size,
            settings=settings,
            passes=(IDENTITY_PASS,),
        )
        assert renderer.post_chain.active_preset is None
        assert renderer.post_chain.active_texture_count == 1
        assert np.array_equal(direct_img, identity_img)
    finally:
        scene.release()
        renderer.release()
        ctx.release()


def test_multi_pass_chain_executes_scaled_stages_and_custom_uniforms() -> None:
    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    scene = SceneRenderer(renderer)
    try:
        palette = [(0, 0, 0)] * 32
        palette[1] = (200, 100, 50)
        scene.set_palette(palette)
        scene.canvas[:, :] = 1
        scene.flush_to_fbo()

        pass_upscale = make_identity_pass(
            "upscale2x", scale_x=2, scale_y=2, sampling=PassSampling.NEAREST
        )
        pass_tint = PostPassSpec(
            name="half_red",
            fragment_shader=_TINT_FRAG,
            scale_x=1,
            scale_y=1,
            sampling=PassSampling.LINEAR,
            uniforms=(("u_gain", (0.5, 1.0, 1.0)),),
        )
        settings = DisplaySettings(
            filter=FilterPreset.FAITHFUL,
            scale_mode=ScaleMode.INTEGER,
            aspect=AspectMode.SQUARE,
            crop=CropMode.PLAYFIELD,
        )
        img = _render_presented(
            ctx,
            renderer,
            (640, 400),
            settings=settings,
            passes=(pass_upscale, pass_tint),
        )
        assert renderer.post_chain.active_texture_count == 2
        textures = renderer.post_chain.stage_textures
        assert textures[0].size == (CANVAS_WIDTH * 2, PLAYFIELD_HEIGHT * 2)
        assert textures[1].size == (CANVAS_WIDTH * 2, PLAYFIELD_HEIGHT * 2)

        center = img[200, 320, :3]
        assert abs(int(center[0]) - 100) <= 1
        assert int(center[1]) == 100
        assert int(center[2]) == 50
    finally:
        scene.release()
        renderer.release()
        ctx.release()


def test_no_leaked_textures_across_100_preset_switches() -> None:
    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    scene = SceneRenderer(renderer)
    try:
        _fill_test_pattern(scene)
        pass_1 = make_identity_pass("p1", scale_x=1, scale_y=1)
        pass_2 = make_identity_pass("p2", scale_x=2, scale_y=2)
        settings_faithful = DisplaySettings(filter=FilterPreset.FAITHFUL)
        settings_scanlines = DisplaySettings(filter=FilterPreset.SCANLINES)

        released_refs: list[moderngl.Texture] = []
        chain = renderer.post_chain

        for idx in range(100):
            phase = idx % 4
            if phase == 0:
                _render_presented(
                    ctx, renderer, (640, 512), settings=settings_faithful, passes=(pass_1,)
                )
                assert chain.active_texture_count == 1
                released_refs.extend(chain.stage_textures)
            elif phase == 1:
                _render_presented(
                    ctx,
                    renderer,
                    (640, 512),
                    settings=settings_faithful,
                    passes=(pass_1, pass_2),
                )
                assert chain.active_texture_count == 2
                released_refs.extend(chain.stage_textures)
            elif phase == 2:
                _render_presented(ctx, renderer, (640, 512), settings=settings_scanlines)
                assert chain.active_texture_count == 0
            else:
                _render_presented(ctx, renderer, (640, 512), settings=settings_faithful)
                assert chain.active_texture_count == 0

            assert (
                chain.total_textures_created - chain.total_textures_released
                == chain.active_texture_count
            )

        # Final switch to faithful: zero live intermediate textures, all created textures released
        _render_presented(ctx, renderer, (640, 512), settings=settings_faithful)
        assert chain.active_texture_count == 0
        assert chain.total_textures_created == 75
        assert chain.total_textures_released == 75
        assert len(released_refs) == 75
        assert all(type(tex.mglo).__name__ == "InvalidObject" for tex in released_refs)
    finally:
        scene.release()
        renderer.release()
        ctx.release()


def test_post_process_passes_never_alter_session_state_hash() -> None:
    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    scene = SceneRenderer(renderer)
    audio = AudioEngine(headless=True)
    font = default_bitmap_font()
    try:
        session = GameSession(audio=audio)
        for _ in range(8):
            session.step_tick(InputFrame())
        expected_hash = session.state_hash()

        pass_2x = make_identity_pass("p2x", scale_x=2, scale_y=2, sampling=PassSampling.LINEAR)
        for passes in ((), (IDENTITY_PASS,), (IDENTITY_PASS, pass_2x)):
            for preset in FilterPreset:
                settings = DisplaySettings(filter=preset)
                session.render_to_scene(scene, font)
                _render_presented(
                    ctx,
                    renderer,
                    (960, 768),
                    settings=settings,
                    passes=passes if passes else None,
                )
                assert session.state_hash() == expected_hash
    finally:
        audio.close()
        scene.release()
        renderer.release()
        ctx.release()
