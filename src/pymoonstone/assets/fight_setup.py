"""Read original entry, wave and warning operands from the user's mog (§10.24)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pymoonstone.assets.fight_palette import MogVersionError
from pymoonstone.assets.mog_code import DATA_HUNK, MogCode
from pymoonstone.sim.fight_setup import FightSetupRules

if TYPE_CHECKING:
    from pymoonstone.assets.hunk import DecodedHunkExecutable

_WARNING_ROWS = (0x9C6, 0x9DE, 0xA0E, 0x9F6, 0xA1C)


def _warning_rows(mog: MogCode) -> tuple[tuple[int, ...], ...]:
    return tuple(
        tuple(
            mog.operand(
                start + i * 4,
                mog.expect(start + i * 4, (0x30BC, 0x30FC), "warning colour"),
                "warning colour",
            )
            for i in range(3)
        )
        for start in _WARNING_ROWS
    )


def _same(mog: MogCode, positions: tuple[int, ...], opcode: int, what: str) -> int:
    values = tuple(mog.operand(pos, opcode, what) for pos in positions)
    if len(set(values)) != 1:
        raise MogVersionError(f"mog: {what}: repeated operands disagree")
    return values[0]


def parse_fight_setup(executable: DecodedHunkExecutable) -> FightSetupRules:
    """Check code identity and every instruction before reading its setup operand."""
    mog = MogCode(executable)
    what = "fight setup"
    mog.expect(0x418C, (0x5D40,), "wave class SUBQ")
    mog.expect(0x4198, (0x203C,), "wave class cap")
    if mog.word(0x419A) != 0:
        raise MogVersionError("mog: wave class cap is not a positive word")
    period = _same(mog, (0x8CC, 0x8EA, 0x908, 0x964, 0x982, 0x9A0), 0x343C, "warning period")
    if _same(mog, (0x8D0, 0x8EE, 0x90C, 0x968, 0x986, 0x9A4), 0x363C, "warning repeat"):
        raise MogVersionError("mog: low-health warnings do not repeat indefinitely")
    return FightSetupRules(
        frame_vbls=_same(mog, (0x3E88, 0x3D00), 0x33FC, "fight frame budget"),
        enter_script=mog.relocated(0x4A64, 0x207C, DATA_HUNK, what),
        knight_x=mog.operand(0x4A46, 0x337C, what, displacement=4),
        knight_height=mog.operand(0x4A4C, 0x337C, what, displacement=6),
        knight_facing=mog.operand(0x4A58, 0x137C, what, displacement=10),
        opponent_x=mog.operand(0x3CCE, 0x337C, what, displacement=4),
        opponent_facing=mog.operand(0x3CDA, 0x137C, what, displacement=10),
        lane_bottom=_same(mog, (0x4A74, 0x4A94, 0x4AB4), 0x0440, "lane bottom"),
        lane_offset=_same(mog, (0x4A82, 0x4AA2, 0x4AC8), 0x0440, "lane offset"),
        strength_limit=mog.operand(0x40B6, 0x0C28, what, displacement=0x46),
        hp_limits=(
            mog.operand(0x40C4, 0x0C68, what, displacement=0x54),
            mog.operand(0x40DA, 0x0C68, what, displacement=0x54),
            mog.operand(0x40F0, 0x0C68, what, displacement=0x54),
        ),
        class_base=(mog.word(0x418C) >> 9) & 7,
        class_limit=mog.operand(0x4192, 0xB07C, what),
        class_cap=mog.word(0x419C),
        death_delay=mog.operand(0xA82, 0x157C, what, displacement=0x10),
        warning_hp=_same(mog, (0x8B6, 0x94E), 0xB07C, "warning HP"),
        warning_period=period,
        warning_rows=_warning_rows(mog),
    )
