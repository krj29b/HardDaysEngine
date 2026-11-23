"""Unit tests for T19.7 artist QA tooling: Inspector HD CLI & audit side-by-side helpers."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pytest

from harddaysengine.app.audit import _sheet_filename, _sheet_layout, compose_side_by_side
from harddaysengine.app.inspector import BackgroundItem, InspectorState, SpriteSetItem
from harddaysengine.assets.cel import CelFrame, CelFrameMeta, DecodedCel
from harddaysengine.assets.palette import default_combat_palette
from harddaysengine.assets.picture import DecodedPicture
from harddaysengine.cli.inspect_assets import build_inspect_parser, run_inspector_cli
from harddaysengine.render.presentation import DisplaySettings, FilterPreset, HdMode

if TYPE_CHECKING:
    from types import ModuleType


def _load_audit_shots_module() -> ModuleType:
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "audit_shots.py"
    spec = importlib.util.spec_from_file_location("audit_shots_script", script_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["audit_shots_script"] = mod
    spec.loader.exec_module(mod)
    return mod


def _dummy_inspector_state() -> InspectorState:
    pic = DecodedPicture(
        width=320,
        height=200,
        planes=5,
        packed_size=0,
        raw_palette=tuple([0] * 32),
        palette=default_combat_palette(),
        pixels=np.zeros((200, 320), dtype=np.uint8),
        raw_planes=b"",
    )
    frame = CelFrame(
        meta=CelFrameMeta(
            index=0,
            data_offset=0,
            width=16,
            height=16,
            plane_bits=0x1F,
            hot_and_flags=0x0000,
        ),
        pixels=np.ones((16, 16), dtype=np.uint8),
        mask=np.ones((16, 16), dtype=np.uint8),
    )
    cel = DecodedCel(frame_count=1, packed_size=0, unpacked_bits=0, frames=(frame,), raw_planes=b"")
    return InspectorState(
        backgrounds=[BackgroundItem(name="fol1.t", picture=pic)],
        sprites=[SpriteSetItem(name="kn4.ob", stem="kn4", cel=cel, hit_set=None)],
    )


def test_compose_side_by_side_upscales_classic_to_match_hd() -> None:
    classic = np.zeros((256, 320, 3), dtype=np.uint8)
    classic[:, :, 0] = 200
    hd = np.zeros((512, 640, 3), dtype=np.uint8)
    hd[:, :, 2] = 180

    combined = compose_side_by_side(classic, hd)
    assert combined.shape == (512, 1280, 3)
    assert tuple(combined[10, 10]) == (200, 0, 0)
    assert tuple(combined[10, 650]) == (0, 0, 180)


def test_compose_side_by_side_non_integer_and_same_size() -> None:
    same_a = np.full((100, 120, 3), 50, dtype=np.uint8)
    same_b = np.full((100, 120, 3), 220, dtype=np.uint8)
    combined_same = compose_side_by_side(same_a, same_b)
    assert combined_same.shape == (100, 240, 3)
    assert int(combined_same[0, 0, 0]) == 50
    assert int(combined_same[0, 120, 0]) == 220

    non_int = compose_side_by_side(same_a, np.full((150, 180, 3), 99, dtype=np.uint8))
    assert non_int.shape == (150, 360, 3)
    assert int(non_int[0, 0, 0]) == 50
    assert int(non_int[0, 180, 0]) == 99


def test_inspector_status_title_includes_optional_hd_mode() -> None:
    state = _dummy_inspector_state()
    assert "Mode:" not in state.status_title()
    assert state.status_title(hd_mode="HD").endswith("| Mode: HD")
    assert state.status_title(hd_mode="Classic").endswith("| Mode: Classic")


def test_inspect_parser_accepts_hd_pack_and_hd_mode() -> None:
    parser = build_inspect_parser()
    args = parser.parse_args(["--hd-pack", "packs/demo", "--hd-mode", "on", "--cel", "kn4"])
    assert args.hd_pack == "packs/demo"
    assert args.hd_mode == "on"
    assert args.cel == "kn4"


def test_run_inspector_cli_rejects_invalid_hd_pack(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "disk1.adf").write_bytes(b"\x00" * 901_120)
    missing_pack = tmp_path / "nonexistent-pack"
    code = run_inspector_cli(
        tmp_path,
        display=DisplaySettings(hd_pack_dir=missing_pack, hd_mode=HdMode.ON),
        headless_frames=1,
    )
    assert code == 1
    assert "HD pack error" in capsys.readouterr().err


def test_audit_sheet_naming_and_layout_for_hd_compare() -> None:
    assert _sheet_filename("fights", None, None, hd_compare=True) == "fights_hd_compare.png"
    assert (
        _sheet_filename(
            "fights", DisplaySettings(filter=FilterPreset.CRT), (1920, 1080), hd_compare=True
        )
        == "fights_hd_crt_1920x1080.png"
    )
    assert _sheet_layout(4, None, hd_compare=True) == (2, 1280)
    assert _sheet_layout(4, (1920, 1080), hd_compare=True) == (1, 1280)


def test_audit_shots_script_parses_hd_pack_and_hitboxes(tmp_path: Path) -> None:
    mod = _load_audit_shots_module()
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    (pack_dir / "pack.toml").write_text(
        'schema_version = 1\nname = "QA"\nversion = "1.0"\nscale = 2\n',
        encoding="utf-8",
    )
    parser = mod.build_parser()
    args = parser.parse_args(["--hd-pack", str(pack_dir), "--hitboxes", "--scenario", "fights"])
    assert args.hd_pack == str(pack_dir)
    assert args.hitboxes is True
    loaded = mod._resolve_hd_pack(parser, args.hd_pack)
    assert loaded is not None
    assert loaded.config.scale == 2

    with pytest.raises(SystemExit):
        mod._resolve_hd_pack(parser, str(tmp_path / "missing"))
