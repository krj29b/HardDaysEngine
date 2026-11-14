"""Tests for configuration loading and ADF discovery (app/config.py)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from pymoonstone.app.config import (
    ADF_IMAGE_SIZE,
    ENV_ADF_DIR,
    ConfigError,
    find_adf_images,
    load_config_file,
    resolve_adf_dir,
)

if TYPE_CHECKING:
    from pathlib import Path


def _write_config(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_cli_value_wins(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path / "config.toml", 'adf_dir = "fromcfg"\n')
    result = resolve_adf_dir(
        "fromcli", environ={ENV_ADF_DIR: "fromenv"}, config_path=cfg, default=tmp_path / "d"
    )
    assert result.name == "fromcli"


def test_env_beats_config(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path / "config.toml", 'adf_dir = "fromcfg"\n')
    result = resolve_adf_dir(
        None, environ={ENV_ADF_DIR: "fromenv"}, config_path=cfg, default=tmp_path / "d"
    )
    assert result.name == "fromenv"


def test_relative_config_value_is_relative_to_config_file(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path / "config.toml", 'adf_dir = "images"\n')
    result = resolve_adf_dir(None, environ={}, config_path=cfg, default=tmp_path / "d")
    assert result == tmp_path / "images"


def test_empty_values_fall_through_to_default(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path / "config.toml", 'adf_dir = ""\n')
    default = tmp_path / "disks"
    assert resolve_adf_dir("", environ={ENV_ADF_DIR: ""}, config_path=cfg, default=default) == (
        default
    )


def test_missing_config_file_is_empty(tmp_path: Path) -> None:
    assert load_config_file(tmp_path / "absent.toml") == {}


def test_invalid_toml_raises(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path / "config.toml", "adf_dir = \n")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config_file(cfg)


def test_wrong_type_raises(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path / "config.toml", "adf_dir = 3\n")
    with pytest.raises(ConfigError, match="must be a string"):
        resolve_adf_dir(None, environ={}, config_path=cfg, default=tmp_path)


def test_find_adf_images_filters_by_exact_size(tmp_path: Path) -> None:
    (tmp_path / "b.img").write_bytes(bytes(ADF_IMAGE_SIZE))
    (tmp_path / "A.adf").write_bytes(bytes(ADF_IMAGE_SIZE))
    (tmp_path / "short.adf").write_bytes(bytes(ADF_IMAGE_SIZE - 1))
    (tmp_path / "subdir.adf").mkdir()
    assert [p.name for p in find_adf_images(tmp_path)] == ["A.adf", "b.img"]


def test_find_adf_images_missing_directory(tmp_path: Path) -> None:
    assert find_adf_images(tmp_path / "nope") == []


@pytest.mark.adf
def test_real_disk_set_present(adf_dir: Path) -> None:
    assert len(find_adf_images(adf_dir)) == 3
