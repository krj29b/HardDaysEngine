"""Checked reads of ``program`` code across its hunks (ARCHITECTURE.md §12.39)."""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.mog_code import MogCode, Pointer
from harddaysengine.assets.turn_rules import text_list

if TYPE_CHECKING:
    from harddaysengine.assets.hunk import DecodedHunkExecutable
    from harddaysengine.sim.turn_rules import TextLine

PROGRAM_CODE_CRC32 = 0x3064C4E2

JSR, JMP, RTS, BSR, BRA, BEQ = 0x4EB9, 0x4EF9, 0x4E75, 0x6100, 0x6000, 0x6700
LEA_A0 = 0x41F9


def key(pointer: Pointer) -> tuple[int, int]:
    """A pointer as a ``(hunk, offset)`` pair."""
    return pointer.hunk, pointer.offset


class ProgramCode:
    """Hunk-aware checked reads of ``program`` over :class:`MogCode` (§12.39.1)."""

    def __init__(
        self, executable: DecodedHunkExecutable, supported: tuple[int, ...] = (PROGRAM_CODE_CRC32,)
    ) -> None:
        self.code = MogCode(executable, supported=supported)

    def word(self, hunk: int, pos: int) -> int:
        return self.code.hunk_word(hunk, pos)

    def long(self, hunk: int, pos: int) -> int:
        value: int = struct.unpack_from(">I", self.code.hunk_data(hunk), pos)[0]
        return value

    def sword(self, hunk: int, pos: int) -> int:
        value: int = struct.unpack_from(">h", self.code.hunk_data(hunk), pos)[0]
        return value

    def expect(self, hunk: int, pos: int, opcode: int, what: str) -> None:
        found = self.word(hunk, pos)
        if found != opcode:
            raise MogVersionError(
                f"program: {what}: expected ${opcode:04X} at hunk {hunk} offset {pos:#x}, "
                f"found ${found:04X}"
            )

    def pointer(self, hunk: int, pos: int, what: str) -> Pointer:
        return self.code.pointer(pos, what, hunk)

    def call(self, hunk: int, pos: int, what: str) -> tuple[int, int]:
        self.expect(hunk, pos, JSR, what)
        return key(self.pointer(hunk, pos + 2, what))

    def name(self, hunk: int, pos: int, what: str) -> str:
        self.expect(hunk, pos, LEA_A0, what)
        value = self.code.cstring(self.pointer(hunk, pos + 2, what), what)
        if not value:
            raise MogVersionError(f"program: empty {what}")
        return value

    def lines(self, pointer: Pointer, what: str) -> tuple[TextLine, ...]:
        return text_list(self.code, pointer, what)

    def branch(self, hunk: int, pos: int, opcode: int, what: str) -> int:
        self.expect(hunk, pos, opcode, what)
        return pos + 2 + self.sword(hunk, pos + 2)

    def short_branch(self, hunk: int, pos: int, what: str) -> int:
        found = self.word(hunk, pos)
        if found & 0xFF00 != BEQ or found & 0xFF == 0:
            raise MogVersionError(f"program: {what}: expected BEQ.S at {pos:#x}")
        return pos + 2 + (found & 0xFF)
