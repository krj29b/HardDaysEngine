"""Configuration loading and ADF disk-image discovery.

The ADF directory is resolved in priority order: the ``--adf-dir`` CLI value, the
``HARDDAYSENGINE_ADF_DIR`` environment variable, ``adf_dir`` in ``config.toml``, then ``./disks``.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

ADF_IMAGE_SIZE = 901_120
"""Size of a standard double-density Amiga floppy image: 80 cyl * 2 heads * 11 sectors * 512."""

ENV_ADF_DIR = "HARDDAYSENGINE_ADF_DIR"


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
