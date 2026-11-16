"""Checked reads of operands in ``mog`` code and a straight-line store run (ARCHITECTURE.md §10.15).

Fight definitions are immediates of 68k instructions in ``mog`` hunk 0. This module reads them in
two ways, both of which refuse to guess:

* single operands at a known position, with the instruction word in front of them compared;
* :class:`StoreRun`, which executes a bounded run of the few store instructions the original's
  table-filling routines are made of, and stops with an error at any other instruction.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pymoonstone.assets.fight_palette import MOG_CODE_CRC32, MogVersionError

if TYPE_CHECKING:
    from collections.abc import Callable

    from pymoonstone.assets.hunk import DecodedHunkExecutable

CODE_HUNK = 0
BSS_HUNK = 1
DATA_HUNK = 4
# Pseudo hunk index of "the record register A1 points at" inside an initialiser.
RECORD_SPACE = -1

OP_RTS = 0x4E75
OP_JSR = 0x4EB9
OP_JMP = 0x4EF9
OP_BSR_W = 0x6100
OP_BRA_W = 0x6000
OP_LEA_A0 = 0x41F9
OP_LEA_A1 = 0x43F9
OP_LEA_A2 = 0x45F9
OP_LEA_A3 = 0x47F9
OP_LEA_A4 = 0x49F9

_LEA_REGISTERS = {OP_LEA_A0: "a0", OP_LEA_A1: "a1", OP_LEA_A4: "a4"}
_STORE_LONG_REGISTERS = {0x217C: "a0", 0x237C: "a1", 0x297C: "a4"}
_OP_STORE_WORD_A1 = 0x337C
_OP_STORE_BYTE_A1 = 0x137C
_OP_PUSH_LONG_A0 = 0x20FC  # MOVE.L #imm,(A0)+
_OP_PUT_LONG_A0 = 0x20BC  # MOVE.L #imm,(A0)
_OP_ADDA_A0 = 0xD1FC
_OP_COPY_LONG = 0x20D9  # MOVE.L (A1)+,(A0)+
_OP_MOVEQ_D0 = 0x7000
_OP_MOVEQ_D7 = 0x7E00
_OP_DBRA_D0 = 0x51C8
_OP_DBRA_D7 = 0x51CF
_MOVEQ_MASK = 0xFF00
_WORD_MASK = 0xFFFF


@dataclass(slots=True, frozen=True)
class Pointer:
    """An address as (hunk index, byte offset)."""

    hunk: int
    offset: int


@dataclass(slots=True, frozen=True)
class Cell:
    """One stored value: ``target`` is the hunk a relocated long points into, else ``None``."""

    value: int
    size: int
    target: int | None = None


class MogCode:
    """``mog`` hunk 0 with its relocations, for checked operand reads (§10.15)."""

    def __init__(
        self, mog_exec: DecodedHunkExecutable, *, supported: tuple[int, ...] = (MOG_CODE_CRC32,)
    ) -> None:
        """Accept ``mog_exec`` only when its code hunk CRC-32 is one of ``supported``."""
        if len(mog_exec.sections) <= DATA_HUNK:
            raise MogVersionError("mog: fewer hunks than the fight definitions need")
        self.executable = mog_exec
        self.code = mog_exec.sections[CODE_HUNK].data
        crc = zlib.crc32(self.code)
        if crc not in supported:
            names = ", ".join(f"${value:08X}" for value in supported)
            msg = f"mog: unsupported version (code hunk CRC-32 ${crc:08X}); supported: {names}"
            raise MogVersionError(msg)
        self._relocs = [_reloc_map(mog_exec, index) for index in range(len(mog_exec.sections))]

    def word(self, pos: int) -> int:
        value: int = struct.unpack_from(">H", self.code, pos)[0]
        return value

    def long(self, pos: int) -> int:
        value: int = struct.unpack_from(">I", self.code, pos)[0]
        return value

    def reloc(self, pos: int, hunk: int = CODE_HUNK) -> int | None:
        """Hunk that the relocated long at ``pos`` of ``hunk`` points into, or ``None``."""
        return self._relocs[hunk].get(pos)

    def relocations(self, hunk: int) -> dict[int, int]:
        """Every relocated long of ``hunk``: position to the hunk it points into."""
        return dict(self._relocs[hunk])

    def hunk_data(self, hunk: int) -> bytes:
        return self.executable.sections[hunk].data

    def expect(self, pos: int, opcodes: tuple[int, ...], what: str) -> int:
        """Return the instruction word at ``pos``; it must be one of ``opcodes``."""
        found = self.word(pos)
        if found not in opcodes:
            wanted = " or ".join(f"${op:04X}" for op in opcodes)
            msg = f"mog: {what}: expected {wanted} at code offset {pos}, found ${found:04X}"
            raise MogVersionError(msg)
        return found

    def pointer(self, pos: int, what: str, hunk: int = CODE_HUNK) -> Pointer:
        """The relocated long at ``pos`` as a pointer."""
        target = self.reloc(pos, hunk)
        if target is None:
            msg = f"mog: {what}: the long at offset {pos} of hunk {hunk} is not relocated"
            raise MogVersionError(msg)
        value: int = struct.unpack_from(">I", self.hunk_data(hunk), pos)[0]
        return Pointer(target, value)

    def lea(self, pos: int, opcode: int, what: str) -> Pointer:
        """Target of the relocated ``LEA abs.l,An`` at ``pos``."""
        self.expect(pos, (opcode,), what)
        return self.pointer(pos + 2, what)

    def call_target(self, pos: int, what: str) -> Pointer:
        """Target of the ``JSR`` / ``JMP abs.l`` at ``pos``."""
        self.expect(pos, (OP_JSR, OP_JMP), what)
        return self.pointer(pos + 2, what)

    def branch_target(self, pos: int, opcode: int, what: str) -> int:
        """Target of the 16-bit ``BSR`` / ``BRA`` at ``pos``."""
        self.expect(pos, (opcode,), what)
        displacement: int = struct.unpack_from(">h", self.code, pos + 2)[0]
        return pos + 2 + displacement

    def cell_word(self, pos: int, cell: int, what: str) -> int:
        """Operand of ``MOVE.W #imm,cell`` where ``cell`` is a hunk-1 offset."""
        self.expect(pos, (0x33FC,), what)
        self._expect_cell(pos + 4, cell, what)
        return self.word(pos + 2)

    def cell_routine(self, pos: int, cell: int, what: str) -> int:
        """Hunk-0 offset stored by ``MOVE.L #routine,cell``."""
        self.expect(pos, (0x23FC,), what)
        self._expect_cell(pos + 6, cell, what)
        routine = self.pointer(pos + 2, what)
        if routine.hunk != CODE_HUNK:
            raise MogVersionError(f"mog: {what}: the routine at {pos} is not in hunk 0")
        return routine.offset

    def cstring(self, pointer: Pointer, what: str) -> str:
        """NUL-terminated ASCII string at ``pointer``."""
        data = self.hunk_data(pointer.hunk)
        end = data.find(0, pointer.offset)
        if end < 0:
            raise MogVersionError(f"mog: {what}: unterminated string")
        return data[pointer.offset : end].decode("ascii")

    def _expect_cell(self, pos: int, cell: int, what: str) -> None:
        if self.pointer(pos, what) != Pointer(BSS_HUNK, cell):
            msg = f"mog: {what}: the instruction at code offset {pos} writes another cell"
            raise MogVersionError(msg)


def _reloc_map(mog_exec: DecodedHunkExecutable, hunk: int) -> dict[int, int]:
    relocs = mog_exec.sections[hunk].relocs
    return {pos: target for target, positions in relocs.items() for pos in positions}


@dataclass(slots=True)
class StoreRun:
    """Executes a run of store instructions and records what they wrote (§10.15).

    Understood: ``LEA abs.l,A0/A1/A4``; ``MOVE.L #imm,d16(A0/A1/A4)``; ``MOVE.W`` / ``MOVE.B
    #imm,d16(A1)``; ``MOVE.L #imm,(A0)+`` and ``(A0)``; ``ADDA.L #imm,A0``; ``MOVEQ #n,D0/D7``;
    ``MOVE.L (A1)+,(A0)+``; ``DBRA D0/D7``. Anything else raises :class:`MogVersionError`.
    """

    mog: MogCode
    memory: dict[Pointer, Cell] = field(default_factory=dict)
    registers: dict[str, Pointer] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)
    # Every address a run loaded into A0: the original fills each table through its own LEA.
    starts: set[Pointer] = field(default_factory=set)

    def run(self, start: int, stop: int, what: str, terminator: int = OP_RTS) -> None:
        """Execute ``[start, stop)``; the word at ``stop`` must be ``terminator``."""
        self.mog.expect(stop, (terminator,), f"{what} (end of run)")
        handlers = self._handlers()
        pc = start
        while pc != stop:
            if not start <= pc < stop:
                raise MogVersionError(f"mog: {what}: run left its bounds at code offset {pc}")
            pc = self._step(pc, what, handlers)

    def run_record(self, start: int, stop: int, what: str, terminator: int = OP_RTS) -> None:
        """Like :meth:`run`, with ``A1`` pointing at a fresh record (``RECORD_SPACE``)."""
        self.registers["a1"] = Pointer(RECORD_SPACE, 0)
        self.run(start, stop, what, terminator)

    def record_fields(self) -> dict[int, Cell]:
        """Everything written through ``A1`` in record runs, keyed by record offset."""
        return {
            pointer.offset: cell
            for pointer, cell in self.memory.items()
            if pointer.hunk == RECORD_SPACE
        }

    def longs(self, pointer: Pointer, count: int) -> tuple[Cell | None, ...]:
        """Up to ``count`` longs from ``pointer``, ending where another filled table starts.

        ``None`` marks a long that nothing stored.
        """
        later = [
            start.offset
            for start in self.starts
            if start.hunk == pointer.hunk and start.offset > pointer.offset
        ]
        if later:
            count = min(count, (min(later) - pointer.offset) // 4)
        return tuple(
            self.memory.get(Pointer(pointer.hunk, pointer.offset + index * 4))
            for index in range(count)
        )

    def _step(self, pc: int, what: str, handlers: dict[int, Callable[[int, int, str], int]]) -> int:
        opcode = self.mog.word(pc)
        handler = handlers.get(opcode)
        if handler is not None:
            return handler(pc, opcode, what)
        if opcode & _MOVEQ_MASK in (_OP_MOVEQ_D0, _OP_MOVEQ_D7):
            return self._moveq(pc, opcode)
        msg = f"mog: {what}: unsupported instruction ${opcode:04X} at code offset {pc}"
        raise MogVersionError(msg)

    def _handlers(self) -> dict[int, Callable[[int, int, str], int]]:
        handlers: dict[int, Callable[[int, int, str], int]] = {
            _OP_STORE_WORD_A1: self._store_small,
            _OP_STORE_BYTE_A1: self._store_small,
            _OP_PUSH_LONG_A0: self._put_long,
            _OP_PUT_LONG_A0: self._put_long,
            _OP_ADDA_A0: self._adda,
            _OP_COPY_LONG: self._copy_long,
            _OP_DBRA_D0: self._dbra,
            _OP_DBRA_D7: self._dbra,
        }
        handlers.update(dict.fromkeys(_LEA_REGISTERS, self._lea))
        handlers.update(dict.fromkeys(_STORE_LONG_REGISTERS, self._store_long))
        return handlers

    def _register(self, name: str, pc: int, what: str) -> Pointer:
        pointer = self.registers.get(name)
        if pointer is None:
            msg = f"mog: {what}: register {name} used before it was loaded (code offset {pc})"
            raise MogVersionError(msg)
        return pointer

    def _immediate_long(self, pos: int) -> Cell:
        return Cell(self.mog.long(pos), 4, self.mog.reloc(pos))

    def _lea(self, pc: int, opcode: int, what: str) -> int:
        target = self.mog.pointer(pc + 2, what)
        self.registers[_LEA_REGISTERS[opcode]] = target
        if opcode == OP_LEA_A0:
            self.starts.add(target)
        return pc + 6

    def _store_long(self, pc: int, opcode: int, what: str) -> int:
        base = self._register(_STORE_LONG_REGISTERS[opcode], pc, what)
        self.memory[Pointer(base.hunk, base.offset + self.mog.word(pc + 6))] = self._immediate_long(
            pc + 2
        )
        return pc + 8

    def _store_small(self, pc: int, opcode: int, what: str) -> int:
        base = self._register("a1", pc, what)
        size = 2 if opcode == _OP_STORE_WORD_A1 else 1
        value = self.mog.word(pc + 2) & (_WORD_MASK if size == 2 else 0xFF)
        self.memory[Pointer(base.hunk, base.offset + self.mog.word(pc + 4))] = Cell(value, size)
        return pc + 6

    def _put_long(self, pc: int, opcode: int, what: str) -> int:
        base = self._register("a0", pc, what)
        self.memory[base] = self._immediate_long(pc + 2)
        if opcode == _OP_PUSH_LONG_A0:
            self.registers["a0"] = Pointer(base.hunk, base.offset + 4)
        return pc + 6

    def _adda(self, pc: int, _opcode: int, what: str) -> int:
        base = self._register("a0", pc, what)
        self.registers["a0"] = Pointer(base.hunk, base.offset + self.mog.long(pc + 2))
        return pc + 6

    def _copy_long(self, pc: int, _opcode: int, what: str) -> int:
        source = self._register("a1", pc, what)
        dest = self._register("a0", pc, what)
        if source.hunk < 0:
            raise MogVersionError(f"mog: {what}: copy from a record at code offset {pc}")
        data = self.mog.hunk_data(source.hunk)
        value: int = struct.unpack_from(">I", data, source.offset)[0]
        self.memory[dest] = Cell(value, 4, self.mog.reloc(source.offset, source.hunk))
        self.registers["a1"] = Pointer(source.hunk, source.offset + 4)
        self.registers["a0"] = Pointer(dest.hunk, dest.offset + 4)
        return pc + 2

    def _moveq(self, pc: int, opcode: int) -> int:
        name = "d0" if opcode & _MOVEQ_MASK == _OP_MOVEQ_D0 else "d7"
        self.counters[name] = opcode & 0xFF
        return pc + 2

    def _dbra(self, pc: int, opcode: int, what: str) -> int:
        name = "d0" if opcode == _OP_DBRA_D0 else "d7"
        if name not in self.counters:
            raise MogVersionError(f"mog: {what}: loop counter {name} not set (code offset {pc})")
        self.counters[name] = (self.counters[name] - 1) & _WORD_MASK
        if self.counters[name] == _WORD_MASK:
            return pc + 4
        return self.mog.branch_target(pc, opcode, what)
