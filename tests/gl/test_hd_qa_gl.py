"""Standalone OpenGL 3.3 tests for T19.7 Inspector HD preview & Audit side-by-side QA (§14.7)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import moderngl
import numpy as np
import pytest
from PIL import Image

from harddaysengine.app.audit import AuditRig
from harddaysengine.app.fight_audit import TROGG_ARENAS, build_fight_audit, fight_audit_frames
from harddaysengine.app.input import InputFrame, PlayerInput
from harddaysengine.app.inspector import (
    BackgroundItem,
    InspectorState,
    SpriteSetItem,
    render_inspector_frame_hd,
    run_inspector_headless,
)
from harddaysengine.assets.adf import DiskSet
from harddaysengine.assets.cel import CelFrame, CelFrameMeta, DecodedCel
from harddaysengine.assets.hd_pack import HdPack
from harddaysengine.assets.hit import HitFrame, HitPoint, HitSet
from harddaysengine.assets.palette import default_combat_palette
from harddaysengine.assets.picture import DecodedPicture
from harddaysengine.render.hd_compositor import HdCompositor
from harddaysengine.render.renderer import HardDaysRenderer
from harddaysengine.sim.types import KnightId

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.gl


def _make_synthetic_pack(pack_dir: Path, *, scale: int = 2) -> HdPack:
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "pack.toml").write_text(
        "schema_version = 1\n"
        'name = "Inspector QA Synthetic"\n'
        'version = "1.0"\n'
        f"scale = {scale}\n"
        'default_mode = "rgba"\n'
        'fallback_filter = "nearest"\n',
        encoding="utf-8",
    )
    arena_path = pack_dir / "arenas" / "fol1.t.png"
    arena_path.parent.mkdir(parents=True, exist_ok=True)
    bg_arr = np.zeros((200 * scale, 320 * scale, 4), dtype=np.uint8)
    bg_arr[:, :, 0] = 20
    bg_arr[:, :, 1] = 30
    bg_arr[:, :, 2] = 160
    bg_arr[:, :, 3] = 255
    Image.fromarray(bg_arr, mode="RGBA").save(arena_path)

    cel_path = pack_dir / "cels" / "kn4.ob" / "frame_000.png"
    cel_path.parent.mkdir(parents=True, exist_ok=True)
    spr_arr = np.zeros((32 * scale, 32 * scale, 4), dtype=np.uint8)
    spr_arr[:, :, 0] = 210
    spr_arr[:, :, 1] = 190
    spr_arr[:, :, 2] = 40
    spr_arr[:, :, 3] = 255
    Image.fromarray(spr_arr, mode="RGBA").save(cel_path)
    return HdPack.load(pack_dir)


def _make_synthetic_inspector_state() -> InspectorState:
    pic = DecodedPicture(
        width=320,
        height=200,
        planes=5,
        packed_size=0,
        raw_palette=tuple([0] * 32),
        palette=default_combat_palette(),
        pixels=np.full((200, 320), 1, dtype=np.uint8),
        raw_planes=b"",
    )
    frame = CelFrame(
        meta=CelFrameMeta(
            index=0,
            data_offset=0,
            width=32,
            height=32,
            plane_bits=0x1F,
            hot_and_flags=0x0000,
        ),
        pixels=np.full((32, 32), 2, dtype=np.uint8),
        mask=np.ones((32, 32), dtype=np.uint8),
    )
    cel = DecodedCel(frame_count=1, packed_size=0, unpacked_bits=0, frames=(frame,), raw_planes=b"")
    hit_frame = HitFrame(
        frame_index=0,
        hit_type=1,
        max_x=28,
        max_y=24,
        points=(HitPoint(x=24, y=14), HitPoint(x=20, y=18)),
    )
    hit_set = HitSet(
        name="kn4",
        norm_name="kn4",
        frames=(hit_frame,),
    )
    return InspectorState(
        backgrounds=[BackgroundItem(name="fol1.t", picture=pic)],
        sprites=[SpriteSetItem(name="kn4.ob", stem="kn4", cel=cel, hit_set=hit_set)],
    )


def test_inspector_hd_preview_renders_hd_art_and_scaled_hitbox_overlay(tmp_path: Path) -> None:
    """Inspector HD preview composites HD arena + cel and draws scaled hurtbox/hitbox lines."""
    pack = _make_synthetic_pack(tmp_path / "hd_pack", scale=2)
    state = _make_synthetic_inspector_state()
    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    compositor = HdCompositor(ctx, pack)
    try:
        render_inspector_frame_hd(state, renderer, compositor)
        rgba = compositor.read_canvas_rgba()
        assert rgba.shape == (512, 640, 4)

        # Playfield background pixel outside sprite reflects HD RGBA blue tint (20, 30, 160).
        bg_px = rgba[28 * 2 + 20, 20, :3]
        assert int(bg_px[2]) > 140 and int(bg_px[0]) < 40

        # Center sprite pixel reflects HD cel gold fill (210, 190, 40).
        spr_px = rgba[(28 + 100) * 2, 160 * 2, :3]
        assert int(spr_px[0]) > 180 and int(spr_px[1]) > 160 and int(spr_px[2]) < 80

        # Green hurtbox wireframe and red collide.hit attack lines appear on the HD canvas.
        green_mask = (rgba[:, :, 1] > 200) & (rgba[:, :, 0] < 80) & (rgba[:, :, 2] < 100)
        red_mask = (rgba[:, :, 0] > 210) & (rgba[:, :, 1] < 90) & (rgba[:, :, 2] < 90)
        assert int(np.count_nonzero(green_mask)) > 40
        assert int(np.count_nonzero(red_mask)) > 20

        # Toggling flip mirrors the cel and hitboxes without error.
        state.toggle_flip()
        render_inspector_frame_hd(state, renderer, compositor)
        flipped_rgba = compositor.read_canvas_rgba()
        assert flipped_rgba.shape == (512, 640, 4)
        flipped_red = (
            (flipped_rgba[:, :, 0] > 210)
            & (flipped_rgba[:, :, 1] < 90)
            & (flipped_rgba[:, :, 2] < 90)
        )
        assert int(np.count_nonzero(flipped_red)) > 20

        compositor.release()
        assert compositor.active_texture_count == 0
    finally:
        compositor.release()
        renderer.release()
        ctx.release()


@pytest.mark.adf
def test_real_disk_inspector_headless_hd_and_audit_side_by_side(
    adf_dir: Path, tmp_path: Path
) -> None:
    """Real-disk headless inspector HD PNG and AuditRig side-by-side comparison with hitboxes."""
    pack_dir = tmp_path / "real_qa_pack"
    pack_dir.mkdir()
    (pack_dir / "pack.toml").write_text(
        'schema_version = 1\nname = "Real QA"\nversion = "1.0"\nscale = 2\n'
        'default_mode = "rgba"\nfallback_filter = "nearest"\n',
        encoding="utf-8",
    )
    pack = HdPack.load(pack_dir)
    disks = DiskSet.from_directory(adf_dir)

    shot_path = tmp_path / "inspector_hd.png"
    rgba = run_inspector_headless(
        disks,
        frames=4,
        cel_name="kn4",
        background_name="fol1.t",
        screenshot_path=shot_path,
        hd_pack=pack,
    )
    assert rgba.shape == (512, 640, 4)
    assert shot_path.is_file()
    with Image.open(shot_path) as saved:
        assert saved.size == (640, 512)

    rig = AuditRig(adf_dir, hd_pack=pack, show_hitboxes=True)
    try:
        session = rig.new_session()
        for _ in range(5):
            session.step_tick(InputFrame())
        session.step_tick(InputFrame(players={KnightId.RED: PlayerInput(skip_intro=True)}))
        for _ in range(40):
            session.step_tick(InputFrame())
        menu_shot = rig.capture(session, "menu_compare")
        assert menu_shot.rgb.shape == (512, 1280, 3)
        assert len(np.unique(menu_shot.rgb[:, :640].reshape(-1, 3), axis=0)) > 8
        assert len(np.unique(menu_shot.rgb[:, 640:].reshape(-1, 3), axis=0)) > 8

        fight_audit = build_fight_audit(rig.fight_assets(), TROGG_ARENAS[0])
        next(fight_audit_frames(fight_audit))
        fight_shot = rig.capture_fight(fight_audit.scene, "fight_compare")
        assert fight_shot.rgb.shape == (512, 1280, 3)
        # Green hurtbox wireframe pixels should appear on both Classic (left) and HD (right) halves.
        left = fight_shot.rgb[:, :640]
        right = fight_shot.rgb[:, 640:]
        left_green = (left[:, :, 1] > 200) & (left[:, :, 0] < 60) & (left[:, :, 2] < 100)
        right_green = (right[:, :, 1] > 200) & (right[:, :, 0] < 60) & (right[:, :, 2] < 100)
        assert int(np.count_nonzero(left_green)) > 20
        assert int(np.count_nonzero(right_green)) > 20
    finally:
        rig.release()
