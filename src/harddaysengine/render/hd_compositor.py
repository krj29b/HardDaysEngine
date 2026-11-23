"""OpenGL 3.3 GPU HD compositor, palette shaders, and LRU texture cache (ARCHITECTURE.md §14.6)."""

from __future__ import annotations

import struct
import zlib
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING

import moderngl
import numpy as np
import numpy.typing as npt

from harddaysengine.assets.hd_pack_schema import (
    MAX_HD_PACK_SCALE,
    MIN_HD_PACK_SCALE,
    HdAssetEntry,
    HdAssetKind,
    HdColorMode,
    HdFallbackFilter,
    HdImage,
)
from harddaysengine.assets.palette import RGBColor, default_combat_palette, palette_to_rgba_array
from harddaysengine.render.scene import PLAYFIELD_CLIP, compute_sprite_draw_origin
from harddaysengine.render.shaders import load_shader
from harddaysengine.render.viewport import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    PLAYFIELD_HEIGHT,
    PLAYFIELD_Y_OFFSET,
)
from harddaysengine.sim.m68k import s16

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from harddaysengine.assets.cel import CelFrame, DecodedCel
    from harddaysengine.assets.hd_pack import HdPack
    from harddaysengine.render.scene import SceneSprite
    from harddaysengine.sim.fight_script import Draw

DEFAULT_HD_CACHE_MAX_TEXTURES = 256
DEFAULT_HD_CACHE_MAX_VRAM_BYTES = 256 * 1024 * 1024
_PLAYFIELD_CLIP_LOCAL: tuple[int, int, int, int] = (0, 0, CANVAS_WIDTH, PLAYFIELD_HEIGHT)
_LUT_SIZE = 32
_CACHE_KEY = tuple[str, str, str | None, int | None, int | None, tuple[int, int] | None, bool]


@dataclass(slots=True, frozen=True)
class HdCacheStats:
    """Telemetry snapshot for the compositor's bounded LRU GPU texture cache (§14.6.2)."""

    entry_count: int
    estimated_vram_bytes: int
    hits: int
    misses: int
    evictions: int
    total_textures_created: int
    total_textures_released: int


@dataclass(slots=True, frozen=True)
class _CachedAsset:
    primary_tex: moderngl.Texture
    mask_tex: moderngl.Texture | None
    mode: HdColorMode | None
    asset_scale: int
    padding: tuple[int, int, int, int]
    anchor_offset: tuple[int, int]
    tex_size: tuple[int, int]
    vram_bytes: int
    texture_count: int


def _set_uniform_int(prog: moderngl.Program, name: str, value: int) -> None:
    member = prog[name]
    if isinstance(member, moderngl.Uniform):
        member.value = value


def _set_uniform_vec2(prog: moderngl.Program, name: str, value: tuple[float, float]) -> None:
    member = prog[name]
    if isinstance(member, moderngl.Uniform):
        member.value = value


def _set_uniform_vec3(prog: moderngl.Program, name: str, value: tuple[float, float, float]) -> None:
    member = prog[name]
    if isinstance(member, moderngl.Uniform):
        member.value = value


def _set_uniform_vec4(
    prog: moderngl.Program, name: str, value: tuple[float, float, float, float]
) -> None:
    member = prog[name]
    if isinstance(member, moderngl.Uniform):
        member.value = value


def _palette_to_float_rgb(palette: Sequence[RGBColor]) -> npt.NDArray[np.float32]:
    arr = np.zeros((_LUT_SIZE, 3), dtype=np.float32)
    count = min(len(palette), _LUT_SIZE)
    for idx in range(count):
        r, g, b = palette[idx]
        arr[idx, 0] = r / 255.0
        arr[idx, 1] = g / 255.0
        arr[idx, 2] = b / 255.0
    return np.clip(arr, 0.0, 1.0)


def compute_fade_and_flash(
    live_palette: Sequence[RGBColor],
    base_palette: Sequence[RGBColor],
) -> tuple[tuple[float, float, float], tuple[float, float, float, float]]:
    """Compute CPU ``u_fade_tint`` and ``u_flash_mix`` from live vs. base palettes (§14.6.1)."""
    base_arr = _palette_to_float_rgb(base_palette)
    live_arr = _palette_to_float_rgb(live_palette)
    ref_mask = np.any(base_arr > 0.0, axis=1)
    if not bool(np.any(ref_mask)):
        fade_tint = (0.0, 0.0, 0.0)
        diff_pos = np.maximum(live_arr[1:] - base_arr[1:], 0.0)
    else:
        base_sel = base_arr[ref_mask]
        live_sel = live_arr[ref_mask]
        eps = 1e-5
        overall = float(np.clip(np.mean(live_sel) / max(float(np.mean(base_sel)), eps), 0.0, 1.0))
        ratios: list[float] = []
        for ch in range(3):
            mb = float(np.mean(base_sel[:, ch]))
            ml = float(np.mean(live_sel[:, ch]))
            ratios.append(overall if mb <= eps else float(np.clip(ml / max(mb, eps), 0.0, 1.0)))
        fade_tint = (ratios[0], ratios[1], ratios[2])
        diff_pos = np.maximum(live_sel - base_sel, 0.0)

    flash_rgb = np.clip(np.mean(diff_pos, axis=0), 0.0, 1.0)
    t = float(np.clip(np.max(flash_rgb), 0.0, 1.0))
    if t > 1e-6:
        target = (
            float(np.clip(flash_rgb[0] / t, 0.0, 1.0)),
            float(np.clip(flash_rgb[1] / t, 0.0, 1.0)),
            float(np.clip(flash_rgb[2] / t, 0.0, 1.0)),
        )
    else:
        target = (0.0, 0.0, 0.0)
        t = 0.0
    return fade_tint, (target[0], target[1], target[2], t)


def _build_quad_verts(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    target_size: tuple[int, int],
    *,
    u0: float,
    u1: float,
    v0: float,
    v1: float,
) -> npt.NDArray[np.float32]:
    tw, th = target_size
    nx0 = (x0 / tw) * 2.0 - 1.0
    nx1 = (x1 / tw) * 2.0 - 1.0
    ny0 = 1.0 - (y0 / th) * 2.0
    ny1 = 1.0 - (y1 / th) * 2.0
    return np.array(
        [
            [nx0, ny0, u0, v0],
            [nx1, ny0, u1, v0],
            [nx0, ny1, u0, v1],
            [nx0, ny1, u0, v1],
            [nx1, ny0, u1, v0],
            [nx1, ny1, u1, v1],
        ],
        dtype=np.float32,
    )


def _compute_draw_quad(
    x: float,
    y: float,
    orig_w: int,
    orig_h: int,
    *,
    asset_scale: int,
    padding: tuple[int, int, int, int],
    anchor_offset: tuple[int, int],
    mirrored: bool,
    target_size: tuple[int, int],
) -> npt.NDArray[np.float32]:
    pad_l, pad_t, pad_r, pad_b = padding
    off_x, off_y = anchor_offset
    if mirrored:
        x0 = x + (-off_x - pad_r) / asset_scale
        x1 = x + orig_w + (-off_x + pad_l) / asset_scale
        u0, u1 = 1.0, 0.0
    else:
        x0 = x + (off_x - pad_l) / asset_scale
        x1 = x + orig_w + (off_x + pad_r) / asset_scale
        u0, u1 = 0.0, 1.0
    y0 = y + (off_y - pad_t) / asset_scale
    y1 = y + orig_h + (off_y + pad_b) / asset_scale
    return _build_quad_verts(x0, y0, x1, y1, target_size, u0=u0, u1=u1, v0=0.0, v1=1.0)


def _compute_scissor_box(
    clip_rect: tuple[int, int, int, int],
    target_size: tuple[int, int],
    scale: int,
) -> tuple[int, int, int, int] | None:
    cx0, cy0, cx1, cy1 = clip_rect
    fbo_w = target_size[0] * scale
    fbo_h = target_size[1] * scale
    sx0 = max(0, cx0 * scale)
    sx1 = min(fbo_w, cx1 * scale)
    sy0_top = max(0, cy0 * scale)
    sy1_top = min(fbo_h, cy1 * scale)
    sw = sx1 - sx0
    sh = sy1_top - sy0_top
    if sw <= 0 or sh <= 0:
        return None
    sy = fbo_h - sy1_top
    return (sx0, sy, sw, sh)


def _upload_r8_texture(
    ctx: moderngl.Context,
    pixels: npt.NDArray[np.uint8],
) -> moderngl.Texture:
    arr = np.ascontiguousarray(pixels, dtype=np.uint8)
    h, w = arr.shape
    tex = ctx.texture((w, h), 1, data=arr.tobytes(), alignment=1)
    tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
    tex.repeat_x = False
    tex.repeat_y = False
    return tex


def _upload_rgba_texture(
    ctx: moderngl.Context,
    pixels: npt.NDArray[np.uint8],
    *,
    linear: bool,
) -> moderngl.Texture:
    arr = np.ascontiguousarray(pixels, dtype=np.uint8)
    h, w, _ = arr.shape
    tex = ctx.texture((w, h), 4, data=arr.tobytes(), alignment=1)
    gl_filter = moderngl.LINEAR if linear else moderngl.NEAREST
    tex.filter = (gl_filter, gl_filter)
    tex.repeat_x = False
    tex.repeat_y = False
    return tex


class _GpuTextureCache:
    """Bounded OrderedDict LRU cache for uploaded HD and 1x fallback textures (§14.6.2)."""

    def __init__(self, max_textures: int, max_vram_bytes: int) -> None:
        self._max_textures = max_textures
        self._max_vram_bytes = max_vram_bytes
        self._entries: OrderedDict[_CACHE_KEY, _CachedAsset] = OrderedDict()
        self._vram_bytes = 0
        self._texture_count = 0
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._created = 0
        self._released = 0

    def get(self, key: _CACHE_KEY) -> _CachedAsset | None:
        item = self._entries.get(key)
        if item is None:
            self._misses += 1
            return None
        self._hits += 1
        self._entries.move_to_end(key)
        return item

    def put(self, key: _CACHE_KEY, asset: _CachedAsset) -> None:
        existing = self._entries.pop(key, None)
        if existing is not None:
            self._release_entry(existing)
        self._entries[key] = asset
        self._vram_bytes += asset.vram_bytes
        self._texture_count += asset.texture_count
        self._created += asset.texture_count
        self._evict_if_needed()

    def _release_entry(self, asset: _CachedAsset) -> None:
        asset.primary_tex.release()
        if asset.mask_tex is not None:
            asset.mask_tex.release()
        self._vram_bytes -= asset.vram_bytes
        self._texture_count -= asset.texture_count
        self._released += asset.texture_count

    def _evict_if_needed(self) -> None:
        while len(self._entries) > 1 and (
            self._texture_count > self._max_textures or self._vram_bytes > self._max_vram_bytes
        ):
            _, oldest = self._entries.popitem(last=False)
            self._release_entry(oldest)
            self._evictions += 1

    def clear(self) -> None:
        while self._entries:
            _, entry = self._entries.popitem(last=False)
            self._release_entry(entry)

    def stats(self) -> HdCacheStats:
        return HdCacheStats(
            entry_count=len(self._entries),
            estimated_vram_bytes=self._vram_bytes,
            hits=self._hits,
            misses=self._misses,
            evictions=self._evictions,
            total_textures_created=self._created,
            total_textures_released=self._released,
        )

    @property
    def active_texture_count(self) -> int:
        return self._created - self._released


class _ShaderBank:
    """Compiled GLSL 330 shader programs and VAOs for :class:`HdCompositor`."""

    def __init__(self, ctx: moderngl.Context, quad_vbo: moderngl.Buffer) -> None:
        vert_src = load_shader("hd_sprite.vert")
        self.indexed = ctx.program(
            vertex_shader=vert_src, fragment_shader=load_shader("hd_indexed.frag")
        )
        self.rgba = ctx.program(vertex_shader=vert_src, fragment_shader=load_shader("hd_rgba.frag"))
        self.masked = ctx.program(
            vertex_shader=vert_src, fragment_shader=load_shader("hd_masked.frag")
        )
        self.fallback_smooth = ctx.program(
            vertex_shader=vert_src, fragment_shader=load_shader("hd_fallback_smooth.frag")
        )
        self.blit = ctx.program(vertex_shader=vert_src, fragment_shader=load_shader("hd_blit.frag"))

        _set_uniform_int(self.indexed, "u_index_tex", 0)
        _set_uniform_int(self.indexed, "u_live_palette", 1)
        _set_uniform_int(self.rgba, "u_diffuse_tex", 0)
        _set_uniform_int(self.masked, "u_diffuse_tex", 0)
        _set_uniform_int(self.masked, "u_mask_tex", 1)
        _set_uniform_int(self.masked, "u_base_palette", 2)
        _set_uniform_int(self.masked, "u_live_palette", 3)
        _set_uniform_int(self.fallback_smooth, "u_index_tex", 0)
        _set_uniform_int(self.fallback_smooth, "u_live_palette", 1)
        _set_uniform_int(self.blit, "u_diffuse_tex", 0)

        self.vao_indexed = ctx.vertex_array(self.indexed, [(quad_vbo, "2f 2f", "in_pos", "in_uv")])
        self.vao_rgba = ctx.vertex_array(self.rgba, [(quad_vbo, "2f 2f", "in_pos", "in_uv")])
        self.vao_masked = ctx.vertex_array(self.masked, [(quad_vbo, "2f 2f", "in_pos", "in_uv")])
        self.vao_fallback_smooth = ctx.vertex_array(
            self.fallback_smooth, [(quad_vbo, "2f 2f", "in_pos", "in_uv")]
        )
        self.vao_blit = ctx.vertex_array(self.blit, [(quad_vbo, "2f 2f", "in_pos", "in_uv")])

    def release(self) -> None:
        self.vao_blit.release()
        self.vao_fallback_smooth.release()
        self.vao_masked.release()
        self.vao_rgba.release()
        self.vao_indexed.release()
        self.blit.release()
        self.fallback_smooth.release()
        self.masked.release()
        self.rgba.release()
        self.indexed.release()


class HdCompositor:
    """OpenGL 3.3 GPU HD compositor for external HD packs (ARCHITECTURE.md §14.6, ADR-014)."""

    def __init__(
        self,
        ctx: moderngl.Context,
        pack: HdPack,
        *,
        max_cached_textures: int = DEFAULT_HD_CACHE_MAX_TEXTURES,
        max_cache_vram_bytes: int = DEFAULT_HD_CACHE_MAX_VRAM_BYTES,
    ) -> None:
        scale = pack.config.scale
        if not MIN_HD_PACK_SCALE <= scale <= MAX_HD_PACK_SCALE:
            raise ValueError(
                f"HdPack scale must be in {MIN_HD_PACK_SCALE}..{MAX_HD_PACK_SCALE}, got {scale}"
            )
        if max_cached_textures < 1 or max_cache_vram_bytes < 1:
            raise ValueError("HD cache limits must be >= 1")

        self.ctx = ctx
        self._pack = pack
        self.scale = scale
        self.canvas_size = (CANVAS_WIDTH * scale, CANVAS_HEIGHT * scale)
        self.playfield_size = (CANVAS_WIDTH * scale, PLAYFIELD_HEIGHT * scale)

        self._canvas_tex, self._canvas_fbo = self._create_fbo(self.canvas_size)
        self._bg_tex, self._bg_fbo = self._create_fbo(self.playfield_size)
        self._fight_tex, self._fight_fbo = self._create_fbo(self.playfield_size)

        self._live_palette_tex = self._create_lut_texture()
        self._base_palette_tex = self._create_lut_texture()
        default_pal = default_combat_palette()
        self._live_palette: tuple[RGBColor, ...] = default_pal
        self._base_palette: tuple[RGBColor, ...] = default_pal
        self._fade_tint: tuple[float, float, float] = (1.0, 1.0, 1.0)
        self._flash_mix: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
        self._released = False
        self.set_palettes(default_pal, default_pal)

        self._owned_vram_bytes = (
            self.canvas_size[0] * self.canvas_size[1] * 4
            + 2 * (self.playfield_size[0] * self.playfield_size[1] * 4)
            + 2 * (_LUT_SIZE * 4)
        )
        self._owned_created = 5
        self._owned_released = 0

        self._quad_vbo = ctx.buffer(reserve=6 * 4 * 4, dynamic=True)
        self._shaders = _ShaderBank(ctx, self._quad_vbo)
        self._cache = _GpuTextureCache(max_cached_textures, max_cache_vram_bytes)

    def _create_fbo(self, size: tuple[int, int]) -> tuple[moderngl.Texture, moderngl.Framebuffer]:
        tex = self.ctx.texture(size, 4)
        tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
        tex.repeat_x = False
        tex.repeat_y = False
        fbo = self.ctx.framebuffer(color_attachments=[tex])
        return tex, fbo

    def _create_lut_texture(self) -> moderngl.Texture:
        tex = self.ctx.texture((_LUT_SIZE, 1), 4)
        tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
        tex.repeat_x = False
        tex.repeat_y = False
        return tex

    def _ensure_alive(self) -> None:
        if self._released:
            raise RuntimeError("HdCompositor has already been released")

    @property
    def output_texture(self) -> moderngl.Texture:
        """Main ``(320*S, 256*S)`` RGBA8 output texture."""
        return self._canvas_tex

    @property
    def output_fbo(self) -> moderngl.Framebuffer:
        """Main ``(320*S, 256*S)`` RGBA8 output framebuffer."""
        return self._canvas_fbo

    @property
    def fight_bg_texture(self) -> moderngl.Texture:
        """Retained ``(320*S, 200*S)`` permanently stamped fight background texture."""
        return self._bg_tex

    @property
    def fight_texture(self) -> moderngl.Texture:
        """Composed ``(320*S, 200*S)`` fight playfield texture."""
        return self._fight_tex

    @property
    def pack(self) -> HdPack:
        """External HD pack bound to this compositor."""
        return self._pack

    @property
    def cache_stats(self) -> HdCacheStats:
        """Current bounded LRU texture cache statistics."""
        return self._cache.stats()

    @property
    def total_textures_created(self) -> int:
        """Total GPU textures created by this compositor (FBOs + LUTs + cache)."""
        return self._owned_created + self._cache.stats().total_textures_created

    @property
    def total_textures_released(self) -> int:
        """Total GPU textures released by this compositor (FBOs + LUTs + cache)."""
        return self._owned_released + self._cache.stats().total_textures_released

    @property
    def active_texture_count(self) -> int:
        """Currently live GPU textures owned by this compositor."""
        return self.total_textures_created - self.total_textures_released

    @property
    def estimated_vram_bytes(self) -> int:
        """Estimated live GPU VRAM bytes (FBO attachments + LUTs + cached assets)."""
        return self._owned_vram_bytes + self._cache.stats().estimated_vram_bytes

    def set_palettes(
        self,
        live_palette: Sequence[RGBColor],
        base_palette: Sequence[RGBColor] | None = None,
        *,
        fade_reference: Sequence[RGBColor] | None = None,
    ) -> None:
        """Upload ``live_palette`` and optional ``base_palette`` and refresh fade/flash."""
        self._ensure_alive()
        self._live_palette = tuple(live_palette)
        self._live_palette_tex.write(
            palette_to_rgba_array(live_palette, size=_LUT_SIZE, zero_transparent=False).tobytes()
        )
        if base_palette is not None:
            self._base_palette = tuple(base_palette)
            self._base_palette_tex.write(
                palette_to_rgba_array(
                    base_palette, size=_LUT_SIZE, zero_transparent=False
                ).tobytes()
            )
        ref = self._base_palette if fade_reference is None else tuple(fade_reference)
        self._fade_tint, self._flash_mix = compute_fade_and_flash(self._live_palette, ref)

    def set_base_palette(self, palette: Sequence[RGBColor]) -> None:
        """Upload the un-faded reference palette."""
        self._ensure_alive()
        self._base_palette = tuple(palette)
        self._base_palette_tex.write(
            palette_to_rgba_array(palette, size=_LUT_SIZE, zero_transparent=False).tobytes()
        )
        self._fade_tint, self._flash_mix = compute_fade_and_flash(
            self._live_palette, self._base_palette
        )

    def set_live_palette(self, palette: Sequence[RGBColor]) -> None:
        """Upload the current 50 Hz tick palette."""
        self.set_palettes(palette)

    def _upload_hd_image(self, img: HdImage) -> _CachedAsset:
        if img.mode is HdColorMode.INDEXED:
            assert img.indexed is not None
            tex = _upload_r8_texture(self.ctx, img.indexed)
            return _CachedAsset(
                primary_tex=tex,
                mask_tex=None,
                mode=img.mode,
                asset_scale=img.scale,
                padding=img.padding,
                anchor_offset=img.anchor_offset,
                tex_size=(img.width, img.height),
                vram_bytes=img.width * img.height,
                texture_count=1,
            )
        assert img.rgba is not None
        linear = img.scale != self.scale
        tex = _upload_rgba_texture(self.ctx, img.rgba, linear=linear)
        vram = img.width * img.height * 4
        mask_tex: moderngl.Texture | None = None
        count = 1
        if img.mode is HdColorMode.MASKED:
            assert img.mask is not None
            mask_tex = _upload_r8_texture(self.ctx, img.mask)
            vram += img.width * img.height
            count = 2
        return _CachedAsset(
            primary_tex=tex,
            mask_tex=mask_tex,
            mode=img.mode,
            asset_scale=img.scale,
            padding=img.padding,
            anchor_offset=img.anchor_offset,
            tex_size=(img.width, img.height),
            vram_bytes=vram,
            texture_count=count,
        )

    def _get_or_upload_hd(
        self,
        entry: HdAssetEntry,
        *,
        orig_w: int,
        orig_h: int,
        cycle_slot: int | None,
    ) -> _CachedAsset:
        key: _CACHE_KEY = (
            entry.kind.value,
            entry.key,
            entry.variant,
            entry.frame_index,
            cycle_slot,
            None,
            False,
        )
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        if cycle_slot is not None:
            img = self._pack.load_cycle_frame(
                entry, cycle_slot, orig_width=orig_w, orig_height=orig_h
            )
        else:
            img = self._pack.load_image(entry, orig_width=orig_w, orig_height=orig_h)
        uploaded = self._upload_hd_image(img)
        self._cache.put(key, uploaded)
        return uploaded

    def _get_or_upload_fallback(
        self,
        kind_tag: str,
        name: str,
        frame_index: int | None,
        pixels: npt.NDArray[np.uint8],
    ) -> _CachedAsset:
        arr = np.ascontiguousarray(pixels, dtype=np.uint8)
        h, w = arr.shape
        digest = zlib.crc32(struct.pack("<II", h, w))
        digest = zlib.crc32(arr.data, digest)
        key: _CACHE_KEY = (kind_tag, name, None, frame_index, digest, (w, h), True)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        tex = _upload_r8_texture(self.ctx, arr)
        asset = _CachedAsset(
            primary_tex=tex,
            mask_tex=None,
            mode=None,
            asset_scale=1,
            padding=(0, 0, 0, 0),
            anchor_offset=(0, 0),
            tex_size=(w, h),
            vram_bytes=w * h,
            texture_count=1,
        )
        self._cache.put(key, asset)
        return asset

    def _configure_blend(self, *, enabled: bool) -> None:
        if enabled:
            self.ctx.enable(moderngl.BLEND)
            self.ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA)
        else:
            self.ctx.disable(moderngl.BLEND)

    def _render_asset_to_fbo(
        self,
        fbo: moderngl.Framebuffer,
        target_size: tuple[int, int],
        asset: _CachedAsset,
        *,
        x: int,
        y: int,
        orig_w: int,
        orig_h: int,
        mirrored: bool,
        transparent_zero: bool,
        clip_rect: tuple[int, int, int, int] | None,
        allow_smooth_fallback: bool,
    ) -> None:
        scissor: tuple[int, int, int, int] | None = None
        if clip_rect is not None:
            scissor = _compute_scissor_box(clip_rect, target_size, self.scale)
            if scissor is None:
                return

        verts = _compute_draw_quad(
            float(x),
            float(y),
            orig_w,
            orig_h,
            asset_scale=asset.asset_scale,
            padding=asset.padding,
            anchor_offset=asset.anchor_offset,
            mirrored=mirrored,
            target_size=target_size,
        )
        self._quad_vbo.write(verts.tobytes())
        fbo.use()
        self.ctx.viewport = (0, 0, target_size[0] * self.scale, target_size[1] * self.scale)
        self._configure_blend(enabled=transparent_zero)
        try:
            self.ctx.scissor = scissor
            self._dispatch_shader(
                asset,
                transparent_zero=transparent_zero,
                allow_smooth_fallback=allow_smooth_fallback,
            )
        finally:
            self.ctx.scissor = None
            self.ctx.disable(moderngl.BLEND)

    def _dispatch_shader(
        self,
        asset: _CachedAsset,
        *,
        transparent_zero: bool,
        allow_smooth_fallback: bool,
    ) -> None:
        t_zero = 1 if transparent_zero else 0
        if asset.mode is None:
            if (
                allow_smooth_fallback
                and self._pack.config.fallback_filter is HdFallbackFilter.SMOOTH
            ):
                prog = self._shaders.fallback_smooth
                _set_uniform_vec2(
                    prog, "u_tex_size", (float(asset.tex_size[0]), float(asset.tex_size[1]))
                )
                _set_uniform_int(prog, "u_scale", self.scale)
                _set_uniform_int(prog, "u_transparent_zero", t_zero)
                asset.primary_tex.use(location=0)
                self._live_palette_tex.use(location=1)
                self._shaders.vao_fallback_smooth.render(moderngl.TRIANGLES)
                return
            _set_uniform_int(self._shaders.indexed, "u_transparent_zero", t_zero)
            asset.primary_tex.use(location=0)
            self._live_palette_tex.use(location=1)
            self._shaders.vao_indexed.render(moderngl.TRIANGLES)
            return

        if asset.mode is HdColorMode.INDEXED:
            _set_uniform_int(self._shaders.indexed, "u_transparent_zero", t_zero)
            asset.primary_tex.use(location=0)
            self._live_palette_tex.use(location=1)
            self._shaders.vao_indexed.render(moderngl.TRIANGLES)
            return

        if asset.mode is HdColorMode.RGBA:
            _set_uniform_vec3(self._shaders.rgba, "u_fade_tint", self._fade_tint)
            _set_uniform_vec4(self._shaders.rgba, "u_flash_mix", self._flash_mix)
            asset.primary_tex.use(location=0)
            self._shaders.vao_rgba.render(moderngl.TRIANGLES)
            return

        assert asset.mask_tex is not None
        _set_uniform_vec3(self._shaders.masked, "u_fade_tint", self._fade_tint)
        _set_uniform_vec4(self._shaders.masked, "u_flash_mix", self._flash_mix)
        asset.primary_tex.use(location=0)
        asset.mask_tex.use(location=1)
        self._base_palette_tex.use(location=2)
        self._live_palette_tex.use(location=3)
        self._shaders.vao_masked.render(moderngl.TRIANGLES)

    def _blit_fbo_texture(
        self,
        src_tex: moderngl.Texture,
        dst_fbo: moderngl.Framebuffer,
        dst_size: tuple[int, int],
        *,
        x: float = 0.0,
        y: float = 0.0,
        w: float | None = None,
        h: float | None = None,
    ) -> None:
        rw = float(dst_size[0]) if w is None else w
        rh = float(dst_size[1]) if h is None else h
        verts = _build_quad_verts(
            x,
            y,
            x + rw,
            y + rh,
            dst_size,
            u0=0.0,
            u1=1.0,
            v0=1.0,
            v1=0.0,
        )
        self._quad_vbo.write(verts.tobytes())
        dst_fbo.use()
        self.ctx.viewport = (0, 0, dst_size[0] * self.scale, dst_size[1] * self.scale)
        self.ctx.disable(moderngl.BLEND)
        self.ctx.scissor = None
        src_tex.use(location=0)
        self._shaders.vao_blit.render(moderngl.TRIANGLES)

    def begin_fight(
        self,
        arena_name: str,
        fallback_bg: npt.NDArray[np.uint8],
        *,
        kind: HdAssetKind = HdAssetKind.ARENA,
        variant: str | None = None,
        palette: Sequence[RGBColor] | None = None,
        base_palette: Sequence[RGBColor] | None = None,
    ) -> None:
        """Initialize ``hd_bg_fbo`` and ``hd_fight_fbo`` with the fight arena (ADR-014)."""
        self._ensure_alive()
        if fallback_bg.shape != (PLAYFIELD_HEIGHT, CANVAS_WIDTH):
            raise ValueError("fight background must be 320 by 200 pixels")
        if palette is not None:
            self.set_palettes(palette, palette if base_palette is None else base_palette)
        elif base_palette is not None:
            self.set_base_palette(base_palette)

        self._bg_fbo.use()
        self.ctx.viewport = (0, 0, self.playfield_size[0], self.playfield_size[1])
        self.ctx.clear(0.0, 0.0, 0.0, 1.0)

        entry = (
            self._pack.resolve_picture(arena_name, variant=variant)
            if kind is HdAssetKind.PICTURE
            else self._pack.resolve_arena(arena_name, variant=variant)
        )
        if entry is not None:
            asset = self._get_or_upload_hd(
                entry, orig_w=CANVAS_WIDTH, orig_h=PLAYFIELD_HEIGHT, cycle_slot=None
            )
        else:
            asset = self._get_or_upload_fallback(kind.value, arena_name, None, fallback_bg)

        self._render_asset_to_fbo(
            self._bg_fbo,
            (CANVAS_WIDTH, PLAYFIELD_HEIGHT),
            asset,
            x=0,
            y=0,
            orig_w=CANVAS_WIDTH,
            orig_h=PLAYFIELD_HEIGHT,
            mirrored=False,
            transparent_zero=False,
            clip_rect=_PLAYFIELD_CLIP_LOCAL,
            allow_smooth_fallback=True,
        )
        self._blit_fbo_texture(self._bg_tex, self._fight_fbo, (CANVAS_WIDTH, PLAYFIELD_HEIGHT))

    def _resolve_fight_draws(
        self,
        draws: Sequence[Draw],
        cels: Mapping[str, DecodedCel],
    ) -> list[tuple[Draw, CelFrame, int, int]]:
        resolved: list[tuple[Draw, CelFrame, int, int]] = []
        for draw in draws:
            cel = cels[draw.cel]
            if not 0 <= draw.frame < len(cel.frames):
                raise ValueError(f"fight draw frame {draw.frame} outside {draw.cel}")
            frame = cel.frames[draw.frame]
            shift = 0 if draw.mirrored else frame.meta.hot_x
            draw_x = s16(draw.x) - shift
            draw_y = s16(draw.y)
            resolved.append((draw, frame, draw_x, draw_y))
        return resolved

    def accept_fight_draws(
        self,
        draws: Sequence[Draw],
        cels: Mapping[str, DecodedCel],
        *,
        variant_for_cel: Callable[[Draw], str | None] | None = None,
    ) -> None:
        """Consume one tick's draws into ``hd_fight_fbo`` and ``hd_bg_fbo`` (ADR-014)."""
        self._ensure_alive()
        resolved = self._resolve_fight_draws(draws, cels)
        self._blit_fbo_texture(self._bg_tex, self._fight_fbo, (CANVAS_WIDTH, PLAYFIELD_HEIGHT))

        for draw, frame, draw_x, draw_y in resolved:
            if frame.meta.width <= 0 or frame.meta.height <= 0:
                continue
            variant = variant_for_cel(draw) if variant_for_cel is not None else None
            entry = self._pack.resolve_cel_frame(draw.cel, draw.frame, variant=variant)
            if entry is not None:
                asset = self._get_or_upload_hd(
                    entry,
                    orig_w=frame.meta.width,
                    orig_h=frame.meta.height,
                    cycle_slot=None,
                )
            else:
                asset = self._get_or_upload_fallback("cel", draw.cel, draw.frame, frame.pixels)
            self._render_asset_to_fbo(
                self._fight_fbo,
                (CANVAS_WIDTH, PLAYFIELD_HEIGHT),
                asset,
                x=draw_x,
                y=draw_y,
                orig_w=frame.meta.width,
                orig_h=frame.meta.height,
                mirrored=draw.mirrored,
                transparent_zero=True,
                clip_rect=_PLAYFIELD_CLIP_LOCAL,
                allow_smooth_fallback=True,
            )
            if draw.stamp:
                self._render_asset_to_fbo(
                    self._bg_fbo,
                    (CANVAS_WIDTH, PLAYFIELD_HEIGHT),
                    asset,
                    x=draw_x,
                    y=draw_y,
                    orig_w=frame.meta.width,
                    orig_h=frame.meta.height,
                    mirrored=draw.mirrored,
                    transparent_zero=True,
                    clip_rect=_PLAYFIELD_CLIP_LOCAL,
                    allow_smooth_fallback=True,
                )

    def render_fight(
        self,
        *,
        background_y_offset: int = 0,
        border_color: tuple[float, float, float] = (0.0, 0.0, 0.0),
    ) -> moderngl.Texture:
        """Blit the retained ``hd_fight_tex`` to ``hd_canvas_fbo`` without mutating stamps."""
        self._ensure_alive()
        self.begin_scene(border_color=border_color)
        self._blit_fbo_texture(
            self._fight_tex,
            self._canvas_fbo,
            (CANVAS_WIDTH, CANVAS_HEIGHT),
            x=0.0,
            y=float(PLAYFIELD_Y_OFFSET + background_y_offset),
            w=float(CANVAS_WIDTH),
            h=float(PLAYFIELD_HEIGHT),
        )
        return self._canvas_tex

    def begin_scene(self, *, border_color: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> None:
        """Clear ``hd_canvas_fbo`` to ``border_color`` for general scene composition."""
        self._ensure_alive()
        self._canvas_fbo.use()
        self.ctx.viewport = (0, 0, self.canvas_size[0], self.canvas_size[1])
        self.ctx.scissor = None
        self.ctx.disable(moderngl.BLEND)
        self.ctx.clear(border_color[0], border_color[1], border_color[2], 1.0)

    def draw_picture(
        self,
        name: str,
        fallback_pixels: npt.NDArray[np.uint8] | None = None,
        *,
        kind: HdAssetKind = HdAssetKind.PICTURE,
        variant: str | None = None,
        cycle_index: int = 0,
        x: int = 0,
        y: int = PLAYFIELD_Y_OFFSET,
        clip_rect: tuple[int, int, int, int] | None = None,
    ) -> None:
        """Draw an HD picture/arena (or 1x fallback) onto ``hd_canvas_fbo``."""
        self._ensure_alive()
        if fallback_pixels is not None:
            orig_h, orig_w = int(fallback_pixels.shape[0]), int(fallback_pixels.shape[1])
        else:
            orig_w, orig_h = CANVAS_WIDTH, PLAYFIELD_HEIGHT

        entry = (
            self._pack.resolve_arena(name, variant=variant)
            if kind is HdAssetKind.ARENA
            else self._pack.resolve_picture(name, variant=variant)
        )
        if entry is not None:
            cycle_slot = (
                cycle_index % len(entry.cycle_frame_paths) if entry.cycle_frame_paths else None
            )
            asset = self._get_or_upload_hd(
                entry, orig_w=orig_w, orig_h=orig_h, cycle_slot=cycle_slot
            )
        elif fallback_pixels is not None:
            asset = self._get_or_upload_fallback(kind.value, name, None, fallback_pixels)
        else:
            return

        self._render_asset_to_fbo(
            self._canvas_fbo,
            (CANVAS_WIDTH, CANVAS_HEIGHT),
            asset,
            x=x,
            y=y,
            orig_w=orig_w,
            orig_h=orig_h,
            mirrored=False,
            transparent_zero=False,
            clip_rect=clip_rect,
            allow_smooth_fallback=True,
        )

    def draw_cel_frame(
        self,
        cel_name: str,
        frame: CelFrame,
        x: int,
        y: int,
        *,
        mirrored: bool = False,
        variant: str | None = None,
        clip_rect: tuple[int, int, int, int] | None = PLAYFIELD_CLIP,
    ) -> None:
        """Draw an HD cel frame (or 1x fallback) onto ``hd_canvas_fbo``."""
        self._ensure_alive()
        w = frame.meta.width
        h = frame.meta.height
        if w <= 0 or h <= 0:
            return
        entry = self._pack.resolve_cel_frame(cel_name, frame.meta.index, variant=variant)
        if entry is not None:
            asset = self._get_or_upload_hd(entry, orig_w=w, orig_h=h, cycle_slot=None)
        else:
            asset = self._get_or_upload_fallback("cel", cel_name, frame.meta.index, frame.pixels)

        self._render_asset_to_fbo(
            self._canvas_fbo,
            (CANVAS_WIDTH, CANVAS_HEIGHT),
            asset,
            x=x,
            y=y,
            orig_w=w,
            orig_h=h,
            mirrored=mirrored,
            transparent_zero=True,
            clip_rect=clip_rect,
            allow_smooth_fallback=True,
        )

    def draw_scene_sprites(
        self,
        sprites: Sequence[tuple[str, SceneSprite]],
        *,
        variant_for_sprite: Callable[[str, SceneSprite], str | None] | None = None,
        clip_rect: tuple[int, int, int, int] = PLAYFIELD_CLIP,
    ) -> list[tuple[str, SceneSprite]]:
        """Sort ``sprites`` by 2.5D painter's order and draw each onto ``hd_canvas_fbo``."""
        self._ensure_alive()
        ordered = sorted(sprites, key=lambda item: (item[1].y, item[1].layer, item[1].order))
        for cel_name, sprite in ordered:
            if sprite.frame.meta.width <= 0 or sprite.frame.meta.height <= 0:
                continue
            draw_x, draw_y = compute_sprite_draw_origin(sprite)
            variant = (
                variant_for_sprite(cel_name, sprite) if variant_for_sprite is not None else None
            )
            self.draw_cel_frame(
                cel_name,
                sprite.frame,
                draw_x,
                draw_y,
                mirrored=sprite.mirrored,
                variant=variant,
                clip_rect=clip_rect,
            )
        return ordered

    def draw_indexed_overlay(
        self,
        pixels: npt.NDArray[np.uint8],
        *,
        x: int = 0,
        y: int = 0,
        transparent_zero: bool = True,
        clip_rect: tuple[int, int, int, int] | None = None,
        cache_key: str | None = None,
    ) -> None:
        """Draw a 1x indexed overlay (e.g., HUD text/bars) onto ``hd_canvas_fbo``."""
        self._ensure_alive()
        if pixels.size == 0 or pixels.ndim != 2:
            return
        h, w = int(pixels.shape[0]), int(pixels.shape[1])
        asset = self._get_or_upload_fallback(
            "overlay", "anon" if cache_key is None else cache_key, None, pixels
        )
        self._render_asset_to_fbo(
            self._canvas_fbo,
            (CANVAS_WIDTH, CANVAS_HEIGHT),
            asset,
            x=x,
            y=y,
            orig_w=w,
            orig_h=h,
            mirrored=False,
            transparent_zero=transparent_zero,
            clip_rect=clip_rect,
            allow_smooth_fallback=False,
        )

    def read_canvas_rgba(self) -> npt.NDArray[np.uint8]:
        """Read back ``hd_canvas_fbo`` as a top-down ``(256*S, 320*S, 4)`` uint8 RGBA array."""
        self._ensure_alive()
        raw = self._canvas_fbo.read(components=4)
        arr = np.frombuffer(raw, dtype=np.uint8).reshape(
            (self.canvas_size[1], self.canvas_size[0], 4)
        )
        return np.ascontiguousarray(arr[::-1, :, :])

    def read_fight_rgba(self) -> npt.NDArray[np.uint8]:
        """Read back ``hd_fight_fbo`` as a top-down ``(200*S, 320*S, 4)`` uint8 RGBA array."""
        self._ensure_alive()
        raw = self._fight_fbo.read(components=4)
        arr = np.frombuffer(raw, dtype=np.uint8).reshape(
            (self.playfield_size[1], self.playfield_size[0], 4)
        )
        return np.ascontiguousarray(arr[::-1, :, :])

    def release(self) -> None:
        """Deterministically release all cached textures, FBOs, LUTs, VAOs, and shaders."""
        if self._released:
            return
        self._released = True
        self._cache.clear()
        self._shaders.release()
        self._quad_vbo.release()
        self._base_palette_tex.release()
        self._live_palette_tex.release()
        self._fight_fbo.release()
        self._fight_tex.release()
        self._bg_fbo.release()
        self._bg_tex.release()
        self._canvas_fbo.release()
        self._canvas_tex.release()
        self._owned_released = self._owned_created
        self._owned_vram_bytes = 0
