"""Sound-only engine routines decoded from ``mog`` (ARCHITECTURE.md §10.32).

Each routine is read instruction by instruction at its position for the supported ``mog``
version. Every instruction word is compared, every call target must be the generator or a
hunk-16 front-end entry, and ids, masks, bases, limits, lists, tables, loop counts and the
initial cell values come from the user's file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.mog_code import (
    BSS_HUNK,
    CODE_HUNK,
    OP_JMP,
    OP_JSR,
    OP_LEA_A0,
    OP_RTS,
    MogCode,
    Pointer,
)
from harddaysengine.sim.m68k import s8, u16
from harddaysengine.sim.sound import (
    CounterStep,
    CycleStep,
    DrawPick,
    Emit,
    ListStep,
    SoundCells,
    SoundKind,
    SoundRequest,
    SoundRules,
    SoundStep,
)

if TYPE_CHECKING:
    from harddaysengine.assets.hunk import DecodedHunkExecutable

FRONT_HUNK = 16
GUARDIAN_HUNK = 40
_GENERATOR = Pointer(CODE_HUNK, 0xA2B0)
_GENERATOR_ENTRY = 0x48E7
_PLAY = 0x0088
_PLAY_ENTRY = 0x1239  # MOVE.B busy,D1
_FIXED = (0x0000, 0x0010, 0x0020, 0x0030)
_RELEASE = {"sound_release_0": 0x0040, "sound_release_1": 0x0052}
_CHANNELS = 4
_HIGH_BITS = 0xF0

_OP_MOVE_D0 = 0x303C  # MOVE.W #imm,D0
_OP_MOVEQ_D1 = 0x7200
_OP_MOVEQ_D7 = 0x7E00
_OP_ORI_B_ABS = 0x0039
_OP_ANDI_B_ABS = 0x0239
_OP_ANDI_W_ABS = 0x0279
_OP_ADDI_W_ABS = 0x0679
_OP_CMPI_W_ABS = 0x0C79
_OP_MOVE_W_ABS_IMM = 0x33FC
_OP_MOVE_W_ABS_D0 = 0x3039
_OP_ANDI_L_D0 = 0x0280
_OP_ANDI_W_D0 = 0x0240
_OP_ADDI_W_D0 = 0x0640
_OP_ADDI_L_D0 = 0x0680
_OP_CMP_W_D0 = 0xB07C
_OP_BEQ = 0x6700
_OP_BNE = 0x6600
_OP_BLT = 0x6D00
_OP_BRA_S = 0x6000
_OP_BSR_W = 0x6100
_OP_FOLD = (0x6702, 0x5340)  # BEQ.S +2 ; SUBQ.W #1,D0
_OP_CMPI_W_INDEX = (0x0C70, 0xFFFF, 0x0000)  # CMPI.W #$FFFF,(0,A0,D0.W)
_OP_MOVEQ_0 = 0x7000
_OP_READ_WORD = (0x3030, 0x0000)  # MOVE.W (0,A0,D0.W),D0
_OP_READ_BYTE = (0x1030, 0x0000)  # MOVE.B (0,A0,D0.W),D0
_OP_EXT_W = 0x4880
_OP_ZERO_EXTEND = (0x0240, 0x00FF)
_OP_SAVE_D7 = 0x2F07
_OP_LOAD_D7 = 0x2E1F
_OP_DBRA_D7 = 0x51CF
_SHORT_MASK = 0xFF00

_MASKED = {
    "sound_pick_1": 0x71D2,
    "sound_pick_14": 0x71EC,
    "sound_pick_2": 0x721C,
    "sound_pick_8": 0x7234,
    "sound_pick_9": 0x724C,
    "sound_pick_3": 0x7264,
    "sound_pick_4": 0x727C,
}
_CYCLE = ("sound_pick_5", 0x7296)
_LISTS = (("sound_pick_6", 0x72C0), ("sound_pick_7", 0x72C8))
_FIXED_RUNS = (
    ("guardian_fixed_sound", GUARDIAN_HUNK, 0x041C, 1),
    ("guardian_fixed_sounds", GUARDIAN_HUNK, 0x0A84, 4),
    ("dragon_sound_0", CODE_HUNK, 0x6D28, 1),
    ("dragon_sound_1", CODE_HUNK, 0x6D32, 1),
)
_BRANCH_PAIRS = (("sound_pick_10", 0x0428), ("sound_pick_13", 0x0AAE))
_SIGNED_TABLE = ("guardian_sound", 0x049E)
_DISCARDED = ("sound_pick_dice", 0xA8C2)
_OP_MOVEQ_0 = 0x7000
_COUNTER = ("sound_pick_11", 0x04BC)
_LOOP_TABLE = ("sound_pick_12", 0x0682)


@dataclass(slots=True)
class _Reader:
    """A cursor over one hunk of ``mog`` with checked reads."""

    mog: MogCode
    hunk: int
    pos: int
    what: str

    def peek(self) -> int:
        return self.mog.hunk_word(self.hunk, self.pos)

    def words(self, *expected: int) -> None:
        for word in expected:
            if self.peek() != word:
                msg = (
                    f"mog: {self.what}: expected ${word:04X} at hunk {self.hunk} "
                    f"offset {self.pos}, found ${self.peek():04X}"
                )
                raise MogVersionError(msg)
            self.pos += 2

    def immediate(self, opcode: int) -> int:
        self.words(opcode)
        return self.take_word()

    def take_word(self) -> int:
        value = self.peek()
        self.pos += 2
        return value

    def take_long(self) -> int:
        value = self.mog.hunk_word(self.hunk, self.pos) << 16
        value |= self.mog.hunk_word(self.hunk, self.pos + 2)
        self.pos += 4
        return value

    def pointer(self) -> Pointer:
        target = self.mog.pointer(self.pos, self.what, self.hunk)
        self.pos += 4
        return target

    def cell(self, opcode: int, hunk: int) -> tuple[int, Pointer]:
        """``op #imm,abs.l``: the immediate and the cell it touches (which must be in ``hunk``)."""
        value = self.immediate(opcode)
        cell = self.pointer()
        if cell.hunk != hunk:
            raise MogVersionError(f"mog: {self.what}: a cell outside hunk {hunk}")
        return value, cell

    def same_cell(self, opcode: int, cell: Pointer) -> int:
        value, found = self.cell(opcode, cell.hunk)
        if found != cell:
            raise MogVersionError(f"mog: {self.what}: the routine touches another cell")
        return value

    def branch(self, opcode: int) -> int:
        """Target of a ``Bcc`` (short or word) with condition ``opcode``."""
        word = self.peek()
        if word & _SHORT_MASK != opcode:
            raise MogVersionError(f"mog: {self.what}: expected a branch at {self.pos}")
        base = self.pos + 2
        if word & 0xFF:
            self.pos += 2
            return base + s8(word & 0xFF)
        self.pos += 4
        return base + (self.mog.hunk_word(self.hunk, base) ^ 0x8000) - 0x8000

    def dbra(self) -> int:
        """Target of a ``DBRA D7``."""
        self.words(_OP_DBRA_D7)
        base = self.pos
        self.pos += 2
        return base + (self.mog.hunk_word(self.hunk, base) ^ 0x8000) - 0x8000

    def generator(self) -> None:
        self.words(OP_JSR)
        if self.pointer() != _GENERATOR:
            raise MogVersionError(f"mog: {self.what}: the draw calls another routine")

    def call(self) -> tuple[bool, SoundKind, int]:
        """A ``JSR`` / ``JMP`` to a front-end entry: (is ``JMP``, kind, fixed channel)."""
        opcode = self.peek()
        if opcode not in (OP_JSR, OP_JMP):
            raise MogVersionError(f"mog: {self.what}: expected a call at {self.pos}")
        self.pos += 2
        target = self.pointer()
        if target.hunk != FRONT_HUNK:
            raise MogVersionError(f"mog: {self.what}: a call outside the sound front end")
        if target.offset == _PLAY:
            return opcode == OP_JMP, SoundKind.PLAY, 0
        if target.offset in _FIXED:
            return opcode == OP_JMP, SoundKind.FIXED, _FIXED.index(target.offset)
        raise MogVersionError(f"mog: {self.what}: an unknown front-end entry")

    def play_call(self) -> bool:
        tail, kind, _ = self.call()
        if kind != SoundKind.PLAY:
            raise MogVersionError(f"mog: {self.what}: expected the play entry")
        return tail


def _check_front_end(mog: MogCode) -> None:
    what = "sound front end"
    if mog.hunk_word(CODE_HUNK, _GENERATOR.offset) != _GENERATOR_ENTRY:
        raise MogVersionError("mog: the generator entry moved")
    _Reader(mog, FRONT_HUNK, _PLAY, what).words(_PLAY_ENTRY)
    for channel, entry in enumerate(_FIXED):
        reader = _Reader(mog, FRONT_HUNK, entry, what)
        reader.words(_OP_MOVEQ_D1 | channel)
        if reader.cell(_OP_ORI_B_ABS, FRONT_HUNK)[0] != 1 << channel:
            raise MogVersionError("mog: a fixed sound entry marks another channel")


def _release(mog: MogCode, entry: int) -> SoundStep:
    reader = _Reader(mog, FRONT_HUNK, entry, "sound release")
    mask = reader.cell(_OP_ANDI_B_ABS, FRONT_HUNK)[0] & 0xFF
    cleared = [ch for ch in range(_CHANNELS) if not mask & (1 << ch)]
    if len(cleared) != 1 or mask & _HIGH_BITS:
        raise MogVersionError("mog: a release mask does not name one channel")
    silence = reader.immediate(_OP_MOVE_D0)
    if reader.branch(_OP_BSR_W) != _PLAY:
        raise MogVersionError("mog: a release does not play its silence sequence")
    reader.words(OP_RTS)
    return Emit(SoundRequest(SoundKind.RELEASE, silence, cleared[0], mask))


def _fixed_run(mog: MogCode, hunk: int, start: int, count: int) -> tuple[SoundStep, ...]:
    reader = _Reader(mog, hunk, start, "fixed sounds")
    steps: list[SoundStep] = []
    for index in range(count):
        sound = reader.immediate(_OP_MOVE_D0)
        tail, kind, channel = reader.call()
        if kind != SoundKind.FIXED or tail != (index == count - 1 and reader.hunk == CODE_HUNK):
            raise MogVersionError("mog: fixed sounds: unexpected call form")
        steps.append(Emit(SoundRequest(SoundKind.FIXED, sound, channel)))
    if hunk != CODE_HUNK:
        reader.words(OP_RTS)
    return tuple(steps)


def _check_mask(mask: int, what: str) -> int:
    if mask > 0xFF or mask & (mask + 1):
        raise MogVersionError(f"mog: {what}: the draw mask is not a low-bit mask")
    return mask


def _masked_pick(reader: _Reader) -> tuple[DrawPick, bool]:
    reader.generator()
    reader.words(_OP_ANDI_L_D0)
    mask = _check_mask(reader.take_long(), reader.what)
    fold = reader.peek() == _OP_FOLD[0]
    if fold:
        reader.words(*_OP_FOLD)
    if reader.peek() == _OP_ADDI_L_D0:
        reader.pos += 2
        base = u16(reader.take_long())
    else:
        base = reader.immediate(_OP_ADDI_W_D0)
    choices = tuple((u16(base + (v - 1 if fold and v else v)),) for v in range(mask + 1))
    return DrawPick(mask, choices), reader.play_call()


def _masked(mog: MogCode, start: int) -> tuple[SoundStep, ...]:
    reader = _Reader(mog, CODE_HUNK, start, "sound picker")
    steps: list[SoundStep] = []
    tail = False
    while not tail:
        step, tail = _masked_pick(reader)
        steps.append(step)
    return tuple(steps)


def _cycle(mog: MogCode, start: int) -> tuple[CycleStep, Pointer]:
    reader = _Reader(mog, CODE_HUNK, start, "sound cycle")
    step, cell = reader.cell(_OP_ADDI_W_ABS, CODE_HUNK)
    limit = reader.same_cell(_OP_CMPI_W_ABS, cell)
    if reader.branch(_OP_BLT) != reader.pos + 8:
        raise MogVersionError("mog: sound cycle: unexpected limit branch")
    reset = reader.same_cell(_OP_MOVE_W_ABS_IMM, cell)
    reader.words(_OP_MOVE_W_ABS_D0)
    if reader.pointer() != cell:
        raise MogVersionError("mog: sound cycle: reads another cell")
    base = reader.immediate(_OP_ADDI_W_D0)
    if not reader.play_call():
        raise MogVersionError("mog: sound cycle: expected a tail call")
    return CycleStep(step, limit, reset, base), cell


def _list_tail(mog: MogCode, start: int) -> tuple[int, int, int, Pointer]:
    """The shared tail: (step, terminator, reset, offset cell)."""
    reader = _Reader(mog, CODE_HUNK, start, "sound list")
    step, cell = reader.cell(_OP_ADDI_W_ABS, CODE_HUNK)
    reader.words(_OP_MOVE_W_ABS_D0)
    if reader.pointer() != cell:
        raise MogVersionError("mog: sound list: reads another cell")
    reader.words(*_OP_CMPI_W_INDEX)
    terminator = _OP_CMPI_W_INDEX[1]
    if reader.branch(_OP_BNE) != reader.pos + 10:
        raise MogVersionError("mog: sound list: unexpected terminator branch")
    reset = reader.same_cell(_OP_MOVE_W_ABS_IMM, cell)
    reader.words(_OP_MOVEQ_0, *_OP_READ_WORD)
    if not reader.play_call():
        raise MogVersionError("mog: sound list: expected a tail call")
    return step, terminator, reset, cell


def _list_starts(mog: MogCode) -> tuple[list[int], int]:
    """Each list's address and the shared tail's position."""
    starts: list[int] = []
    tails: set[int] = set()
    for _name, entry in _LISTS:
        reader = _Reader(mog, CODE_HUNK, entry, "sound list")
        reader.words(OP_LEA_A0)
        target = reader.pointer()
        if target.hunk != CODE_HUNK:
            raise MogVersionError("mog: sound list outside the code hunk")
        starts.append(target.offset)
        branches = reader.peek() & _SHORT_MASK == _OP_BRA_S
        tails.add(reader.branch(_OP_BRA_S) if branches else reader.pos)
    if len(tails) != 1:
        raise MogVersionError("mog: the sound lists do not share one tail")
    return starts, tails.pop()


def _list_words(mog: MogCode, start: int, terminator: int) -> tuple[int, ...]:
    pos = start
    while mog.hunk_word(CODE_HUNK, pos) != terminator:
        pos += 2
    return tuple(mog.hunk_word(CODE_HUNK, p) for p in range(start, pos + 2, 2))


def _lists(mog: MogCode) -> tuple[list[tuple[str, tuple[SoundStep, ...]]], Pointer]:
    starts, tail = _list_starts(mog)
    step, terminator, reset, cell = _list_tail(mog, tail)
    routines: list[tuple[str, tuple[SoundStep, ...]]] = [
        (name, (ListStep(0, _list_words(mog, start, terminator), step, terminator, reset, slot),))
        for slot, ((name, _), start) in enumerate(zip(_LISTS, starts, strict=True))
    ]
    return routines, cell


def _pair(mog: MogCode, start: int) -> tuple[int, ...]:
    reader = _Reader(mog, GUARDIAN_HUNK, start, "sound pair")
    pair = []
    for _ in range(2):
        pair.append(reader.immediate(_OP_MOVE_D0))
        if reader.play_call():
            raise MogVersionError("mog: sound pair: unexpected tail call")
    reader.words(OP_RTS)
    return tuple(pair)


def _branch_pairs(mog: MogCode, start: int) -> tuple[SoundStep, ...]:
    reader = _Reader(mog, GUARDIAN_HUNK, start, "sound pairs")
    reader.generator()
    mask = _check_mask(reader.immediate(_OP_ANDI_W_D0), reader.what)
    targets: dict[int, int] = {}
    while reader.peek() & _SHORT_MASK in (_OP_BEQ, _OP_CMP_W_D0 & _SHORT_MASK):
        value = reader.immediate(_OP_CMP_W_D0) if reader.peek() == _OP_CMP_W_D0 else 0
        targets.setdefault(value, reader.branch(_OP_BEQ))
    choices = tuple(_pair(mog, targets.get(v, reader.pos)) for v in range(mask + 1))
    return (DrawPick(mask, choices),)


def _table_pick(reader: _Reader, *, signed: bool) -> DrawPick:
    reader.generator()
    mask = _check_mask(reader.immediate(_OP_ANDI_W_D0), reader.what)
    reader.words(OP_LEA_A0)
    table = reader.pointer()
    reader.words(*_OP_READ_BYTE)
    reader.words(*((_OP_EXT_W,) if signed else _OP_ZERO_EXTEND))
    data = reader.mog.hunk_data(table.hunk)
    raw = data[table.offset : table.offset + mask + 1]
    if len(raw) != mask + 1:
        raise MogVersionError(f"mog: {reader.what}: the table runs past its hunk")
    choices = tuple((u16(s8(b)) if signed else b,) for b in raw)
    return DrawPick(mask, choices)


def _discarded(mog: MogCode, start: int) -> tuple[SoundStep, ...]:
    """Emit the decoded fixed-channel dice sound cleanly without a discarded draw (§12.32)."""
    reader = _Reader(mog, CODE_HUNK, start, "dice sound")
    reader.generator()
    _check_mask(reader.immediate(_OP_ANDI_W_D0), reader.what)
    reader.words(OP_LEA_A0)
    table = reader.pointer()
    reader.words(_OP_MOVEQ_0, *_OP_READ_BYTE)
    tail, kind, channel = reader.call()
    if tail or kind != SoundKind.FIXED:
        raise MogVersionError("mog: dice sound: expected a fixed-channel call")
    reader.words(OP_RTS)
    sound = mog.hunk_data(table.hunk)[table.offset]
    return (Emit(SoundRequest(SoundKind.FIXED, sound, channel)),)


def _signed_table(mog: MogCode, start: int) -> tuple[SoundStep, ...]:
    reader = _Reader(mog, GUARDIAN_HUNK, start, "sound table")
    pick = _table_pick(reader, signed=True)
    if reader.play_call():
        raise MogVersionError("mog: sound table: unexpected tail call")
    reader.words(OP_RTS)
    return (pick,)


def _loop_table(mog: MogCode, start: int) -> tuple[SoundStep, ...]:
    reader = _Reader(mog, GUARDIAN_HUNK, start, "sound loop")
    moveq = reader.take_word()
    if moveq & _SHORT_MASK != _OP_MOVEQ_D7 or s8(moveq & 0xFF) < 0:
        raise MogVersionError("mog: sound loop: unexpected loop count")
    loop = reader.pos
    pick = _table_pick(reader, signed=False)
    reader.words(_OP_SAVE_D7)
    if reader.play_call():
        raise MogVersionError("mog: sound loop: unexpected tail call")
    reader.words(_OP_LOAD_D7)
    if reader.dbra() != loop:
        raise MogVersionError("mog: sound loop: the loop does not close")
    reader.words(OP_RTS)
    return (pick,) * (s8(moveq & 0xFF) + 1)


def _counter(mog: MogCode, start: int) -> tuple[CounterStep, Pointer]:
    reader = _Reader(mog, GUARDIAN_HUNK, start, "sound counter")
    step, cell = reader.cell(_OP_ADDI_W_ABS, GUARDIAN_HUNK)
    mask = reader.same_cell(_OP_ANDI_W_ABS, cell)
    if reader.branch(_OP_BNE) != reader.pos + 10:
        raise MogVersionError("mog: sound counter: unexpected branch")
    sound = reader.immediate(_OP_MOVE_D0)
    if reader.play_call():
        raise MogVersionError("mog: sound counter: unexpected tail call")
    reader.words(OP_RTS)
    return CounterStep(step, mask, sound), cell


def _cell_word(mog: MogCode, cell: Pointer) -> int:
    if cell.hunk == BSS_HUNK:
        raise MogVersionError("mog: a sound cell in BSS has no stored value")
    return mog.hunk_word(cell.hunk, cell.offset)


def parse_sound_rules(executable: DecodedHunkExecutable) -> SoundRules:
    """Every sound-only engine routine and the initial cell values (§10.32)."""
    return sound_rules(MogCode(executable))


def sound_rules(mog: MogCode) -> SoundRules:
    """Decode from an already version-checked executable (§10.32)."""
    _check_front_end(mog)
    routines: list[tuple[str, tuple[SoundStep, ...]]] = [
        (name, _masked(mog, start)) for name, start in _MASKED.items()
    ]
    cycle, plain = _cycle(mog, _CYCLE[1])
    routines.append((_CYCLE[0], (cycle,)))
    lists, offset = _lists(mog)
    routines.extend(lists)
    routines.extend((name, (_release(mog, entry),)) for name, entry in _RELEASE.items())
    routines.extend(
        (name, _fixed_run(mog, hunk, start, count)) for name, hunk, start, count in _FIXED_RUNS
    )
    routines.extend((name, _branch_pairs(mog, start)) for name, start in _BRANCH_PAIRS)
    routines.append((_SIGNED_TABLE[0], _signed_table(mog, _SIGNED_TABLE[1])))
    routines.append((_DISCARDED[0], _discarded(mog, _DISCARDED[1])))
    counter, counter_cell = _counter(mog, _COUNTER[1])
    routines.append((_COUNTER[0], (counter,)))
    routines.append((_LOOP_TABLE[0], _loop_table(mog, _LOOP_TABLE[1])))
    initial_offset = _cell_word(mog, offset)
    cells = SoundCells(
        _cell_word(mog, plain), [initial_offset] * len(_LISTS), _cell_word(mog, counter_cell)
    )
    return SoundRules(tuple(routines), cells)
