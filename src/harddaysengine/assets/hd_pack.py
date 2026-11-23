"""Headless external HD asset pack loader and validator (ARCHITECTURE.md §14.2-4, ADR-071)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from PIL import Image

from harddaysengine.assets.cel import decode_cel
from harddaysengine.assets.hd_pack_schema import (
    MAX_ASSET_SCALE,
    MAX_PALETTE_INDEX,
    MIN_ASSET_SCALE,
    HdAssetEntry,
    HdAssetKind,
    HdCelMeta,
    HdColorMode,
    HdImage,
    HdPackConfig,
    HdPackError,
    HdPackValidationReport,
    HdSurfaceMeta,
    ensure_within_root,
    normalize_hd_asset_key,
    normalize_variant,
    parse_cel_meta_toml,
    parse_frame_filename,
    parse_pack_toml,
    parse_surface_meta_toml,
    split_key_and_variant,
)
from harddaysengine.assets.picture import PICTURE_HEIGHT, PICTURE_WIDTH, decode_picture_pack

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    import numpy.typing as npt

    from harddaysengine.assets.adf import DiskSet

_PICTURE_SUFFIXES = (".piv", ".p")
_CEL_SUFFIXES = (".cel", ".ob", ".c", ".f", ".font")


def _resolve_entry_mode(
    *,
    explicit_mode: HdColorMode | None,
    default_mode: HdColorMode,
    mask_path: Path | None,
    source: Path,
) -> HdColorMode:
    if explicit_mode is not None:
        if explicit_mode is HdColorMode.MASKED and mask_path is None:
            raise HdPackError(f"{source}: mode='masked' requires a matching .mask.png file")
        return explicit_mode
    if mask_path is not None and default_mode in (HdColorMode.RGBA, HdColorMode.MASKED):
        return HdColorMode.MASKED
    if default_mode is HdColorMode.MASKED and mask_path is None:
        raise HdPackError(f"{source}: default_mode='masked' requires a matching .mask.png file")
    return default_mode


def _effective_dimensions(
    png_w: int,
    png_h: int,
    padding: tuple[int, int, int, int],
    source: Path,
) -> tuple[int, int]:
    pad_l, pad_t, pad_r, pad_b = padding
    eff_w = png_w - pad_l - pad_r
    eff_h = png_h - pad_t - pad_b
    if eff_w <= 0 or eff_h <= 0:
        raise HdPackError(f"{source}: padding {padding} exceeds image dimensions {png_w}x{png_h}")
    return eff_w, eff_h


def _validate_dimensions(
    png_w: int,
    png_h: int,
    *,
    orig_w: int,
    orig_h: int,
    padding: tuple[int, int, int, int],
    declared_scale: int | None,
    source: Path,
) -> int:
    eff_w, eff_h = _effective_dimensions(png_w, png_h, padding, source)
    if orig_w <= 0 or orig_h <= 0:
        raise HdPackError(f"{source}: original dimensions must be positive, got {orig_w}x{orig_h}")
    if eff_w % orig_w != 0 or eff_h % orig_h != 0:
        raise HdPackError(
            f"{source}: effective size {eff_w}x{eff_h} is not an integer multiple of "
            f"original {orig_w}x{orig_h}"
        )
    kx, ky = eff_w // orig_w, eff_h // orig_h
    if kx != ky or not MIN_ASSET_SCALE <= kx <= MAX_ASSET_SCALE:
        raise HdPackError(
            f"{source}: inferred scale ({kx}x, {ky}x) must be uniform in "
            f"{MIN_ASSET_SCALE}..{MAX_ASSET_SCALE}"
        )
    if declared_scale is not None and kx != declared_scale:
        raise HdPackError(
            f"{source}: inferred scale {kx}x does not match declared scale {declared_scale}x"
        )
    return kx


def _validate_unpadded_without_orig(
    png_w: int,
    png_h: int,
    *,
    padding: tuple[int, int, int, int],
    declared_scale: int | None,
    source: Path,
) -> None:
    eff_w, eff_h = _effective_dimensions(png_w, png_h, padding, source)
    if declared_scale is not None and (eff_w % declared_scale != 0 or eff_h % declared_scale != 0):
        raise HdPackError(
            f"{source}: effective size {eff_w}x{eff_h} is not divisible by "
            f"declared scale {declared_scale}x"
        )


def _decode_indexed_png(path: Path) -> tuple[int, int, npt.NDArray[np.uint8]]:
    try:
        with Image.open(path) as img:
            if img.mode not in ("P", "L"):
                raise HdPackError(
                    f"{path}: indexed/mask PNG must be 8-bit 'P' or 'L', got {img.mode!r}"
                )
            arr = np.ascontiguousarray(np.asarray(img, dtype=np.uint8))
    except HdPackError:
        raise
    except (OSError, ValueError) as exc:
        raise HdPackError(f"{path}: unreadable PNG: {exc}") from exc
    if arr.ndim != 2 or arr.size == 0:
        raise HdPackError(f"{path}: expected non-empty 2D indexed PNG")
    max_val = int(arr.max())
    if max_val > MAX_PALETTE_INDEX:
        raise HdPackError(
            f"{path}: palette index {max_val} exceeds Amiga 5-bit maximum ({MAX_PALETTE_INDEX})"
        )
    return int(arr.shape[1]), int(arr.shape[0]), arr


def _decode_rgba_png(path: Path) -> tuple[int, int, npt.NDArray[np.uint8]]:
    try:
        with Image.open(path) as img:
            rgba_img = img.convert("RGBA")
            arr = np.ascontiguousarray(np.asarray(rgba_img, dtype=np.uint8))
    except (OSError, ValueError) as exc:
        raise HdPackError(f"{path}: unreadable PNG: {exc}") from exc
    if arr.ndim != 3 or arr.shape[2] != 4 or arr.size == 0:
        raise HdPackError(f"{path}: expected non-empty RGBA PNG")
    return int(arr.shape[1]), int(arr.shape[0]), arr


def _decode_mask_png(mask_path: Path, expected_w: int, expected_h: int) -> npt.NDArray[np.uint8]:
    mw, mh, mask = _decode_indexed_png(mask_path)
    if (mw, mh) != (expected_w, expected_h):
        raise HdPackError(
            f"{mask_path}: mask dimensions {mw}x{mh} do not match diffuse {expected_w}x{expected_h}"
        )
    return mask


def _decode_image_file(
    image_path: Path,
    *,
    mode: HdColorMode,
    mask_path: Path | None,
    padding: tuple[int, int, int, int],
    anchor_offset: tuple[int, int],
    declared_scale: int | None,
    orig_dims: tuple[int, int] | None,
) -> HdImage:
    if mode is HdColorMode.INDEXED:
        png_w, png_h, indexed = _decode_indexed_png(image_path)
        rgba: npt.NDArray[np.uint8] | None = None
        mask: npt.NDArray[np.uint8] | None = None
    else:
        png_w, png_h, rgba = _decode_rgba_png(image_path)
        indexed = None
        if mode is HdColorMode.MASKED:
            if mask_path is None or not mask_path.is_file():
                raise HdPackError(f"{image_path}: missing required mask PNG")
            mask = _decode_mask_png(mask_path, png_w, png_h)
        else:
            mask = None

    if orig_dims is not None:
        scale = _validate_dimensions(
            png_w,
            png_h,
            orig_w=orig_dims[0],
            orig_h=orig_dims[1],
            padding=padding,
            declared_scale=declared_scale,
            source=image_path,
        )
    else:
        _validate_unpadded_without_orig(
            png_w, png_h, padding=padding, declared_scale=declared_scale, source=image_path
        )
        scale = declared_scale if declared_scale is not None else 1

    return HdImage(
        mode=mode,
        width=png_w,
        height=png_h,
        scale=scale,
        padding=padding,
        anchor_offset=anchor_offset,
        rgba=rgba,
        indexed=indexed,
        mask=mask,
    )


def _scan_surface_meta_files(
    subdir: Path,
    pack_root: Path,
) -> tuple[dict[str, HdSurfaceMeta], set[Path]]:
    metas: dict[str, HdSurfaceMeta] = {}
    cycle_claimed: set[Path] = set()
    for path in sorted(subdir.iterdir(), key=lambda p: p.name.casefold()):
        if not path.is_file() or not path.name.casefold().endswith(".meta.toml"):
            continue
        stem = path.name[: -len(".meta.toml")].casefold()
        meta = parse_surface_meta_toml(path, pack_root)
        metas[stem] = meta
        cycle_claimed.update(meta.cycle_frames)
    return metas, cycle_claimed


def _classify_surface_files(
    subdir: Path,
    pack_root: Path,
    cycle_claimed: set[Path],
) -> tuple[dict[str, Path], dict[str, Path]]:
    images: dict[str, Path] = {}
    masks: dict[str, Path] = {}
    for path in sorted(subdir.iterdir(), key=lambda p: p.name.casefold()):
        resolved = ensure_within_root(pack_root, path, label=str(path))
        if resolved.is_dir():
            raise HdPackError(f"{subdir}: unexpected subdirectory {path.name!r}")
        lower = path.name.casefold()
        if lower.endswith(".meta.toml"):
            continue
        if resolved in cycle_claimed:
            continue
        if lower.endswith(".mask.png"):
            masks[lower[: -len(".mask.png")]] = resolved
        elif lower.endswith(".png"):
            images[lower[: -len(".png")]] = resolved
        else:
            raise HdPackError(f"{subdir}: unsupported file {path.name!r}")
    return images, masks


def _scan_surface_dir(
    pack_root: Path,
    dirname: str,
    kind: HdAssetKind,
    config: HdPackConfig,
) -> list[HdAssetEntry]:
    subdir = pack_root / dirname
    if not subdir.exists():
        return []
    if not subdir.is_dir():
        raise HdPackError(f"{subdir}: expected a directory")
    ensure_within_root(pack_root, subdir, label=dirname)

    metas, cycle_claimed = _scan_surface_meta_files(subdir, pack_root)
    images, masks = _classify_surface_files(subdir, pack_root, cycle_claimed)

    orphan_masks = sorted(set(masks) - set(images))
    if orphan_masks:
        raise HdPackError(f"{subdir}: mask without matching PNG: {orphan_masks[0]}.mask.png")
    orphan_metas = sorted(set(metas) - set(images))
    if orphan_metas:
        raise HdPackError(f"{subdir}: meta.toml without matching PNG: {orphan_metas[0]}.meta.toml")

    entries: list[HdAssetEntry] = []
    for stem in sorted(images):
        key, variant = split_key_and_variant(stem)
        image_path = images[stem]
        mask_path = masks.get(stem)
        meta = metas.get(stem)
        mode = _resolve_entry_mode(
            explicit_mode=meta.mode if meta is not None else None,
            default_mode=config.default_mode,
            mask_path=mask_path,
            source=image_path,
        )
        entries.append(
            HdAssetEntry(
                kind=kind,
                key=key,
                variant=variant,
                frame_index=None,
                image_path=image_path,
                mask_path=mask_path,
                mode=mode,
                declared_scale=meta.scale if meta is not None else None,
                padding=meta.padding if meta is not None else (0, 0, 0, 0),
                anchor_offset=(0, 0),
                cycle_frame_paths=meta.cycle_frames if meta is not None else (),
            )
        )
    return entries


def _classify_cel_dir_files(
    cel_dir: Path,
    pack_root: Path,
) -> tuple[dict[int, Path], dict[int, Path], HdCelMeta | None]:
    frames: dict[int, Path] = {}
    masks: dict[int, Path] = {}
    meta: HdCelMeta | None = None
    for path in sorted(cel_dir.iterdir(), key=lambda p: p.name.casefold()):
        resolved = ensure_within_root(pack_root, path, label=str(path))
        if resolved.is_dir():
            raise HdPackError(f"{cel_dir}: unexpected nested directory {path.name!r}")
        lower = path.name.casefold()
        if lower == "meta.toml":
            meta = parse_cel_meta_toml(resolved, pack_root)
            continue
        parsed = parse_frame_filename(lower)
        if parsed is None:
            raise HdPackError(
                f"{cel_dir}: unsupported cel file {path.name!r} "
                "(expected frame_NNN.png, frame_NNN.mask.png, or meta.toml)"
            )
        frame_idx, is_mask = parsed
        target = masks if is_mask else frames
        if frame_idx in target:
            raise HdPackError(f"{cel_dir}: duplicate frame index {frame_idx}")
        target[frame_idx] = resolved
    return frames, masks, meta


def _build_cel_frame_entry(
    *,
    key: str,
    variant: str | None,
    frame_index: int,
    image_path: Path,
    mask_path: Path | None,
    meta: HdCelMeta | None,
    config: HdPackConfig,
) -> HdAssetEntry:
    frame_meta = meta.frame_meta(frame_index) if meta is not None else None
    cel_mode = meta.mode if meta is not None else None
    cel_scale = meta.scale if meta is not None else None
    explicit_mode = (
        frame_meta.mode if frame_meta is not None and frame_meta.mode is not None else cel_mode
    )
    declared_scale = (
        frame_meta.scale if frame_meta is not None and frame_meta.scale is not None else cel_scale
    )
    mode = _resolve_entry_mode(
        explicit_mode=explicit_mode,
        default_mode=config.default_mode,
        mask_path=mask_path,
        source=image_path,
    )
    return HdAssetEntry(
        kind=HdAssetKind.CEL,
        key=key,
        variant=variant,
        frame_index=frame_index,
        image_path=image_path,
        mask_path=mask_path,
        mode=mode,
        declared_scale=declared_scale,
        padding=frame_meta.padding if frame_meta is not None else (0, 0, 0, 0),
        anchor_offset=frame_meta.anchor_offset if frame_meta is not None else (0, 0),
    )


def _scan_single_cel_dir(
    cel_dir: Path,
    pack_root: Path,
    config: HdPackConfig,
) -> list[HdAssetEntry]:
    key, variant = split_key_and_variant(cel_dir.name)
    frames, masks, meta = _classify_cel_dir_files(cel_dir, pack_root)
    orphan_masks = sorted(set(masks) - set(frames))
    if orphan_masks:
        raise HdPackError(
            f"{cel_dir}: mask without matching frame PNG: frame_{orphan_masks[0]:03d}.mask.png"
        )
    if meta is not None:
        orphan_meta_frames = sorted({idx for idx, _ in meta.frames} - set(frames))
        if orphan_meta_frames:
            raise HdPackError(
                f"{cel_dir}: meta.toml references missing frame {orphan_meta_frames[0]}"
            )
    return [
        _build_cel_frame_entry(
            key=key,
            variant=variant,
            frame_index=idx,
            image_path=frames[idx],
            mask_path=masks.get(idx),
            meta=meta,
            config=config,
        )
        for idx in sorted(frames)
    ]


def _scan_cels_dir(pack_root: Path, config: HdPackConfig) -> list[HdAssetEntry]:
    cels_dir = pack_root / "cels"
    if not cels_dir.exists():
        return []
    if not cels_dir.is_dir():
        raise HdPackError(f"{cels_dir}: expected a directory")
    ensure_within_root(pack_root, cels_dir, label="cels")

    entries: list[HdAssetEntry] = []
    for child in sorted(cels_dir.iterdir(), key=lambda p: p.name.casefold()):
        resolved = ensure_within_root(pack_root, child, label=str(child))
        if not resolved.is_dir():
            raise HdPackError(f"{cels_dir}: unexpected file {child.name!r}; expected cel directory")
        entries.extend(_scan_single_cel_dir(resolved, pack_root, config))
    return entries


def _collect_disk_inventory(
    disk_set: DiskSet,
) -> tuple[set[str], set[str], dict[str, tuple[tuple[int, int], ...]]]:
    pictures: set[str] = set()
    arenas: set[str] = set()
    cel_dims: dict[str, tuple[tuple[int, int], ...]] = {}

    if disk_set.has_file("test"):
        test_pics = decode_picture_pack(disk_set.read_file("test"))
        pictures.update(f"test_{idx}" for idx in range(len(test_pics)))

    for entry in disk_set.list_entries():
        norm = entry.norm_name
        if "/" in norm:
            continue
        if norm.endswith(_PICTURE_SUFFIXES) or norm == "mindscape":
            pictures.add(norm)
        elif norm.endswith(".t"):
            arenas.add(norm)
        elif norm.endswith(_CEL_SUFFIXES):
            cel = decode_cel(disk_set.read_file(norm))
            cel_dims[norm] = tuple((f.meta.width, f.meta.height) for f in cel.frames)
    return pictures, arenas, cel_dims


def _resolve_disk_dims(
    entry: HdAssetEntry,
    *,
    disk_pictures: set[str],
    disk_arenas: set[str],
    disk_cels: Mapping[str, tuple[tuple[int, int], ...]],
    warnings: list[str],
) -> tuple[int, int] | None:
    if entry.kind is HdAssetKind.PICTURE:
        if entry.key not in disk_pictures:
            warnings.append(f"unrecognized picture asset key: {entry.key}")
        return (PICTURE_WIDTH, PICTURE_HEIGHT)
    if entry.kind is HdAssetKind.ARENA:
        if entry.key not in disk_arenas:
            warnings.append(f"unrecognized arena asset key: {entry.key}")
        return (PICTURE_WIDTH, PICTURE_HEIGHT)
    if entry.key not in disk_cels:
        warnings.append(f"unrecognized cel asset key: {entry.key}")
        return None
    frames = disk_cels[entry.key]
    assert entry.frame_index is not None
    if not 0 <= entry.frame_index < len(frames):
        raise HdPackError(
            f"{entry.image_path}: frame {entry.frame_index} out of range for {entry.key} "
            f"({len(frames)} frames)"
        )
    return frames[entry.frame_index]


class HdPack:
    """Loaded external HD pack with lazy PNG decoding and variant resolution (§14.2-4)."""

    def __init__(
        self,
        root: Path,
        config: HdPackConfig,
        entries: tuple[HdAssetEntry, ...],
    ) -> None:
        self._root = root.resolve()
        self._config = config
        self._entries = entries
        self._index: dict[tuple[HdAssetKind, str, int | None, str | None], HdAssetEntry] = {}
        for entry in entries:
            lookup_key = (entry.kind, entry.key, entry.frame_index, entry.variant)
            if lookup_key in self._index:
                raise HdPackError(
                    f"{self._root}: duplicate override for {entry.kind.value} {entry.key}"
                )
            self._index[lookup_key] = entry

    @classmethod
    def load(cls, pack_root: Path) -> HdPack:
        """Parse ``pack.toml`` and scan all override directories under ``pack_root`` (§14.4)."""
        if not pack_root.exists() or not pack_root.is_dir():
            raise HdPackError(f"HD pack directory does not exist: {pack_root}")
        resolved_root = pack_root.resolve()
        config = parse_pack_toml(resolved_root)
        entries: list[HdAssetEntry] = []
        entries.extend(_scan_surface_dir(resolved_root, "pictures", HdAssetKind.PICTURE, config))
        entries.extend(_scan_surface_dir(resolved_root, "arenas", HdAssetKind.ARENA, config))
        entries.extend(_scan_cels_dir(resolved_root, config))
        return cls(resolved_root, config, tuple(entries))

    @property
    def root(self) -> Path:
        """Resolved root directory of this HD pack."""
        return self._root

    @property
    def config(self) -> HdPackConfig:
        """Validated root ``pack.toml`` configuration."""
        return self._config

    @property
    def entries(self) -> tuple[HdAssetEntry, ...]:
        """All indexed override entries in deterministic order."""
        return self._entries

    def _resolve(
        self,
        kind: HdAssetKind,
        name: str,
        frame_index: int | None,
        variant: str | None,
    ) -> HdAssetEntry | None:
        key = normalize_hd_asset_key(name)
        if variant is not None:
            norm_variant = normalize_variant(variant)
            exact = self._index.get((kind, key, frame_index, norm_variant))
            if exact is not None:
                return exact
        return self._index.get((kind, key, frame_index, None))

    def resolve_picture(self, name: str, *, variant: str | None = None) -> HdAssetEntry | None:
        """Look up a picture override (exact ``__variant`` first, then base) (§14.4)."""
        return self._resolve(HdAssetKind.PICTURE, name, None, variant)

    def resolve_arena(self, name: str, *, variant: str | None = None) -> HdAssetEntry | None:
        """Look up an arena override (exact ``__variant`` first, then base) (§14.4)."""
        return self._resolve(HdAssetKind.ARENA, name, None, variant)

    def resolve_cel_frame(
        self,
        name: str,
        frame_index: int,
        *,
        variant: str | None = None,
    ) -> HdAssetEntry | None:
        """Look up a cel frame override (exact ``__variant`` first, then base) (§14.4)."""
        return self._resolve(HdAssetKind.CEL, name, frame_index, variant)

    def load_image(
        self,
        entry: HdAssetEntry,
        *,
        orig_width: int,
        orig_height: int,
    ) -> HdImage:
        """Decode and dimension-validate ``entry`` against ``(orig_width, orig_height)``."""
        return _decode_image_file(
            entry.image_path,
            mode=entry.mode,
            mask_path=entry.mask_path,
            padding=entry.padding,
            anchor_offset=entry.anchor_offset,
            declared_scale=entry.declared_scale,
            orig_dims=(orig_width, orig_height),
        )

    def load_cycle_frame(
        self,
        entry: HdAssetEntry,
        cycle_index: int,
        *,
        orig_width: int = PICTURE_WIDTH,
        orig_height: int = PICTURE_HEIGHT,
    ) -> HdImage:
        """Decode one frame from ``entry.cycle_frame_paths`` modulo sequence length (§14.3.2)."""
        if not entry.cycle_frame_paths:
            raise HdPackError(f"{entry.image_path}: entry has no cycle_frames configured")
        frame_path = entry.cycle_frame_paths[cycle_index % len(entry.cycle_frame_paths)]
        return _decode_image_file(
            frame_path,
            mode=entry.mode,
            mask_path=entry.mask_path,
            padding=entry.padding,
            anchor_offset=entry.anchor_offset,
            declared_scale=entry.declared_scale,
            orig_dims=(orig_width, orig_height),
        )

    def validate(self, *, disk_set: DiskSet | None = None) -> HdPackValidationReport:
        """Eagerly validate every PNG, mask, and cycle frame in the pack (§14.4)."""
        warnings: list[str] = []
        disk_inventory = _collect_disk_inventory(disk_set) if disk_set is not None else None
        picture_count = 0
        arena_count = 0
        cel_keys: set[tuple[str, str | None]] = set()
        cel_frame_count = 0
        cycle_frame_count = 0
        mode_counts = {HdColorMode.INDEXED: 0, HdColorMode.RGBA: 0, HdColorMode.MASKED: 0}

        for entry in self._entries:
            orig_dims = self._entry_orig_dims(entry, disk_inventory, warnings)
            _decode_image_file(
                entry.image_path,
                mode=entry.mode,
                mask_path=entry.mask_path,
                padding=entry.padding,
                anchor_offset=entry.anchor_offset,
                declared_scale=entry.declared_scale,
                orig_dims=orig_dims,
            )
            for cycle_path in entry.cycle_frame_paths:
                _decode_image_file(
                    cycle_path,
                    mode=entry.mode,
                    mask_path=entry.mask_path,
                    padding=entry.padding,
                    anchor_offset=entry.anchor_offset,
                    declared_scale=entry.declared_scale,
                    orig_dims=orig_dims,
                )
                cycle_frame_count += 1

            mode_counts[entry.mode] += 1
            if entry.kind is HdAssetKind.PICTURE:
                picture_count += 1
            elif entry.kind is HdAssetKind.ARENA:
                arena_count += 1
            else:
                cel_keys.add((entry.key, entry.variant))
                cel_frame_count += 1

        return HdPackValidationReport(
            pack_root=self._root,
            config=self._config,
            picture_count=picture_count,
            arena_count=arena_count,
            cel_count=len(cel_keys),
            cel_frame_count=cel_frame_count,
            cycle_frame_count=cycle_frame_count,
            indexed_count=mode_counts[HdColorMode.INDEXED],
            rgba_count=mode_counts[HdColorMode.RGBA],
            masked_count=mode_counts[HdColorMode.MASKED],
            checked_against_disks=disk_set is not None,
            warnings=tuple(dict.fromkeys(warnings)),
        )

    @staticmethod
    def _entry_orig_dims(
        entry: HdAssetEntry,
        disk_inventory: tuple[set[str], set[str], dict[str, tuple[tuple[int, int], ...]]] | None,
        warnings: list[str],
    ) -> tuple[int, int] | None:
        if disk_inventory is not None:
            return _resolve_disk_dims(
                entry,
                disk_pictures=disk_inventory[0],
                disk_arenas=disk_inventory[1],
                disk_cels=disk_inventory[2],
                warnings=warnings,
            )
        if entry.kind in (HdAssetKind.PICTURE, HdAssetKind.ARENA):
            return (PICTURE_WIDTH, PICTURE_HEIGHT)
        return None
