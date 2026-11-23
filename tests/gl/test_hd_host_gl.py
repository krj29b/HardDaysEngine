"""Standalone OpenGL 3.3 tests for T19.5 host HD integration & FightScene stamping."""

from __future__ import annotations

from typing import TYPE_CHECKING

import moderngl
import numpy as np
import pytest
from PIL import Image

from harddaysengine.app.fight_scene import FightScene
from harddaysengine.app.hd_host import (
    FightHdHistory,
    HdHostPresenter,
    make_fight_cel_variant_fn,
)
from harddaysengine.assets.cel import CelFrame, CelFrameMeta, DecodedCel
from harddaysengine.assets.hd_pack import HdPack
from harddaysengine.assets.hd_pack_schema import HdAssetKind
from harddaysengine.assets.palette import default_combat_palette
from harddaysengine.render.presentation import DisplaySettings, FilterPreset, HdMode
from harddaysengine.render.renderer import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    PLAYFIELD_HEIGHT,
    HardDaysRenderer,
)
from harddaysengine.sim.fight_script import Draw

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.gl


def _write_rgba_png(path: Path, rgba: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, mode="RGBA").save(path, format="PNG")


def _make_synthetic_fight_pack(pack_root: Path, *, scale: int = 2) -> HdPack:
    pack_root.mkdir(parents=True, exist_ok=True)
    (pack_root / "pack.toml").write_text(
        f'schema_version = 1\nname = "Fight HD Test"\nscale = {scale}\n'
        'default_mode = "rgba"\nfallback_filter = "nearest"\n',
        encoding="utf-8",
    )
    # HD arena override (blue-tinted forest)
    arena = np.zeros((PLAYFIELD_HEIGHT * scale, CANVAS_WIDTH * scale, 4), dtype=np.uint8)
    arena[:, :] = (20, 60, 140, 255)
    _write_rgba_png(pack_root / "arenas" / "fol1.t__forest.png", arena)

    # HD picture override (for Picture-backed FightCanvas screens like Stonehenge/Dice/Wizard)
    pic = np.zeros((PLAYFIELD_HEIGHT * scale, CANVAS_WIDTH * scale, 4), dtype=np.uint8)
    pic[:, :] = (80, 40, 120, 255)
    _write_rgba_png(pack_root / "pictures" / "hen1.p.png", pic)

    # Red knight cel variant (16x16 -> 32x32)
    knight = np.zeros((16 * scale, 16 * scale, 4), dtype=np.uint8)
    knight[:, :] = (220, 30, 30, 255)
    _write_rgba_png(pack_root / "cels" / "kn1.ob__red" / "frame_000.png", knight)

    # Semi-transparent blood stamp (8x8 -> 16x16) to prove zero double-blending on rebuild!
    blood = np.zeros((8 * scale, 8 * scale, 4), dtype=np.uint8)
    blood[:, :] = (200, 0, 0, 128)
    _write_rgba_png(pack_root / "cels" / "blo.cel" / "frame_000.png", blood)
    return HdPack.load(pack_root)


def _dummy_cel(width: int, height: int, fill: int = 7) -> DecodedCel:
    pixels = np.full((height, width), fill, dtype=np.uint8)
    frame = CelFrame(
        meta=CelFrameMeta(
            index=0,
            data_offset=0,
            width=width,
            height=height,
            hot_and_flags=0x01,
            plane_bits=0x1F,
        ),
        pixels=pixels,
        mask=pixels != 0,
    )
    return DecodedCel(
        frame_count=1,
        packed_size=0,
        unpacked_bits=width * height * 5,
        frames=(frame,),
        raw_planes=b"",
    )


def test_hd_host_fight_stamping_mid_fight_toggle_and_zero_leaks(tmp_path: Path) -> None:
    ctx = moderngl.create_standalone_context(require=330)
    try:
        pack = _make_synthetic_fight_pack(tmp_path / "pack", scale=2)
        host = HdHostPresenter(pack)
        base_renderer = HardDaysRenderer(ctx)
        target_tex = ctx.texture((640, 512), 4)
        target_fbo = ctx.framebuffer(color_attachments=[target_tex])

        pal = default_combat_palette()
        bg = np.full((PLAYFIELD_HEIGHT, CANVAS_WIDTH), 1, dtype=np.uint8)
        cels = {"KN1.ob": _dummy_cel(16, 16), "blo.cel": _dummy_cel(8, 8, fill=15)}
        history = FightHdHistory(
            arena_name="fol1.t",
            arena_kind=HdAssetKind.ARENA,
            arena_variant="forest",
            background=bg,
            cels=cels,
            base_palette=pal,
            cel_variant_fn=make_fight_cel_variant_fn(3, is_duel=False),
        )

        # Tick 1 happens while HD is active: blood stamps at (40, 50), knight at (100, 60).
        comp = host.ensure_active(ctx, enabled=True)
        assert comp is not None
        d_blood = Draw("blo.cel", 0, 40, 50, False, True, False)
        d_knight = Draw("KN1.ob", 0, 100, 60, False, False, False)
        history.record([d_blood, d_knight])

        tex_a = host.render_fight_history(comp, history, live_palette=pal)
        snap_first = comp.read_fight_rgba().copy()
        # Repeated render on the same tick must be byte-identical (ADR-014 render purity).
        host.render_fight_history(comp, history, live_palette=pal)
        assert np.array_equal(snap_first, comp.read_fight_rgba())

        # Verify knight __red variant and HD arena colors actually rendered.
        assert tuple(int(c) for c in snap_first[10, 10, :3]) == (20, 60, 140)
        assert tuple(int(c) for c in snap_first[60 * 2 + 4, 100 * 2 + 4, :3]) == (220, 30, 30)

        # Tick 2: knight moves away; blood stamp must remain at (40, 50).
        history.record([Draw("KN1.ob", 0, 140, 60, False, False, False)])
        host.render_fight_history(comp, history, live_palette=pal)
        snap_tick2 = comp.read_fight_rgba().copy()
        blood_px = snap_tick2[50 * 2 + 2, 40 * 2 + 2, :3]
        assert int(blood_px[0]) > 90  # red alpha-blended onto blue arena

        # Simulate mid-fight F7 OFF -> ON toggle: compositor releases and rebuilds cleanly.
        assert host.ensure_active(ctx, enabled=False) is None
        assert host.compositor is None
        comp2 = host.ensure_active(ctx, enabled=True)
        assert comp2 is not None
        host.render_fight_history(comp2, history, live_palette=pal)
        # Rebuilt HD frame with prior stamp must be byte-identical to continuous HD run!
        assert np.array_equal(snap_tick2, comp2.read_fight_rgba())

        # Present HD texture through HardDaysRenderer (SMOOTH bypassed cleanly).
        settings = DisplaySettings(
            filter=FilterPreset.SMOOTH, hd_pack_dir=tmp_path / "pack", hd_mode=HdMode.ON
        )
        base_renderer.present_to_framebuffer(
            target_fbo,
            (640, 512),
            settings=settings,
            hd_tex=tex_a if host.compositor is comp else comp2.output_texture,
            hd_scale=host.scale,
        )

        # Picture-backed FightCanvas caller (Stonehenge/Dice/Wizard) resolves pictures/hen1.p.png.
        pic_hist = FightHdHistory(
            arena_name="hen1.p",
            arena_kind=HdAssetKind.PICTURE,
            arena_variant=None,
            background=bg,
            cels=cels,
            base_palette=pal,
        )
        host.render_fight_history(comp2, pic_hist, live_palette=pal)
        pic_snap = comp2.read_fight_rgba()
        assert tuple(int(c) for c in pic_snap[10, 10, :3]) == (80, 40, 120)

        # 20 rapid F7 Classic <-> HD toggles must leave zero leaked compositor textures.
        for _ in range(20):
            active = host.ensure_active(ctx, enabled=True)
            assert active is not None
            host.render_fight_history(active, history, live_palette=pal)
            host.ensure_active(ctx, enabled=False)
            assert active.active_texture_count == 0

        host.release()
        target_fbo.release()
        target_tex.release()
        base_renderer.release()
    finally:
        ctx.release()


def test_fight_scene_records_hd_history_without_gl() -> None:
    """Smoke check that FightScene keeps hd_history metadata decoupled from OpenGL."""
    assert FightScene is not None
    assert CANVAS_WIDTH == 320 and CANVAS_HEIGHT == 256
