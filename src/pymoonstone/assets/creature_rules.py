"""Creature handler numbers and scripts read from ``mog`` (ARCHITECTURE.md §10.20).

Like :mod:`pymoonstone.assets.fighter_rules` this module holds only *positions* in the supported
``mog`` version. Every value is read from the user's file: an operand after its instruction word
(compared), a relocated long after its instruction word, a table through its relocated ``LEA``.
The handler is identified through the handler-table entries of its types, and every helper it
calls by its call site and its first instruction word.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pymoonstone.assets.fight_palette import MogVersionError
from pymoonstone.assets.mog_code import (
    CODE_HUNK,
    DATA_HUNK,
    OP_LEA_A0,
    OP_LEA_A1,
    MogCode,
    Pointer,
    StoreRun,
)
from pymoonstone.sim.fight_ai.approach import STEP_TABLE_WORDS, ApproachRules
from pymoonstone.sim.fight_ai.brawler import BRAWLER_TYPES, BrawlerRules

if TYPE_CHECKING:
    from pymoonstone.assets.hunk import DecodedHunkExecutable

HELPER_HUNK = 40

# The handler table: its fill routine (LEA to the next LEA) and the job pass's LEA (§10.18).
_HANDLER_FILL = (0x4C76, 0x4D04)
_HANDLER_TABLE_LEA = 0x78F8
_BRAWLER_ENTRY = (0x5C28, 0x23C8)

# Instruction words.
_CMP_W_D0 = 0xB07C
_CMP_W_D1 = 0xB27C
_MOVE_B_D16_A0 = 0x117C
_MOVE_B_D16_A1 = 0x137C
_MOVE_W_D16_A0 = 0x317C
_CMPI_W_D16_A2 = 0x0C6A
_MOVE_L_ABS = 0x23FC
_MOVE_W_ABS = 0x33FC
_MOVEQ_D0 = 0x7000
_MOVEQ_MASK = 0xFF00
_COOLDOWN = 0x6A
_ACTION = 0x40

# (position, instruction word, displacement or None) of an immediate word.
Operand = tuple[int, int, int | None]

_DOWN_RANGE: Operand = (0x5D10, _CMP_W_D1, None)
_CLOSE_RANGE: Operand = (0x5D76, _CMP_W_D1, None)
_GLOAT_ODDS: Operand = (0x5D82, _CMP_W_D0, None)
_HEAVY_RANGE: Operand = (0x5DAE, _CMP_W_D1, None)
_STRIKE_COOLDOWN: Operand = (0x5D96, _MOVE_B_D16_A0, _COOLDOWN)
_HEAVY_COOLDOWN: Operand = (0x5DB6, _MOVE_B_D16_A0, _COOLDOWN)
_THRUST_COOLDOWN: Operand = (0x5D5C, _MOVE_B_D16_A0, _COOLDOWN)
_AFTER_HIT_COOLDOWN: Operand = (0x5E18, _MOVE_B_D16_A1, _COOLDOWN)
_STRIKE_ACTION: Operand = (0x5D9C, _MOVE_W_D16_A0, _ACTION)
_HEAVY_ACTION: Operand = (0x5DBC, _MOVE_W_D16_A0, _ACTION)
_THRUST_ACTION: Operand = (0x5D62, _MOVE_W_D16_A0, _ACTION)
_DEFEND_ACTION: Operand = (0x5D8E, _CMPI_W_D16_A2, _ACTION)
_DAGGER_DAMAGE = 0x5DE6
_THRUST_SCRIPT = 0x5D68
_FINISH_SCRIPT = 0x5E5C

# Step tables: (LEA position, call position, helper entry, helper's first instruction word).
_UP_STEP = (0x5C98, 0x5C9E, 0x1052, 0x7E01)
_DOWN_STEP = (0x5CAE, 0x5CB4, 0x107C, 0x7E01)
_RIGHT_STEP = (0x5CC2, 0x5CC8, 0x1020, 0x4EB9)
_LEFT_STEP = (0x5CD6, 0x5CDC, 0x0FE8, 0x4EB9)
# (call position, helper entry, first instruction word) of approach and the step.
_APPROACH_CALL = (0x5C5E, 0x1130, 0x33FC)
_STEP_CALL = (0x5CE2, 0x10A0, 0x2079)
# MOVE.W #imm,abs.l of the two depth overrides, in the helper hunk.
_UP_OVERRIDE = 0x1072
_DOWN_OVERRIDE = 0x1094


def _operand(mog: MogCode, operand: Operand, what: str) -> int:
    pos, opcode, displacement = operand
    return mog.operand(pos, opcode, what, displacement=displacement)


def _check_helper(mog: MogCode, call: int, entry: int, opcode: int, what: str) -> None:
    """The call at ``call`` must reach ``entry`` of the helper hunk, starting with ``opcode``."""
    if mog.call_target(call, what) != Pointer(HELPER_HUNK, entry):
        raise MogVersionError(
            f"mog: {what}: the call at code offset {call} reaches another routine"
        )
    found = mog.hunk_word(HELPER_HUNK, entry)
    if found != opcode:
        msg = f"mog: {what}: expected ${opcode:04X} at hunk {HELPER_HUNK} offset {entry}"
        raise MogVersionError(f"{msg}, found ${found:04X}")


def _step_table(mog: MogCode, spec: tuple[int, int, int, int], what: str) -> tuple[int, ...]:
    lea, call, entry, opcode = spec
    pointer = mog.lea(lea, OP_LEA_A1, what)
    if pointer.hunk != CODE_HUNK:
        raise MogVersionError(f"mog: {what}: the table is not in the code hunk")
    _check_helper(mog, call, entry, opcode, what)
    return tuple(mog.word(pointer.offset + 2 * i) for i in range(STEP_TABLE_WORDS))


def _approach(mog: MogCode) -> ApproachRules:
    what = "approach helpers"
    _check_helper(mog, *_APPROACH_CALL, what)
    _check_helper(mog, *_STEP_CALL, what)
    side = _step_table(mog, _RIGHT_STEP, what)
    if _step_table(mog, _LEFT_STEP, what) != side:
        raise MogVersionError("mog: approach helpers: the two side steps read different tables")
    return ApproachRules(
        side=side,
        up=_step_table(mog, _UP_STEP, what),
        down=_step_table(mog, _DOWN_STEP, what),
        up_override=mog.operand(_UP_OVERRIDE, _MOVE_W_ABS, what, hunk=HELPER_HUNK),
        down_override=mog.operand(_DOWN_OVERRIDE, _MOVE_W_ABS, what, hunk=HELPER_HUNK),
    )


def _check_entry(mog: MogCode) -> None:
    """Every brawler type's handler-table entry must name the brawler's entry (§10.18).

    The table is filled at start-up; its fill routine runs as a store run (§10.15).
    """
    what = "brawler handler"
    start, stop = _HANDLER_FILL
    table = mog.lea(_HANDLER_TABLE_LEA, OP_LEA_A1, what)
    if mog.lea(start, OP_LEA_A0, what) != table:
        raise MogVersionError(f"mog: {what}: the fill routine writes another table")
    run = StoreRun(mog)
    run.run(start, stop, what, terminator=OP_LEA_A0)
    entry, opcode = _BRAWLER_ENTRY
    for kind in BRAWLER_TYPES:
        cell = run.memory.get(Pointer(table.hunk, table.offset + kind))
        if cell is None or cell.target != CODE_HUNK or cell.value != entry:
            raise MogVersionError(f"mog: {what}: type ${kind:02X} names another handler")
    mog.expect(entry, (opcode,), what)


def _moveq(mog: MogCode, pos: int, what: str) -> int:
    """The sign-extended byte of ``MOVEQ #imm,D0`` at ``pos``, as a word."""
    word = mog.word(pos)
    if word & _MOVEQ_MASK != _MOVEQ_D0:
        raise MogVersionError(f"mog: {what}: expected MOVEQ #imm,D0 at code offset {pos}")
    return word & 0xFF if word & 0x80 == 0 else (word | _MOVEQ_MASK) & 0xFFFF


def parse_brawler_rules(mog_exec: DecodedHunkExecutable) -> BrawlerRules:
    """Read everything §10.20 needs for the Troggs from the user's ``mog``."""
    mog = MogCode(mog_exec)
    _check_entry(mog)
    what = "brawler"
    return BrawlerRules(
        approach=_approach(mog),
        down_range=_operand(mog, _DOWN_RANGE, what),
        close_range=_operand(mog, _CLOSE_RANGE, what),
        heavy_range=_operand(mog, _HEAVY_RANGE, what),
        gloat_odds=_operand(mog, _GLOAT_ODDS, what),
        strike_cooldown=_operand(mog, _STRIKE_COOLDOWN, what) & 0xFF,
        heavy_cooldown=_operand(mog, _HEAVY_COOLDOWN, what) & 0xFF,
        thrust_cooldown=_operand(mog, _THRUST_COOLDOWN, what) & 0xFF,
        after_hit_cooldown=_operand(mog, _AFTER_HIT_COOLDOWN, what) & 0xFF,
        strike_action=_operand(mog, _STRIKE_ACTION, what),
        heavy_action=_operand(mog, _HEAVY_ACTION, what),
        thrust_action=_operand(mog, _THRUST_ACTION, what),
        defend_action=_operand(mog, _DEFEND_ACTION, what),
        dagger_damage=_moveq(mog, _DAGGER_DAMAGE, what),
        thrust_script=mog.relocated(_THRUST_SCRIPT, _MOVE_L_ABS, DATA_HUNK, what),
        finish_script=mog.relocated(_FINISH_SCRIPT, _MOVE_L_ABS, DATA_HUNK, what),
    )
