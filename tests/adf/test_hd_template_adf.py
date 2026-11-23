"""Real-disk verification of the T19.3 HD template exporter (`@pytest.mark.adf`, §14.5)."""

from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING

import numpy as np
import pytest
from PIL import Image

from harddaysengine.assets import (
    DiskSet,
    HdColorMode,
    HdPack,
    cel_frame_to_rgba,
    decode_cel,
    decode_picture,
    decode_picture_pack,
    parse_hunk_executable,
)
from harddaysengine.assets.fight_defs import parse_fight_defs
from harddaysengine.assets.fight_palette import (
    REGION_GLEN,
    REGION_SWAMP,
    SCENE_DUEL,
    SCENE_FLYER,
    SCENE_MUDMEN,
    assemble_fight_palette,
    parse_fight_palette_tables,
)
from harddaysengine.assets.intro_data import parse_menu_data
from harddaysengine.assets.palette import cell_word_to_rgb, decode_palette_word
from harddaysengine.cli.extract import main as extract_cli_main

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.adf


def test_real_disk_hd_template_round_trip_and_masks(adf_dir: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "hd_template"
    rc = extract_cli_main(["--adf-dir", str(adf_dir), "--hd-template", "--output", str(out_dir)])
    assert rc == 0

    ds = DiskSet.from_directory(adf_dir)
    pack = HdPack.load(out_dir)
    report = pack.validate(disk_set=ds)
    assert report.checked_against_disks is True
    assert report.warnings == ()
    assert report.picture_count == 32
    assert report.arena_count == 56
    assert report.cel_count == 50

    for mask_rel in (
        "cels/kn1.ob/frame_000.mask.png",
        "cels/he1.ob/frame_000.mask.png",
        "arenas/fol1.t.mask.png",
        "pictures/test_8.mask.png",
    ):
        mask_path = out_dir / mask_rel
        assert mask_path.is_file()
        with Image.open(mask_path) as img:
            assert img.mode == "L"
            arr = np.asarray(img)
            assert int(arr.max()) <= 31
            assert int((arr > 0).sum()) > 0

    kn1_entry = pack.resolve_cel_frame("kn1.ob", 0)
    he1_entry = pack.resolve_cel_frame("he1.ob", 0)
    fol1_entry = pack.resolve_arena("fol1.t")
    test8_entry = pack.resolve_picture("test_8")
    assert kn1_entry is not None and kn1_entry.mode is HdColorMode.MASKED
    assert he1_entry is not None and he1_entry.mode is HdColorMode.MASKED
    assert fol1_entry is not None and fol1_entry.mode is HdColorMode.MASKED
    assert test8_entry is not None and test8_entry.mode is HdColorMode.MASKED

    kn4_meta_text = (out_dir / "cels" / "kn4.ob" / "meta.toml").read_text(encoding="utf-8")
    assert tomllib.loads(kn4_meta_text) == {}
    assert "hurt_tight_box=" in kn4_meta_text
    assert "collide.hit:" in kn4_meta_text
    assert "hit_points=" in kn4_meta_text


def test_real_disk_hd_template_uses_authentic_mog_scene_palettes(
    adf_dir: Path, tmp_path: Path
) -> None:
    out_dir = tmp_path / "hd_template_pals"
    assert (
        extract_cli_main(["--adf-dir", str(adf_dir), "--hd-template", "--output", str(out_dir)])
        == 0
    )

    ds = DiskSet.from_directory(adf_dir)
    mog_exec = parse_hunk_executable("mog", ds.read_file("mog"))
    tables = parse_fight_palette_tables(mog_exec)
    defs = parse_fight_defs(mog_exec)
    test_pics = decode_picture_pack(ds.read_file("test"))

    # 1. kn1.ob uses assembled Blue Knight fight palette (indices 6..8 != test_0[6..8])
    kn1_pal = tuple(
        cell_word_to_rgb(w)
        for w in assemble_fight_palette(
            tuple(decode_palette_word(w) for w in test_pics[0].raw_palette),
            tables,
            scene=SCENE_FLYER,
            region=REGION_GLEN,
            first_kind=0,
        )
    )
    kn1_px = decode_cel(ds.read_file("kn1.ob")).frames[0].pixels
    ys, xs = np.nonzero(kn1_px == 6)
    with Image.open(out_dir / "cels" / "kn1.ob" / "frame_000.png") as kn1_img:
        assert tuple(int(c) for c in np.asarray(kn1_img)[ys[0], xs[0]][:3]) == kn1_pal[6]

    # 2. he1.ob uses SCENE_DUEL Black Knight class palette at indices 9..11
    second_kind = defs.duel_cels.kind if defs.duel_cels.kind is not None else 4
    he1_pal = tuple(
        cell_word_to_rgb(w)
        for w in assemble_fight_palette(
            tuple(decode_palette_word(w) for w in test_pics[0].raw_palette),
            tables,
            scene=SCENE_DUEL,
            region=REGION_GLEN,
            first_kind=0,
            second_kind=second_kind,
        )
    )
    he1_px = decode_cel(ds.read_file("he1.ob")).frames[0].pixels
    ys_h, xs_h = np.nonzero(he1_px == 9)
    with Image.open(out_dir / "cels" / "he1.ob" / "frame_000.png") as he1_img:
        assert tuple(int(c) for c in np.asarray(he1_img)[ys_h[0], xs_h[0]][:3]) == he1_pal[9]

    # 3. mudmen1.cel uses Swamp Mudmen fight palette (test_pics[2], REGION_SWAMP)
    mud_pal = tuple(
        cell_word_to_rgb(w)
        for w in assemble_fight_palette(
            tuple(decode_palette_word(w) for w in test_pics[2].raw_palette),
            tables,
            scene=SCENE_MUDMEN,
            region=REGION_SWAMP,
            first_kind=0,
        )
    )
    mud_frame = decode_cel(ds.read_file("mudmen1.cel")).frames[0]
    with Image.open(out_dir / "cels" / "mudmen1.cel" / "frame_000.png") as mud_img:
        mud_rgba = np.asarray(mud_img)
        assert np.array_equal(mud_rgba, cel_frame_to_rgba(mud_frame, mud_pal))
        assert not np.array_equal(mud_rgba, cel_frame_to_rgba(mud_frame, test_pics[0].palette))

    # 4. sel.cel frame 0 uses ch.piv while frame 2 uses knight_select.palette
    menu = parse_menu_data(mog_exec)
    ch_pal = decode_picture(ds.read_file("ch.piv")).palette
    ks_pal = tuple(cell_word_to_rgb(w) for w in menu.knight_select.palette)
    sel_cel = decode_cel(ds.read_file("sel.cel"))
    with Image.open(out_dir / "cels" / "sel.cel" / "frame_000.png") as s0_img:
        assert np.array_equal(np.asarray(s0_img), cel_frame_to_rgba(sel_cel.frames[0], ch_pal))
    with Image.open(out_dir / "cels" / "sel.cel" / "frame_002.png") as s2_img:
        assert np.array_equal(np.asarray(s2_img), cel_frame_to_rgba(sel_cel.frames[2], ks_pal))
