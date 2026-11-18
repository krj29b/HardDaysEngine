"""The original random number generator (ARCHITECTURE.md §11.2).

The reference model below follows the 68000 instruction sequence step by step, with the X flag
of ``ROXR`` kept explicitly, so it shares no code with :class:`harddaysengine.sim.rng.Rng`.
"""

from __future__ import annotations

import pytest

from harddaysengine.sim.rng import Rng

_MASK = 0xFFFFFFFF


def _ror(value: int, count: int) -> int:
    for _ in range(count):
        value = (value >> 1) | ((value & 1) << 31)
    return value


def _roxr(value: int, count: int, extend: int) -> tuple[int, int]:
    for _ in range(count):
        out = value & 1
        value = (value >> 1) | (extend << 31)
        extend = out
    return value, extend


def _reference_draw(state: int) -> int:
    d0, extend = state, 0
    for _ in range(8):
        d1 = _ror(d0, 3) ^ d0
        d1, extend = _roxr(d1, 2, extend)
        d0, extend = _roxr(d0, 1, extend)
    return d0 & _MASK


@pytest.mark.parametrize("seed", [1, 0x12345678, 0xDEADBEEF, 0x80000000, 0xFFFFFFFF, 7])
def test_draws_follow_the_instruction_sequence(seed: int) -> None:
    rng, state = Rng(seed), seed
    for _ in range(50):
        state = _reference_draw(state)
        assert rng.draw() == state
        assert rng.state == state


def test_the_percent_draw_folds_into_0_to_100() -> None:
    rng, state = Rng(0x2468ACE1), 0x2468ACE1
    for _ in range(500):
        state = _reference_draw(state)
        low = state & 0x7F
        expected = low - 27 if low >= 100 else low
        value = rng.percent()
        assert value == expected
        assert 0 <= value <= 100


def test_a_zero_state_stays_zero() -> None:
    assert Rng(0).draw() == 0
