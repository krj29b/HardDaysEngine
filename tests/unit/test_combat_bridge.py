"""Unit tests for `app.combat_bridge` (T9.4)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from pymoonstone.app.combat_bridge import (
    CombatSpritePiece,
    build_mog_encounter,
    cel_mask_from_frame,
    cel_screen_origin,
    clamp_entity_to_arena,
    compose_knight_pieces,
    resolve_composed_melee_attack,
    resolve_melee_attack_points,
    select_knight_body_frame,
)
from pymoonstone.assets.arena import DecodedArena, ObstacleSegment
from pymoonstone.assets.cel import CelFrame, CelFrameMeta, DecodedCel
from pymoonstone.assets.hit import HitFrame, HitPoint, HitSet
from pymoonstone.assets.tables import (
    BuiltinFontTable,
    CombatActionTables,
    CreatureNodeRecord,
    EncounterBandRecord,
    LairRegionMetadata,
    MapSiteRecord,
    MogRuntimeTables,
    SynthSequenceTables,
)
from pymoonstone.sim.collision import ARENA_MAX_X, ARENA_MAX_Y, ARENA_MIN_X, ARENA_MIN_Y
from pymoonstone.sim.monster_ai import MonsterArchetype, default_monster_params
from pymoonstone.sim.overworld import EncounterTrigger, MoonPhase, TriggerKind
from pymoonstone.sim.types import EntityId, KnightId, Pos25D


def _make_mog_tables(nodes: tuple[CreatureNodeRecord, ...]) -> MogRuntimeTables:
    return MogRuntimeTables(
        knight_names=("Sir Red", "Sir Blue", "Sir Green", "Sir Yellow"),
        combat_actions=CombatActionTables(right_offsets=(0,) * 11, left_offsets=(0,) * 11),
        lair_region=LairRegionMetadata(
            map_sites=(MapSiteRecord(site_id=0, x=10, y=20),),
            creature_nodes=nodes,
            encounter_bands=(EncounterBandRecord(limit=50, kind=0),),
        ),
        builtin_font=BuiltinFontTable(glyphs=(b"\x00" * 8,) * 128),
        synth=SynthSequenceTables(
            blob_base=0,
            blob=b"",
            vibrato_rows=(),
            envelope_rows=(),
            sequences=(),
            waveforms=b"",
            instruments=(),
        ),
    )


def _make_cel_frame(pattern: tuple[str, ...]) -> CelFrame:
    height = len(pattern)
    width = len(pattern[0]) if height > 0 else 0
    pixels = np.zeros((height, width), dtype=np.uint8)
    for y, row in enumerate(pattern):
        for x, ch in enumerate(row):
            if ch != ".":
                pixels[y, x] = 3
    mask = pixels != 0
    meta = CelFrameMeta(
        index=0,
        data_offset=0,
        width=width,
        height=height,
        hot_and_flags=0,
        plane_bits=0x1F,
    )
    return CelFrame(meta=meta, pixels=pixels, mask=mask)


def test_build_mog_encounter_fallback_without_tables() -> None:
    trigger = EncounterTrigger(
        kind=TriggerKind.LAIR_BOSS,
        knight_id=KnightId.RED,
        node_id=5,
        day=3,
        moon_phase=MoonPhase.NEW_MOON,
        lair_index=1,
    )
    encounter = build_mog_encounter(trigger, (KnightId.RED,), mog_tables=None)
    assert len(encounter.waves) == 1
    assert len(encounter.monsters) == 1
    expected = default_monster_params(MonsterArchetype.BALOK)
    assert encounter.monsters[0].params.archetype == MonsterArchetype.BALOK
    assert encounter.monsters[0].params.max_hp == expected.max_hp
    assert encounter.monsters[0].params.defense == expected.defense


def test_build_mog_encounter_with_mog_tables_lair_waves_and_arena() -> None:
    nodes = (
        CreatureNodeRecord(
            index=0,
            creature_type=int(MonsterArchetype.TROLL),
            defence=7,
            map_x=100,
            map_y=80,
            aux_value=15,
            arena_script="fol1.t",
        ),
        CreatureNodeRecord(
            index=1,
            creature_type=int(MonsterArchetype.BALOK),
            defence=9,
            map_x=200,
            map_y=90,
            aux_value=25,
            arena_script="gll1.t",
        ),
    )
    mog = _make_mog_tables(nodes)
    arena = DecodedArena(
        name="gll1.t",
        region=1,
        is_lair=True,
        obstacles=(ObstacleSegment(x1=120, x2=180, y=100, unused=0),),
        tiles=(),
    )
    trigger = EncounterTrigger(
        kind=TriggerKind.LAIR_BOSS,
        knight_id=KnightId.BLUE,
        node_id=12,
        day=10,
        moon_phase=MoonPhase.FULL_MOON,
        lair_index=1,
    )
    encounter = build_mog_encounter(
        trigger,
        (KnightId.RED, KnightId.BLUE),
        mog_tables=mog,
        arena=arena,
    )
    assert len(encounter.waves) == 2
    assert len(encounter.waves[0].spawns) == 3
    assert len(encounter.waves[1].spawns) == 1
    boss_spec = encounter.waves[1].spawns[0]
    assert boss_spec.params.archetype == MonsterArchetype.BALOK
    assert boss_spec.params.defense == 9
    assert boss_spec.params.max_hp == default_monster_params(MonsterArchetype.BALOK).max_hp + 25
    assert len(encounter.obstacles) == 1
    assert encounter.obstacles[0].y == 100


def test_build_mog_encounter_wandering_and_dragon() -> None:
    nodes = (
        CreatureNodeRecord(
            index=0,
            creature_type=int(MonsterArchetype.RATMAN),
            defence=3,
            map_x=50,
            map_y=50,
            aux_value=4,
            arena_script="fo1.t",
        ),
    )
    mog = _make_mog_tables(nodes)
    wandering_trigger = EncounterTrigger(
        kind=TriggerKind.WANDERING_MONSTER,
        knight_id=KnightId.GREEN,
        node_id=0,
        day=8,
        moon_phase=MoonPhase.FIRST_QUARTER,
        encounter_kind=1,
    )
    wandering = build_mog_encounter(wandering_trigger, (KnightId.GREEN,), mog_tables=mog)
    assert len(wandering.waves) == 1
    assert len(wandering.monsters) == 2
    assert wandering.monsters[0].params.archetype == MonsterArchetype.RATMAN
    assert wandering.monsters[0].params.defense == 3

    dragon_trigger = EncounterTrigger(
        kind=TriggerKind.DRAGON_INTERCEPT,
        knight_id=KnightId.RED,
        node_id=4,
        day=14,
        moon_phase=MoonPhase.FULL_MOON,
    )
    dragon = build_mog_encounter(dragon_trigger, (KnightId.RED,), mog_tables=mog)
    assert len(dragon.monsters) == 1
    assert dragon.monsters[0].params.archetype == MonsterArchetype.DRAGON


def test_cel_mask_from_frame_pixels_and_empty() -> None:
    empty = _make_cel_frame(())
    empty_mask = cel_mask_from_frame(empty)
    assert empty_mask.width == 0
    assert empty_mask.height == 0

    frame = _make_cel_frame(
        (
            "#.........#.",
            "..##........",
            "...........#",
        )
    )
    mask = cel_mask_from_frame(frame)
    assert mask.width == 12
    assert mask.height == 3
    assert mask.test_pixel(0, 0)
    assert mask.test_pixel(10, 0)
    assert not mask.test_pixel(1, 0)
    assert mask.test_pixel(2, 1)
    assert mask.test_pixel(3, 1)
    assert mask.test_pixel(11, 2)
    assert mask.test_pixel(0, 2, flipped=True)


def test_cel_mask_from_frame_hurtbox_planes() -> None:
    @dataclass(slots=True, frozen=True)
    class _HurtboxFrame:
        meta: CelFrameMeta
        hurtbox_planes: tuple[bytes, ...]

    meta = CelFrameMeta(
        index=0,
        data_offset=0,
        width=4,
        height=2,
        hot_and_flags=0,
        plane_bits=1,
    )
    frame = _HurtboxFrame(meta=meta, hurtbox_planes=(b"\x80\x00\x10\x00",))
    mask = cel_mask_from_frame(frame)
    assert mask.width == 4
    assert mask.height == 2
    assert mask.test_pixel(0, 0)
    assert not mask.test_pixel(1, 0)
    assert mask.test_pixel(3, 1)


def test_resolve_melee_attack_points_hit_miss_and_flip() -> None:
    defender = _make_cel_frame(
        (
            "....",
            ".##.",
            ".##.",
            "....",
        )
    )
    hit_frame = HitFrame(
        frame_index=0,
        hit_type=1,
        max_x=20,
        max_y=10,
        points=(HitPoint(x=20, y=2),),
    )

    assert resolve_melee_attack_points(
        attacker_id=EntityId(1),
        attacker_pos=Pos25D(100, 90, 0),
        attacker_screen_x=90,
        attacker_screen_y=80,
        attacker_cel_width=24,
        attacker_cel_height=16,
        attacker_flipped=False,
        hit_frame=hit_frame,
        defender_id=EntityId(2),
        defender_pos=Pos25D(110, 92, 0),
        defender_screen_x=109,
        defender_screen_y=80,
        defender_frame=defender,
    )

    # Misses when attack point lands on transparent pixel
    miss_frame = HitFrame(
        frame_index=0,
        hit_type=1,
        max_x=20,
        max_y=10,
        points=(HitPoint(x=20, y=0),),
    )
    assert not resolve_melee_attack_points(
        attacker_id=EntityId(1),
        attacker_pos=Pos25D(100, 90, 0),
        attacker_screen_x=90,
        attacker_screen_y=80,
        attacker_cel_width=24,
        attacker_cel_height=16,
        attacker_flipped=False,
        hit_frame=miss_frame,
        defender_id=EntityId(2),
        defender_pos=Pos25D(110, 92, 0),
        defender_screen_x=109,
        defender_screen_y=80,
        defender_frame=defender,
    )

    # Misses when depth gate fails
    assert not resolve_melee_attack_points(
        attacker_id=EntityId(1),
        attacker_pos=Pos25D(100, 90, 0),
        attacker_screen_x=90,
        attacker_screen_y=80,
        attacker_cel_width=24,
        attacker_cel_height=16,
        attacker_flipped=False,
        hit_frame=hit_frame,
        defender_id=EntityId(2),
        defender_pos=Pos25D(110, 115, 0),
        defender_screen_x=109,
        defender_screen_y=80,
        defender_frame=defender,
    )

    # Flipped attacker hits defender to its left (cel_width - 20 = 4 -> screen_x + 4 = 94)
    assert resolve_melee_attack_points(
        attacker_id=EntityId(1),
        attacker_pos=Pos25D(100, 90, 0),
        attacker_screen_x=90,
        attacker_screen_y=80,
        attacker_cel_width=24,
        attacker_cel_height=16,
        attacker_flipped=True,
        hit_frame=hit_frame,
        defender_id=EntityId(2),
        defender_pos=Pos25D(94, 90, 0),
        defender_screen_x=93,
        defender_screen_y=80,
        defender_frame=defender,
    )


def test_resolve_melee_attack_points_distance_fallback() -> None:
    assert resolve_melee_attack_points(
        attacker_id=EntityId(1),
        attacker_pos=Pos25D(100, 90, 0),
        attacker_screen_x=90,
        attacker_screen_y=80,
        attacker_cel_width=24,
        attacker_cel_height=16,
        attacker_flipped=False,
        hit_frame=None,
        defender_id=EntityId(2),
        defender_pos=Pos25D(125, 94, 0),
        defender_screen_x=115,
        defender_screen_y=80,
        defender_frame=None,
        fallback_reach_x=30,
    )
    assert not resolve_melee_attack_points(
        attacker_id=EntityId(1),
        attacker_pos=Pos25D(100, 90, 0),
        attacker_screen_x=90,
        attacker_screen_y=80,
        attacker_cel_width=24,
        attacker_cel_height=16,
        attacker_flipped=False,
        hit_frame=None,
        defender_id=EntityId(2),
        defender_pos=Pos25D(160, 94, 0),
        defender_screen_x=150,
        defender_screen_y=80,
        defender_frame=None,
        fallback_reach_x=30,
    )
    # Self-hit is always False
    assert not resolve_melee_attack_points(
        attacker_id=EntityId(1),
        attacker_pos=Pos25D(100, 90, 0),
        attacker_screen_x=90,
        attacker_screen_y=80,
        attacker_cel_width=24,
        attacker_cel_height=16,
        attacker_flipped=False,
        hit_frame=None,
        defender_id=EntityId(1),
        defender_pos=Pos25D(100, 90, 0),
        defender_screen_x=90,
        defender_screen_y=80,
        defender_frame=None,
    )


def test_clamp_entity_to_arena() -> None:
    clamped = clamp_entity_to_arena(Pos25D(0, 999, 7), None)
    assert clamped == Pos25D(ARENA_MIN_X, ARENA_MAX_Y, 7)

    clamped_upper = clamp_entity_to_arena(Pos25D(500, 0, 3), None)
    assert clamped_upper == Pos25D(ARENA_MAX_X, ARENA_MIN_Y, 3)

    arena = DecodedArena(
        name="fo1.t",
        region=0,
        is_lair=False,
        obstacles=(ObstacleSegment(x1=140, x2=180, y=100, unused=0),),
        tiles=(),
    )
    blocked_y = clamp_entity_to_arena(Pos25D(160, 95, 0), arena, dy=10)
    assert blocked_y == Pos25D(160, 99, 0)

    blocked_x = clamp_entity_to_arena(Pos25D(135, 100, 0), arena, dx=10)
    assert blocked_x == Pos25D(139, 100, 0)


def _make_sized_frame(index: int, width: int, height: int, *, hot_x: int = 0) -> CelFrame:
    pixels = np.ones((height, width), dtype=np.uint8)
    mask = np.ones((height, width), dtype=bool)
    meta = CelFrameMeta(
        index=index,
        data_offset=0,
        width=width,
        height=height,
        hot_and_flags=(hot_x & 0x0F) << 4,
        plane_bits=0x1F,
    )
    return CelFrame(meta=meta, pixels=pixels, mask=mask)


def test_select_knight_body_frame_skips_slivers_and_stays_in_hit_range() -> None:
    from pymoonstone.sim.knight_combat import KnightAction

    frames = tuple(
        _make_sized_frame(i, 7, 33) if i % 2 == 1 else _make_sized_frame(i, 54, 23)
        for i in range(60)
    )
    cel = DecodedCel(frame_count=60, packed_size=0, unpacked_bits=0, frames=frames, raw_planes=b"")
    hit_frames = tuple(
        HitFrame(
            frame_index=i,
            hit_type=1,
            max_x=40 if i == 10 else 0,
            max_y=12 if i == 10 else 0,
            points=(HitPoint(40, 12),) if i == 10 else (),
        )
        for i in range(26)
    )
    hit_set = HitSet(name="kn4", norm_name="kn4", frames=hit_frames)

    for action in KnightAction:
        for tick in (0, 6, 12, 18):
            fidx = select_knight_body_frame(cel, action, tick, hit_set=hit_set)
            assert 0 <= fidx < 26
            assert cel.frames[fidx].meta.width >= 20
            if action in (
                KnightAction.OVERHEAD_SLASH,
                KnightAction.THRUST,
                KnightAction.LOW_SWEEP,
            ):
                assert fidx == 10


def test_compose_knight_pieces_from_mog_bytecode() -> None:
    import struct

    from pymoonstone.sim.collision import DRAW_FLAG_ATTACK, DRAW_FLAG_HURTBOX
    from pymoonstone.sim.combat_vm import OP_END_FRAME, OP_END_SCRIPT
    from pymoonstone.sim.knight_combat import KnightAction

    frames = tuple(_make_sized_frame(i, 24, 20, hot_x=2) for i in range(26))
    cel = DecodedCel(frame_count=26, packed_size=0, unpacked_bits=0, frames=frames, raw_planes=b"")

    script = (
        struct.pack(">BBbBh", 0, 4, -8, DRAW_FLAG_HURTBOX, 2)
        + struct.pack(">BBbBh", 0, 5, 0, DRAW_FLAG_HURTBOX | DRAW_FLAG_ATTACK, 10)
        + bytes([OP_END_FRAME, OP_END_SCRIPT])
    )
    s4 = b"\x00" * 32 + script
    right_offsets = (32,) * 11
    left_offsets = (32,) * 11
    actions = CombatActionTables(right_offsets=right_offsets, left_offsets=left_offsets)

    pieces = compose_knight_pieces(
        cel,
        KnightAction.OVERHEAD_SLASH,
        0,
        flipped=False,
        mog_s4=s4,
        combat_actions=actions,
    )
    assert len(pieces) == 2
    assert pieces[0].frame_index == 4
    assert (pieces[0].dx, pieces[0].dy) == (2, -8)
    assert pieces[0].hurtbox is True
    assert pieces[0].attack is False
    assert pieces[1].frame_index == 5
    assert (pieces[1].dx, pieces[1].dy) == (10, 0)
    assert pieces[1].attack is True


def test_resolve_composed_melee_attack_with_hotspots() -> None:
    atk_frame = _make_sized_frame(0, 24, 20, hot_x=2)
    def_frame = _make_sized_frame(1, 20, 20, hot_x=0)
    hit_set = HitSet(
        name="kn4",
        norm_name="kn4",
        frames=(
            HitFrame(
                frame_index=0,
                hit_type=1,
                max_x=22,
                max_y=10,
                points=(HitPoint(22, 10),),
            ),
        ),
    )
    atk_piece = CombatSpritePiece(frame=atk_frame, frame_index=0, dx=6, dy=0, attack=True)
    def_piece = CombatSpritePiece(frame=def_frame, frame_index=1, hurtbox=True)

    # cel_screen_origin for attacker at x=100: 100 + 6 - 12 - 2 = 92; hit point at 92 + 22 = 114
    sx, sy = cel_screen_origin(Pos25D(100, 90, 0), atk_frame, dx=6, dy=0, flipped=False)
    assert (sx, sy) == (92, 80)

    assert resolve_composed_melee_attack(
        attacker_id=EntityId(1),
        attacker_pos=Pos25D(100, 90, 0),
        attacker_pieces=(atk_piece,),
        attacker_hit_set=hit_set,
        attacker_flipped=False,
        defender_id=EntityId(2),
        defender_pos=Pos25D(115, 92, 0),
        defender_pieces=(def_piece,),
    )

    assert not resolve_composed_melee_attack(
        attacker_id=EntityId(1),
        attacker_pos=Pos25D(100, 90, 0),
        attacker_pieces=(atk_piece,),
        attacker_hit_set=hit_set,
        attacker_flipped=False,
        defender_id=EntityId(2),
        defender_pos=Pos25D(160, 92, 0),
        defender_pieces=(def_piece,),
    )
