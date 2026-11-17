"""Read the original DIW shake operands and sentinel table (ARCHITECTURE.md §10.27)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pymoonstone.assets.fight_palette import MogVersionError
from pymoonstone.assets.mog_code import CODE_HUNK, MogCode
from pymoonstone.sim.m68k import s16
from pymoonstone.sim.screen_fx import ShakeRules

if TYPE_CHECKING:
    from pymoonstone.assets.hunk import DecodedHunkExecutable


def shake_rules(mog: MogCode) -> ShakeRules:
    """Read the relocated code-hunk table and both agreeing reload operands (§10.27)."""
    period = mog.operand(0x90D0, 0x33FC, "shake wait")
    if mog.operand(0x90D8, 0x33FC, "shake reload") != period:
        raise MogVersionError("mog: screen-shake wait and reload disagree")
    table = mog.relocated(0x90E0, 0x23FC, CODE_HUNK, "shake table")
    offsets: list[int] = []
    for pos in range(table, 0x917C, 2):
        word = mog.word(pos)
        if word == 0xFFFF:
            return ShakeRules(period, tuple(offsets))
        if word & 0xFF:
            raise MogVersionError("mog: screen-shake table contains a horizontal displacement")
        offsets.append(s16(word) >> 8)
    raise MogVersionError("mog: screen-shake table has no terminator before the next routine")


def parse_shake_rules(executable: DecodedHunkExecutable) -> ShakeRules:
    """Accept only the identified original executable (§10.27)."""
    return shake_rules(MogCode(executable))
