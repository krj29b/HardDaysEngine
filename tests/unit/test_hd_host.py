"""Unit tests for HD host config, F7/F10 pre-router toggles, and FightHdHistory (T19.5)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pygame
import pytest

from harddaysengine.app.config import (
    ENV_HD_PACK_DIR,
    ConfigError,
    resolve_display_settings,
)
from harddaysengine.app.display_preview import DisplayPreview
from harddaysengine.app.game import GameScene, GameSession
from harddaysengine.app.hd_host import (
    FightHdHistory,
    knight_class_variant,
    make_fight_cel_variant_fn,
    region_variant,
)
from harddaysengine.app.input import InputRouter
from harddaysengine.app.window import WindowController
from harddaysengine.assets.fight_palette import (
    REGION_FOREST,
    REGION_GLEN,
    REGION_SWAMP,
    REGION_WASTELAND,
)
from harddaysengine.assets.hd_pack_schema import HdAssetKind
from harddaysengine.assets.palette import default_combat_palette
from harddaysengine.cli.main import main as cli_main
from harddaysengine.render.presentation import (
    CrtSettings,
    DisplaySettings,
    FilterPreset,
    FullscreenMode,
    HdMode,
)
from harddaysengine.sim.fight_script import Draw
from harddaysengine.sim.types import KnightId

if TYPE_CHECKING:
    from pathlib import Path


class _FakeWindowBackend:
    def __init__(self) -> None:
        self.size = (960, 768)

    def set_windowed(self, size: tuple[int, int]) -> None:
        self.size = size

    def set_desktop_fullscreen(self) -> None:
        self.size = (1920, 1080)

    def set_exclusive_fullscreen(self) -> None:
        self.size = (1920, 1080)

    def get_window_size(self) -> tuple[int, int]:
        return self.size

    def get_refresh_rate(self) -> int:
        return 60


def test_hd_pack_config_precedence_and_relative_resolution(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    cfg = cfg_dir / "config.toml"
    cfg.write_text(
        '[display]\nhd_pack_dir = "packs/file_pack"\nhd_mode = "auto"\n', encoding="utf-8"
    )

    # 1. Config file (relative resolved against config file parent)
    from_file = resolve_display_settings(cfg)
    assert from_file.hd_pack_dir == cfg_dir / "packs" / "file_pack"
    assert from_file.hd_mode is HdMode.AUTO
    assert from_file.hd_enabled is True

    # 2. Environment overrides config file
    env_pack = tmp_path / "env_pack"
    from_env = resolve_display_settings(cfg, environ={ENV_HD_PACK_DIR: str(env_pack)})
    assert from_env.hd_pack_dir == env_pack
    assert from_env.hd_enabled is True

    # 3. CLI flag overrides env and config file
    cli_pack = tmp_path / "cli_pack"
    from_cli = resolve_display_settings(
        cfg,
        hd_pack=str(cli_pack),
        hd_mode="off",
        environ={ENV_HD_PACK_DIR: str(env_pack)},
    )
    assert from_cli.hd_pack_dir == cli_pack
    assert from_cli.hd_mode is HdMode.OFF
    assert from_cli.hd_enabled is False


def test_hd_config_validation_errors(tmp_path: Path) -> None:
    missing = tmp_path / "missing.toml"
    # hd_mode = "on" without a pack directory is rejected.
    with pytest.raises(ConfigError, match="hd_mode='on' requires an HD pack directory"):
        resolve_display_settings(missing, hd_mode="on")

    bad_type = tmp_path / "bad_type.toml"
    bad_type.write_text("[display]\nhd_pack_dir = 42\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="hd_pack_dir must be a string"):
        resolve_display_settings(bad_type)

    bad_mode = tmp_path / "bad_mode.toml"
    bad_mode.write_text('[display]\nhd_mode = "ultra"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="off, auto, on"):
        resolve_display_settings(bad_mode)


def test_cli_invalid_hd_pack_fails_fast(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli_main(
            [
                "--config",
                str(tmp_path / "missing.toml"),
                "--hd-pack",
                str(tmp_path / "does_not_exist"),
                "--play",
            ]
        )
    assert exc.value.code == 2
    assert "invalid HD pack:" in capsys.readouterr().err


def test_f7_toggle_consumed_before_router_and_preserves_replay_hashes(tmp_path: Path) -> None:
    window = WindowController(
        (960, 768),
        FullscreenMode.OFF,
        initial_crt=CrtSettings(),
        initial_hd_available=True,
        initial_hd_enabled=True,
        backend=_FakeWindowBackend(),
    )
    router = InputRouter()
    base_settings = DisplaySettings(hd_pack_dir=tmp_path / "pack", hd_mode=HdMode.AUTO)

    assert window.hd_enabled is True
    assert window.apply_display_settings(base_settings).hd_enabled is True

    # Press F7, key-repeat, and release: all consumed before InputRouter.
    for ev in (
        pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F7, mod=0),
        pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F7, mod=0, repeat=1),
        pygame.event.Event(pygame.KEYUP, key=pygame.K_F7, mod=0),
    ):
        assert window.consume_event(ev) is True

    assert window.hd_enabled is False
    assert window.apply_display_settings(base_settings).hd_mode is HdMode.OFF
    assert window.apply_display_settings(base_settings).hd_enabled is False
    assert router.sample_frame() == InputRouter().sample_frame()

    # Toggling F7 during a session leaves simulation state hashes byte-identical.
    s_clean = GameSession(seed=1337, players=(KnightId.RED,), start_scene=GameScene.KNIGHT_SELECT)
    s_toggled = GameSession(seed=1337, players=(KnightId.RED,), start_scene=GameScene.KNIGHT_SELECT)
    router_clean = InputRouter()
    router_toggled = InputRouter()
    for tick in range(15):
        if tick in (3, 7, 11):
            ev_down = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F7, mod=0)
            ev_up = pygame.event.Event(pygame.KEYUP, key=pygame.K_F7, mod=0)
            if not window.consume_event(ev_down):
                router_toggled.process_pygame_event(ev_down)
            if not window.consume_event(ev_up):
                router_toggled.process_pygame_event(ev_up)
        s_clean.step_tick(router_clean.sample_frame())
        s_toggled.step_tick(router_toggled.sample_frame())
        assert s_clean.state_hash() == s_toggled.state_hash()


def test_f10_preview_hd_mode_row_apply_and_discard(tmp_path: Path) -> None:
    # Without HD pack, DisplayPreview keeps original single-row layout in non-CRT mode.
    plain = DisplayPreview(FilterPreset.FAITHFUL, CrtSettings())
    assert plain.rows() == (("Filter", "faithful"),)

    # With HD pack available, F10 preview includes HD mode row, supports live draft + discard/apply.
    window = WindowController(
        (960, 768),
        FullscreenMode.OFF,
        initial_filter=FilterPreset.FAITHFUL,
        initial_crt=CrtSettings(),
        initial_hd_available=True,
        initial_hd_enabled=True,
        backend=_FakeWindowBackend(),
    )
    settings = DisplaySettings(hd_pack_dir=tmp_path / "pack", hd_mode=HdMode.AUTO)

    assert window.consume_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F10, mod=0))
    assert window.display_preview is not None
    assert window.display_preview.rows() == (("Filter", "faithful"), ("HD mode", "on"))

    # Move down to HD mode row and flip it off in draft; ESC discards draft.
    assert window.consume_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_DOWN, mod=0))
    assert window.consume_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RIGHT, mod=0))
    assert window.active_hd_enabled is False
    assert window.apply_display_settings(settings).hd_enabled is False
    assert window.consume_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE, mod=0))
    assert window.display_preview is None
    assert window.hd_enabled is True
    assert window.apply_display_settings(settings).hd_enabled is True

    # Re-open F10, toggle with F7 inside preview, and press ENTER to apply.
    assert window.consume_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F10, mod=0))
    assert window.consume_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F7, mod=0))
    assert window.active_hd_enabled is False
    assert window.consume_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0))
    assert window.display_preview is None
    assert window.hd_enabled is False
    assert window.apply_display_settings(settings).hd_enabled is False


def test_variant_helpers_and_fight_hd_history() -> None:
    assert [knight_class_variant(k) for k in range(5)] == [
        "blue",
        "yellow",
        "green",
        "red",
        "black",
    ]
    assert region_variant(REGION_GLEN) == "glen"
    assert region_variant(REGION_FOREST) == "forest"
    assert region_variant(REGION_SWAMP) == "swamp"
    assert region_variant(REGION_WASTELAND) == "wasteland"
    assert region_variant(99) is None

    single_fn = make_fight_cel_variant_fn(3, is_duel=False)
    assert single_fn is not None
    assert single_fn(Draw("KN1.ob", 0, 10, 20, False, False, False)) == "red"
    assert single_fn(Draw("HE2.ob", 0, 10, 20, False, False, False)) == "red"
    assert single_fn(Draw("TROGGAxe1.CEL", 0, 10, 20, False, False, False)) is None
    assert make_fight_cel_variant_fn(3, is_duel=True) is None

    bg = np.zeros((200, 320), dtype=np.uint8)
    hist = FightHdHistory(
        arena_name="fol1.t",
        arena_kind=HdAssetKind.ARENA,
        arena_variant="forest",
        background=bg,
        cels={},
        base_palette=default_combat_palette(),
    )
    d_stamp = Draw("blo.cel", 0, 50, 80, False, True, False)
    d_actor = Draw("KN1.ob", 0, 60, 80, False, False, False)
    hist.record([d_stamp, d_actor])
    assert hist.frame_seq == 1
    assert hist.prior_stamps == []
    assert hist.last_draws == (d_stamp, d_actor)

    hist.record([d_actor])
    assert hist.frame_seq == 2
    assert hist.prior_stamps == [d_stamp]
    assert hist.last_draws == (d_actor,)
