"""Synthetic Guardian immediates and relocated scripts prove decoding (§10.29)."""

import struct
import zlib

import pytest

from pymoonstone.assets.fight_palette import MogVersionError
from pymoonstone.assets.guardian_rules import _OPERANDS, _SCRIPT_POSITIONS, HUNK, _rules
from pymoonstone.assets.hunk import HUNK_CODE, HUNK_DATA, DecodedHunkExecutable, HunkSection
from pymoonstone.assets.mog_code import MogCode


def _mog(*, bad_step: bool = False, bad_script: bool = False) -> MogCode:
    code = bytes(64)
    body = bytearray(0xD00)
    for i, (pos, opcode, disp) in enumerate(_OPERANDS.values()):
        struct.pack_into(">HH", body, pos, opcode, i + 11)
        if disp is not None:
            struct.pack_into(">H", body, pos + 4, disp)
    step = 14
    for pos, opcode, value in (
        (0x78C, 0x303C, -step & 65535),
        (0x798, 0x323C, -step & 65535),
        (0x7A8, 0x323C, step + int(bad_step)),
        (0xC14, 0x0441, 28),
    ):
        struct.pack_into(">HH", body, pos, opcode, value)
    relocs = []
    for i, pos in enumerate(_SCRIPT_POSITIONS):
        opcode = 0x207C if pos in (0xA34, 0x8CC, 0xC7E) else 0x23FC
        struct.pack_into(">HI", body, pos, opcode, 1000 + 10 * i)
        relocs.append(pos + 2)
    for pos, target in ((0x928, 1110), (0x80E, 500), (0x874, 600)):
        struct.pack_into(">HI", body, pos, 0x207C if pos == 0x928 else 0x23FC, target)
        relocs.append(pos + 2)
    if bad_script:
        struct.pack_into(">H", body, 0xA34, 0x23FC)
    for i, pos in enumerate((0xA84, 0xA8E, 0xA98, 0xAA2)):
        struct.pack_into(">HH", body, pos, 0x303C, 40 + i)
    sections = [HunkSection(0, HUNK_CODE, len(code), code, {})]
    sections.extend(HunkSection(i, HUNK_DATA, 0, b"", {}) for i in range(1, HUNK))
    sections.append(HunkSection(HUNK, HUNK_CODE, len(body), bytes(body), {4: tuple(relocs)}))
    return MogCode(
        DecodedHunkExecutable("synthetic", tuple(sections)), supported=(zlib.crc32(code),)
    )


def test_decodes_invented_operands_and_script_pointers() -> None:
    rules = _rules(_mog())
    assert rules.lunge_range == 11 and rules.step == 14
    assert rules.grab_damage == 27 and rules.release_offset == 28
    assert rules.scripts.body == 1100 and rules.scripts.release == 1110
    assert rules.hop_table == 500 and rules.hop2_table == 600
    assert rules.sounds == (40, 41, 42, 43)


@pytest.mark.parametrize("corruption", ["step", "script"])
def test_bad_paired_steps_or_wrong_script_instruction_is_rejected(corruption: str) -> None:
    with pytest.raises(MogVersionError):
        _rules(_mog(bad_step=corruption == "step", bad_script=corruption == "script"))
