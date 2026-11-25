"""Configuration loading and ADF disk-image discovery.

The ADF directory is resolved in priority order: the ``--adf-dir`` CLI value, the
``HARDDAYSENGINE_ADF_DIR`` environment variable, ``adf_dir`` in ``config.toml``, then ``./disks``.
Display settings come from the ``[display]`` table, with CLI flags taking precedence
(ARCHITECTURE.md §13.7).
"""

from __future__ import annotations

import math
import tomllib
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from harddaysengine.app.debug_dump import DEFAULT_DUMP_DIR_NAME, DebugDumpSettings
from harddaysengine.app.input import ControlMode
from harddaysengine.render.presentation import (
    DEFAULT_SURROUND_COLOR_HEX,
    MAX_CRT_CURVATURE,
    MAX_CRT_STRENGTH,
    MAX_SURROUND_BRIGHTNESS,
    MAX_WINDOW_SCALE,
    MIN_CRT_CURVATURE,
    MIN_CRT_STRENGTH,
    MIN_SURROUND_BRIGHTNESS,
    MIN_WINDOW_SCALE,
    AspectMode,
    CropMode,
    CrtMaskMode,
    CrtSettings,
    DisplaySettings,
    FilterPreset,
    FullscreenMode,
    HdMode,
    ScaleMode,
    SurroundMode,
    SurroundSettings,
    parse_hex_rgb,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

ADF_IMAGE_SIZE = 901_120
"""Size of a standard double-density Amiga floppy image: 80 cyl * 2 heads * 11 sectors * 512."""

ENV_ADF_DIR = "HARDDAYSENGINE_ADF_DIR"
ENV_HD_PACK_DIR = "HARDDAYSENGINE_HD_PACK_DIR"
ENV_DEBUG_DUMP_DIR = "HARDDAYSENGINE_DEBUG_DUMP_DIR"
DISPLAY_TABLE = "display"
INPUT_TABLE = "input"
DEBUG_TABLE = "debug"
_INPUT_KEYS = frozenset({"mode"})
_DEBUG_KEYS = frozenset({"dump_dir", "crash_dumps"})
_DISPLAY_KEYS = frozenset(
    {
        "scale",
        "filter",
        "scale_mode",
        "aspect",
        "crop",
        "fullscreen",
        "vsync",
        "crt_mask",
        "crt_mask_strength",
        "crt_scanlines",
        "crt_curvature",
        "crt_bloom",
        "surround",
        "surround_brightness",
        "surround_color",
        "hd_pack_dir",
        "hd_mode",
    }
)


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


def _bool_setting(value: object, key: str, source: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{source}: {key} must be a boolean, got {type(value).__name__}")
    return value


def resolve_save_path(config_path: Path) -> Path | None:
    """Return the optional campaign save beside the selected config (§13.11)."""
    gameplay = load_config_file(config_path).get("gameplay", {})
    if not isinstance(gameplay, dict):
        raise ConfigError(f"{config_path}: gameplay must be a table")
    enabled = _bool_setting(gameplay.get("save_load", False), "save_load", str(config_path))
    return config_path.with_name("campaign-save.json") if enabled else None


def resolve_control_mode(
    config_path: Path,
    *,
    cli_mode: str | None = None,
) -> ControlMode:
    """Resolve ``[input] mode``: CLI flag, then ``config.toml``, then ``auto`` (§13.13)."""
    table = load_config_file(config_path).get(INPUT_TABLE, {})
    if not isinstance(table, dict):
        raise ConfigError(f"{config_path}: [{INPUT_TABLE}] must be a table")
    unknown = sorted(set(table) - _INPUT_KEYS)
    if unknown:
        raise ConfigError(f"{config_path}: unknown [{INPUT_TABLE}] key(s): {', '.join(unknown)}")
    file_source = f"{config_path}: [{INPUT_TABLE}]"
    return _resolve_enum(
        cli_mode, table, ControlMode, "mode", "--control-mode", ControlMode.AUTO, file_source
    )


def _resolve_debug_dump_dir(
    cli_dump_dir: str | None,
    table: Mapping[str, object],
    *,
    environ: Mapping[str, str],
    config_path: Path,
    file_source: str,
) -> Path:
    if cli_dump_dir:
        return Path(cli_dump_dir)
    env_value = environ.get(ENV_DEBUG_DUMP_DIR, "")
    if env_value:
        return Path(env_value)
    raw = table.get("dump_dir")
    if raw is None:
        return config_path.parent / DEFAULT_DUMP_DIR_NAME
    if not isinstance(raw, str):
        raise ConfigError(f"{file_source}: dump_dir must be a string, got {type(raw).__name__}")
    if not raw:
        return config_path.parent / DEFAULT_DUMP_DIR_NAME
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else config_path.parent / candidate


def resolve_debug_settings(
    config_path: Path,
    *,
    cli_dump_dir: str | None = None,
    crash_dumps: bool | None = None,
    environ: Mapping[str, str] | None = None,
) -> DebugDumpSettings:
    """Resolve ``[debug]`` dump settings: CLI, env, config file, then defaults (§13.14.2)."""
    table = load_config_file(config_path).get(DEBUG_TABLE, {})
    if not isinstance(table, dict):
        raise ConfigError(f"{config_path}: [{DEBUG_TABLE}] must be a table")
    unknown = sorted(set(table) - _DEBUG_KEYS)
    if unknown:
        raise ConfigError(f"{config_path}: unknown [{DEBUG_TABLE}] key(s): {', '.join(unknown)}")
    file_source = f"{config_path}: [{DEBUG_TABLE}]"
    resolved_dir = _resolve_debug_dump_dir(
        cli_dump_dir,
        table,
        environ={} if environ is None else environ,
        config_path=config_path,
        file_source=file_source,
    )
    resolved_crash = (
        _bool_setting(crash_dumps, "crash_dumps", "--crash-dumps")
        if crash_dumps is not None
        else _bool_setting(table.get("crash_dumps", True), "crash_dumps", file_source)
    )
    return DebugDumpSettings(dump_dir=resolved_dir, crash_dumps=resolved_crash)


def _float_setting(
    value: object,
    key: str,
    lo: float,
    hi: float,
    source: str,
) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"{source}: {key} must be a number, got {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number) or not lo <= number <= hi:
        raise ConfigError(f"{source}: {key} must be {lo}..{hi}, got {value!r}")
    return number


def _resolve_crt_settings(
    table: Mapping[str, object],
    defaults: CrtSettings,
    file_source: str,
) -> CrtSettings:
    return CrtSettings(
        mask=_enum_setting(
            table.get("crt_mask", defaults.mask.value), CrtMaskMode, "crt_mask", file_source
        ),
        mask_strength=_float_setting(
            table.get("crt_mask_strength", defaults.mask_strength),
            "crt_mask_strength",
            MIN_CRT_STRENGTH,
            MAX_CRT_STRENGTH,
            file_source,
        ),
        scanlines=_float_setting(
            table.get("crt_scanlines", defaults.scanlines),
            "crt_scanlines",
            MIN_CRT_STRENGTH,
            MAX_CRT_STRENGTH,
            file_source,
        ),
        curvature=_float_setting(
            table.get("crt_curvature", defaults.curvature),
            "crt_curvature",
            MIN_CRT_CURVATURE,
            MAX_CRT_CURVATURE,
            file_source,
        ),
        bloom=_float_setting(
            table.get("crt_bloom", defaults.bloom),
            "crt_bloom",
            MIN_CRT_STRENGTH,
            MAX_CRT_STRENGTH,
            file_source,
        ),
    )


def _hex_color_setting(value: object, key: str, source: str) -> tuple[int, int, int]:
    if not isinstance(value, str):
        raise ConfigError(
            f"{source}: {key} must be a #RRGGBB hex string, got {type(value).__name__}"
        )
    try:
        return parse_hex_rgb(value)
    except ValueError as exc:
        raise ConfigError(f"{source}: {key} must be a #RRGGBB hex string, got {value!r}") from exc


def _resolve_surround_settings(
    surround_cli: str | None,
    table: Mapping[str, object],
    defaults: SurroundSettings,
    file_source: str,
) -> SurroundSettings:
    mode = _resolve_enum(
        surround_cli,
        table,
        SurroundMode,
        "surround",
        "--surround",
        defaults.mode,
        file_source,
    )
    brightness = _float_setting(
        table.get("surround_brightness", defaults.brightness),
        "surround_brightness",
        MIN_SURROUND_BRIGHTNESS,
        MAX_SURROUND_BRIGHTNESS,
        file_source,
    )
    color = _hex_color_setting(
        table.get("surround_color", DEFAULT_SURROUND_COLOR_HEX),
        "surround_color",
        file_source,
    )
    return SurroundSettings(mode=mode, brightness=brightness, color=color)


def _resolve_hd_pack_dir(
    cli_value: str | None,
    table: Mapping[str, object],
    *,
    environ: Mapping[str, str],
    config_path: Path,
    file_source: str,
) -> Path | None:
    if cli_value:
        return Path(cli_value)
    env_value = environ.get(ENV_HD_PACK_DIR, "")
    if env_value:
        return Path(env_value)
    raw = table.get("hd_pack_dir")
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ConfigError(f"{file_source}: hd_pack_dir must be a string, got {type(raw).__name__}")
    if not raw:
        return None
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else config_path.parent / candidate


def resolve_display_settings(
    config_path: Path,
    *,
    scale: int | None = None,
    filter_name: str | None = None,
    scale_mode: str | None = None,
    aspect: str | None = None,
    crop: str | None = None,
    fullscreen: str | None = None,
    vsync: bool | None = None,
    surround: str | None = None,
    hd_pack: str | None = None,
    hd_mode: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> DisplaySettings:
    """Resolve ``[display]`` settings: CLI value, env, config file, then default (§13.7, §14.7)."""
    table = _display_table(load_config_file(config_path), config_path)
    defaults = DisplaySettings()
    file_source = f"{config_path}: [{DISPLAY_TABLE}]"
    resolved_scale = (
        _window_scale(scale, "--scale")
        if scale is not None
        else _window_scale(table.get("scale", defaults.scale), file_source)
    )
    resolved_vsync = (
        _bool_setting(vsync, "vsync", "--vsync")
        if vsync is not None
        else _bool_setting(table.get("vsync", defaults.vsync), "vsync", file_source)
    )
    resolved_hd_pack = _resolve_hd_pack_dir(
        hd_pack,
        table,
        environ={} if environ is None else environ,
        config_path=config_path,
        file_source=file_source,
    )
    resolved_hd_mode = _resolve_enum(
        hd_mode, table, HdMode, "hd_mode", "--hd-mode", defaults.hd_mode, file_source
    )
    if resolved_hd_mode is HdMode.ON and resolved_hd_pack is None:
        raise ConfigError(
            f"{file_source}: hd_mode='on' requires an HD pack directory "
            f"(--hd-pack, {ENV_HD_PACK_DIR}, or [display] hd_pack_dir)"
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
        fullscreen=_resolve_enum(
            fullscreen,
            table,
            FullscreenMode,
            "fullscreen",
            "--fullscreen",
            defaults.fullscreen,
            file_source,
        ),
        vsync=resolved_vsync,
        crt=_resolve_crt_settings(table, defaults.crt, file_source),
        surround=_resolve_surround_settings(surround, table, defaults.surround, file_source),
        hd_pack_dir=resolved_hd_pack,
        hd_mode=resolved_hd_mode,
    )
