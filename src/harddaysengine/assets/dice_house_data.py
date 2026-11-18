"""The dice house decoded from ``mog`` (ARCHITECTURE.md §12.32).

Every operand is read at its position for the supported version with the instruction word (and
any relocated target) compared first. The region list, colour-ramp table, dice draws and text
suffix are walked instruction by instruction and stop with an error at anything unexpected.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.mog_code import BSS_HUNK, CODE_HUNK, DATA_HUNK, MogCode, Pointer
from harddaysengine.sim.dice_house import DiceHouse, DiceLine, DiceRegion, Thrower
from harddaysengine.sim.m68k import s8

if TYPE_CHECKING:
    from harddaysengine.assets.hunk import DecodedHunkExecutable

SCRIPT_HUNK = 42
_PLAYER = Pointer(BSS_HUNK, 0xCE4)
_SCRIPT_CELL = Pointer(BSS_HUNK, 0xC0C)
_ROUND_PALETTE = Pointer(BSS_HUNK, 0x1E6)
_ROLL_PALETTE = Pointer(BSS_HUNK, 0x226)
_POINTER_X = Pointer(DATA_HUNK, 0x9030)
_SCRATCH = Pointer(11, 4)
_PICTURE_LOADER = Pointer(25, 0x186)
_CEL_LOADER = Pointer(26, 0x6FC)
_CEL_DRAWER = Pointer(28, 0x22C)
_ADD_REGION = Pointer(CODE_HUNK, 0x9480)
_FADE_OUT = Pointer(CODE_HUNK, 0x8B26)
_RAMP = Pointer(CODE_HUNK, 0xAADE)
_SPAWN_CALL = Pointer(CODE_HUNK, 0x7084)
_COPY_PALETTE = Pointer(CODE_HUNK, 0x907C)
_FIXED = (0x0000, 0x0010, 0x0020, 0x0030)
_FRONT = 16

_FRAME_WAIT = Pointer(SCRIPT_HUNK, 0x3F8)
_CEL_TABLE = Pointer(SCRIPT_HUNK, 0x3DE)
_DICE = Pointer(SCRIPT_HUNK, 0x3FE)
_BET = Pointer(SCRIPT_HUNK, 0x402)

_GOLD_TEST = 0xA318
_TAVERN = 0xA34C
_ROUND_COPY = 0xA35E
_TABLE = 0xA38A
_ROLL_COPY = 0xA39C
_CELS = 0xA3C6
_REGIONS = 0xA3FA
_RAMP_CALL = 0xA4DA
_SOUNDS = (0xA4EC, 0xA4F6, 0xA500)
_POINTER = 0xA510
_GOLD_TEXT = 0xA5BE
_SPAWN = 0xA60C
_IDLE_SCRIPT, _IDLE_WAIT = 0xA65E, 0xA668
_BET_KIND, _LEAVE_KIND = 0xA690, 0xA69A
_BET_CODE = 0xA6B0
_THROW_SCRIPT, _THROW_WAIT = 0xA6D8, 0xA6E2
_DICE_COUNT = 0xA728
_DICE_DRAWS, _DICE_STRIDE = 0xA76A, 0x1C
_FIELDS = {"bet": (0xA7E6, 0xA7EC), "won": (0xA846, 0xA852), "gold": (0xA802, 0xA806)}
_LOST, _WON = 0xA816, 0xA87C
_SUFFIX = 0xA826
_ROLL_WAIT = 0xA8B2

_OP_LEA_A0, _OP_LEA_A1, _OP_LEA_A2 = 0x41F9, 0x43F9, 0x45F9
_OP_JSR, _OP_RTS = 0x4EB9, 0x4E75
_OP_MOVEA_A1 = 0x2279
_OP_MOVEA_A0 = 0x2079
_OP_STORE_W, _OP_STORE_L = 0x317C, 0x217C
_OP_MOVE_W_IMM_ABS = 0x33FC
_OP_MOVE_L_IMM_ABS = 0x23FC
_OP_MOVE_W_D0, _OP_MOVE_W_D1, _OP_MOVE_W_D2 = 0x303C, 0x323C, 0x343C
_OP_MOVE_L_D0 = 0x203C
_OP_CMPI_L_A0 = 0x0CA8
_OP_CMPI_L_A1 = 0x0CA9
_OP_PUSH_W_A0 = 0x30FC  # MOVE.W #imm,(A0)+
_OP_PUT_W_A0 = 0x30BC  # MOVE.W #imm,(A0)
_OP_PUSH_B_A2 = 0x14FC  # MOVE.B #imm,(A2)+
_OP_PUT_B_A2 = 0x14BC  # MOVE.B #imm,(A2)
_MOVEQ_MASK = 0xFF00
_REGION_FIELDS = {"x": 12, "y": 14, "width": 4, "height": 6, "kind": 16, "code": 20}
_KIND_OFFSET = 0x36


class _Code:
    """Checked reads of hunk 0 (and the hunk a pointer names)."""

    def __init__(self, mog: MogCode) -> None:
        self.mog = mog

    def word(self, pos: int) -> int:
        return self.mog.word(pos)

    def expect(self, pos: int, *words: int) -> None:
        for i, word in enumerate(words):
            if self.mog.word(pos + 2 * i) != word:
                raise MogVersionError(f"mog: dice house: unexpected code at {pos + 2 * i}")

    def target(self, pos: int, hunk: int | None = None) -> Pointer:
        found = self.mog.pointer(pos, "dice house")
        if hunk is not None and found.hunk != hunk:
            raise MogVersionError(f"mog: dice house: the operand at {pos} points elsewhere")
        return found

    def at(self, pos: int, wanted: Pointer) -> None:
        if self.target(pos) != wanted:
            raise MogVersionError(f"mog: dice house: the operand at {pos} points elsewhere")

    def immediate(self, pos: int, opcode: int) -> int:
        self.expect(pos, opcode)
        return self.mog.word(pos + 2)

    def store(self, pos: int, opcode: int, cell: Pointer) -> int:
        value = self.immediate(pos, opcode)
        self.at(pos + 4, cell)
        return value

    def moveq(self, pos: int, register: int = 0) -> int:
        """The immediate of ``MOVEQ #imm,D<register>`` at ``pos``."""
        word = self.mog.word(pos)
        if word & _MOVEQ_MASK != 0x7000 | register << 9:
            raise MogVersionError(f"mog: dice house: expected MOVEQ at {pos}")
        return s8(word & 0xFF)


def _file(code: _Code, pos: int, register: Pointer, loader: Pointer) -> str:
    code.expect(pos, _OP_LEA_A0)
    name = code.target(pos + 2, SCRIPT_HUNK)
    code.expect(pos + 6, _OP_MOVEA_A1)
    code.at(pos + 8, register)
    code.expect(pos + 12, _OP_JSR)
    code.at(pos + 14, loader)
    return code.mog.cstring(name, "dice house file")


def _palette_copy(code: _Code, pos: int, into: Pointer) -> None:
    code.expect(pos, _OP_LEA_A0)
    code.target(pos + 2)
    code.expect(pos + 6, _OP_LEA_A1)
    code.at(pos + 8, into)
    code.expect(pos + 12, _OP_JSR)
    code.at(pos + 14, _COPY_PALETTE)


def _regions(code: _Code) -> tuple[DiceRegion, ...]:
    fields: dict[int, int] = {}
    regions: list[DiceRegion] = []
    pos = _REGIONS
    while True:
        opcode = code.word(pos)
        if opcode == _OP_LEA_A0:
            code.at(pos + 2, _SCRATCH)
            pos += 6
        elif opcode == _OP_STORE_W:
            fields[code.word(pos + 4)] = code.word(pos + 2)
            pos += 6
        elif opcode == _OP_STORE_L:
            fields[code.word(pos + 6)] = code.mog.long(pos + 2)
            pos += 8
        elif code.target(pos + 2) == _FADE_OUT:
            break
        else:
            code.expect(pos, _OP_JSR)
            code.at(pos + 2, _ADD_REGION)
            pos += 6
            if any(d not in fields for d in _REGION_FIELDS.values()):
                raise MogVersionError("mog: a dice region leaves a field unset")
            regions.append(DiceRegion(**{k: fields[d] for k, d in _REGION_FIELDS.items()}))
    if not regions:
        raise MogVersionError("mog: the dice house registers no region")
    return tuple(regions)


def _ramps(code: _Code) -> tuple[tuple[int, tuple[int, ...]], ...]:
    """The kind rows of the five-shade colour ramp (§12.32)."""
    pos = _RAMP.offset
    code.expect(pos, _OP_MOVEA_A1)
    code.at(pos + 2, _PLAYER)
    code.expect(pos + 6, 0xD1C0)  # ADDA.L D0,A0
    pos += 8
    rows = []
    while code.word(pos) == _OP_CMPI_L_A1:
        kind = code.mog.long(pos + 2)
        code.expect(pos + 6, _KIND_OFFSET)
        skip = pos + 10 + (code.word(pos + 8) & 0xFF)
        code.expect(pos + 8, 0x6600 | (skip - pos - 10))
        words = tuple(code.immediate(pos + 10 + 4 * i, _OP_PUSH_W_A0) for i in range(4))
        last = code.immediate(pos + 26, _OP_PUT_W_A0)
        rows.append((kind, (*words, last)))
        pos = skip
    code.expect(pos, _OP_RTS)
    return tuple(rows)


def _sounds(code: _Code) -> tuple[tuple[int, int], ...]:
    out = []
    for pos in _SOUNDS:
        seq = code.immediate(pos, _OP_MOVE_W_D0)
        code.expect(pos + 4, _OP_JSR)
        entry = code.target(pos + 6, _FRONT)
        if entry.offset not in _FIXED:
            raise MogVersionError("mog: a dice-house sound is not a fixed-channel start")
        out.append((seq, _FIXED.index(entry.offset)))
    return tuple(out)


def _script(code: _Code, pos: int) -> int:
    code.expect(pos, _OP_MOVE_L_IMM_ABS)
    script = code.target(pos + 2, SCRIPT_HUNK)
    code.at(pos + 6, _SCRIPT_CELL)
    return script.offset


def _thrower(code: _Code) -> Thrower:
    code.expect(_SPAWN, _OP_LEA_A0)
    spawn_script = code.target(_SPAWN + 2, SCRIPT_HUNK).offset
    code.expect(_SPAWN + 6, _OP_LEA_A2)
    code.at(_SPAWN + 8, _CEL_TABLE)
    x = code.immediate(_SPAWN + 12, _OP_MOVE_W_D0)
    height = code.immediate(_SPAWN + 16, _OP_MOVE_W_D1)
    depth = code.immediate(_SPAWN + 20, _OP_MOVE_W_D2)
    facing = code.immediate(_SPAWN + 24, 0x163C) & 0xFF  # MOVE.B #imm,D3
    kind_word = code.word(_SPAWN + 28)
    if kind_word & _MOVEQ_MASK != 0x7A00:  # MOVEQ #kind,D5
        raise MogVersionError("mog: unexpected thrower type")
    code.expect(_SPAWN + 30, _OP_JSR)
    code.at(_SPAWN + 32, _SPAWN_CALL)
    spawn_wait = code.store(_SPAWN + 36, _OP_MOVE_W_IMM_ABS, _FRAME_WAIT)
    idle = _script(code, _IDLE_SCRIPT)
    if idle != spawn_script:
        raise MogVersionError("mog: the thrower spawns on another script")
    code.expect(_BET_CODE, 0x33E8, _REGION_FIELDS["code"])  # MOVE.W 20(A0),bet
    code.at(_BET_CODE + 4, _BET)
    kinds = []
    for pos in (_BET_KIND, _LEAVE_KIND):
        code.expect(pos, _OP_CMPI_L_A0)
        code.expect(pos + 6, _REGION_FIELDS["kind"])
        kinds.append(code.mog.long(pos + 2))
    return Thrower(
        x, height, depth, facing, kind_word & 0xFF, idle, _script(code, _THROW_SCRIPT),
        spawn_wait,
        code.store(_IDLE_WAIT, _OP_MOVE_W_IMM_ABS, _FRAME_WAIT),
        code.store(_THROW_WAIT, _OP_MOVE_W_IMM_ABS, _FRAME_WAIT),
        kinds[0], kinds[1],
    )  # fmt: skip


def _gold_at(code: _Code) -> tuple[int, int, int]:
    x = code.immediate(_GOLD_TEXT, _OP_MOVE_W_D0)
    y = code.immediate(_GOLD_TEXT + 4, _OP_MOVE_W_D1)
    code.expect(_GOLD_TEXT + 8, 0x7400, 0x08C2)  # MOVEQ #0,D2 ; BSET #n,D2
    return x, y, 1 << code.word(_GOLD_TEXT + 12)


def _dice_at(code: _Code) -> tuple[tuple[int, int], ...]:
    count = code.moveq(_DICE_COUNT, 7) + 1
    out = []
    for i in range(count):
        pos = _DICE_DRAWS + i * _DICE_STRIDE
        code.expect(pos, _OP_MOVEA_A0)
        code.at(pos + 2, _CEL_TABLE)
        code.expect(pos + 6, 0x7000, 0x1039)  # MOVEQ #0,D0 ; MOVE.B die,D0
        code.at(pos + 10, Pointer(SCRIPT_HUNK, _DICE.offset + i))
        x = code.immediate(pos + 14, _OP_MOVE_W_D1)
        y = code.immediate(pos + 18, _OP_MOVE_W_D2)
        code.expect(pos + 22, _OP_JSR)
        code.at(pos + 24, _CEL_DRAWER)
        out.append((x, y))
    return tuple(out)


def _suffix(code: _Code) -> str:
    pos, chars = _SUFFIX, []
    while code.word(pos) == _OP_PUSH_B_A2:
        chars.append(chr(code.word(pos + 2) & 0xFF))
        pos += 4
    code.expect(pos, _OP_PUT_B_A2, 0, _OP_RTS)
    return "".join(chars)


def _field_targets(code: _Code) -> dict[str, Pointer]:
    out = {}
    for name, (source, lea) in _FIELDS.items():
        if name == "bet":
            code.expect(source, 0x3039)
            code.at(source + 2, _BET)
        elif name == "gold":
            code.expect(source, 0x3028, 0x004A)  # MOVE.W 74(A0),D0: the gold
        else:
            code.expect(source, 0xC0C1)  # MULU D1,D0: the winnings
        code.expect(lea, _OP_LEA_A2)
        out[name] = code.target(lea + 2, SCRIPT_HUNK)
    return out


def _lines(code: _Code, pos: int, fields: dict[str, Pointer]) -> tuple[DiceLine, ...]:
    code.expect(pos, _OP_LEA_A0)
    record = code.target(pos + 2, SCRIPT_HUNK)
    mog, out, seen = code.mog, [], set()
    while True:
        if record in seen:
            raise MogVersionError("mog: cyclic dice text list")
        seen.add(record)
        text = mog.pointer(record.offset, "dice text", SCRIPT_HUNK)
        string = mog.cstring(text, "dice text")
        x, y, style = (mog.hunk_word(SCRIPT_HUNK, record.offset + d) for d in (4, 6, 8))
        found = [
            n for n, f in fields.items() if text.offset <= f.offset <= text.offset + len(string)
        ]
        if found:
            cut = fields[found[0]].offset - text.offset
            out.append(DiceLine(string[:cut], x, y, style, found[0]))
        else:
            out.append(DiceLine(string, x, y, style))
        if mog.reloc(record.offset + 10, SCRIPT_HUNK) is None:
            return tuple(out)
        record = mog.pointer(record.offset + 10, "next dice text", SCRIPT_HUNK)


def dice_house(mog: MogCode) -> DiceHouse:
    """Decode from an already version-checked executable (§12.32)."""
    code = _Code(mog)
    code.expect(_GOLD_TEST, _OP_MOVEA_A0)
    code.at(_GOLD_TEST + 2, _PLAYER)
    code.expect(_GOLD_TEST + 6, 0x4A68, 0x004A, 0x6E02, _OP_RTS)
    _palette_copy(code, _ROUND_COPY, _ROUND_PALETTE)
    _palette_copy(code, _ROLL_COPY, _ROLL_PALETTE)
    code.expect(_RAMP_CALL, _OP_LEA_A0)
    code.at(_RAMP_CALL + 2, _ROUND_PALETTE)
    code.expect(_RAMP_CALL + 6, _OP_MOVE_L_D0)
    code.expect(_RAMP_CALL + 12, _OP_JSR)
    code.at(_RAMP_CALL + 14, _RAMP)
    fields = _field_targets(code)
    return DiceHouse(
        tavern=_file(code, _TAVERN, Pointer(35, 4), _PICTURE_LOADER),
        table=_file(code, _TABLE, Pointer(35, 4), _PICTURE_LOADER),
        cel_file=_file(code, _CELS, _CEL_TABLE, _CEL_LOADER),
        regions=_regions(code),
        ramp_entry=mog.long(_RAMP_CALL + 8) // 2,
        ramps=_ramps(code),
        sounds=_sounds(code),
        pointer_x=code.store(_POINTER, _OP_MOVE_W_IMM_ABS, _POINTER_X),
        thrower=_thrower(code),
        gold_at=_gold_at(code),
        dice_at=_dice_at(code),
        lost=_lines(code, _LOST, {k: fields[k] for k in ("bet", "gold")}),
        won=_lines(code, _WON, {k: fields[k] for k in ("won", "gold")}),
        suffix=_suffix(code),
        roll_wait=code.moveq(_ROLL_WAIT),
    )


def parse_dice_house(executable: DecodedHunkExecutable) -> DiceHouse:
    """The dice house of the user's ``mog`` (§12.32)."""
    return dice_house(MogCode(executable))
