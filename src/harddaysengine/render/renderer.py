"""ModernGL OpenGL 3.3 renderer: 320x256 canvas, palette LUT, CRT pass, and overlay (T4.1, T4.2)."""

from __future__ import annotations

import math
from dataclasses import replace
from typing import TYPE_CHECKING

import moderngl
import numpy as np
import numpy.typing as npt

from harddaysengine.assets.palette import RGBColor, default_combat_palette, palette_to_rgba_array
from harddaysengine.render.overlay import (
    OverlayLine,
    build_frame_overlay,
    pack_overlay_vertex_array,
)
from harddaysengine.render.postfx import PostPassSpec, PostProcessChain, SurroundRenderer
from harddaysengine.render.presentation import (
    CrtMaskMode,
    CrtSettings,
    DisplaySettings,
    FilterPreset,
)
from harddaysengine.render.shaders import load_shader
from harddaysengine.render.viewport import (
    CANVAS_HEIGHT as CANVAS_HEIGHT,
    CANVAS_WIDTH as CANVAS_WIDTH,
    PLAYFIELD_HEIGHT as PLAYFIELD_HEIGHT,
    PLAYFIELD_Y_OFFSET as PLAYFIELD_Y_OFFSET,
    AxisSampling,
    PresentationViewport,
    SourceRect,
    compute_viewport,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from harddaysengine.assets.cel import CelFrame
    from harddaysengine.assets.hit import HitFrame

MAX_OVERLAY_LINES = 2048
_CRT_MASK_IDS: dict[CrtMaskMode, int] = {
    CrtMaskMode.NONE: 0,
    CrtMaskMode.APERTURE: 1,
    CrtMaskMode.SHADOW: 2,
}

_INDEXED_VERT = """
#version 330 core
in vec2 in_pos;
in vec2 in_uv;
out vec2 v_uv;
void main() {
    gl_Position = vec4(in_pos, 0.0, 1.0);
    v_uv = in_uv;
}
"""

_INDEXED_FRAG = """
#version 330 core
uniform sampler2D u_index_tex;
uniform sampler2D u_palette_tex;
uniform int u_transparent_zero;
in vec2 v_uv;
out vec4 f_color;
void main() {
    float raw_idx = texture(u_index_tex, v_uv).r;
    int idx = int(raw_idx * 255.0 + 0.5);
    if (u_transparent_zero == 1 && idx == 0) {
        discard;
    }
    f_color = texelFetch(u_palette_tex, ivec2(idx & 31, 0), 0);
}
"""

_LINE_VERT = """
#version 330 core
in vec2 in_pos;
in vec4 in_color;
out vec4 v_color;
void main() {
    gl_Position = vec4(in_pos, 0.0, 1.0);
    v_color = in_color;
}
"""

_LINE_FRAG = """
#version 330 core
in vec4 v_color;
out vec4 f_color;
void main() {
    f_color = v_color;
}
"""

_PRESENT_VERT = """
#version 330 core
in vec2 in_pos;
in vec2 in_uv;
out vec2 v_uv;
void main() {
    gl_Position = vec4(in_pos, 0.0, 1.0);
    v_uv = in_uv;
}
"""

_PRESENT_FRAG = """
#version 330 core
uniform sampler2D u_canvas_tex;
uniform vec2 u_src_origin;
uniform vec2 u_src_size;
uniform vec2 u_tex_size;
uniform ivec2 u_axis_mode;
uniform vec2 u_prescale;
uniform int u_scanlines;
uniform float u_dest_top;
uniform float u_scale_y;
in vec2 v_uv;
out vec4 f_color;

float sample_axis(float p, float src_dim, int mode, float prescale) {
    float coord;
    if (mode == 0) {
        coord = floor(p) + 0.5;
    } else if (mode == 2) {
        float rel = p - 0.5;
        float base = floor(rel);
        float f = clamp((rel - base - 0.5) * prescale + 0.5, 0.0, 1.0);
        coord = base + 0.5 + f;
    } else {
        coord = p;
    }
    return clamp(coord, 0.5, src_dim - 0.5);
}

void main() {
    vec2 p = vec2(v_uv.x, 1.0 - v_uv.y) * u_src_size;
    float sx = sample_axis(p.x, u_src_size.x, u_axis_mode.x, u_prescale.x);
    float sy = sample_axis(p.y, u_src_size.y, u_axis_mode.y, u_prescale.y);
    vec2 canvas_uv = vec2(
        (u_src_origin.x + sx) / u_tex_size.x,
        1.0 - (u_src_origin.y + sy) / u_tex_size.y
    );
    vec4 col = texture(u_canvas_tex, canvas_uv);
    if (u_scanlines == 1) {
        float local_y = u_dest_top - gl_FragCoord.y;
        float row_phase = fract(local_y / u_scale_y);
        float scan = (row_phase < 0.49999) ? 1.0 : 0.82;
        col.rgb *= scan;
    }
    f_color = vec4(col.rgb, 1.0);
}
"""


def _set_uniform_int(prog: moderngl.Program, name: str, value: int) -> None:
    member = prog[name]
    if isinstance(member, moderngl.Uniform):
        member.value = value


def _set_uniform_float(prog: moderngl.Program, name: str, value: float) -> None:
    member = prog[name]
    if isinstance(member, moderngl.Uniform):
        member.value = value


def _set_uniform_vec2(prog: moderngl.Program, name: str, value: tuple[float, float]) -> None:
    member = prog[name]
    if isinstance(member, moderngl.Uniform):
        member.value = value


def _set_uniform_ivec2(prog: moderngl.Program, name: str, value: tuple[int, int]) -> None:
    member = prog[name]
    if isinstance(member, moderngl.Uniform):
        member.value = value


def _resolve_axis_sampling(
    sampling: AxisSampling,
    dest_dim: int,
    src_dim: int,
    present_dim: int,
) -> AxisSampling:
    if sampling is AxisSampling.NEAREST and present_dim != src_dim and dest_dim % present_dim != 0:
        return AxisSampling.LINEAR
    return sampling


def _quad_vertices(
    x: float,
    y: float,
    w: float,
    h: float,
    canvas_w: int = CANVAS_WIDTH,
    canvas_h: int = CANVAS_HEIGHT,
) -> npt.NDArray[np.float32]:
    x0 = (x / canvas_w) * 2.0 - 1.0
    x1 = ((x + w) / canvas_w) * 2.0 - 1.0
    y0 = 1.0 - (y / canvas_h) * 2.0
    y1 = 1.0 - ((y + h) / canvas_h) * 2.0
    return np.array(
        [
            [x0, y0, 0.0, 0.0],
            [x1, y0, 1.0, 0.0],
            [x0, y1, 0.0, 1.0],
            [x0, y1, 0.0, 1.0],
            [x1, y0, 1.0, 0.0],
            [x1, y1, 1.0, 1.0],
        ],
        dtype=np.float32,
    )


class HardDaysRenderer:
    """OpenGL 3.3 core renderer for the 320x256 virtual canvas and hitbox overlay (T4.1, T4.2)."""

    def __init__(self, ctx: moderngl.Context) -> None:
        self.ctx = ctx
        self._canvas_tex = ctx.texture((CANVAS_WIDTH, CANVAS_HEIGHT), 4)
        self._canvas_tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
        self._canvas_tex.repeat_x = False
        self._canvas_tex.repeat_y = False
        self.canvas_fbo = ctx.framebuffer(color_attachments=[self._canvas_tex])

        self._palette_tex = ctx.texture((32, 1), 4)
        self._palette_tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
        self.set_palette(default_combat_palette())

        self._bg_tex = ctx.texture((CANVAS_WIDTH, PLAYFIELD_HEIGHT), 1)
        self._bg_tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
        self._bg_tex.write(np.zeros((PLAYFIELD_HEIGHT, CANVAS_WIDTH), dtype=np.uint8).tobytes())

        self._indexed_prog = ctx.program(vertex_shader=_INDEXED_VERT, fragment_shader=_INDEXED_FRAG)
        _set_uniform_int(self._indexed_prog, "u_index_tex", 0)
        _set_uniform_int(self._indexed_prog, "u_palette_tex", 1)

        bg_verts = _quad_vertices(0.0, float(PLAYFIELD_Y_OFFSET), 320.0, 200.0)
        self._bg_vbo = ctx.buffer(bg_verts.tobytes())
        self._bg_vao = ctx.vertex_array(
            self._indexed_prog,
            [(self._bg_vbo, "2f 2f", "in_pos", "in_uv")],
        )

        self._sprite_vbo = ctx.buffer(reserve=6 * 4 * 4, dynamic=True)
        self._sprite_vao = ctx.vertex_array(
            self._indexed_prog,
            [(self._sprite_vbo, "2f 2f", "in_pos", "in_uv")],
        )

        self._line_prog = ctx.program(vertex_shader=_LINE_VERT, fragment_shader=_LINE_FRAG)
        self._line_vbo = ctx.buffer(reserve=MAX_OVERLAY_LINES * 2 * 6 * 4, dynamic=True)
        self._line_vao = ctx.vertex_array(
            self._line_prog,
            [(self._line_vbo, "2f 4f", "in_pos", "in_color")],
        )

        self._crt_prog = ctx.program(vertex_shader=_PRESENT_VERT, fragment_shader=_PRESENT_FRAG)
        _set_uniform_int(self._crt_prog, "u_canvas_tex", 0)
        fs_verts = np.array(
            [
                [-1.0, -1.0, 0.0, 0.0],
                [1.0, -1.0, 1.0, 0.0],
                [-1.0, 1.0, 0.0, 1.0],
                [-1.0, 1.0, 0.0, 1.0],
                [1.0, -1.0, 1.0, 0.0],
                [1.0, 1.0, 1.0, 1.0],
            ],
            dtype=np.float32,
        )
        self._fs_vbo = ctx.buffer(fs_verts.tobytes())
        self._fs_vao = ctx.vertex_array(
            self._crt_prog,
            [(self._fs_vbo, "2f 2f", "in_pos", "in_uv")],
        )
        self._crt_shader_prog = ctx.program(
            vertex_shader=load_shader("crt.vert"),
            fragment_shader=load_shader("crt.frag"),
        )
        _set_uniform_int(self._crt_shader_prog, "u_canvas_tex", 0)
        _set_uniform_int(self._crt_shader_prog, "u_bloom_tex", 1)
        self._crt_shader_vao = ctx.vertex_array(
            self._crt_shader_prog,
            [(self._fs_vbo, "2f 2f", "in_pos", "in_uv")],
        )
        self.post_chain = PostProcessChain(ctx)
        self.surround = SurroundRenderer(ctx)

    def set_palette(self, palette: Sequence[RGBColor]) -> None:
        """Upload a 32-entry RGB888 palette to the GPU lookup texture."""
        lut = palette_to_rgba_array(palette, size=32, zero_transparent=False)
        self._palette_tex.write(lut.tobytes())

    def upload_background(
        self,
        pixels: npt.NDArray[np.uint8],
        palette: Sequence[RGBColor] | None = None,
    ) -> None:
        """Upload a (200, 320) uint8 indexed background picture and optional palette."""
        if palette is not None:
            self.set_palette(palette)
        contiguous = np.ascontiguousarray(pixels[:PLAYFIELD_HEIGHT, :CANVAS_WIDTH], dtype=np.uint8)
        self._bg_tex.write(contiguous.tobytes())

    def _draw_sprite(self, frame: CelFrame, draw_x: int, draw_y: int) -> None:
        w = frame.meta.width
        h = frame.meta.height
        if w <= 0 or h <= 0:
            return
        tex = self.ctx.texture((w, h), 1, data=np.ascontiguousarray(frame.pixels).tobytes())
        tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
        verts = _quad_vertices(float(draw_x), float(draw_y), float(w), float(h))
        self._sprite_vbo.write(verts.tobytes())
        _set_uniform_int(self._indexed_prog, "u_transparent_zero", 1)
        tex.use(location=0)
        self._palette_tex.use(location=1)
        self._sprite_vao.render(moderngl.TRIANGLES)
        tex.release()

    def draw_overlay_lines(self, lines: Sequence[OverlayLine]) -> None:
        """Render a sequence of `OverlayLine` wireframe segments onto the active FBO."""
        capped = lines[:MAX_OVERLAY_LINES]
        if not capped:
            return
        verts = pack_overlay_vertex_array(capped, CANVAS_WIDTH, CANVAS_HEIGHT)
        self._line_vbo.write(verts.tobytes())
        self.ctx.enable(moderngl.BLEND)
        self.ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA)
        self._line_vao.render(moderngl.LINES, vertices=len(capped) * 2)
        self.ctx.disable(moderngl.BLEND)

    def render_to_canvas(
        self,
        sprite_frame: CelFrame | None = None,
        sprite_x: int = 160,
        sprite_y: int = 100,
        hit_frame: HitFrame | None = None,
        *,
        show_overlay: bool = True,
        top_left: bool = False,
    ) -> list[OverlayLine]:
        """Render the background, sprite, and optional hitbox/hurtbox overlay into `canvas_fbo`."""
        self.canvas_fbo.use()
        self.ctx.viewport = (0, 0, CANVAS_WIDTH, CANVAS_HEIGHT)
        self.ctx.clear(0.0, 0.0, 0.0, 1.0)

        _set_uniform_int(self._indexed_prog, "u_transparent_zero", 0)
        self._bg_tex.use(location=0)
        self._palette_tex.use(location=1)
        self._bg_vao.render(moderngl.TRIANGLES)

        lines: list[OverlayLine] = []
        if (
            sprite_frame is not None
            and sprite_frame.meta.width > 0
            and sprite_frame.meta.height > 0
        ):
            if top_left:
                draw_x = sprite_x
                draw_y = PLAYFIELD_Y_OFFSET + sprite_y
            else:
                draw_x = sprite_x - (sprite_frame.meta.width // 2) - sprite_frame.meta.hot_x
                draw_y = PLAYFIELD_Y_OFFSET + sprite_y - (sprite_frame.meta.height // 2)
            self._draw_sprite(sprite_frame, draw_x, draw_y)
            if show_overlay:
                lines = build_frame_overlay(draw_x, draw_y, sprite_frame, hit_frame)
                self.draw_overlay_lines(lines)

        return lines

    def _present_standard(
        self,
        target: moderngl.Framebuffer,
        pv: PresentationViewport,
        present_tex: moderngl.Texture,
        present_src: SourceRect,
        present_tex_size: tuple[int, int],
        *,
        use_scanlines: bool,
    ) -> None:
        target.use()
        self.ctx.viewport = pv.gl_viewport

        sampling_x = _resolve_axis_sampling(
            pv.sampling_x, pv.dest_width, pv.source.width, present_src.width
        )
        sampling_y = _resolve_axis_sampling(
            pv.sampling_y, pv.dest_height, pv.source.height, present_src.height
        )
        uses_linear = sampling_x is not AxisSampling.NEAREST or (
            sampling_y is not AxisSampling.NEAREST
        )
        gl_filter = moderngl.LINEAR if uses_linear else moderngl.NEAREST
        present_tex.filter = (gl_filter, gl_filter)

        prescale_x = max(1, math.floor(pv.dest_width / present_src.width))
        prescale_y = max(1, math.floor(pv.dest_height / present_src.height))
        _set_uniform_vec2(
            self._crt_prog, "u_src_origin", (float(present_src.x), float(present_src.y))
        )
        _set_uniform_vec2(
            self._crt_prog, "u_src_size", (float(present_src.width), float(present_src.height))
        )
        _set_uniform_vec2(
            self._crt_prog,
            "u_tex_size",
            (float(present_tex_size[0]), float(present_tex_size[1])),
        )
        _set_uniform_ivec2(self._crt_prog, "u_axis_mode", (int(sampling_x), int(sampling_y)))
        _set_uniform_vec2(self._crt_prog, "u_prescale", (float(prescale_x), float(prescale_y)))
        _set_uniform_int(self._crt_prog, "u_scanlines", 1 if use_scanlines else 0)
        _set_uniform_float(self._crt_prog, "u_dest_top", float(pv.dest_y + pv.dest_height))
        _set_uniform_float(self._crt_prog, "u_scale_y", pv.scale_y)

        present_tex.use(location=0)
        self._fs_vao.render(moderngl.TRIANGLES)

    def _resolve_hd_source(
        self,
        pv: PresentationViewport,
        hd_tex: moderngl.Texture | None,
        hd_scale: int,
    ) -> tuple[moderngl.Texture, SourceRect, tuple[int, int], PresentationViewport]:
        if hd_tex is None:
            return (self._canvas_tex, pv.source, (CANVAS_WIDTH, CANVAS_HEIGHT), pv)
        if hd_scale < 1:
            raise ValueError(f"hd_scale must be >= 1, got {hd_scale}")
        expected_size = (CANVAS_WIDTH * hd_scale, CANVAS_HEIGHT * hd_scale)
        if hd_tex.size != expected_size:
            raise ValueError(
                f"hd_tex size {hd_tex.size} does not match expected {expected_size} "
                f"for hd_scale={hd_scale}"
            )
        hd_src = SourceRect(
            pv.source.x * hd_scale,
            pv.source.y * hd_scale,
            pv.source.width * hd_scale,
            pv.source.height * hd_scale,
        )
        return (hd_tex, hd_src, expected_size, replace(pv, source=hd_src))

    @staticmethod
    def _resolve_effective_filter(
        hd_tex: moderngl.Texture | None,
        passes: Sequence[PostPassSpec] | None,
        preset: FilterPreset,
    ) -> FilterPreset:
        if (
            hd_tex is not None
            and passes is None
            and preset in (FilterPreset.SMOOTH, FilterPreset.SCALEFX)
        ):
            return FilterPreset.FAITHFUL
        return preset

    def _present_crt(
        self,
        target: moderngl.Framebuffer,
        pv: PresentationViewport,
        crt: CrtSettings,
        bloom_tex: moderngl.Texture,
        source_tex: moderngl.Texture,
        source_rect: SourceRect,
        tex_size: tuple[int, int],
    ) -> None:
        target.use()
        self.ctx.viewport = pv.gl_viewport

        source_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        bloom_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)

        prog = self._crt_shader_prog
        _set_uniform_vec2(prog, "u_src_origin", (float(source_rect.x), float(source_rect.y)))
        _set_uniform_vec2(prog, "u_src_size", (float(source_rect.width), float(source_rect.height)))
        _set_uniform_vec2(
            prog, "u_logical_src_size", (float(pv.source.width), float(pv.source.height))
        )
        _set_uniform_vec2(prog, "u_tex_size", (float(tex_size[0]), float(tex_size[1])))
        _set_uniform_vec2(prog, "u_dest_size", (float(pv.dest_width), float(pv.dest_height)))
        _set_uniform_int(prog, "u_mask_type", _CRT_MASK_IDS[crt.mask])
        _set_uniform_float(prog, "u_mask_strength", crt.mask_strength)
        _set_uniform_float(prog, "u_scanline_strength", crt.scanlines)
        _set_uniform_float(prog, "u_curvature", crt.curvature)
        _set_uniform_float(prog, "u_bloom_strength", crt.bloom)

        source_tex.use(location=0)
        bloom_tex.use(location=1)
        self._crt_shader_vao.render(moderngl.TRIANGLES)

    def present_to_framebuffer(
        self,
        target: moderngl.Framebuffer,
        window_size: tuple[int, int],
        *,
        settings: DisplaySettings | None = None,
        scanlines: bool | None = None,
        passes: Sequence[PostPassSpec] | None = None,
        hd_tex: moderngl.Texture | None = None,
        hd_scale: int = 1,
    ) -> PresentationViewport:
        """Present the canvas (or ``hd_tex``) into ``target`` through post-process and viewport."""
        resolved = settings if settings is not None else DisplaySettings()
        use_scanlines = resolved.scanlines if scanlines is None else scanlines
        win_w, win_h = window_size
        pv = compute_viewport(
            win_w,
            win_h,
            crop=resolved.crop,
            aspect=resolved.aspect,
            scale_mode=resolved.scale_mode,
        )
        src_tex, src_rect, src_tex_size, surround_pv = self._resolve_hd_source(pv, hd_tex, hd_scale)
        effective_filter = self._resolve_effective_filter(hd_tex, passes, resolved.filter)

        if passes is None and effective_filter is FilterPreset.CRT:
            self.post_chain.set_preset(FilterPreset.CRT, crt_bloom_enabled=resolved.crt.bloom > 0.0)
            bloom_tex, _, _ = self.post_chain.execute(src_tex, src_rect, canvas_size=src_tex_size)
            self.surround.render(
                target, src_tex, surround_pv, resolved.surround, canvas_size=src_tex_size
            )
            self._present_crt(target, pv, resolved.crt, bloom_tex, src_tex, src_rect, src_tex_size)
            return pv

        if passes is None:
            self.post_chain.set_preset(effective_filter)
        else:
            self.post_chain.set_passes(passes)
        present_tex, present_src, present_tex_size = self.post_chain.execute(
            src_tex, src_rect, canvas_size=src_tex_size
        )
        self.surround.render(
            target, src_tex, surround_pv, resolved.surround, canvas_size=src_tex_size
        )
        self._present_standard(
            target,
            pv,
            present_tex,
            present_src,
            present_tex_size,
            use_scanlines=use_scanlines,
        )
        return pv

    def present_to_screen(
        self,
        viewport: tuple[int, int, int, int],
        *,
        settings: DisplaySettings | None = None,
        scanlines: bool | None = None,
        passes: Sequence[PostPassSpec] | None = None,
        hd_tex: moderngl.Texture | None = None,
        hd_scale: int = 1,
    ) -> PresentationViewport:
        """Blit the canvas (or ``hd_tex``) to the default screen framebuffer (§13.2-§13.4)."""
        return self.present_to_framebuffer(
            self.ctx.screen,
            (viewport[2], viewport[3]),
            settings=settings,
            scanlines=scanlines,
            passes=passes,
            hd_tex=hd_tex,
            hd_scale=hd_scale,
        )

    def blit_screen_texture(
        self,
        target: moderngl.Framebuffer,
        window_size: tuple[int, int],
        texture: moderngl.Texture,
        rect: tuple[int, int, int, int],
    ) -> None:
        """Draw an RGB texture on the presentation target after filtering (§13.10)."""
        width, height = texture.size
        source = SourceRect(0, 0, width, height)
        viewport = PresentationViewport(
            *window_size,
            source,
            *rect,
            rect[2] / width,
            rect[3] / height,
            1,
            1,
            AxisSampling.NEAREST,
            AxisSampling.NEAREST,
        )
        self._present_standard(target, viewport, texture, source, texture.size, use_scanlines=False)

    def read_canvas_rgba(self) -> npt.NDArray[np.uint8]:
        """Read back the 320x256 virtual canvas FBO as a top-down (256, 320, 4) uint8 RGBA array."""
        raw = self.canvas_fbo.read(components=4)
        arr = np.frombuffer(raw, dtype=np.uint8).reshape((CANVAS_HEIGHT, CANVAS_WIDTH, 4))
        return np.ascontiguousarray(arr[::-1, :, :])

    def release(self) -> None:
        """Release all GPU resources owned by this renderer."""
        self.surround.release()
        self.post_chain.release()
        self._crt_shader_vao.release()
        self._crt_shader_prog.release()
        self._fs_vao.release()
        self._fs_vbo.release()
        self._crt_prog.release()
        self._line_vao.release()
        self._line_vbo.release()
        self._line_prog.release()
        self._sprite_vao.release()
        self._sprite_vbo.release()
        self._bg_vao.release()
        self._bg_vbo.release()
        self._indexed_prog.release()
        self._bg_tex.release()
        self._palette_tex.release()
        self.canvas_fbo.release()
        self._canvas_tex.release()
