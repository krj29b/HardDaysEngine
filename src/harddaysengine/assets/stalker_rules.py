"""Read checked Troll and shared hit-spark instructions (ARCHITECTURE.md §10.27)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.mog_code import (
    BSS_HUNK,
    CODE_HUNK,
    DATA_HUNK,
    OP_LEA_A0,
    MogCode,
    Pointer,
    StoreRun,
)
from harddaysengine.sim.fight_ai.effects import HitSparkRules
from harddaysengine.sim.fight_ai.stalker import STALKER_TYPE, StalkerRules
from harddaysengine.sim.m68k import s16

if TYPE_CHECKING:
    from harddaysengine.assets.hunk import DecodedHunkExecutable

HUNK = 40
_HELPERS = (
    (0x51E, 0x4EB9, HUNK, 0x1130, 0x33FC),
    (0x550, 0x4EB9, HUNK, 0x121E, 0x2079),
    (0x56C, 0x4EB9, HUNK, 0x121E, 0x2079),
    (0x57E, 0x4EB9, HUNK, 0x121E, 0x2079),
    (0x5A8, 0x4EB9, HUNK, 0x121E, 0x2079),
    (0x55E, 0x6000, HUNK, 0x10A0, 0x2079),
    (0x568, 0x6700, HUNK, 0x10A0, 0x2079),
    (0x57A, 0x6000, HUNK, 0x10A0, 0x2079),
    (0x5CC, 0x4EB9, CODE_HUNK, 0x6F48, 0x48E7),
    (0x63C, 0x4EB9, CODE_HUNK, 0x59A8, 0x48E7),
    (0x64C, 0x6100, HUNK, 0x1298, 0x2F08),
    (0x12D6, 0x4EF9, CODE_HUNK, 0x7084, 0x4EB9),
)


def _pointer(mog: MogCode, pos: int, opcode: int, target: int) -> int:
    if mog.hunk_word(HUNK, pos) != opcode:
        raise MogVersionError(f"mog: unexpected stalker instruction at {pos}")
    pointer = mog.pointer(pos + 2, "stalker", HUNK)
    if pointer.hunk != target:
        raise MogVersionError(f"mog: unexpected stalker pointer hunk at {pos}")
    return pointer.offset


def _entry(mog: MogCode) -> None:
    table = mog.lea(0x4C76, OP_LEA_A0, "stalker handler")
    run = StoreRun(mog)
    run.run(0x4C76, 0x4D04, "stalker handler", terminator=OP_LEA_A0)
    cell = run.memory.get(Pointer(table.hunk, table.offset + STALKER_TYPE))
    if cell is None or (cell.target, cell.value) != (HUNK, 0x4E0):
        raise MogVersionError("mog: Troll type names another handler")
    if mog.hunk_word(HUNK, 0x4E0) != 0x23C8:
        raise MogVersionError("mog: unexpected stalker entry instruction")
    for pos, opcode, hunk, entry, word in _HELPERS:
        if opcode in (0x6000, 0x6100, 0x6700):
            actual = Pointer(HUNK, pos + 2 + s16(mog.operand(pos, opcode, "stalker", hunk=HUNK)))
        else:
            actual = Pointer(hunk, _pointer(mog, pos, opcode, hunk))
        if actual != Pointer(hunk, entry) or mog.hunk_word(hunk, entry) != word:
            raise MogVersionError(f"mog: unexpected stalker helper at {pos}")


def _steps(mog: MogCode) -> tuple[int, ...]:
    table = _pointer(mog, 0x58A, 0x43F9, HUNK)
    if _pointer(mog, 0x5B4, 0x43F9, HUNK) != table:
        raise MogVersionError("mog: stalker left and right step tables differ")
    return tuple(mog.hunk_word(HUNK, table + i * 2) for i in range(16))


def _spark(mog: MogCode, frame_lists: tuple[int, ...]) -> HitSparkRules:
    frame = _pointer(mog, 0x12BE, 0x247C, BSS_HUNK)
    if frame not in frame_lists:
        raise MogVersionError("mog: hit spark names an unknown frame list")
    idle = _pointer(mog, 0x12A0, 0x217C, CODE_HUNK)
    if idle != 0x70CA or mog.word(idle) != 0x23C8:
        raise MogVersionError("mog: hit spark installs another idle handler")
    if mog.hunk_word(HUNK, 0x12D0) != 0x2A3C:
        raise MogVersionError("mog: hit spark type instruction differs")
    kind = int.from_bytes(mog.hunk_data(HUNK)[0x12D2:0x12D6], "big")
    if kind != mog.hunk_word(HUNK, 0x12A6):
        raise MogVersionError("mog: hit spark handler slot and type disagree")
    return HitSparkRules(
        _pointer(mog, 0x12C4, 0x207C, DATA_HUNK), frame_lists.index(frame) + 1, kind
    )


def parse_stalker_rules(
    executable: DecodedHunkExecutable, frame_lists: tuple[int, ...]
) -> StalkerRules:
    """Read fixed scripts, step data and spark after checking instruction identities (§10.27)."""
    mog = MogCode(executable)
    _entry(mog)
    return StalkerRules(
        steps=_steps(mog),
        up=mog.operand(0x556, 0x33FC, "stalker up", hunk=HUNK),
        down=mog.operand(0x572, 0x33FC, "stalker down", hunk=HUNK),
        strike_near=mog.operand(0x5D8, 0xB07C, "stalker near", hunk=HUNK),
        strike_far=mog.operand(0x5DE, 0xB07C, "stalker far", hunk=HUNK),
        strike_script=_pointer(mog, 0x5EA, 0x23FC, DATA_HUNK),
        heavy_script=_pointer(mog, 0x61A, 0x23FC, DATA_HUNK),
        hurt_script=_pointer(mog, 0x650, 0x23FC, DATA_HUNK),
        hop_table=_pointer(mog, 0x5FC, 0x23FC, DATA_HUNK),
        spark=_spark(mog, frame_lists),
    )
