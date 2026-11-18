"""Checked computer knight operands and step data (ARCHITECTURE.md §10.31)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.mog_code import OP_LEA_A0, MogCode, Pointer, StoreRun
from harddaysengine.sim.fight_ai.approach import ApproachRules
from harddaysengine.sim.fight_ai.knight import COMPUTER_KNIGHT_TYPE, ComputerKnightRules

if TYPE_CHECKING:
    from harddaysengine.assets.hunk import DecodedHunkExecutable

HUNK = 40
_RANGES = {
    "strike8_range": 0xE96,
    "heavy_range": 0xEBA,
    "strike4_range": 0xEDE,
    "guard_range": 0xE04,
}


def _pointer(mog: MogCode, pos: int, opcode: int, target: int) -> Pointer:
    if mog.hunk_word(HUNK, pos) != opcode:
        raise MogVersionError(f"mog: unexpected computer knight instruction at {pos}")
    pointer = mog.pointer(pos + 2, "computer knight", HUNK)
    if pointer.hunk != target:
        raise MogVersionError(f"mog: unexpected computer knight pointer at {pos}")
    return pointer


def _entry(mog: MogCode) -> None:
    table = mog.lea(0x4C76, OP_LEA_A0, "computer knight handler")
    run = StoreRun(mog)
    run.run(0x4C76, 0x4D04, "computer knight handler", terminator=OP_LEA_A0)
    cell = run.memory.get(Pointer(table.hunk, table.offset + COMPUTER_KNIGHT_TYPE))
    if cell is None or (cell.target, cell.value) != (HUNK, 0xCAA):
        raise MogVersionError("mog: computer knight type names another handler")
    if mog.hunk_word(HUNK, 0xCAA) != 0x23C8:
        raise MogVersionError("mog: unexpected computer knight entry")
    if mog.pointer(0xCF8, "computer knight last action", HUNK) != Pointer(41, 52):
        raise MogVersionError("mog: unexpected computer knight last-action cell")
    for call, hunk, entry, word in (
        (0xCFC, 40, 0x1130, 0x33FC),
        (0xD12, 0, 0x6F48, 0x48E7),
        (0xDC4, 0, 0xA2D6, 0x4EB9),
        (0xE52, 0, 0xA2D6, 0x4EB9),
        (0xE6C, 0, 0xA2B0, 0x48E7),
        (0xF3A, 0, 0x53FC, 0x48E7),
        (0xF82, 0, 0x59A8, 0x48E7),
    ):
        if _pointer(mog, call, 0x4EB9, hunk).offset != entry or mog.hunk_word(hunk, entry) != word:
            raise MogVersionError(f"mog: unexpected computer knight helper at {call}")


def _steps(mog: MogCode) -> list[tuple[int, ...]]:
    pointers = [_pointer(mog, pos, 0x43F9, 41) for pos in (0xD5C, 0xD38, 0xD4A)]
    if (
        tuple(p.offset for p in pointers) != (0, 20, 36)
        or _pointer(mog, 0xD6E, 0x43F9, 41) != pointers[0]
    ):
        raise MogVersionError("mog: unexpected computer knight step layout")
    return [tuple(mog.hunk_word(p.hunk, p.offset + i * 2) for i in range(16)) for p in pointers]


def _rules(mog: MogCode) -> ComputerKnightRules:
    values = {
        name: mog.operand(
            pos, 0xB27C if name != "guard_range" else 0xB07C, "computer knight range", hunk=HUNK
        )
        for name, pos in _RANGES.items()
    }
    for pos, name, opcode in ((0xD94, "strike8_range", 0xB27C), (0xE36, "guard_range", 0xB07C)):
        if mog.operand(pos, opcode, "computer knight paired range", hunk=HUNK) != values[name]:
            raise MogVersionError("mog: computer knight ranges disagree")
    steps = _steps(mog)
    odds = _pointer(mog, 0xDD2, 0x4BF9, 4)
    if _pointer(mog, 0xE60, 0x4BF9, 4) != odds:
        raise MogVersionError("mog: computer knight odds pointers disagree")
    return ComputerKnightRules(
        steps=ApproachRules(
            side=steps[0],
            up=steps[1],
            down=steps[2],
            up_override=mog.operand(0x1072, 0x33FC, "computer knight up override", hunk=HUNK),
            down_override=mog.operand(0x1094, 0x33FC, "computer knight down override", hunk=HUNK),
        ),
        odds=mog.hunk_data(odds.hunk)[odds.offset :],
        **values,
    )


def parse_computer_knight_rules(executable: DecodedHunkExecutable) -> ComputerKnightRules:
    """Validate handler identity and helpers before reading rule data (§10.31)."""
    mog = MogCode(executable)
    _entry(mog)
    return _rules(mog)
