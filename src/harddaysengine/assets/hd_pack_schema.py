"""Schema, path safety, and strict TOML parsing for external HD packs (ARCHITECTURE.md §14.2-3)."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    import numpy as np
    import numpy.typing as npt

HD_PACK_SCHEMA_VERSION = 1
MIN_HD_PACK_SCALE = 2
MAX_HD_PACK_SCALE = 8
MIN_ASSET_SCALE = 1
MAX_ASSET_SCALE = 8
MAX_PALETTE_INDEX = 31

_VARIANT_RE = re.compile(r"^[a-z0-9_-]+$")
_FRAME_FILE_RE = re.compile(r"^frame_(\d{3,})\.png$")
_FRAME_MASK_RE = re.compile(r"^frame_(\d{3,})\.mask\.png$")

_PACK_KEYS = frozenset(
    {
        "schema_version",
        "name",
        "version",
        "author",
        "scale",
        "default_mode",
        "fallback_filter",
    }
)
_SURFACE_META_KEYS = frozenset({"mode", "scale", "padding", "cycle_frames"})
_CEL_META_KEYS = frozenset({"mode", "scale", "frames"})
_FRAME_META_KEYS = frozenset({"mode", "scale", "padding", "anchor_offset"})


class HdPackError(ValueError):
    """Raised when an HD pack manifest, path, metadata, or PNG fails §14 validation."""


class HdColorMode(StrEnum):
    """Color and palette-interaction mode for an HD asset (§14.3, §14.6.1)."""

    INDEXED = "indexed"
    RGBA = "rgba"
    MASKED = "masked"


class HdFallbackFilter(StrEnum):
    """Sampling filter for unreplaced 1x Amiga assets inside an HD scene (§14.6.1)."""

    NEAREST = "nearest"
    SMOOTH = "smooth"


class HdAssetKind(StrEnum):
    """Top-level override category inside an HD pack (§14.2)."""

    PICTURE = "picture"
    ARENA = "arena"
    CEL = "cel"


@dataclass(slots=True, frozen=True)
class HdPackConfig:
    """Validated root ``pack.toml`` configuration (§14.3.1)."""

    schema_version: int
    name: str
    version: str
    author: str
    scale: int
    default_mode: HdColorMode
    fallback_filter: HdFallbackFilter


@dataclass(slots=True, frozen=True)
class HdFrameMeta:
    """Optional per-frame metadata from ``cels/<cel>/meta.toml`` (§14.3.2)."""

    mode: HdColorMode | None = None
    scale: int | None = None
    padding: tuple[int, int, int, int] = (0, 0, 0, 0)
    anchor_offset: tuple[int, int] = (0, 0)


@dataclass(slots=True, frozen=True)
class HdCelMeta:
    """Optional cel-level and per-frame metadata from ``cels/<cel>/meta.toml`` (§14.3.2)."""

    mode: HdColorMode | None = None
    scale: int | None = None
    frames: tuple[tuple[int, HdFrameMeta], ...] = ()

    def frame_meta(self, frame_index: int) -> HdFrameMeta | None:
        """Return frame-specific metadata for ``frame_index`` if declared."""
        for idx, meta in self.frames:
            if idx == frame_index:
                return meta
        return None


@dataclass(slots=True, frozen=True)
class HdSurfaceMeta:
    """Optional metadata from ``pictures/<name>.meta.toml`` or ``arenas/<name>.meta.toml``."""

    mode: HdColorMode | None = None
    scale: int | None = None
    padding: tuple[int, int, int, int] = (0, 0, 0, 0)
    cycle_frames: tuple[Path, ...] = ()


@dataclass(slots=True, frozen=True)
class HdAssetEntry:
    """Indexed override entry inside an :class:`HdPack` (§14.2, §14.4)."""

    kind: HdAssetKind
    key: str
    variant: str | None
    frame_index: int | None
    image_path: Path
    mask_path: Path | None
    mode: HdColorMode
    declared_scale: int | None
    padding: tuple[int, int, int, int]
    anchor_offset: tuple[int, int]
    cycle_frame_paths: tuple[Path, ...] = ()


@dataclass(slots=True, frozen=True)
class HdImage:
    """Decoded and dimension-validated HD image pixels (§14.4, §14.6.1)."""

    mode: HdColorMode
    width: int
    height: int
    scale: int
    padding: tuple[int, int, int, int]
    anchor_offset: tuple[int, int]
    rgba: npt.NDArray[np.uint8] | None = None
    indexed: npt.NDArray[np.uint8] | None = None
    mask: npt.NDArray[np.uint8] | None = None


@dataclass(slots=True, frozen=True)
class HdPackValidationReport:
    """Summary emitted by :meth:`HdPack.validate` and ``--validate-hd-pack`` (§14.4)."""

    pack_root: Path
    config: HdPackConfig
    picture_count: int
    arena_count: int
    cel_count: int
    cel_frame_count: int
    cycle_frame_count: int
    indexed_count: int
    rgba_count: int
    masked_count: int
    checked_against_disks: bool
    warnings: tuple[str, ...] = ()


def ensure_within_root(root: Path, candidate: Path, *, label: str) -> Path:
    """Resolve ``candidate`` and reject any path or symlink escaping ``root`` (§14.2)."""
    resolved_root = root.resolve()
    try:
        resolved = candidate.resolve()
    except OSError as exc:
        raise HdPackError(f"{label}: cannot resolve path {candidate}: {exc}") from exc
    if not resolved.is_relative_to(resolved_root):
        raise HdPackError(f"{label}: path escapes pack root: {candidate}")
    return resolved


def normalize_hd_asset_key(name: str) -> str:
    """Normalize a picture, arena, or cel key to canonical lowercase form (§14.2)."""
    cleaned = name.strip().casefold()
    if not cleaned or "/" in cleaned or "\\" in cleaned or "\x00" in cleaned:
        raise HdPackError(f"invalid HD asset key: {name!r}")
    if cleaned in {".", ".."} or ".." in cleaned:
        raise HdPackError(f"invalid HD asset key (path traversal): {name!r}")
    return cleaned


def normalize_variant(variant: str) -> str:
    """Validate and normalize a lowercase ASCII variant identifier (§14.2)."""
    cleaned = variant.strip().casefold()
    if not _VARIANT_RE.fullmatch(cleaned):
        raise HdPackError(f"invalid variant identifier {variant!r}; expected [a-z0-9_-]+")
    return cleaned


def split_key_and_variant(stem: str) -> tuple[str, str | None]:
    """Split ``<base>`` or ``<base>__<variant>`` into canonical ``(key, variant)`` (§14.2)."""
    if "__" not in stem:
        return normalize_hd_asset_key(stem), None
    base, raw_variant = stem.split("__", 1)
    if "__" in raw_variant:
        raise HdPackError(f"invalid asset stem {stem!r}: multiple '__' variant separators")
    return normalize_hd_asset_key(base), normalize_variant(raw_variant)


def parse_frame_filename(filename: str) -> tuple[int, bool] | None:
    """Parse ``frame_NNN.png`` or ``frame_NNN.mask.png`` into ``(index, is_mask)``."""
    match = _FRAME_FILE_RE.fullmatch(filename)
    if match is not None:
        return int(match.group(1)), False
    mask_match = _FRAME_MASK_RE.fullmatch(filename)
    if mask_match is not None:
        return int(mask_match.group(1)), True
    return None


def _load_toml_table(path: Path, root: Path) -> dict[str, object]:
    resolved = ensure_within_root(root, path, label=str(path))
    try:
        with resolved.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise HdPackError(f"{path}: invalid TOML: {exc}") from exc
    return data


def _check_unknown_keys(table: Mapping[str, object], allowed: frozenset[str], source: Path) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise HdPackError(f"{source}: unknown key(s): {', '.join(unknown)}")


def _require_int(value: object, name: str, source: Path, *, lo: int, hi: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise HdPackError(f"{source}: {name} must be an integer, got {type(value).__name__}")
    if not lo <= value <= hi:
        raise HdPackError(f"{source}: {name} must be in {lo}..{hi}, got {value}")
    return value


def _require_str(value: object, name: str, source: Path, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise HdPackError(f"{source}: {name} must be a string, got {type(value).__name__}")
    if not allow_empty and not value.strip():
        raise HdPackError(f"{source}: {name} must be a non-empty string")
    return value


def _parse_enum[E: StrEnum](value: object, enum_cls: type[E], name: str, source: Path) -> E:
    allowed = {member.value for member in enum_cls}
    if not isinstance(value, str) or value not in allowed:
        names = ", ".join(member.value for member in enum_cls)
        raise HdPackError(f"{source}: {name} must be one of {names}, got {value!r}")
    return enum_cls(value)


def _parse_int_tuple(
    value: object,
    name: str,
    source: Path,
    *,
    length: int,
    min_value: int | None = None,
) -> tuple[int, ...]:
    if not isinstance(value, list) or len(value) != length:
        raise HdPackError(f"{source}: {name} must be a list of {length} integers")
    parsed: list[int] = []
    for item in value:
        if not isinstance(item, int) or isinstance(item, bool):
            raise HdPackError(f"{source}: {name} entries must be integers")
        if min_value is not None and item < min_value:
            raise HdPackError(f"{source}: {name} entries must be >= {min_value}, got {item}")
        parsed.append(item)
    return tuple(parsed)


def _parse_padding(table: Mapping[str, object], source: Path) -> tuple[int, int, int, int]:
    raw = table.get("padding")
    if raw is None:
        return (0, 0, 0, 0)
    vals = _parse_int_tuple(raw, "padding", source, length=4, min_value=0)
    return (vals[0], vals[1], vals[2], vals[3])


def _parse_anchor_offset(table: Mapping[str, object], source: Path) -> tuple[int, int]:
    raw = table.get("anchor_offset")
    if raw is None:
        return (0, 0)
    vals = _parse_int_tuple(raw, "anchor_offset", source, length=2)
    return (vals[0], vals[1])


def _parse_optional_mode(table: Mapping[str, object], source: Path) -> HdColorMode | None:
    raw = table.get("mode")
    return None if raw is None else _parse_enum(raw, HdColorMode, "mode", source)


def _parse_optional_scale(table: Mapping[str, object], source: Path) -> int | None:
    raw = table.get("scale")
    if raw is None:
        return None
    return _require_int(raw, "scale", source, lo=MIN_ASSET_SCALE, hi=MAX_ASSET_SCALE)


def parse_pack_toml(pack_root: Path) -> HdPackConfig:
    """Parse and strictly validate ``<pack_root>/pack.toml`` (§14.3.1)."""
    toml_path = pack_root / "pack.toml"
    if not toml_path.is_file():
        raise HdPackError(f"{pack_root}: missing required pack.toml")
    table = _load_toml_table(toml_path, pack_root)
    _check_unknown_keys(table, _PACK_KEYS, toml_path)

    if "schema_version" not in table:
        raise HdPackError(f"{toml_path}: missing required schema_version")
    schema_version = _require_int(
        table["schema_version"],
        "schema_version",
        toml_path,
        lo=HD_PACK_SCHEMA_VERSION,
        hi=HD_PACK_SCHEMA_VERSION,
    )
    if "name" not in table:
        raise HdPackError(f"{toml_path}: missing required name")
    name = _require_str(table["name"], "name", toml_path).strip()
    version = _require_str(table.get("version", "0.0.0"), "version", toml_path)
    author = _require_str(table.get("author", ""), "author", toml_path, allow_empty=True)
    if "scale" not in table:
        raise HdPackError(f"{toml_path}: missing required scale")
    scale = _require_int(
        table["scale"], "scale", toml_path, lo=MIN_HD_PACK_SCALE, hi=MAX_HD_PACK_SCALE
    )
    default_mode = _parse_enum(
        table.get("default_mode", HdColorMode.RGBA.value),
        HdColorMode,
        "default_mode",
        toml_path,
    )
    fallback_filter = _parse_enum(
        table.get("fallback_filter", HdFallbackFilter.NEAREST.value),
        HdFallbackFilter,
        "fallback_filter",
        toml_path,
    )
    return HdPackConfig(
        schema_version=schema_version,
        name=name,
        version=version,
        author=author,
        scale=scale,
        default_mode=default_mode,
        fallback_filter=fallback_filter,
    )


def _parse_cycle_frames(
    raw: object,
    *,
    meta_path: Path,
    pack_root: Path,
) -> tuple[Path, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not raw:
        raise HdPackError(f"{meta_path}: cycle_frames must be a non-empty list of PNG filenames")
    paths: list[Path] = []
    for item in raw:
        rel = _require_str(item, "cycle_frames[]", meta_path)
        if Path(rel).is_absolute() or "/" in rel or "\\" in rel:
            raise HdPackError(
                f"{meta_path}: cycle_frames entry must be a sibling filename, got {rel!r}"
            )
        candidate = meta_path.parent / rel
        resolved = ensure_within_root(pack_root, candidate, label=str(meta_path))
        if not resolved.is_file() or resolved.suffix.casefold() != ".png":
            raise HdPackError(f"{meta_path}: cycle frame PNG not found: {candidate}")
        paths.append(resolved)
    return tuple(paths)


def parse_surface_meta_toml(meta_path: Path, pack_root: Path) -> HdSurfaceMeta:
    """Parse ``pictures/<name>.meta.toml`` or ``arenas/<name>.meta.toml`` (§14.3.2)."""
    table = _load_toml_table(meta_path, pack_root)
    _check_unknown_keys(table, _SURFACE_META_KEYS, meta_path)
    return HdSurfaceMeta(
        mode=_parse_optional_mode(table, meta_path),
        scale=_parse_optional_scale(table, meta_path),
        padding=_parse_padding(table, meta_path),
        cycle_frames=_parse_cycle_frames(
            table.get("cycle_frames"), meta_path=meta_path, pack_root=pack_root
        ),
    )


def _parse_frame_table(raw_frames: object, meta_path: Path) -> tuple[tuple[int, HdFrameMeta], ...]:
    if raw_frames is None:
        return ()
    if not isinstance(raw_frames, dict):
        raise HdPackError(f"{meta_path}: [frames] must be a table")
    parsed: list[tuple[int, HdFrameMeta]] = []
    for raw_key, raw_entry in raw_frames.items():
        if not isinstance(raw_key, str) or not raw_key.isdigit():
            raise HdPackError(f"{meta_path}: frame key must be a decimal index, got {raw_key!r}")
        if not isinstance(raw_entry, dict):
            raise HdPackError(f"{meta_path}: [frames.{raw_key}] must be a table")
        _check_unknown_keys(raw_entry, _FRAME_META_KEYS, meta_path)
        parsed.append(
            (
                int(raw_key),
                HdFrameMeta(
                    mode=_parse_optional_mode(raw_entry, meta_path),
                    scale=_parse_optional_scale(raw_entry, meta_path),
                    padding=_parse_padding(raw_entry, meta_path),
                    anchor_offset=_parse_anchor_offset(raw_entry, meta_path),
                ),
            )
        )
    return tuple(sorted(parsed, key=lambda pair: pair[0]))


def parse_cel_meta_toml(meta_path: Path, pack_root: Path) -> HdCelMeta:
    """Parse ``cels/<cel>/meta.toml`` (§14.3.2)."""
    table = _load_toml_table(meta_path, pack_root)
    _check_unknown_keys(table, _CEL_META_KEYS, meta_path)
    return HdCelMeta(
        mode=_parse_optional_mode(table, meta_path),
        scale=_parse_optional_scale(table, meta_path),
        frames=_parse_frame_table(table.get("frames"), meta_path),
    )
