"""Tests for the ``harddaysengine`` console script (cli/main.py)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harddaysengine import __version__
from harddaysengine.app.config import ADF_IMAGE_SIZE
from harddaysengine.cli.main import main

if TYPE_CHECKING:
    from pathlib import Path


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"HardDaysEngine {__version__}"


def test_no_arguments_prints_help(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    assert main(["--config", str(tmp_path / "none.toml")]) == 0
    assert "usage: harddaysengine" in capsys.readouterr().out


def test_list_disks_complete_set(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    for name in ("one.adf", "two.adf", "three.adf"):
        (tmp_path / name).write_bytes(bytes(ADF_IMAGE_SIZE))
    assert main(["--adf-dir", str(tmp_path), "--list-disks"]) == 0
    out = capsys.readouterr().out
    assert "3 image(s) found" in out
    assert "two.adf" in out


def test_list_disks_incomplete_set_fails(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    (tmp_path / "one.adf").write_bytes(bytes(ADF_IMAGE_SIZE))
    assert main(["--adf-dir", str(tmp_path), "--list-disks"]) == 1
    assert "1 image(s) found" in capsys.readouterr().out


def test_bad_config_is_a_usage_error(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text("adf_dir = [1]\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main(["--config", str(cfg), "--list-disks"])
    assert exc.value.code == 2
