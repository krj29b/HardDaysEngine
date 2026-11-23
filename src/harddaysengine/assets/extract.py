"""Headless ADF asset extractor producing PNG/WAV/MOD files and `manifest.json` (T3.6)."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image

from harddaysengine.assets.adf import DiskSet, OFSFileEntry
from harddaysengine.assets.arena import composite_arena_background, decode_arena
from harddaysengine.assets.audio import (
    DEFAULT_SAMPLE_RATE,
    decode_cmp_module,
    decode_sample_bank,
    pcm8_to_wav_bytes,
)
from harddaysengine.assets.cel import CelFormatError, CelFrame, cel_frame_to_rgba, decode_cel
from harddaysengine.assets.dice_house_data import parse_dice_house
from harddaysengine.assets.ending_data import parse_ending_plan
from harddaysengine.assets.fight_defs import parse_fight_defs
from harddaysengine.assets.fight_palette import (
    REGION_FOREST,
    REGION_GLEN,
    REGION_SWAMP,
    REGION_WASTELAND,
    SCENE_BALOK,
    SCENE_DUEL,
    SCENE_FLYER,
    SCENE_GUARDIAN,
    MogVersionError,
    assemble_fight_palette,
    parse_fight_palette_tables,
)
from harddaysengine.assets.hd_pack_schema import MAX_PALETTE_INDEX, normalize_hd_asset_key
from harddaysengine.assets.hit import HitFrame, HitSet, compute_cel_hurtbox, parse_collide_hit
from harddaysengine.assets.hunk import (
    HUNK_HEADER,
    HunkFormatError,
    parse_hunk_executable,
    parse_mog_synth_instruments,
)
from harddaysengine.assets.intro_data import parse_intro_plan, parse_menu_data
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
from harddaysengine.assets.map_rules import parse_map_rules
from harddaysengine.assets.mog_code import MogCode
from harddaysengine.assets.palette import (
    RGBColor,
    cell_word_to_rgb,
    decode_palette_word,
    default_combat_palette,
)
from harddaysengine.assets.picture import (
    DecodedPicture,
    PictureFormatError,
    decode_picture,
    decode_picture_pack,
    picture_to_rgba,
)
from harddaysengine.assets.service_screen_data import parse_service_screens
from harddaysengine.assets.sheet_data import parse_sheet_data
from harddaysengine.assets.stonehenge_data import parse_stone_rules
from harddaysengine.assets.valley_data import valley_arena
from harddaysengine.assets.wizard_screen_data import parse_wizard_rules

if TYPE_CHECKING:
    from collections.abc import Sequence

    import numpy.typing as npt

    from harddaysengine.assets.fight_defs import ArenaDef, FightDefs
    from harddaysengine.assets.fight_palette import FightPaletteTables
    from harddaysengine.assets.hunk import DecodedHunkExecutable

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


# ---------------------------------------------------------------------------
# HD asset pack template exporter (ARCHITECTURE.md §14.5)
# ---------------------------------------------------------------------------

_DEFAULT_ARENA_REGION: dict[str, int] = {
    "dragon": REGION_WASTELAND,
    "guardian": REGION_WASTELAND,
    "trogg_axe": REGION_FOREST,
    "trogg_axe_2": REGION_FOREST,
}

_REGION_TO_FG_SHEET: dict[int, int] = {
    REGION_GLEN: 0,
    REGION_FOREST: 0,
    REGION_SWAMP: 2,
    REGION_WASTELAND: 3,
}

_PROGRAM_CEL_PICTURES: dict[str, tuple[str, ...]] = {
    "au1.cel": ("bg4.piv", "bg5a.piv"),
    "li1.cel": ("bg5a.piv",),
    "da1.cel": ("bg3.piv",),
    "ha1.cel": ("bg2a.piv",),
    "dw1.cel": ("bg2.piv",),
    "ov1.cel": ("bg2a.piv", "bg2.piv"),
    "dg1.cel": ("bg5.piv",),
    "klift1.cel": ("bg5.piv", "bg5a.piv"),
    "co1.cel": ("bg7.piv",),
}


@dataclass(slots=True, frozen=True)
class HdTemplateSummary:
    """Summary of an exported 1x HD asset pack template (ARCHITECTURE.md §14.5)."""

    pack_root: Path
    picture_count: int
    arena_count: int
    cel_count: int
    cel_frame_count: int
    mask_count: int
    meta_count: int
    skipped_zero_frames: int


@dataclass(slots=True)
class _TemplateContext:
    disk_set: DiskSet
    test_pics: list[DecodedPicture]
    hit_sets: dict[str, HitSet]
    map_mask_indices: frozenset[int]
    cel_palettes: dict[str, tuple[RGBColor, ...]]
    frame_palettes: dict[tuple[str, int], tuple[RGBColor, ...]]


def encode_mask_png_bytes(mask: npt.NDArray[np.uint8]) -> bytes:
    """Deterministically encode a 2D uint8 palette-slot mask (0..31) as 8-bit 'L' PNG (§14.5)."""
    raw = np.asarray(mask)
    if raw.dtype != np.uint8:
        raise ValueError("Mask array must have dtype uint8")
    if raw.ndim != 2 or raw.size == 0:
        raise ValueError("Mask array must be a non-empty 2D uint8 array")
    max_val = int(raw.max())
    if max_val > MAX_PALETTE_INDEX:
        raise ValueError(f"Mask index {max_val} exceeds Amiga 5-bit maximum ({MAX_PALETTE_INDEX})")
    arr = np.ascontiguousarray(raw)
    img = Image.fromarray(arr, mode="L")
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=1, optimize=False)
    return buf.getvalue()


def _try_parse_hunk(disk_set: DiskSet, name: str) -> DecodedHunkExecutable | None:
    if not disk_set.has_file(name):
        return None
    try:
        return parse_hunk_executable(name, disk_set.read_file(name))
    except (FileNotFoundError, HunkFormatError):
        return None


def _words_to_rgb_palette(words: Sequence[int]) -> tuple[RGBColor, ...]:
    return tuple(cell_word_to_rgb(w & 0x0FFF) for w in words)


def _picture_words_12bit(pic: DecodedPicture) -> tuple[int, ...]:
    return tuple(decode_palette_word(w) for w in pic.raw_palette)


def _apply_palette_patch(
    base: Sequence[RGBColor],
    start: int,
    words: Sequence[int],
) -> tuple[RGBColor, ...]:
    patched = list(base)
    for offset, word in enumerate(words):
        idx = start + offset
        if 0 <= idx < len(patched):
            patched[idx] = cell_word_to_rgb(word & 0x0FFF)
    return tuple(patched)


def _load_picture_palette(disk_set: DiskSet, *names: str) -> tuple[RGBColor, ...] | None:
    for name in names:
        if not disk_set.has_file(name):
            continue
        try:
            pic = decode_picture(disk_set.read_file(name))
        except (FileNotFoundError, PictureFormatError):
            continue
        return tuple(pic.palette)
    return None


def _resolve_arena_sheet_index(
    mog_exec: DecodedHunkExecutable,
    arena: ArenaDef,
    region: int,
    test_count: int,
) -> int:
    if arena.scene == SCENE_GUARDIAN:
        try:
            idx = valley_arena(MogCode(mog_exec)).picture
            if 0 <= idx < test_count:
                return idx
        except (MogVersionError, HunkFormatError):
            pass
    idx = _REGION_TO_FG_SHEET.get(region, 0)
    return idx if 0 <= idx < test_count else 0


def _add_arena_cel_palettes(
    mog_exec: DecodedHunkExecutable,
    fight_defs: FightDefs,
    fight_palettes: FightPaletteTables,
    test_pics: Sequence[DecodedPicture],
    cel_palettes: dict[str, tuple[RGBColor, ...]],
) -> None:
    for arena in fight_defs.arenas:
        region = (
            arena.region_override
            if arena.region_override is not None
            else _DEFAULT_ARENA_REGION.get(arena.name, REGION_GLEN)
        )
        sheet_idx = _resolve_arena_sheet_index(mog_exec, arena, region, len(test_pics))
        words = assemble_fight_palette(
            _picture_words_12bit(test_pics[sheet_idx]),
            fight_palettes,
            scene=arena.scene,
            region=region,
            first_kind=0,
        )
        pal = _words_to_rgb_palette(words)
        for cf in arena.cels.slots:
            if cf is not None:
                cel_palettes[normalize_hd_asset_key(cf.name)] = pal


def _add_fight_palettes(
    mog_exec: DecodedHunkExecutable,
    test_pics: Sequence[DecodedPicture],
    cel_palettes: dict[str, tuple[RGBColor, ...]],
) -> None:
    if not test_pics:
        return
    try:
        fight_palettes = parse_fight_palette_tables(mog_exec)
        fight_defs = parse_fight_defs(mog_exec)
    except (MogVersionError, HunkFormatError):
        return

    base_words = _picture_words_12bit(test_pics[0])
    knight_pal = _words_to_rgb_palette(
        assemble_fight_palette(
            base_words, fight_palettes, scene=SCENE_FLYER, region=REGION_GLEN, first_kind=0
        )
    )
    balok_knight_pal = _words_to_rgb_palette(
        assemble_fight_palette(
            base_words, fight_palettes, scene=SCENE_BALOK, region=REGION_GLEN, first_kind=0
        )
    )
    for cf in (*fight_defs.knight_cels.knight_slots, *fight_defs.knight_cels.effect_slots):
        if cf is not None:
            cel_palettes[normalize_hd_asset_key(cf.name)] = knight_pal
    cel_palettes["kn5.ob"] = balok_knight_pal

    second_kind = fight_defs.duel_cels.kind if fight_defs.duel_cels.kind is not None else 4
    duel_pal = _words_to_rgb_palette(
        assemble_fight_palette(
            base_words,
            fight_palettes,
            scene=SCENE_DUEL,
            region=REGION_GLEN,
            first_kind=0,
            second_kind=second_kind,
        )
    )
    for cf in fight_defs.duel_cels.slots:
        if cf is not None:
            key = normalize_hd_asset_key(cf.name)
            if key.startswith("he"):
                cel_palettes[key] = duel_pal

    _add_arena_cel_palettes(mog_exec, fight_defs, fight_palettes, test_pics, cel_palettes)


def _add_sheet_palettes(
    mog_exec: DecodedHunkExecutable,
    cel_palettes: dict[str, tuple[RGBColor, ...]],
) -> None:
    try:
        sheet = parse_sheet_data(mog_exec)
    except (MogVersionError, HunkFormatError):
        return
    base_pal = _words_to_rgb_palette(sheet.palette)
    cel_palettes["po.cel"] = base_pal
    cel_palettes["small.font"] = base_pal
    if sheet.class_pairs:
        cel_palettes["ki.cel"] = _apply_palette_patch(base_pal, 28, sheet.class_pairs[0])
    else:
        cel_palettes["ki.cel"] = base_pal


def _add_dice_palettes(
    disk_set: DiskSet,
    mog_exec: DecodedHunkExecutable,
    cel_palettes: dict[str, tuple[RGBColor, ...]],
    frame_palettes: dict[tuple[str, int], tuple[RGBColor, ...]],
) -> None:
    try:
        dice = parse_dice_house(mog_exec)
    except (MogVersionError, HunkFormatError):
        return
    cel_key = normalize_hd_asset_key(dice.cel_file)
    tav_pal = _load_picture_palette(disk_set, dice.tavern)
    if tav_pal is not None:
        cel_palettes[cel_key] = _apply_palette_patch(tav_pal, dice.ramp_entry, dice.ramp(0))
    tbl_pal = _load_picture_palette(disk_set, dice.table)
    if tbl_pal is not None:
        for idx in range(len(dice.dice_at)):
            frame_palettes[(cel_key, idx)] = tbl_pal


def _add_service_palettes(
    disk_set: DiskSet,
    mog_exec: DecodedHunkExecutable,
    cel_palettes: dict[str, tuple[RGBColor, ...]],
) -> None:
    try:
        svc = parse_service_screens(mog_exec)
    except (MogVersionError, HunkFormatError):
        return
    pal = _load_picture_palette(disk_set, svc.mystic.picture, svc.healer.picture)
    if pal is not None:
        cel_palettes[normalize_hd_asset_key(svc.donation.cel_file)] = pal


def _add_ritual_palettes(
    disk_set: DiskSet,
    mog_exec: DecodedHunkExecutable,
    cel_palettes: dict[str, tuple[RGBColor, ...]],
) -> None:
    try:
        stone = parse_stone_rules(mog_exec)
    except (MogVersionError, HunkFormatError):
        return
    pal = _load_picture_palette(disk_set, stone.ritual.picture)
    if pal is None:
        return
    ramp = next((words for kind, words in stone.ritual.ramps if kind == 0), ())
    cel_palettes[normalize_hd_asset_key(stone.ritual.cel)] = _apply_palette_patch(
        pal, stone.ritual.palette_entry, ramp
    )


def _add_wizard_palettes(
    disk_set: DiskSet,
    mog_exec: DecodedHunkExecutable,
    cel_palettes: dict[str, tuple[RGBColor, ...]],
) -> None:
    try:
        wiz = parse_wizard_rules(mog_exec)
    except (MogVersionError, HunkFormatError):
        return
    pal = _load_picture_palette(disk_set, wiz.tower, wiz.gift)
    if pal is None:
        return
    ramp = next((words for kind, words in wiz.ramps if kind == 0), ())
    cel_palettes[normalize_hd_asset_key(wiz.cel)] = _apply_palette_patch(
        pal, wiz.palette_entry, ramp
    )


def _add_menu_palettes(
    disk_set: DiskSet,
    mog_exec: DecodedHunkExecutable,
    cel_palettes: dict[str, tuple[RGBColor, ...]],
    frame_palettes: dict[tuple[str, int], tuple[RGBColor, ...]],
) -> None:
    try:
        menu = parse_menu_data(mog_exec)
    except (MogVersionError, HunkFormatError):
        return
    cel_palettes["sel.cel"] = _words_to_rgb_palette(menu.knight_select.palette)
    ch_pal = _load_picture_palette(disk_set, "ch.piv")
    if ch_pal is not None:
        frame_palettes[("sel.cel", menu.title.cursor_frame)] = ch_pal
        cel_palettes.setdefault("bold.f", ch_pal)


def _add_program_cel_palettes(
    disk_set: DiskSet,
    prog_exec: DecodedHunkExecutable | None,
    cel_palettes: dict[str, tuple[RGBColor, ...]],
) -> None:
    if prog_exec is None:
        return
    loaded_cels: set[str] = set()
    for parser in (parse_intro_plan, parse_ending_plan):
        try:
            plan = parser(prog_exec)
        except (MogVersionError, HunkFormatError):
            continue
        loaded_cels.update(normalize_hd_asset_key(name) for _, name in plan.load.cels)
        loaded_cels.add(normalize_hd_asset_key(plan.load.overlay))
    for cel_key in sorted(loaded_cels):
        pic_names = _PROGRAM_CEL_PICTURES.get(cel_key, ())
        pal = _load_picture_palette(disk_set, *pic_names)
        if pal is not None:
            cel_palettes.setdefault(cel_key, pal)


def _add_picture_fallbacks(
    disk_set: DiskSet,
    test_pics: Sequence[DecodedPicture],
    cel_palettes: dict[str, tuple[RGBColor, ...]],
) -> None:
    if len(test_pics) > 8:
        map_pal = tuple(test_pics[8].palette)
        cel_palettes.setdefault("mi.c", map_pal)
        cel_palettes.setdefault("small.font", map_pal)
    for cel_key, pic_names in (
        ("bold.f", ("ch.piv", "message.piv")),
        ("sel.cel", ("ch.piv",)),
        ("dice.cel", ("tav.piv", "dice.piv")),
        ("mys.cel", ("mys.piv", "hea.piv")),
        ("hen1.c", ("hen1.p",)),
        ("wi1.c", ("wi2.p", "wi1.p")),
    ):
        if cel_key not in cel_palettes:
            pal = _load_picture_palette(disk_set, *pic_names)
            if pal is not None:
                cel_palettes[cel_key] = pal


def _compute_map_mask_indices(
    disk_set: DiskSet,
    mog_exec: DecodedHunkExecutable | None,
) -> frozenset[int]:
    if mog_exec is None or not disk_set.has_file("mi.c"):
        return frozenset()
    try:
        mi_cel = decode_cel(disk_set.read_file("mi.c"))
        sizes = tuple((f.meta.width, f.meta.height) for f in mi_cel.frames)
        if not sizes:
            return frozenset()
        rules = parse_map_rules(mog_exec, sizes)
    except (MogVersionError, HunkFormatError, CelFormatError, ValueError):
        return frozenset()
    raw_indices = {rules.ramp_index, *range(rules.cycle_start, rules.cycle_end + 1)}
    return frozenset(idx for idx in raw_indices if 0 <= idx <= MAX_PALETTE_INDEX)


def _build_template_context(disk_set: DiskSet) -> _TemplateContext:
    test_pics = decode_picture_pack(disk_set.read_file("test")) if disk_set.has_file("test") else []
    hit_sets = (
        parse_collide_hit(disk_set.read_file("collide.hit"))
        if disk_set.has_file("collide.hit")
        else {}
    )
    mog_exec = _try_parse_hunk(disk_set, "mog")
    prog_exec = _try_parse_hunk(disk_set, "program")
    cel_palettes: dict[str, tuple[RGBColor, ...]] = {}
    frame_palettes: dict[tuple[str, int], tuple[RGBColor, ...]] = {}

    if mog_exec is not None:
        _add_fight_palettes(mog_exec, test_pics, cel_palettes)
        _add_sheet_palettes(mog_exec, cel_palettes)
        _add_dice_palettes(disk_set, mog_exec, cel_palettes, frame_palettes)
        _add_service_palettes(disk_set, mog_exec, cel_palettes)
        _add_ritual_palettes(disk_set, mog_exec, cel_palettes)
        _add_wizard_palettes(disk_set, mog_exec, cel_palettes)
        _add_menu_palettes(disk_set, mog_exec, cel_palettes, frame_palettes)

    _add_program_cel_palettes(disk_set, prog_exec, cel_palettes)
    _add_picture_fallbacks(disk_set, test_pics, cel_palettes)
    map_mask_indices = _compute_map_mask_indices(disk_set, mog_exec)

    return _TemplateContext(
        disk_set=disk_set,
        test_pics=test_pics,
        hit_sets=hit_sets,
        map_mask_indices=map_mask_indices,
        cel_palettes=cel_palettes,
        frame_palettes=frame_palettes,
    )


def _resolve_cel_palette(
    ctx: _TemplateContext,
    norm_key: str,
    frame_index: int,
) -> tuple[RGBColor, ...]:
    frame_pal = ctx.frame_palettes.get((norm_key, frame_index))
    if frame_pal is not None:
        return frame_pal
    cel_pal = ctx.cel_palettes.get(norm_key)
    if cel_pal is not None:
        return cel_pal
    if ctx.test_pics:
        return tuple(ctx.test_pics[0].palette)
    return default_combat_palette()


def _build_cel_mask(norm_key: str, pixels: npt.NDArray[np.uint8]) -> npt.NDArray[np.uint8] | None:
    if norm_key.startswith("kn") and norm_key.endswith(".ob"):
        keep = (pixels >= 6) & (pixels <= 8)
        return np.where(keep, pixels, 0).astype(np.uint8)
    if norm_key.startswith("he") and norm_key.endswith(".ob"):
        keep = ((pixels >= 6) & (pixels <= 8)) | ((pixels >= 9) & (pixels <= 11))
        return np.where(keep, pixels, 0).astype(np.uint8)
    return None


def _build_arena_mask(pixels: npt.NDArray[np.uint8]) -> npt.NDArray[np.uint8]:
    keep = ((pixels >= 1) & (pixels <= 4)) | ((pixels >= 16) & (pixels <= 28))
    return np.where(keep, pixels, 0).astype(np.uint8)


def _build_map_mask(
    pixels: npt.NDArray[np.uint8],
    indices: frozenset[int],
) -> npt.NDArray[np.uint8] | None:
    if not indices:
        return None
    keep = np.isin(pixels, sorted(indices))
    return np.where(keep, pixels, 0).astype(np.uint8)


def _render_starter_pack_toml() -> str:
    return (
        "schema_version = 1\n"
        'name = "Moonstone HD Template"\n'
        'version = "0.1.0"\n'
        'author = ""\n'
        "scale = 4\n"
        'default_mode = "rgba"\n'
        'fallback_filter = "nearest"\n'
    )


def _format_cel_meta_toml(
    norm_key: str,
    frames: Sequence[CelFrame],
    hit_set: HitSet | None,
) -> str:
    lines = [
        f"# HD template reference metadata for {norm_key} (1x export; ARCHITECTURE.md §14.5)",
        "# Uncomment overrides below only after upscaling (see §14.3.2):",
        '# mode = "rgba"',
        "# scale = 4",
        "#",
    ]
    for idx, frame in enumerate(frames):
        meta = frame.meta
        if meta.width == 0 or meta.height == 0:
            lines.append(
                f"# frame_{meta.index:03d}: width=0 height=0 hot_x={meta.hot_x} "
                "hurt_tight_box=[0, 0, 0, 0] (0x0 empty frame; PNG omitted)"
            )
            continue
        hurt = compute_cel_hurtbox(frame)
        hurt_box = f"[{hurt.tight_x}, {hurt.tight_y}, {hurt.tight_width}, {hurt.tight_height}]"
        lines.append(
            f"# frame_{meta.index:03d}: width={meta.width} height={meta.height} "
            f"hot_x={meta.hot_x} hurt_tight_box={hurt_box}"
        )
        if hit_set is not None and idx < len(hit_set.frames) and hit_set.frames[idx].count > 0:
            hf = hit_set.frames[idx]
            pts = ", ".join(f"({p.x}, {p.y})" for p in hf.points)
            lines.append(
                f"#   collide.hit: hit_type={hf.hit_type} "
                f"hit_max_box=[{hf.max_x}, {hf.max_y}] hit_points=[{pts}]"
            )
    return "\n".join(lines) + "\n"


def _export_hd_pictures(ctx: _TemplateContext, output_dir: Path) -> tuple[int, int]:
    pic_count = 0
    mask_count = 0
    for idx, pic in enumerate(ctx.test_pics):
        _write_output_file(
            output_dir,
            f"pictures/test_{idx}.png",
            encode_rgba_png_bytes(picture_to_rgba(pic)),
        )
        pic_count += 1
        if idx == 8:
            mask = _build_map_mask(pic.pixels, ctx.map_mask_indices)
            if mask is not None:
                _write_output_file(
                    output_dir,
                    "pictures/test_8.mask.png",
                    encode_mask_png_bytes(mask),
                )
                mask_count += 1

    for entry in ctx.disk_set.list_entries():
        norm = entry.norm_name
        if "/" in norm:
            continue
        if norm.endswith(PICTURE_EXTENSIONS) or norm == "mindscape":
            pic = decode_picture(ctx.disk_set.read_file(norm))
            key = normalize_hd_asset_key(norm)
            _write_output_file(
                output_dir,
                f"pictures/{key}.png",
                encode_rgba_png_bytes(picture_to_rgba(pic)),
            )
            pic_count += 1
    return pic_count, mask_count


def _export_hd_arenas(ctx: _TemplateContext, output_dir: Path) -> tuple[int, int]:
    if len(ctx.test_pics) < 8:
        return 0, 0
    arena_count = 0
    mask_count = 0
    for entry in ctx.disk_set.list_entries():
        norm = entry.norm_name
        if "/" in norm or not norm.endswith(".t"):
            continue
        arena = decode_arena(norm, ctx.disk_set.read_file(norm))
        bg_pic = composite_arena_background(arena, ctx.test_pics)
        key = normalize_hd_asset_key(norm)
        _write_output_file(
            output_dir,
            f"arenas/{key}.png",
            encode_rgba_png_bytes(picture_to_rgba(bg_pic)),
        )
        _write_output_file(
            output_dir,
            f"arenas/{key}.mask.png",
            encode_mask_png_bytes(_build_arena_mask(bg_pic.pixels)),
        )
        arena_count += 1
        mask_count += 1
    return arena_count, mask_count


def _export_hd_cel_entry(
    ctx: _TemplateContext,
    entry: OFSFileEntry,
    output_dir: Path,
) -> tuple[int, int, int]:
    key = normalize_hd_asset_key(entry.norm_name)
    cel = decode_cel(ctx.disk_set.read_file(entry.norm_name))
    hit_set = ctx.hit_sets.get(Path(key).stem)
    exported_frames = 0
    skipped_zero = 0
    mask_count = 0

    for frame in cel.frames:
        if frame.meta.width == 0 or frame.meta.height == 0:
            skipped_zero += 1
            continue
        pal = _resolve_cel_palette(ctx, key, frame.meta.index)
        rgba = cel_frame_to_rgba(frame, pal)
        _write_output_file(
            output_dir,
            f"cels/{key}/frame_{frame.meta.index:03d}.png",
            encode_rgba_png_bytes(rgba),
        )
        exported_frames += 1
        mask = _build_cel_mask(key, frame.pixels)
        if mask is not None:
            _write_output_file(
                output_dir,
                f"cels/{key}/frame_{frame.meta.index:03d}.mask.png",
                encode_mask_png_bytes(mask),
            )
            mask_count += 1

    meta_text = _format_cel_meta_toml(key, cel.frames, hit_set)
    _write_output_file(output_dir, f"cels/{key}/meta.toml", meta_text.encode("utf-8"))
    return exported_frames, skipped_zero, mask_count


def _export_hd_cels(ctx: _TemplateContext, output_dir: Path) -> tuple[int, int, int, int, int]:
    cel_count = 0
    frame_count = 0
    mask_count = 0
    meta_count = 0
    skipped_zero = 0
    for entry in ctx.disk_set.list_entries():
        norm = entry.norm_name
        if "/" in norm or not norm.endswith(CEL_EXTENSIONS):
            continue
        frames, skipped, masks = _export_hd_cel_entry(ctx, entry, output_dir)
        cel_count += 1
        frame_count += frames
        skipped_zero += skipped
        mask_count += masks
        meta_count += 1
    return cel_count, frame_count, mask_count, meta_count, skipped_zero


def export_hd_template_from_disk_set(disk_set: DiskSet, output_dir: Path) -> HdTemplateSummary:
    """Export a ready-to-edit 1x HD asset pack template from `disk_set` (ARCHITECTURE.md §14.5)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    ctx = _build_template_context(disk_set)
    _write_output_file(output_dir, "pack.toml", _render_starter_pack_toml().encode("utf-8"))

    pic_count, pic_masks = _export_hd_pictures(ctx, output_dir)
    arena_count, arena_masks = _export_hd_arenas(ctx, output_dir)
    cel_count, cel_frame_count, cel_masks, meta_count, skipped_zero = _export_hd_cels(
        ctx, output_dir
    )

    return HdTemplateSummary(
        pack_root=output_dir,
        picture_count=pic_count,
        arena_count=arena_count,
        cel_count=cel_count,
        cel_frame_count=cel_frame_count,
        mask_count=pic_masks + arena_masks + cel_masks,
        meta_count=meta_count,
        skipped_zero_frames=skipped_zero,
    )


def export_hd_template(adf_dir: Path, output_dir: Path) -> HdTemplateSummary:
    """Discover `.adf` images in `adf_dir` and export a 1x HD template (ARCHITECTURE.md §14.5)."""
    disk_set = DiskSet.from_directory(adf_dir)
    return export_hd_template_from_disk_set(disk_set, output_dir)
