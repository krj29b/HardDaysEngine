"""Verification of Moonstone asset decoders against the user's real Amiga ADF disks (T3.7)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harddaysengine.app.game import GameScene, GameSession
from harddaysengine.app.input import InputFrame
from harddaysengine.app.scene_assets import GameAssetBundle, build_intro_cinematic_frame
from harddaysengine.assets import (
    DiskSet,
    composite_arena_background,
    decode_arena,
    decode_cel,
    decode_cmp_module,
    decode_picture,
    decode_picture_pack,
    decode_sample_bank,
    decode_stile,
    extract_all_assets,
    parse_collide_hit,
    parse_hunk_executable,
    parse_mog_synth_instruments,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.adf


def test_real_disk_set_catalog_and_duplicate_resolution(adf_dir: Path) -> None:
    ds = DiskSet.from_directory(adf_dir)
    assert set(ds.disks.keys()) == {"A", "B", "C"}
    assert len(ds.disks["A"].list_entries()) == 33  # 32 root + s/startup-sequence
    assert len(ds.disks["B"].list_entries()) == 88
    assert len(ds.disks["C"].list_entries()) == 91

    # Duplicate resolution: Disk B's 20,760-byte KN1.ob beats Disk A's 5-byte stub
    kn1 = ds.get_entry("kn1.ob")
    assert kn1.disk_id == "B"
    assert kn1.size == 20_760


def test_real_pictures_and_test_pack(adf_dir: Path) -> None:
    ds = DiskSet.from_directory(adf_dir)
    bg1a = decode_picture(ds.read_file("bg1a.PIV"))
    assert bg1a.width == 320
    assert bg1a.height == 200
    assert bg1a.planes in (4, 5)

    test_pics = decode_picture_pack(ds.read_file("test"))
    assert len(test_pics) == 9
    for pic in test_pics:
        assert pic.width == 320
        assert pic.height == 200
        assert pic.planes == 5


def test_real_cels_and_collide_hit(adf_dir: Path) -> None:
    ds = DiskSet.from_directory(adf_dir)
    hit_sets = parse_collide_hit(ds.read_file("collide.hit"))
    assert len(hit_sets) == 14
    for expected in ("kn4", "troll1", "dragon1", "balok1", "ratmen1", "mudmen1"):
        assert expected in hit_sets

    kn4_cel = decode_cel(ds.read_file("KN4.ob"))
    assert kn4_cel.frame_count == len(hit_sets["kn4"].frames)
    assert any(f.meta.width > 0 and f.meta.height > 0 for f in kn4_cel.frames)


def test_real_arenas_stiles_audio_and_hunks(adf_dir: Path) -> None:
    ds = DiskSet.from_directory(adf_dir)
    test_pics = decode_picture_pack(ds.read_file("test"))
    for arena_name, exp_lair in (
        ("fo1.t", False),
        ("fol1.t", True),
        ("gl1.t", False),
        ("gll1.t", True),
        ("sw1.t", False),
        ("swl1.t", True),
        ("wa1.t", False),
        ("wal1.t", True),
    ):
        arena = decode_arena(arena_name, ds.read_file(arena_name))
        assert arena.is_lair is exp_lair
        bg = composite_arena_background(arena, test_pics)
        assert bg.pixels.shape == (200, 320)
        assert len(bg.palette) == 32

    assert len(decode_stile(ds.read_file("co.stile"))) == 480
    assert len(decode_stile(ds.read_file("intro.stile"))) == 480

    kn_bank = decode_sample_bank("kn.a", ds.read_file("kn.a"))
    assert kn_bank.sample_count > 1000

    music = decode_cmp_module("music.cmp", ds.read_file("music.cmp"))
    assert len(music.raw_mod) == 123_228
    assert music.title == "introx5"
    assert music.signature == "FLT4"

    vmusic = decode_cmp_module("vmusic.cmp", ds.read_file("vmusic.cmp"))
    assert len(vmusic.raw_mod) == 87_792
    assert vmusic.title == "vict0ry6yy"
    assert vmusic.signature == "FLT4"

    prog = parse_hunk_executable("program", ds.read_file("program"))
    assert len(prog.sections) == 34
    mog = parse_hunk_executable("mog", ds.read_file("mog"))
    assert len(mog.sections) == 46
    assert len(parse_mog_synth_instruments(mog)) == 131


def test_real_full_extraction_manifest(adf_dir: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "extracted"
    manifest = extract_all_assets(adf_dir, out_dir, export_cel_pngs=False)
    assert len(manifest.disks) == 3
    assert len(manifest.pictures) == 32  # 9 from test + 23 standalone pictures
    assert len(manifest.arenas) == 56
    assert len(manifest.audio_banks) == 13
    assert len(manifest.modules) == 2
    assert len(manifest.executables) == 4  # Crystal, mog, nb, program
    assert (out_dir / "manifest.json").is_file()


def test_real_intro_cinematic_player_all_scenes(adf_dir: Path) -> None:
    ds = DiskSet.from_directory(adf_dir)
    bundle = GameAssetBundle(ds)
    player = bundle.get_intro_player()
    assert player is not None
    assert player.panorama.shape == (1200, 320)
    assert len(player.credit_frames) == 6

    # Sample across Mindscape, Title, Credits, Wipe, Story Scenes 001B..002E, and 00AA Caption
    sample_ticks = (30, 180, 320, 900, 1400, 1900, 2400, 2900, 3100, 3300, 3550, 3750, 3950)
    prev_bg = None
    distinct_frames = 0
    for tick in sample_ticks:
        bg, pal, sprites = build_intro_cinematic_frame(bundle, tick)
        assert bg is not None
        assert bg.shape == (200, 320)
        assert len(pal) == 32
        assert sprites == []
        if prev_bg is None or not (bg == prev_bg).all():
            distinct_frames += 1
        prev_bg = bg
    assert distinct_frames >= 10

    menu_bg, menu_pal = player.build_main_menu(bundle, 2, menu_cursor=3, gore_on=True)
    assert menu_bg is not None
    assert menu_bg.shape == (200, 320)
    assert len(menu_pal) == 32

    kbg, kpal = player.build_knight_select(bundle, 0)
    assert kbg is not None
    assert kbg.shape == (200, 320)
    assert len(kpal) == 32

    session = GameSession(disk_set=ds, start_scene=GameScene.TITLE)
    session.tick_count = player.total_ticks() - 1
    session.step_tick(InputFrame())
    assert session.scene == GameScene.KNIGHT_SELECT
