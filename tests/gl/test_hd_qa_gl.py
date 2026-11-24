"""Standalone OpenGL 3.3 tests for T19.7 Inspector HD preview & Audit side-by-side QA (§14.7)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import moderngl
import numpy as np
import pytest
from PIL import Image

from harddaysengine.app.audit import AuditRig
from harddaysengine.app.fight_audit import TROGG_ARENAS, build_fight_audit, fight_audit_frames
from harddaysengine.app.fight_runtime import FightLoader
from harddaysengine.app.game import GameSession
from harddaysengine.app.hd_host import HdHostPresenter
from harddaysengine.app.input import InputFrame, PlayerInput
from harddaysengine.app.inspector import (
    BackgroundItem,
    InspectorState,
    SpriteSetItem,
    render_inspector_frame_hd,
    run_inspector_headless,
)
from harddaysengine.app.scene_assets import GameAssetBundle
from harddaysengine.assets.adf import DiskSet
from harddaysengine.assets.cel import CelFrame, CelFrameMeta, DecodedCel, decode_cel
from harddaysengine.assets.extract import cel_frame_to_rgba, encode_mask_png_bytes
from harddaysengine.assets.fight_palette import (
    REGION_GLEN,
    SCENE_FLYER,
    assemble_fight_palette,
    parse_fight_palette_tables,
)
from harddaysengine.assets.hd_pack import HdPack
from harddaysengine.assets.hit import HitFrame, HitPoint, HitSet
from harddaysengine.assets.hunk import parse_hunk_executable
from harddaysengine.assets.palette import (
    cell_word_to_rgb,
    decode_palette_word,
    default_combat_palette,
)
from harddaysengine.assets.picture import DecodedPicture, decode_picture_pack
from harddaysengine.assets.sheet_data import parse_sheet_data
from harddaysengine.render.hd_compositor import HdCompositor
from harddaysengine.render.renderer import HardDaysRenderer
from harddaysengine.render.scene import SceneRenderer, default_bitmap_font
from harddaysengine.sim.actor import Actor
from harddaysengine.sim.inventory import Inventory
from harddaysengine.sim.rng import Rng
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


def _save_masked_cel_2x(
    cel: DecodedCel,
    pal: tuple[tuple[int, int, int], ...],
    slots: frozenset[int],
    out_dir: Path,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for fr in cel.frames:
        if fr.meta.width == 0 or fr.meta.height == 0:
            continue
        rgba = cel_frame_to_rgba(fr, pal)
        img = Image.fromarray(rgba, mode="RGBA").resize(
            (fr.meta.width * 2, fr.meta.height * 2), Image.Resampling.NEAREST
        )
        img.save(out_dir / f"frame_{fr.meta.index:03d}.png")
        mask = np.where(np.isin(fr.pixels, sorted(slots)), fr.pixels, 0).astype(np.uint8)
        if bool(np.any(mask > 0)):
            mask2 = np.repeat(np.repeat(mask, 2, axis=0), 2, axis=1)
            (out_dir / f"frame_{fr.meta.index:03d}.mask.png").write_bytes(
                encode_mask_png_bytes(mask2)
            )


@pytest.mark.adf
def test_masked_template_knights_match_classic_colors_for_all_players(
    adf_dir: Path, tmp_path: Path
) -> None:
    """Masked Blue-template kn*.ob and ki.cel recolor to exact Classic RGB for all 4 players."""
    disks = DiskSet.from_directory(adf_dir)
    mog = parse_hunk_executable("mog", disks.read_file("mog"))
    tables = parse_fight_palette_tables(mog)
    test_pics = decode_picture_pack(disks.read_file("test"))
    base_words = tuple(decode_palette_word(w) for w in test_pics[0].raw_palette)
    kn_template_pal = tuple(
        cell_word_to_rgb(w)
        for w in assemble_fight_palette(
            base_words, tables, scene=SCENE_FLYER, region=REGION_GLEN, first_kind=0
        )
    )
    sheet_data = parse_sheet_data(mog)
    ki_words = list(sheet_data.palette)
    ki_words[28:30] = sheet_data.class_pairs[0]
    ki_template_pal = tuple(cell_word_to_rgb(w) for w in ki_words)

    pack_dir = tmp_path / "masked_knights_2x"
    pack_dir.mkdir()
    (pack_dir / "pack.toml").write_text(
        'schema_version = 1\nname = "Masked Knights"\nversion = "1.0"\n'
        'scale = 2\ndefault_mode = "rgba"\nfallback_filter = "nearest"\n',
        encoding="utf-8",
    )
    for kn_name in ("kn1.ob", "kn2.ob", "kn3.ob", "kn4.ob"):
        _save_masked_cel_2x(
            decode_cel(disks.read_file(kn_name)),
            kn_template_pal,
            frozenset({6, 7, 8}),
            pack_dir / "cels" / kn_name,
        )
    _save_masked_cel_2x(
        decode_cel(disks.read_file("ki.cel")),
        ki_template_pal,
        frozenset({28, 29, 30, 31}),
        pack_dir / "cels" / "ki.cel",
    )

    pack = HdPack.load(pack_dir)
    bundle = GameAssetBundle(disks)
    loader = FightLoader(bundle)
    arena = next(a for a in loader.defs.arenas if a.name == TROGG_ARENAS[0])

    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    scene_renderer = SceneRenderer(renderer)
    font = default_bitmap_font()
    hd_host = HdHostPresenter(pack)
    try:
        compositor = hd_host.ensure_active(ctx, enabled=True)
        assert compositor is not None
        # Verify all 4 player classes (Blue=0, Yellow=1, Green=2, Red=3) match Classic RGB exactly
        # on masked knight pixels (no dark-blue luma crushing and no frozen Blue colors).
        for kind in (0, 1, 2, 3):
            knights = [Actor(kind=i) for i in range(4)]
            knights[0].kind = kind
            runtime = loader.creature(
                arena,
                "fo1.t",
                knights,
                Actor(),
                {1: Inventory()},
                Rng(0x13579BDF),
                first=1,
            )
            runtime.scene.advance(0)
            runtime.scene.render(scene_renderer)
            scene_renderer.flush_to_fbo()
            classic_rgb = renderer.read_canvas_rgba()[..., :3]
            classic_2x = np.repeat(np.repeat(classic_rgb, 2, axis=0), 2, axis=1)

            hd_host.render_fight_history(
                compositor, runtime.scene.hd_history, live_palette=runtime.scene.palette
            )
            hd_rgb = compositor.read_canvas_rgba()[..., :3]
            max_diff = int(np.max(np.abs(hd_rgb.astype(np.int16) - classic_2x.astype(np.int16))))
            assert max_diff <= 2, f"kind={kind} fight HD masked RGB drifted by {max_diff}"

        # Verify shared Status sheet (ki.cel) recolors Red knight (kind=3) identically to Classic.
        session = GameSession(disk_set=disks, players=(KnightId.RED,))
        session._open_sheet(9)
        for _ in range(40):
            session.step_tick(InputFrame())
        session.render_to_scene(scene_renderer, font)
        classic_sheet = renderer.read_canvas_rgba()[..., :3]
        classic_sheet_2x = np.repeat(np.repeat(classic_sheet, 2, axis=0), 2, axis=1)

        hd_tex = hd_host.render_session_hd(session, compositor)
        assert hd_tex is not None
        hd_sheet = compositor.read_canvas_rgba()[..., :3]
        sheet_diff = int(
            np.max(np.abs(hd_sheet.astype(np.int16) - classic_sheet_2x.astype(np.int16)))
        )
        assert sheet_diff <= 2, f"Red knight sheet HD masked RGB drifted by {sheet_diff}"
    finally:
        hd_host.release()
        scene_renderer.release()
        renderer.release()
        ctx.release()
