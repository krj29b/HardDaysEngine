"""Checked Flyer instructions, table and scripts (ARCHITECTURE.md §10.26)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.mog_code import (
    CODE_HUNK,
    DATA_HUNK,
    OP_LEA_A0,
    OP_LEA_A1,
    OP_LEA_A2,
    MogCode,
    Pointer,
    StoreRun,
)
from harddaysengine.sim.fight_ai.flyer import FLYER_TYPE, FlyerRules

if TYPE_CHECKING:
    from harddaysengine.assets.hunk import DecodedHunkExecutable

_OPERANDS: dict[str, tuple[int, int, int | None]] = {
    "right_edge": (0x5ABA, 0x0C69, 4),
    "enter_right": (0x5AC4, 0x337C, 4),
    "enter_left": (0x5ADA, 0x337C, 4),
    "facing_left": (0x5ACA, 0x137C, 10),
    "facing_right": (0x5AE0, 0x137C, 10),
    "toggle_xor": (0x5AE6, 0x0A79, None),
    "pause_mask": (0x5B16, 0x0240, None),
    "pause_or": (0x5B1A, 0x0040, None),
    "pause_step": (0x5B22, 0x0429, 13),
    "phase_step": (0x5B2C, 0x0629, 12),
    "phase_count": (0x5B32, 0x0C29, 12),
    "phase_reset": (0x5B3A, 0x137C, 12),
}
# Helper identity and entry word, including the original PRNG and outcome routines.
_HELPERS = (
    (0x5B10, 0xA2B0, 0x48E7),
    (0x5B8C, 0x59A8, 0x48E7),
    (0x5BD6, 0x7874, 0x6100),
    (0x5BDC, 0x03F4, 0x2F08),
    (0x5BE2, 0x0380, 0x45F9),
)


def _entry(mog: MogCode) -> None:
    what = "Flyer handler"
    table = mog.lea(0x78F8, OP_LEA_A1, what)
    if mog.lea(0x4C76, OP_LEA_A0, what) != table:
        raise MogVersionError("mog: Flyer handler table fill targets another table")
    run = StoreRun(mog)
    run.run(0x4C76, 0x4D04, what, terminator=OP_LEA_A0)
    cell = run.memory.get(Pointer(table.hunk, table.offset + FLYER_TYPE))
    if cell is None or (cell.target, cell.value) != (CODE_HUNK, 0x5A76):
        raise MogVersionError("mog: Flyer type names another handler")
    mog.expect(0x5A76, (0x2248,), what)
    for call, entry, opcode in _HELPERS:
        if mog.call_target(call, what) != Pointer(CODE_HUNK, entry):
            raise MogVersionError(f"mog: unexpected Flyer helper at {call}")
        mog.expect(entry, (opcode,), what)
    # Hardware register access is deliberately replaced by an integer tick sample (§10.26).
    mog.expect(0x5AFC, (0x3239,), what)
    if mog.long(0x5AFE) != 0xDFF006:
        raise MogVersionError("mog: Flyer reads another raster register")


def _tables(mog: MogCode, phases: int) -> tuple[int, tuple[int, ...]]:
    what = "Flyer steps"
    toggle = mog.pointer(0x5AEA, what)
    steps = mog.lea(0x5B62, OP_LEA_A2, what)
    if toggle.hunk != CODE_HUNK or steps != Pointer(CODE_HUNK, toggle.offset + 2):
        raise MogVersionError("mog: Flyer alternation word is not before its step table")
    if phases <= 0 or steps.offset + phases * 2 != 0x5C28:
        raise MogVersionError("mog: Flyer step table has an unexpected extent")
    return mog.word(toggle.offset), tuple(mog.word(steps.offset + i * 2) for i in range(phases))


def parse_flyer_rules(executable: DecodedHunkExecutable) -> FlyerRules:
    """Decode the Flyer only from the supported executable and identified instructions (§10.26)."""
    mog = MogCode(executable)
    _entry(mog)
    values = {
        name: mog.operand(pos, opcode, "Flyer", displacement=disp)
        for name, (pos, opcode, disp) in _OPERANDS.items()
    }
    initial, steps = _tables(mog, values["phase_count"])
    mog.expect(0x5B02, (0x0281,), "Flyer beam mask")
    shift = mog.expect(0x5B08, (0xE549,), "Flyer beam shift")
    return FlyerRules(
        steps=steps,
        finish_same=mog.relocated(0x5C02, 0x23FC, DATA_HUNK, "Flyer finish same"),
        finish_opposite=mog.relocated(0x5BF2, 0x23FC, DATA_HUNK, "Flyer finish opposite"),
        initial_toggle=initial,
        beam_mask=mog.long(0x5B04),
        beam_shift=(shift >> 9) & 7,
        **values,
    )
