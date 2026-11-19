"""Checked fixed town welcome messages (ARCHITECTURE.md §12.33)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.mog_code import MogCode, Pointer
from harddaysengine.assets.town_data import _call, _string
from harddaysengine.assets.turn_rules import bold_lines, text_list
from harddaysengine.sim.town_types import TownWelcome

if TYPE_CHECKING:
    from harddaysengine.assets.hunk import DecodedHunkExecutable


def _welcome(mog: MogCode, start: int) -> tuple[Pointer, Pointer, Pointer]:
    mog.expect(start, (0x23FC,), "town welcome name store")
    name = mog.pointer(start + 2, "town welcome name")
    cell = mog.pointer(start + 6, "town welcome name cell")
    root = mog.lea(start + 10, 0x41F9, "town welcome list")
    _call(mog, start + 16, 0x2FB4)
    if (name.hunk, cell.hunk, root.hunk) != (4, 4, 4):
        raise MogVersionError("mog: town welcome pointers outside the data hunk")
    _string(mog, name)
    return name, cell, root


def _message_path(mog: MogCode) -> None:
    if mog.branch_target(0x2FBA, 0x6100, "town message unpack") != 0x3044:
        raise MogVersionError("mog: unexpected town message unpack target")
    mog.expect(0x2C32, (0x2149,), "bold font slot store")
    mog.expect(0x2C34, (0x0010,), "bold font slot offset")
    mog.expect(0x2FCA, (0x2169,), "town message font selection")
    mog.expect(0x2FCC, (0x0010,), "town message font slot")
    mog.expect(0x2FCE, (0x000A,), "town message active font")
    _call(mog, 0x2FD6, 0x9196)
    _call(mog, 0x2FE2, 0x8B7C)
    _call(mog, 0x2E22, 0x8B26)


def _read_welcome(mog: MogCode, *, waterdeep: bool) -> TownWelcome:
    first, second = _welcome(mog, 0x2D96), _welcome(mog, 0x2DD2)
    if first[1:] != second[1:]:
        raise MogVersionError("mog: town welcome loaders disagree on their shared list or cell")
    _message_path(mog)
    name, cell, root = second if waterdeep else first
    lines = text_list(mog, root, "town welcome", text_pointers={cell: name}, hunk=4)
    return TownWelcome(
        _string(mog, mog.lea(0x2BFA, 0x41F9, "message picture filename")),
        _string(mog, mog.lea(0x2C36, 0x41F9, "message font filename")),
        bold_lines(mog, lines),
    )


def parse_town_welcome(executable: DecodedHunkExecutable, *, waterdeep: bool) -> TownWelcome:
    """Apply the checked loader's name substitution without modifying mog (§12.33)."""
    return _read_welcome(MogCode(executable), waterdeep=waterdeep)
