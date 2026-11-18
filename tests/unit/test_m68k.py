"""Tests for 68000 integer semantics (sim/m68k.py)."""

from __future__ import annotations

import pytest

from harddaysengine.sim.m68k import (
    DivResult,
    c_div,
    c_mod,
    divs_w,
    divu_w,
    muls_w,
    mulu_w,
    s8,
    s16,
    s32,
    u8,
    u16,
    u32,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, 0), (0x7F, 127), (0x80, -128), (0xFF, -1), (0x100, 0), (-1, -1), (-129, 127)],
)
def test_s8(value: int, expected: int) -> None:
    assert s8(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0x7FFF, 32767), (0x8000, -32768), (0xFFFF, -1), (0x1_0000, 0), (32768, -32768)],
)
def test_s16(value: int, expected: int) -> None:
    assert s16(value) == expected


def test_s32_and_unsigned_wraps() -> None:
    assert s32(0x8000_0000) == -(2**31)
    assert s32(0x1_0000_0001) == 1
    assert u8(-1) == 0xFF
    assert u16(-1) == 0xFFFF
    assert u32(-1) == 0xFFFF_FFFF
    assert u16(0x12345) == 0x2345


@pytest.mark.parametrize(
    ("num", "den", "quot", "rem"),
    [(7, 2, 3, 1), (-7, 2, -3, -1), (7, -2, -3, 1), (-7, -2, 3, -1), (0, 5, 0, 0)],
)
def test_c_div_truncates_toward_zero(num: int, den: int, quot: int, rem: int) -> None:
    assert c_div(num, den) == quot
    assert c_mod(num, den) == rem
    assert c_div(num, den) * den + c_mod(num, den) == num


def test_c_div_by_zero_raises() -> None:
    with pytest.raises(ZeroDivisionError):
        c_div(1, 0)


def test_divs_w_signed_operands_and_remainder_sign() -> None:
    assert divs_w(-7, 2) == DivResult(-3, -1)
    # divisor is a word: 0xFFFE is -2
    assert divs_w(7, 0xFFFE) == DivResult(-3, 1)
    # dividend is a long: 0xFFFFFFF9 is -7
    assert divs_w(0xFFFF_FFF9, 2) == DivResult(-3, -1)


def test_divs_w_overflow_returns_none() -> None:
    assert divs_w(0x10000, 1) is None
    assert divs_w(-0x8000, 1) == DivResult(-0x8000, 0)
    assert divs_w(0x8000, 1) is None


def test_divu_w() -> None:
    assert divu_w(100, 7) == DivResult(14, 2)
    assert divu_w(0xFFFF_FFFF, 0xFFFF) is None
    assert divu_w(0xFFFE_0001, 0xFFFF) == DivResult(0xFFFF, 0)
    with pytest.raises(ZeroDivisionError):
        divu_w(1, 0x1_0000)


def test_divs_w_by_zero_raises() -> None:
    with pytest.raises(ZeroDivisionError):
        divs_w(1, 0x1_0000)


def test_multiplies_use_word_operands() -> None:
    assert muls_w(0xFFFF, 2) == -2
    assert muls_w(-32768, -32768) == 0x4000_0000
    assert mulu_w(0xFFFF, 0xFFFF) == 0xFFFE_0001
    assert mulu_w(0x1_0002, 3) == 6
