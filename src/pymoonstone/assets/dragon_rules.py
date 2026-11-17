"""Checked Dragon handler operands, fixed scripts and phase requests (§10.30)."""

from __future__ import annotations

from dataclasses import fields
from typing import TYPE_CHECKING

from pymoonstone.assets.fight_palette import MogVersionError
from pymoonstone.assets.mog_code import (
    BSS_HUNK,
    CODE_HUNK,
    DATA_HUNK,
    OP_LEA_A0,
    MogCode,
    Pointer,
    StoreRun,
)
from pymoonstone.sim.fight_ai.dragon import (
    DRAGON_TYPE,
    PART_TYPE,
    DragonFlight,
    DragonRules,
    DragonScripts,
)

if TYPE_CHECKING:
    from pymoonstone.assets.hunk import DecodedHunkExecutable
    from pymoonstone.sim.fight_ai.effects import HitSparkRules
    from pymoonstone.sim.trajectory import FlightRules

_OPERANDS: dict[str, tuple[int, int, int | None]] = {
    "phase_range": (0x65C0, 0xB07C, None),
    "breath_range": (0x6758, 0x0C79, None),
    "step": (0x68E4, 0x0641, None),
    "minimum_x": (0x6900, 0xB27C, None),
    "maximum_x": (0x68E8, 0xB27C, None),
    "reduced_reach": (0x68B8, 0x317C, 116),
    "reduced_close": (0x68BE, 0x317C, 118),
    "dagger_damage": (0x6848, 0x0469, 80),
    "link_offset": (0x6A6E, 0x0640, None),
    "link_gap": (0x6A76, 0x0440, None),
    "fire_x": (0x6994, 0x0640, None),
    "fire_y": (0x698E, 0x0642, None),
    "fire_height": (0x6986, 0x323C, None),
    "part_limit": (0x6A00, 0x0C68, 4),
    "part_action": (0x6A08, 0x337C, 64),
}
_SCRIPT_POSITIONS = (0x67B8, 0x6772, 0x67A4, 0x6874, 0x6976, 0x6A0E, 0x69E0)
_HELPERS = (
    (0x65B4, 0x4EB9, 0, 0x6F48, 0x48E7),
    (0x6630, 0x4EB9, 0, 0x7388, 0x7E05),
    (0x66DC, 0x4EB9, 0, 0x747E, 0x7E05),
    (0x680A, 0x4EB9, 0, 0x59A8, 0x48E7),
    (0x682A, 0x4EB9, 40, 0x1298, 0x2F08),
    (0x686E, 0x4EB9, 0, 0x7874, 0x6100),
    (0x68C4, 0x4EB9, 40, 0x1130, 0x33FC),
    (0x69F6, 0x4EB9, 0, 0x6EE6, 0x48E7),
    (0x65B0, 0x6100, 0, 0x6882, 0x207C),
    (0x6784, 0x6100, 0, 0x6962, 0x41F9),
    (0x699E, 0x6100, 0, 0x7084, 0x4EB9),
)


def _entry(mog: MogCode) -> None:
    table = mog.lea(0x4C76, OP_LEA_A0, "Dragon handlers")
    run = StoreRun(mog)
    run.run(0x4C76, 0x4D04, "Dragon handlers", terminator=OP_LEA_A0)
    for kind, offset in ((DRAGON_TYPE, 0x6574), (PART_TYPE, 0x69A4)):
        cell = run.memory.get(Pointer(table.hunk, table.offset + kind))
        if cell is None or (cell.target, cell.value) != (CODE_HUNK, offset):
            raise MogVersionError("mog: Dragon type names another handler")
        mog.expect(offset, (0x23E8,), "Dragon handler entry")
    for call, opcode, hunk, offset, word in _HELPERS:
        actual = (
            mog.call_target(call, "Dragon helper")
            if opcode == 0x4EB9
            else Pointer(CODE_HUNK, mog.branch_target(call, opcode, "Dragon helper"))
        )
        if actual != Pointer(hunk, offset) or mog.hunk_word(hunk, offset) != word:
            raise MogVersionError(f"mog: unexpected Dragon helper at {call}")


def _plan(mog: MogCode, *, far: bool) -> DragonFlight:
    block, step, x, height, repeat, arc = (
        (0x6660, 0x667A, 0x668E, 0x6696, 0x669A, 0x669E)
        if far
        else (0x65EE, 0x6608, 0x661C, 0x6624, 0x6628, 0x662C)
    )
    steps = mog.operand(step, 0x337C, "Dragon flight steps", displacement=106)
    if mog.operand(repeat, 0x30FC, "Dragon request steps") != steps:
        raise MogVersionError("mog: Dragon flight step operands disagree")
    return DragonFlight(
        block=mog.operand(block, 0x23E8, "Dragon flight walk block"),
        steps=steps,
        x=mog.operand(x, 0x30FC, "Dragon target x"),
        height=mog.operand(height, 0x30FC, "Dragon target height"),
        arc=mog.operand(arc, 0x30FC, "Dragon arc"),
    )


def _fire(mog: MogCode, frame_lists: tuple[int, ...]) -> tuple[int, int, int]:
    frame = mog.relocated(0x697C, 0x247C, BSS_HUNK, "Dragon fire frame list")
    if frame not in frame_lists:
        raise MogVersionError("mog: Dragon fire uses an unknown frame list")
    idle = mog.relocated(0x6968, 0x217C, CODE_HUNK, "Dragon fire idle handler")
    if idle != 0x70CA or mog.word(idle) != 0x23C8:
        raise MogVersionError("mog: Dragon fire uses another idle handler")
    mog.expect(0x6998, (0x2A3C,), "Dragon fire type")
    kind = mog.long(0x699A)
    if mog.word(0x696E) != kind:
        raise MogVersionError("mog: Dragon fire type and handler slot disagree")
    facing = mog.word(0x6992)
    if facing & 0xFF00 != 0x7600:
        raise MogVersionError("mog: Dragon fire facing is not a MOVEQ")
    return frame_lists.index(frame) + 1, kind, facing & 0xFF


def _rules(
    mog: MogCode, flight: FlightRules, spark: HitSparkRules, frame_lists: tuple[int, ...]
) -> DragonRules:
    values = {
        name: mog.operand(pos, op, "Dragon", displacement=disp)
        for name, (pos, op, disp) in _OPERANDS.items()
    }
    for pos, opcode in (
        (0x68FC, 0x0441),
        (0x6910, 0x0468),
        (0x691C, 0x0668),
        (0x66CC, 0x0469),
        (0x66D4, 0x0669),
    ):
        if mog.operand(pos, opcode, "Dragon paired step") != values["step"]:
            raise MogVersionError("mog: Dragon paired steps disagree")
    scripts = {
        field.name: mog.relocated(
            pos, 0x207C if field.name == "fire" else 0x23FC, DATA_HUNK, "Dragon script"
        )
        for field, pos in zip(fields(DragonScripts), _SCRIPT_POSITIONS, strict=True)
    }
    fire_list, fire_kind, fire_facing = _fire(mog, frame_lists)
    return DragonRules(
        scripts=DragonScripts(**scripts),
        near=_plan(mog, far=False),
        far=_plan(mog, far=True),
        flight=flight,
        spark=spark,
        fire_list=fire_list,
        fire_kind=fire_kind,
        fire_facing=fire_facing,
        hop_table=mog.relocated(0x6A42, 0x23FC, DATA_HUNK, "Dragon object hop table"),
        sounds=(
            mog.operand(0x6D28, 0x303C, "Dragon sound 0"),
            mog.operand(0x6D32, 0x303C, "Dragon sound 1"),
        ),
        **values,
    )


def parse_dragon_rules(
    executable: DecodedHunkExecutable,
    flight: FlightRules,
    spark: HitSparkRules,
    frame_lists: tuple[int, ...],
) -> DragonRules:
    """Validate both handlers and their helpers, then read executable data (§10.30)."""
    mog = MogCode(executable)
    _entry(mog)
    return _rules(mog, flight, spark, frame_lists)
