"""Hitbox (red) and hurtbox (green) wireframe overlay geometry builder (ARCHITECTURE.md §4.2)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import numpy.typing as npt

from harddaysengine.assets.hit import HitFrame, compute_cel_hurtbox

if TYPE_CHECKING:
    from collections.abc import Sequence

    from harddaysengine.assets.cel import CelFrame

RGBAFloat = tuple[float, float, float, float]

# Bright green for cel hurtbox bounds; bright red for weapon attack box and points (T4.2).
HURTBOX_OUTER_COLOR: RGBAFloat = (0.1, 0.85, 0.25, 0.75)
HURTBOX_TIGHT_COLOR: RGBAFloat = (0.0, 1.0, 0.3, 0.95)
HITBOX_BOX_COLOR: RGBAFloat = (1.0, 0.2, 0.2, 0.85)
HITBOX_POINT_COLOR: RGBAFloat = (1.0, 0.1, 0.1, 1.0)


@dataclass(slots=True, frozen=True)
class OverlayLine:
    """Single 2D line segment `(x0, y0) -> (x1, y1)` in virtual canvas pixel coordinates."""

    x0: float
    y0: float
    x1: float
    y1: float
    r: float
    g: float
    b: float
    a: float


def build_rect_lines(
    x: float,
    y: float,
    width: float,
    height: float,
    color: RGBAFloat,
) -> list[OverlayLine]:
    """Build 4 line segments outlining the rectangle `[x, x + width] x [y, y + height]`."""
    if width <= 0 or height <= 0:
        return []
    r, g, b, a = color
    x1 = x + width
    y1 = y + height
    return [
        OverlayLine(x, y, x1, y, r, g, b, a),
        OverlayLine(x1, y, x1, y1, r, g, b, a),
        OverlayLine(x1, y1, x, y1, r, g, b, a),
        OverlayLine(x, y1, x, y, r, g, b, a),
    ]


def build_crosshair_lines(
    x: float,
    y: float,
    radius: float,
    color: RGBAFloat,
) -> list[OverlayLine]:
    """Build a 2-segment crosshair centered at `(x, y)` marking a weapon attack point."""
    r, g, b, a = color
    return [
        OverlayLine(x - radius, y, x + radius, y, r, g, b, a),
        OverlayLine(x, y - radius, x, y + radius, r, g, b, a),
    ]


def build_frame_overlay(
    draw_x: int,
    draw_y: int,
    frame: CelFrame,
    hit_frame: HitFrame | None = None,
) -> list[OverlayLine]:
    """Build hurtbox (green) and hitbox (red) lines for `frame` drawn at `(draw_x, draw_y)`."""
    lines: list[OverlayLine] = []
    hurt = compute_cel_hurtbox(frame)
    if hurt.cel_width > 0 and hurt.cel_height > 0:
        lines.extend(
            build_rect_lines(
                float(draw_x),
                float(draw_y),
                float(hurt.cel_width),
                float(hurt.cel_height),
                HURTBOX_OUTER_COLOR,
            )
        )
    if hurt.opaque_pixels > 0:
        lines.extend(
            build_rect_lines(
                float(draw_x + hurt.tight_x),
                float(draw_y + hurt.tight_y),
                float(hurt.tight_width),
                float(hurt.tight_height),
                HURTBOX_TIGHT_COLOR,
            )
        )

    if hit_frame is not None and hit_frame.count > 0:
        cel_w = frame.meta.width
        unflipped = frame.meta.unflipped
        box_x = draw_x if unflipped else (draw_x + cel_w - hit_frame.max_x)
        lines.extend(
            build_rect_lines(
                float(box_x),
                float(draw_y),
                float(hit_frame.max_x),
                float(hit_frame.max_y),
                HITBOX_BOX_COLOR,
            )
        )
        for pt in hit_frame.points:
            px = (draw_x + pt.x) if unflipped else (draw_x + cel_w - pt.x)
            py = draw_y + pt.y
            lines.extend(build_crosshair_lines(float(px), float(py), 2.5, HITBOX_POINT_COLOR))

    return lines


def pack_overlay_vertex_array(
    lines: Sequence[OverlayLine],
    canvas_width: int = 320,
    canvas_height: int = 256,
) -> npt.NDArray[np.float32]:
    """Pack `OverlayLine` segments into a `(2 * len(lines), 6)` float32 NDC + RGBA vertex array."""
    if not lines:
        return np.zeros((0, 6), dtype=np.float32)
    verts = np.zeros((len(lines) * 2, 6), dtype=np.float32)
    inv_w = 2.0 / float(canvas_width)
    inv_h = 2.0 / float(canvas_height)
    for idx, ln in enumerate(lines):
        v0 = idx * 2
        v1 = v0 + 1
        verts[v0, 0] = ln.x0 * inv_w - 1.0
        verts[v0, 1] = 1.0 - ln.y0 * inv_h
        verts[v0, 2:] = (ln.r, ln.g, ln.b, ln.a)
        verts[v1, 0] = ln.x1 * inv_w - 1.0
        verts[v1, 1] = 1.0 - ln.y1 * inv_h
        verts[v1, 2:] = (ln.r, ln.g, ln.b, ln.a)
    return verts
