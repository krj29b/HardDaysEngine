"""Read checked Mudmen handler operands from mog hunk 40 (ARCHITECTURE.md §10.25)."""

from __future__ import annotations

from dataclasses import fields
from typing import TYPE_CHECKING

from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.mog_code import DATA_HUNK, OP_LEA_A0, MogCode, Pointer, StoreRun
from harddaysengine.sim.fight_ai.snatcher import SNATCHER_TYPE, SnatcherRules, SnatcherScripts

if TYPE_CHECKING:
    from harddaysengine.assets.hunk import DecodedHunkExecutable

HUNK = 40
_OPERANDS = {
    "up": (0x1B0, 0x33FC, None),
    "down": (0x1BC, 0x33FC, None),
    "walk_range": (0x98, 0xB27C, None),
    "strike_range": (0xA0, 0xB27C, None),
    "reach_range": (0xA8, 0xB27C, None),
    "appear_offset": (0x1F8, 0x303C, None),
    "appear_left": (0x210, 0x303C, None),
    "appear_side": (0x202, 0x0C68, 4),
    "facing_left": (0x1FC, 0x117C, 10),
    "facing_right": (0x20A, 0x117C, 10),
    "grab_min": (0x268, 0xB07C, None),
    "grab_max": (0x26E, 0xB07C, None),
    "release_damage": (0x39E, 0x0468, 80),
    "hold_timer": (0x3D6, 0x117C, 13),
    "hurt_timer": (0x3F0, 0x117C, 13),
}
_SCRIPTS = (0x1D2, 0x218, 0x234, 0x282, 0x2A0, 0x2D8, 0x304, 0x33A, 0x3FA)
_HELPERS = (
    (0x7A, 40, 0x1130),
    (0x90, 0, 0x6F48),
    (0xBA, 40, 0x1252),
    (0x108, 0, 0x8508),
    (0x1C6, 0, 0x6EE6),
    (0x256, 0, 0x6EE6),
    (0x262, 0, 0x6F48),
    (0x276, 0, 0x7862),
    (0x27C, 0, 0x3F4),
    (0x2C2, 0, 0x1FE6),
    (0x32A, 0, 0x3F4),
    (0x34A, 0, 0x7874),
    (0x382, 0, 0x7862),
    (0x392, 0, 0x777C),
    (0x3B8, 0, 0x7862),
    (0x3E8, 0, 0x59A8),
)


def _pointer(mog: MogCode, pos: int, opcode: int, target: int) -> int:
    if mog.hunk_word(HUNK, pos) != opcode:
        raise MogVersionError(f"mog: unexpected snatcher instruction at {pos}")
    pointer = mog.pointer(pos + 2, "snatcher", HUNK)
    if pointer.hunk != target:
        raise MogVersionError(f"mog: unexpected snatcher pointer hunk at {pos}")
    return pointer.offset


def _entry(mog: MogCode) -> None:
    table = mog.lea(0x4C76, OP_LEA_A0, "snatcher handler")
    run = StoreRun(mog)
    run.run(0x4C76, 0x4D04, "snatcher handler", terminator=OP_LEA_A0)
    cell = run.memory.get(Pointer(table.hunk, table.offset + SNATCHER_TYPE))
    if cell is None or (cell.target, cell.value) != (HUNK, 0):
        raise MogVersionError("mog: Mudmen type names another handler")
    if mog.hunk_word(HUNK, 0) != 0x23C8:
        raise MogVersionError("mog: unexpected snatcher entry instruction")
    for pos, target_hunk, offset in _HELPERS:
        if _pointer(mog, pos, 0x4EB9, target_hunk) != offset:
            raise MogVersionError(f"mog: unexpected snatcher helper at {pos}")


def parse_snatcher_rules(executable: DecodedHunkExecutable) -> SnatcherRules:
    """Validate version, handler identity, operands and relocations (§10.25)."""
    mog = MogCode(executable)
    _entry(mog)
    values = {
        name: mog.operand(pos, opcode, "snatcher", displacement=disp, hunk=HUNK)
        for name, (pos, opcode, disp) in _OPERANDS.items()
    }
    scripts = {
        field.name: _pointer(mog, pos, 0x23FC, DATA_HUNK)
        for field, pos in zip(fields(SnatcherScripts), _SCRIPTS, strict=True)
    }
    table = _pointer(mog, 0x180, 0x43F9, HUNK)
    if _pointer(mog, 0x198, 0x43F9, HUNK) != table:
        raise MogVersionError("mog: snatcher left/right step tables differ")
    return SnatcherRules(
        SnatcherScripts(**scripts),
        tuple(mog.hunk_word(HUNK, table + 2 * i) for i in range(8)),
        **values,
    )
