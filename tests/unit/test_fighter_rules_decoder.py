"""The checked reads of the knight-rule decoder on synthetic code (ARCHITECTURE.md §10.19)."""

from __future__ import annotations

import struct
import zlib

import pytest

from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.fighter_rules import _operand, _relocated, _rule_of, _rule_table
from harddaysengine.assets.hunk import HUNK_CODE, HUNK_DATA, DecodedHunkExecutable, HunkSection
from harddaysengine.assets.mog_code import BSS_HUNK, CODE_HUNK, DATA_HUNK, MogCode, StoreRun
from harddaysengine.sim.fighter_rules import HitRule

_CODE = struct.pack(
    ">3H 3H 4H 4H 3H H 3H",
    *(0x0469, 0x0005, 0x0050),  # 0: SUBI.W #5,$50(A1)
    *(0x207C, 0x0000, 0x0040),  # 6: MOVEA.L #$40,A0 (hunk 4)
    *(0x41F9, 0x0000, 0x0010, 0x217C),  # 12: LEA $10,A0 (hunk 1); 18: MOVE.L ...
    *(0x0000, 0x0030, 0x0004, 0x4E75),  # ... #$30,4(A0) (hunk 0); 26: RTS
    *(0x45F9, 0x0000, 0x0010),  # 28: LEA $10,A2 (hunk 1)
    0x4E75,  # 34: RTS
    *(0x45F9, 0x0000, 0x0014),  # 36: LEA $14,A2 (hunk 1)
)
_CODE += bytes(0x30 - len(_CODE)) + struct.pack(">H", 0x0C28)  # rule routine entry at $30
_RELOCS = {DATA_HUNK: (8,), BSS_HUNK: (14, 30, 38), CODE_HUNK: (20,)}


def _section(index: int, data: bytes, relocs: dict[int, tuple[int, ...]]) -> HunkSection:
    kind = HUNK_CODE if index == 0 else HUNK_DATA
    return HunkSection(index=index, hunk_type=kind, alloc_size=len(data), data=data, relocs=relocs)


def _mog() -> MogCode:
    sections = (_section(0, _CODE, _RELOCS), *(_section(i, bytes(64), {}) for i in range(1, 5)))
    return MogCode(DecodedHunkExecutable("mog", sections), supported=(zlib.crc32(_CODE),))


def test_an_operand_is_read_after_its_instruction_word_and_displacement() -> None:
    mog = _mog()
    assert _operand(mog, (0, 0x0469, 0x50), "t") == 5
    with pytest.raises(MogVersionError, match=r"expected \$0440"):
        _operand(mog, (0, 0x0440, None), "t")
    with pytest.raises(MogVersionError, match="displacement"):
        _operand(mog, (0, 0x0469, 0x52), "t")


def test_a_relocated_operand_must_point_into_the_expected_hunk() -> None:
    mog = _mog()
    assert _relocated(mog, 6, 0x207C, DATA_HUNK, "t") == 0x40
    with pytest.raises(MogVersionError, match="does not point into hunk 0"):
        _relocated(mog, 6, 0x207C, CODE_HUNK, "t")


def test_rule_tables_come_from_the_fill_and_name_rules_by_entry() -> None:
    mog = _mog()
    run = StoreRun(mog)
    run.run(12, 26, "fill")
    entries = {0x30: (0x0C28, HitRule.CARRY_ON)}
    assert _rule_table(mog, run, (12, 28), 0x10, entries, "t") == {4: HitRule.CARRY_ON}
    with pytest.raises(MogVersionError, match="reads another table"):
        _rule_table(mog, run, (12, 36), 0x10, entries, "t")
    with pytest.raises(MogVersionError, match="unknown rule routine"):
        _rule_of(mog, 0x32, entries, "t")
    with pytest.raises(MogVersionError, match=r"expected \$4E75"):
        _rule_of(mog, 0x30, {0x30: (0x4E75, HitRule.CARRY_ON)}, "t")
