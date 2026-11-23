"""Headless unit tests for the T19.3 HD template exporter (ARCHITECTURE.md §14.5)."""

from __future__ import annotations

import struct
import tomllib
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest
from PIL import Image

from harddaysengine.assets import (
    DiskSet,
    HdColorMode,
    HdFallbackFilter,
    HdPack,
    encode_mask_png_bytes,
    export_hd_template,
    export_hd_template_from_disk_set,
    parse_pack_toml,
)
from harddaysengine.assets.adf import ADF_IMAGE_SIZE
from harddaysengine.assets.cel import decode_cel
from harddaysengine.assets.fight_defs import ActorInit, ArenaDef, CelFile, CelSet, FightDefs
from harddaysengine.assets.fight_palette import FightPaletteTables
from harddaysengine.assets.lzss import LZSSMatch, compress_lzss_literals, encode_lzss_tokens
from harddaysengine.assets.picture import PICTURE_HEIGHT, PICTURE_WIDTH, indexed_to_planar
from harddaysengine.cli.extract import main as extract_cli_main
from harddaysengine.cli.main import main as cli_main
from harddaysengine.sim.map_rules import LairStart, MapRules
from tests.fixtures.synthetic_adf import (
    build_synthetic_arena,
    build_synthetic_cel,
    build_synthetic_hunk_executable,
    build_synthetic_ofs_image,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


def _compress_lzss_rle(data: bytes) -> bytes:
    """Compact synthetic LZSS stream using distance=1 back-references for repeated bytes."""
    tokens: list[int | LZSSMatch] = []
    idx = 0
    total = len(data)
    while idx < total:
        byte = data[idx]
        tokens.append(byte)
        idx += 1
        while idx < total and data[idx] == byte:
            run = 1
            while run < 34 and idx + run < total and data[idx + run] == byte:
                run += 1
            if run >= 3:
                tokens.append(LZSSMatch(count=run, distance=1))
                idx += run
            else:
                break
    return encode_lzss_tokens(tokens)


def _build_compact_picture(
    planes: int = 5,
    fill_index: int = 7,
    raw_palette: Sequence[int] | None = None,
) -> bytes:
    pal_count = 16 if planes == 4 else 32
    pal_words = (
        tuple(raw_palette)
        if raw_palette is not None
        else tuple(0x8000 | ((i * 0x111) & 0x0FFF) for i in range(pal_count))
    )
    pixels = np.full((PICTURE_HEIGHT, PICTURE_WIDTH), fill_index, dtype=np.uint8)
    pixels[0, :16] = np.arange(16, dtype=np.uint8)
    planar = indexed_to_planar(pixels, planes=planes)
    packed = _compress_lzss_rle(planar)
    header = struct.pack(f">HI{pal_count}H", planes, len(packed), *pal_words)
    return header + packed


def _build_indexed_cel(frames: list[tuple[int, int, int, np.ndarray | None]]) -> bytes:
    """Build a synthetic cel from `(width, height, plane_bits, pixels_or_none)` entries."""
    raw_chunks: list[bytes] = []
    dir_entries: list[bytes] = []
    offset = 0
    for width, height, plane_bits, pixels in frames:
        if width == 0 or height == 0 or (plane_bits & 0x1F) == 0:
            chunk = b""
        else:
            assert pixels is not None
            chunk = indexed_to_planar(pixels, planes=5, plane_bits=plane_bits)
        raw_chunks.append(chunk)
        dir_entries.append(struct.pack(">IHHBB", offset, width, height, 0x21, plane_bits))
        offset += len(chunk)

    raw_planes = b"".join(raw_chunks)
    packed = compress_lzss_literals(raw_planes)
    header = struct.pack(">HII", len(frames), len(packed), len(raw_planes) * 8)
    return header + b"".join(dir_entries) + packed


def _build_rich_picture(
    fill_indices: tuple[int, ...], raw_palette: tuple[int, ...] | None = None
) -> bytes:
    pal_words = (
        raw_palette
        if raw_palette is not None
        else tuple(0x8000 | ((i * 0x111) & 0x0FFF) for i in range(32))
    )
    pixels = np.zeros((PICTURE_HEIGHT, PICTURE_WIDTH), dtype=np.uint8)
    for col, val in enumerate(fill_indices):
        pixels[:, col] = val
    planar = indexed_to_planar(pixels, planes=5)
    packed = _compress_lzss_rle(planar)
    header = struct.pack(">HI32H", 5, len(packed), *pal_words)
    return header + packed


def _build_synthetic_hd_disks(adf_dir: Path) -> DiskSet:
    adf_dir.mkdir(parents=True, exist_ok=True)
    kn1_px = np.zeros((8, 16), dtype=np.uint8)
    kn1_px[1:7, 2:6] = 4
    kn1_px[1:7, 6:10] = 6
    kn1_px[2:6, 10:13] = 7
    kn1_px[3:5, 13:15] = 8
    kn1_bytes = _build_indexed_cel(
        [
            (16, 8, 0x1F, kn1_px),
            (0, 0, 0x00, None),
            (8, 8, 0x00, None),
        ]
    )

    he1_px = np.zeros((8, 16), dtype=np.uint8)
    he1_px[1:7, 2:6] = 3
    he1_px[1:7, 6:10] = 9
    he1_px[2:6, 10:13] = 10
    he1_px[3:5, 13:15] = 11
    he1_bytes = _build_indexed_cel([(16, 8, 0x1F, he1_px)])

    test_pics = [_build_compact_picture(planes=5, fill_index=i + 1) for i in range(8)]
    # test_8 includes indices 18, 19, 20, 29 for MapRules mask testing
    test_pics.append(_build_rich_picture((0, 2, 18, 19, 20, 29)))
    test_pack = b"".join(test_pics)

    ch_pal = tuple(0x8000 | (0x0F0 if i == 1 else i * 0x011) for i in range(16))
    ch_bytes = _build_compact_picture(planes=4, fill_index=1, raw_palette=ch_pal)

    disk_a = build_synthetic_ofs_image(
        {
            "program": build_synthetic_hunk_executable(with_mog_s45=False),
            "mog": build_synthetic_hunk_executable(with_mog_s45=True),
            "HighWood.PIV": _build_compact_picture(planes=5, fill_index=20),
            "bold.f": build_synthetic_cel(width=8, height=8),
            " drag.cel": build_synthetic_cel(width=8, height=8),
            "kn1.ob": b"stub",
        }
    )
    disk_b = build_synthetic_ofs_image(
        {
            "KN1.ob": kn1_bytes,
            "HE1.ob": he1_bytes,
            "kn4.ob": build_synthetic_cel(width=16, height=8),
            "mi.c": build_synthetic_cel(width=8, height=8),
            "ch.piv": ch_bytes,
            "collide.hit": b"kn4\n00\n02 01 010 020 035 042\n99\n",
            "test": test_pack,
            "fol1.t": build_synthetic_arena(),
        }
    )
    disk_c = build_synthetic_ofs_image(
        {
            "be1.c": build_synthetic_cel(width=8, height=8),
        }
    )
    (adf_dir / "Disk1.adf").write_bytes(disk_a)
    (adf_dir / "Disk2.adf").write_bytes(disk_b)
    (adf_dir / "Disk3.adf").write_bytes(disk_c)
    return DiskSet.from_directory(adf_dir)


def _snapshot_tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_hd_template_export_is_deterministic_and_round_trips_hd_pack(tmp_path: Path) -> None:
    disks_dir = tmp_path / "disks"
    ds = _build_synthetic_hd_disks(disks_dir)

    out1 = tmp_path / "pack1"
    out2 = tmp_path / "pack2"
    summary1 = export_hd_template_from_disk_set(ds, out1)
    summary2 = export_hd_template(disks_dir, out2)

    assert _snapshot_tree(out1) == _snapshot_tree(out2)
    assert summary1.picture_count == 11  # 9 test_* + highwood.piv + ch.piv
    assert summary1.arena_count == 1
    assert summary1.cel_count == 7  # be1.c, bold.f, drag.cel, he1.ob, kn1.ob, kn4.ob, mi.c
    assert summary1.skipped_zero_frames == 1
    assert summary1.meta_count == 7
    assert summary2.cel_frame_count == summary1.cel_frame_count

    cfg = parse_pack_toml(out1)
    assert cfg.schema_version == 1
    assert cfg.name == "Moonstone HD Template"
    assert cfg.version == "0.1.0"
    assert cfg.author == ""
    assert cfg.scale == 4
    assert cfg.default_mode is HdColorMode.RGBA
    assert cfg.fallback_filter is HdFallbackFilter.NEAREST

    # Canonical extension-retaining keys and skipped 0x0 frame check
    assert (out1 / "pictures" / "test_0.png").is_file()
    assert (out1 / "pictures" / "highwood.piv.png").is_file()
    assert (out1 / "arenas" / "fol1.t.png").is_file()
    assert (out1 / "arenas" / "fol1.t.mask.png").is_file()
    assert (out1 / "cels" / "kn1.ob" / "frame_000.png").is_file()
    assert not (out1 / "cels" / "kn1.ob" / "frame_001.png").exists()
    assert (out1 / "cels" / "kn1.ob" / "frame_002.png").is_file()
    assert (out1 / "cels" / "drag.cel" / "frame_000.png").is_file()

    # Non-0x0 frame with plane_bits == 0 exports as fully transparent 8x8 RGBA PNG
    with Image.open(out1 / "cels" / "kn1.ob" / "frame_002.png") as f2_img:
        assert f2_img.size == (8, 8)
        assert f2_img.mode == "RGBA"
        assert int(np.asarray(f2_img)[:, :, 3].max()) == 0

    # Round-trip validation via HdPack + CLI --validate-hd-pack
    pack = HdPack.load(out1)
    report = pack.validate(disk_set=ds)
    assert report.checked_against_disks is True
    assert report.warnings == ()
    assert report.picture_count == 11
    assert report.arena_count == 1
    assert report.cel_count == 7
    assert report.masked_count > 0

    kn1_entry = pack.resolve_cel_frame("KN1.ob", 0)
    he1_entry = pack.resolve_cel_frame("he1.ob", 0)
    arena_entry = pack.resolve_arena("FOL1.t")
    assert kn1_entry is not None and kn1_entry.mode is HdColorMode.MASKED
    assert he1_entry is not None and he1_entry.mode is HdColorMode.MASKED
    assert arena_entry is not None and arena_entry.mode is HdColorMode.MASKED

    assert (
        cli_main(
            [
                "--adf-dir",
                str(disks_dir),
                "--config",
                str(tmp_path / "missing.toml"),
                "--validate-hd-pack",
                str(out1),
            ]
        )
        == 0
    )


def test_hd_template_masks_and_meta_toml_comments(tmp_path: Path) -> None:
    ds = _build_synthetic_hd_disks(tmp_path / "disks")
    out = tmp_path / "pack"
    export_hd_template_from_disk_set(ds, out)

    # Without valid mog MapRules, test_8.mask.png is omitted cleanly
    assert not (out / "pictures" / "test_8.mask.png").exists()

    with Image.open(out / "cels" / "kn1.ob" / "frame_000.mask.png") as kn_mask_img:
        assert kn_mask_img.mode == "L"
        assert kn_mask_img.size == (16, 8)
        kn_mask = np.asarray(kn_mask_img)
        assert int(kn_mask.max()) <= 31
        assert set(np.unique(kn_mask).tolist()) == {0, 6, 7, 8}

    with Image.open(out / "cels" / "he1.ob" / "frame_000.mask.png") as he_mask_img:
        assert he_mask_img.mode == "L"
        assert he_mask_img.size == (16, 8)
        he_mask = np.asarray(he_mask_img)
        assert int(he_mask.max()) <= 31
        assert set(np.unique(he_mask).tolist()) == {0, 9, 10, 11}

    with Image.open(out / "arenas" / "fol1.t.mask.png") as arena_mask_img:
        assert arena_mask_img.mode == "L"
        assert arena_mask_img.size == (320, 200)
        arena_mask = np.asarray(arena_mask_img)
        assert int(arena_mask.max()) <= 31
        nonzero = set(np.unique(arena_mask).tolist()) - {0}
        assert nonzero
        assert nonzero <= {1, 2, 3, 4, *range(16, 29)}

    # meta.toml has ZERO active keys (no scale=4, no [frames.N] for skipped 0x0)
    for meta_path in (out / "cels").rglob("meta.toml"):
        assert tomllib.loads(meta_path.read_text(encoding="utf-8")) == {}

    kn4_meta = (out / "cels" / "kn4.ob" / "meta.toml").read_text(encoding="utf-8")
    assert "width=16" in kn4_meta
    assert "height=8" in kn4_meta
    assert "hot_x=2" in kn4_meta
    assert "hurt_tight_box=[2, 1, 12, 6]" in kn4_meta
    assert "hit_type=1" in kn4_meta
    assert "hit_max_box=[35, 42]" in kn4_meta
    assert "hit_points=[(10, 20), (35, 42)]" in kn4_meta

    kn1_meta = (out / "cels" / "kn1.ob" / "meta.toml").read_text(encoding="utf-8")
    assert "0x0 empty frame; PNG omitted" in kn1_meta


def test_hd_template_map_rules_mask_and_scene_palettes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ds = _build_synthetic_hd_disks(tmp_path / "disks")

    fake_map_rules = MapRules(
        nodes=(),
        lairs=tuple(LairStart(0, 0, 0, 0, 0, "") for _ in range(24)),
        terrain=b"\x00" * 1000,
        regions=b"\x00" * 1000,
        sizes=((8, 8),),
        seeds=(0, 0, 0, 0),
        defaults=(),
        human_positions=((0, 0),) * 4,
        computer_positions=((0, 0),) * 4,
        human_names=("A", "B", "C", "D"),
        computer_names=("E", "F", "G", "H"),
        may_text="",
        lair_text="",
        battle_text="",
        place_texts=(),
        key_table=b"",
        char_map=b"",
        ramp_index=29,
        ramp_target=0,
        ramp_period=1,
        cycle_start=18,
        cycle_end=20,
        cycle_period=1,
        terrain_tail=b"",
        region_tail=b"",
    )
    monkeypatch.setattr(
        "harddaysengine.assets.extract.parse_map_rules",
        lambda _exe, _sizes: fake_map_rules,
    )

    dummy_actor = ActorInit(
        actor_type=0,
        input_port=0,
        facing=0,
        position=(0, 0, 0),
        frame_set=0,
        idle_script=0,
        after_hit_script=0,
        hurt_scripts=(),
        action_scripts=(),
        walk_scripts=(),
        damage_table=(),
        defence_table=(),
        hp=1,
        hp_max=1,
        reach_x=0,
        too_close_x=0,
        depth_reach=0,
    )
    fake_tables = FightPaletteTables(
        class_rows=(
            (0x011F, 0x011A, 0x0115),
            (0x0F80, 0x0C60, 0x0840),
            (0x00F0, 0x00B0, 0x0070),
            (0x0F00, 0x0B00, 0x0700),
            (0x0EE0, 0x0AA0, 0x0660),
        ),
        scene_rows={0x00: (0x0123,) * 7, 0x30: (0x0456,) * 7},
        trogg_rows=((0x0111,) * 7, (0x0222,) * 7, (0x0333,) * 7),
        dragon_tail=(0x0111, 0x0222, 0x0333),
        low_tweak=(0x0111, 0x0222, 0x0333, 0x0444),
        region_rows={0: (0x0246,) * 13},
        guardian_row=(0x0555,) * 23,
        blood_red=0x0A00,
    )
    fake_defs = FightDefs(
        knight=dummy_actor,
        knight_cels=CelSet(
            kind=None,
            slots=(None, None, None, None, None),
            knight_slots=(CelFile("kn1.ob", False), None, None, None, None),
            effect_slots=(None, None, None, None, None),
            sound_bank=None,
        ),
        duel_cels=CelSet(
            kind=12,
            slots=(CelFile("he1.ob", False), None, None, None, None),
            knight_slots=(None, None, None, None, None),
            effect_slots=(None, None, None, None, None),
            sound_bank=None,
        ),
        arenas=(
            ArenaDef(
                name="flyer",
                handler_offsets=(),
                cels=CelSet(
                    kind=0,
                    slots=(CelFile("be1.c", False), None, None, None, None),
                    knight_slots=(None, None, None, None, None),
                    effect_slots=(None, None, None, None, None),
                    sound_bank=None,
                ),
                scene=0x00,
                region_override=None,
                max_alive=1,
                total=1,
                first_wave=(),
                next_creature=(),
                one_at_a_time=False,
                alive_cap=None,
                cut_row=(),
                knight_pokes=(),
                creature=dummy_actor,
                objects=(),
                moon_variants=(),
            ),
        ),
        arena_by_handler={},
        duel_handler_offsets=(),
        frame_set_lists=(),
        lairs=(),
    )
    monkeypatch.setattr(
        "harddaysengine.assets.extract.parse_fight_palette_tables",
        lambda _exe: fake_tables,
    )
    monkeypatch.setattr(
        "harddaysengine.assets.extract.parse_fight_defs",
        lambda _exe: fake_defs,
    )

    out = tmp_path / "pack_mog"
    export_hd_template_from_disk_set(ds, out)

    with Image.open(out / "pictures" / "test_8.mask.png") as map_mask_img:
        assert map_mask_img.mode == "L"
        assert map_mask_img.size == (320, 200)
        assert set(np.unique(np.asarray(map_mask_img)).tolist()) == {0, 18, 19, 20, 29}

    # Verify kn1.ob uses class_rows[0] (0x011F -> (17, 17, 255)) at index 6
    with Image.open(out / "cels" / "kn1.ob" / "frame_000.png") as kn_img:
        kn_rgba = np.asarray(kn_img)
        kn_px = decode_cel(ds.read_file("kn1.ob")).frames[0].pixels
        ys, xs = np.nonzero(kn_px == 6)
        assert tuple(int(c) for c in kn_rgba[ys[0], xs[0]]) == (17, 17, 255, 255)

    # Verify he1.ob uses class_rows[4] (0x0EE0 -> (238, 238, 0)) at index 9
    with Image.open(out / "cels" / "he1.ob" / "frame_000.png") as he_img:
        he_rgba = np.asarray(he_img)
        he_px = decode_cel(ds.read_file("he1.ob")).frames[0].pixels
        ys, xs = np.nonzero(he_px == 9)
        assert tuple(int(c) for c in he_rgba[ys[0], xs[0]]) == (238, 238, 0, 255)

    # Verify bold.f uses ch.piv palette (index 5 -> 5 * 0x011 = 0x055 -> (0, 85, 85))
    with Image.open(out / "cels" / "bold.f" / "frame_000.png") as bold_img:
        bold_rgba = np.asarray(bold_img)
        bold_px = decode_cel(ds.read_file("bold.f")).frames[0].pixels
        ys, xs = np.nonzero(bold_px == 5)
        assert ys.size > 0
        assert tuple(int(c) for c in bold_rgba[ys[0], xs[0]]) == (0, 85, 85, 255)


def test_encode_mask_png_bytes_validation() -> None:
    invalid_shapes_and_empty: list[np.ndarray] = [
        np.zeros((4,), dtype=np.uint8),
        np.zeros((1, 2, 2), dtype=np.uint8),
        np.zeros((0, 4), dtype=np.uint8),
    ]
    for bad_shape in invalid_shapes_and_empty:
        with pytest.raises(ValueError, match="non-empty 2D"):
            encode_mask_png_bytes(bad_shape)

    with pytest.raises(ValueError, match="exceeds Amiga 5-bit maximum"):
        encode_mask_png_bytes(np.full((4, 4), 32, dtype=np.uint8))

    invalid_dtypes: list[Any] = [
        np.array([[256, 0]], dtype=np.uint16),
        np.array([[257, 1]], dtype=np.int32),
        np.array([[-256, 0]], dtype=np.int16),
        np.array([[1, 2]], dtype=np.int32),
        np.array([[1.5, 2.5]], dtype=np.float32),
        np.array([[True, False]], dtype=bool),
    ]
    for bad_dtype in invalid_dtypes:
        with pytest.raises(ValueError, match="dtype uint8"):
            encode_mask_png_bytes(bad_dtype)

    valid_mask = np.array([[0, 6, 31], [16, 28, 1]], dtype=np.uint8)
    png_bytes = encode_mask_png_bytes(valid_mask)
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"


def test_extract_cli_hd_template_and_legacy_behavior(tmp_path: Path) -> None:
    disks_dir = tmp_path / "disks"
    _build_synthetic_hd_disks(disks_dir)

    out_alias = tmp_path / "cli_alias"
    rc_alias = extract_cli_main(
        ["--adf-dir", str(disks_dir), "--hd-template", "--output", str(out_alias)]
    )
    assert rc_alias == 0
    assert (out_alias / "pack.toml").is_file()
    assert not (out_alias / "manifest.json").exists()

    out_dir = tmp_path / "cli_dir"
    rc_dir = extract_cli_main(
        ["--adf-dir", str(disks_dir), "--hd-template", "--output-dir", str(out_dir)]
    )
    assert rc_dir == 0
    assert _snapshot_tree(out_alias) == _snapshot_tree(out_dir)

    # Reject contradictory --hd-template --no-cel-pngs
    with pytest.raises(SystemExit) as exc:
        extract_cli_main(
            [
                "--adf-dir",
                str(disks_dir),
                "--hd-template",
                "--no-cel-pngs",
                "--output",
                str(tmp_path / "bad"),
            ]
        )
    assert exc.value.code == 2

    # Missing disks returns exit code 1
    assert extract_cli_main(["--adf-dir", str(tmp_path / "empty"), "--hd-template"]) == 1

    # Corrupt .adf returns exit code 1
    bad_adf_dir = tmp_path / "bad_adf"
    bad_adf_dir.mkdir()
    (bad_adf_dir / "bad.adf").write_bytes(b"\x00" * ADF_IMAGE_SIZE)
    assert (
        extract_cli_main(
            ["--adf-dir", str(bad_adf_dir), "--hd-template", "--output", str(tmp_path / "bad_out")]
        )
        == 1
    )

    # Legacy behavior preserved
    legacy_out = tmp_path / "legacy"
    assert extract_cli_main(["--adf-dir", str(disks_dir), "--output-dir", str(legacy_out)]) == 0
    assert (legacy_out / "manifest.json").is_file()
    assert not (legacy_out / "pack.toml").exists()
