"""Multi-sprite 2.5D scene renderer, bitmap font blitter, and HUD (ARCHITECTURE.md §3, §4, §8)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import moderngl
import numpy as np
import numpy.typing as npt

from pymoonstone.assets.palette import RGBColor, default_combat_palette, palette_to_rgba_array
from pymoonstone.render.renderer import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    PLAYFIELD_HEIGHT,
    PLAYFIELD_Y_OFFSET,
    MoonstoneRenderer,
)
from pymoonstone.sim.palette_fx import rgb12_to_rgb24

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pymoonstone.assets.cel import CelFrame, DecodedCel
    from pymoonstone.render.overlay import OverlayLine

LAYER_SHADOW = 0
LAYER_CORPSE = 1
LAYER_ACTOR = 2
LAYER_PROJECTILE = 3
LAYER_FOREGROUND = 4

BORDER_HEIGHT = PLAYFIELD_Y_OFFSET
PLAYFIELD_CLIP: tuple[int, int, int, int] = (
    0,
    PLAYFIELD_Y_OFFSET,
    CANVAS_WIDTH,
    PLAYFIELD_Y_OFFSET + PLAYFIELD_HEIGHT,
)
CANVAS_CLIP: tuple[int, int, int, int] = (0, 0, CANVAS_WIDTH, CANVAS_HEIGHT)

_SCENE_VERT = """
#version 330 core
in vec2 in_pos;
in vec2 in_uv;
out vec2 v_uv;
void main() {
    gl_Position = vec4(in_pos, 0.0, 1.0);
    v_uv = in_uv;
}
"""

_SCENE_FRAG = """
#version 330 core
uniform sampler2D u_index_tex;
uniform sampler2D u_palette_tex;
in vec2 v_uv;
out vec4 f_color;
void main() {
    float raw_idx = texture(u_index_tex, v_uv).r;
    int idx = int(raw_idx * 255.0 + 0.5);
    f_color = texelFetch(u_palette_tex, ivec2(idx & 31, 0), 0);
}
"""

# Clean-room 8x8 monochrome bitmap glyphs (8 row bytes, MSB = leftmost pixel).
_BUILTIN_GLYPHS_8X8: dict[str, tuple[int, ...]] = {
    " ": (0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00),
    "!": (0x18, 0x18, 0x18, 0x18, 0x18, 0x00, 0x18, 0x00),
    "%": (0x62, 0x64, 0x08, 0x10, 0x26, 0x46, 0x00, 0x00),
    "+": (0x00, 0x18, 0x18, 0x7E, 0x18, 0x18, 0x00, 0x00),
    "-": (0x00, 0x00, 0x00, 0x7E, 0x00, 0x00, 0x00, 0x00),
    ".": (0x00, 0x00, 0x00, 0x00, 0x00, 0x18, 0x18, 0x00),
    "/": (0x06, 0x0C, 0x18, 0x30, 0x60, 0xC0, 0x00, 0x00),
    ":": (0x00, 0x18, 0x18, 0x00, 0x18, 0x18, 0x00, 0x00),
    "0": (0x3C, 0x66, 0x6E, 0x76, 0x66, 0x66, 0x3C, 0x00),
    "1": (0x18, 0x38, 0x18, 0x18, 0x18, 0x18, 0x7E, 0x00),
    "2": (0x3C, 0x66, 0x06, 0x1C, 0x30, 0x60, 0x7E, 0x00),
    "3": (0x3C, 0x66, 0x06, 0x1C, 0x06, 0x66, 0x3C, 0x00),
    "4": (0x0C, 0x1C, 0x3C, 0x6C, 0x7E, 0x0C, 0x0C, 0x00),
    "5": (0x7E, 0x60, 0x7C, 0x06, 0x06, 0x66, 0x3C, 0x00),
    "6": (0x3C, 0x60, 0x7C, 0x66, 0x66, 0x66, 0x3C, 0x00),
    "7": (0x7E, 0x06, 0x0C, 0x18, 0x30, 0x30, 0x30, 0x00),
    "8": (0x3C, 0x66, 0x66, 0x3C, 0x66, 0x66, 0x3C, 0x00),
    "9": (0x3C, 0x66, 0x66, 0x3E, 0x06, 0x0C, 0x38, 0x00),
    "A": (0x18, 0x3C, 0x66, 0x7E, 0x66, 0x66, 0x66, 0x00),
    "B": (0x7C, 0x66, 0x66, 0x7C, 0x66, 0x66, 0x7C, 0x00),
    "C": (0x3C, 0x66, 0x60, 0x60, 0x60, 0x66, 0x3C, 0x00),
    "D": (0x78, 0x6C, 0x66, 0x66, 0x66, 0x6C, 0x78, 0x00),
    "E": (0x7E, 0x60, 0x60, 0x78, 0x60, 0x60, 0x7E, 0x00),
    "F": (0x7E, 0x60, 0x60, 0x78, 0x60, 0x60, 0x60, 0x00),
    "G": (0x3C, 0x66, 0x60, 0x6E, 0x66, 0x66, 0x3E, 0x00),
    "H": (0x66, 0x66, 0x66, 0x7E, 0x66, 0x66, 0x66, 0x00),
    "I": (0x3C, 0x18, 0x18, 0x18, 0x18, 0x18, 0x3C, 0x00),
    "J": (0x1E, 0x0C, 0x0C, 0x0C, 0x0C, 0x6C, 0x38, 0x00),
    "K": (0x66, 0x6C, 0x78, 0x70, 0x78, 0x6C, 0x66, 0x00),
    "L": (0x60, 0x60, 0x60, 0x60, 0x60, 0x60, 0x7E, 0x00),
    "M": (0x63, 0x77, 0x7F, 0x6B, 0x63, 0x63, 0x63, 0x00),
    "N": (0x66, 0x76, 0x7E, 0x7E, 0x6E, 0x66, 0x66, 0x00),
    "O": (0x3C, 0x66, 0x66, 0x66, 0x66, 0x66, 0x3C, 0x00),
    "P": (0x7C, 0x66, 0x66, 0x7C, 0x60, 0x60, 0x60, 0x00),
    "Q": (0x3C, 0x66, 0x66, 0x66, 0x6A, 0x6C, 0x36, 0x00),
    "R": (0x7C, 0x66, 0x66, 0x7C, 0x6C, 0x66, 0x66, 0x00),
    "S": (0x3C, 0x66, 0x60, 0x3C, 0x06, 0x66, 0x3C, 0x00),
    "T": (0x7E, 0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x00),
    "U": (0x66, 0x66, 0x66, 0x66, 0x66, 0x66, 0x3C, 0x00),
    "V": (0x66, 0x66, 0x66, 0x66, 0x66, 0x3C, 0x18, 0x00),
    "W": (0x63, 0x63, 0x63, 0x6B, 0x7F, 0x77, 0x63, 0x00),
    "X": (0x66, 0x66, 0x3C, 0x18, 0x3C, 0x66, 0x66, 0x00),
    "Y": (0x66, 0x66, 0x66, 0x3C, 0x18, 0x18, 0x18, 0x00),
    "Z": (0x7E, 0x06, 0x0C, 0x18, 0x30, 0x60, 0x7E, 0x00),
}


@dataclass(slots=True, frozen=True)
class SceneSprite:
    """2.5D scene sprite instance for painter's-algorithm Y-sorting (ARCHITECTURE.md §4.1)."""

    frame: CelFrame
    x: int
    y: int
    z: int = 0
    layer: int = LAYER_ACTOR
    order: int = 0
    mirrored: bool = False
    dx: int = 0
    dy: int = 0
    top_left: bool = False


@dataclass(slots=True, frozen=True)
class CombatHudState:
    """Combat border HUD metrics (knight/enemy vitals, stamina, lives, score)."""

    hp: int
    max_hp: int
    stamina: int
    max_stamina: int
    lives: int
    score: int = 0
    knight_name: str = ""
    enemy_name: str = ""
    enemy_hp: int = 0
    enemy_max_hp: int = 0


@dataclass(slots=True, frozen=True)
class OverworldHudState:
    """Overworld border HUD metrics (vitals, gold, lives, moon phase, turn, score)."""

    knight_name: str
    hp: int
    max_hp: int
    gold: int
    lives: int
    moon_phase: int
    turn: int = 1
    score: int = 0


def sort_sprites_painter(sprites: Sequence[SceneSprite]) -> list[SceneSprite]:
    """Sort sprites back-to-front by ground depth ``y``, then ``layer`` and ``order``."""
    return sorted(sprites, key=lambda s: (s.y, s.layer, s.order))


def compute_sprite_draw_origin(sprite: SceneSprite) -> tuple[int, int]:
    """Compute top-left canvas ``(draw_x, draw_y)`` applying ``y - z`` altitude and mirroring."""
    if sprite.top_left:
        return sprite.x + sprite.dx, PLAYFIELD_Y_OFFSET + sprite.y - sprite.z + sprite.dy
    w = sprite.frame.meta.width
    h = sprite.frame.meta.height
    hot_x = sprite.frame.meta.hot_x
    x_offset = hot_x if sprite.mirrored else -hot_x
    draw_x = sprite.x + sprite.dx - (w // 2) + x_offset
    draw_y = PLAYFIELD_Y_OFFSET + sprite.y - sprite.z + sprite.dy - (h // 2)
    return draw_x, draw_y


def blit_indexed_sprite(
    canvas: npt.NDArray[np.uint8],
    pixels: npt.NDArray[np.uint8],
    draw_x: int,
    draw_y: int,
    *,
    mirrored: bool = False,
    clip_rect: tuple[int, int, int, int] = PLAYFIELD_CLIP,
) -> None:
    """Blit indexed ``pixels`` onto ``canvas`` with transparency ``0`` via numpy slicing."""
    if pixels.size == 0:
        return
    src = pixels[:, ::-1] if mirrored else pixels
    sh, sw = src.shape
    cx0, cy0, cx1, cy1 = clip_rect

    dst_x0 = max(cx0, draw_x)
    dst_y0 = max(cy0, draw_y)
    dst_x1 = min(cx1, draw_x + sw)
    dst_y1 = min(cy1, draw_y + sh)
    if dst_x0 >= dst_x1 or dst_y0 >= dst_y1:
        return

    src_x0 = dst_x0 - draw_x
    src_y0 = dst_y0 - draw_y
    src_x1 = src_x0 + (dst_x1 - dst_x0)
    src_y1 = src_y0 + (dst_y1 - dst_y0)

    patch = src[src_y0:src_y1, src_x0:src_x1]
    region = canvas[dst_y0:dst_y1, dst_x0:dst_x1]
    mask = patch != 0
    region[mask] = patch[mask]


def palette_fx_to_rgb(palette_words: Sequence[int]) -> tuple[RGBColor, ...]:
    """Convert 32-entry 12-bit ``$0RGB`` palette words (``sim/palette_fx``) to 8-bit RGB."""
    return tuple(rgb12_to_rgb24(word) for word in palette_words)


def apply_palette_fx(
    renderer: MoonstoneRenderer,
    palette_words: Sequence[int],
) -> tuple[RGBColor, ...]:
    """Upload a 32-entry ``$0RGB`` palette from ``sim/palette_fx`` to ``MoonstoneRenderer``."""
    rgb = palette_fx_to_rgb(palette_words)
    renderer.set_palette(rgb)
    return rgb


def _unpack_glyph_rows(rows: Sequence[int]) -> npt.NDArray[np.uint8]:
    packed = np.array(rows, dtype=np.uint8)[:, None]
    return np.unpackbits(packed, axis=1)


class BitmapFont:
    """Indexed bitmap font blitter supporting ``.f``, ``.font``, and 8x8 glyphs (§3.3)."""

    def __init__(
        self,
        glyphs: dict[int, npt.NDArray[np.uint8]],
        char_width: int = 8,
        char_height: int = 8,
    ) -> None:
        self.glyphs = glyphs
        self.char_width = char_width
        self.char_height = char_height
        self._blank = np.zeros((char_height, char_width), dtype=np.uint8)

    @classmethod
    def from_cel(
        cls,
        cel: DecodedCel,
        *,
        base_char: int = 0x20,
        char_width: int = 8,
        char_height: int = 8,
    ) -> BitmapFont:
        """Build a ``BitmapFont`` from a decoded ``.f`` or ``.font`` cel container (§3.3)."""
        glyphs: dict[int, npt.NDArray[np.uint8]] = {}
        if cel.frame_count == 1 and cel.frames[0].pixels.size > 0:
            sheet = cel.frames[0].pixels
            sh, sw = sheet.shape
            cols = max(1, sw // char_width)
            rows = max(1, sh // char_height)
            idx = 0
            for r in range(rows):
                for c in range(cols):
                    y0 = r * char_height
                    x0 = c * char_width
                    glyphs[base_char + idx] = np.ascontiguousarray(
                        sheet[y0 : y0 + char_height, x0 : x0 + char_width]
                    )
                    idx += 1
            return cls(glyphs, char_width=char_width, char_height=char_height)

        for idx, frame in enumerate(cel.frames):
            tile = np.zeros((char_height, char_width), dtype=np.uint8)
            h = min(char_height, frame.meta.height)
            w = min(char_width, frame.meta.width)
            if h > 0 and w > 0:
                tile[:h, :w] = frame.pixels[:h, :w]
            glyphs[base_char + idx] = tile
        return cls(glyphs, char_width=char_width, char_height=char_height)

    def _lookup_glyph(self, ch: str) -> npt.NDArray[np.uint8]:
        code = ord(ch)
        if code in self.glyphs:
            return self.glyphs[code]
        upper_code = ord(ch.upper())
        if upper_code in self.glyphs:
            return self.glyphs[upper_code]
        return self.glyphs.get(0x20, self._blank)

    def draw_text(
        self,
        canvas: npt.NDArray[np.uint8],
        text: str,
        x: int,
        y: int,
        *,
        color_index: int | None = 31,
        spacing: int = 0,
        clip_rect: tuple[int, int, int, int] = CANVAS_CLIP,
    ) -> None:
        """Render ``text`` onto ``canvas`` at ``(x, y)`` using vectorized numpy blitting."""
        if not text:
            return
        step = self.char_width + spacing
        total_w = step * len(text) - spacing
        if total_w <= 0:
            return
        strip = np.zeros((self.char_height, total_w), dtype=np.uint8)
        for idx, ch in enumerate(text):
            x0 = idx * step
            strip[:, x0 : x0 + self.char_width] = self._lookup_glyph(ch)
        if color_index is not None:
            painted = np.where(strip != 0, np.uint8(color_index & 0x1F), np.uint8(0))
        else:
            painted = strip
        blit_indexed_sprite(canvas, painted, x, y, clip_rect=clip_rect)


def default_bitmap_font() -> BitmapFont:
    """Return the built-in clean-room 8x8 ASCII bitmap font."""
    glyphs = {ord(ch): _unpack_glyph_rows(rows) for ch, rows in _BUILTIN_GLYPHS_8X8.items()}
    return BitmapFont(glyphs, char_width=8, char_height=8)


def draw_bar(
    canvas: npt.NDArray[np.uint8],
    x: int,
    y: int,
    width: int,
    height: int,
    value: int,
    max_value: int,
    *,
    fill_index: int,
    bg_index: int = 1,
    border_index: int = 4,
) -> None:
    """Draw a bordered horizontal status bar onto ``canvas`` using numpy array slicing."""
    if width <= 0 or height <= 0:
        return
    x0 = max(0, x)
    y0 = max(0, y)
    x1 = min(CANVAS_WIDTH, x + width)
    y1 = min(CANVAS_HEIGHT, y + height)
    if x0 >= x1 or y0 >= y1:
        return

    canvas[y0:y1, x0:x1] = np.uint8(border_index & 0x1F)
    ix0 = max(0, x + 1)
    iy0 = max(0, y + 1)
    ix1 = min(CANVAS_WIDTH, x + width - 1)
    iy1 = min(CANVAS_HEIGHT, y + height - 1)
    if ix0 >= ix1 or iy0 >= iy1:
        return

    canvas[iy0:iy1, ix0:ix1] = np.uint8(bg_index & 0x1F)
    if max_value <= 0 or value <= 0:
        return
    clamped = min(value, max_value)
    inner_w = max(0, width - 2)
    filled_w = (inner_w * clamped) // max_value
    fx1 = min(CANVAS_WIDTH, x + 1 + filled_w)
    if ix0 < fx1:
        canvas[iy0:iy1, ix0:fx1] = np.uint8(fill_index & 0x1F)


def render_combat_hud(
    canvas: npt.NDArray[np.uint8],
    hud: CombatHudState,
    font: BitmapFont,
) -> None:
    """Render combat HP/stamina bars, enemy HP bar, lives, and score in the 28px borders."""
    label = hud.knight_name[:8] if hud.knight_name else "KNIGHT"
    font.draw_text(canvas, label, 8, 4, color_index=31)
    font.draw_text(canvas, "HP", 8, 15, color_index=30)
    draw_bar(canvas, 28, 15, 84, 8, hud.hp, hud.max_hp, fill_index=8)
    font.draw_text(canvas, "ST", 120, 15, color_index=30)
    draw_bar(canvas, 140, 15, 64, 8, hud.stamina, hud.max_stamina, fill_index=20)

    if hud.enemy_max_hp > 0:
        ename = hud.enemy_name[:8] if hud.enemy_name else "FOE"
        font.draw_text(canvas, ename, 224, 4, color_index=31)
        draw_bar(canvas, 224, 15, 88, 8, hud.enemy_hp, hud.enemy_max_hp, fill_index=15)

    bottom_y = PLAYFIELD_Y_OFFSET + PLAYFIELD_HEIGHT + 10
    font.draw_text(canvas, f"LIVES:{hud.lives}", 8, bottom_y, color_index=31)
    font.draw_text(canvas, f"SCORE:{hud.score:06d}", 208, bottom_y, color_index=30)


def render_overworld_hud(
    canvas: npt.NDArray[np.uint8],
    hud: OverworldHudState,
    font: BitmapFont,
) -> None:
    """Render overworld knight vitals, gold, lives, moon phase, and turn in the 28px borders."""
    font.draw_text(canvas, hud.knight_name[:10], 8, 4, color_index=31)
    draw_bar(canvas, 8, 15, 80, 8, hud.hp, hud.max_hp, fill_index=8)
    font.draw_text(canvas, f"GOLD:{hud.gold}", 104, 15, color_index=30)
    font.draw_text(canvas, f"LIVES:{hud.lives}", 232, 15, color_index=31)

    bottom_y = PLAYFIELD_Y_OFFSET + PLAYFIELD_HEIGHT + 10
    font.draw_text(canvas, f"MOON:{hud.moon_phase:02d}/28", 8, bottom_y, color_index=29)
    font.draw_text(canvas, f"TURN:{hud.turn}", 112, bottom_y, color_index=31)
    font.draw_text(canvas, f"SCORE:{hud.score:06d}", 208, bottom_y, color_index=30)


def _set_scene_uniform_int(prog: moderngl.Program, name: str, value: int) -> None:
    member = prog[name]
    if isinstance(member, moderngl.Uniform):
        member.value = value


class SceneRenderer:
    """Compositor for 2.5D Y-sorted sprites, bitmap HUDs, and ``MoonstoneRenderer`` output."""

    def __init__(self, renderer: MoonstoneRenderer) -> None:
        self.renderer = renderer
        self.ctx = renderer.ctx
        self.canvas: npt.NDArray[np.uint8] = np.zeros((CANVAS_HEIGHT, CANVAS_WIDTH), dtype=np.uint8)

        self._canvas_idx_tex = self.ctx.texture((CANVAS_WIDTH, CANVAS_HEIGHT), 1)
        self._canvas_idx_tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
        self._canvas_idx_tex.write(self.canvas.tobytes())

        self._palette_tex = self.ctx.texture((32, 1), 4)
        self._palette_tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
        self.set_palette(default_combat_palette())

        self._prog = self.ctx.program(vertex_shader=_SCENE_VERT, fragment_shader=_SCENE_FRAG)
        _set_scene_uniform_int(self._prog, "u_index_tex", 0)
        _set_scene_uniform_int(self._prog, "u_palette_tex", 1)

        fs_verts = np.array(
            [
                [-1.0, 1.0, 0.0, 0.0],
                [1.0, 1.0, 1.0, 0.0],
                [-1.0, -1.0, 0.0, 1.0],
                [-1.0, -1.0, 0.0, 1.0],
                [1.0, 1.0, 1.0, 0.0],
                [1.0, -1.0, 1.0, 1.0],
            ],
            dtype=np.float32,
        )
        self._vbo = self.ctx.buffer(fs_verts.tobytes())
        self._vao = self.ctx.vertex_array(
            self._prog,
            [(self._vbo, "2f 2f", "in_pos", "in_uv")],
        )

    def set_palette(self, palette: Sequence[RGBColor]) -> None:
        """Upload a 32-entry RGB888 palette to both this compositor and ``MoonstoneRenderer``."""
        self.renderer.set_palette(palette)
        lut = palette_to_rgba_array(palette, size=32, zero_transparent=False)
        self._palette_tex.write(lut.tobytes())

    def set_palette_fx(self, palette_words: Sequence[int]) -> tuple[RGBColor, ...]:
        """Convert 12-bit ``$0RGB`` words from ``sim/palette_fx`` and upload to GPU."""
        rgb = palette_fx_to_rgb(palette_words)
        self.set_palette(rgb)
        return rgb

    def clear_canvas(self, fill_index: int = 0) -> None:
        """Fill the entire 320x256 indexed canvas with ``fill_index``."""
        self.canvas.fill(np.uint8(fill_index & 0x1F))

    def compose_scene(
        self,
        *,
        background: npt.NDArray[np.uint8] | None = None,
        sprites: Sequence[SceneSprite] = (),
        border_index: int = 0,
    ) -> list[SceneSprite]:
        """Composite ``background`` at ``y=28`` and Y-sorted ``sprites`` onto ``self.canvas``."""
        self.clear_canvas(border_index)
        if background is not None:
            bh = min(PLAYFIELD_HEIGHT, background.shape[0])
            bw = min(CANVAS_WIDTH, background.shape[1])
            self.canvas[PLAYFIELD_Y_OFFSET : PLAYFIELD_Y_OFFSET + bh, :bw] = background[:bh, :bw]

        ordered = sort_sprites_painter(sprites)
        for sprite in ordered:
            if sprite.frame.meta.width <= 0 or sprite.frame.meta.height <= 0:
                continue
            draw_x, draw_y = compute_sprite_draw_origin(sprite)
            blit_indexed_sprite(
                self.canvas,
                sprite.frame.pixels,
                draw_x,
                draw_y,
                mirrored=sprite.mirrored,
                clip_rect=PLAYFIELD_CLIP,
            )
        return ordered

    def flush_to_fbo(self, overlay_lines: Sequence[OverlayLine] = ()) -> None:
        """Upload ``self.canvas`` to ``renderer.canvas_fbo`` and draw optional overlay lines."""
        contiguous = np.ascontiguousarray(self.canvas, dtype=np.uint8)
        self._canvas_idx_tex.write(contiguous.tobytes())

        self.renderer.canvas_fbo.use()
        self.ctx.viewport = (0, 0, CANVAS_WIDTH, CANVAS_HEIGHT)
        self.ctx.clear(0.0, 0.0, 0.0, 1.0)
        self._canvas_idx_tex.use(location=0)
        self._palette_tex.use(location=1)
        self._vao.render(moderngl.TRIANGLES)

        if overlay_lines:
            self.renderer.draw_overlay_lines(overlay_lines)

    def release(self) -> None:
        """Release GPU resources owned by this scene compositor."""
        self._vao.release()
        self._vbo.release()
        self._prog.release()
        self._canvas_idx_tex.release()
        self._palette_tex.release()
