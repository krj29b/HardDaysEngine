"""Checked Guardian handler, scripts and job operands (ARCHITECTURE.md §10.29)."""

from __future__ import annotations

from dataclasses import fields
from typing import TYPE_CHECKING

from pymoonstone.assets.fight_palette import MogVersionError
from pymoonstone.assets.mog_code import DATA_HUNK, OP_LEA_A0, MogCode, Pointer, StoreRun
from pymoonstone.sim.fight_ai.guardian import GUARDIAN_TYPE, GuardianRules, GuardianScripts

if TYPE_CHECKING:
    from pymoonstone.assets.hunk import DecodedHunkExecutable

HUNK = 40
_OPERANDS: dict[str, tuple[int, int, int | None]] = {
    "lunge_range": (0x7F0, 0xB27C, None),
    "strike8_range": (0x824, 0xB27C, None),
    "strike4_range": (0x84E, 0xB27C, None),
    "step": (0x77C, 0x303C, None),
    "lunge_wait": (0x818, 0x117C, 106),
    "strike8_wait": (0x842, 0x117C, 106),
    "strike4_wait": (0x87E, 0x117C, 106),
    "grab5_min": (0x96E, 0xB07C, None),
    "grab5_max": (0x968, 0xB07C, None),
    "grab5_wait": (0x992, 0x117C, 106),
    "grab7_min": (0x9C4, 0xB07C, None),
    "grab7_max": (0x9BE, 0xB07C, None),
    "grab7_wait": (0x9E8, 0x117C, 106),
    "snap_range": (0xB44, 0xB07C, None),
    "snap_offset": (0xB5C, 0x323C, None),
    "hit_wait": (0xB76, 0x117C, 106),
    "grab_damage": (0xBC2, 0x0469, 80),
    "release_offset": (0xC0E, 0x0641, None),
    "release_limit": (0xC18, 0xB27C, None),
    "release_max": (0xC1E, 0x323C, None),
    "release_min": (0xC26, 0x323C, None),
}
_SCRIPT_POSITIONS = (
    0x7FC,
    0x838,
    0x85A,
    0x898,
    0x8F4,
    0x958,
    0x980,
    0x9AE,
    0x9D6,
    0xB22,
    0xA34,
    0x8CC,
    0xC7E,
)
_HELPERS = (
    (0x6C6, 0, 0x783C, 0x2F0E),
    (0x74C, 40, 0x1130, 0x33FC),
    (0x762, 0, 0x6F48, 0x48E7),
    (0xB32, 0, 0x59A8, 0x48E7),
    (0xA4A, 0, 0x77AC, 0x7E09),
    (0xA7C, 0, 0x7874, 0x6100),
    (0xBCA, 0, 0x7862, 0x6100),
    (0xC8A, 0, 0x777C, 0x2009),
)


def _pointer(mog: MogCode, pos: int, opcode: int, target: int) -> int:
    if mog.hunk_word(HUNK, pos) != opcode:
        raise MogVersionError(f"mog: unexpected Guardian instruction at {pos}")
    pointer = mog.pointer(pos + 2, "Guardian", HUNK)
    if pointer.hunk != target:
        raise MogVersionError(f"mog: unexpected Guardian pointer hunk at {pos}")
    return pointer.offset


def _entry(mog: MogCode) -> None:
    table = mog.lea(0x4C76, OP_LEA_A0, "Guardian handler")
    run = StoreRun(mog)
    run.run(0x4C76, 0x4D04, "Guardian handler", terminator=OP_LEA_A0)
    cell = run.memory.get(Pointer(table.hunk, table.offset + GUARDIAN_TYPE))
    if cell is None or (cell.target, cell.value) != (HUNK, 0x6AC):
        raise MogVersionError("mog: Guardian type names another handler")
    if mog.hunk_word(HUNK, 0x6AC) != 0x23C8:
        raise MogVersionError("mog: unexpected Guardian entry")
    for call, hunk, entry, word in _HELPERS:
        if _pointer(mog, call, 0x4EB9, hunk) != entry or mog.hunk_word(hunk, entry) != word:
            raise MogVersionError(f"mog: unexpected Guardian helper at {call}")


def _rules(mog: MogCode) -> GuardianRules:
    values = {
        name: mog.operand(pos, opcode, "Guardian", displacement=disp, hunk=HUNK)
        for name, (pos, opcode, disp) in _OPERANDS.items()
    }
    for pos, opcode, expected in (
        (0x78C, 0x303C, -values["step"] & 0xFFFF),
        (0x798, 0x323C, -values["step"] & 0xFFFF),
        (0x7A8, 0x323C, values["step"]),
        (0xC14, 0x0441, values["release_offset"]),
    ):
        if mog.operand(pos, opcode, "Guardian paired operand", hunk=HUNK) != expected:
            raise MogVersionError("mog: Guardian paired operands disagree")
    scripts = {
        field.name: _pointer(
            mog, pos, 0x207C if pos in (0xA34, 0x8CC, 0xC7E) else 0x23FC, DATA_HUNK
        )
        for field, pos in zip(fields(GuardianScripts), _SCRIPT_POSITIONS, strict=True)
    }
    if _pointer(mog, 0x928, 0x207C, DATA_HUNK) != scripts["release"]:
        raise MogVersionError("mog: Guardian release scripts disagree")
    return GuardianRules(
        scripts=GuardianScripts(**scripts),
        hop_table=_pointer(mog, 0x80E, 0x23FC, DATA_HUNK),
        hop2_table=_pointer(mog, 0x874, 0x23FC, DATA_HUNK),
        sounds=tuple(
            mog.operand(pos, 0x303C, "Guardian sound", hunk=HUNK)
            for pos in (0xA84, 0xA8E, 0xA98, 0xAA2)
        ),
        **values,
    )


def parse_guardian_rules(executable: DecodedHunkExecutable) -> GuardianRules:
    """Validate handler identity and helper calls, then decode original operands (§10.29)."""
    mog = MogCode(executable)
    _entry(mog)
    return _rules(mog)
