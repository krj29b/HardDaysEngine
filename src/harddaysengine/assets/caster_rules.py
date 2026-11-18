"""Checked Ratmen operands and flight helpers (ARCHITECTURE.md §10.23)."""

from __future__ import annotations

from dataclasses import fields
from typing import TYPE_CHECKING

from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.mog_code import (
    CODE_HUNK,
    DATA_HUNK,
    OP_LEA_A0,
    OP_LEA_A1,
    MogCode,
    Pointer,
    StoreRun,
)
from harddaysengine.sim.fight_ai.caster import CASTER_TYPE, CasterRules, CasterScripts
from harddaysengine.sim.trajectory import FlightRules

if TYPE_CHECKING:
    from harddaysengine.assets.hunk import DecodedHunkExecutable

# Positions and instruction words only; values come from the user's executable.
_OPERANDS: dict[str, tuple[int, int, int | None]] = {
    "strike8_range": (0x5F90, 0xB07C, None),
    "strike4_range": (0x5FAA, 0xB07C, None),
    "initial_wait": (0x6004, 0x337C, 0x6A),
    "initial_height": (0x6020, 0x377C, 8),
    "initial_depth": (0x6038, 0x066B, 0xC),
    "initial_steps": (0x603E, 0x377C, 0x10),
    "initial_arc": (0x6044, 0x377C, 0x12),
    "minimum_steps": (0x6066, 0xB07C, None),
    "quick_range": (0x6080, 0x0C79, None),
    "quick_arc": (0x6090, 0x377C, 0x12),
    "airborne_height": (0x6110, 0x0C68, 6),
    "hold_range": (0x614C, 0xB07C, None),
    "throw_wait": (0x628C, 0x337C, 0x6A),
    "throw_height": (0x62A0, 0x0658, None),
    "throw_x": (0x62A8, 0x0658, None),
    "throw_steps": (0x62B4, 0x30FC, None),
    "throw_arc": (0x62B8, 0x30FC, None),
    "displayed_height": (0x62D8, 0x337C, 6),
    "throw_damage": (0x6310, 0x0469, 0x50),
    "squeeze_damage": (0x6206, 0x0468, 0x50),
    "dead_hp": (0x63AC, 0x337C, 0x50),
    "shared_wait": (0x64A6, 0x33FC, None),
    "endurance_bonus": (0x6512, 0x0600, None),
    "object_x": (0x43A6, 0x303C, None),
    "object_height": (0x43B0, 0x0441, None),
}
_SCRIPT_POSITIONS = (
    0x5F9C,
    0x5FB6,
    0x60C4,
    0x62DE,
    0x6152,
    0x6160,
    0x6230,
    0x6244,
    0x626A,
    0x6196,
    0x61D4,
    0x61E8,
    0x61F6,
    0x620E,
    0x6318,
    0x6396,
    0x6426,
    0x6434,
    0x64C4,
    0x64D6,
    0x654A,
)
_FLIGHT_OPERANDS = (
    (0x73D4, 0xB07C),
    (0x7180, 0x0C79),
    (0x718A, 0x33FC),
    (0x7192, 0x0C79),
    (0x719C, 0x33FC),
    (0x71A4, 0x33FC),
)
_HELPERS = (
    (0x60A6, 40, 0x1252, 0xDF28),
    (0x60AC, 40, 0x1278, 0x7000),
    (0x6058, 0, 0x70E6, 0x2279),
    (0x604A, 0, 0x7388, 0x7E05),
    (0x60D8, 0, 0x747E, 0x7E05),
)


def _entry(mog: MogCode) -> None:
    what = "caster handler"
    table = mog.lea(0x78F8, OP_LEA_A1, what)
    if mog.lea(0x4C76, OP_LEA_A0, what) != table:
        raise MogVersionError("mog: caster handler table fill targets another table")
    run = StoreRun(mog)
    run.run(0x4C76, 0x4D04, what, terminator=OP_LEA_A0)
    cell = run.memory.get(Pointer(table.hunk, table.offset + CASTER_TYPE))
    if cell is None or cell.target != CODE_HUNK or cell.value != 0x5EC2:
        raise MogVersionError("mog: Ratmen type names another handler")
    mog.expect(0x5EC2, (0x23E8,), what)
    for call, hunk, entry, opcode in _HELPERS:
        if mog.call_target(call, what) != Pointer(hunk, entry):
            raise MogVersionError(f"mog: caster helper call at {call} targets another routine")
        if mog.hunk_word(hunk, entry) != opcode:
            raise MogVersionError(f"mog: caster helper at {entry} has another entry instruction")


def _moveq(mog: MogCode, pos: int, register: int) -> int:
    word = mog.word(pos)
    if word & 0xFF00 != 0x7000 | (register << 9):
        raise MogVersionError(f"mog: expected caster MOVEQ at {pos}")
    return word & 0xFF


def _flight(mog: MogCode) -> FlightRules:
    count = _moveq(mog, 0x7388, 7)
    if _moveq(mog, 0x747E, 7) != count:
        raise MogVersionError("mog: trajectory start and step disagree on pool size")
    values = [mog.operand(pos, opcode, "trajectory") for pos, opcode in _FLIGHT_OPERANDS]
    return FlightRules(count + 1, *values)


def parse_caster_rules(executable: DecodedHunkExecutable) -> CasterRules:
    """Read the Ratmen rules after checking the supported CRC, instructions and pointers."""
    mog = MogCode(executable)
    _entry(mog)
    values = {
        name: mog.operand(pos, opcode, "caster", displacement=disp)
        for name, (pos, opcode, disp) in _OPERANDS.items()
    }
    if mog.operand(0x606C, 0x303C, "caster") != values["minimum_steps"]:
        raise MogVersionError("mog: caster minimum step operands disagree")
    if mog.operand(0x644A, 0x337C, "caster", displacement=0x50) != values["dead_hp"]:
        raise MogVersionError("mog: caster death markers disagree")
    scripts = {
        field.name: mog.relocated(
            pos,
            0x207C if field.name in ("victim_released", "victim_fall") else 0x23FC,
            DATA_HUNK,
            "caster script",
        )
        for field, pos in zip(fields(CasterScripts), _SCRIPT_POSITIONS, strict=True)
    }
    return CasterRules(
        scripts=CasterScripts(**scripts),
        flight=_flight(mog),
        object_facing=_moveq(mog, 0x43BA, 3),
        **values,
    )
