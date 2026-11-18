"""Checked wizard speech tables from the user's mog (ARCHITECTURE.md §12.35)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.mog_code import MogCode, Pointer
from harddaysengine.sim.wizard_types import WizardDialogue, WizardSpeech

if TYPE_CHECKING:
    from harddaysengine.assets.hunk import DecodedHunkExecutable


def _data_pointer(mog: MogCode, pointer: Pointer, size: int = 1) -> bytes:
    data = mog.hunk_data(4)
    if pointer.hunk != 4 or not 0 <= pointer.offset <= len(data) - size:
        raise MogVersionError("mog: wizard pointer outside data hunk")
    return data


def _speech(mog: MogCode, pointer: Pointer, field: Pointer | None = None) -> WizardSpeech:
    data = _data_pointer(mog, pointer)
    count = data[pointer.offset]
    if not count:
        raise MogVersionError("mog: empty wizard speech count")
    lines: list[str] = []
    insertion = None
    pos = pointer.offset + 1
    for row in range(count):
        end = data.find(b"\0", pos)
        if end < 0:
            raise MogVersionError("mog: unterminated wizard speech")
        try:
            lines.append(data[pos:end].decode("ascii"))
        except UnicodeDecodeError as exc:
            raise MogVersionError("mog: non-ASCII wizard speech") from exc
        if field is not None and field.hunk == 4 and pos <= field.offset <= end:
            insertion = row, field.offset - pos
        pos = end + 1
    if field is not None and insertion is None:
        raise MogVersionError("mog: wizard field outside its speech")
    return WizardSpeech(tuple(lines), insertion)


def _pointer_table(mog: MogCode, pos: int, count: int) -> tuple[WizardSpeech, ...]:
    pointer = mog.lea(pos, 0x41F9, "wizard message table")
    _data_pointer(mog, pointer, count * 4)
    return tuple(
        _speech(mog, mog.pointer(pointer.offset + i * 4, "wizard speech", 4)) for i in range(count)
    )


def _cycles(mog: MogCode) -> tuple[int, int]:
    gold = mog.operand(0x98FE, 0x0C79, "wizard gold cycle limit")
    mask = mog.operand(0x9938, 0x0279, "wizard item cycle mask")
    if (gold, mask) != (3, 3):
        raise MogVersionError("mog: unsupported wizard message cycle extents")
    for inc, test, load in ((0x98F6, 0x98FE, 0x9910), (0x9930, 0x9938, 0x9940)):
        if mog.operand(inc, 0x0679, "wizard message cycle increment") != 1:
            raise MogVersionError("mog: unexpected wizard message cycle increment")
        cell = mog.pointer(inc + 4, "wizard cycle cell")
        mog.expect(load, (0x3039,), "wizard cycle load")
        if cell != mog.pointer(test + 4, "wizard cycle test") or cell != mog.pointer(
            load + 2, "wizard cycle load"
        ):
            raise MogVersionError("mog: wizard message cycle cells disagree")
    mog.expect(0x9906, (0x6D08,), "wizard gold cycle branch")
    if mog.operand(0x9908, 0x33FC, "wizard gold cycle reset") != 0:
        raise MogVersionError("mog: wizard gold cycle does not reset to zero")
    if mog.pointer(0x990C, "wizard gold reset") != mog.pointer(0x98FA, "wizard gold cycle"):
        raise MogVersionError("mog: wizard gold reset uses another cell")
    return gold, mask + 1


def _stats(mog: MogCode) -> tuple[tuple[int, WizardSpeech], ...]:
    pointer = mog.lea(0x99E4, 0x41F9, "wizard stat rows")
    mog.expect(0x99F6, (0xB0BC,), "wizard last stat row")
    count = mog.long(0x99F8) + 1
    if count != 9:
        raise MogVersionError("mog: unsupported wizard stat table extent")
    _data_pointer(mog, pointer, count * 8)
    rows = []
    for i in range(count):
        pos = pointer.offset + i * 8
        offset = int.from_bytes(mog.hunk_data(4)[pos : pos + 4], "big")
        if offset not in (70, 71, 72):
            raise MogVersionError("mog: wizard stat row is not a knight stat")
        rows.append((offset, _speech(mog, mog.pointer(pos + 4, "wizard stat speech", 4))))
    return tuple(rows)


def _message_cell(mog: MogCode) -> Pointer:
    mog.expect(0x98E2, (0x23FC,), "wizard curse message store")
    cell = mog.pointer(0x98E8, "wizard message cell")
    for pos, opcode in ((0x991E, 0x23F0), (0x994E, 0x23F0), (0x9968, 0x23F0)):
        mog.expect(pos, (opcode,), "wizard indexed message store")
        if mog.pointer(pos + 4, "wizard indexed message cell") != cell:
            raise MogVersionError("mog: wizard message stores disagree")
    return cell


def _farewells(mog: MogCode) -> tuple[tuple[int, WizardSpeech], ...]:
    item = mog.lea(0x9B52, 0x4BF9, "wizard item field")
    gold = mog.lea(0x9A4C, 0x45F9, "wizard gold field")
    if gold != mog.lea(0x9A58, 0x45F9, "wizard gold field append"):
        raise MogVersionError("mog: wizard gold field pointers disagree")
    rows = []
    kind_cell = None
    for pos in (0x97D6, 0x97E6, 0x97F6, 0x9806):
        key = mog.operand(pos, 0x0C79, "wizard farewell kind")
        cell = mog.pointer(pos + 4, "wizard gift kind cell")
        if kind_cell is not None and kind_cell != cell:
            raise MogVersionError("mog: wizard farewell kind cells disagree")
        kind_cell = cell
        mog.expect(pos + 8, (0x6606,), "wizard farewell branch")
        pointer = mog.lea(pos + 10, 0x207C, "wizard farewell speech")
        field = {1: item, 2: gold}.get(key)
        rows.append((key, _speech(mog, pointer, field)))
    if {key for key, _ in rows} != {1, 2, 3, 4}:
        raise MogVersionError("mog: wizard farewell kinds are incomplete")
    return tuple(rows)


def _read_dialogue(mog: MogCode) -> WizardDialogue:
    gold_count, item_count = _cycles(mog)
    _message_cell(mog)
    mog.expect(0xA2A2, (0x0679,), "wizard speech line spacing")
    return WizardDialogue(
        _speech(mog, mog.lea(0x9602, 0x41F9, "wizard greeting")),
        _pointer_table(mog, 0x9948, item_count),
        _pointer_table(mog, 0x9918, gold_count),
        _stats(mog),
        _speech(mog, mog.pointer(0x98E4, "wizard curse speech")),
        _speech(mog, mog.lea(0x97D0, 0x207C, "wizard omen farewell")),
        _farewells(mog),
        mog.word(0xA2A4),
        mog.operand(0x9876, 0x117C, "wizard visit recency", displacement=83),
    )


def parse_wizard_dialogue(executable: DecodedHunkExecutable) -> WizardDialogue:
    """Read supported mog's speeches without embedding or modifying game data (§12.35)."""
    return _read_dialogue(MogCode(executable))
