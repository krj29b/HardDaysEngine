"""OpenGL 3.3 tests for T19.6 gameplay scene HD integration (map cycling, towns, sheets, menus)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import moderngl
import numpy as np
import pytest
from PIL import Image

from harddaysengine.app.game import GameScene, GameSession
from harddaysengine.app.hd_host import HdHostPresenter
from harddaysengine.app.input import InputFrame, PlayerInput
from harddaysengine.assets.adf import DiskSet
from harddaysengine.assets.hd_pack import HdPack
from harddaysengine.render.renderer import PLAYFIELD_HEIGHT, PLAYFIELD_Y_OFFSET
from harddaysengine.sim.types import KnightId

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.gl


def _write_rgba(path: Path, rgba: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, mode="RGBA").save(path, format="PNG")


def _write_mask(path: Path, mask: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(mask, mode="L").save(path, format="PNG")


def _make_gameplay_pack(
    pack_root: Path,
    *,
    scale: int = 2,
    use_cycle_frames: bool = False,
    mask_slot: int = 20,
) -> HdPack:
    pack_root.mkdir(parents=True, exist_ok=True)
    (pack_root / "pack.toml").write_text(
        f'schema_version = 1\nname = "Gameplay HD"\nscale = {scale}\n'
        'default_mode = "rgba"\nfallback_filter = "nearest"\n',
        encoding="utf-8",
    )
    h, w = PLAYFIELD_HEIGHT * scale, 320 * scale
    if use_cycle_frames:
        f0 = np.full((h, w, 4), (10, 40, 200, 255), dtype=np.uint8)
        f1 = np.full((h, w, 4), (200, 180, 20, 255), dtype=np.uint8)
        _write_rgba(pack_root / "pictures" / "test_8.png", f0)
        _write_rgba(pack_root / "pictures" / "test_8__cycle_00.png", f0)
        _write_rgba(pack_root / "pictures" / "test_8__cycle_01.png", f1)
        (pack_root / "pictures" / "test_8.meta.toml").write_text(
            'cycle_frames = ["test_8__cycle_00.png", "test_8__cycle_01.png"]\n',
            encoding="utf-8",
        )
    else:
        # Left half masked to `mask_slot`, right half unmasked green land.
        map_rgba = np.full((h, w, 4), (40, 140, 40, 255), dtype=np.uint8)
        map_rgba[:, : w // 2] = (128, 128, 128, 255)
        mask = np.zeros((h, w), dtype=np.uint8)
        mask[:, : w // 2] = mask_slot
        _write_rgba(pack_root / "pictures" / "test_8.png", map_rgba)
        _write_mask(pack_root / "pictures" / "test_8.mask.png", mask)

    # Distinct HD overrides for Highwood town, ch.piv (menu / Next Day), and message.piv
    _write_rgba(
        pack_root / "pictures" / "highwood.piv.png",
        np.full((h, w, 4), (120, 90, 30, 255), dtype=np.uint8),
    )
    _write_rgba(
        pack_root / "pictures" / "ch.piv.png",
        np.full((h, w, 4), (30, 50, 110, 255), dtype=np.uint8),
    )
    _write_rgba(
        pack_root / "pictures" / "message.piv.png",
        np.full((h, w, 4), (90, 20, 90, 255), dtype=np.uint8),
    )
    return HdPack.load(pack_root)


def test_intro_and_ending_cleanly_fall_back_to_classic(tmp_path: Path) -> None:
    ctx = moderngl.create_standalone_context(require=330)
    try:
        pack = _make_gameplay_pack(tmp_path / "pack")
        host = HdHostPresenter(pack)
        comp = host.ensure_active(ctx, enabled=True)
        assert comp is not None

        title_session = GameSession(seed=7, players=(KnightId.RED,), start_scene=GameScene.TITLE)
        assert host.render_session_hd(title_session, comp) is None

        ending_session = GameSession(seed=7, players=(KnightId.RED,), start_scene=GameScene.ENDING)
        assert host.render_session_hd(ending_session, comp) is None

        host.release()
    finally:
        ctx.release()


@pytest.mark.adf
def test_real_disk_overworld_cycling_mask_and_multiframe_sequences(
    adf_dir: Path, tmp_path: Path
) -> None:
    ctx = moderngl.create_standalone_context(require=330)
    try:
        disk_set = DiskSet.from_directory(adf_dir)
        session = GameSession(
            seed=42,
            players=(KnightId.RED,),
            disk_set=disk_set,
            start_scene=GameScene.OVERWORLD,
        )
        assert session.overworld.rules is not None
        rules = session.overworld.rules
        cycle_slot = rules.cycle_start

        # Settle overworld entry fade so live_palette reflects cycling cleanly.
        for _ in range(30):
            session.step_tick(InputFrame())
        hash_before = session.state_hash()

        # 1. Masked RGBA map cycles live on GPU when overworld.colors steps.
        mask_pack = _make_gameplay_pack(
            tmp_path / "mask_pack", scale=2, use_cycle_frames=False, mask_slot=cycle_slot
        )
        host_mask = HdHostPresenter(mask_pack)
        comp_mask = host_mask.ensure_active(ctx, enabled=True)
        assert comp_mask is not None

        tex = host_mask.render_session_hd(session, comp_mask)
        assert tex is not None
        frame_a = comp_mask.read_canvas_rgba().copy()
        assert session.state_hash() == hash_before

        # Advance across the full cycle range so every cycling palette entry visits `cycle_slot`.
        period = max(1, rules.cycle_period)
        cycle_steps = max(2, rules.cycle_end - rules.cycle_start + 1)
        y_sample = (PLAYFIELD_Y_OFFSET + 10) * 2
        masked_colors = {tuple(int(c) for c in frame_a[y_sample, 4, :3])}
        land_color = tuple(int(c) for c in frame_a[y_sample, 600, :3])
        for _ in range(cycle_steps):
            for _ in range(period):
                session.step_tick(InputFrame())
            host_mask.render_session_hd(session, comp_mask)
            frame_next = comp_mask.read_canvas_rgba()
            assert tuple(int(c) for c in frame_next[y_sample, 600, :3]) == land_color
            masked_colors.add(tuple(int(c) for c in frame_next[y_sample, 4, :3]))
        assert len(masked_colors) > 1
        host_mask.release()

        # 2. Multi-frame cycle_frames sequence swaps frames every cycle_period ticks.
        seq_pack = _make_gameplay_pack(tmp_path / "seq_pack", scale=2, use_cycle_frames=True)
        host_seq = HdHostPresenter(seq_pack)
        comp_seq = host_seq.ensure_active(ctx, enabled=True)
        assert comp_seq is not None

        session.tick_count = 0
        host_seq.render_session_hd(session, comp_seq)
        seq_0 = comp_seq.read_canvas_rgba().copy()
        session.tick_count = period
        host_seq.render_session_hd(session, comp_seq)
        seq_1 = comp_seq.read_canvas_rgba().copy()
        assert int(seq_0[y_sample, 4, 2]) > int(seq_0[y_sample, 4, 0])  # blue frame 0
        assert int(seq_1[y_sample, 4, 0]) > int(seq_1[y_sample, 4, 2])  # gold frame 1
        host_seq.release()
    finally:
        ctx.release()


@pytest.mark.adf
def test_real_disk_menus_towns_sheets_and_calendar_hd_preserve_hashes(
    adf_dir: Path, tmp_path: Path
) -> None:
    ctx = moderngl.create_standalone_context(require=330)
    try:
        disk_set = DiskSet.from_directory(adf_dir)
        pack = _make_gameplay_pack(tmp_path / "pack", scale=2)
        host = HdHostPresenter(pack)
        comp = host.ensure_active(ctx, enabled=True)
        assert comp is not None

        # Main menu & knight select
        s_menu = GameSession(
            seed=99,
            players=(KnightId.RED,),
            disk_set=disk_set,
            start_scene=GameScene.KNIGHT_SELECT,
        )
        for _ in range(25):
            s_menu.step_tick(InputFrame())
        h_menu = s_menu.state_hash()
        assert host.render_session_hd(s_menu, comp) is not None
        assert s_menu.state_hash() == h_menu
        menu_px = comp.read_canvas_rgba()
        assert tuple(int(c) for c in menu_px[(PLAYFIELD_Y_OFFSET + 2) * 2, 4, :3]) == (30, 50, 110)

        # Enter knight-pick portraits screen
        s_menu.step_tick(InputFrame(players={KnightId.RED: PlayerInput(confirm=True)}))
        h_pick = s_menu.state_hash()
        assert host.render_session_hd(s_menu, comp) is not None
        assert s_menu.state_hash() == h_pick

        # Status sheet via Space on overworld
        s_world = GameSession(
            seed=99,
            players=(KnightId.RED,),
            disk_set=disk_set,
            start_scene=GameScene.OVERWORLD,
        )
        for _ in range(25):
            s_world.step_tick(InputFrame())
        s_world.step_tick(InputFrame(key=ord(" ")))
        s_world.step_tick(InputFrame())
        assert s_world.scene == GameScene.STATUS
        h_sheet = s_world.state_hash()
        assert host.render_session_hd(s_world, comp) is not None
        assert s_world.state_hash() == h_sheet

        # Farewell screen (GAME_OVER)
        s_over = GameSession(
            seed=99,
            players=(KnightId.RED,),
            disk_set=disk_set,
            start_scene=GameScene.OVERWORLD,
        )
        s_over.scene = GameScene.GAME_OVER
        h_over = s_over.state_hash()
        assert host.render_session_hd(s_over, comp) is not None
        assert s_over.state_hash() == h_over

        host.release()
        assert comp.active_texture_count == 0
    finally:
        ctx.release()
