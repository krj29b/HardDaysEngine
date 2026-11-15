"""Fight palette operand readers and assembly on synthetic data (ARCHITECTURE.md §10.13, §10.15)."""

from __future__ import annotations

import struct

import pytest

from pymoonstone.assets.fight_palette import (
    REGION_FOREST,
    REGION_GLEN,
    REGION_SWAMP,
    REGION_WASTELAND,
    SCENE_DRAGON,
    SCENE_DUEL,
    SCENE_GUARDIAN,
    SCENE_RATMEN,
    SCENE_TROGG_AXE,
    SCENE_TROGG_SPEAR,
    FightPaletteTables,
    MogVersionError,
    _read_immediate_run,
    _read_table_via_lea,
    assemble_fight_palette,
    parse_fight_palette_tables,
)
from pymoonstone.assets.hunk import HUNK_CODE, HUNK_DATA, DecodedHunkExecutable, HunkSection

PICTURE = tuple(0x100 + i for i in range(32))


def _tables() -> FightPaletteTables:
    return FightPaletteTables(
        class_rows=tuple(tuple(0xA00 + kind * 16 + i for i in range(3)) for kind in range(5)),
        scene_rows={
            SCENE_RATMEN: tuple(0xB00 + i for i in range(7)),
            SCENE_DRAGON: tuple(0xC00 + i for i in range(7)),
        },
        trogg_rows=tuple(tuple(0xD00 + row * 16 + i for i in range(7)) for row in range(3)),
        dragon_tail=(0xE01, 0xE02, 0xE03),
        low_tweak=(0x901, 0x902, 0x903, 0x904),
        region_rows={
            region: tuple(0x800 + region * 16 + i for i in range(13))
            for region in (REGION_GLEN, REGION_FOREST, REGION_SWAMP, REGION_WASTELAND)
        },
        guardian_row=tuple(0x700 + i for i in range(23)),
        blood_red=0xF00,
    )


def _code_section(data: bytes, relocs: dict[int, tuple[int, ...]] | None = None) -> HunkSection:
    return HunkSection(
        index=0, hunk_type=HUNK_CODE, alloc_size=len(data), data=data, relocs=relocs or {}
    )


def test_immediate_run_reads_operands_of_both_move_forms() -> None:
    code = struct.pack(">8H", 0x4E71, 0x4E71, 0x30FC, 0x0123, 0x30FC, 0x0456, 0x30BC, 0x0789)

    assert _read_immediate_run(code, 4, 3, "test") == (0x0123, 0x0456, 0x0789)


def test_immediate_run_rejects_a_different_instruction() -> None:
    code = struct.pack(">4H", 0x30FC, 0x0123, 0x4E75, 0x0456)

    with pytest.raises(MogVersionError, match="code offset 4, found \\$4E75"):
        _read_immediate_run(code, 0, 2, "test")


def test_table_via_lea_follows_the_relocated_offset() -> None:
    code = struct.pack(">HHI", 0x4E71, 0x43F9, 6)
    data = struct.pack(">6H", 1, 2, 3, 0x0AAA, 0x0BBB, 0x0CCC)
    section = _code_section(code, {4: (4,)})

    assert _read_table_via_lea(section, data, 2, 3, "test") == (0x0AAA, 0x0BBB, 0x0CCC)


def test_table_via_lea_needs_the_relocation_and_the_opcode() -> None:
    code = struct.pack(">HHI", 0x4E71, 0x43F9, 0)
    data = bytes(8)

    with pytest.raises(MogVersionError, match="relocated LEA"):
        _read_table_via_lea(_code_section(code), data, 2, 3, "test")
    with pytest.raises(MogVersionError, match="relocated LEA"):
        _read_table_via_lea(_code_section(code, {4: (4,)}), data, 0, 3, "test")


def test_table_via_lea_rejects_a_table_past_the_hunk() -> None:
    code = struct.pack(">HI", 0x43F9, 4)

    with pytest.raises(MogVersionError, match="runs past the hunk"):
        _read_table_via_lea(_code_section(code, {4: (2,)}), bytes(8), 0, 3, "test")


def test_parse_rejects_an_unknown_mog_version() -> None:
    sections = tuple(
        HunkSection(index=i, hunk_type=HUNK_DATA, alloc_size=64, data=bytes(64), relocs={})
        for i in range(5)
    )

    with pytest.raises(MogVersionError, match="unsupported version"):
        parse_fight_palette_tables(DecodedHunkExecutable(name="mog", sections=sections))


def test_parse_rejects_too_few_hunks() -> None:
    with pytest.raises(MogVersionError, match="fewer hunks"):
        parse_fight_palette_tables(DecodedHunkExecutable(name="mog", sections=()))


def test_creature_scene_in_the_forest() -> None:
    tables = _tables()

    pal = assemble_fight_palette(
        PICTURE, tables, scene=SCENE_RATMEN, region=REGION_FOREST, first_kind=2
    )

    assert len(pal) == 32
    assert pal[0] == 0
    assert pal[1:6] == PICTURE[1:6]
    assert pal[6:9] == tables.class_rows[2]
    assert pal[9:15] == tables.scene_rows[SCENE_RATMEN][:6]
    assert pal[15] == tables.blood_red
    assert pal[16:29] == tables.region_rows[REGION_FOREST]
    assert pal[29:32] == PICTURE[29:32]


@pytest.mark.parametrize("region", [REGION_SWAMP, REGION_WASTELAND])
def test_swamp_and_wasteland_replace_the_low_colours(region: int) -> None:
    tables = _tables()

    pal = assemble_fight_palette(PICTURE, tables, scene=SCENE_RATMEN, region=region, first_kind=0)

    assert pal[1:5] == tables.low_tweak
    assert pal[5] == PICTURE[5]
    assert pal[16:29] == tables.region_rows[region]


@pytest.mark.parametrize(
    ("region", "row"),
    [(REGION_WASTELAND, 0), (REGION_GLEN, 1), (REGION_FOREST, 2), (REGION_SWAMP, 2)],
)
@pytest.mark.parametrize("scene", [SCENE_TROGG_AXE, SCENE_TROGG_SPEAR])
def test_trogg_scenes_pick_their_row_by_region(scene: int, region: int, row: int) -> None:
    tables = _tables()

    pal = assemble_fight_palette(PICTURE, tables, scene=scene, region=region, first_kind=0)

    assert pal[9:15] == tables.trogg_rows[row][:6]
    assert pal[15] == tables.blood_red


def test_dragon_scene_keeps_its_own_colour_15_and_sets_the_tail() -> None:
    tables = _tables()

    pal = assemble_fight_palette(
        PICTURE, tables, scene=SCENE_DRAGON, region=REGION_FOREST, first_kind=1
    )

    assert pal[9:16] == tables.scene_rows[SCENE_DRAGON]
    assert pal[29:32] == tables.dragon_tail


def test_duel_scene_shows_the_second_fighter_class() -> None:
    tables = _tables()

    pal = assemble_fight_palette(
        PICTURE, tables, scene=SCENE_DUEL, region=REGION_GLEN, first_kind=0, second_kind=3
    )

    assert pal[6:9] == tables.class_rows[0]
    assert pal[9:12] == tables.class_rows[3]
    assert pal[12:15] == PICTURE[12:15]


def test_guardian_scene_skips_the_region_colours() -> None:
    tables = _tables()

    pal = assemble_fight_palette(
        PICTURE, tables, scene=SCENE_GUARDIAN, region=REGION_WASTELAND, first_kind=0
    )

    assert pal[1:5] == PICTURE[1:5]
    assert pal[9:15] == tables.guardian_row[:6]
    assert pal[15] == tables.blood_red
    assert pal[16:32] == tables.guardian_row[7:23]


@pytest.mark.parametrize("kind", [4, 5, 99, -1])
def test_other_kinds_use_the_last_class_row(kind: int) -> None:
    tables = _tables()

    pal = assemble_fight_palette(
        PICTURE, tables, scene=SCENE_RATMEN, region=REGION_FOREST, first_kind=kind
    )

    assert pal[6:9] == tables.class_rows[4]


def test_unknown_scene_and_short_picture_palette() -> None:
    tables = _tables()

    pal = assemble_fight_palette(PICTURE[:16], tables, scene=-1, region=REGION_FOREST, first_kind=0)

    assert pal[9:15] == PICTURE[9:15]
    assert pal[29:32] == (0, 0, 0)
