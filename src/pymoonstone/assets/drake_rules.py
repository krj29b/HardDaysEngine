"""Checked Balok handler operands and fixed scripts (ARCHITECTURE.md §10.28)."""

from __future__ import annotations

from dataclasses import fields
from typing import TYPE_CHECKING

from pymoonstone.assets.fight_palette import MogVersionError
from pymoonstone.assets.mog_code import CODE_HUNK, DATA_HUNK, OP_LEA_A0, MogCode, Pointer, StoreRun
from pymoonstone.sim.fight_ai.drake import DRAKE_TYPE, DrakeRules, DrakeScripts

if TYPE_CHECKING:
    from pymoonstone.assets.hunk import DecodedHunkExecutable
    from pymoonstone.sim.fight_ai.effects import HitSparkRules
    from pymoonstone.sim.trajectory import FlightRules

_OPERANDS: dict[str, tuple[int, int, int | None]] = {
    "close": (0x6B76, 0xB07C, None),
    "hop_range": (0x6B88, 0xB07C, None),
    "heavy_range": (0x6BBC, 0xB07C, None),
    "wait_range": (0x6BE2, 0xB07C, None),
    "aim_reach": (0x6BEC, 0x3E3C, None),
    "minimum_steps": (0x6BF6, 0x0C79, None),
    "maximum_steps": (0x6C30, 0xB07C, None),
    "landing_height": (0x6CB2, 0xB67C, None),
    "landing_range": (0x6CC4, 0xB07C, None),
    "landing_wait": (0x6CEE, 0x117C, 105),
    "hard_arc": (0x6D0E, 0x0C79, None),
    "release_offset": (0x6E9E, 0x323C, None),
    "toggle_xor": (0x6E08, 0x0A79, None),
    "sound_soft": (0x6D68, 0x303C, None),
    "sound_hard_first": (0x6D52, 0x303C, None),
    "sound_hard_second": (0x6D5C, 0x303C, None),
    "sound_grab_first": (0x6D3C, 0x303C, None),
    "sound_grab_second": (0x6D46, 0x303C, None),
    "counter_step": (0x50FC, 0x0429, 13),
    "counter_reload": (0x510E, 0x137C, 13),
}
_SCRIPT_POSITIONS = (
    0x6B96,
    0x6BC8,
    0x6C5E,
    0x6C98,
    0x6CD6,
    0x6D90,
    0x6DB2,
    0x6DD4,
    0x6DEA,
    0x6E14,
    0x6E32,
)
_HELPERS = (
    (0x6B5C, 0, 0x6EE6, 0x48E7),
    (0x6B70, 0, 0x6F48, 0x48E7),
    (0x6BF0, 0, 0x70E6, 0x2279),
    (0x6C48, 0, 0x7388, 0x7E05),
    (0x6C76, 0, 0x747E, 0x7E05),
    (0x6CBE, 0, 0x6F48, 0x48E7),
    (0x6CDC, 0, 0x777C, 0x2009),
    (0x6CE2, 0, 0x03F4, 0x2F08),
    (0x6D1A, 0, 0x90B8, 0x7002),
    (0x6D7E, 0, 0x59A8, 0x48E7),
    (0x6D8A, 40, 0x1298, 0x2F08),
    (0x6DCE, 0, 0x7862, 0x6100),
    (0x6E6E, 0, 0x7862, 0x6100),
    (0x6E80, 0, 0x777C, 0x2009),
    (0x6E8C, 0, 0x783C, 0x2F0E),
    (0x5104, 0, 0xA2B0, 0x48E7),
)


def _entry(mog: MogCode) -> None:
    what = "drake handler"
    table = mog.lea(0x4C76, OP_LEA_A0, what)
    run = StoreRun(mog)
    run.run(0x4C76, 0x4D04, what, terminator=OP_LEA_A0)
    cell = run.memory.get(Pointer(table.hunk, table.offset + DRAKE_TYPE))
    if cell is None or (cell.target, cell.value) != (CODE_HUNK, 0x6AD6):
        raise MogVersionError("mog: Balok type names another handler")
    mog.expect(0x6AD6, (0x23C8,), what)
    for call, hunk, entry, opcode in _HELPERS:
        if mog.call_target(call, what) != Pointer(hunk, entry):
            raise MogVersionError(f"mog: unexpected drake helper at {call}")
        if mog.hunk_word(hunk, entry) != opcode:
            raise MogVersionError(f"mog: unexpected drake helper entry at {entry}")


def parse_drake_rules(
    executable: DecodedHunkExecutable, flight: FlightRules, spark: HitSparkRules
) -> DrakeRules:
    """Reuse checked shared helper definitions and decode Balok's own operands (§10.28)."""
    mog = MogCode(executable)
    _entry(mog)
    return _rules(mog, flight, spark)


def _rules(mog: MogCode, flight: FlightRules, spark: HitSparkRules) -> DrakeRules:
    """Read operands and scripts from an already version-checked executable (§10.28)."""
    values = {
        name: mog.operand(pos, opcode, "drake", displacement=disp)
        for name, (pos, opcode, disp) in _OPERANDS.items()
    }
    if mog.operand(0x6C36, 0x303C, "drake maximum steps") != values["maximum_steps"]:
        raise MogVersionError("mog: drake step limit operands disagree")
    scripts = {
        field.name: mog.relocated(
            pos, 0x207C if field.name == "victim_landing" else 0x23FC, DATA_HUNK, "drake script"
        )
        for field, pos in zip(fields(DrakeScripts), _SCRIPT_POSITIONS, strict=True)
    }
    return DrakeRules(
        scripts=DrakeScripts(**scripts),
        flight=flight,
        spark=spark,
        hop_table=mog.relocated(0x6BAE, 0x23FC, DATA_HUNK, "drake hop table"),
        **values,
    )
