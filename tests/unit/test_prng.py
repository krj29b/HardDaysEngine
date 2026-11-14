"""Tests for deterministic simulation PRNG and core integer value types."""

from __future__ import annotations

import pytest

from pymoonstone.sim.prng import DEFAULT_SEED, SimPrng
from pymoonstone.sim.types import Direction, EntityId, KnightId, Pos25D, Rect2D, TickCount


@pytest.mark.sim
def test_prng_reproduces_identical_sequence_from_same_seed() -> None:
    a = SimPrng(0xCAFE_BABE)
    b = SimPrng(0xCAFE_BABE)
    seq_a = [a.rand_u16() for _ in range(64)]
    seq_b = [b.rand_u16() for _ in range(64)]
    assert seq_a == seq_b
    assert a == b
    assert all(0 <= v <= 0xFFFF for v in seq_a)


@pytest.mark.sim
def test_prng_seed_wraps_to_u32_and_zero_seed_is_not_stuck() -> None:
    a = SimPrng(-1)
    b = SimPrng(0xFFFF_FFFF)
    assert a.state == 0xFFFF_FFFF
    assert a == b
    assert a.rand_u16() == b.rand_u16()

    zero = SimPrng(0)
    draws = [zero.rand_u16() for _ in range(16)]
    assert len(set(draws)) > 1

    zero.seed(DEFAULT_SEED)
    assert zero.state == DEFAULT_SEED


@pytest.mark.sim
def test_prng_rand_u32_bounds_and_determinism() -> None:
    a = SimPrng(42)
    b = SimPrng(42)
    vals_a = [a.rand_u32() for _ in range(32)]
    vals_b = [b.rand_u32() for _ in range(32)]
    assert vals_a == vals_b
    assert all(0 <= v <= 0xFFFF_FFFF for v in vals_a)


@pytest.mark.sim
@pytest.mark.parametrize(
    ("low", "high"),
    [(0, 1), (0, 10), (-5, 5), (100, 1000), (0, 0x1_0000), (0, 0x2_0000)],
)
def test_prng_rand_range_respects_bounds(low: int, high: int) -> None:
    rng = SimPrng(999)
    values = [rng.rand_range(low, high) for _ in range(200)]
    assert all(low <= v < high for v in values)
    if high - low > 1:
        assert len(set(values)) > 1


@pytest.mark.sim
def test_prng_rand_range_single_arg_and_invalid_ranges() -> None:
    rng = SimPrng(7)
    values = [rng.rand_range(12) for _ in range(100)]
    assert all(0 <= v < 12 for v in values)

    with pytest.raises(ValueError, match="empty range"):
        rng.rand_range(0)
    with pytest.raises(ValueError, match="empty range"):
        rng.rand_range(5, 5)
    with pytest.raises(ValueError, match="empty range"):
        rng.rand_range(10, 3)


@pytest.mark.sim
def test_prng_rand_bool() -> None:
    rng = SimPrng(1234)
    assert all(not rng.rand_bool(0, 10) for _ in range(20))
    assert all(rng.rand_bool(10, 10) for _ in range(20))
    flips = [rng.rand_bool(1, 2) for _ in range(50)]
    assert True in flips and False in flips
    with pytest.raises(ValueError, match="denominator"):
        rng.rand_bool(1, 0)


@pytest.mark.sim
def test_prng_serialization_round_trip_and_clone() -> None:
    rng = SimPrng(0x1357_2468)
    for _ in range(17):
        rng.rand_u16()

    raw = rng.to_bytes()
    assert len(raw) == 4
    restored_bytes = SimPrng.from_bytes(raw)
    restored_dict = SimPrng.from_dict(rng.to_dict())
    cloned = rng.clone()

    assert restored_bytes == rng
    assert restored_dict == rng
    assert cloned == rng
    assert rng != "not a prng"
    assert repr(rng).startswith("SimPrng(state=0x")

    expected = [rng.rand_u16() for _ in range(25)]
    assert [restored_bytes.rand_u16() for _ in range(25)] == expected
    assert [restored_dict.rand_u16() for _ in range(25)] == expected
    assert [cloned.rand_u16() for _ in range(25)] == expected


@pytest.mark.sim
def test_prng_deserialization_validation() -> None:
    with pytest.raises(ValueError, match="expected 4 state bytes"):
        SimPrng.from_bytes(b"\x01\x02")
    with pytest.raises(ValueError, match="missing 'state'"):
        SimPrng.from_dict({})
    with pytest.raises(TypeError, match="expected int state"):
        SimPrng.from_dict({"state": True})


@pytest.mark.sim
def test_direction_and_knight_id_semantics() -> None:
    assert Direction.RIGHT == 1
    assert Direction.LEFT == 3
    assert not Direction.RIGHT.is_left
    assert Direction.LEFT.is_left
    assert Direction.RIGHT.sign == 1
    assert Direction.LEFT.sign == -1
    assert Direction.RIGHT.flipped() is Direction.LEFT
    assert Direction.LEFT.flipped() is Direction.RIGHT

    assert [k.value for k in KnightId] == [0, 1, 2, 3]
    assert EntityId(10) == 10
    assert TickCount(50) == 50


@pytest.mark.sim
def test_pos25d_projection_and_operations() -> None:
    p = Pos25D(120, 80, 15)
    assert p.screen_y == 65
    moved = p.translate(dx=-10, dy=5, dz=-5)
    assert moved == Pos25D(110, 85, 10)
    assert p.depth_distance(moved) == 5
    assert moved.depth_distance(p) == 5


@pytest.mark.sim
def test_rect2d_bounds_contains_and_intersects() -> None:
    r1 = Rect2D.from_bounds(10, 20, 30, 50)
    assert r1 == Rect2D(10, 20, 20, 30)
    assert r1.right == 30
    assert r1.bottom == 50
    assert r1.contains(10, 20)
    assert not r1.contains(30, 20)
    assert not r1.contains(10, 50)

    r2 = Rect2D(25, 40, 10, 15)
    r_edge = Rect2D(30, 20, 10, 10)
    r_empty = Rect2D(15, 25, 0, 10)
    assert r1.intersects(r2)
    assert r2.intersects(r1)
    assert not r1.intersects(r_edge)
    assert not r1.intersects(r_empty)
