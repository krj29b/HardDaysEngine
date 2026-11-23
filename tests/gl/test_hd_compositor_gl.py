"""Standalone OpenGL 3.3 tests for the GPU HD compositor and renderer integration (T19.4)."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import moderngl
import numpy as np
import numpy.typing as npt
import pytest
from PIL import Image

from harddaysengine.assets.cel import CelFrame, CelFrameMeta, DecodedCel
from harddaysengine.assets.hd_pack import HdPack
from harddaysengine.assets.hd_pack_schema import HdAssetKind
from harddaysengine.assets.palette import RGBColor, palette_to_rgba_array
from harddaysengine.render.fight import FightCanvas
from harddaysengine.render.hd_compositor import HdCompositor, compute_fade_and_flash
from harddaysengine.render.presentation import (
    AspectMode,
    CropMode,
    CrtMaskMode,
    CrtSettings,
    DisplaySettings,
    FilterPreset,
    ScaleMode,
    SurroundMode,
    SurroundSettings,
)
from harddaysengine.render.renderer import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    PLAYFIELD_HEIGHT,
    PLAYFIELD_Y_OFFSET,
    HardDaysRenderer,
)
from harddaysengine.render.scene import SceneRenderer, SceneSprite
from harddaysengine.render.viewport import SourceRect
from harddaysengine.sim.fight_script import Draw

if TYPE_CHECKING:
    from pathlib import Path

    from harddaysengine.render.viewport import PresentationViewport

pytestmark = pytest.mark.gl


def _make_palette(*overrides: tuple[int, RGBColor]) -> list[RGBColor]:
    pal: list[RGBColor] = [(0, 0, 0)] * 32
    for idx, rgb in overrides:
        pal[idx] = rgb
    return pal


def _make_cel_frame(
    pixels: npt.NDArray[np.uint8],
    *,
    index: int = 0,
    hot_x: int = 0,
) -> CelFrame:
    h, w = pixels.shape
    meta = CelFrameMeta(
        index=index,
        data_offset=0,
        width=w,
        height=h,
        hot_and_flags=((hot_x & 0x0F) << 4) | 0x01,
        plane_bits=0x1F,
    )
    return CelFrame(meta=meta, pixels=pixels.copy(), mask=pixels != 0)


def _make_cel(frames: list[CelFrame]) -> DecodedCel:
    return DecodedCel(
        frame_count=len(frames),
        packed_size=0,
        unpacked_bits=0,
        frames=tuple(frames),
        raw_planes=b"",
    )


def _write_pack_toml(
    pack_dir: Path,
    *,
    scale: int = 2,
    default_mode: str = "rgba",
    fallback_filter: str = "nearest",
) -> None:
    (pack_dir / "pack.toml").write_text(
        (
            "schema_version = 1\n"
            'name = "Synthetic HD Test"\n'
            f"scale = {scale}\n"
            f'default_mode = "{default_mode}"\n'
            f'fallback_filter = "{fallback_filter}"\n'
        ),
        encoding="utf-8",
    )


def _save_l_png(path: Path, arr: npt.NDArray[np.uint8]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr, mode="L").save(path)


def _save_rgba_png(path: Path, arr: npt.NDArray[np.uint8]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr, mode="RGBA").save(path)


def _present_to_array(
    ctx: moderngl.Context,
    renderer: HardDaysRenderer,
    window_size: tuple[int, int],
    settings: DisplaySettings,
    *,
    hd_tex: moderngl.Texture | None = None,
    hd_scale: int = 1,
) -> tuple[npt.NDArray[np.uint8], PresentationViewport]:
    win_w, win_h = window_size
    out_tex = ctx.texture((win_w, win_h), 4)
    out_fbo = ctx.framebuffer(color_attachments=[out_tex])
    try:
        pv = renderer.present_to_framebuffer(
            out_fbo,
            window_size,
            settings=settings,
            hd_tex=hd_tex,
            hd_scale=hd_scale,
        )
        raw = out_fbo.read(components=4)
        arr = np.frombuffer(raw, dtype=np.uint8).reshape((win_h, win_w, 4))
        return np.ascontiguousarray(arr[::-1, :, :]), pv
    finally:
        out_fbo.release()
        out_tex.release()


def test_hd_mode_indexed_lut_and_sprite_zero_transparency(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    _write_pack_toml(pack_dir, scale=2, default_mode="indexed")

    bg_hd = np.zeros((400, 640), dtype=np.uint8)
    bg_hd[:, :320] = 0
    bg_hd[:, 320:] = 2
    _save_l_png(pack_dir / "arenas" / "fol1.t.png", bg_hd)

    # Odd-width 13x9 logical -> 26x18 HD indexed sprite (exercises alignment=1)
    spr_1x = np.zeros((9, 13), dtype=np.uint8)
    spr_hd = np.zeros((18, 26), dtype=np.uint8)
    spr_hd[:, 6:20] = 6
    _save_l_png(pack_dir / "cels" / "kn1.ob" / "frame_000.png", spr_hd)

    pack = HdPack.load(pack_dir)
    pal_a = _make_palette((0, (12, 24, 36)), (2, (40, 120, 60)), (6, (200, 30, 30)))
    pal_b = _make_palette((0, (50, 10, 80)), (2, (10, 180, 190)), (6, (30, 90, 230)))

    ctx = moderngl.create_standalone_context(require=330)
    comp = HdCompositor(ctx, pack)
    try:
        comp.set_palettes(pal_a, pal_a)
        comp.begin_scene()
        comp.draw_picture(
            "fol1.t",
            np.zeros((200, 320), dtype=np.uint8),
            kind=HdAssetKind.ARENA,
        )
        frame = _make_cel_frame(spr_1x, index=0)
        comp.draw_cel_frame("kn1.ob", frame, 154, PLAYFIELD_Y_OFFSET + 50)
        rgba_a = comp.read_canvas_rgba()

        # Background idx 0 is opaque (12, 24, 36), sprite idx 0 does NOT overwrite background!
        py = (PLAYFIELD_Y_OFFSET + 50) * 2 + 4
        assert tuple(int(c) for c in rgba_a[py, 154 * 2 + 2, :3]) == (12, 24, 36)
        assert tuple(int(c) for c in rgba_a[py, 154 * 2 + 8, :3]) == (200, 30, 30)
        assert tuple(int(c) for c in rgba_a[py, 320 + 20, :3]) == (40, 120, 60)

        # Updating live palette updates all indexed pixels cleanly
        comp.set_live_palette(pal_b)
        comp.begin_scene()
        comp.draw_picture("fol1.t", np.zeros((200, 320), dtype=np.uint8), kind=HdAssetKind.ARENA)
        comp.draw_cel_frame("kn1.ob", frame, 154, PLAYFIELD_Y_OFFSET + 50)
        rgba_b = comp.read_canvas_rgba()
        assert tuple(int(c) for c in rgba_b[py, 154 * 2 + 2, :3]) == (50, 10, 80)
        assert tuple(int(c) for c in rgba_b[py, 154 * 2 + 8, :3]) == (30, 90, 230)
        assert tuple(int(c) for c in rgba_b[py, 320 + 20, :3]) == (10, 180, 190)
    finally:
        comp.release()
        ctx.release()


def test_hd_mode_masked_luminance_recoloring_and_slot_zero_fallback(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    _write_pack_toml(pack_dir, scale=4, default_mode="rgba")

    # 16x12 HD (4x3 logical) diffuse RGBA + 8-bit mask
    diffuse = np.zeros((12, 16, 4), dtype=np.uint8)
    mask = np.zeros((12, 16), dtype=np.uint8)

    # Row 0..3: slot 6 (knight tunic) with dark, mid, bright shading
    diffuse[0:4, 0:4] = (60, 12, 12, 255)
    diffuse[0:4, 4:8] = (140, 28, 28, 255)
    diffuse[0:4, 8:12] = (230, 80, 80, 255)
    mask[0:4, 0:12] = 6

    # Row 4..7: slot 7 (near-black base palette slot guard)
    diffuse[4:8, 0:4] = (100, 100, 100, 255)
    mask[4:8, 0:4] = 7

    # Row 8..11: slot 0 (unmasked armor -> follows u_fade_tint/u_flash_mix)
    diffuse[8:12, 0:4] = (160, 160, 160, 255)
    mask[8:12, 0:4] = 0

    _save_rgba_png(pack_dir / "cels" / "kn1.ob" / "frame_000.png", diffuse)
    _save_l_png(pack_dir / "cels" / "kn1.ob" / "frame_000.mask.png", mask)

    pack = HdPack.load(pack_dir)
    base_pal = _make_palette(
        (1, (160, 160, 160)),
        (6, (200, 40, 40)),
        (7, (0, 0, 0)),
    )
    # Swap slot 6 from red to blue, fade slot 1 to 50%, brighten slot 7
    live_pal = _make_palette(
        (1, (80, 80, 80)),
        (6, (40, 80, 220)),
        (7, (0, 200, 0)),
    )

    ctx = moderngl.create_standalone_context(require=330)
    comp = HdCompositor(ctx, pack)
    try:
        comp.set_palettes(live_pal, base_pal)
        comp.begin_scene()
        frame = _make_cel_frame(np.ones((3, 4), dtype=np.uint8), index=0)
        comp.draw_cel_frame("kn1.ob", frame, 20, PLAYFIELD_Y_OFFSET + 20)
        rgba = comp.read_canvas_rgba()

        ox = 20 * 4
        oy = (PLAYFIELD_Y_OFFSET + 20) * 4

        dark = rgba[oy + 2, ox + 2, :3].astype(int)
        mid = rgba[oy + 2, ox + 6, :3].astype(int)
        bright = rgba[oy + 2, ox + 10, :3].astype(int)
        # Switched hue to blue
        assert dark[2] > dark[0]
        assert mid[2] > mid[0] + 50
        assert bright[2] > bright[0] + 80
        # Preserved dark < mid < bright shading gradient
        assert int(dark.sum()) < int(mid.sum()) < int(bright.sum())

        # Near-black base slot 7 clamped cleanly via 0.04 floor (no zero-div / NaN artifact)
        guarded = rgba[oy + 6, ox + 2, :3].astype(int)
        assert guarded[1] > 0
        assert guarded[0] == 0 and guarded[2] == 0

        # Unmasked slot 0 follows rgba fade_tint/flash_mix path (not palette[0] black)
        unmasked = rgba[oy + 10, ox + 2, :3].astype(int)
        assert int(unmasked.mean()) > 40
    finally:
        comp.release()
        ctx.release()


def test_hd_rgba_fade_flash_identities_and_zero_base_guard(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    _write_pack_toml(pack_dir, scale=2, default_mode="rgba")

    # 8x8 HD (4x4 logical) with dark/bright pixels and transparent corner
    diffuse = np.zeros((8, 8, 4), dtype=np.uint8)
    diffuse[:, :4] = (80, 100, 120, 255)
    diffuse[:, 4:] = (160, 180, 200, 255)
    diffuse[0:2, 0:2] = (10, 20, 30, 0)
    _save_rgba_png(pack_dir / "cels" / "prop.cel" / "frame_000.png", diffuse)

    pack = HdPack.load(pack_dir)
    base_pal = _make_palette((1, (100, 120, 140)), (2, (200, 160, 80)))
    pure_red_pal = _make_palette((1, (255, 0, 0)))

    # Pure single-channel base identity check
    red_fade, red_flash = compute_fade_and_flash(pure_red_pal, pure_red_pal)
    assert red_fade == pytest.approx((1.0, 1.0, 1.0), abs=1e-5)
    assert red_flash[3] == pytest.approx(0.0, abs=1e-6)

    # Zero-base guard check
    all_black = _make_palette()
    z_fade, z_flash = compute_fade_and_flash(base_pal, all_black)
    assert z_fade == (0.0, 0.0, 0.0)
    assert all(np.isfinite(v) for v in (*z_fade, *z_flash))

    ctx = moderngl.create_standalone_context(require=330)
    comp = HdCompositor(ctx, pack)
    try:
        frame = _make_cel_frame(np.ones((4, 4), dtype=np.uint8), index=0)
        ox = 40 * 2
        oy = (PLAYFIELD_Y_OFFSET + 30) * 2

        # 1. live == base -> exact identity
        comp.set_palettes(base_pal, base_pal)
        comp.begin_scene(border_color=(0.2, 0.4, 0.6))
        comp.draw_cel_frame("prop.cel", frame, 40, PLAYFIELD_Y_OFFSET + 30)
        rgba_id = comp.read_canvas_rgba()
        assert tuple(int(c) for c in rgba_id[oy + 4, ox + 2, :3]) == (80, 100, 120)
        assert tuple(int(c) for c in rgba_id[oy + 4, ox + 6, :3]) == (160, 180, 200)
        # Transparent pixel preserves border background
        assert tuple(int(c) for c in rgba_id[oy, ox, :3]) == (51, 102, 153)

        # 2. live == black -> RGB fades to (0, 0, 0) while alpha stays transparent in (0..2)
        comp.set_palettes(all_black, base_pal)
        comp.begin_scene(border_color=(0.2, 0.4, 0.6))
        comp.draw_cel_frame("prop.cel", frame, 40, PLAYFIELD_Y_OFFSET + 30)
        rgba_blk = comp.read_canvas_rgba()
        assert tuple(int(c) for c in rgba_blk[oy + 4, ox + 2, :3]) == (0, 0, 0)
        assert tuple(int(c) for c in rgba_blk[oy, ox, :3]) == (51, 102, 153)

        # 3. 50% fade -> ~0.5x RGB
        half_pal = _make_palette((1, (50, 60, 70)), (2, (100, 80, 40)))
        comp.set_palettes(half_pal, base_pal)
        comp.begin_scene()
        comp.draw_cel_frame("prop.cel", frame, 40, PLAYFIELD_Y_OFFSET + 30)
        rgba_half = comp.read_canvas_rgba()
        assert tuple(int(c) for c in rgba_half[oy + 4, ox + 2, :3]) == pytest.approx(
            (40, 50, 60), abs=2
        )

        # 4. Flash/pulse (live > base) -> brightens without crushing texture contrast
        flash_pal = _make_palette((1, (255, 255, 255)), (2, (255, 255, 255)))
        comp.set_palettes(flash_pal, base_pal)
        comp.begin_scene()
        comp.draw_cel_frame("prop.cel", frame, 40, PLAYFIELD_Y_OFFSET + 30)
        rgba_flash = comp.read_canvas_rgba()
        left_px = rgba_flash[oy + 4, ox + 2, :3].astype(int)
        right_px = rgba_flash[oy + 4, ox + 6, :3].astype(int)
        assert int(left_px.mean()) > 120
        assert int(right_px.mean()) > int(left_px.mean())
    finally:
        comp.release()
        ctx.release()


def test_fight_draw_order_s16_hotx_mirroring_and_parity_vs_fight_canvas(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    _write_pack_toml(pack_dir, scale=2, default_mode="indexed")

    # Odd-width 13x11 asymmetric CelFrame with hot_x=5
    spr_1x = np.zeros((11, 13), dtype=np.uint8)
    spr_1x[1:10, 0:4] = 6
    spr_1x[2:9, 4:10] = 8
    spr_1x[0:4, 10:13] = 15
    spr_hd = np.repeat(np.repeat(spr_1x, 2, axis=0), 2, axis=1)
    _save_l_png(pack_dir / "cels" / "fighter.ob" / "frame_000.png", spr_hd)

    # 320x200 indexed background -> 640x400 HD
    bg_1x = np.full((PLAYFIELD_HEIGHT, CANVAS_WIDTH), 2, dtype=np.uint8)
    bg_1x[20:40, 30:80] = 3
    bg_hd = np.repeat(np.repeat(bg_1x, 2, axis=0), 2, axis=1)
    _save_l_png(pack_dir / "arenas" / "fo1.t.png", bg_hd)

    frame = _make_cel_frame(spr_1x, index=0, hot_x=5)
    cels = {"fighter.ob": _make_cel([frame])}
    pal = _make_palette(
        (2, (20, 60, 20)),
        (3, (80, 90, 80)),
        (6, (200, 40, 40)),
        (8, (240, 120, 40)),
        (15, (250, 250, 50)),
    )
    lut = palette_to_rgba_array(pal, size=32, zero_transparent=False)

    # Non-Y-sorted overlapping draws + s16 wrapping (-4, -2) + mirroring + playfield clipping
    draws = [
        Draw(cel="fighter.ob", frame=0, x=100, y=120, mirrored=False, stamp=False, text=False),
        Draw(cel="fighter.ob", frame=0, x=98, y=80, mirrored=True, stamp=False, text=False),
        Draw(cel="fighter.ob", frame=0, x=65532, y=65534, mirrored=False, stamp=False, text=False),
        Draw(cel="fighter.ob", frame=0, x=315, y=194, mirrored=True, stamp=False, text=False),
    ]

    fight_canvas = FightCanvas(bg_1x, cels)
    fight_canvas.accept(draws)
    expected_rgba = lut[fight_canvas.pixels()]

    pack = HdPack.load(pack_dir)
    ctx = moderngl.create_standalone_context(require=330)
    comp = HdCompositor(ctx, pack)
    try:
        comp.begin_fight("fo1.t", bg_1x, palette=pal)
        # Validate fail-fast on bad cel / frame without mutating FBO
        with pytest.raises(KeyError):
            comp.accept_fight_draws(
                [
                    Draw(
                        cel="missing.ob", frame=0, x=0, y=0, mirrored=False, stamp=False, text=False
                    )
                ],
                cels,
            )
        with pytest.raises(ValueError):
            comp.accept_fight_draws(
                [
                    Draw(
                        cel="fighter.ob", frame=9, x=0, y=0, mirrored=False, stamp=False, text=False
                    )
                ],
                cels,
            )

        comp.accept_fight_draws(draws, cels)
        comp.render_fight(border_color=(0.2, 0.4, 0.6))
        canvas_rgba = comp.read_canvas_rgba()

        # Borders stay untouched (no playfield clip leak from y=65534 or y=194)
        top_border = canvas_rgba[: PLAYFIELD_Y_OFFSET * 2, :, :3]
        bot_border = canvas_rgba[(PLAYFIELD_Y_OFFSET + PLAYFIELD_HEIGHT) * 2 :, :, :3]
        assert np.all(top_border == np.array([51, 102, 153], dtype=np.uint8))
        assert np.all(bot_border == np.array([51, 102, 153], dtype=np.uint8))

        # 100% pixel-for-pixel parity vs FightCanvas at S=2 texel blocks
        playfield = canvas_rgba[
            PLAYFIELD_Y_OFFSET * 2 : (PLAYFIELD_Y_OFFSET + PLAYFIELD_HEIGHT) * 2, :, :
        ]
        downsampled = playfield[0::2, 0::2, :]
        assert np.array_equal(downsampled, expected_rgba)
        assert np.array_equal(playfield[1::2, 1::2, :], expected_rgba)
    finally:
        comp.release()
        ctx.release()


def test_fight_padding_and_anchor_offset_mirror_symmetry(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    _write_pack_toml(pack_dir, scale=2, default_mode="rgba")

    # Logical 6x4 frame -> base 12x8 HD + padding [2, 4, 6, 8] -> 20x20 HD
    padded_rgba = np.full((20, 20, 4), (220, 140, 40, 255), dtype=np.uint8)
    _save_rgba_png(pack_dir / "cels" / "glow.cel" / "frame_000.png", padded_rgba)
    (pack_dir / "cels" / "glow.cel" / "meta.toml").write_text(
        ('[frames.0]\nmode = "rgba"\nscale = 2\npadding = [2, 4, 6, 8]\nanchor_offset = [4, -2]\n'),
        encoding="utf-8",
    )

    pack = HdPack.load(pack_dir)
    frame = _make_cel_frame(np.ones((4, 6), dtype=np.uint8), index=0, hot_x=0)
    cels = {"glow.cel": _make_cel([frame])}
    bg_1x = np.zeros((PLAYFIELD_HEIGHT, CANVAS_WIDTH), dtype=np.uint8)

    ctx = moderngl.create_standalone_context(require=330)
    comp = HdCompositor(ctx, pack)
    try:
        # Unmirrored at (100, 80):
        # x0 = 100 + (4 - 2)/2 = 101 -> HD x=202; x1 = 106 + (4 + 6)/2 = 111 -> HD x=222
        # y0 = 80 + (-2 - 4)/2 = 77 -> HD y=154; y1 = 84 + (-2 + 8)/2 = 87 -> HD y=174
        comp.begin_fight("missing.t", bg_1x, palette=_make_palette((1, (200, 200, 200))))
        comp.accept_fight_draws(
            [Draw(cel="glow.cel", frame=0, x=100, y=80, mirrored=False, stamp=False, text=False)],
            cels,
        )
        fg_unmir = comp.read_fight_rgba()
        ys, xs = np.nonzero(fg_unmir[:, :, 0] > 0)
        assert (int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1) == (
            202,
            222,
            154,
            174,
        )

        # Mirrored at (100, 80):
        # x0 = 100 + (-4 - 6)/2 = 95 -> HD x=190; x1 = 106 + (-4 + 2)/2 = 105 -> HD x=210
        comp.accept_fight_draws(
            [Draw(cel="glow.cel", frame=0, x=100, y=80, mirrored=True, stamp=False, text=False)],
            cels,
        )
        fg_mir = comp.read_fight_rgba()
        ys_m, xs_m = np.nonzero(fg_mir[:, :, 0] > 0)
        assert (int(xs_m.min()), int(xs_m.max()) + 1, int(ys_m.min()), int(ys_m.max()) + 1) == (
            190,
            210,
            154,
            174,
        )
    finally:
        comp.release()
        ctx.release()


def test_fight_permanent_stamps_and_render_purity_with_shake(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    _write_pack_toml(pack_dir, scale=2, default_mode="rgba")

    # Ordinary cel (blue) and overlapping stamp cel (red)
    blue_spr = np.full((16, 16, 4), (30, 80, 230, 255), dtype=np.uint8)
    red_spr = np.full((16, 16, 4), (230, 40, 40, 255), dtype=np.uint8)
    _save_rgba_png(pack_dir / "cels" / "actor.ob" / "frame_000.png", blue_spr)
    _save_rgba_png(pack_dir / "cels" / "blood.cel" / "frame_000.png", red_spr)

    pack = HdPack.load(pack_dir)
    cels = {
        "actor.ob": _make_cel([_make_cel_frame(np.ones((8, 8), dtype=np.uint8))]),
        "blood.cel": _make_cel([_make_cel_frame(np.ones((8, 8), dtype=np.uint8))]),
    }
    bg_1x = np.full((PLAYFIELD_HEIGHT, CANVAS_WIDTH), 1, dtype=np.uint8)
    pal = _make_palette((1, (20, 40, 20)))

    ctx = moderngl.create_standalone_context(require=330)
    comp = HdCompositor(ctx, pack)
    try:
        comp.begin_fight("fo1.t", bg_1x, palette=pal)
        # Tick 1: ordinary draw first, overlapping stamped draw second -> red covers overlap
        comp.accept_fight_draws(
            [
                Draw(cel="actor.ob", frame=0, x=60, y=50, mirrored=False, stamp=False, text=False),
                Draw(cel="blood.cel", frame=0, x=64, y=52, mirrored=False, stamp=True, text=False),
            ],
            cels,
        )
        t1 = comp.read_fight_rgba()
        assert tuple(int(c) for c in t1[50 * 2 + 1, 60 * 2 + 1, :3]) == (30, 80, 230)
        assert tuple(int(c) for c in t1[52 * 2 + 1, 64 * 2 + 1, :3]) == (230, 40, 40)

        # Tick 2: empty draws -> blue actor gone, red stamp remains permanently
        comp.accept_fight_draws([], cels)
        t2 = comp.read_fight_rgba()
        assert tuple(int(c) for c in t2[50 * 2 + 1, 60 * 2 + 1, :3]) == (20, 40, 20)
        assert tuple(int(c) for c in t2[52 * 2 + 1, 64 * 2 + 1, :3]) == (230, 40, 40)

        # Repeated render_fight() with screen shake never mutates stamps
        comp.render_fight(background_y_offset=0)
        baseline = comp.read_canvas_rgba()

        comp.render_fight(background_y_offset=3)
        shaken_down = comp.read_canvas_rgba()
        # Playfield shifts +6 HD pixels down (including into bottom border)
        stamp_y_base = (PLAYFIELD_Y_OFFSET + 52) * 2 + 1
        assert tuple(int(c) for c in shaken_down[stamp_y_base + 6, 64 * 2 + 1, :3]) == (230, 40, 40)
        assert tuple(int(c) for c in shaken_down[PLAYFIELD_Y_OFFSET * 2 + 2, 10, :3]) == (0, 0, 0)

        comp.render_fight(background_y_offset=-3)
        shaken_up = comp.read_canvas_rgba()
        # Top of playfield shifts into top border (not clipped at y=28)
        assert tuple(int(c) for c in shaken_up[PLAYFIELD_Y_OFFSET * 2 - 4, 10, :3]) == (20, 40, 20)

        comp.render_fight(background_y_offset=0)
        after = comp.read_canvas_rgba()
        assert np.array_equal(baseline, after)
    finally:
        comp.release()
        ctx.release()


def test_unreplaced_1x_fallback_nearest_and_smooth_respects_transparency(tmp_path: Path) -> None:
    # Diagonal 11x11 odd-width sprite (unreplaced in pack) over bright background
    spr_1x = np.zeros((11, 11), dtype=np.uint8)
    for row in range(2, 9):
        spr_1x[row, row : row + 3] = 6
    frame = _make_cel_frame(spr_1x, index=0)
    bg_1x = np.full((PLAYFIELD_HEIGHT, CANVAS_WIDTH), 1, dtype=np.uint8)
    pal = _make_palette((1, (220, 220, 220)), (6, (180, 20, 20)), (9, (20, 180, 20)))

    ctx = moderngl.create_standalone_context(require=330)
    near_dir = tmp_path / "near"
    near_dir.mkdir()
    _write_pack_toml(near_dir, scale=4, default_mode="rgba", fallback_filter="nearest")
    smooth_dir = tmp_path / "smooth"
    smooth_dir.mkdir()
    _write_pack_toml(smooth_dir, scale=4, default_mode="rgba", fallback_filter="smooth")

    comp_near = HdCompositor(ctx, HdPack.load(near_dir))
    comp_smooth = HdCompositor(ctx, HdPack.load(smooth_dir))
    try:
        for comp in (comp_near, comp_smooth):
            comp.set_palettes(pal, pal)
            comp.begin_scene()
            comp.draw_picture("bg.piv", bg_1x)
            comp.draw_cel_frame("unreplaced.cel", frame, 50, PLAYFIELD_Y_OFFSET + 40)

        rgba_near = comp_near.read_canvas_rgba()
        rgba_smooth = comp_smooth.read_canvas_rgba()

        ox = 50 * 4
        oy = (PLAYFIELD_Y_OFFSET + 40) * 4

        # Nearest: every 4x4 block is uniform; idx==0 stays (220, 220, 220)
        assert tuple(int(c) for c in rgba_near[oy, ox, :3]) == (220, 220, 220)
        block_spr = rgba_near[oy + 8 : oy + 12, ox + 8 : ox + 12, :3]
        assert np.all(block_spr == np.array([180, 20, 20], dtype=np.uint8))

        # Smooth: outside silhouette remains clean (220, 220, 220) with ZERO dark fringe
        patch_smooth = rgba_smooth[oy : oy + 44, ox : ox + 44, :3]
        patch_near = rgba_near[oy : oy + 44, ox : ox + 44, :3]
        assert int(patch_smooth[:, :, 0].min()) >= 175
        # Diagonal corners are smoothed (different from nearest)
        assert not np.array_equal(patch_smooth, patch_near)

        # Also exercise draw_scene_sprites painter's order & draw_indexed_overlay
        spr_b = _make_cel_frame(np.full((6, 6), 9, dtype=np.uint8), index=0)
        comp_near.begin_scene()
        ordered = comp_near.draw_scene_sprites(
            [
                ("front", SceneSprite(frame=spr_b, x=100, y=90, top_left=True)),
                ("back", SceneSprite(frame=frame, x=98, y=80, top_left=True)),
            ]
        )
        assert [name for name, _ in ordered] == ["back", "front"]
        comp_near.draw_indexed_overlay(np.full((4, 8), 1, dtype=np.uint8), x=4, y=4)
        scene_out = comp_near.read_canvas_rgba()
        assert tuple(int(c) for c in scene_out[(PLAYFIELD_Y_OFFSET + 91) * 4, 101 * 4, :3]) == (
            20,
            180,
            20,
        )
        assert tuple(int(c) for c in scene_out[18, 20, :3]) == (220, 220, 220)
    finally:
        comp_near.release()
        comp_smooth.release()
        ctx.release()


def test_present_to_framebuffer_hd_bypasses_smooth_and_scalefx_with_zero_stages() -> None:
    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    scene = SceneRenderer(renderer)
    hd_tex = ctx.texture((CANVAS_WIDTH * 4, CANVAS_HEIGHT * 4), 4)
    try:
        # Pre-warm Classic SMOOTH and SCALEFX so post_chain has active stage textures
        scene.canvas[:, :] = 1
        scene.flush_to_fbo()
        _present_to_array(ctx, renderer, (1920, 1080), DisplaySettings(filter=FilterPreset.SMOOTH))
        assert renderer.post_chain.active_texture_count > 0
        _present_to_array(ctx, renderer, (1920, 1080), DisplaySettings(filter=FilterPreset.SCALEFX))
        assert renderer.post_chain.active_texture_count > 0

        # Populate distinct 4x HD texture with sub-logical-pixel checkerboard
        ys, xs = np.indices((CANVAS_HEIGHT * 4, CANVAS_WIDTH * 4))
        hd_arr = np.zeros((CANVAS_HEIGHT * 4, CANVAS_WIDTH * 4, 4), dtype=np.uint8)
        hd_arr[:, :, 0] = np.where(((xs + ys) & 1) == 0, 210, 40).astype(np.uint8)
        hd_arr[:, :, 1] = 90
        hd_arr[:, :, 2] = 160
        hd_arr[:, :, 3] = 255
        hd_tex.write(np.ascontiguousarray(hd_arr).tobytes())

        faithful_img, pv = _present_to_array(
            ctx,
            renderer,
            (1920, 1080),
            DisplaySettings(filter=FilterPreset.FAITHFUL, scale_mode=ScaleMode.INTEGER),
            hd_tex=hd_tex,
            hd_scale=4,
        )
        assert pv.source.width == CANVAS_WIDTH
        assert pv.source.height == CANVAS_HEIGHT

        for preset in (FilterPreset.SMOOTH, FilterPreset.SCALEFX):
            bypassed_img, pv_b = _present_to_array(
                ctx,
                renderer,
                (1920, 1080),
                DisplaySettings(filter=preset, scale_mode=ScaleMode.INTEGER),
                hd_tex=hd_tex,
                hd_scale=4,
            )
            assert pv_b == pv
            assert renderer.post_chain.active_texture_count == 0
            assert (
                renderer.post_chain.total_textures_created
                == renderer.post_chain.total_textures_released
            )
            assert np.array_equal(bypassed_img, faithful_img)
    finally:
        hd_tex.release()
        scene.release()
        renderer.release()
        ctx.release()


def test_present_to_framebuffer_hd_scanlines_and_crt_keep_logical_rows() -> None:
    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    hd_tex = ctx.texture((CANVAS_WIDTH * 4, CANVAS_HEIGHT * 4), 4)
    try:
        # Left half bright (240), right half dark (90), plus sub-row detail in row 100
        hd_arr = np.zeros((CANVAS_HEIGHT * 4, CANVAS_WIDTH * 4, 4), dtype=np.uint8)
        hd_arr[:, : (CANVAS_WIDTH * 4) // 2, :3] = 240
        hd_arr[:, (CANVAS_WIDTH * 4) // 2 :, :3] = 90
        hd_arr[:, :, 3] = 255
        # Logical row 100 (HD rows 400..403): top 2 HD rows black, bottom 2 HD rows white
        hd_arr[400:402, 40:120, :3] = 0
        hd_arr[402:404, 40:120, :3] = 255
        # Flip vertically on CPU upload so row 0 lands at FBO top (GL v=1.0)
        hd_tex.write(np.ascontiguousarray(hd_arr[::-1, :, :]).tobytes())

        # 1. SCANLINES in FULL and PLAYFIELD crops retain logical pv.scale_y period (4 and 5)
        for crop, expected_period in ((CropMode.FULL, 4), (CropMode.PLAYFIELD, 5)):
            scan_img, pv = _present_to_array(
                ctx,
                renderer,
                (1920, 1080),
                DisplaySettings(
                    filter=FilterPreset.SCANLINES,
                    crop=crop,
                    scale_mode=ScaleMode.INTEGER,
                ),
                hd_tex=hd_tex,
                hd_scale=4,
            )
            assert round(pv.scale_y) == expected_period
            top_y = pv.window_height - (pv.dest_y + pv.dest_height)
            col = scan_img[
                top_y + 40 * expected_period : top_y + 56 * expected_period,
                pv.dest_x + 200,
                0,
            ]
            rows = col.reshape(16, expected_period)
            assert int(np.max(np.ptp(rows, axis=0))) <= 1
            assert int(rows[0].max()) > int(rows[0].min()) + 20

        # 2. CRT scanlines track logical 4px rows while preserving sub-row HD detail
        crt_cfg = DisplaySettings(
            filter=FilterPreset.CRT,
            scale_mode=ScaleMode.INTEGER,
            crt=CrtSettings(mask=CrtMaskMode.NONE, scanlines=0.75, curvature=0.0, bloom=0.0),
        )
        crt_img, pv_crt = _present_to_array(
            ctx, renderer, (1920, 1080), crt_cfg, hd_tex=hd_tex, hd_scale=4
        )
        top_y = pv_crt.window_height - (pv_crt.dest_y + pv_crt.dest_height)
        scale_y = round(pv_crt.scale_y)
        assert scale_y == 4

        bright_col = crt_img[top_y + 80 * scale_y : top_y + 96 * scale_y, pv_crt.dest_x + 300, 0]
        dark_col = crt_img[top_y + 80 * scale_y : top_y + 96 * scale_y, pv_crt.dest_x + 900, 0]
        bright_rows = bright_col.reshape(16, scale_y).astype(float)
        dark_rows = dark_col.reshape(16, scale_y).astype(float)
        assert float(np.max(np.ptp(bright_rows, axis=0))) <= 1.0
        assert float(np.max(np.ptp(dark_rows, axis=0))) <= 1.0
        bright_ratio = float(bright_rows[0].min() / bright_rows[0].max())
        dark_ratio = float(dark_rows[0].min() / dark_rows[0].max())
        assert bright_ratio < 0.92
        assert dark_ratio < bright_ratio

        # Sub-row HD detail in logical row 100: bottom half of row 100 is brighter than top half
        r100_y = top_y + 100 * scale_y
        r100_x = pv_crt.dest_x + 80
        assert int(crt_img[r100_y + 2, r100_x, 0]) > int(crt_img[r100_y + 1, r100_x, 0]) + 40

        # 3. CRT bloom halo on bright square and clean release at bloom=0.0
        sq_arr = np.zeros((CANVAS_HEIGHT * 4, CANVAS_WIDTH * 4, 4), dtype=np.uint8)
        sq_arr[116 * 4 : 140 * 4, 148 * 4 : 172 * 4, :3] = 255
        sq_arr[:, :, 3] = 255
        hd_tex.write(np.ascontiguousarray(sq_arr[::-1, :, :]).tobytes())

        bloom_on = DisplaySettings(
            filter=FilterPreset.CRT,
            scale_mode=ScaleMode.INTEGER,
            crt=CrtSettings(mask=CrtMaskMode.NONE, scanlines=0.0, curvature=0.0, bloom=0.35),
        )
        img_on, pv_b = _present_to_array(
            ctx, renderer, (1920, 1080), bloom_on, hd_tex=hd_tex, hd_scale=4
        )
        assert renderer.post_chain.active_texture_count == 2
        cy = pv_b.window_height // 2
        probe_x = pv_b.dest_x + round(172 * pv_b.scale_x) + 2
        assert int(img_on[cy, probe_x, 0]) > 15

        bloom_off = DisplaySettings(
            filter=FilterPreset.CRT,
            scale_mode=ScaleMode.INTEGER,
            crt=CrtSettings(mask=CrtMaskMode.NONE, scanlines=0.0, curvature=0.0, bloom=0.0),
        )
        img_off, _ = _present_to_array(
            ctx, renderer, (1920, 1080), bloom_off, hd_tex=hd_tex, hd_scale=4
        )
        assert renderer.post_chain.active_texture_count == 0
        assert int(img_off[cy, probe_x, 0]) == 0
    finally:
        hd_tex.release()
        renderer.release()
        ctx.release()


def test_present_to_framebuffer_hd_surround_and_playfield_crop() -> None:
    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    hd_tex = ctx.texture((CANVAS_WIDTH * 2, CANVAS_HEIGHT * 2), 4)
    try:
        # Magenta overscan borders; split red-left / blue-right playfield
        arr = np.zeros((CANVAS_HEIGHT * 2, CANVAS_WIDTH * 2, 4), dtype=np.uint8)
        arr[:, :, 0] = 255
        arr[:, :, 2] = 255
        arr[56:456, :320, :3] = (230, 10, 10)
        arr[56:456, 320:, :3] = (10, 30, 230)
        arr[:, :, 3] = 255
        hd_tex.write(np.ascontiguousarray(arr).tobytes())

        cfg = DisplaySettings(
            filter=FilterPreset.FAITHFUL,
            scale_mode=ScaleMode.FIT,
            crop=CropMode.PLAYFIELD,
            aspect=AspectMode.PAL,
            surround=SurroundSettings(mode=SurroundMode.BLUR, brightness=0.35),
        )
        img, pv = _present_to_array(ctx, renderer, (2560, 1080), cfg, hd_tex=hd_tex, hd_scale=2)
        assert pv.dest_x > 100
        left_bar = img[:, : pv.dest_x, :3]
        right_bar = img[:, pv.dest_x + pv.dest_width :, :3]
        game_rect = img[
            pv.window_height - (pv.dest_y + pv.dest_height) : pv.window_height - pv.dest_y,
            pv.dest_x : pv.dest_x + pv.dest_width,
            :3,
        ]
        # Zero magenta overscan bleed anywhere in side bars or game viewport
        assert int(left_bar[:, :, 2].max()) <= 3
        assert int(right_bar[:, :, 0].max()) <= 3
        assert int(game_rect[:, : pv.dest_width // 2 - 4, 2].max()) <= 15
        # Left bar red-dominant, right bar blue-dominant
        mid_y = pv.window_height // 2
        assert int(left_bar[mid_y, pv.dest_x // 2, 0]) > 20
        assert int(right_bar[mid_y, right_bar.shape[1] // 2, 2]) > 20
    finally:
        hd_tex.release()
        renderer.release()
        ctx.release()


def test_present_to_framebuffer_hd_scale_mismatch_raises() -> None:
    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    hd_tex = ctx.texture((CANVAS_WIDTH * 4, CANVAS_HEIGHT * 4), 4)
    out_tex = ctx.texture((1280, 1024), 4)
    out_fbo = ctx.framebuffer(color_attachments=[out_tex])
    try:
        with pytest.raises(ValueError, match="does not match expected"):
            renderer.present_to_framebuffer(out_fbo, (1280, 1024), hd_tex=hd_tex, hd_scale=1)
        with pytest.raises(ValueError, match="hd_scale must be >= 1"):
            renderer.present_to_framebuffer(out_fbo, (1280, 1024), hd_tex=hd_tex, hd_scale=0)
    finally:
        out_fbo.release()
        out_tex.release()
        hd_tex.release()
        renderer.release()
        ctx.release()


def test_hd_compositor_lru_eviction_and_100_toggles_zero_leaks(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    _write_pack_toml(pack_dir, scale=4, default_mode="rgba")
    for idx in range(10):
        arr = np.full((16, 16, 4), (20 * idx + 10, 60, 120, 255), dtype=np.uint8)
        _save_rgba_png(pack_dir / "cels" / "item.cel" / f"frame_{idx:03d}.png", arr)

    pack = HdPack.load(pack_dir)
    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    scene = SceneRenderer(renderer)
    try:
        # 1. Bounded LRU eviction test
        comp = HdCompositor(ctx, pack, max_cached_textures=4)
        try:
            comp.begin_scene()
            for idx in range(4):
                frame = _make_cel_frame(np.ones((4, 4), dtype=np.uint8), index=idx)
                comp.draw_cel_frame("item.cel", frame, 10, PLAYFIELD_Y_OFFSET + 10)
            # Re-touch frame 0 so it becomes MRU before inserting 6 more frames
            frame0 = _make_cel_frame(np.ones((4, 4), dtype=np.uint8), index=0)
            comp.draw_cel_frame("item.cel", frame0, 10, PLAYFIELD_Y_OFFSET + 10)
            assert comp.cache_stats.hits == 1

            for idx in range(4, 10):
                frame = _make_cel_frame(np.ones((4, 4), dtype=np.uint8), index=idx)
                comp.draw_cel_frame("item.cel", frame, 10, PLAYFIELD_Y_OFFSET + 10)
                assert comp.cache_stats.entry_count <= 4
                assert (
                    comp.total_textures_created - comp.total_textures_released
                    == comp.active_texture_count
                )

            assert comp.cache_stats.evictions >= 6
            assert comp.estimated_vram_bytes > 0
            # Frame 9 is definitely resident (cache hit)
            frame9 = _make_cel_frame(np.ones((4, 4), dtype=np.uint8), index=9)
            hits_before = comp.cache_stats.hits
            comp.draw_cel_frame("item.cel", frame9, 10, PLAYFIELD_Y_OFFSET + 10)
            assert comp.cache_stats.hits == hits_before + 1
        finally:
            comp.release()
        assert comp.active_texture_count == 0
        assert comp.estimated_vram_bytes == 0
        assert comp.total_textures_created == comp.total_textures_released
        # Idempotent release and use-after-release guard
        comp.release()
        with pytest.raises(RuntimeError, match="already been released"):
            comp.begin_scene()

        # 1b. Byte-budget VRAM eviction test (each 16x16 RGBA frame = 1024 bytes)
        vram_comp = HdCompositor(ctx, pack, max_cached_textures=256, max_cache_vram_bytes=2048)
        try:
            vram_comp.begin_scene()
            for idx in range(5):
                frame = _make_cel_frame(np.ones((4, 4), dtype=np.uint8), index=idx)
                vram_comp.draw_cel_frame("item.cel", frame, 10, PLAYFIELD_Y_OFFSET + 10)
            assert vram_comp.cache_stats.estimated_vram_bytes <= 2048
            assert vram_comp.cache_stats.evictions >= 3
        finally:
            vram_comp.release()
        assert vram_comp.active_texture_count == 0

        # 2. 100-iteration Classic <-> HD toggle test across all presentation presets
        configs = (
            DisplaySettings(filter=FilterPreset.FAITHFUL),
            DisplaySettings(filter=FilterPreset.SCANLINES),
            DisplaySettings(
                filter=FilterPreset.CRT,
                crt=CrtSettings(mask=CrtMaskMode.APERTURE, bloom=0.25),
            ),
            DisplaySettings(
                filter=FilterPreset.SMOOTH,
                surround=SurroundSettings(mode=SurroundMode.BLUR, brightness=0.3),
            ),
            DisplaySettings(
                filter=FilterPreset.SCALEFX,
                crop=CropMode.PLAYFIELD,
                aspect=AspectMode.PAL,
            ),
        )
        frame0 = _make_cel_frame(np.ones((4, 4), dtype=np.uint8), index=0)
        scene.canvas[:, :] = 1
        scene.flush_to_fbo()

        for step in range(100):
            cfg = configs[step % len(configs)]
            if step % 2 == 0:
                hd_comp = HdCompositor(ctx, pack)
                try:
                    hd_comp.begin_scene()
                    hd_comp.draw_cel_frame("item.cel", frame0, 20, PLAYFIELD_Y_OFFSET + 20)
                    _present_to_array(
                        ctx,
                        renderer,
                        (2560, 1080),
                        cfg,
                        hd_tex=hd_comp.output_texture,
                        hd_scale=hd_comp.scale,
                    )
                finally:
                    hd_comp.release()
                assert hd_comp.active_texture_count == 0
                assert hd_comp.total_textures_created == hd_comp.total_textures_released
            else:
                _present_to_array(ctx, renderer, (2560, 1080), cfg)

        # Final faithful off-surround pass releases any post_chain / surround stages
        _present_to_array(
            ctx,
            renderer,
            (2560, 1080),
            DisplaySettings(
                filter=FilterPreset.FAITHFUL,
                surround=SurroundSettings(mode=SurroundMode.OFF),
            ),
        )
        assert renderer.post_chain.active_texture_count == 0
        assert (
            renderer.post_chain.total_textures_created
            == renderer.post_chain.total_textures_released
        )
        assert renderer.surround.active_texture_count == 0
        assert renderer.surround.total_textures_created == renderer.surround.total_textures_released
    finally:
        scene.release()
        renderer.release()
        ctx.release()


def test_hd_cycle_frames_variant_and_mixed_asset_scale(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    _write_pack_toml(pack_dir, scale=4, default_mode="rgba")

    # Picture test_8 with two cycle frames at 4x (1280x800)
    cyc0 = np.full((800, 1280, 4), (10, 40, 200, 255), dtype=np.uint8)
    cyc1 = np.full((800, 1280, 4), (20, 200, 80, 255), dtype=np.uint8)
    _save_rgba_png(pack_dir / "pictures" / "test_8.png", cyc0)
    _save_rgba_png(pack_dir / "pictures" / "test_8__cycle_00.png", cyc0)
    _save_rgba_png(pack_dir / "pictures" / "test_8__cycle_01.png", cyc1)
    (pack_dir / "pictures" / "test_8.meta.toml").write_text(
        'cycle_frames = ["test_8__cycle_00.png", "test_8__cycle_01.png"]\n',
        encoding="utf-8",
    )

    # Base 2x sprite inside 4x pack (exercises scale != pack.scale -> LINEAR filter) + __red variant
    base_spr = np.full((8, 8, 4), (100, 100, 220, 255), dtype=np.uint8)
    red_spr = np.full((16, 16, 4), (230, 30, 30, 255), dtype=np.uint8)
    _save_rgba_png(pack_dir / "cels" / "kn1.ob" / "frame_000.png", base_spr)
    (pack_dir / "cels" / "kn1.ob" / "meta.toml").write_text("scale = 2\n", encoding="utf-8")
    _save_rgba_png(pack_dir / "cels" / "kn1.ob__red" / "frame_000.png", red_spr)

    pack = HdPack.load(pack_dir)
    pal = _make_palette((1, (200, 200, 200)))
    frame = _make_cel_frame(np.ones((4, 4), dtype=np.uint8), index=0)

    ctx = moderngl.create_standalone_context(require=330)
    comp = HdCompositor(ctx, pack)
    try:
        comp.set_palettes(pal, pal)
        comp.begin_scene()
        comp.draw_picture("test_8", cycle_index=0)
        comp.draw_cel_frame("kn1.ob", frame, 20, PLAYFIELD_Y_OFFSET + 20)
        rgba0 = comp.read_canvas_rgba()
        assert tuple(int(c) for c in rgba0[PLAYFIELD_Y_OFFSET * 4 + 4, 4, :3]) == (10, 40, 200)
        assert tuple(int(c) for c in rgba0[(PLAYFIELD_Y_OFFSET + 21) * 4, 21 * 4, :3]) == (
            100,
            100,
            220,
        )

        comp.begin_scene()
        comp.draw_picture("test_8", cycle_index=1)
        comp.draw_cel_frame("kn1.ob", frame, 20, PLAYFIELD_Y_OFFSET + 20, variant="red")
        rgba1 = comp.read_canvas_rgba()
        assert tuple(int(c) for c in rgba1[PLAYFIELD_Y_OFFSET * 4 + 4, 4, :3]) == (20, 200, 80)
        assert tuple(int(c) for c in rgba1[(PLAYFIELD_Y_OFFSET + 21) * 4, 21 * 4, :3]) == (
            230,
            30,
            30,
        )
    finally:
        comp.release()
        ctx.release()


def test_fallback_overlay_shape_collision_and_non_contiguous(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    _write_pack_toml(pack_dir, scale=2, default_mode="rgba")
    pack = HdPack.load(pack_dir)

    pal = _make_palette(
        *[
            (i, ((i * 7) % 250 + 5, (255 - i * 8) % 250 + 5, (i * 17) % 250 + 5))
            for i in range(1, 32)
        ]
    )
    raw_32 = (np.arange(32, dtype=np.uint8) % 31) + 1
    a4x8 = raw_32.reshape(4, 8)
    a8x4 = raw_32.reshape(8, 4)
    sliced = (np.arange(64, dtype=np.uint8).reshape(8, 8)[::2, ::2] % 31) + 1

    ctx = moderngl.create_standalone_context(require=330)
    comp = HdCompositor(ctx, pack)
    try:
        comp.set_palettes(pal, pal)
        comp.begin_scene()
        comp.draw_indexed_overlay(a4x8, x=10, y=10)
        comp.draw_indexed_overlay(a8x4, x=40, y=10)
        assert comp.cache_stats.misses == 2
        assert comp.cache_stats.hits == 0

        rgba = comp.read_canvas_rgba()
        # a8x4[1, 0] == 5; a4x8[0, 1] == 2 (would be hit under raw-byte CRC collision)
        assert tuple(int(c) for c in rgba[11 * 2 + 1, 40 * 2 + 1, :3]) == pal[int(a8x4[1, 0])]
        assert tuple(int(c) for c in rgba[17 * 2 + 1, 43 * 2 + 1, :3]) == pal[int(a8x4[7, 3])]

        # Repeat a8x4 hits cache cleanly; non-contiguous slice uploads cleanly
        comp.draw_indexed_overlay(a8x4, x=60, y=10)
        assert comp.cache_stats.hits == 1
        comp.draw_indexed_overlay(sliced, x=80, y=10)
        assert comp.cache_stats.misses == 3
        rgba2 = comp.read_canvas_rgba()
        assert tuple(int(c) for c in rgba2[10 * 2 + 1, 80 * 2 + 1, :3]) == pal[int(sliced[0, 0])]
        assert tuple(int(c) for c in rgba2[11 * 2 + 1, 81 * 2 + 1, :3]) == pal[int(sliced[1, 1])]
    finally:
        comp.release()
        ctx.release()


@pytest.mark.parametrize("scale", [3, 8])
def test_hd_odd_scale_s3_s8_and_odd_width_r8_alignment(tmp_path: Path, scale: int) -> None:
    pack_dir = tmp_path / f"pack_{scale}"
    pack_dir.mkdir()
    _write_pack_toml(pack_dir, scale=scale, default_mode="indexed")

    ys, xs = np.indices((17 * scale, 13 * scale))
    spr_hd = (((xs // scale) + 2 * (ys // scale)) % 31 + 1).astype(np.uint8)
    _save_l_png(pack_dir / "cels" / "odd.cel" / "frame_000.png", spr_hd)

    diffuse = np.full((17 * scale, 13 * scale, 4), (120, 120, 120, 255), dtype=np.uint8)
    mask = np.full((17 * scale, 13 * scale), 6, dtype=np.uint8)
    _save_rgba_png(pack_dir / "cels" / "maskodd.cel" / "frame_000.png", diffuse)
    _save_l_png(pack_dir / "cels" / "maskodd.cel" / "frame_000.mask.png", mask)
    (pack_dir / "cels" / "maskodd.cel" / "meta.toml").write_text(
        'mode = "masked"\n', encoding="utf-8"
    )

    pack = HdPack.load(pack_dir)
    pal = _make_palette(
        *[
            (i, ((i * 8) % 250 + 5, (250 - i * 7) % 250 + 5, (i * 13) % 250 + 5))
            for i in range(1, 32)
        ]
    )
    frame_1x = _make_cel_frame(
        np.fromfunction(lambda y, x: ((x + 2 * y) % 31) + 1, (17, 13), dtype=int).astype(np.uint8),
        index=0,
    )

    ctx = moderngl.create_standalone_context(require=330)
    comp = HdCompositor(ctx, pack)
    try:
        assert comp.canvas_size == (CANVAS_WIDTH * scale, CANVAS_HEIGHT * scale)
        assert comp.playfield_size == (CANVAS_WIDTH * scale, PLAYFIELD_HEIGHT * scale)
        comp.set_palettes(pal, pal)
        comp.begin_scene()
        comp.draw_cel_frame("odd.cel", frame_1x, 10, PLAYFIELD_Y_OFFSET + 10)
        comp.draw_cel_frame("maskodd.cel", frame_1x, 30, PLAYFIELD_Y_OFFSET + 10)
        comp.draw_cel_frame("unreplaced.cel", frame_1x, 50, PLAYFIELD_Y_OFFSET + 10)
        rgba = comp.read_canvas_rgba()

        for lx, ly in ((0, 0), (12, 8), (7, 16)):
            expected = pal[int(frame_1x.pixels[ly, lx])]
            hy = (PLAYFIELD_Y_OFFSET + 10 + ly) * scale + scale // 2
            hx_hd = (10 + lx) * scale + scale // 2
            hx_fb = (50 + lx) * scale + scale // 2
            assert tuple(int(c) for c in rgba[hy, hx_hd, :3]) == expected
            assert tuple(int(c) for c in rgba[hy, hx_fb, :3]) == expected

        masked_px = rgba[(PLAYFIELD_Y_OFFSET + 18) * scale, 35 * scale, :3]
        assert int(masked_px.sum()) > 0
    finally:
        comp.release()
        ctx.release()


def test_hd_compositor_invalid_scale_raises(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    _write_pack_toml(pack_dir, scale=2, default_mode="rgba")
    base_pack = HdPack.load(pack_dir)

    ctx = moderngl.create_standalone_context(require=330)
    try:
        for bad_scale in (1, 9):
            bad_pack = HdPack(base_pack.root, replace(base_pack.config, scale=bad_scale), ())
            with pytest.raises(ValueError, match="HdPack scale"):
                HdCompositor(ctx, bad_pack)
    finally:
        ctx.release()


def test_hd_mode_masked_slot_31_upper_clamp_and_partial_alpha(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    _write_pack_toml(pack_dir, scale=2, default_mode="rgba")

    # 8x8 HD (4x4 logical) with slot 31 shaded, overbright (shade > 2.0 clamp), and alpha ~ 0.5
    diffuse = np.zeros((8, 8, 4), dtype=np.uint8)
    mask = np.full((8, 8), 31, dtype=np.uint8)
    diffuse[0:4, 0:4] = (40, 40, 40, 255)
    diffuse[0:4, 4:8] = (255, 255, 255, 255)
    diffuse[4:8, 0:4] = (40, 40, 40, 128)
    diffuse[4:8, 4:8] = (40, 40, 40, 0)
    _save_rgba_png(pack_dir / "cels" / "slot31.cel" / "frame_000.png", diffuse)
    _save_l_png(pack_dir / "cels" / "slot31.cel" / "frame_000.mask.png", mask)

    pack = HdPack.load(pack_dir)
    base_pal = _make_palette((31, (40, 40, 40)))
    live_pal = _make_palette((31, (60, 100, 40)))

    ctx = moderngl.create_standalone_context(require=330)
    comp = HdCompositor(ctx, pack)
    try:
        comp.set_palettes(live_pal, base_pal)
        comp.begin_scene(border_color=(0.0, 1.0, 0.0))
        frame = _make_cel_frame(np.ones((4, 4), dtype=np.uint8), index=0)
        comp.draw_cel_frame("slot31.cel", frame, 10, PLAYFIELD_Y_OFFSET + 10)
        rgba = comp.read_canvas_rgba()

        ox = 10 * 2
        oy = (PLAYFIELD_Y_OFFSET + 10) * 2
        # Top-left: shade = 1.0 -> exact live_pal[31]
        assert tuple(int(c) for c in rgba[oy + 1, ox + 1, :3]) == pytest.approx(
            (60, 100, 40), abs=2
        )
        # Top-right: Y(d)/Y(base) = 6.375 -> clamped to 2.0 * live_pal[31] = (120, 200, 80)
        assert tuple(int(c) for c in rgba[oy + 1, ox + 5, :3]) == pytest.approx(
            (120, 200, 80), abs=2
        )
        # Bottom-left: alpha=128/255 over (0, 255, 0) -> (30, 177, 20)
        assert tuple(int(c) for c in rgba[oy + 5, ox + 1, :3]) == pytest.approx(
            (30, 177, 20), abs=3
        )
        # Bottom-right: alpha=0 preserves background
        assert tuple(int(c) for c in rgba[oy + 5, ox + 5, :3]) == (0, 255, 0)
    finally:
        comp.release()
        ctx.release()


def test_fight_semi_transparent_stamp_no_double_blend_and_y_offset_12(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    _write_pack_toml(pack_dir, scale=2, default_mode="rgba")

    blood_spr = np.full((16, 16, 4), (200, 20, 20, 128), dtype=np.uint8)
    actor_spr = np.full((16, 16, 4), (20, 60, 220, 255), dtype=np.uint8)
    _save_rgba_png(pack_dir / "cels" / "blood.cel" / "frame_000.png", blood_spr)
    _save_rgba_png(pack_dir / "cels" / "actor.ob" / "frame_000.png", actor_spr)

    pack = HdPack.load(pack_dir)
    cels = {
        "blood.cel": _make_cel([_make_cel_frame(np.ones((8, 8), dtype=np.uint8))]),
        "actor.ob": _make_cel([_make_cel_frame(np.ones((8, 8), dtype=np.uint8))]),
    }
    bg_1x = np.full((PLAYFIELD_HEIGHT, CANVAS_WIDTH), 1, dtype=np.uint8)
    pal = _make_palette((1, (0, 200, 40)))

    ctx = moderngl.create_standalone_context(require=330)
    comp = HdCompositor(ctx, pack)
    try:
        comp.begin_fight("fo1.t", bg_1x, palette=pal)
        comp.accept_fight_draws(
            [
                Draw(cel="blood.cel", frame=0, x=60, y=50, mirrored=False, stamp=True, text=False),
                Draw(cel="actor.ob", frame=0, x=64, y=52, mirrored=False, stamp=False, text=False),
            ],
            cels,
        )
        t1 = comp.read_fight_rgba()
        # Non-overlapped stamp region blended ONCE over (0, 200, 40): ~ (100, 110, 30)
        assert tuple(int(c) for c in t1[50 * 2 + 1, 60 * 2 + 1, :3]) == pytest.approx(
            (100, 110, 30), abs=3
        )
        assert tuple(int(c) for c in t1[52 * 2 + 1, 64 * 2 + 1, :3]) == (20, 60, 220)

        # Tick 2 with empty draws: retained stamp stays single-blended (no double-blend)
        comp.accept_fight_draws([], cels)
        t2 = comp.read_fight_rgba()
        assert np.array_equal(t2[50 * 2 + 1, 60 * 2 + 1], t1[50 * 2 + 1, 60 * 2 + 1])
        assert tuple(int(c) for c in t2[52 * 2 + 1, 64 * 2 + 1, :3]) == pytest.approx(
            (100, 110, 30), abs=3
        )

        # Large screen shake +/-12 shifts into borders cleanly without mutating fight_fbo
        comp.render_fight(background_y_offset=12, border_color=(0.2, 0.4, 0.6))
        down = comp.read_canvas_rgba()
        assert tuple(int(c) for c in down[PLAYFIELD_Y_OFFSET * 2 + 10, 20, :3]) == (51, 102, 153)
        assert tuple(int(c) for c in down[(PLAYFIELD_Y_OFFSET + 205) * 2, 20, :3]) == (0, 200, 40)

        comp.render_fight(background_y_offset=-12, border_color=(0.2, 0.4, 0.6))
        up = comp.read_canvas_rgba()
        assert tuple(int(c) for c in up[(PLAYFIELD_Y_OFFSET - 8) * 2, 20, :3]) == (0, 200, 40)
        assert tuple(int(c) for c in up[(PLAYFIELD_Y_OFFSET + 195) * 2, 20, :3]) == (51, 102, 153)
        assert np.array_equal(comp.read_fight_rgba(), t2)
    finally:
        comp.release()
        ctx.release()


def test_unreplaced_1x_smooth_picture_opaque_zero_and_isolated_corners(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    _write_pack_toml(pack_dir, scale=4, default_mode="rgba", fallback_filter="smooth")
    pack = HdPack.load(pack_dir)

    pal = _make_palette((0, (30, 60, 180)), (2, (220, 200, 40)), (6, (200, 30, 30)))
    bg_1x = np.zeros((PLAYFIELD_HEIGHT, CANVAS_WIDTH), dtype=np.uint8)
    for r in range(10, 20):
        bg_1x[r, r:] = 2

    # 9x9 sprite: 5x5 solid block (2..6, 2..6) + isolated 1x1 pixel at (1, 1)
    spr_1x = np.zeros((9, 9), dtype=np.uint8)
    spr_1x[2:7, 2:7] = 6
    spr_1x[1, 1] = 2
    frame = _make_cel_frame(spr_1x, index=0)

    ctx = moderngl.create_standalone_context(require=330)
    comp = HdCompositor(ctx, pack)
    try:
        comp.set_palettes(pal, pal)
        # 1. draw_picture uses u_transparent_zero=0: idx 0 is opaque blue and diagonal smooths
        comp.begin_scene(border_color=(0.0, 0.0, 0.0))
        comp.draw_picture("bg.piv", bg_1x)
        pic_rgba = comp.read_canvas_rgba()
        assert tuple(int(c) for c in pic_rgba[(PLAYFIELD_Y_OFFSET + 10) * 4, 2 * 4, :3]) == (
            30,
            60,
            180,
        )
        diag_patch = pic_rgba[
            (PLAYFIELD_Y_OFFSET + 12) * 4 : (PLAYFIELD_Y_OFFSET + 18) * 4,
            12 * 4 : 18 * 4,
            :3,
        ]
        # Intermediate blended colours exist along the 0 <-> 2 diagonal
        is_blue = np.all(diag_patch == np.array([30, 60, 180], dtype=np.uint8), axis=-1)
        is_gold = np.all(diag_patch == np.array([220, 200, 40], dtype=np.uint8), axis=-1)
        assert not bool(np.all(is_blue | is_gold))

        # 2. Solid 90-degree box corner stays sharp & isolated 1-pixel dot has no dark fringe
        comp.begin_scene(border_color=(0.0, 0.0, 0.0))
        comp.draw_picture("bg.piv", np.zeros((PLAYFIELD_HEIGHT, CANVAS_WIDTH), dtype=np.uint8))
        comp.draw_cel_frame("spr.cel", frame, 20, PLAYFIELD_Y_OFFSET + 20)
        spr_rgba = comp.read_canvas_rgba()
        ox = 20 * 4
        oy = (PLAYFIELD_Y_OFFSET + 20) * 4
        # Outer 90-degree bottom-right corner of 5x5 box at (6, 6) stays sharp red
        assert tuple(int(c) for c in spr_rgba[oy + 6 * 4 + 3, ox + 6 * 4 + 3, :3]) == (200, 30, 30)
        # Isolated 1x1 pixel at (1, 1) renders gold without black halo bleed
        iso_patch = spr_rgba[oy + 4 : oy + 8, ox + 4 : ox + 8, :3]
        assert int(iso_patch[:, :, 2].min()) >= 40
        assert tuple(int(c) for c in iso_patch[1, 1]) == (220, 200, 40)
    finally:
        comp.release()
        ctx.release()


def test_hd_cache_vram_lru_move_to_end_and_masked_texture_budget(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    _write_pack_toml(pack_dir, scale=4, default_mode="rgba")
    for idx in range(4):
        arr = np.full((16, 16, 4), (30 * idx + 20, 80, 140, 255), dtype=np.uint8)
        _save_rgba_png(pack_dir / "cels" / "item.cel" / f"frame_{idx:03d}.png", arr)

    # Masked cel (each frame allocates 2 GPU textures: RGBA + R8 mask)
    for idx in range(4):
        rgba = np.full((8, 8, 4), (40 * idx + 20, 120, 120, 255), dtype=np.uint8)
        mask = np.full((8, 8), 6, dtype=np.uint8)
        _save_rgba_png(pack_dir / "cels" / "masked.cel" / f"frame_{idx:03d}.png", rgba)
        _save_l_png(pack_dir / "cels" / "masked.cel" / f"frame_{idx:03d}.mask.png", mask)

    pack = HdPack.load(pack_dir)
    ctx = moderngl.create_standalone_context(require=330)
    try:
        # 1. Explicit move_to_end LRU verification under max_cache_vram_bytes=2048 (fits 2x 1024B)
        vram_comp = HdCompositor(ctx, pack, max_cached_textures=256, max_cache_vram_bytes=2048)
        try:
            vram_comp.begin_scene()
            frames = [_make_cel_frame(np.ones((4, 4), dtype=np.uint8), index=i) for i in range(4)]
            vram_comp.draw_cel_frame("item.cel", frames[0], 10, PLAYFIELD_Y_OFFSET + 10)
            vram_comp.draw_cel_frame("item.cel", frames[1], 10, PLAYFIELD_Y_OFFSET + 10)
            # Touch frame 0 so frame 1 becomes LRU
            vram_comp.draw_cel_frame("item.cel", frames[0], 10, PLAYFIELD_Y_OFFSET + 10)
            assert vram_comp.cache_stats.hits == 1
            vram_comp.draw_cel_frame("item.cel", frames[2], 10, PLAYFIELD_Y_OFFSET + 10)
            assert vram_comp.cache_stats.evictions == 1
            # Frame 0 must still be resident (hit), frame 1 must have been evicted (miss)
            vram_comp.draw_cel_frame("item.cel", frames[0], 10, PLAYFIELD_Y_OFFSET + 10)
            assert vram_comp.cache_stats.hits == 2
            vram_comp.draw_cel_frame("item.cel", frames[1], 10, PLAYFIELD_Y_OFFSET + 10)
            assert vram_comp.cache_stats.misses == 4
            assert vram_comp.cache_stats.evictions == 2
        finally:
            vram_comp.release()
        assert vram_comp.active_texture_count == 0

        # 2. max_cached_textures=4 counts individual GL textures (2 per MASKED -> max 2 entries)
        m_comp = HdCompositor(ctx, pack, max_cached_textures=4)
        try:
            m_comp.begin_scene()
            m_frame = _make_cel_frame(np.ones((2, 2), dtype=np.uint8), index=0)
            for idx in range(4):
                m_comp.draw_cel_frame(
                    "masked.cel", replace(m_frame, meta=replace(m_frame.meta, index=idx)), 10, 40
                )
                live_cache_tex = (
                    m_comp.cache_stats.total_textures_created
                    - m_comp.cache_stats.total_textures_released
                )
                assert live_cache_tex <= 4
            assert m_comp.cache_stats.entry_count == 2
            assert m_comp.cache_stats.evictions == 2
        finally:
            m_comp.release()
        assert m_comp.active_texture_count == 0

        # 3. Edge case: max_cached_textures=1 with 2-texture MASKED asset keeps current entry alive
        one_comp = HdCompositor(ctx, pack, max_cached_textures=1)
        try:
            one_comp.begin_scene()
            m_frame = _make_cel_frame(np.ones((2, 2), dtype=np.uint8), index=0)
            one_comp.draw_cel_frame("masked.cel", m_frame, 10, 40)
            one_comp.draw_cel_frame(
                "masked.cel", replace(m_frame, meta=replace(m_frame.meta, index=1)), 20, 40
            )
            assert one_comp.cache_stats.entry_count == 1
            assert one_comp.cache_stats.evictions == 1
        finally:
            one_comp.release()
        assert one_comp.active_texture_count == 0
    finally:
        ctx.release()


def test_present_to_framebuffer_hd_s3_playfield_pal_crt_bloom_and_blur_surround() -> None:
    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    hd_tex = ctx.texture((CANVAS_WIDTH * 3, CANVAS_HEIGHT * 3), 4)
    try:
        arr = np.zeros((CANVAS_HEIGHT * 3, CANVAS_WIDTH * 3, 4), dtype=np.uint8)
        # Magenta overscan borders; red-left / blue-right playfield + sub-row detail in row 100
        arr[:, :, 0] = 255
        arr[:, :, 2] = 255
        arr[28 * 3 : 228 * 3, : (CANVAS_WIDTH * 3) // 2, :3] = (230, 10, 10)
        arr[28 * 3 : 228 * 3, (CANVAS_WIDTH * 3) // 2 :, :3] = (10, 30, 230)
        arr[100 * 3 : 100 * 3 + 1, 30:90, :3] = (0, 0, 0)
        arr[100 * 3 + 2 : 100 * 3 + 3, 30:90, :3] = (255, 255, 255)
        arr[:, :, 3] = 255
        hd_tex.write(np.ascontiguousarray(arr[::-1, :, :]).tobytes())

        cfg = DisplaySettings(
            filter=FilterPreset.CRT,
            scale_mode=ScaleMode.FIT,
            crop=CropMode.PLAYFIELD,
            aspect=AspectMode.PAL,
            crt=CrtSettings(
                mask=CrtMaskMode.APERTURE,
                mask_strength=0.25,
                scanlines=0.75,
                curvature=0.05,
                bloom=0.35,
            ),
            surround=SurroundSettings(mode=SurroundMode.BLUR, brightness=0.35),
        )
        img, pv = _present_to_array(ctx, renderer, (2560, 1080), cfg, hd_tex=hd_tex, hd_scale=3)
        assert pv.source == SourceRect(0, PLAYFIELD_Y_OFFSET, CANVAS_WIDTH, PLAYFIELD_HEIGHT)
        assert pv.dest_x > 100
        assert renderer.post_chain.active_texture_count == 2
        assert renderer.surround.active_texture_count == 2

        left_bar = img[:, : pv.dest_x, :3]
        right_bar = img[:, pv.dest_x + pv.dest_width :, :3]
        assert int(left_bar[:, :, 2].max()) <= 3
        assert int(right_bar[:, :, 0].max()) <= 3
        mid_y = pv.window_height // 2
        assert int(left_bar[mid_y, pv.dest_x // 2, 0]) > 15
        assert int(right_bar[mid_y, right_bar.shape[1] // 2, 2]) > 15

        out_tex = ctx.texture((1280, 720), 4)
        out_fbo = ctx.framebuffer(color_attachments=[out_tex])
        try:
            for bad_scale in (0, -1, 4):
                with pytest.raises(ValueError):
                    renderer.present_to_framebuffer(
                        out_fbo, (1280, 720), hd_tex=hd_tex, hd_scale=bad_scale
                    )
        finally:
            out_fbo.release()
            out_tex.release()
    finally:
        hd_tex.release()
        renderer.release()
        ctx.release()
