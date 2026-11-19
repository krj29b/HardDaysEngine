"""Independent fixed welcome records, PAL continuations and pixels (§12.33)."""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

import numpy as np
import pytest

from harddaysengine.app.audit import AuditRig
from harddaysengine.app.game import GameScene, GameSession
from harddaysengine.app.input import InputFrame, PlayerInput
from harddaysengine.assets.adf import DiskSet
from harddaysengine.assets.hunk import parse_hunk_executable
from harddaysengine.assets.town_loading_data import parse_town_welcome
from harddaysengine.sim.towns import TownEntry
from harddaysengine.sim.types import KnightId

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.adf


@pytest.mark.parametrize("waterdeep", (False, True))
def test_welcome_matches_an_independent_linked_record_walk(adf_dir: Path, waterdeep: bool) -> None:
    exe = parse_hunk_executable("mog", DiskSet.from_directory(adf_dir).read_file("mog"))
    code, data = exe.sections[0].data, exe.sections[4].data
    start = 0x2DD2 if waterdeep else 0x2D96
    name, cell = struct.unpack_from(">II", code, start + 2)
    cursor = int.from_bytes(code[start + 12 : start + 16], "big")
    expected = []
    while True:
        text, x, y, style, next_record = struct.unpack_from(">IHHHI", data, cursor)
        target = name if cursor == cell else text
        bold_style = int.from_bytes(code[0x9352:0x9354], "big")
        expected.append(
            (data[target : data.index(0, target)].decode("ascii"), x, y, style | bold_style)
        )
        if not next_record:
            break
        cursor = next_record
    welcome = parse_town_welcome(exe, waterdeep=waterdeep)
    assert [(r.text, r.x, r.y, r.style) for r in welcome.lines] == expected
    for pos, actual in ((0x2BFC, welcome.picture), (0x2C38, welcome.font)):
        target = int.from_bytes(code[pos : pos + 4], "big")
        assert actual == data[target : data.index(0, target)].decode("ascii")
    assert exe.sections[4].data == data


@pytest.mark.parametrize("waterdeep", (False, True))
def test_prologue_ignores_fire_and_space_without_consuming_rng_and_replays(
    adf_dir: Path,
    waterdeep: bool,
) -> None:
    traces = []
    for _ in range(2):
        session = GameSession(disk_set=DiskSet.from_directory(adf_dir))
        session._open_town(KnightId.RED, waterdeep=waterdeep)
        town = session.town_screen
        assert town is not None
        rng, spent = session.overworld.prng.state, session.overworld.spent
        trace, phases = [], set()
        for _ in range(128):
            phases.add(town.state.entry)
            trace.append(session.state_hash())
            if town.state.entry == TownEntry.READY and not session.palette_fx.is_fading:
                break
            session.step_tick(InputFrame(players={KnightId.RED: PlayerInput(fire=True)}, key=32))
            assert session.sheet_screen is session.dice_screen is session.service_screen is None
        else:
            raise AssertionError("town prologue did not finish")
        assert phases == set(TownEntry)
        assert session.scene == GameScene.LOCATION
        assert (session.overworld.prng.state, session.overworld.spent) == (rng, spent)
        assert (town.state.pointer_x, town.state.pointer_y) == (
            town.data.pointer_x,
            town.data.pointer_y,
        )
        digest = session.state_hash()
        town.state.entry = TownEntry.WELCOME_OUT
        assert session.state_hash() != digest
        traces.append(trace)
    assert traces[0] == traces[1]


@pytest.mark.gl
@pytest.mark.parametrize("waterdeep", (False, True))
def test_welcome_pixels_use_loaded_font_and_message_palette_without_render_side_effects(
    adf_dir: Path,
    waterdeep: bool,
) -> None:
    session = GameSession(disk_set=DiskSet.from_directory(adf_dir))
    session._open_town(KnightId.RED, waterdeep=waterdeep)
    town = session.town_screen
    assert town is not None and town.message is not None and town.font is not None
    for _ in range(64):
        if town.state.entry == TownEntry.WELCOME_OUT:
            break
        session.step_tick(InputFrame())
    assert town.state.entry == TownEntry.WELCOME_OUT
    expected = town.message.pixels.copy()
    for line in town.welcome.lines:
        assert line.style == 9
        glyphs = [town.font.frames[town.char_map[ord(ch) - 32]] for ch in line.text]
        x = (town.span - sum(g.meta.width - 3 for g in glyphs)) >> 1
        for glyph in glyphs:
            height, width = glyph.pixels.shape
            target = expected[line.y : line.y + height, x : x + width]
            target[glyph.pixels != 0] = glyph.pixels[glyph.pixels != 0]
            x += width - 3
    rig = AuditRig(adf_dir)
    try:
        before = session.state_hash()
        first, second = rig.capture(session, "welcome"), rig.capture(session, "welcome_again")
        assert session.state_hash() == before
        assert np.array_equal(first.rgb, second.rgb)
        palette = np.asarray(town.message.palette, dtype=np.uint8)
        assert np.array_equal(first.rgb[28:228], palette[expected])
    finally:
        rig.release()
