"""Unit tests for pure-integer 2.5D depth, hurtbox/hitbox, and arena obstacle collision."""

from __future__ import annotations

import pytest

from pymoonstone.sim.collision import (
    ARENA_MAX_X,
    ARENA_MAX_Y,
    ARENA_MIN_X,
    ARENA_MIN_Y,
    DEFAULT_DEPTH_THRESHOLD,
    DRAW_FLAG_ATTACK,
    DRAW_FLAG_HURTBOX,
    DRAW_FLAG_NO_BODY_BOX,
    ArenaBounds,
    AttackEntry,
    AttackPattern,
    AttackPoint,
    CelMask,
    HurtEntry,
    ObstacleSegment,
    attack_coarse_rect,
    attack_point_screen,
    bodies_blocked,
    check_attack_vs_hurt,
    depth_close,
    expand_body_box,
    positions_depth_close,
    resolve_arena_movement,
    resolve_combat_hits,
)
from pymoonstone.sim.types import EntityId, Pos25D, Rect2D


def test_depth_band_gate_accepts_within_threshold_and_rejects_front_back() -> None:
    assert DEFAULT_DEPTH_THRESHOLD == 10
    assert depth_close(100, 100)
    assert depth_close(100, 110)
    assert depth_close(110, 100)
    assert not depth_close(100, 111)
    assert not depth_close(111, 100)

    attacker = Pos25D(120, 100, z=12)
    in_band = Pos25D(125, 110, z=0)
    too_far_front = Pos25D(125, 111, z=0)
    too_far_back = Pos25D(125, 89, z=0)

    assert positions_depth_close(attacker, in_band)
    assert not positions_depth_close(attacker, too_far_front)
    assert not positions_depth_close(attacker, too_far_back)


def test_cel_mask_from_bitplanes_msb_first_and_tight_bounds() -> None:
    # 10x3 cel -> row_bytes = 2 (16-bit word aligned).
    # Plane 0 has pixel (1, 0), plane 1 has pixel (9, 2).
    plane0 = bytes([0b0100_0000, 0x00, 0x00, 0x00, 0x00, 0x00])
    plane1 = bytes([0x00, 0x00, 0x00, 0x00, 0x00, 0b0100_0000])
    mask = CelMask.from_bitplanes(10, 3, (plane0, plane1))

    assert mask.test_pixel(1, 0)
    assert not mask.test_pixel(0, 0)
    assert mask.test_pixel(9, 2)
    assert not mask.test_pixel(9, 1)
    assert not mask.test_pixel(-1, 0)
    assert not mask.test_pixel(10, 0)

    # Flipped horizontally: x=1 -> x=8, x=9 -> x=0
    assert mask.test_pixel(8, 0, flipped=True)
    assert not mask.test_pixel(1, 0, flipped=True)
    assert mask.test_pixel(0, 2, flipped=True)

    assert mask.outer_bounds(50, 60) == Rect2D(50, 60, 10, 3)
    assert mask.tight_bounds(50, 60) == Rect2D(51, 60, 9, 3)
    assert mask.tight_bounds(50, 60, flipped=True) == Rect2D(50, 60, 9, 3)


def test_cel_mask_from_bitplanes_rejects_short_buffer() -> None:
    with pytest.raises(ValueError, match="Bitplane buffer too short"):
        CelMask.from_bitplanes(8, 2, (b"\x00\x00",))


def test_cel_mask_empty_and_all_transparent() -> None:
    empty = CelMask.empty()
    assert empty.outer_bounds(10, 20) == Rect2D(10, 20, 0, 0)
    assert empty.tight_bounds(10, 20) == Rect2D(10, 20, 0, 0)

    blank = CelMask.from_pattern(("....", "...."))
    assert blank.tight_bounds(10, 20) == Rect2D(10, 20, 0, 0)


def test_attack_point_vs_bitplane_mask_hit_and_miss() -> None:
    # Defender has an L-shaped opaque mask: transparent at (1, 0), opaque at (0, 1) & (1, 1).
    mask = CelMask.from_pattern(
        (
            "#...",
            "##..",
        )
    )
    hurt = HurtEntry(
        entity_id=EntityId(2),
        pos=Pos25D(100, 90),
        screen_x=100,
        screen_y=70,
        mask=mask,
    )

    # Point lands on transparent pixel (101, 70) -> local (1, 0) -> miss.
    miss_attack = AttackEntry(
        entity_id=EntityId(1),
        pos=Pos25D(90, 90),
        screen_x=95,
        screen_y=68,
        cel_width=16,
        cel_height=16,
        pattern=AttackPattern.from_points(((6, 2),)),
    )
    assert check_attack_vs_hurt(miss_attack, hurt) is None

    # First point misses transparent (1, 0), second point hits opaque (1, 1) at (101, 71).
    hit_attack = AttackEntry(
        entity_id=EntityId(1),
        pos=Pos25D(90, 95),
        screen_x=95,
        screen_y=68,
        cel_width=16,
        cel_height=16,
        pattern=AttackPattern.from_points(((6, 2), (6, 3)), hit_type=1),
    )
    result = check_attack_vs_hurt(hit_attack, hurt)
    assert result is not None
    assert result.attacker_id == EntityId(1)
    assert result.defender_id == EntityId(2)
    assert (result.hit_x, result.hit_y) == (101, 71)
    assert result.point_index == 1
    assert result.hit_type == 1


def test_depth_rejection_and_draw_flags_gate_hits() -> None:
    mask = CelMask.from_pattern(("##", "##"))
    hurt = HurtEntry(
        entity_id=EntityId(2),
        pos=Pos25D(100, 100),
        screen_x=100,
        screen_y=80,
        mask=mask,
        draw_flags=DRAW_FLAG_HURTBOX,
    )
    attack = AttackEntry(
        entity_id=EntityId(1),
        pos=Pos25D(95, 111),  # |111 - 100| = 11 > 10
        screen_x=98,
        screen_y=80,
        cel_width=8,
        cel_height=8,
        pattern=AttackPattern.from_points(((2, 0),)),
        draw_flags=DRAW_FLAG_ATTACK,
    )
    assert check_attack_vs_hurt(attack, hurt) is None

    # Move into depth band, but disable hurtbox flag -> still no hit.
    in_depth_attack = AttackEntry(
        entity_id=EntityId(1),
        pos=Pos25D(95, 110),
        screen_x=98,
        screen_y=80,
        cel_width=8,
        cel_height=8,
        pattern=AttackPattern.from_points(((2, 0),)),
        draw_flags=DRAW_FLAG_ATTACK,
    )
    inactive_hurt = HurtEntry(
        entity_id=EntityId(2),
        pos=Pos25D(100, 100),
        screen_x=100,
        screen_y=80,
        mask=mask,
        draw_flags=0,
    )
    assert check_attack_vs_hurt(in_depth_attack, inactive_hurt) is None

    # Self-hit is also rejected.
    self_hurt = HurtEntry(
        entity_id=EntityId(1),
        pos=Pos25D(100, 100),
        screen_x=100,
        screen_y=80,
        mask=mask,
    )
    assert check_attack_vs_hurt(in_depth_attack, self_hurt) is None


def test_mirrored_attacker_and_mirrored_defender() -> None:
    # Attacker cel width=20, attack point at x=16, y=4 -> mirrored x = 20 - 16 = 4.
    pattern = AttackPattern.from_points(((16, 4), (18, 4)))
    mirrored_attack = AttackEntry(
        entity_id=EntityId(1),
        pos=Pos25D(120, 100),
        screen_x=100,
        screen_y=80,
        cel_width=20,
        cel_height=12,
        pattern=pattern,
        flipped=True,
    )
    assert attack_coarse_rect(mirrored_attack) == Rect2D(102, 80, 19, 5)
    assert attack_point_screen(mirrored_attack, AttackPoint(16, 4)) == (104, 84)

    # Defender cel width=4 at screen_x=101, screen_y=84, opaque at unflipped x=0 -> flipped x=3.
    # Screen x=104 -> local_x = 3 -> hits when defender is flipped, misses when unflipped.
    def_mask = CelMask.from_pattern(("#...",))
    flipped_hurt = HurtEntry(
        entity_id=EntityId(2),
        pos=Pos25D(102, 100),
        screen_x=101,
        screen_y=84,
        mask=def_mask,
        flipped=True,
    )
    unflipped_hurt = HurtEntry(
        entity_id=EntityId(2),
        pos=Pos25D(102, 100),
        screen_x=101,
        screen_y=84,
        mask=def_mask,
        flipped=False,
    )

    hit = check_attack_vs_hurt(mirrored_attack, flipped_hurt)
    assert hit is not None
    assert (hit.hit_x, hit.hit_y) == (104, 84)
    assert check_attack_vs_hurt(mirrored_attack, unflipped_hurt) is None


def test_multi_target_hits_and_pair_deduplication() -> None:
    mask = CelMask.from_pattern(("###", "###"))
    attack = AttackEntry(
        entity_id=EntityId(1),
        pos=Pos25D(100, 100),
        screen_x=100,
        screen_y=80,
        cel_width=10,
        cel_height=10,
        pattern=AttackPattern.from_points(((2, 1),)),
    )
    hurts = (
        HurtEntry(EntityId(2), Pos25D(102, 95), 101, 80, mask),
        HurtEntry(EntityId(2), Pos25D(102, 95), 102, 80, mask),  # second cel for same defender
        HurtEntry(EntityId(3), Pos25D(102, 108), 100, 80, mask),  # second defender in depth band
        HurtEntry(EntityId(4), Pos25D(102, 120), 100, 80, mask),  # out of depth band
    )

    hits = resolve_combat_hits((attack,), hurts)
    assert [h.defender_id for h in hits] == [EntityId(2), EntityId(3)]


def test_body_box_expansion_and_blocking() -> None:
    box = expand_body_box(None, Rect2D(10, 20, 8, 12), DRAW_FLAG_HURTBOX)
    assert box == Rect2D(10, 20, 8, 12)

    # Flag 0x40 skips body box expansion.
    unchanged = expand_body_box(box, Rect2D(0, 0, 50, 50), DRAW_FLAG_NO_BODY_BOX)
    assert unchanged == box

    expanded = expand_body_box(box, Rect2D(15, 25, 10, 10), 0)
    assert expanded == Rect2D(10, 20, 15, 15)

    assert expanded is not None
    assert bodies_blocked(expanded, Pos25D(10, 100), Rect2D(20, 30, 5, 5), Pos25D(20, 105))
    assert not bodies_blocked(expanded, Pos25D(10, 100), Rect2D(20, 30, 5, 5), Pos25D(20, 115))


def test_arena_obstacle_blocking_and_boundary_clamping() -> None:
    wall = ObstacleSegment(x1=120, x2=100, y=80)  # intentionally reversed x1/x2
    assert wall.min_x == 100
    assert wall.max_x == 120

    # Vertical crossing blocked when x is within [100, 120].
    blocked_down = resolve_arena_movement(Pos25D(110, 75, z=4), dx=0, dy=10, obstacles=(wall,))
    assert blocked_down == Pos25D(110, 79, z=4)

    blocked_up = resolve_arena_movement(Pos25D(110, 85, z=0), dx=0, dy=-10, obstacles=(wall,))
    assert blocked_up == Pos25D(110, 81, z=0)

    # Vertical movement allowed when x is outside [100, 120].
    free_down = resolve_arena_movement(Pos25D(95, 75), dx=0, dy=10, obstacles=(wall,))
    assert free_down == Pos25D(95, 85)

    # Horizontal entry blocked when |depth - wall.y| <= 6.
    blocked_right = resolve_arena_movement(Pos25D(95, 86), dx=10, dy=0, obstacles=(wall,))
    assert blocked_right == Pos25D(99, 86)

    blocked_left = resolve_arena_movement(Pos25D(125, 74), dx=-10, dy=0, obstacles=(wall,))
    assert blocked_left == Pos25D(121, 74)

    # Horizontal movement allowed when |depth - wall.y| > 6.
    free_right = resolve_arena_movement(Pos25D(95, 87), dx=10, dy=0, obstacles=(wall,))
    assert free_right == Pos25D(105, 87)

    # Arena outer boundary clamping.
    bounds = ArenaBounds()
    clamped_min = resolve_arena_movement(
        Pos25D(15, 35, z=3), dx=-20, dy=-20, obstacles=(), bounds=bounds
    )
    assert clamped_min == Pos25D(ARENA_MIN_X, ARENA_MIN_Y, z=3)

    clamped_max = resolve_arena_movement(
        Pos25D(310, 150), dx=30, dy=30, obstacles=(), bounds=bounds
    )
    assert clamped_max == Pos25D(ARENA_MAX_X, ARENA_MAX_Y)
