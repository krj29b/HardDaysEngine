"""Synthetic Dragon code proves operands, phase requests and scripts are decoded (§10.30)."""

import struct
import zlib

import pytest

from harddaysengine.assets.dragon_rules import _OPERANDS, _SCRIPT_POSITIONS, _rules
from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.hunk import HUNK_CODE, HUNK_DATA, DecodedHunkExecutable, HunkSection
from harddaysengine.assets.mog_code import MogCode
from tests.unit.dragon_setup import DRAGON_RULES


def _mog(*, bad_step: bool = False, bad_plan: bool = False, bad_frame: bool = False) -> MogCode:
    code = bytearray(0x7200)
    for index, (pos, opcode, disp) in enumerate(_OPERANDS.values()):
        struct.pack_into(">HH", code, pos, opcode, index + 10)
        if disp is not None:
            struct.pack_into(">H", code, pos + 4, disp)
    for pos, opcode in (
        (0x68FC, 0x0441),
        (0x6910, 0x0468),
        (0x691C, 0x0668),
        (0x66CC, 0x0469),
        (0x66D4, 0x0669),
    ):
        struct.pack_into(">HH", code, pos, opcode, 12 + int(bad_step))
    relocs = []
    for index, pos in enumerate(_SCRIPT_POSITIONS):
        struct.pack_into(">HI", code, pos, 0x207C if pos == 0x6976 else 0x23FC, 500 + index * 10)
        relocs.append(pos + 2)
    for block, step, x, height, repeat, arc, offset in (
        (0x65EE, 0x6608, 0x661C, 0x6624, 0x6628, 0x662C, 32),
        (0x6660, 0x667A, 0x668E, 0x6696, 0x669A, 0x669E, 64),
    ):
        struct.pack_into(">HH", code, block, 0x23E8, offset)
        struct.pack_into(">HHH", code, step, 0x337C, 7, 106)
        for pos, value in ((x, 90), (height, 0xFFF0), (repeat, 7 + int(bad_plan)), (arc, 35)):
            struct.pack_into(">HH", code, pos, 0x30FC, value)
    struct.pack_into(">HI", code, 0x6A42, 0x23FC, 800)
    relocs.append(0x6A44)
    struct.pack_into(">HIH", code, 0x6968, 0x217C, 0x70CA, 40)
    struct.pack_into(">H", code, 0x70CA, 0x23C8)
    struct.pack_into(">HI", code, 0x697C, 0x247C, 201 if bad_frame else 200)
    struct.pack_into(">HI", code, 0x6998, 0x2A3C, 40)
    struct.pack_into(">H", code, 0x6992, 0x7603)
    struct.pack_into(">HH", code, 0x6D28, 0x303C, 71)
    struct.pack_into(">HH", code, 0x6D32, 0x303C, 72)
    data = bytes(code)
    sections = (
        HunkSection(0, HUNK_CODE, len(data), data, {4: tuple(relocs), 0: (0x696A,), 1: (0x697E,)}),
        *(HunkSection(i, HUNK_DATA, 1000, bytes(1000), {}) for i in range(1, 5)),
    )
    return MogCode(DecodedHunkExecutable("synthetic", sections), supported=(zlib.crc32(data),))


def test_reads_invented_tunables_scripts_and_flight_requests() -> None:
    rules = _rules(_mog(), DRAGON_RULES.flight, DRAGON_RULES.spark, (100, 200, 300, 400))
    assert rules.phase_range == 10 and rules.step == 12
    assert rules.near.steps == 7 and rules.far.arc == 35 and rules.near.x == 90
    assert rules.scripts.strike8 == 500 and rules.scripts.part_death == 560
    assert rules.fire_list == 2 and rules.fire_kind == 40 and rules.fire_facing == 3
    assert rules.sounds == (71, 72) and rules.hop_table == 800


@pytest.mark.parametrize("corruption", ["step", "plan", "frame"])
def test_corrupt_paired_operands_or_unknown_frame_list_are_rejected(corruption: str) -> None:
    with pytest.raises(MogVersionError):
        _rules(
            _mog(
                bad_step=corruption == "step",
                bad_plan=corruption == "plan",
                bad_frame=corruption == "frame",
            ),
            DRAGON_RULES.flight,
            DRAGON_RULES.spark,
            (100, 200, 300, 400),
        )
