"""Fight palette rows read from the real ``mog``, checked against independent sources (T11.6)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from pymoonstone.app.game import GameSession
from pymoonstone.app.scene_assets import GameAssetBundle
from pymoonstone.assets.adf import DiskSet
from pymoonstone.assets.fight_palette import (
    REGION_FOREST,
    REGION_GLEN,
    REGION_SWAMP,
    REGION_WASTELAND,
    SCENE_DRAGON,
    FightPaletteTables,
)
from pymoonstone.sim.overworld import EncounterTrigger, TriggerKind
from pymoonstone.sim.types import KnightId

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.adf

# The region backdrops of the "test" picture pack (ARCHITECTURE.md §3.1): a different file from
# `mog`, so their colours 16..28 are an independent check of the region tables.
_REGION_BACKDROP_PICTURES = {
    REGION_GLEN: 7,
    REGION_FOREST: 4,
    REGION_SWAMP: 6,
    REGION_WASTELAND: 5,
}
_ARENA_FILES = ("fo1.t", "gl1.t", "sw1.t", "wa1.t")


@pytest.fixture(scope="module")
def bundle(adf_dir: Path) -> GameAssetBundle:
    return GameAssetBundle(DiskSet.from_directory(adf_dir))


@pytest.fixture(scope="module")
def tables(bundle: GameAssetBundle) -> FightPaletteTables:
    loaded = bundle.get_fight_palette_tables()
    assert loaded is not None
    return loaded


def _rgb4(word: int) -> tuple[int, int, int]:
    return (word >> 8) & 15, (word >> 4) & 15, word & 15


def test_row_shapes(tables: FightPaletteTables) -> None:
    assert [len(row) for row in tables.class_rows] == [3, 3, 3, 3, 3]
    assert {scene: len(row) for scene, row in tables.scene_rows.items()} == {
        0x00: 7,
        0x24: 7,
        0x30: 7,
        0x40: 6,
        0x04: 7,
        0x14: 7,
    }
    assert [len(row) for row in tables.trogg_rows] == [7, 7, 7]
    assert len(tables.guardian_row) == 23
    assert all(len(row) == 13 for row in tables.region_rows.values())


def test_class_rows_are_the_four_knight_colours(tables: FightPaletteTables) -> None:
    # Portrait order of the knight select screen: blue, orange, green, red.
    for word in tables.class_rows[0]:
        r, g, b = _rgb4(word)
        assert b > r and b > g
    for word in tables.class_rows[1]:
        r, g, b = _rgb4(word)
        assert r > g > b
    for word in tables.class_rows[2]:
        r, g, b = _rgb4(word)
        assert g > r and g > b
    for word in tables.class_rows[3]:
        r, g, b = _rgb4(word)
        assert r > g and r > b
    # Every row is a ramp from light to dark.
    for row in tables.class_rows:
        brightness = [sum(_rgb4(word)) for word in row]
        assert brightness == sorted(brightness, reverse=True)


def test_blood_colour_is_pure_red(tables: FightPaletteTables) -> None:
    r, g, b = _rgb4(tables.blood_red)
    assert r >= 10
    assert (g, b) == (0, 0)


def test_region_tables_match_the_backdrop_pictures(
    bundle: GameAssetBundle, tables: FightPaletteTables
) -> None:
    for region, picture_index in _REGION_BACKDROP_PICTURES.items():
        picture = bundle.get_test_picture(picture_index)
        assert picture is not None
        from_picture = tuple(
            ((r >> 4) << 8) | ((g >> 4) << 4) | (b >> 4) for r, g, b in picture.palette[16:29]
        )
        assert tables.region_rows[region] == from_picture, f"region ${region:X}"


@pytest.mark.parametrize("arena_file", _ARENA_FILES)
def test_fight_palette_keeps_the_picture_colour_5(adf_dir: Path, arena_file: str) -> None:
    session = GameSession(disk_set=DiskSet.from_directory(adf_dir), players=(KnightId.BLUE,))
    assert session.asset_bundle is not None
    loaded = session.asset_bundle.get_arena(arena_file)
    assert loaded is not None
    _arena, picture = loaded
    from unittest.mock import patch

    from pymoonstone.app.fight_runtime import FightLoader

    session.fight_loader = FightLoader(session.asset_bundle)
    trogg = next(a for a in session.fight_loader.defs.arenas if a.name == "trogg_axe")
    trigger = EncounterTrigger(
        kind=TriggerKind.WANDERING_MONSTER,
        knight_id=KnightId.BLUE,
        node_id=0,
        day=1,
        moon_phase=session.overworld.moon_phase,
        creature_type=trogg.handler_offsets[0],
    )
    with patch.object(session, "_arena_name_for_trigger", return_value=arena_file):
        session._start_combat(trigger, KnightId.BLUE)
    assert session.active_encounter is not None
    palette = session.active_encounter.runtime.scene.palette

    assert palette[5] == picture.palette[5]
    assert palette[0] == (0, 0, 0)
    assert palette[29:32] == tuple(picture.palette[29:32])
    red, green, blue = palette[6]
    assert blue > red and blue > green


def test_dragon_scene_is_the_only_one_without_blood_at_15(tables: FightPaletteTables) -> None:
    dragon_15 = tables.scene_rows[SCENE_DRAGON][6]
    assert dragon_15 != tables.blood_red
