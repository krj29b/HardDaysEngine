"""``[display]`` settings resolution and CLI wiring (§13.7, ADR-053)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harddaysengine.app.config import ConfigError, resolve_display_settings
from harddaysengine.cli import main as cli_main
from harddaysengine.render.presentation import DisplaySettings, FilterPreset

if TYPE_CHECKING:
    from pathlib import Path


def _config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_are_the_faithful_look(tmp_path: Path) -> None:
    settings = resolve_display_settings(tmp_path / "missing.toml")
    assert settings == DisplaySettings(scale=3, filter=FilterPreset.FAITHFUL)
    assert not settings.scanlines


def test_file_values_apply(tmp_path: Path) -> None:
    cfg = _config(tmp_path, '[display]\nscale = 5\nfilter = "scanlines"\n')
    settings = resolve_display_settings(cfg)
    assert settings == DisplaySettings(scale=5, filter=FilterPreset.SCANLINES)
    assert settings.scanlines


def test_cli_values_beat_the_file(tmp_path: Path) -> None:
    cfg = _config(tmp_path, '[display]\nscale = 5\nfilter = "scanlines"\n')
    settings = resolve_display_settings(cfg, scale=2, filter_name="faithful")
    assert settings == DisplaySettings(scale=2, filter=FilterPreset.FAITHFUL)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("display = 3\n", "must be a table"),
        ("[display]\nscanline = true\n", "unknown"),
        ('[display]\nscale = "3"\n', "integer"),
        ("[display]\nscale = true\n", "integer"),
        ("[display]\nscale = 0\n", "1..8"),
        ("[display]\nscale = 9\n", "1..8"),
        ('[display]\nfilter = "crt"\n', "faithful, scanlines"),
        ("[display]\nfilter = 1\n", "faithful, scanlines"),
    ],
)
def test_invalid_file_values_raise(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        resolve_display_settings(_config(tmp_path, text))


@pytest.mark.parametrize(("scale", "filter_name"), [(0, None), (9, None), (None, "crt")])
def test_invalid_cli_values_raise(
    tmp_path: Path, scale: int | None, filter_name: str | None
) -> None:
    with pytest.raises(ConfigError, match="--"):
        resolve_display_settings(tmp_path / "missing.toml", scale=scale, filter_name=filter_name)


def test_cli_passes_resolved_settings_to_the_game_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[DisplaySettings | None] = []

    def fake_window(_adf_dir: object, *, display: DisplaySettings | None, **_: object) -> int:
        seen.append(display)
        return 0

    monkeypatch.setattr(cli_main, "run_game_window", fake_window)
    cfg = _config(tmp_path, '[display]\nfilter = "scanlines"\n')
    assert cli_main.main(["--config", str(cfg), "--play", "--scale", "4"]) == 0
    assert seen == [DisplaySettings(scale=4, filter=FilterPreset.SCANLINES)]


def test_cli_reports_bad_display_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = _config(tmp_path, "[display]\nscale = 12\n")
    with pytest.raises(SystemExit) as exc:
        cli_main.main(["--config", str(cfg), "--play"])
    assert exc.value.code == 2
    assert "scale must be 1..8" in capsys.readouterr().err
