"""ModernGL OpenGL 3.3 core presentation and wireframe overlay layer."""

from __future__ import annotations

from pymoonstone.render.overlay import (
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
from pymoonstone.render.renderer import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    PLAYFIELD_HEIGHT,
    PLAYFIELD_Y_OFFSET,
    MoonstoneRenderer,
)

__all__ = [
    "CANVAS_HEIGHT",
    "CANVAS_WIDTH",
    "HITBOX_BOX_COLOR",
    "HITBOX_POINT_COLOR",
    "HURTBOX_OUTER_COLOR",
    "HURTBOX_TIGHT_COLOR",
    "PLAYFIELD_HEIGHT",
    "PLAYFIELD_Y_OFFSET",
    "MoonstoneRenderer",
    "OverlayLine",
    "RGBAFloat",
    "build_crosshair_lines",
    "build_frame_overlay",
    "build_rect_lines",
    "pack_overlay_vertex_array",
]
