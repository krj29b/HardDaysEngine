"""Checked original Valley caller and dedicated Guardian stage (§12.38)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from harddaysengine.assets.arena import ObstacleSegment
from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.mog_code import MogCode, Pointer
from harddaysengine.assets.stonehenge_data import _fade
from harddaysengine.assets.turn_rules import text_list
from harddaysengine.assets.wizard_screen_data import _loading
from harddaysengine.sim.m68k import s16
from harddaysengine.sim.valley import ValleyRules

if TYPE_CHECKING:
    from harddaysengine.assets.hunk import DecodedHunkExecutable
    from harddaysengine.sim.turn_rules import TextLine
    from harddaysengine.sim.wizard_types import WizardLoading


@dataclass(slots=True, frozen=True)
class ValleyData:
    rules: ValleyRules
    missing: tuple[TextLine, ...]
    victory: tuple[TextLine, ...]
    loading: WizardLoading
    recolor: tuple[int, ...]
    style: int


@dataclass(slots=True, frozen=True)
class ValleyArena:
    picture: int
    obstacles: tuple[ObstacleSegment, ...]


def _call(mog: MogCode, pos: int, target: Pointer, *, hunk: int = 0) -> None:
    if mog.hunk_word(hunk, pos) not in (0x4EB9, 0x4EF9):
        raise MogVersionError("mog: unexpected Valley call instruction")
    if mog.pointer(pos + 2, "Valley helper", hunk) != target:
        raise MogVersionError("mog: unexpected Valley call target")


def valley_arena(mog: MogCode) -> ValleyArena:
    """Packed slot8 is Test picture5; default walls come from hunk12 (§12.38)."""
    _call(mog, 0x4916, Pointer(0, 0x29AC))
    _call(mog, 0x492E, Pointer(12, 0x3DA))
    _call(mog, 0x29AC, Pointer(0, 0x3214))
    mog.expect(0x4908, (0x7003,), "Guardian scene selector")
    if mog.operand(0x3226, 0x2068, "Guardian packed picture slot") != 32:
        raise MogVersionError("mog: unsupported Guardian packed picture slot")
    values = tuple(
        mog.operand(pos, 0x32FC, "Guardian default obstacle", hunk=12)
        for pos in range(0x3E0, 0x3F4, 4)
    )
    if values[0] != 1:
        raise MogVersionError("mog: unsupported Guardian default obstacle count")
    if mog.hunk_word(12, 0x3DA) != 0x2279 or mog.pointer(
        0x3DC, "Guardian obstacle table", 12
    ) != Pointer(14, 0):
        raise MogVersionError("mog: unexpected Guardian default obstacle table")
    floor = mog.operand(0x3F4, 0x33FC, "Guardian floor edge", hunk=12)
    if mog.pointer(0x3F8, "Guardian floor cell", 12) != Pointer(14, 78) or floor != values[3]:
        raise MogVersionError("mog: Guardian default obstacle and floor disagree")
    wall = ObstacleSegment(*(s16(v) for v in values[1:]))
    return ValleyArena(5, (wall,))


def _caller(mog: MogCode) -> None:
    for pos, target in (
        (0x16D8, Pointer(0, 0x2FB4)),
        (0x16E6, Pointer(36, 0x47A)),
        (0x16EC, Pointer(0, 0x4908)),
        (0x16F2, Pointer(0, 0x73C)),
        (0x170E, Pointer(0, 0x99D8)),
        (0x174E, Pointer(0, 0x2FEA)),
        (0x1754, Pointer(0, 0x1FD0)),
        (0x175C, Pointer(34, 0x658)),
        (0x1762, Pointer(36, 0x65E)),
        (0x4910, Pointer(0, 0x2F5A)),
    ):
        _call(mog, pos, target)
    mog.expect(0x16D0, (0x6714,), "Valley exact key match branch")
    mog.expect(0x1726, (0x6706,), "Valley stat floor branch")
    mog.expect(0x175A, (0x700A,), "Valley reward wait")
    for pos, continuation in ((0x16DE, 0x1FD0), (0x16E2, 0x18D6), (0x172E, 0x18C4)):
        opcode = 0x6100 if pos == 0x16DE else 0x6000
        if mog.branch_target(pos, opcode, "Valley continuation") != continuation:
            raise MogVersionError("mog: unexpected Valley continuation target")


def _tail_mask(mog: MogCode) -> int:
    for pos, target in (
        (0x65E, Pointer(20, 0x666)),
        (0x664, Pointer(36, 0x47A)),
        (0x66A, Pointer(0, 0xA2B0)),
    ):
        _call(mog, pos, target, hunk=36)
    mask_hi = mog.operand(0x670, 0x0280, "Valley moonstone mask", hunk=36)
    mask = mog.hunk_word(36, 0x674)
    if mask_hi or mask > 7:
        raise MogVersionError("mog: Valley moonstone mask outside inventory byte")
    if mog.operand(0x680, 0x01E8, "Valley moonstone field", hunk=36) != 22:
        raise MogVersionError("mog: unexpected Valley moonstone field")
    if mog.hunk_word(36, 0x684) != 0x4E75:
        raise MogVersionError("mog: unexpected Valley reward return")
    return mask


def _rules(mog: MogCode) -> ValleyRules:
    _caller(mog)
    mask = _tail_mask(mog)
    period, wait = _fade(mog)
    values = (
        mog.operand(0x16CA, 0x0C28, "Valley keys", displacement=20),
        mog.operand(0x171A, 0x0428, "Valley extra lives", displacement=73),
        mog.operand(0x1720, 0x0C30, "Valley stat floor", displacement=0x1000),
        mog.operand(0x1728, 0x0430, "Valley stat loss", displacement=0x1000),
    )
    clear = mog.operand(0x1742, 0x117C, "Valley keys clear", displacement=20)
    if max(*values, clear) > 255:
        raise MogVersionError("mog: Valley byte immediate outside byte")
    return ValleyRules(
        *values,
        mog.operand(0x1738, 0x0668, "Valley progress", displacement=78),
        clear,
        mog.word(0x175A) & 255,
        mask,
        period,
        wait,
    )


def _read_data(mog: MogCode) -> ValleyData:
    rules = _rules(mog)
    missing = text_list(mog, mog.lea(0x16D2, 0x41F9, "Valley missing keys"), hunk=4)
    victory = text_list(mog, mog.lea(0x1748, 0x41F9, "Valley victory"), hunk=4)
    shades = tuple(mog.operand(p, 0x30FC, "Valley message shade") for p in range(0x301E, 0x3032, 4))
    shades += (mog.operand(0x3032, 0x30BC, "Valley final message shade"),)
    if any(word > 4095 for word in shades):
        raise MogVersionError("mog: Valley message shade outside RGB12")
    style = mog.operand(0x9350, 0x002A, "Valley bold-font style", displacement=9)
    if style != 8:
        raise MogVersionError("mog: unexpected Valley bold-font style")
    return ValleyData(rules, missing, victory, _loading(mog), shades, style)


def parse_valley_data(executable: DecodedHunkExecutable) -> ValleyData:
    return _read_data(MogCode(executable))
