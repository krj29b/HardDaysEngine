"""Real-disk verification of the decoded ``program`` intro and ``mog`` menus (§12.42)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harddaysengine.app.intro_player import _NullCanvas
from harddaysengine.app.scene_assets import GameAssetBundle
from harddaysengine.assets.adf import DiskSet
from harddaysengine.assets.hunk import parse_hunk_executable
from harddaysengine.assets.intro_data import parse_intro_plan, parse_menu_data
from harddaysengine.sim.ending_types import Buffer, Caption, Series, Spawn
from harddaysengine.sim.intro import IntroRun
from harddaysengine.sim.intro_types import ForwardWipe, PictureCopy, PictureSetup

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.adf


def test_intro_plan_from_real_program_has_expected_scenes_loader_and_wipe(adf_dir: Path) -> None:
    ds = DiskSet.from_directory(adf_dir)
    plan = parse_intro_plan(parse_hunk_executable("program", ds.read_file("program")))

    assert plan.flag_bit == 0x80
    assert plan.next_program == "Mog"
    assert [p.name for p in plan.load.pictures] == [
        "bg1a.piv",
        "bg1c.piv",
        "bg1b.piv",
        "bg4.piv",
        "bg5a.piv",
        "bg3.piv",
        "bg2.piv",
        "bg2a.piv",
    ]
    assert plan.load.cels == (
        (0, "au1.cel"),
        (1, "li1.cel"),
        (4, "da1.cel"),
        (2, "ha1.cel"),
        (3, "dw1.cel"),
    )
    assert plan.load.cels_with_progress == 4
    assert plan.load.overlay == "ov1.cel"
    assert plan.load.stile == "intro.stile" and plan.load.stile_bytes == 1000
    assert plan.load.music == "music.cmp" and plan.load.music_bytes == 0x159D9
    assert len(plan.load.progress.credits) == 6
    assert plan.load.progress.logo_frame == 0x49
    assert (plan.load.progress.logo_x, plan.load.progress.logo_y) == (9, 60)

    wipes = [s for s in plan.steps if isinstance(s, ForwardWipe)]
    assert len(wipes) == 1
    assert wipes[0].limit == 1000 and wipes[0].music_step == 4
    assert len(wipes[0].thresholds) == 11 and len(wipes[0].steps) == 11

    setups = [s for s in plan.steps if isinstance(s, PictureSetup)]
    assert setups == [
        PictureSetup(
            (
                PictureCopy(Buffer.P5, Buffer.P2, 3, 0),
                PictureCopy(Buffer.P6, Buffer.P3, 4, 1),
                PictureCopy(Buffer.P7, Buffer.P4, 5, 2),
            )
        )
    ]

    series = [s for s in plan.steps if isinstance(s, Series)]
    assert len(series) == 2
    assert series[0].first_frame == 4 and len(series[0].scripts) == 5
    assert series[1].first_frame == 8 and len(series[1].scripts) == 5

    captions = [s for s in plan.steps if isinstance(s, Caption)]
    assert len(captions) == 1
    assert captions[0].lines[-1].text == "MOONSTONE"

    spawns = [s for s in plan.steps if isinstance(s, Spawn)]
    assert len(spawns) == 23


def test_menu_data_from_real_mog_matches_original_portrait_order(adf_dir: Path) -> None:
    ds = DiskSet.from_directory(adf_dir)
    menu = parse_menu_data(parse_hunk_executable("mog", ds.read_file("mog")))

    assert menu.title.banner.frame == 0x49
    assert (menu.title.banner.x, menu.title.banner.y) == (5, 10)
    assert menu.title.cursor_x == 50
    assert menu.title.cursor_y == (85, 110, 148, 168)
    assert menu.title.gore_on_text == "On" and menu.title.gore_off_text == "Off"
    assert [line.text.strip() for line in menu.title.lines[:4]] == [
        "Players",
        "Gore",
        "Practice",
        "Select Knight",
    ]

    ks = menu.knight_select
    assert ks.lines[0].text == "Select a Knight"
    assert ks.portrait_x == (12, 88, 164, 240)
    assert ks.portrait_y == 80
    assert ks.portrait_first_frame == 2 and ks.highlight_frame == 1
    assert (ks.name_x, ks.name_y) == (50, 50)
    # Portrait 0 is Blue (Sir Godber, h4:0x76), Portrait 1 is Yellow (Sir Richard, h4:0x60)
    assert ks.knight_names == ("SIR GODBER", "SIR RICHARD", "SIR JEFFREY", "SIR EDWARD")
    assert len(ks.palette) == 32


def test_intro_run_replays_deterministically_on_real_disks(adf_dir: Path) -> None:
    ds = DiskSet.from_directory(adf_dir)
    bundle = GameAssetBundle(ds)
    player = bundle.get_intro_player()
    assert player is not None

    run_a = IntroRun(player.plan, player.inputs, _NullCanvas())
    run_b = IntroRun(player.plan, player.inputs, _NullCanvas())

    checkpoints = {68, 136, 176, 400, 1000, 2000, 3500, 4800, 5332}
    snaps_a: dict[int, dict[str, object]] = {}
    snaps_b: dict[int, dict[str, object]] = {}

    while not run_a.advance():
        if run_a.tick in checkpoints:
            snaps_a[run_a.tick] = run_a.snapshot()
    while not run_b.advance():
        if run_b.tick in checkpoints:
            snaps_b[run_b.tick] = run_b.snapshot()

    assert run_a.tick == 5332 and run_b.tick == 5332
    assert run_a.music is True
    assert snaps_a == snaps_b
