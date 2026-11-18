"""Operand reads and store runs on synthetic code (ARCHITECTURE.md §10.15)."""

from __future__ import annotations

import struct
import zlib

import pytest

from harddaysengine.assets.fight_defs import NOT_A_SCRIPT, _script_entry, _wave_reach
from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.hunk import HUNK_CODE, HUNK_DATA, DecodedHunkExecutable, HunkSection
from harddaysengine.assets.mog_code import (
    BSS_HUNK,
    DATA_HUNK,
    OP_LEA_A0,
    Cell,
    MogCode,
    Pointer,
    StoreRun,
)

_CODE = struct.pack(
    ">3H H 3H 2H 3H 3H 3H H H H",
    *(0x41F9, 0x0000, 0x0010),  # 0: LEA $10,A0 (hunk 1)
    0x7E01,  # 6: MOVEQ #1,D7
    *(0x20FC, 0x0000, 0x0020),  # 8: MOVE.L #$20,(A0)+ (hunk 4)
    *(0x51CF, 0xFFF8),  # 14: DBRA D7,8
    *(0x41F9, 0x0000, 0x0018),  # 18: LEA $18,A0 (hunk 1)
    *(0x20BC, 0x0000, 0x0005),  # 24: MOVE.L #5,(A0)
    *(0x337C, 0x1234, 0x0050),  # 30: MOVE.W #$1234,$50(A1)
    0x4E75,  # 36: RTS
    0x4E71,  # 38: NOP (not a store)
    0x4E75,  # 40: RTS
)
_RELOCS = {BSS_HUNK: (2, 20), DATA_HUNK: (10,)}


def _section(index: int, data: bytes, relocs: dict[int, tuple[int, ...]]) -> HunkSection:
    kind = HUNK_CODE if index == 0 else HUNK_DATA
    return HunkSection(index=index, hunk_type=kind, alloc_size=len(data), data=data, relocs=relocs)


def _mog(code: bytes = _CODE) -> MogCode:
    sections = (_section(0, code, _RELOCS), *(_section(i, bytes(64), {}) for i in range(1, 5)))
    executable = DecodedHunkExecutable(name="mog", sections=sections)
    return MogCode(executable, supported=(zlib.crc32(code),))


def test_unknown_version_is_refused_with_the_supported_list() -> None:
    sections = (_section(0, _CODE, _RELOCS), *(_section(i, bytes(4), {}) for i in range(1, 5)))
    with pytest.raises(MogVersionError, match=r"supported: \$00000001"):
        MogCode(DecodedHunkExecutable(name="mog", sections=sections), supported=(1,))


def test_operand_reads_check_the_instruction_word() -> None:
    mog = _mog()
    assert mog.lea(0, OP_LEA_A0, "t") == Pointer(BSS_HUNK, 0x10)
    assert mog.branch_target(14, 0x51CF, "t") == 8
    with pytest.raises(MogVersionError, match="expected \\$4EB9 or \\$4EF9 at code offset 6"):
        mog.call_target(6, "t")
    with pytest.raises(MogVersionError, match="not relocated"):
        mog.pointer(26, "t")


def test_store_run_loops_and_bounds_tables_at_the_next_start() -> None:
    run = StoreRun(_mog())
    run.run(0, 30, "t", terminator=0x337C)

    filled = Cell(0x20, 4, DATA_HUNK)
    # The first table holds two longs: the second LEA marks where the next table starts.
    assert run.longs(Pointer(BSS_HUNK, 0x10), 9) == (filled, filled)
    assert run.longs(Pointer(BSS_HUNK, 0x18), 2) == (Cell(5, 4, None), None)


def test_record_run_writes_through_a1() -> None:
    run = StoreRun(_mog())
    run.run_record(30, 36, "t")
    assert run.record_fields() == {0x50: Cell(0x1234, 2)}


def test_store_run_refuses_other_instructions() -> None:
    with pytest.raises(MogVersionError, match="unsupported instruction \\$4E71 at code offset 38"):
        StoreRun(_mog()).run(38, 40, "t")


def test_store_run_refuses_a_wrong_terminator() -> None:
    with pytest.raises(MogVersionError, match="end of run"):
        StoreRun(_mog()).run(0, 6, "t")


def test_script_entries() -> None:
    assert _script_entry(None, "t") == 0
    assert _script_entry(Cell(0, 4), "t") == 0
    assert _script_entry(Cell(0x40, 4, DATA_HUNK), "t") == 0x40
    assert _script_entry(Cell(0x40, 4, BSS_HUNK), "t") == NOT_A_SCRIPT
    with pytest.raises(MogVersionError, match="not relocated"):
        _script_entry(Cell(3, 4), "t")


def test_wave_reach_follows_the_forced_limits() -> None:
    assert _wave_reach(1, one_at_a_time=False, alive_cap=None) == 3
    assert _wave_reach(2, one_at_a_time=True, alive_cap=None) == 1
    assert _wave_reach(1, one_at_a_time=False, alive_cap=2) == 2
