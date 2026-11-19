"""Ordered RGB post-processing pass chain and preset registry (T13.5, ARCHITECTURE.md §13.4).

Runs after the indexed-canvas palette lookup on the 320x256 RGB canvas texture, crops to the
selected source region, executes zero or more intermediate shader passes into offscreen RGBA
textures, and hands the resulting texture to the §13.2 viewport scaler. Never mutates simulation
state, the indexed canvas, or the palette.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

import moderngl
import numpy as np

from harddaysengine.render.presentation import FilterPreset
from harddaysengine.render.viewport import CANVAS_HEIGHT, CANVAS_WIDTH, SourceRect

if TYPE_CHECKING:
    from collections.abc import Sequence

type UniformValue = (
    int
    | float
    | tuple[float, float]
    | tuple[float, float, float]
    | tuple[float, float, float, float]
    | tuple[int, int]
)


class PassSampling(StrEnum):
    """Input-texture filtering mode for one post-process shader pass (§13.4)."""

    NEAREST = "nearest"
    LINEAR = "linear"


DEFAULT_POST_VERT = """
#version 330 core
in vec2 in_pos;
in vec2 in_uv;
out vec2 v_uv;
void main() {
    gl_Position = vec4(in_pos, 0.0, 1.0);
    v_uv = in_uv;
}
"""

IDENTITY_POST_FRAG = """
#version 330 core
uniform sampler2D u_input_tex;
uniform vec2 u_in_origin;
uniform vec2 u_in_size;
uniform vec2 u_in_tex_size;
in vec2 v_uv;
out vec4 f_color;
void main() {
    vec2 top_down = vec2(v_uv.x, 1.0 - v_uv.y) * u_in_size;
    vec2 clamped = clamp(top_down, vec2(0.5), u_in_size - vec2(0.5));
    vec2 uv = vec2(
        (u_in_origin.x + clamped.x) / u_in_tex_size.x,
        1.0 - (u_in_origin.y + clamped.y) / u_in_tex_size.y
    );
    f_color = texture(u_input_tex, uv);
}
"""


@dataclass(slots=True, frozen=True)
class PostPassSpec:
    """Declarative specification for one intermediate post-process shader pass (§13.4)."""

    name: str
    fragment_shader: str
    vertex_shader: str = DEFAULT_POST_VERT
    scale_x: int = 1
    scale_y: int = 1
    sampling: PassSampling = PassSampling.NEAREST
    uniforms: tuple[tuple[str, UniformValue], ...] = ()

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("PostPassSpec.name must be non-empty")
        if not self.fragment_shader:
            raise ValueError("PostPassSpec.fragment_shader must be non-empty")
        if self.scale_x < 1 or self.scale_y < 1:
            msg = f"PostPassSpec scale must be >= 1, got {self.scale_x}x{self.scale_y}"
            raise ValueError(msg)


IDENTITY_PASS = PostPassSpec(
    name="identity",
    fragment_shader=IDENTITY_POST_FRAG,
    scale_x=1,
    scale_y=1,
    sampling=PassSampling.NEAREST,
)


def make_identity_pass(
    name: str = "identity",
    *,
    scale_x: int = 1,
    scale_y: int = 1,
    sampling: PassSampling = PassSampling.NEAREST,
) -> PostPassSpec:
    """Build a pass-through :class:`PostPassSpec` with the requested scale and sampling."""
    return PostPassSpec(
        name=name,
        fragment_shader=IDENTITY_POST_FRAG,
        scale_x=scale_x,
        scale_y=scale_y,
        sampling=sampling,
    )


_PRESET_PASSES: dict[FilterPreset, tuple[PostPassSpec, ...]] = {
    FilterPreset.FAITHFUL: (),
    FilterPreset.SCANLINES: (),
}


def preset_passes(preset: FilterPreset) -> tuple[PostPassSpec, ...]:
    """Return the ordered intermediate pass list for ``preset`` (§13.4).

    ``faithful`` has no intermediate passes. ``scanlines`` also uses zero intermediate passes
    and applies output-grid scanlines in the final viewport presentation step (§13.3).
    """
    return _PRESET_PASSES[preset]


def _set_optional_uniform(prog: moderngl.Program, name: str, value: UniformValue) -> None:
    if name not in prog:
        return
    member = prog[name]
    if isinstance(member, moderngl.Uniform):
        member.value = value


@dataclass(slots=True)
class _CompiledStage:
    """Allocated GPU resources for one compiled post-process pass."""

    spec: PostPassSpec
    output_size: tuple[int, int]
    program: moderngl.Program
    vao: moderngl.VertexArray
    texture: moderngl.Texture
    fbo: moderngl.Framebuffer

    def release(self) -> None:
        """Release the framebuffer, output texture, VAO, and shader program."""
        self.fbo.release()
        self.texture.release()
        self.vao.release()
        self.program.release()


class PostProcessChain:
    """Manages intermediate shader passes and GPU textures between canvas and viewport (§13.4)."""

    def __init__(self, ctx: moderngl.Context) -> None:
        self.ctx = ctx
        quad = np.array(
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
        self._quad_vbo = ctx.buffer(quad.tobytes())
        self._active_preset: FilterPreset | None = FilterPreset.FAITHFUL
        self._active_passes: tuple[PostPassSpec, ...] = ()
        self._active_input_size: tuple[int, int] | None = None
        self._stages: list[_CompiledStage] = []
        self._total_textures_created = 0
        self._total_textures_released = 0

    @property
    def active_preset(self) -> FilterPreset | None:
        """Currently configured preset, or ``None`` when custom passes are installed."""
        return self._active_preset

    @property
    def active_passes(self) -> tuple[PostPassSpec, ...]:
        """Currently configured ordered tuple of pass specifications."""
        return self._active_passes

    @property
    def active_texture_count(self) -> int:
        """Number of currently allocated intermediate pass textures."""
        return len(self._stages)

    @property
    def total_textures_created(self) -> int:
        """Cumulative number of intermediate textures allocated by this chain."""
        return self._total_textures_created

    @property
    def total_textures_released(self) -> int:
        """Cumulative number of intermediate textures released by this chain."""
        return self._total_textures_released

    @property
    def stage_textures(self) -> tuple[moderngl.Texture, ...]:
        """Currently allocated intermediate output textures in pass order."""
        return tuple(stage.texture for stage in self._stages)

    def set_preset(self, preset: FilterPreset) -> None:
        """Switch to ``preset``, releasing intermediate GPU resources when the passes change."""
        passes = preset_passes(preset)
        if self._active_preset is preset and self._active_passes == passes:
            return
        self._release_stages()
        self._active_preset = preset
        self._active_passes = passes

    def set_passes(self, passes: Sequence[PostPassSpec]) -> None:
        """Install an explicit sequence of passes, releasing previous intermediate resources."""
        resolved = tuple(passes)
        if self._active_preset is None and self._active_passes == resolved:
            return
        self._release_stages()
        self._active_preset = None
        self._active_passes = resolved

    def execute(
        self,
        canvas_tex: moderngl.Texture,
        source: SourceRect,
        *,
        canvas_size: tuple[int, int] = (CANVAS_WIDTH, CANVAS_HEIGHT),
    ) -> tuple[moderngl.Texture, SourceRect, tuple[int, int]]:
        """Run all active passes and return ``(output_texture, output_source, texture_size)``."""
        if not self._active_passes:
            if self._stages:
                self._release_stages()
            return (canvas_tex, source, canvas_size)

        input_size = (source.width, source.height)
        if self._active_input_size != input_size or len(self._stages) != len(self._active_passes):
            self._rebuild_stages(input_size)

        cur_tex = canvas_tex
        cur_origin = (float(source.x), float(source.y))
        cur_size = (float(source.width), float(source.height))
        cur_tex_size = (float(canvas_size[0]), float(canvas_size[1]))

        for stage in self._stages:
            self._run_stage(stage, cur_tex, cur_origin, cur_size, cur_tex_size)
            out_w, out_h = stage.output_size
            cur_tex = stage.texture
            cur_origin = (0.0, 0.0)
            cur_size = (float(out_w), float(out_h))
            cur_tex_size = cur_size

        last_w, last_h = self._stages[-1].output_size
        return (cur_tex, SourceRect(0, 0, last_w, last_h), (last_w, last_h))

    def _run_stage(
        self,
        stage: _CompiledStage,
        in_tex: moderngl.Texture,
        in_origin: tuple[float, float],
        in_size: tuple[float, float],
        in_tex_size: tuple[float, float],
    ) -> None:
        out_w, out_h = stage.output_size
        gl_filter = (
            moderngl.LINEAR if stage.spec.sampling is PassSampling.LINEAR else moderngl.NEAREST
        )
        in_tex.filter = (gl_filter, gl_filter)

        stage.fbo.use()
        self.ctx.viewport = (0, 0, out_w, out_h)
        prog = stage.program
        _set_optional_uniform(prog, "u_input_tex", 0)
        _set_optional_uniform(prog, "u_in_origin", in_origin)
        _set_optional_uniform(prog, "u_in_size", in_size)
        _set_optional_uniform(prog, "u_in_tex_size", in_tex_size)
        _set_optional_uniform(prog, "u_out_size", (float(out_w), float(out_h)))
        for u_name, u_val in stage.spec.uniforms:
            _set_optional_uniform(prog, u_name, u_val)

        in_tex.use(location=0)
        stage.vao.render(moderngl.TRIANGLES)

    def _rebuild_stages(self, input_size: tuple[int, int]) -> None:
        self._release_stages()
        cur_w, cur_h = input_size
        built: list[_CompiledStage] = []
        try:
            for spec in self._active_passes:
                out_w = cur_w * spec.scale_x
                out_h = cur_h * spec.scale_y
                prog = self.ctx.program(
                    vertex_shader=spec.vertex_shader,
                    fragment_shader=spec.fragment_shader,
                )
                vao = self.ctx.vertex_array(
                    prog,
                    [(self._quad_vbo, "2f 2f", "in_pos", "in_uv")],
                )
                tex = self.ctx.texture((out_w, out_h), 4)
                tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
                tex.repeat_x = False
                tex.repeat_y = False
                self._total_textures_created += 1
                fbo = self.ctx.framebuffer(color_attachments=[tex])
                built.append(
                    _CompiledStage(
                        spec=spec,
                        output_size=(out_w, out_h),
                        program=prog,
                        vao=vao,
                        texture=tex,
                        fbo=fbo,
                    )
                )
                cur_w, cur_h = out_w, out_h
        except Exception:
            for stage in built:
                stage.release()
                self._total_textures_released += 1
            raise
        self._stages = built
        self._active_input_size = input_size

    def _release_stages(self) -> None:
        for stage in self._stages:
            stage.release()
            self._total_textures_released += 1
        self._stages.clear()
        self._active_input_size = None

    def release(self) -> None:
        """Release all intermediate stages and the fullscreen quad VBO."""
        self._release_stages()
        self._quad_vbo.release()
