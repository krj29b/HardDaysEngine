"""Checked bank loaders and symbolic relocation (ARCHITECTURE.md §6.2.10)."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING

from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.mog_code import Pointer

if TYPE_CHECKING:
    from harddaysengine.assets.mog_code import MogCode
    from harddaysengine.assets.synth_data import SynthData


@dataclass(slots=True, frozen=True)
class BankLoad:
    """One original file read into a persistent slot (§6.2.10)."""

    name: str
    slot: int
    size: int


@dataclass(slots=True, frozen=True)
class BankData:
    """Runtime loader records and resolved instrument (slot, offset) pairs."""

    startup: BankLoad
    loaders: tuple[BankLoad, ...]
    samples: tuple[tuple[int, int], ...]
    header_bytes: int

    def named(self, name: str) -> BankLoad:
        """Look up the original loader by its disk filename."""
        return next(
            row for row in (self.startup, *self.loaders) if row.name.casefold() == name.casefold()
        )


def _word(mog: MogCode, hunk: int, pos: int) -> int:
    return mog.hunk_word(hunk, pos)


def _long(mog: MogCode, hunk: int, pos: int) -> int:
    value: int = struct.unpack_from(">I", mog.hunk_data(hunk), pos)[0]
    return value


def _expect(mog: MogCode, hunk: int, pos: int, opcode: int) -> None:
    if _word(mog, hunk, pos) != opcode:
        raise MogVersionError(f"mog: unsupported bank instruction at hunk {hunk} ${pos:04X}")


def _slot(pointer: Pointer) -> int:
    if pointer.hunk != 1 or not 0x140 <= pointer.offset <= 0x150 or pointer.offset % 4:
        raise MogVersionError("mog: bank destination is not a bank buffer cell")
    return (pointer.offset - 0x140) // 4 + 1


def _load(mog: MogCode, pos: int) -> BankLoad:
    for delta, op in ((0, 0x41F9), (6, 0x2279), (12, 0x203C)):
        _expect(mog, 16, pos + delta, op)
    name = mog.cstring(mog.pointer(pos + 2, "bank filename", 16), "bank filename")
    slot = _slot(mog.pointer(pos + 8, "bank buffer", 16))
    return BankLoad(name, slot, _long(mog, 16, pos + 14))


class _Relocate:
    """Bounded symbolic execution of the original sample-address routine."""

    def __init__(self, mog: MogCode, synth: SynthData) -> None:
        self.mog = mog
        self.base = mog.pointer(0x712, "instrument table", 44).offset
        self.samples = [(0, row.sample) for row in synth.instruments]
        self.pc = mog.pointer(0x10C, "bank relocation", 16).offset
        self.a0 = self.slot = 0
        self.equal = False

    def _pointer(self, pos: int) -> int:
        pointer = self.mog.pointer(pos, "bank instrument", 44)
        if pointer.hunk != 45:
            raise MogVersionError("mog: bank relocation points outside instrument hunk")
        return pointer.offset

    def _index(self) -> int:
        index, rem = divmod(self.a0 - self.base, 14)
        if rem or not 0 <= index < len(self.samples):
            raise MogVersionError("mog: bank relocation addresses an invalid instrument")
        return index

    def step(self) -> bool:
        p, mog = self.pc, self.mog
        op = _word(mog, 44, p)
        if op == 0x4E75:
            return True
        if op & 0xFF00 in (0x6000, 0x6600, 0x6700):
            self._branch(op)
        elif op in (0x41F9, 0xB1FC):
            value = self._pointer(p + 2)
            if op == 0x41F9:
                self.a0 = value
            else:
                self.equal = self.a0 == value
            self.pc += 6
        elif op == 0xD1FC:
            self.a0 += _long(mog, 44, p + 2)
            self.pc += 6
        else:
            self._sample(op)
        return False

    def _branch(self, op: int) -> None:
        displacement = op & 0xFF
        if not displacement:
            raise MogVersionError("mog: bank relocation expected a byte branch")
        if displacement >= 128:
            displacement -= 256
        take = op & 0xFF00 == 0x6000 or self.equal == (op & 0xFF00 == 0x6700)
        self.pc += 2 + (displacement if take else 0)

    def _sample(self, op: int) -> None:
        p, mog = self.pc, self.mog
        if op == 0x2239:
            self.slot = _slot(mog.pointer(p + 2, "relocation bank", 44))
            self.pc += 6
            return
        index = self._index()
        slot, offset = self.samples[index]
        if op == 0xD3A8:
            _expect(mog, 44, p + 2, 6)
            slot = self.slot
            self.pc += 4
        elif op == 0x2179:
            _expect(mog, 44, p + 6, 6)
            slot, offset = _slot(mog.pointer(p + 2, "replacement bank", 44)), 0
            self.pc += 8
        elif op == 0x06A8:
            _expect(mog, 44, p + 6, 6)
            offset += _long(mog, 44, p + 2)
            self.pc += 8
        else:
            raise MogVersionError(f"mog: unsupported bank relocation at ${p:04X}")
        self.samples[index] = slot, offset


def parse_banks(mog: MogCode, synth: SynthData) -> BankData:
    """Read loaders and execute relocation against symbolic slots (§6.2.10)."""
    _expect(mog, 16, 0x10A, 0x4EB9)
    if mog.pointer(0x10C, "bank relocation", 16).hunk != 44:
        raise MogVersionError("mog: bank relocation is outside synth hunk")
    _expect(mog, 16, 0x274, 0x203C)
    header = _long(mog, 16, 0x276)
    startup = _load(mog, 0xF2)
    rows = []
    pos = 0x134
    while pos < 0x26A:
        rows.append(_load(mog, pos))
        call = pos + 18
        op = _word(mog, 16, call)
        if op == 0x6100:
            displacement = struct.unpack_from(">h", mog.hunk_data(16), call + 2)[0]
            target, end = Pointer(16, call + 2 + displacement), call + 4
        elif op == 0x4EB9:
            target, end = mog.pointer(call + 2, "bank reader", 16), call + 6
        else:
            raise MogVersionError("mog: unsupported bank reader call")
        if target != Pointer(16, 0x26A):
            raise MogVersionError("mog: bank loader calls a different reader")
        _expect(mog, 16, end, 0x4E75)
        pos = end + 2
    run = _Relocate(mog, synth)
    for _ in range(4096):
        if run.step():
            return BankData(startup, tuple(rows), tuple(run.samples), header)
    raise MogVersionError("mog: bank relocation did not finish")
