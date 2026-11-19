"""``[display]`` settings resolution and CLI wiring (§13.7, ADR-053)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harddaysengine.app.config import ConfigError, resolve_display_settings
from harddaysengine.cli import inspect_assets as inspect_cli, main as cli_main
from harddaysengine.render.presentation import (
    AspectMode,
    CropMode,
    CrtMaskMode,
    CrtSettings,
    DisplaySettings,
    FilterPreset,
    FullscreenMode,
    ScaleMode,
)

if TYPE_CHECKING:
    from pathlib import Path


def _config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_are_the_faithful_look(tmp_path: Path) -> None:
    settings = resolve_display_settings(tmp_path / "missing.toml")
    assert settings == DisplaySettings(
        scale=3,
        filter=FilterPreset.FAITHFUL,
        scale_mode=ScaleMode.INTEGER,
        aspect=AspectMode.SQUARE,
        crop=CropMode.FULL,
        fullscreen=FullscreenMode.OFF,
        vsync=True,
    )
    assert not settings.scanlines


def test_file_values_apply(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path,
        '[display]\nscale = 5\nfilter = "scanlines"\n'
        'scale_mode = "sharp-bilinear"\naspect = "pal"\ncrop = "playfield"\n'
        'fullscreen = "desktop"\nvsync = false\n',
    )
    settings = resolve_display_settings(cfg)
    assert settings == DisplaySettings(
        scale=5,
        filter=FilterPreset.SCANLINES,
        scale_mode=ScaleMode.SHARP_BILINEAR,
        aspect=AspectMode.PAL,
        crop=CropMode.PLAYFIELD,
        fullscreen=FullscreenMode.DESKTOP,
        vsync=False,
    )
    assert settings.scanlines


def test_cli_values_beat_the_file(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path,
        '[display]\nscale = 5\nfilter = "scanlines"\n'
        'scale_mode = "fit"\naspect = "pal"\ncrop = "playfield"\n'
        'fullscreen = "exclusive"\nvsync = false\n',
    )
    settings = resolve_display_settings(
        cfg,
        scale=2,
        filter_name="faithful",
        scale_mode="integer",
        aspect="square",
        crop="full",
        fullscreen="off",
        vsync=True,
    )
    assert settings == DisplaySettings(
        scale=2,
        filter=FilterPreset.FAITHFUL,
        scale_mode=ScaleMode.INTEGER,
        aspect=AspectMode.SQUARE,
        crop=CropMode.FULL,
        fullscreen=FullscreenMode.OFF,
        vsync=True,
    )


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("display = 3\n", "must be a table"),
        ("[display]\nscanline = true\n", "unknown"),
        ('[display]\nscale = "3"\n', "integer"),
        ("[display]\nscale = true\n", "integer"),
        ("[display]\nscale = 0\n", "1..8"),
        ("[display]\nscale = 9\n", "1..8"),
        ('[display]\nfilter = "vhs"\n', "faithful, scanlines, crt"),
        ("[display]\nfilter = 1\n", "faithful, scanlines, crt"),
        ('[display]\nscale_mode = "nearest"\n', "integer, sharp-bilinear, fit"),
        ("[display]\nscale_mode = 2\n", "integer, sharp-bilinear, fit"),
        ('[display]\naspect = "4:3"\n', "square, pal"),
        ('[display]\ncrop = "overscan"\n', "full, playfield"),
        ('[display]\nfullscreen = "borderless"\n', "off, desktop, exclusive"),
        ("[display]\nfullscreen = true\n", "off, desktop, exclusive"),
        ('[display]\nvsync = "true"\n', "boolean"),
        ("[display]\nvsync = 1\n", "boolean"),
        ('[display]\ncrt_mask = "grille"\n', "aperture, shadow, none"),
        ("[display]\ncrt_mask = 1\n", "aperture, shadow, none"),
        ("[display]\ncrt_mask_strength = true\n", "number"),
        ("[display]\ncrt_mask_strength = 1.5\n", "0.0..1.0"),
        ("[display]\ncrt_scanlines = -0.1\n", "0.0..1.0"),
        ("[display]\ncrt_curvature = 0.5\n", "0.0..0.3"),
        ("[display]\ncrt_bloom = nan\n", "0.0..1.0"),
        ("[display]\ncrt_bloom = inf\n", "0.0..1.0"),
    ],
)
def test_invalid_file_values_raise(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        resolve_display_settings(_config(tmp_path, text))


def test_crt_file_values_and_int_coercion(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path,
        '[display]\nfilter = "crt"\ncrt_mask = "shadow"\n'
        "crt_mask_strength = 0.4\ncrt_scanlines = 1\ncrt_curvature = 0\ncrt_bloom = 0.35\n",
    )
    settings = resolve_display_settings(cfg)
    assert settings.filter is FilterPreset.CRT
    assert not settings.scanlines
    assert settings.crt == CrtSettings(
        mask=CrtMaskMode.SHADOW,
        mask_strength=0.4,
        scanlines=1.0,
        curvature=0.0,
        bloom=0.35,
    )


def test_crt_settings_dataclass_validates_bounds() -> None:
    with pytest.raises(ValueError, match="crt_mask_strength"):
        CrtSettings(mask_strength=-0.01)
    with pytest.raises(ValueError, match="crt_scanlines"):
        CrtSettings(scanlines=1.1)
    with pytest.raises(ValueError, match="crt_curvature"):
        CrtSettings(curvature=0.35)
    with pytest.raises(ValueError, match="crt_bloom"):
        CrtSettings(bloom=float("nan"))


@pytest.mark.parametrize(
    ("scale", "filter_name", "scale_mode", "aspect", "crop", "fullscreen"),
    [
        (0, None, None, None, None, None),
        (9, None, None, None, None, None),
        (None, "vhs", None, None, None, None),
        (None, None, "nearest", None, None, None),
        (None, None, None, "4:3", None, None),
        (None, None, None, None, "overscan", None),
        (None, None, None, None, None, "borderless"),
    ],
)
def test_invalid_cli_values_raise(
    tmp_path: Path,
    scale: int | None,
    filter_name: str | None,
    scale_mode: str | None,
    aspect: str | None,
    crop: str | None,
    fullscreen: str | None,
) -> None:
    with pytest.raises(ConfigError, match="--"):
        resolve_display_settings(
            tmp_path / "missing.toml",
            scale=scale,
            filter_name=filter_name,
            scale_mode=scale_mode,
            aspect=aspect,
            crop=crop,
            fullscreen=fullscreen,
        )


def test_cli_passes_resolved_settings_to_the_game_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[DisplaySettings | None] = []

    def fake_window(_adf_dir: object, *, display: DisplaySettings | None, **_: object) -> int:
        seen.append(display)
        return 0

    monkeypatch.setattr(cli_main, "run_game_window", fake_window)
    cfg = _config(tmp_path, '[display]\nfilter = "scanlines"\naspect = "pal"\n')
    rc = cli_main.main(
        [
            "--config",
            str(cfg),
            "--play",
            "--scale",
            "4",
            "--scale-mode",
            "sharp-bilinear",
            "--crop",
            "playfield",
            "--fullscreen",
            "desktop",
            "--no-vsync",
        ]
    )
    assert rc == 0
    assert seen == [
        DisplaySettings(
            scale=4,
            filter=FilterPreset.SCANLINES,
            scale_mode=ScaleMode.SHARP_BILINEAR,
            aspect=AspectMode.PAL,
            crop=CropMode.PLAYFIELD,
            fullscreen=FullscreenMode.DESKTOP,
            vsync=False,
        )
    ]


def test_inspector_cli_passes_resolved_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[DisplaySettings | None] = []

    def fake_inspect(_adf_dir: object, *, display: DisplaySettings | None, **_: object) -> int:
        seen.append(display)
        return 0

    monkeypatch.setattr(inspect_cli, "run_inspector_cli", fake_inspect)
    cfg = _config(tmp_path, '[display]\nscale = 4\ncrop = "playfield"\n')
    rc = inspect_cli.main(
        [
            "--config",
            str(cfg),
            "--aspect",
            "pal",
            "--scale-mode",
            "fit",
            "--filter",
            "scanlines",
            "--fullscreen",
            "exclusive",
        ]
    )
    assert rc == 0
    assert seen == [
        DisplaySettings(
            scale=4,
            filter=FilterPreset.SCANLINES,
            scale_mode=ScaleMode.FIT,
            aspect=AspectMode.PAL,
            crop=CropMode.PLAYFIELD,
            fullscreen=FullscreenMode.EXCLUSIVE,
        )
    ]


def test_cli_reports_bad_display_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = _config(tmp_path, "[display]\nscale = 12\n")
    with pytest.raises(SystemExit) as exc:
        cli_main.main(["--config", str(cfg), "--play"])
    assert exc.value.code == 2
    assert "scale must be 1..8" in capsys.readouterr().err
