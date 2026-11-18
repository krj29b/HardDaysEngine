"""Synthetic Balok instructions prove tunables and script pointers are runtime data (§10.28)."""

import struct
import zlib

import pytest

from harddaysengine.assets.drake_rules import _OPERANDS, _SCRIPT_POSITIONS, _rules
from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.hunk import HUNK_CODE, HUNK_DATA, DecodedHunkExecutable, HunkSection
from harddaysengine.assets.mog_code import MogCode
from tests.unit.drake_setup import DRAKE


def _mog(
    *, maximum: int = 16, reload_opcode: int = 0x137C, reload_displacement: int = 13
) -> MogCode:
    code = bytearray(0x6F00)
    for i, (pos, opcode, displacement) in enumerate(_OPERANDS.values()):
        struct.pack_into(">HH", code, pos, opcode, i + 10)
        if displacement is not None:
            struct.pack_into(">H", code, pos + 4, displacement)
    struct.pack_into(">HH", code, 0x6C36, 0x303C, maximum)
    struct.pack_into(">HHH", code, 0x510E, reload_opcode, 29, reload_displacement)
    relocs = []
    for i, pos in enumerate(_SCRIPT_POSITIONS):
        struct.pack_into(">HI", code, pos, 0x207C if pos == 0x6CD6 else 0x23FC, 500 + 6 * i)
        relocs.append(pos + 2)
    struct.pack_into(">HI", code, 0x6BAE, 0x23FC, 900)
    relocs.append(0x6BB0)
    data = bytes(code)
    sections = (
        HunkSection(0, HUNK_CODE, len(data), data, {4: tuple(relocs)}),
        *(HunkSection(i, HUNK_DATA, 1000, bytes(1000), {}) for i in range(1, 5)),
    )
    return MogCode(DecodedHunkExecutable("synthetic", sections), supported=(zlib.crc32(data),))


def test_rule_values_and_special_victim_script_operand_are_read() -> None:
    rules = _rules(_mog(), DRAKE.flight, DRAKE.spark)
    assert rules.close == 10 and rules.maximum_steps == 16
    assert rules.counter_reload == 29 and rules.hop_table == 900
    assert rules.scripts.strike == 500 and rules.scripts.victim_landing == 524
    assert rules.spark is DRAKE.spark and rules.flight is DRAKE.flight


@pytest.mark.parametrize(
    ("maximum", "opcode", "disp"), [(17, 0x137C, 13), (16, 0x317C, 13), (16, 0x137C, 14)]
)
def test_disagreeing_cap_or_wrong_timer_instruction_and_displacement_are_rejected(
    maximum: int, opcode: int, disp: int
) -> None:
    with pytest.raises(MogVersionError):
        _rules(
            _mog(maximum=maximum, reload_opcode=opcode, reload_displacement=disp),
            DRAKE.flight,
            DRAKE.spark,
        )
