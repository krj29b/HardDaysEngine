"""ModernGL OpenGL 3.3 core presentation and wireframe overlay layer."""

from __future__ import annotations

from harddaysengine.render.hd_compositor import (
    DEFAULT_HD_CACHE_MAX_TEXTURES,
    DEFAULT_HD_CACHE_MAX_VRAM_BYTES,
    HdCacheStats,
    HdCompositor,
    compute_fade_and_flash,
)
from harddaysengine.render.overlay import (
    HITBOX_BOX_COLOR,
    HITBOX_POINT_COLOR,
    HURTBOX_OUTER_COLOR,
    HURTBOX_TIGHT_COLOR,
    OverlayLine,
    RGBAFloat,
    build_crosshair_lines,
    build_frame_overlay,
    build_rect_lines,
    pack_overlay_vertex_array,
)
from harddaysengine.render.renderer import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    PLAYFIELD_HEIGHT,
    PLAYFIELD_Y_OFFSET,
    HardDaysRenderer,
)

__all__ = [
    "CANVAS_HEIGHT",
    "CANVAS_WIDTH",
    "DEFAULT_HD_CACHE_MAX_TEXTURES",
    "DEFAULT_HD_CACHE_MAX_VRAM_BYTES",
    "HITBOX_BOX_COLOR",
    "HITBOX_POINT_COLOR",
    "HURTBOX_OUTER_COLOR",
    "HURTBOX_TIGHT_COLOR",
    "PLAYFIELD_HEIGHT",
    "PLAYFIELD_Y_OFFSET",
    "HardDaysRenderer",
    "HdCacheStats",
    "HdCompositor",
    "OverlayLine",
    "RGBAFloat",
    "build_crosshair_lines",
    "build_frame_overlay",
    "build_rect_lines",
    "compute_fade_and_flash",
    "pack_overlay_vertex_array",
]
