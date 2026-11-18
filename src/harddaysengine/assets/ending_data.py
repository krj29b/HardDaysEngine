"""Checked decoder of the ``program`` ending pass (ARCHITECTURE.md §12.39).

The ending scenes, its loader and its palette tweaks are straight-line 68k code in the user's own
``program`` executable. This module walks that code with a small set of understood instruction
forms and turns it into :class:`~harddaysengine.sim.ending_types.EndingPlan` records. Any other
instruction, any call to an unknown routine and any store to an unknown cell raises
:class:`MogVersionError`; nothing is guessed and no original value is embedded here.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.mog_code import MogCode, Pointer
from harddaysengine.assets.turn_rules import text_list
from harddaysengine.sim.ending_types import (
    Buffer,
    Call,
    Caption,
    Cell,
    ClassTweak,
    Clear,
    EndingLoad,
    EndingPlan,
    EndingTweak,
    FadeBlack,
    Invoke,
    Load,
    Pairs,
    PaletteTo,
    PictureLoad,
    RampRelease,
    RampSpec,
    Select,
    Show,
    Spawn,
    SpawnKind,
    SpawnSide,
    StageTable,
    Step,
    Store,
    Target,
    Text,
    Ticks,
    Wait,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from harddaysengine.assets.hunk import DecodedHunkExecutable
    from harddaysengine.sim.turn_rules import TextLine

PROGRAM_CODE_CRC32 = 0x3064C4E2

_CODE = 0
_LOADER = 8
_PALETTES = 9
_SCREENS = 10
_SCRIPTS = 2

# Instruction words this decoder understands.
_JSR, _JMP, _RTS, _BSR, _BRA, _BEQ = 0x4EB9, 0x4EF9, 0x4E75, 0x6100, 0x6000, 0x6700
_MOVE_W_ABS, _MOVE_L_ABS, _COPY_L = 0x33FC, 0x23FC, 0x23F9
_MOVE_W_ABS_D0, _MOVE_L_ABS_D0, _MOVE_L_IMM_D0 = 0x3039, 0x2039, 0x203C
_LEA_A0, _MOVEA_A0, _MOVEA_A1, _MOVEA_IMM_A0 = 0x41F9, 0x2079, 0x2279, 0x207C
_PUSH_D0, _POP_D0, _DBF_D0 = 0x2F00, 0x201F, 0x51C8

# Entry points (hunk, offset) of the routines the ending calls; the plan names each one.
_CALLS: dict[tuple[int, int], Call] = {
    (10, 0x264): Call.JOBS_RESET,
    (10, 0xBF8): Call.BLACKOUT,
    (10, 0xC50): Call.FADE_OUT,
    (10, 0xC92): Call.FLIP,
    (10, 0xCC4): Call.PREPARE,
    (1, 0x000): Call.MUSIC,
    (0, 0x1BA): Call.FRAMES,
    (0, 0x292): Call.STAGE,
    (31, 0x68A): Call.LOADING_A,
    (31, 0x71C): Call.LOADING_B,
}
_SPAWNS = {(0, 0x2E4): SpawnKind.LEFT, (0, 0x30A): SpawnKind.RIGHT}
_SUBSCENE = (0, 0x862)
_CAPTION, _LOADER_ENTRY, _PAIRS = (0, 0x11CA), (8, 0x500), (0, 0x508)
_UNPACK, _CLEAR, _TARGET, _WAIT = (10, 0xD6A), (29, 0x5E0), (10, 0xD9A), (29, 0x658)
_RAMP_FREE, _PALETTE_TO, _TEXT, _FADE = (31, 0x5A), (10, 0xC70), (12, 0x132), (31, 0x1A)
_TICK_BODY = ((0, 0x330), (10, 0x2BE), (10, 0x32A), (10, 0xC92), (10, 0xA18), (0, 0x33C))

_WORD_CELLS = {
    (2, 0x246): Cell.CREDITS,
    (0, 0xEC2): Cell.OVERLAY_PHASE,
    (3, 0x88): Cell.STAGE_MODE,
    (3, 0x8A): Cell.STAGED,
    (3, 0x8E): Cell.STOP,
    (2, 0x2DC6): Cell.LEFT_DEPTH,
    (2, 0x2DC8): Cell.RIGHT_DEPTH,
}
_LONG_CELLS = {(2, 0x23E): Cell.SPEED, (3, 0x9A): Cell.GAP}
_STAGE_TABLE = (3, 0x84)
_PAIR_INDEX, _PAIR_TABLE, _PAIR_COUNT, _PAIR_SPARE = (0, 0x602), (0, 0x60A), (0, 0x608), (0, 0x60E)
_RAMP_SLOTS = (0, 0x92C)
_BLACK = (10, 0xDCA)
_FLAGS = (0, 0x190)
_SCENES = (0x938, 0xABA, 0xBD8, 0xD2E)


def _key(pointer: Pointer) -> tuple[int, int]:
    return pointer.hunk, pointer.offset


def _buffer(pointer: Pointer, what: str) -> Buffer:
    if pointer.hunk == 30 and pointer.offset in (0, 4):
        return Buffer.SCREEN if pointer.offset == 0 else Buffer.WORK
    index, rest = divmod(pointer.offset - 0x212, 4)
    if pointer.hunk != _SCRIPTS or rest or not 0 <= index <= 9:
        raise MogVersionError(f"program: {what}: unknown picture buffer cell {pointer}")
    return Buffer(index)


def _palette(pointer: Pointer, what: str) -> int:
    index, rest = divmod(pointer.offset - 6, 0x40)
    if pointer.hunk != _PALETTES or rest or not 0 <= index <= 7:
        raise MogVersionError(f"program: {what}: unknown palette table {pointer}")
    return index


class _Program:
    """Hunk-aware checked reads over :class:`MogCode` (§12.39.1)."""

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
        self.expect(hunk, pos, _JSR, what)
        return _key(self.pointer(hunk, pos + 2, what))

    def name(self, hunk: int, pos: int, what: str) -> str:
        self.expect(hunk, pos, _LEA_A0, what)
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
        if found & 0xFF00 != _BEQ or found & 0xFF == 0:
            raise MogVersionError(f"program: {what}: expected BEQ.S at {pos:#x}")
        return pos + 2 + (found & 0xFF)


@dataclass(slots=True)
class _Scene:
    """Walks one scene body in hunk 0 into steps (§12.39.4)."""

    prog: _Program
    steps: list[Step] = field(default_factory=list)
    pairs: dict[tuple[int, int], int] = field(default_factory=dict)

    def run(self, pos: int) -> None:
        handlers = self._handlers()
        while (word := self.prog.word(_CODE, pos)) != _RTS:
            handler = handlers.get(word)
            if handler is None:
                raise MogVersionError(f"program: ending scene: ${word:04X} at {pos:#x}")
            pos = handler(pos)

    def _handlers(self) -> dict[int, Callable[[int], int]]:
        return {
            _MOVE_W_ABS: self._store_word,
            _MOVE_L_ABS: self._store_long,
            _COPY_L: self._select,
            _JSR: self._jsr,
            _MOVEA_IMM_A0: self._spawn,
            _MOVEA_A0: self._buffer_call,
            _MOVE_L_ABS_D0: self._d0_cell_call,
            _MOVE_L_IMM_D0: self._wait_long,
            _LEA_A0: self._table_call,
            _BSR: self._pairs_run,
            **{0x7000 | n: self._quick for n in range(256)},
        }

    def _store_word(self, pos: int) -> int:
        cell = _key(self.prog.pointer(_CODE, pos + 4, "scene word cell"))
        value = self.prog.word(_CODE, pos + 2)
        if cell in (_PAIR_INDEX, _PAIR_COUNT, _PAIR_SPARE):
            self.pairs[cell] = value
        elif cell in _WORD_CELLS:
            self.steps.append(Store(_WORD_CELLS[cell], value))
        else:
            raise MogVersionError(f"program: ending scene stores an unknown word {cell}")
        return pos + 8

    def _store_long(self, pos: int) -> int:
        cell = _key(self.prog.pointer(_CODE, pos + 6, "scene long cell"))
        if cell == _STAGE_TABLE:
            table = self.prog.pointer(_CODE, pos + 2, "stage palette")
            self.steps.append(StageTable(_palette(table, "stage palette")))
        elif cell == _PAIR_TABLE:
            self.pairs[cell] = self.prog.pointer(_CODE, pos + 2, "pair table").offset
        elif cell in _LONG_CELLS and self.prog.code.reloc(pos + 2, _CODE) is None:
            self.steps.append(Store(_LONG_CELLS[cell], self.prog.long(_CODE, pos + 2)))
        else:
            raise MogVersionError(f"program: ending scene stores an unknown long {cell}")
        return pos + 10

    def _select(self, pos: int) -> int:
        source = _buffer(self.prog.pointer(_CODE, pos + 2, "select source"), "select source")
        target = _buffer(self.prog.pointer(_CODE, pos + 6, "select target"), "select target")
        if target != Buffer.SHOWN:
            raise MogVersionError("program: ending scene selects into another buffer")
        self.steps.append(Select(source))
        return pos + 10

    def _jsr(self, pos: int) -> int:
        target = _key(self.prog.pointer(_CODE, pos + 2, "scene call"))
        if target == _SUBSCENE:
            self.run(target[1])
        elif target in _CALLS:
            self.steps.append(Invoke(_CALLS[target]))
        else:
            raise MogVersionError(f"program: ending scene calls an unknown routine {target}")
        return pos + 6

    def _spawn(self, pos: int) -> int:
        script = self.prog.pointer(_CODE, pos + 2, "spawn script")
        kind = _SPAWNS.get(self.prog.call(_CODE, pos + 6, "spawn helper"))
        if kind is None or script.hunk != _SCRIPTS:
            raise MogVersionError(f"program: ending spawn at {pos:#x} is not understood")
        self.steps.append(Spawn(kind, script.offset))
        return pos + 12

    def _buffer_call(self, pos: int) -> int:
        buffer = _buffer(self.prog.pointer(_CODE, pos + 2, "buffer operand"), "buffer operand")
        if self.prog.word(_CODE, pos + 6) == _MOVEA_A1:
            shown = self.prog.pointer(_CODE, pos + 8, "unpack target")
            if _buffer(shown, "unpack target") != Buffer.SHOWN:
                raise MogVersionError("program: ending unpacks into another buffer")
            if self.prog.call(_CODE, pos + 12, "unpack") != _UNPACK:
                raise MogVersionError("program: ending unpack calls another routine")
            self.steps.append(Show(buffer))
            return pos + 18
        if self.prog.call(_CODE, pos + 6, "clear") != _CLEAR:
            raise MogVersionError("program: ending buffer call is not a clear")
        self.steps.append(Clear(buffer))
        return pos + 12

    def _d0_cell_call(self, pos: int) -> int:
        cell = self.prog.pointer(_CODE, pos + 2, "D0 cell")
        target = self.prog.call(_CODE, pos + 6, "D0 cell call")
        slot, rest = divmod(cell.offset - _RAMP_SLOTS[1], 4)
        if target == _RAMP_FREE and cell.hunk == _CODE and not rest and 0 <= slot <= 2:
            self.steps.append(RampRelease(slot))
        elif target == _TARGET:
            self.steps.append(Target(_buffer(cell, "draw target")))
        else:
            raise MogVersionError(f"program: ending D0 call at {pos:#x} is not understood")
        return pos + 12

    def _wait_long(self, pos: int) -> int:
        if self.prog.call(_CODE, pos + 6, "wait") != _WAIT:
            raise MogVersionError("program: ending D0 immediate feeds another routine")
        self.steps.append(Wait(self.prog.long(_CODE, pos + 2)))
        return pos + 12

    def _quick(self, pos: int) -> int:
        value = self.prog.word(_CODE, pos) & 0xFF
        if self.prog.word(_CODE, pos + 2) == _PUSH_D0:
            return self._ticks(pos, value)
        if self.prog.call(_CODE, pos + 2, "quick wait") != _WAIT:
            raise MogVersionError("program: ending MOVEQ feeds another routine")
        self.steps.append(Wait(value))
        return pos + 8

    def _ticks(self, pos: int, count: int) -> int:
        body = pos + 4
        for index, routine in enumerate(_TICK_BODY):
            if self.prog.call(_CODE, body + 6 * index, "tick pass") != routine:
                raise MogVersionError("program: ending tick loop calls another routine")
        end = body + 6 * len(_TICK_BODY)
        self.prog.expect(_CODE, end, _POP_D0, "tick loop restore")
        if self.prog.branch(_CODE, end + 2, _DBF_D0, "tick loop") != pos + 2:
            raise MogVersionError("program: ending tick loop branches elsewhere")
        self.steps.append(Ticks(count + 1))
        return end + 6

    def _table_call(self, pos: int) -> int:
        table = self.prog.pointer(_CODE, pos + 2, "table operand")
        if self.prog.word(_CODE, pos + 6) & 0xFF00 == 0x7000:
            period = self.prog.word(_CODE, pos + 6) & 0xFF
            if _key(table) != _BLACK or self.prog.call(_CODE, pos + 8, "fade") != _FADE:
                raise MogVersionError("program: ending fade is not a fade to black")
            self.steps.append(FadeBlack(period))
            return pos + 14
        target = self.prog.call(_CODE, pos + 6, "table call")
        if target == _PALETTE_TO:
            self.steps.append(PaletteTo(_palette(table, "palette target")))
        elif target == _TEXT:
            self.steps.append(Text(self.prog.lines(table, "ending text")))
        else:
            raise MogVersionError(f"program: ending table call at {pos:#x} is not understood")
        return pos + 12

    def _pairs_run(self, pos: int) -> int:
        if (0, self.prog.branch(_CODE, pos, _BSR, "pairs")) != _PAIRS:
            raise MogVersionError("program: ending BSR is not the pairs routine")
        wanted = {_PAIR_INDEX, _PAIR_TABLE, _PAIR_COUNT, _PAIR_SPARE}
        if set(self.pairs) != wanted or self.pairs[_PAIR_INDEX] or self.pairs[_PAIR_SPARE]:
            raise MogVersionError("program: ending pairs run without its complete set-up")
        table, count = self.pairs[_PAIR_TABLE], self.pairs[_PAIR_COUNT]
        if not 0 < count <= 16:
            raise MogVersionError("program: ending pairs count out of range")
        scripts = []
        for index in range(count):
            script = self.prog.pointer(_CODE, table + 4 * index, "pair script")
            if script.hunk != _SCRIPTS:
                raise MogVersionError("program: ending pair script outside the script hunk")
            scripts.append(script.offset)
        self.steps.append(Pairs(tuple(scripts)))
        self.pairs.clear()
        return pos + 4


def _flag_bit(prog: _Program) -> int:
    prog.expect(_CODE, 0x1E, 0x33F9, "boot flag copy")
    if prog.long(_CODE, 0x20) != 0x3E0 or _key(prog.pointer(_CODE, 0x24, "flags")) != _FLAGS:
        raise MogVersionError("program: boot flags are copied elsewhere")
    prog.expect(_CODE, 0x64, _MOVE_W_ABS_D0, "ending flag read")
    if _key(prog.pointer(_CODE, 0x66, "ending flag cell")) != _FLAGS:
        raise MogVersionError("program: the ending test reads another cell")
    prog.expect(_CODE, 0x6A, 0x0240, "ending flag mask")
    bit = prog.word(_CODE, 0x6C)
    prog.expect(_CODE, 0x6E, 0xB07C, "ending flag compare")
    if prog.word(_CODE, 0x70) != bit or bit == 0 or bit & (bit - 1):
        raise MogVersionError("program: the ending test is not a single-bit compare")
    if prog.branch(_CODE, 0x72, _BEQ, "ending branch") != 0x120:
        raise MogVersionError("program: the ending branch leads elsewhere")
    return bit


def _top_level(prog: _Program) -> tuple[tuple[Step, ...], str]:
    """The ending sequence from $0120 to its jump back to the next program (§12.39.1)."""
    for pos in (0x120, 0x154):
        prog.expect(_CODE, pos, _LEA_A0, "caption text")
    steps: list[Step] = [
        Caption(prog.lines(prog.pointer(_CODE, 0x122, "caption"), "ending caption"))
    ]
    if prog.call(_CODE, 0x126, "caption screen") != _CAPTION:
        raise MogVersionError("program: the ending caption calls another routine")
    if prog.call(_CODE, 0x12C, "ending loader") != _LOADER_ENTRY:
        raise MogVersionError("program: the ending loader is elsewhere")
    steps.append(Load())
    prog.expect(_CODE, 0x132, _MOVE_L_ABS, "ending gap")
    if _key(prog.pointer(_CODE, 0x138, "gap cell")) not in _LONG_CELLS:
        raise MogVersionError("program: the ending gap store writes another cell")
    steps.append(Store(Cell.GAP, prog.long(_CODE, 0x134)))
    for index, scene in enumerate(_SCENES):
        if prog.call(_CODE, 0x13C + 6 * index, "ending scene") != (0, scene):
            raise MogVersionError("program: an ending scene call moved")
        body = _Scene(prog)
        body.run(scene)
        steps.extend(body.steps)
    return (*steps, *_closing(prog)), _hand_back(prog)


def _closing(prog: _Program) -> tuple[Step, ...]:
    """The closing caption, its wait and the final fade (§12.39.1)."""
    caption = Caption(prog.lines(prog.pointer(_CODE, 0x156, "caption"), "closing caption"))
    if prog.call(_CODE, 0x15A, "closing caption screen") != _CAPTION:
        raise MogVersionError("program: the closing caption calls another routine")
    prog.expect(_CODE, 0x160, _MOVE_L_IMM_D0, "closing wait")
    if prog.call(_CODE, 0x166, "closing wait") != _WAIT:
        raise MogVersionError("program: the closing wait calls another routine")
    if prog.call(_CODE, 0x16C, "closing fade") != (10, 0xC50):
        raise MogVersionError("program: the closing fade calls another routine")
    return caption, Wait(prog.long(_CODE, 0x162)), Invoke(Call.FADE_OUT)


def _hand_back(prog: _Program) -> str:
    """Name of the program the ending starts next (§12.39.1)."""
    if prog.branch(_CODE, 0x172, _BRA, "hand back") != 0x114:
        raise MogVersionError("program: the ending does not return to the program loader")
    name = prog.name(_CODE, 0x114, "next program")
    prog.expect(_CODE, 0x11A, _JMP, "program loader jump")
    return name


def _picture(prog: _Program, pos: int) -> tuple[PictureLoad, int]:
    prog.expect(_LOADER, pos, _MOVE_L_ABS_D0, "loader target")
    buffer = _buffer(prog.pointer(_LOADER, pos + 2, "loader buffer"), "loader buffer")
    if prog.call(_LOADER, pos + 6, "loader target") != _TARGET:
        raise MogVersionError("program: loader target calls another routine")
    name = prog.name(_LOADER, pos + 12, "loader picture")
    prog.expect(_LOADER, pos + 18, _MOVEA_A1, "loader work buffer")
    if prog.call(_LOADER, pos + 24, "picture file") != (20, 0x186):
        raise MogVersionError("program: loader picture uses another routine")
    prog.expect(_LOADER, pos + 30, _LEA_A0, "loader palette")
    palette = _palette(prog.pointer(_LOADER, pos + 32, "palette"), "loader palette")
    if prog.call(_LOADER, pos + 36, "palette copy") != (10, 0xC2C):
        raise MogVersionError("program: loader palette copy uses another routine")
    pos += 42
    ending = prog.word(_LOADER, pos) == _JSR
    if ending:
        if prog.call(_LOADER, pos, "ending tweak") != (8, 0x938):
            raise MogVersionError("program: loader calls an unknown tweak")
        pos += 6
    tweaked = prog.word(_LOADER, pos) == _LEA_A0 and prog.word(_LOADER, pos + 6) == _JSR
    if tweaked and prog.call(_LOADER, pos + 6, "class tweak") == (8, 0x9DE):
        if _palette(prog.pointer(_LOADER, pos + 2, "tweak"), "class tweak") != palette:
            raise MogVersionError("program: class tweak applies to another palette")
        pos += 12
    else:
        tweaked = False
    return PictureLoad(name, buffer, palette, ending, tweaked), pos


@dataclass(slots=True)
class _CelWalk:
    """Symbolic run of the loader's cel placement (§12.39.3)."""

    prog: _Program
    table: dict[int, object] = field(default_factory=dict)
    a0: object = None
    a1: object = None
    overlay: object = None
    size_of: str = ""
    loads: list[tuple[object, str]] = field(default_factory=list)

    def run(self, pos: int) -> int:
        """Walk until the ``LEA name,A0 ; JSR open`` of the first data file."""
        while not self._opens_file(pos):
            pos = self._step(pos)
        return pos

    def _opens_file(self, pos: int) -> bool:
        if self.prog.word(_LOADER, pos) != _LEA_A0 or self.prog.word(_LOADER, pos + 6) != _JSR:
            return False
        return _key(self.prog.pointer(_LOADER, pos + 8, "loader call")) == (18, 0x8C)

    def _step(self, pos: int) -> int:
        word = self.prog.word(_LOADER, pos)
        if word in (_LEA_A0, _MOVEA_A1, 0x20B9, 0x23C9, _JSR):
            return self._absolute(pos, word)
        offset = self.prog.word(_LOADER, pos + 2)
        if word == 0x2149 and self.a0 == "table":
            self.table[offset // 4] = self.a1
        elif word == 0x2268 and self.a0 == "table":
            self.a1 = self.table[offset // 4]
        elif word == 0xD3C0:
            self.a1 = ("after", self.size_of)
            return pos + 2
        else:
            raise MogVersionError(f"program: loader cel walk: ${word:04X} at {pos:#x}")
        return pos + 4

    def _absolute(self, pos: int, word: int) -> int:
        operand = self.prog.pointer(_LOADER, pos + 2, "cel operand")
        if word == _LEA_A0:
            table = operand.hunk == 11
            self.a0 = "table" if table else self.prog.code.cstring(operand, "cel name")
        elif word == _MOVEA_A1:
            self.a1 = _key(operand)
        elif word == 0x20B9 and self.a0 == "table":
            self.table[0] = _key(operand)
        elif word == 0x23C9:
            self.overlay = self.a1
        elif word == _JSR:
            self._call(_key(operand))
        else:
            raise MogVersionError(f"program: loader cel walk: ${word:04X} at {pos:#x}")
        return pos + 6

    def _call(self, target: tuple[int, int]) -> None:
        if not isinstance(self.a0, str):
            raise MogVersionError("program: loader cel call without a file name")
        if target == (21, 0x6FC):
            self.loads.append((self.a1, self.a0))
        elif target == (21, 0x4FE):
            self.size_of = self.a0
        else:
            raise MogVersionError(f"program: loader cel walk calls {target}")

    def cels(self) -> tuple[tuple[tuple[int, str], ...], str]:
        slots: list[tuple[int, str]] = []
        overlay = ""
        for place, name in self.loads:
            owners = [slot for slot, value in self.table.items() if value == place]
            if place == self.overlay and not owners:
                overlay = name
            elif len(owners) == 1:
                slots.append((owners[0], name))
            else:
                raise MogVersionError(f"program: loader cel {name} has no single table slot")
        if not overlay or len({slot for slot, _ in slots}) != len(slots):
            raise MogVersionError("program: loader cel table is incomplete")
        return tuple(slots), overlay


def _data_file(prog: _Program, pos: int, what: str) -> tuple[str, int, int]:
    name = prog.name(_LOADER, pos, what)
    if prog.call(_LOADER, pos + 6, f"{what} open") != (18, 0x8C):
        raise MogVersionError(f"program: {what} is opened by another routine")
    if prog.word(_LOADER, pos + 12) not in (_MOVEA_IMM_A0, _MOVEA_A0):
        raise MogVersionError(f"program: {what} has no destination")
    prog.pointer(_LOADER, pos + 14, f"{what} destination")
    prog.expect(_LOADER, pos + 18, _MOVE_L_IMM_D0, f"{what} length")
    if prog.call(_LOADER, pos + 24, f"{what} read") != (18, 0x398):
        raise MogVersionError(f"program: {what} is read by another routine")
    if prog.call(_LOADER, pos + 30, f"{what} close") != (18, 0x77E):
        raise MogVersionError(f"program: {what} is closed by another routine")
    return name, prog.long(_LOADER, pos + 20), pos + 36


def _loader(prog: _Program) -> EndingLoad:
    pos, pictures = _LOADER_ENTRY[1], []
    while prog.word(_LOADER, pos) == _MOVE_L_ABS_D0:
        picture, pos = _picture(prog, pos)
        pictures.append(picture)
        if prog.word(_LOADER, pos) == _MOVEA_A0:
            break
    prog.expect(_LOADER, pos, _MOVEA_A0, "loader clear")
    cleared = _buffer(prog.pointer(_LOADER, pos + 2, "clear"), "loader clear")
    if prog.call(_LOADER, pos + 6, "loader clear") != _CLEAR:
        raise MogVersionError("program: loader clear calls another routine")
    pos += 12
    while prog.word(_LOADER, pos) == _MOVE_L_ABS_D0:
        picture, pos = _picture(prog, pos)
        pictures.append(picture)
    walk = _CelWalk(prog)
    pos = walk.run(pos)
    cels, overlay = walk.cels()
    stile, stile_bytes, pos = _data_file(prog, pos, "ending stile")
    music, music_bytes, _ = _data_file(prog, pos, "ending music")
    return EndingLoad(
        tuple(pictures), cleared, cels, overlay, stile, stile_bytes, music, music_bytes
    )


def _tweak_blocks(prog: _Program, pos: int, size: int, what: str) -> list[tuple[int, int]]:
    """``BTST #bit,D0 ; BEQ.S next`` blocks of ``size`` bytes; returns (bit, body) pairs."""
    blocks = []
    while prog.word(_LOADER, pos) == 0x0800:
        bit = prog.word(_LOADER, pos + 2)
        skip = prog.short_branch(_LOADER, pos + 4, what)
        body, pos = pos + 6, pos + 6 + size
        if prog.word(_LOADER, pos) == _BRA:
            pos += 4
        if skip != pos or bit > 7:
            raise MogVersionError(f"program: {what} block at {body:#x} is malformed")
        blocks.append((bit, body))
    prog.expect(_LOADER, pos, _RTS, f"{what} end")
    return blocks


def _ending_tweaks(prog: _Program) -> tuple[EndingTweak, ...]:
    prog.expect(_LOADER, 0x938, _LEA_A0, "ending tweak palette")
    prog.expect(_LOADER, 0x93E, _MOVE_W_ABS_D0, "ending tweak flags")
    if _key(prog.pointer(_LOADER, 0x940, "ending tweak flags")) != _FLAGS:
        raise MogVersionError("program: ending tweak reads another cell")
    rows = []
    for bit, body in _tweak_blocks(prog, 0x944, 42, "ending tweak"):
        entries = []
        for index in range(3):
            prog.expect(_LOADER, body + 6 * index, 0x317C, "ending tweak colour")
            displacement = prog.word(_LOADER, body + 6 * index + 4)
            entries.append((displacement // 2, prog.word(_LOADER, body + 6 * index + 2)))
        colours = []
        for index in range(3):
            at = body + 18 + 8 * index
            prog.expect(_LOADER, at, _MOVE_W_ABS, "ending ramp colour")
            if _key(prog.pointer(_LOADER, at + 4, "ramp colour")) != (_PALETTES, 2 * index):
                raise MogVersionError("program: ending ramp colours are stored elsewhere")
            colours.append(prog.word(_LOADER, at + 2))
        rows.append(EndingTweak(bit, tuple(entries), (colours[0], colours[1], colours[2])))
    return tuple(rows)


def _class_tweaks(prog: _Program) -> tuple[tuple[ClassTweak, ...], int]:
    prog.expect(_LOADER, 0x9DE, 0xD1FC, "class tweak offset")
    first, rest = divmod(prog.long(_LOADER, 0x9E0), 2)
    prog.expect(_LOADER, 0x9E4, _MOVE_W_ABS_D0, "class tweak flags")
    if rest or _key(prog.pointer(_LOADER, 0x9E6, "class tweak flags")) != _FLAGS:
        raise MogVersionError("program: class tweak reads another cell")
    rows = []
    for bit, body in _tweak_blocks(prog, 0x9EA, 16, "class tweak"):
        words = []
        for index, opcode in enumerate((0x30FC, 0x30FC, 0x30FC, 0x30BC)):
            prog.expect(_LOADER, body + 4 * index, opcode, "class tweak colour")
            words.append(prog.word(_LOADER, body + 4 * index + 2))
        rows.append(ClassTweak(bit, (words[0], words[1], words[2], words[3])))
    return tuple(rows), first


def _ramps(prog: _Program) -> tuple[RampSpec, ...]:
    rows = []
    for index in range(3):
        pos = 0x8DC + 0x1A * index
        quick = prog.word(_CODE, pos)
        if quick & 0xFF00 != 0x7000:
            raise MogVersionError("program: ramp entry is not a MOVEQ")
        prog.expect(_CODE, pos + 2, 0x3239, "ramp colour read")
        colour = prog.pointer(_CODE, pos + 4, "ramp colour")
        rate = prog.word(_CODE, pos + 10) if prog.word(_CODE, pos + 8) == 0x343C else -1
        prog.expect(_CODE, pos + 12, 0x7600, "ramp start")
        if prog.call(_CODE, pos + 14, "ramp create") != (31, 0x60) or rate < 0:
            raise MogVersionError("program: ramp is created by another routine")
        prog.expect(_CODE, pos + 20, 0x23C0, "ramp slot store")
        slot = prog.pointer(_CODE, pos + 22, "ramp slot")
        if colour != Pointer(_PALETTES, 2 * index) or _key(slot) != (0, 0x92C + 4 * index):
            raise MogVersionError("program: ramp reads or stores unexpected cells")
        rows.append(RampSpec(quick & 0xFF, index, rate))
    prog.expect(_CODE, 0x8DC + 0x1A * 3, _RTS, "ramp end")
    return tuple(rows)


def _spawn_side(prog: _Program, pos: int) -> SpawnSide:
    prog.expect(_CODE, pos, 0x247C, "spawn frame table")
    if prog.pointer(_CODE, pos + 2, "spawn frames").hunk != 11:
        raise MogVersionError("program: spawn frame table outside its hunk")
    values = []
    for index, opcode in enumerate((0x303C, 0x323C, 0x343C)):
        prog.expect(_CODE, pos + 6 + 4 * index, opcode, "spawn position")
        values.append(prog.word(_CODE, pos + 8 + 4 * index))
    prog.expect(_CODE, pos + 18, 0xD479, "spawn depth")
    depth = _WORD_CELLS.get(_key(prog.pointer(_CODE, pos + 20, "spawn depth")))
    prog.expect(_CODE, pos + 24, 0x363C, "spawn flags")
    prog.expect(_CODE, pos + 28, 0x7A00, "spawn handler")
    if prog.call(_CODE, pos + 30, "spawn") != (10, 0x198) or depth is None:
        raise MogVersionError("program: spawn helper allocates elsewhere")
    return SpawnSide(values[0], values[1], values[2], prog.word(_CODE, pos + 26), depth)


def _caption_entries(prog: _Program) -> tuple[tuple[int, int], ...]:
    prog.expect(_CODE, 0x1222, _LEA_A0, "caption palette")
    prog.expect(_CODE, 0x1228, 0xD1FC, "caption first entry")
    entry, rest = divmod(prog.long(_CODE, 0x122A), 2)
    pos, rows = 0x122E, []
    while prog.word(_CODE, pos) in (0x30FC, 0x30BC):
        rows.append((entry, prog.word(_CODE, pos + 2)))
        last = prog.word(_CODE, pos) == 0x30BC
        entry, pos = entry + 1, pos + 4
        if last:
            break
    prog.expect(_CODE, pos, _LEA_A0, "caption palette set")
    if rest or not rows or prog.call(_CODE, pos + 6, "caption fade") != _PALETTE_TO:
        raise MogVersionError("program: caption recolour is not understood")
    return tuple(rows)


def _fade(prog: _Program) -> tuple[int, int, int]:
    prog.expect(_SCREENS, 0xC52, _LEA_A0, "fade table")
    if _key(prog.pointer(_SCREENS, 0xC54, "fade table")) != _BLACK:
        raise MogVersionError("program: fade uses another table")
    if any(prog.code.hunk_data(_SCREENS)[_BLACK[1] : _BLACK[1] + 64]):
        raise MogVersionError("program: fade table is not black")
    quick = prog.word(_SCREENS, 0xC58)
    prog.expect(_SCREENS, 0xC60, _MOVE_L_IMM_D0, "fade wait")
    prog.expect(_SCREENS, 0xC70, _MOVE_L_ABS_D0, "palette period")
    period_cell = prog.pointer(_SCREENS, 0xC72, "palette period")
    prog.expect(_SCREENS, 0xC82, 0xE988, "palette wait shift")
    if quick & 0xFF00 != 0x7000 or period_cell.hunk != _SCREENS:
        raise MogVersionError("program: fade operands are not understood")
    return quick & 0xFF, prog.long(_SCREENS, 0xC62), prog.long(_SCREENS, period_cell.offset)


def _pair_frames(prog: _Program) -> int:
    prog.expect(_CODE, 0x248, 0x0C79, "pair frame count")
    if _key(prog.pointer(_CODE, 0x24C, "pair frame counter")) != (0, 0x604):
        raise MogVersionError("program: pair frame counter moved")
    return prog.word(_CODE, 0x24A)


def parse_ending_plan(executable: DecodedHunkExecutable) -> EndingPlan:
    """Decode the whole ending pass of ``program`` (§12.39)."""
    prog = _Program(executable)
    steps, next_program = _top_level(prog)
    class_tweaks, class_first = _class_tweaks(prog)
    fade_period, fade_wait, palette_period = _fade(prog)
    defaults = struct.unpack_from(">3H", prog.code.hunk_data(_PALETTES), 0)
    return EndingPlan(
        flag_bit=_flag_bit(prog),
        steps=steps,
        load=_loader(prog),
        ending_tweaks=_ending_tweaks(prog),
        class_tweaks=class_tweaks,
        class_first=class_first,
        ramp_defaults=(defaults[0], defaults[1], defaults[2]),
        ramps=_ramps(prog),
        spawns=(_spawn_side(prog, 0x2E4), _spawn_side(prog, 0x30A)),
        pair_frames=_pair_frames(prog),
        caption_entries=_caption_entries(prog),
        fade_period=fade_period,
        fade_wait=fade_wait,
        palette_period=palette_period,
        next_program=next_program,
    )
