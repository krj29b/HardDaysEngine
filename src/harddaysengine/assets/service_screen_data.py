"""Healer and mystic screens decoded from ``mog`` (ARCHITECTURE.md §12.31).

The opening, region and redraw routines are walked instruction by instruction: every word must
be one of the few shapes these routines use, and every relocated operand must point where that
shape expects. The reply lists and constants are checked operands at their positions for the
supported version.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.mog_code import BSS_HUNK, CODE_HUNK, DATA_HUNK, MogCode, Pointer
from harddaysengine.assets.turn_rules import text_list
from harddaysengine.sim.service_screen_types import (
    CelDraw,
    DonationDraw,
    DonationLayout,
    LabelDraw,
    NumberDraw,
    ScreenRegion,
    ServiceOpening,
    ServiceScreens,
)

if TYPE_CHECKING:
    from harddaysengine.assets.hunk import DecodedHunkExecutable
    from harddaysengine.sim.turn_rules import TextLine

_MYSTIC = 0x9B66
_HEALER = 0x9DD0
_DONATION = 0x9F4E
_REGIONS = 0x9F70
_REDRAW = 0xA100
_CELS = 0xA23A

_FONTS = Pointer(BSS_HUNK, 0x1BC)
_ACTIVE = Pointer(BSS_HUNK, 0x1D0)
_SHEET = Pointer(BSS_HUNK, 0x12C)
_POINTER_X = Pointer(DATA_HUNK, 0x9030)
_POINTER_Y = Pointer(DATA_HUNK, 0x9032)
_PORTRAIT = Pointer(DATA_HUNK, 0x7DA6)
_DONATED = Pointer(DATA_HUNK, 0x8F28)
_PURSE = Pointer(DATA_HUNK, 0x8F2A)
_FIELD = Pointer(DATA_HUNK, 0x8F20)
_SCRATCH = Pointer(11, 4)
_PICTURE_LOADER = Pointer(25, 0x186)
_CEL_LOADER = Pointer(26, 0x6FC)
_CEL_DRAWER = Pointer(28, 0x22C)
_SYNTH_START = Pointer(44, 0x698)
_LIST_PRINTER = Pointer(CODE_HUNK, 0x9196)
_STRING_PRINTER = Pointer(CODE_HUNK, 0x917C)
_FORMAT = Pointer(CODE_HUNK, 0x93CA)
_ADD_REGION = Pointer(CODE_HUNK, 0x9480)
_CLEAR_REGIONS = Pointer(CODE_HUNK, 0x94BA)
_WAIT_FIRE = Pointer(CODE_HUNK, 0x1FD0)
_PALETTE_FADE = Pointer(CODE_HUNK, 0x8B7C)
# Calls of the opening that only manage the display: fade out, pointer on, blit wait, flip.
_DISPLAY_CALLS = frozenset(
    {
        Pointer(CODE_HUNK, 0x8B26),
        Pointer(CODE_HUNK, 0xC586),
        Pointer(CODE_HUNK, 0x8FC2),
        Pointer(CODE_HUNK, 0x8F88),
        Pointer(30, 0x41A),
    }
)
_TARGET_CALL = Pointer(CODE_HUNK, 0x908E)

_OP_LEA_A0, _OP_LEA_A1, _OP_LEA_A2 = 0x41F9, 0x43F9, 0x45F9
_OP_MOVEA_A1, _OP_MOVEA_A0 = 0x2279, 0x2079
_OP_JSR, _OP_BSR, _OP_RTS = 0x4EB9, 0x6100, 0x4E75
_OP_MOVE_L_ABS_D0 = 0x2039
_OP_MOVE_W_IMM_ABS = 0x33FC
_OP_MOVE_W_D0, _OP_MOVE_W_D1, _OP_MOVE_W_D2 = 0x303C, 0x323C, 0x343C
_OP_MOVE_W_ABS_D0 = 0x3039
_OP_CLEAR_D2 = 0xB582  # EOR.L D2,D2
_OP_STORE_W, _OP_STORE_L = 0x317C, 0x217C  # MOVE.W / MOVE.L #imm,d16(A0)
_OP_COPY_FONT = (0x2169, 0x0000, 0x000A)  # MOVE.L 0(A1),10(A0): font table entry 0
_OP_MOVEQ_D0 = 0x7000
_BYTE = 0xFF

# Region scratch displacements (§12.31).
_REGION_FIELDS = {"x": 12, "y": 14, "width": 4, "height": 6, "code": 20}

# Reply operands: (position, opcode) of each checked LEA / MOVE.L / MOVEQ / branch.
_MYSTIC_NOTHING = 0x9C8C
_RAISE_TABLE = 0x9CC0
_LOWER_TABLE = 0x9D06
_TABLE_ROWS = 0x9D10
_TABLE_STRIDE = 0x9D1C
_NOTHING_GIVEN = 0x9D34
_REFUSED = 0x9D42
_UNCHANGED = 0x9D50
_CLOSE = 0x9D5C
_CLOSE_WAIT = 0x9D62
_HEALER_SMALL = 0x9F2A
_HEALER_PLAIN = 0x9F32
_HEALER_HEALED = 0x9F3E
_REPEAT_WAITS = (0xA0CC, 0xA0F4)
# (branch position, opcode, target) that tie the two screens to the shared reply tail.
_BRANCHES = (
    (0x9C64, 0x6700, _NOTHING_GIVEN),
    (0x9C6C, 0x6600, _REFUSED),
    (0x9EB6, 0x6600, _REFUSED),
    (0x9EC2, 0x6700, _NOTHING_GIVEN),
    (0x9F4A, 0x6000, _CLOSE),
)


@dataclass(slots=True)
class _Walk:
    """A cursor over hunk 0 with checked reads."""

    mog: MogCode
    pos: int
    what: str

    def peek(self, offset: int = 0) -> int:
        return self.mog.word(self.pos + offset)

    def words(self, *expected: int) -> None:
        for word in expected:
            if self.peek() != word:
                found = self.peek()
                msg = f"mog: {self.what}: expected ${word:04X} at {self.pos}, found ${found:04X}"
                raise MogVersionError(msg)
            self.pos += 2

    def immediate(self, opcode: int) -> int:
        self.words(opcode)
        return self.take()

    def take(self) -> int:
        value = self.peek()
        self.pos += 2
        return value

    def pointer(self) -> Pointer:
        target = self.mog.pointer(self.pos, self.what)
        self.pos += 4
        return target

    def expect_pointer(self, wanted: Pointer) -> None:
        if self.pointer() != wanted:
            raise MogVersionError(f"mog: {self.what}: an operand points elsewhere at {self.pos}")

    def call(self, wanted: Pointer) -> None:
        self.words(_OP_JSR)
        self.expect_pointer(wanted)

    def bsr(self) -> int:
        self.words(_OP_BSR)
        base = self.pos
        self.pos += 2
        return base + (self.mog.word(base) ^ 0x8000) - 0x8000

    def data_string(self) -> str:
        target = self.pointer()
        if target.hunk != DATA_HUNK:
            raise MogVersionError(f"mog: {self.what}: a string outside the data hunk")
        return self.mog.cstring(target, self.what)


@dataclass(slots=True)
class _Opening:
    picture: str = ""
    cels: str = ""
    pointer: dict[Pointer, int] = field(default_factory=dict)
    greeting: tuple[TextLine, ...] = ()
    sounds: list[tuple[int, int]] = field(default_factory=list)
    font: bool = False
    waited: bool = False
    faded: bool = False


def _lea_a0(walk: _Walk, out: _Opening) -> None:
    target = walk.pointer()
    if target == _ACTIVE:
        walk.words(_OP_LEA_A1)
        walk.expect_pointer(_FONTS)
        walk.words(*_OP_COPY_FONT)
        out.font = True
    elif walk.peek() == _OP_MOVEA_A1:
        walk.words(_OP_MOVEA_A1)
        walk.expect_pointer(_SHEET)
        walk.call(_PICTURE_LOADER)
        out.picture = walk.mog.cstring(target, walk.what)
    elif target.hunk == DATA_HUNK:
        walk.call(_LIST_PRINTER)
        out.greeting = text_list(walk.mog, target, walk.what)
    else:
        walk.call(_PALETTE_FADE)
        out.faded = True


def _cel_file(mog: MogCode, start: int) -> str:
    walk = _Walk(mog, start, "screen cels")
    walk.words(_OP_LEA_A0)
    name = walk.data_string()
    walk.words(_OP_MOVEA_A1)
    walk.expect_pointer(_SHEET)
    walk.call(_CEL_LOADER)
    walk.words(_OP_RTS)
    return name


def _plain_call(walk: _Walk, out: _Opening) -> None:
    target = walk.pointer()
    if target == _WAIT_FIRE:
        out.waited = True
    elif target not in _DISPLAY_CALLS:
        raise MogVersionError(f"mog: {walk.what}: an unexpected call before {walk.pos}")


def _opening_step(walk: _Walk, out: _Opening) -> None:
    opcode = walk.peek()
    if opcode == _OP_LEA_A0:
        walk.pos += 2
        _lea_a0(walk, out)
    elif opcode == _OP_MOVE_L_ABS_D0:
        walk.pos += 2
        walk.pointer()
        walk.call(_TARGET_CALL)
    elif opcode == _OP_BSR:
        target = walk.bsr()
        if target != _CELS:
            raise MogVersionError(f"mog: {walk.what}: an unexpected subroutine")
        out.cels = _cel_file(walk.mog, target)
    elif opcode == _OP_JSR:
        walk.pos += 2
        _plain_call(walk, out)
    elif opcode == _OP_MOVE_W_IMM_ABS:
        value = walk.immediate(_OP_MOVE_W_IMM_ABS)
        out.pointer[walk.pointer()] = value
    else:
        seq = walk.immediate(_OP_MOVE_W_D0)
        channel = walk.immediate(_OP_MOVE_W_D1)
        walk.call(_SYNTH_START)
        out.sounds.append((seq, channel))


def _donation_call(walk: _Walk) -> None:
    """The ``JSR`` or ``BSR`` to the donation screen after the portrait ``MOVEQ``."""
    if walk.peek() != _OP_BSR:
        walk.call(Pointer(CODE_HUNK, _DONATION))
    elif walk.bsr() != _DONATION:
        raise MogVersionError(f"mog: {walk.what}: the donation call goes elsewhere")


def _check_opening(out: _Opening, what: str) -> None:
    complete = out.font and out.waited and out.faded and out.sounds and out.greeting
    if not complete or out.cels == "" or out.picture == "":
        raise MogVersionError(f"mog: {what}: the opening is incomplete")
    if set(out.pointer) != {_POINTER_X, _POINTER_Y}:
        raise MogVersionError(f"mog: {what}: unexpected pointer stores")


def _opening(mog: MogCode, start: int, what: str) -> ServiceOpening:
    walk, out = _Walk(mog, start, what), _Opening()
    while walk.peek() & 0xFF00 != _OP_MOVEQ_D0:
        _opening_step(walk, out)
    portrait = walk.take() & _BYTE
    _donation_call(walk)
    _check_opening(out, what)
    return ServiceOpening(
        out.picture, out.pointer[_POINTER_X], out.pointer[_POINTER_Y], out.greeting,
        tuple(out.sounds), portrait,
    )  # fmt: skip


def _region(fields: dict[int, int]) -> ScreenRegion:
    if any(d not in fields for d in _REGION_FIELDS.values()):
        raise MogVersionError("mog: a donation region leaves a field unset")
    return ScreenRegion(**{name: fields[d] for name, d in _REGION_FIELDS.items()})


def _region_step(walk: _Walk, fields: dict[int, int], regions: list[ScreenRegion]) -> None:
    opcode = walk.peek()
    if opcode == _OP_LEA_A0:
        walk.pos += 2
        walk.expect_pointer(_SCRATCH)
    elif opcode == _OP_STORE_W:
        value = walk.immediate(_OP_STORE_W)
        fields[walk.take()] = value
    elif opcode == _OP_STORE_L:
        walk.pos += 6
        walk.take()
    else:
        walk.call(_ADD_REGION)
        regions.append(_region(fields))


def _regions(mog: MogCode) -> tuple[ScreenRegion, ...]:
    walk = _Walk(mog, _REGIONS, "donation regions")
    fields: dict[int, int] = {}
    regions: list[ScreenRegion] = []
    while walk.peek() != _OP_LEA_A0 or mog.pointer(walk.pos + 2, walk.what) != _ACTIVE:
        _region_step(walk, fields, regions)
    if not regions:
        raise MogVersionError("mog: the donation screen registers no region")
    return tuple(regions)


def _number_cell(walk: _Walk) -> bool:
    walk.words(_OP_LEA_A2)
    walk.expect_pointer(_FIELD)
    walk.words(_OP_MOVE_W_ABS_D0)
    cell = walk.pointer()
    walk.call(_FORMAT)
    if cell not in (_PURSE, _DONATED):
        raise MogVersionError("mog: the donation screen formats another cell")
    return cell == _PURSE


def _string_draw(walk: _Walk, purse: bool | None) -> DonationDraw:
    walk.words(_OP_LEA_A0)
    target = walk.pointer()
    x = walk.immediate(_OP_MOVE_W_D0)
    y = walk.immediate(_OP_MOVE_W_D1)
    if walk.peek() == _OP_CLEAR_D2:
        walk.pos += 2
        style = 0
    else:
        style = walk.immediate(_OP_MOVE_W_D2)
    walk.call(_STRING_PRINTER)
    if target == _FIELD:
        if purse is None:
            raise MogVersionError("mog: a number is printed before it is formatted")
        return NumberDraw(purse, x, y, style)
    if target.hunk != DATA_HUNK:
        raise MogVersionError("mog: a donation label outside the data hunk")
    return LabelDraw(walk.mog.cstring(target, walk.what), x, y, style)


def _cel_draw(walk: _Walk) -> CelDraw:
    walk.words(_OP_MOVEA_A0)
    walk.expect_pointer(_SHEET)
    frame: int | None
    if walk.peek() == _OP_MOVE_W_ABS_D0:
        walk.pos += 2
        walk.expect_pointer(_PORTRAIT)
        frame = None
    else:
        frame = walk.immediate(_OP_MOVE_W_D0)
    x = walk.immediate(_OP_MOVE_W_D1)
    y = walk.immediate(_OP_MOVE_W_D2)
    walk.call(_CEL_DRAWER)
    return CelDraw(frame, x, y)


def _redraw_skip(walk: _Walk) -> bool:
    """Skip the display-only instructions of the redraw; ``False`` at its ``RTS``."""
    opcode = walk.peek()
    if opcode == _OP_RTS:
        return False
    if opcode == _OP_BSR:
        walk.bsr()
    elif opcode == _OP_MOVE_W_IMM_ABS:
        walk.pos += 8
    elif opcode == _OP_MOVE_L_ABS_D0:
        walk.pos += 6
        walk.call(_TARGET_CALL)
    else:
        walk.call(Pointer(CODE_HUNK, 0x8F88))
    return True


def _redraw(mog: MogCode) -> tuple[DonationDraw, ...]:
    walk = _Walk(mog, _REDRAW, "donation redraw")
    draws: list[DonationDraw] = []
    purse: bool | None = None
    while True:
        opcode = walk.peek()
        if opcode == _OP_MOVEA_A0:
            draws.append(_cel_draw(walk))
        elif opcode == _OP_LEA_A2:
            purse = _number_cell(walk)
        elif opcode == _OP_LEA_A0:
            draws.append(_string_draw(walk, purse))
        elif not _redraw_skip(walk):
            break
    return tuple(draws)


def _check_donation_entry(mog: MogCode) -> None:
    walk = _Walk(mog, _DONATION, "donation screen")
    walk.words(_OP_MOVEA_A0)
    walk.pointer()
    walk.words(0x33E8, 0x004A)  # MOVE.W 74(A0),purse: the knight's gold
    walk.expect_pointer(_PURSE)
    walk.words(_OP_MOVE_W_IMM_ABS, 0)
    walk.expect_pointer(_DONATED)
    walk.words(0x33C0)  # MOVE.W D0,portrait
    walk.expect_pointer(_PORTRAIT)
    walk.call(_CLEAR_REGIONS)
    if walk.pos != _REGIONS:
        raise MogVersionError("mog: the donation entry has moved")


def _donation(mog: MogCode) -> DonationLayout:
    _check_donation_entry(mog)
    waits = {mog.word(pos) for pos in _REPEAT_WAITS}
    if len(waits) != 1 or next(iter(waits)) & 0xFF00 != _OP_MOVEQ_D0:
        raise MogVersionError("mog: the donation repeat waits disagree")
    return DonationLayout(
        _cel_file(mog, _CELS), _regions(mog), _redraw(mog), next(iter(waits)) & _BYTE
    )


def _list_at(mog: MogCode, pos: int, what: str) -> tuple[TextLine, ...]:
    """The text list of the ``LEA`` at ``pos`` (its printer call is checked by the caller)."""
    walk = _Walk(mog, pos, what)
    walk.words(_OP_LEA_A0)
    return text_list(mog, walk.pointer(), what)


def _check_printers(mog: MogCode) -> None:
    """Each reply ``LEA`` reaches the list printer; the healer's three share one call."""
    for pos in (_MYSTIC_NOTHING, _NOTHING_GIVEN, _REFUSED, _UNCHANGED):
        _Walk(mog, pos + 6, "service reply").call(_LIST_PRINTER)
    walk = _Walk(mog, _HEALER_SMALL + 6, "healer reply")
    shared = walk.pos + 2 + (walk.peek() & _BYTE)
    walk.words(0x6000 | (shared - walk.pos - 2))
    if walk.pos != _HEALER_PLAIN:
        raise MogVersionError("mog: the healer replies have moved")
    walk.pos += 6
    walk.words(0x0802, 0x0000, 0x6706)  # BTST #0,D2 (curse lifted) ; BEQ.S
    if walk.pos != _HEALER_HEALED or walk.pos + 6 != shared:
        raise MogVersionError("mog: the healer reply choice has moved")
    _Walk(mog, shared, "healer reply").call(_LIST_PRINTER)


def _table(mog: MogCode, pos: int, rows: int) -> tuple[tuple[int, tuple[TextLine, ...]], ...]:
    walk = _Walk(mog, pos, "mystic reply table")
    walk.words(0x23FC)
    table = walk.pointer()
    walk.pointer()
    out = []
    for row in range(rows):
        at = Pointer(table.hunk, table.offset + row * 8)
        stat = mog.hunk_word(at.hunk, at.offset + 2)
        if mog.hunk_word(at.hunk, at.offset) != 0:
            raise MogVersionError("mog: a mystic reply row names no stat")
        text = mog.pointer(at.offset + 4, "mystic reply", at.hunk)
        out.append((stat, text_list(mog, text, "mystic reply")))
    return tuple(out)


def _check_branches(mog: MogCode) -> None:
    for pos, opcode, target in _BRANCHES:
        walk = _Walk(mog, pos, "service reply")
        walk.words(opcode)
        found = walk.pos + (mog.word(walk.pos) ^ 0x8000) - 0x8000
        if found != target:
            raise MogVersionError(f"mog: the service reply branch at {pos} moved")


def _constants(mog: MogCode) -> tuple[int, int]:
    """The reply table row count and the close wait."""
    rows = mog.word(_TABLE_ROWS)
    if rows & 0xFF00 != 0x7000 or mog.word(_TABLE_STRIDE) != 0xD1FC:
        raise MogVersionError("mog: unexpected mystic table search")
    if mog.long(_TABLE_STRIDE + 2) != 8:
        raise MogVersionError("mog: unexpected mystic table stride")
    walk = _Walk(mog, _CLOSE, "service close")
    walk.call(Pointer(CODE_HUNK, 0x8F88))
    walk.words(0x203C)
    wait = mog.long(walk.pos)
    return (rows & _BYTE) + 1, wait


def service_screens(mog: MogCode) -> ServiceScreens:
    """Decode from an already version-checked executable (§12.31)."""
    _check_branches(mog)
    _check_printers(mog)
    rows, wait = _constants(mog)
    return ServiceScreens(
        donation=_donation(mog),
        mystic=_opening(mog, _MYSTIC, "mystic screen"),
        healer=_opening(mog, _HEALER, "healer screen"),
        refused=_list_at(mog, _REFUSED, "refusal"),
        nothing_given=_list_at(mog, _NOTHING_GIVEN, "nothing given"),
        close_wait=wait,
        healer_small=_list_at(mog, _HEALER_SMALL, "healer small reply"),
        healer_plain=_list_at(mog, _HEALER_PLAIN, "healer reply"),
        healer_healed=_list_at(mog, _HEALER_HEALED, "healer cured reply"),
        mystic_nothing=_list_at(mog, _MYSTIC_NOTHING, "mystic nothing reply"),
        mystic_unchanged=_list_at(mog, _UNCHANGED, "mystic unchanged reply"),
        mystic_raise=_table(mog, _RAISE_TABLE, rows),
        mystic_lower=_table(mog, _LOWER_TABLE, rows),
    )


def parse_service_screens(executable: DecodedHunkExecutable) -> ServiceScreens:
    """Both donation screens of the user's ``mog`` (§12.31)."""
    return service_screens(MogCode(executable))
