"""Unit tests for Phase 4 50 Hz accumulator, hitbox/hurtbox overlay, and inspector state."""

from __future__ import annotations

import pytest

from harddaysengine.app import (
    MAX_FRAME_DELTA_NS,
    TICK_NS,
    Accumulator50Hz,
    InspectorState,
    build_inspector_state,
)
from harddaysengine.assets import (
    DiskSet,
    OFSVolume,
    decode_cel,
    flip_cel_frame,
    parse_collide_hit,
)
from harddaysengine.render import (
    HITBOX_BOX_COLOR,
    HITBOX_POINT_COLOR,
    HURTBOX_OUTER_COLOR,
    HURTBOX_TIGHT_COLOR,
    build_crosshair_lines,
    build_frame_overlay,
    build_rect_lines,
    pack_overlay_vertex_array,
)
from tests.fixtures.synthetic_adf import (
    build_synthetic_arena,
    build_synthetic_cel,
    build_synthetic_ofs_image,
    build_synthetic_picture,
)


def test_accumulator_50hz_exact_and_clamping() -> None:
    acc = Accumulator50Hz()
    assert acc.advance(0) == 0
    assert acc.advance(-500) == 0
    assert acc.advance(TICK_NS // 2) == 0
    assert acc.alpha_permille == 500
    assert acc.advance(TICK_NS // 2) == 1
    assert acc.total_ticks == 1
    assert acc.alpha_permille == 0

    # Spiral-of-death clamping at 10 ticks (200 ms)
    ticks = acc.advance(MAX_FRAME_DELTA_NS * 5)
    assert ticks == 10
    assert acc.total_ticks == 11


def test_overlay_geometry_hurtbox_and_hitbox() -> None:
    assert build_rect_lines(10.0, 20.0, 0.0, 5.0, HURTBOX_OUTER_COLOR) == []
    rect = build_rect_lines(10.0, 20.0, 30.0, 40.0, HURTBOX_OUTER_COLOR)
    assert len(rect) == 4
    cross = build_crosshair_lines(50.0, 60.0, 2.0, HITBOX_POINT_COLOR)
    assert len(cross) == 2

    cel = decode_cel(build_synthetic_cel(width=16, height=8))
    hit_sets = parse_collide_hit("kn4\n02 01 004 003 012 006\n99\n")
    hf = hit_sets["kn4"].frames[0]

    lines = build_frame_overlay(100, 80, cel.frames[0], hf)
    # 4 outer hurtbox + 4 tight hurtbox + 4 attack box + 2 * 2 crosshairs = 16 lines
    assert len(lines) == 16
    colors = {(ln.r, ln.g, ln.b, ln.a) for ln in lines}
    assert HURTBOX_OUTER_COLOR in colors
    assert HURTBOX_TIGHT_COLOR in colors
    assert HITBOX_BOX_COLOR in colors
    assert HITBOX_POINT_COLOR in colors

    # Flipped frame reflects hitbox X coordinates
    flipped_frame = flip_cel_frame(cel.frames[0])
    flipped_lines = build_frame_overlay(100, 80, flipped_frame, hf)
    assert len(flipped_lines) == 16

    verts = pack_overlay_vertex_array(lines, 320, 256)
    assert verts.shape == (32, 6)
    assert pack_overlay_vertex_array([], 320, 256).shape == (0, 6)


def test_inspector_state_machine_and_50hz_stepping() -> None:
    test_pack = b"".join(build_synthetic_picture(planes=5, fill_index=i + 1) for i in range(9))
    vol_b = OFSVolume(
        build_synthetic_ofs_image(
            {
                "HE1.ob": build_synthetic_cel(width=16, height=8),
                "kn4.ob": build_synthetic_cel(width=20, height=12),
                "collide.hit": b"kn4\n00\n01 01 010 006\n99\n",
                "test": test_pack,
                "fol1.t": build_synthetic_arena(),
                "gll1.t": build_synthetic_arena(),
            }
        )
    )
    ds = DiskSet([vol_b])
    state = build_inspector_state(ds)

    assert state.current_sprite_set.stem == "kn4"
    assert state.current_background.name == "fol1.t"
    assert "kn4.ob" in state.status_title()

    # 50 Hz stepping: normal mode advances frame every 6 VBLs
    initial_frame = state.frame_index
    for _ in range(5):
        state.tick_vbl()
    assert state.frame_index == initial_frame
    state.tick_vbl()
    assert state.frame_index != initial_frame

    # Fast mode advances every 5 VBLs
    state.toggle_speed()
    assert state.fast_mode is True
    f_before = state.frame_index
    for _ in range(5):
        state.tick_vbl()
    assert state.frame_index != f_before

    # Pause stops automatic stepping
    state.toggle_play()
    assert state.playing is False
    f_paused = state.frame_index
    for _ in range(10):
        state.tick_vbl()
    assert state.frame_index == f_paused

    # Manual controls
    state.next_frame()
    state.prev_frame()
    state.next_cel()
    assert state.current_sprite_set.stem == "he1"
    state.prev_cel()
    assert state.current_sprite_set.stem == "kn4"

    state.next_background()
    assert state.current_background.name == "gll1.t"
    state.prev_background()
    assert state.current_background.name == "fol1.t"

    state.toggle_flip()
    assert state.current_frame.meta.unflipped is False
    state.toggle_overlay()
    assert state.show_overlay is False
    state.toggle_scanlines()
    assert state.scanlines is False

    assert state.select_cel_by_name("HE1.ob") is True
    assert state.select_cel_by_name("nonexistent") is False
    assert state.select_background_by_name("gll1") is True
    assert state.select_background_by_name("nonexistent") is False

    with pytest.raises(ValueError, match="background"):
        InspectorState([], state.sprites)
    with pytest.raises(ValueError, match="sprite"):
        InspectorState(state.backgrounds, [])
