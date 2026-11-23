"""Headless unit tests for external HD asset pack loader and validator (T19.2, §14.2-4)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pytest
from PIL import Image

from harddaysengine.assets.adf import DiskSet
from harddaysengine.assets.hd_pack import HdPack
from harddaysengine.assets.hd_pack_schema import (
    HdAssetKind,
    HdColorMode,
    HdFallbackFilter,
    HdPackError,
    normalize_hd_asset_key,
    normalize_variant,
    parse_pack_toml,
)
from harddaysengine.cli.main import main as cli_main
from tests.fixtures.synthetic_adf import (
    build_synthetic_arena,
    build_synthetic_cel,
    build_synthetic_hunk_executable,
    build_synthetic_ofs_image,
    build_synthetic_picture,
)

if TYPE_CHECKING:
    from pathlib import Path


def _write_pack_toml(pack_root: Path, body: str | None = None) -> None:
    pack_root.mkdir(parents=True, exist_ok=True)
    text = (
        body
        if body is not None
        else (
            "schema_version = 1\n"
            'name = "Synthetic HD Pack"\n'
            'version = "1.2.3"\n'
            'author = "Test Artist"\n'
            "scale = 4\n"
            'default_mode = "rgba"\n'
            'fallback_filter = "nearest"\n'
        )
    )
    (pack_root / "pack.toml").write_text(text, encoding="utf-8")


def _write_rgba_png(path: Path, width: int, height: int, color: tuple[int, int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.zeros((height, width, 4), dtype=np.uint8)
    arr[:, :] = color
    Image.fromarray(arr, mode="RGBA").save(path, format="PNG")


def _write_indexed_png(path: Path, width: int, height: int, fill: int, *, mode: str = "L") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.full((height, width), fill, dtype=np.uint8)
    img = Image.fromarray(arr, mode="L")
    if mode == "P":
        p_img = Image.new("P", (width, height))
        p_img.putpalette([c for i in range(256) for c in (i, i, i)])
        p_img.putdata(arr.flatten().tolist())
        p_img.save(path, format="PNG")
        return
    img.save(path, format="PNG")


def _build_synthetic_disk_dir(adf_dir: Path) -> DiskSet:
    adf_dir.mkdir(parents=True, exist_ok=True)
    disk_a = build_synthetic_ofs_image(
        {
            "program": build_synthetic_hunk_executable(),
            "mog": build_synthetic_hunk_executable(with_mog_s45=True),
            "HighWood.PIV": build_synthetic_picture(planes=5),
        }
    )
    disk_b = build_synthetic_ofs_image(
        {
            "HE1.ob": build_synthetic_cel(width=16, height=8),
            "KN1.ob": build_synthetic_cel(width=16, height=8),
            "fol1.t": build_synthetic_arena(),
            "test": build_synthetic_picture(planes=5) + build_synthetic_picture(planes=5),
        }
    )
    disk_c = build_synthetic_ofs_image(
        {
            "be1.c": build_synthetic_cel(width=16, height=8),
        }
    )
    (adf_dir / "Disk1.adf").write_bytes(disk_a)
    (adf_dir / "Disk2.adf").write_bytes(disk_b)
    (adf_dir / "Disk3.adf").write_bytes(disk_c)
    return DiskSet.from_directory(adf_dir)


def test_pack_toml_defaults_and_full_fields(tmp_path: Path) -> None:
    minimal = tmp_path / "minimal"
    _write_pack_toml(minimal, 'schema_version = 1\nname = "Minimal"\nscale = 2\n')
    cfg = parse_pack_toml(minimal)
    assert cfg.schema_version == 1
    assert cfg.name == "Minimal"
    assert cfg.version == "0.0.0"
    assert cfg.author == ""
    assert cfg.scale == 2
    assert cfg.default_mode is HdColorMode.RGBA
    assert cfg.fallback_filter is HdFallbackFilter.NEAREST

    full = tmp_path / "full"
    _write_pack_toml(
        full,
        (
            'schema_version = 1\nname = "Full"\nversion = "2.0"\nauthor = "Jane"\n'
            'scale = 6\ndefault_mode = "indexed"\nfallback_filter = "smooth"\n'
        ),
    )
    full_cfg = parse_pack_toml(full)
    assert full_cfg.scale == 6
    assert full_cfg.default_mode is HdColorMode.INDEXED
    assert full_cfg.fallback_filter is HdFallbackFilter.SMOOTH


@pytest.mark.parametrize(
    "toml_text",
    [
        'name = "NoSchema"\nscale = 4\n',
        'schema_version = 2\nname = "BadSchema"\nscale = 4\n',
        'schema_version = 1\nname = "   "\nscale = 4\n',
        'schema_version = 1\nname = "BadScale"\nscale = 1\n',
        'schema_version = 1\nname = "BadScale"\nscale = 9\n',
        'schema_version = 1\nname = "BoolScale"\nscale = true\n',
        'schema_version = 1\nname = "Unknown"\nscale = 4\nextra = 123\n',
        'schema_version = 1\nname = "BadMode"\nscale = 4\ndefault_mode = "rgb"\n',
        'schema_version = 1\nname = "BadFallback"\nscale = 4\nfallback_filter = "bilinear"\n',
        "not valid toml ====",
    ],
)
def test_pack_toml_rejects_malformed_configs(tmp_path: Path, toml_text: str) -> None:
    _write_pack_toml(tmp_path, toml_text)
    with pytest.raises(HdPackError):
        parse_pack_toml(tmp_path)


def test_key_and_variant_normalization() -> None:
    assert normalize_hd_asset_key(" HighWood.PIV ") == "highwood.piv"
    assert normalize_hd_asset_key(" drag.cel") == "drag.cel"
    assert normalize_variant("Swamp_1") == "swamp_1"
    for bad_key in ("", "   ", "../evil.png", "a/b.png", "a\\b.png", "bad..key"):
        with pytest.raises(HdPackError):
            normalize_hd_asset_key(bad_key)
    for bad_variant in ("", "red blue", "bad.var", "a/b"):
        with pytest.raises(HdPackError):
            normalize_variant(bad_variant)


def test_all_three_color_modes_and_auto_mask_promotion(tmp_path: Path) -> None:
    _write_pack_toml(tmp_path)
    # 1. RGBA picture (2x)
    _write_rgba_png(tmp_path / "pictures" / "highwood.piv.png", 640, 400, (10, 20, 30, 255))
    # 2. Auto-promoted MASKED arena (2x) via sibling .mask.png
    _write_rgba_png(tmp_path / "arenas" / "fol1.t.png", 640, 400, (40, 50, 60, 255))
    _write_indexed_png(tmp_path / "arenas" / "fol1.t.mask.png", 640, 400, 18, mode="L")
    # 3. Explicit INDEXED cel (2x, mode="P") + explicit RGBA frame overriding sibling mask
    cel_dir = tmp_path / "cels" / "kn1.ob"
    _write_indexed_png(cel_dir / "frame_000.png", 32, 16, 7, mode="P")
    _write_rgba_png(cel_dir / "frame_001.png", 32, 16, (200, 10, 10, 255))
    _write_indexed_png(cel_dir / "frame_001.mask.png", 32, 16, 6, mode="L")
    (cel_dir / "meta.toml").write_text(
        'mode = "indexed"\n[frames.1]\nmode = "rgba"\n',
        encoding="utf-8",
    )

    pack = HdPack.load(tmp_path)
    pic_entry = pack.resolve_picture("HighWood.PIV")
    assert pic_entry is not None
    assert pic_entry.mode is HdColorMode.RGBA
    pic_img = pack.load_image(pic_entry, orig_width=320, orig_height=200)
    assert pic_img.scale == 2
    assert pic_img.rgba is not None and pic_img.rgba.shape == (400, 640, 4)
    assert pic_img.indexed is None and pic_img.mask is None

    arena_entry = pack.resolve_arena("FOL1.T")
    assert arena_entry is not None
    assert arena_entry.mode is HdColorMode.MASKED
    arena_img = pack.load_image(arena_entry, orig_width=320, orig_height=200)
    assert arena_img.rgba is not None
    assert arena_img.mask is not None and int(arena_img.mask[0, 0]) == 18

    f0_entry = pack.resolve_cel_frame("KN1.ob", 0)
    assert f0_entry is not None and f0_entry.mode is HdColorMode.INDEXED
    f0_img = pack.load_image(f0_entry, orig_width=16, orig_height=8)
    assert f0_img.indexed is not None and int(f0_img.indexed[0, 0]) == 7
    assert f0_img.rgba is None and f0_img.mask is None

    f1_entry = pack.resolve_cel_frame("kn1.ob", 1)
    assert f1_entry is not None and f1_entry.mode is HdColorMode.RGBA
    f1_img = pack.load_image(f1_entry, orig_width=16, orig_height=8)
    assert f1_img.rgba is not None and f1_img.mask is None


def test_variant_lookup_precedence_and_fallback(tmp_path: Path) -> None:
    _write_pack_toml(tmp_path)
    _write_rgba_png(tmp_path / "arenas" / "fol1.t.png", 640, 400, (1, 2, 3, 255))
    _write_rgba_png(tmp_path / "arenas" / "fol1.t__swamp.png", 640, 400, (4, 5, 6, 255))
    _write_rgba_png(tmp_path / "cels" / "kn1.ob" / "frame_000.png", 32, 16, (10, 10, 10, 255))
    _write_rgba_png(tmp_path / "cels" / "kn1.ob__red" / "frame_000.png", 32, 16, (255, 0, 0, 255))

    pack = HdPack.load(tmp_path)
    swamp = pack.resolve_arena("fol1.t", variant="swamp")
    glen = pack.resolve_arena("fol1.t", variant="glen")
    assert swamp is not None and swamp.variant == "swamp"
    assert glen is not None and glen.variant is None
    assert pack.resolve_arena("missing.t") is None

    red = pack.resolve_cel_frame("KN1.ob", 0, variant="red")
    blue = pack.resolve_cel_frame("KN1.ob", 0, variant="blue")
    assert red is not None and red.variant == "red"
    assert blue is not None and blue.variant is None
    assert pack.resolve_cel_frame("KN1.ob", 5, variant="red") is None


def test_cycle_frames_sequence_and_validation(tmp_path: Path) -> None:
    _write_pack_toml(tmp_path)
    pics = tmp_path / "pictures"
    _write_rgba_png(pics / "test_1.png", 640, 400, (0, 0, 10, 255))
    _write_rgba_png(pics / "test_1__cycle_00.png", 640, 400, (0, 0, 100, 255))
    _write_rgba_png(pics / "test_1__cycle_01.png", 640, 400, (0, 0, 200, 255))
    (pics / "test_1.meta.toml").write_text(
        'cycle_frames = ["test_1__cycle_00.png", "test_1__cycle_01.png"]\n',
        encoding="utf-8",
    )

    pack = HdPack.load(tmp_path)
    # Auxiliary cycle frames must not appear as standalone variant overrides.
    assert len(pack.entries) == 1
    entry = pack.resolve_picture("test_1")
    assert entry is not None and len(entry.cycle_frame_paths) == 2

    img0 = pack.load_cycle_frame(entry, 0)
    img1 = pack.load_cycle_frame(entry, 1)
    img2 = pack.load_cycle_frame(entry, 2)
    assert img0.rgba is not None and int(img0.rgba[0, 0, 2]) == 100
    assert img1.rgba is not None and int(img1.rgba[0, 0, 2]) == 200
    assert img2.rgba is not None and int(img2.rgba[0, 0, 2]) == 100

    report = pack.validate()
    assert report.picture_count == 1
    assert report.cycle_frame_count == 2


def test_mixed_scales_padding_and_anchor_offset(tmp_path: Path) -> None:
    _write_pack_toml(tmp_path)
    cel_dir = tmp_path / "cels" / "kn1.ob"
    # Original 16x8 at scale 3 = 48x24, plus padding [4, 2, 6, 8] => 58x34.
    _write_rgba_png(cel_dir / "frame_000.png", 58, 34, (120, 130, 140, 255))
    (cel_dir / "meta.toml").write_text(
        "[frames.0]\nscale = 3\npadding = [4, 2, 6, 8]\nanchor_offset = [-2, 1]\n",
        encoding="utf-8",
    )

    pack = HdPack.load(tmp_path)
    entry = pack.resolve_cel_frame("kn1.ob", 0)
    assert entry is not None
    img = pack.load_image(entry, orig_width=16, orig_height=8)
    assert img.scale == 3
    assert img.width == 58 and img.height == 34
    assert img.padding == (4, 2, 6, 8)
    assert img.anchor_offset == (-2, 1)


@pytest.mark.parametrize(
    ("png_w", "png_h", "meta_text"),
    [
        (500, 400, ""),  # non-integer width multiple of 320
        (640, 600, ""),  # non-uniform 2x width vs 3x height
        (640, 400, "scale = 4\n"),  # declared scale mismatch
        (640, 400, "padding = [320, 0, 320, 0]\n"),  # padding eats whole image
    ],
)
def test_dimension_and_scale_errors(tmp_path: Path, png_w: int, png_h: int, meta_text: str) -> None:
    _write_pack_toml(tmp_path)
    pics = tmp_path / "pictures"
    _write_rgba_png(pics / "highwood.piv.png", png_w, png_h, (1, 1, 1, 255))
    if meta_text:
        (pics / "highwood.piv.meta.toml").write_text(meta_text, encoding="utf-8")
    pack = HdPack.load(tmp_path)
    with pytest.raises(HdPackError):
        pack.validate()


def test_indexed_and_mask_pixel_and_mode_validation_errors(tmp_path: Path) -> None:
    # Indexed PNG with value > 31 fails.
    p1 = tmp_path / "bad_idx_val"
    _write_pack_toml(
        p1, 'schema_version = 1\nname = "BadIdx"\nscale = 2\ndefault_mode = "indexed"\n'
    )
    _write_indexed_png(p1 / "pictures" / "highwood.piv.png", 640, 400, 32, mode="L")
    with pytest.raises(HdPackError, match="exceeds Amiga 5-bit maximum"):
        HdPack.load(p1).validate()

    # Indexed mode given an RGBA PNG fails.
    p2 = tmp_path / "bad_idx_mode"
    _write_pack_toml(
        p2, 'schema_version = 1\nname = "BadMode"\nscale = 2\ndefault_mode = "indexed"\n'
    )
    _write_rgba_png(p2 / "pictures" / "highwood.piv.png", 640, 400, (1, 2, 3, 255))
    with pytest.raises(HdPackError, match="must be 8-bit"):
        HdPack.load(p2).validate()

    # Mask dimension mismatch fails.
    p3 = tmp_path / "bad_mask_dim"
    _write_pack_toml(p3)
    _write_rgba_png(p3 / "pictures" / "highwood.piv.png", 640, 400, (1, 2, 3, 255))
    _write_indexed_png(p3 / "pictures" / "highwood.piv.mask.png", 320, 200, 6, mode="L")
    with pytest.raises(HdPackError, match="do not match diffuse"):
        HdPack.load(p3).validate()

    # Mask value > 31 fails.
    p4 = tmp_path / "bad_mask_val"
    _write_pack_toml(p4)
    _write_rgba_png(p4 / "pictures" / "highwood.piv.png", 640, 400, (1, 2, 3, 255))
    _write_indexed_png(p4 / "pictures" / "highwood.piv.mask.png", 640, 400, 40, mode="L")
    with pytest.raises(HdPackError, match="exceeds Amiga 5-bit maximum"):
        HdPack.load(p4).validate()

    # Explicit masked mode without .mask.png fails at load time.
    p5 = tmp_path / "missing_mask"
    _write_pack_toml(p5)
    _write_rgba_png(p5 / "pictures" / "highwood.piv.png", 640, 400, (1, 2, 3, 255))
    (p5 / "pictures" / "highwood.piv.meta.toml").write_text('mode = "masked"\n', encoding="utf-8")
    with pytest.raises(HdPackError, match=r"requires a matching \.mask\.png"):
        HdPack.load(p5)


def test_orphan_files_and_path_traversal_rejected(tmp_path: Path) -> None:
    # Orphan mask file
    p1 = tmp_path / "orphan_mask"
    _write_pack_toml(p1)
    _write_indexed_png(p1 / "pictures" / "highwood.piv.mask.png", 640, 400, 6)
    with pytest.raises(HdPackError, match="mask without matching PNG"):
        HdPack.load(p1)

    # Bad cel filename
    p2 = tmp_path / "bad_cel_name"
    _write_pack_toml(p2)
    _write_rgba_png(p2 / "cels" / "kn1.ob" / "000.png", 32, 16, (1, 2, 3, 255))
    with pytest.raises(HdPackError, match="unsupported cel file"):
        HdPack.load(p2)

    # Path traversal in cycle_frames
    p3 = tmp_path / "cycle_escape"
    _write_pack_toml(p3)
    _write_rgba_png(p3 / "pictures" / "test_1.png", 640, 400, (1, 2, 3, 255))
    (p3 / "pictures" / "test_1.meta.toml").write_text(
        'cycle_frames = ["../evil.png"]\n', encoding="utf-8"
    )
    with pytest.raises(HdPackError, match="sibling filename"):
        HdPack.load(p3)

    # Meta referencing missing cel frame
    p4 = tmp_path / "orphan_meta_frame"
    _write_pack_toml(p4)
    _write_rgba_png(p4 / "cels" / "kn1.ob" / "frame_000.png", 32, 16, (1, 2, 3, 255))
    (p4 / "cels" / "kn1.ob" / "meta.toml").write_text("[frames.9]\nscale = 2\n", encoding="utf-8")
    with pytest.raises(HdPackError, match="missing frame 9"):
        HdPack.load(p4)


def test_disk_set_cross_check_warnings_and_out_of_range_frame(tmp_path: Path) -> None:
    disk_set = _build_synthetic_disk_dir(tmp_path / "disks")
    pack_dir = tmp_path / "pack"
    _write_pack_toml(pack_dir)
    _write_rgba_png(pack_dir / "pictures" / "highwood.piv.png", 640, 400, (1, 2, 3, 255))
    _write_rgba_png(pack_dir / "pictures" / "unknown_pic.piv.png", 640, 400, (1, 2, 3, 255))
    _write_rgba_png(pack_dir / "arenas" / "fol1.t.png", 640, 400, (1, 2, 3, 255))
    _write_rgba_png(pack_dir / "cels" / "kn1.ob" / "frame_000.png", 32, 16, (1, 2, 3, 255))

    report = HdPack.load(pack_dir).validate(disk_set=disk_set)
    assert report.checked_against_disks is True
    assert report.picture_count == 2
    assert report.arena_count == 1
    assert report.cel_count == 1
    assert report.cel_frame_count == 1
    assert report.warnings == ("unrecognized picture asset key: unknown_pic.piv",)

    # Out-of-range frame index against real/synthetic cel fails validation.
    _write_rgba_png(pack_dir / "cels" / "kn1.ob" / "frame_005.png", 32, 16, (1, 2, 3, 255))
    with pytest.raises(HdPackError, match="out of range"):
        HdPack.load(pack_dir).validate(disk_set=disk_set)


def test_cli_validate_hd_pack_exit_codes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    adf_dir = tmp_path / "disks"
    _build_synthetic_disk_dir(adf_dir)
    pack_dir = tmp_path / "valid_pack"
    _write_pack_toml(pack_dir)
    _write_rgba_png(pack_dir / "pictures" / "highwood.piv.png", 640, 400, (1, 2, 3, 255))
    _write_rgba_png(pack_dir / "cels" / "kn1.ob" / "frame_000.png", 32, 16, (1, 2, 3, 255))

    rc = cli_main(
        [
            "--adf-dir",
            str(adf_dir),
            "--config",
            str(tmp_path / "missing.toml"),
            "--validate-hd-pack",
            str(pack_dir),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "HD pack valid: Synthetic HD Pack" in out
    assert "disks_checked=yes" in out

    # Disk-free validation succeeds cleanly too.
    rc_nodisk = cli_main(
        [
            "--adf-dir",
            str(tmp_path / "empty_disks"),
            "--config",
            str(tmp_path / "missing.toml"),
            "--validate-hd-pack",
            str(pack_dir),
        ]
    )
    assert rc_nodisk == 0
    assert "disks_checked=no" in capsys.readouterr().out

    # Invalid pack returns exit code 1 with error on stderr.
    bad_dir = tmp_path / "bad_pack"
    _write_pack_toml(bad_dir)
    _write_rgba_png(bad_dir / "pictures" / "highwood.piv.png", 333, 200, (1, 2, 3, 255))
    rc_bad = cli_main(
        [
            "--adf-dir",
            str(tmp_path / "empty_disks"),
            "--config",
            str(tmp_path / "missing.toml"),
            "--validate-hd-pack",
            str(bad_dir),
        ]
    )
    assert rc_bad == 1
    assert "HD pack validation failed:" in capsys.readouterr().err


def test_hd_pack_asset_kind_enum_values() -> None:
    assert HdAssetKind.PICTURE.value == "picture"
    assert HdAssetKind.ARENA.value == "arena"
    assert HdAssetKind.CEL.value == "cel"
