"""Configuration loading and ADF disk-image discovery.

The ADF directory is resolved in priority order: the ``--adf-dir`` CLI value, the
``HARDDAYSENGINE_ADF_DIR`` environment variable, ``adf_dir`` in ``config.toml``, then ``./disks``.
Display settings come from the ``[display]`` table, with CLI flags taking precedence
(ARCHITECTURE.md §13.7).
"""

from __future__ import annotations

import tomllib
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from harddaysengine.render.presentation import (
    MAX_WINDOW_SCALE,
    MIN_WINDOW_SCALE,
    AspectMode,
    CropMode,
    DisplaySettings,
    FilterPreset,
    ScaleMode,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

ADF_IMAGE_SIZE = 901_120
"""Size of a standard double-density Amiga floppy image: 80 cyl * 2 heads * 11 sectors * 512."""

ENV_ADF_DIR = "HARDDAYSENGINE_ADF_DIR"
DISPLAY_TABLE = "display"
_DISPLAY_KEYS = frozenset({"scale", "filter", "scale_mode", "aspect", "crop"})


class ConfigError(Exception):
    """Raised when ``config.toml`` exists but cannot be parsed or has a wrong value type."""


def load_config_file(path: Path) -> dict[str, object]:
    """Read a TOML config file; a missing file is an empty configuration."""
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc


def _config_adf_dir(config: Mapping[str, object], config_path: Path) -> str | None:
    value = config.get("adf_dir")
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"{config_path}: adf_dir must be a string, got {type(value).__name__}")
    return value or None


def resolve_adf_dir(
    cli_value: str | None,
    *,
    environ: Mapping[str, str],
    config_path: Path,
    default: Path,
) -> Path:
    """Return the ADF directory from the first non-empty source in priority order.

    A relative ``adf_dir`` in the config file is taken relative to the config file's directory.
    """
    if cli_value:
        return Path(cli_value)
    env_value = environ.get(ENV_ADF_DIR, "")
    if env_value:
        return Path(env_value)
    config_value = _config_adf_dir(load_config_file(config_path), config_path)
    if config_value is not None:
        candidate = Path(config_value)
        return candidate if candidate.is_absolute() else config_path.parent / candidate
    return default


def find_adf_images(directory: Path) -> list[Path]:
    """List every regular file in ``directory`` whose size is exactly one ADF image, by name.

    File names and extensions are not significant: disks are identified later by content
    (marker files on the OFS volume), as the original game does.
    """
    if not directory.is_dir():
        return []
    images = [
        entry
        for entry in directory.iterdir()
        if entry.is_file() and entry.stat().st_size == ADF_IMAGE_SIZE
    ]
    return sorted(images, key=lambda entry: entry.name.casefold())


def _display_table(config: Mapping[str, object], config_path: Path) -> Mapping[str, object]:
    table = config.get(DISPLAY_TABLE, {})
    if not isinstance(table, dict):
        raise ConfigError(f"{config_path}: [{DISPLAY_TABLE}] must be a table")
    unknown = sorted(set(table) - _DISPLAY_KEYS)
    if unknown:
        raise ConfigError(f"{config_path}: unknown [{DISPLAY_TABLE}] key(s): {', '.join(unknown)}")
    return table


def _window_scale(value: object, source: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigError(f"{source}: scale must be an integer, got {type(value).__name__}")
    if not MIN_WINDOW_SCALE <= value <= MAX_WINDOW_SCALE:
        raise ConfigError(
            f"{source}: scale must be {MIN_WINDOW_SCALE}..{MAX_WINDOW_SCALE}, got {value}"
        )
    return value


def _enum_setting[E: StrEnum](value: object, enum_cls: type[E], key: str, source: str) -> E:
    names = ", ".join(member.value for member in enum_cls)
    if not isinstance(value, str) or value not in {member.value for member in enum_cls}:
        raise ConfigError(f"{source}: {key} must be one of {names}, got {value!r}")
    return enum_cls(value)


def _resolve_enum[E: StrEnum](
    cli_value: str | None,
    table: Mapping[str, object],
    enum_cls: type[E],
    key: str,
    cli_flag: str,
    default: E,
    file_source: str,
) -> E:
    if cli_value is not None:
        return _enum_setting(cli_value, enum_cls, key, cli_flag)
    return _enum_setting(table.get(key, default.value), enum_cls, key, file_source)


def resolve_display_settings(
    config_path: Path,
    *,
    scale: int | None = None,
    filter_name: str | None = None,
    scale_mode: str | None = None,
    aspect: str | None = None,
    crop: str | None = None,
) -> DisplaySettings:
    """Resolve ``[display]`` settings: CLI value, then the config file, then the default (§13.7)."""
    table = _display_table(load_config_file(config_path), config_path)
    defaults = DisplaySettings()
    file_source = f"{config_path}: [{DISPLAY_TABLE}]"
    resolved_scale = (
        _window_scale(scale, "--scale")
        if scale is not None
        else _window_scale(table.get("scale", defaults.scale), file_source)
    )
    return DisplaySettings(
        scale=resolved_scale,
        filter=_resolve_enum(
            filter_name, table, FilterPreset, "filter", "--filter", defaults.filter, file_source
        ),
        scale_mode=_resolve_enum(
            scale_mode,
            table,
            ScaleMode,
            "scale_mode",
            "--scale-mode",
            defaults.scale_mode,
            file_source,
        ),
        aspect=_resolve_enum(
            aspect, table, AspectMode, "aspect", "--aspect", defaults.aspect, file_source
        ),
        crop=_resolve_enum(crop, table, CropMode, "crop", "--crop", defaults.crop, file_source),
    )
