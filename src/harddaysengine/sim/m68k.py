"""Motorola 68000 integer semantics for the deterministic simulation.

Python integers are unbounded and ``//`` / ``%`` floor toward negative infinity. The 68000
works on fixed-width registers and its ``DIVS`` truncates toward zero, with the remainder taking
the sign of the dividend. Simulation code uses these helpers wherever the original relies on
width, wrap-around or division behaviour.
"""

from __future__ import annotations

from typing import NamedTuple

_MASK8 = 0xFF
_MASK16 = 0xFFFF
_MASK32 = 0xFFFF_FFFF


class DivResult(NamedTuple):
    """Quotient and remainder of a 68000 word division (both already in range)."""

    quotient: int
    remainder: int


def u8(value: int) -> int:
    """Wrap to an unsigned byte (``.B`` operand)."""
    return value & _MASK8


def u16(value: int) -> int:
    """Wrap to an unsigned word (``.W`` operand)."""
    return value & _MASK16


def u32(value: int) -> int:
    """Wrap to an unsigned long (``.L`` operand)."""
    return value & _MASK32


def s8(value: int) -> int:
    """Wrap to a signed byte (two's complement, -128..127)."""
    value &= _MASK8
    return value - 0x100 if value & 0x80 else value


def s16(value: int) -> int:
    """Wrap to a signed word (-32768..32767), e.g. the result of ``EXT.W`` or a word add."""
    value &= _MASK16
    return value - 0x1_0000 if value & 0x8000 else value


def s32(value: int) -> int:
    """Wrap to a signed long (-2**31..2**31-1)."""
    value &= _MASK32
    return value - 0x1_0000_0000 if value & 0x8000_0000 else value


def c_div(numerator: int, denominator: int) -> int:
    """Integer division truncating toward zero (C / 68000 semantics, unlike Python ``//``)."""
    if denominator == 0:
        raise ZeroDivisionError("c_div by zero")
    quotient = abs(numerator) // abs(denominator)
    return -quotient if (numerator < 0) != (denominator < 0) else quotient


def c_mod(numerator: int, denominator: int) -> int:
    """Remainder matching :func:`c_div`: it takes the sign of the numerator."""
    return numerator - denominator * c_div(numerator, denominator)


def divs_w(dividend: int, divisor: int) -> DivResult | None:
    """``DIVS.W``: signed 32/16 division giving a 16-bit quotient and remainder.

    ``dividend`` is taken as a signed long, ``divisor`` as a signed word. Returns ``None``
    when the quotient does not fit in a signed word: the 68000 then sets V and leaves the
    destination register unchanged, so the caller keeps its old value. Division by zero
    raises ``ZeroDivisionError`` (the hardware takes the zero-divide trap).
    """
    numerator = s32(dividend)
    denominator = s16(divisor)
    quotient = c_div(numerator, denominator)
    if not -0x8000 <= quotient <= 0x7FFF:
        return None
    return DivResult(quotient, c_mod(numerator, denominator))


def divu_w(dividend: int, divisor: int) -> DivResult | None:
    """``DIVU.W``: unsigned 32/16 division giving a 16-bit quotient and remainder.

    Returns ``None`` on quotient overflow (V set, destination unchanged); division by zero
    raises ``ZeroDivisionError``.
    """
    numerator = u32(dividend)
    denominator = u16(divisor)
    if denominator == 0:
        raise ZeroDivisionError("divu_w by zero")
    quotient, remainder = divmod(numerator, denominator)
    if quotient > _MASK16:
        return None
    return DivResult(quotient, remainder)


def muls_w(left: int, right: int) -> int:
    """``MULS.W``: signed 16x16 multiply giving a signed 32-bit product."""
    return s16(left) * s16(right)


def mulu_w(left: int, right: int) -> int:
    """``MULU.W``: unsigned 16x16 multiply giving an unsigned 32-bit product."""
    return u16(left) * u16(right)
