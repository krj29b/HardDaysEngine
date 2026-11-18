"""Headless ADF asset extractor producing PNG/WAV/MOD files and `manifest.json` (T3.6)."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image

from harddaysengine.assets.adf import DiskSet, OFSFileEntry
from harddaysengine.assets.arena import composite_arena_background, decode_arena
from harddaysengine.assets.audio import (
    DEFAULT_SAMPLE_RATE,
    decode_cmp_module,
    decode_sample_bank,
    pcm8_to_wav_bytes,
)
from harddaysengine.assets.cel import CelFrame, cel_frame_to_rgba, decode_cel
from harddaysengine.assets.hit import HitFrame, HitSet, compute_cel_hurtbox, parse_collide_hit
from harddaysengine.assets.hunk import (
    HUNK_HEADER,
    parse_hunk_executable,
    parse_mog_synth_instruments,
)
from harddaysengine.assets.manifest import (
    ArenaManifestEntry,
    AssetManifest,
    AudioBankManifestEntry,
    CelFrameManifestEntry,
    CelManifestEntry,
    DiskManifestEntry,
    ExecutableManifestEntry,
    HitPointEntry,
    ModuleManifestEntry,
    PictureManifestEntry,
)
from harddaysengine.assets.palette import RGBColor, default_combat_palette
from harddaysengine.assets.picture import (
    DecodedPicture,
    decode_picture,
    decode_picture_pack,
    picture_to_rgba,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    import numpy as np
    import numpy.typing as npt

CEL_EXTENSIONS = (".cel", ".ob", ".c", ".f", ".font")
PICTURE_EXTENSIONS = (".piv", ".p")


def sha256_hex(data: bytes) -> str:
    """Return the lowercase hexadecimal SHA-256 digest of `data`."""
    return hashlib.sha256(data).hexdigest()


def encode_rgba_png_bytes(rgba: npt.NDArray[np.uint8]) -> bytes:
    """Deterministically encode an RGBA8888 uint8 array into PNG bytes without timestamps."""
    img = Image.fromarray(rgba, mode="RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=1, optimize=False)
    return buf.getvalue()


def _palette_to_hex(palette: Sequence[RGBColor]) -> list[str]:
    return [f"#{r:02x}{g:02x}{b:02x}" for r, g, b in palette]


def _write_output_file(output_dir: Path, rel_path: str, payload: bytes) -> str:
    full_path = output_dir / rel_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_bytes(payload)
    return sha256_hex(payload)


def _extract_single_picture(
    name: str,
    disk_id: str,
    src_sha: str,
    pic: DecodedPicture,
    output_dir: Path,
) -> PictureManifestEntry:
    rgba = picture_to_rgba(pic)
    png_bytes = encode_rgba_png_bytes(rgba)
    safe_stem = name.replace("/", "_").lower()
    rel_png = f"pictures/{safe_stem}.png"
    out_sha = _write_output_file(output_dir, rel_png, png_bytes)
    return PictureManifestEntry(
        name=name,
        source_disk=disk_id,
        source_sha256=src_sha,
        width=pic.width,
        height=pic.height,
        planes=pic.planes,
        palette_hex=_palette_to_hex(pic.palette),
        output_png=rel_png,
        output_sha256=out_sha,
    )


def _build_cel_frame_entry(
    cel_stem: str,
    frame: CelFrame,
    hit_frame: HitFrame | None,
    palette: Sequence[RGBColor],
    output_dir: Path,
    *,
    export_png: bool,
) -> CelFrameManifestEntry:
    hurt = compute_cel_hurtbox(frame)
    hit_type = hit_frame.hit_type if hit_frame is not None else 0
    hit_max = (hit_frame.max_x, hit_frame.max_y) if hit_frame is not None else (0, 0)
    hit_pts = (
        [HitPointEntry(x=p.x, y=p.y) for p in hit_frame.points] if hit_frame is not None else []
    )

    rel_png: str | None = None
    out_sha: str | None = None
    if export_png and frame.meta.width > 0 and frame.meta.height > 0:
        rgba = cel_frame_to_rgba(frame, palette)
        png_bytes = encode_rgba_png_bytes(rgba)
        rel_png = f"cels/{cel_stem}/frame_{frame.meta.index:03d}.png"
        out_sha = _write_output_file(output_dir, rel_png, png_bytes)

    return CelFrameManifestEntry(
        index=frame.meta.index,
        width=frame.meta.width,
        height=frame.meta.height,
        hot_x=frame.meta.hot_x,
        unflipped=frame.meta.unflipped,
        plane_bits=frame.meta.plane_bits,
        opaque_pixels=hurt.opaque_pixels,
        hurt_tight_box=(hurt.tight_x, hurt.tight_y, hurt.tight_width, hurt.tight_height),
        hit_type=hit_type,
        hit_max_box=hit_max,
        hit_points=hit_pts,
        output_png=rel_png,
        output_sha256=out_sha,
    )


def _extract_cel_entry(
    entry: OFSFileEntry,
    raw_bytes: bytes,
    hit_sets: dict[str, HitSet],
    palette: Sequence[RGBColor],
    output_dir: Path,
    *,
    export_png: bool,
) -> CelManifestEntry:
    cel = decode_cel(raw_bytes)
    stem = Path(entry.norm_name).stem
    hit_set = hit_sets.get(stem)
    frame_entries: list[CelFrameManifestEntry] = []
    for idx, frame in enumerate(cel.frames):
        hf = hit_set.frames[idx] if (hit_set is not None and idx < len(hit_set.frames)) else None
        frame_entries.append(
            _build_cel_frame_entry(stem, frame, hf, palette, output_dir, export_png=export_png)
        )
    return CelManifestEntry(
        name=entry.name.strip(),
        norm_name=entry.norm_name,
        source_disk=entry.disk_id,
        source_sha256=sha256_hex(raw_bytes),
        frame_count=cel.frame_count,
        unpacked_bytes=cel.unpacked_bits >> 3,
        has_hit_set=hit_set is not None,
        frames=frame_entries,
    )


def _extract_arena_entry(
    entry: OFSFileEntry,
    raw_bytes: bytes,
    test_pics: Sequence[DecodedPicture],
    output_dir: Path,
) -> ArenaManifestEntry:
    arena = decode_arena(entry.norm_name, raw_bytes)
    rel_png: str | None = None
    out_sha: str | None = None
    if len(test_pics) >= 7:
        bg_pic = composite_arena_background(arena, test_pics)
        png_bytes = encode_rgba_png_bytes(picture_to_rgba(bg_pic))
        stem = Path(entry.norm_name).stem
        rel_png = f"arenas/{stem}.png"
        out_sha = _write_output_file(output_dir, rel_png, png_bytes)

    return ArenaManifestEntry(
        name=entry.name.strip(),
        region=arena.region,
        is_lair=arena.is_lair,
        source_disk=entry.disk_id,
        source_sha256=sha256_hex(raw_bytes),
        obstacle_count=len(arena.obstacles),
        tile_op_count=len(arena.tiles),
        output_png=rel_png,
        output_sha256=out_sha,
    )


def _extract_audio_bank_entry(
    entry: OFSFileEntry,
    raw_bytes: bytes,
    output_dir: Path,
) -> AudioBankManifestEntry:
    bank = decode_sample_bank(entry.norm_name, raw_bytes)
    wav_bytes = pcm8_to_wav_bytes(bank.samples_pcm8, sample_rate=DEFAULT_SAMPLE_RATE)
    stem = Path(entry.norm_name).stem
    rel_wav = f"audio/{stem}.wav"
    out_sha = _write_output_file(output_dir, rel_wav, wav_bytes)
    return AudioBankManifestEntry(
        name=entry.name.strip(),
        source_disk=entry.disk_id,
        source_sha256=sha256_hex(raw_bytes),
        sample_bytes=bank.sample_count,
        sample_rate_hz=DEFAULT_SAMPLE_RATE,
        output_wav=rel_wav,
        output_sha256=out_sha,
    )


def _extract_module_entry(
    entry: OFSFileEntry,
    raw_bytes: bytes,
    output_dir: Path,
) -> ModuleManifestEntry:
    mod = decode_cmp_module(entry.norm_name, raw_bytes)
    stem = Path(entry.norm_name).stem
    rel_mod = f"music/{stem}.mod"
    out_sha = _write_output_file(output_dir, rel_mod, mod.raw_mod)
    return ModuleManifestEntry(
        name=entry.name.strip(),
        source_disk=entry.disk_id,
        source_sha256=sha256_hex(raw_bytes),
        title=mod.title,
        signature=mod.signature,
        song_length=mod.song_length,
        pattern_count=mod.pattern_count,
        unpacked_bytes=len(mod.raw_mod),
        output_mod=rel_mod,
        output_sha256=out_sha,
    )


def _extract_executable_entry(entry: OFSFileEntry, raw_bytes: bytes) -> ExecutableManifestEntry:
    hunk_exec = parse_hunk_executable(entry.norm_name, raw_bytes)
    synth_count = 0
    if entry.norm_name == "mog" and len(hunk_exec.sections) > 45:
        synth_count = len(parse_mog_synth_instruments(hunk_exec))
    return ExecutableManifestEntry(
        name=entry.name.strip(),
        source_disk=entry.disk_id,
        source_sha256=sha256_hex(raw_bytes),
        size_bytes=len(raw_bytes),
        hunk_count=len(hunk_exec.sections),
        synth_instrument_count=synth_count,
    )


def _is_hunk_binary(raw_bytes: bytes) -> bool:
    if len(raw_bytes) < 4:
        return False
    return int.from_bytes(raw_bytes[:4], "big") == HUNK_HEADER


def _collect_disk_manifests(disk_set: DiskSet) -> list[DiskManifestEntry]:
    disks: list[DiskManifestEntry] = []
    for role in sorted(disk_set.disks):
        vol = disk_set.disks[role]
        fname = vol.source_path.name if vol.source_path is not None else f"Disk_{role}.adf"
        raw = vol.source_path.read_bytes() if vol.source_path is not None else b""
        disks.append(
            DiskManifestEntry(
                disk_id=role,
                filename=fname,
                size_bytes=len(raw),
                sha256=sha256_hex(raw),
                file_count=len(vol.list_entries()),
            )
        )
    return disks


@dataclass(slots=True)
class _ExtractionAccumulator:
    hit_sets: dict[str, HitSet]
    test_pics: list[DecodedPicture]
    combat_pal: tuple[RGBColor, ...]
    pictures: list[PictureManifestEntry]
    cels: list[CelManifestEntry]
    arenas: list[ArenaManifestEntry]
    audio_banks: list[AudioBankManifestEntry]
    modules: list[ModuleManifestEntry]
    executables: list[ExecutableManifestEntry]


def _dispatch_entry(
    entry: OFSFileEntry,
    raw: bytes,
    acc: _ExtractionAccumulator,
    output_dir: Path,
    *,
    export_cel_pngs: bool,
) -> None:
    norm = entry.norm_name
    if norm.endswith(PICTURE_EXTENSIONS) or norm == "mindscape":
        pic = decode_picture(raw)
        acc.pictures.append(
            _extract_single_picture(
                entry.name.strip(), entry.disk_id, sha256_hex(raw), pic, output_dir
            )
        )
    elif norm.endswith(CEL_EXTENSIONS):
        acc.cels.append(
            _extract_cel_entry(
                entry, raw, acc.hit_sets, acc.combat_pal, output_dir, export_png=export_cel_pngs
            )
        )
    elif norm.endswith(".t"):
        acc.arenas.append(_extract_arena_entry(entry, raw, acc.test_pics, output_dir))
    elif norm.endswith(".a"):
        acc.audio_banks.append(_extract_audio_bank_entry(entry, raw, output_dir))
    elif norm.endswith(".cmp"):
        acc.modules.append(_extract_module_entry(entry, raw, output_dir))
    elif _is_hunk_binary(raw):
        acc.executables.append(_extract_executable_entry(entry, raw))


def _extract_test_pack(
    disk_set: DiskSet,
    output_dir: Path,
) -> tuple[list[DecodedPicture], list[PictureManifestEntry]]:
    if not disk_set.has_file("test"):
        return [], []
    test_entry = disk_set.get_entry("test")
    test_raw = disk_set.read_file("test")
    test_sha = sha256_hex(test_raw)
    test_pics = decode_picture_pack(test_raw)
    entries = [
        _extract_single_picture(f"test_{idx}", test_entry.disk_id, test_sha, pic, output_dir)
        for idx, pic in enumerate(test_pics)
    ]
    return test_pics, entries


def extract_disk_set(
    disk_set: DiskSet,
    output_dir: Path,
    *,
    export_cel_pngs: bool = True,
) -> AssetManifest:
    """Extract all assets from a `DiskSet` into `output_dir` and write `manifest.json`."""
    output_dir.mkdir(parents=True, exist_ok=True)
    hit_sets = (
        parse_collide_hit(disk_set.read_file("collide.hit"))
        if disk_set.has_file("collide.hit")
        else {}
    )
    test_pics, pictures = _extract_test_pack(disk_set, output_dir)
    combat_pal = test_pics[0].palette if test_pics else default_combat_palette()
    acc = _ExtractionAccumulator(
        hit_sets=hit_sets,
        test_pics=test_pics,
        combat_pal=combat_pal,
        pictures=pictures,
        cels=[],
        arenas=[],
        audio_banks=[],
        modules=[],
        executables=[],
    )

    for entry in disk_set.list_entries():
        if entry.norm_name in ("test", "collide.hit") or "/" in entry.norm_name:
            continue
        raw = disk_set.read_file(entry.norm_name)
        _dispatch_entry(entry, raw, acc, output_dir, export_cel_pngs=export_cel_pngs)

    manifest = AssetManifest(
        disks=_collect_disk_manifests(disk_set),
        pictures=sorted(acc.pictures, key=lambda p: p.name.lower()),
        cels=sorted(acc.cels, key=lambda c: c.norm_name),
        arenas=sorted(acc.arenas, key=lambda a: a.name.lower()),
        audio_banks=sorted(acc.audio_banks, key=lambda b: b.name.lower()),
        modules=sorted(acc.modules, key=lambda m: m.name.lower()),
        executables=sorted(acc.executables, key=lambda e: e.name.lower()),
    )
    (output_dir / "manifest.json").write_text(manifest.to_deterministic_json(), encoding="utf-8")
    return manifest


def extract_all_assets(
    adf_dir: Path,
    output_dir: Path,
    *,
    export_cel_pngs: bool = True,
) -> AssetManifest:
    """Discover `.adf` images in `adf_dir` and extract all assets into `output_dir`."""
    disk_set = DiskSet.from_directory(adf_dir)
    return extract_disk_set(disk_set, output_dir, export_cel_pngs=export_cel_pngs)
