"""Checked meeting/protection and dragon-hoard caller operands (§12.40)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.mog_code import MogCode, Pointer
from harddaysengine.assets.stonehenge_data import _fade
from harddaysengine.assets.turn_rules import text_list
from harddaysengine.assets.valley_data import _call
from harddaysengine.assets.wizard_screen_data import _string
from harddaysengine.sim.meeting import MeetingRules

if TYPE_CHECKING:
    from harddaysengine.assets.hunk import DecodedHunkExecutable
    from harddaysengine.sim.turn_rules import TextLine


@dataclass(slots=True, frozen=True)
class MeetingData:
    rules: MeetingRules
    message: tuple[TextLine, ...]
    name_row: int
    picture: str
    font: str
    recolor: tuple[int, ...]
    style: int


def _rules(mog: MogCode) -> MeetingRules:
    for pos, target in (
        (0xB40, Pointer(0, 0x3C12)),
        (0xB46, Pointer(0, 0x73C)),
        (0xBA2, Pointer(0, 0xABEC)),
        (0xBC4, Pointer(36, 0)),
        (0xBE8, Pointer(0, 0xABEC)),
        (0xC42, Pointer(0, 0x2FEA)),
        (0xC48, Pointer(0, 0x1FD0)),
        (0xC72, Pointer(0, 0xABEC)),
        (0x1248, Pointer(0, 0xABEC)),
    ):
        _call(mog, pos, target)
    mog.expect(0xC0A, (0x0CA9,), "meeting computer kind")
    mog.expect(0xC10, (54,), "meeting computer field")
    slot = mog.operand(0xC20, 0x4A28, "meeting protection inventory field")
    if mog.operand(0xC82, 0x0C79, "meeting last protection slot") != slot:
        raise MogVersionError("mog: meeting protection slots disagree")
    if slot % 2 or not 0 <= slot < 20:
        raise MogVersionError("mog: meeting protection outside usable inventory")
    mog.expect(0xB9C, (0x203C,), "meeting loot sheet")
    mog.expect(0xBE2, (0x203C,), "meeting mutual-defeat sheet")
    mog.expect(0xC70, (0x7009,), "meeting defender temple")
    mog.expect(0x1246, (0x700A,), "dragon hoard sheet")
    if mog.operand(0xAC02, 0x33FC, "nested sheet change reset") != 0:
        raise MogVersionError("mog: nested sheet changes are not reset")
    if mog.operand(0xAD5E, 0x4A28, "meeting loser lives") != 73:
        raise MogVersionError("mog: unexpected meeting loser lives field")
    mog.expect(0xAD62, (0x6E08,), "meeting signed loser-life branch")
    loot, temple = mog.long(0xB9E), mog.long(0xBE4)
    if (loot, temple) != (1, 9):
        raise MogVersionError("mog: unexpected meeting sheet numbers")
    period, wait = _fade(mog)
    return MeetingRules(
        mog.long(0xC0C),
        slot,
        mog.operand(0xBF2, 0x0668, "computer meeting progress", displacement=78),
        loot,
        temple,
        mog.word(0x1246) & 255,
        period,
        wait,
    )


def _name_row(mog: MogCode, start: Pointer, count: int) -> int:
    name = mog.lea(0xC28, 0x47F9, "protection name destination")
    pointer = start
    for row in range(count):
        if mog.pointer(pointer.offset, "protection text", pointer.hunk) == name:
            return row
        if row + 1 < count:
            pointer = mog.pointer(pointer.offset + 10, "next protection row", pointer.hunk)
    raise MogVersionError("mog: protection name is not in its message")


def _read_data(mog: MogCode) -> MeetingData:
    rules = _rules(mog)
    pointer = mog.lea(0xC3C, 0x41F9, "protection message")
    lines = text_list(mog, pointer, "protection message", hunk=4)
    shades = tuple(
        mog.operand(p, 0x30FC, "meeting message shade") for p in range(0x301E, 0x3032, 4)
    )
    shades += (mog.operand(0x3032, 0x30BC, "meeting last shade"),)
    if any(word > 4095 for word in shades):
        raise MogVersionError("mog: meeting message shade outside RGB12")
    style = mog.operand(0x9350, 0x002A, "meeting bold-font style", displacement=9)
    if style != 8:
        raise MogVersionError("mog: unexpected meeting bold-font style")
    return MeetingData(
        rules,
        lines,
        _name_row(mog, pointer, len(lines)),
        _string(mog, mog.lea(0x2BFA, 0x41F9, "meeting picture")),
        _string(mog, mog.lea(0x2C36, 0x41F9, "meeting font")),
        shades,
        style,
    )


def parse_meeting_data(executable: DecodedHunkExecutable) -> MeetingData:
    return _read_data(MogCode(executable))
