"""Checked runtime town button geometry and identity (ARCHITECTURE.md §12.27)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pymoonstone.assets.fight_palette import MogVersionError
from pymoonstone.assets.mog_code import MogCode, Pointer
from pymoonstone.sim.town_types import TownButton, TownData

if TYPE_CHECKING:
    from pymoonstone.assets.hunk import DecodedHunkExecutable


def _call(mog: MogCode, pos: int, target: int) -> None:
    mog.expect(pos, (0x4EB9,), "town JSR")
    if mog.call_target(pos, "town call") != Pointer(0, target):
        raise MogVersionError("mog: unexpected town call target")


def _long_store(mog: MogCode, pos: int, displacement: int) -> int:
    mog.expect(pos, (0x217C,), "town long store")
    if mog.word(pos + 6) != displacement or mog.reloc(pos + 2) is not None:
        raise MogVersionError("mog: unexpected town long store destination or relocation")
    return mog.long(pos + 2)


def _valid_button(button: TownButton, expected_id: int) -> None:
    if button.id != expected_id:
        raise MogVersionError("mog: town button ids are not in registration order")
    if not (button.width > 0 and button.height > 0):
        raise MogVersionError("mog: town button has nonpositive extent")
    if not (0 <= button.x <= 320 - button.width and 0 <= button.y <= 200 - button.height):
        raise MogVersionError("mog: town button is outside the playfield")


def _buttons(mog: MogCode, start: int) -> tuple[TownButton, ...]:
    """Reconstruct five snapshots, retaining fields not rewritten (§12.27)."""
    _call(mog, start, 0x94BA)
    record = mog.lea(start + 6, 0x41F9, "town scratch record")
    if record.hunk != 11 or len(mog.executable.sections) <= 11:
        raise MogVersionError("mog: town scratch record is outside its BSS allocation")
    if not 0 <= record.offset <= mog.executable.sections[11].alloc_size - 24:
        raise MogVersionError("mog: town scratch record is outside its BSS allocation")
    first = start + 12
    mog.expect(start + 0xC0, (0x4E75,), "town button return")

    def word(offset: int, displacement: int) -> int:
        return mog.operand(first + offset, 0x317C, "town word store", displacement=displacement)

    x, width, height = word(0, 12), word(12, 4), word(18, 6)
    kind, field = word(0x28, 20), word(0x2E, 22)
    if _long_store(mog, first + 24, 8) != 0:
        raise MogVersionError("mog: town scratch value is not cleared")
    rows = []
    # Offsets locate writes/calls; original values are always read from mog.
    for index, (load, y_at, id_at, call) in enumerate(
        (
            (6, 6, 0x20, 0x40),
            (0x46, 0x40, 0x46, 0x5A),
            (0x60, 0x5A, 0x60, 0x74),
            (0x7A, 0x74, 0x86, 0x9A),
            (0xA0, 0x9A, 0xA6, 0xBA),
        ),
        start=1,
    ):
        if mog.lea(start + load, 0x41F9, "town scratch reload") != record:
            raise MogVersionError("mog: town button reload names another scratch record")
        _call(mog, start + call, 0x9480)
        if index == 4:
            width, height = word(0x7A, 4), word(0x80, 6)
        elif index == 5:
            height = word(0xA0, 6)
        button = TownButton(
            _long_store(mog, first + id_at, 16), x, word(y_at, 14), width, height, kind, field
        )
        _valid_button(button, index)
        rows.append(button)
    return tuple(rows)


def _string(mog: MogCode, pointer: Pointer) -> str:
    if pointer.hunk != 4:
        raise MogVersionError("mog: town string is outside the data hunk")
    data = mog.hunk_data(4)
    if not 0 <= pointer.offset < len(data):
        raise MogVersionError("mog: town string is outside the data allocation")
    try:
        text = mog.cstring(pointer, "town string")
    except UnicodeDecodeError as exc:
        raise MogVersionError("mog: town string is not ASCII") from exc
    if not text:
        raise MogVersionError("mog: empty town string")
    return text


def _cursor(mog: MogCode, start: int) -> tuple[int, int, Pointer]:
    x = mog.operand(start, 0x33FC, "town pointer x")
    y = mog.operand(start + 8, 0x33FC, "town pointer y")
    target = mog.pointer(start + 4, "town pointer x")
    if target.hunk != 4 or mog.pointer(start + 12, "town pointer y") != Pointer(
        4, target.offset + 2
    ):
        raise MogVersionError("mog: town pointer stores do not name adjacent data cells")
    if not (0 <= x <= 314 and 0 <= y <= 195):
        raise MogVersionError("mog: town pointer starts outside its bounds")
    return x, y, target


def _space_sheet(mog: MogCode, highwood: bool) -> int:
    start = 0x14AE if highwood else 0x1366
    mog.expect(start + 4, (0x6600,), "town Space branch")
    key = mog.operand(start, 0xB07C, "town Space comparison")
    if key != 32:
        raise MogVersionError("mog: town status key is not Space")
    _call(mog, start + 8, 0x8B26)
    _call(mog, start + 14, 0xC5F4)
    if highwood:
        mog.expect(start + 20, (0x203C,), "town status sheet number")
        key = mog.long(start + 22)
    _call(mog, start + (26 if highwood else 20), 0xABEC)
    return key


def _town(mog: MogCode, *, highwood: bool) -> tuple[TownData, Pointer, Pointer]:
    loader, cursor, buttons = (0x2D96, 0x13F4, 0x153C) if highwood else (0x2DD2, 0x12AE, 0x15FE)
    mog.expect(loader, (0x23FC,), "town name store")
    name = _string(mog, mog.pointer(loader + 2, "town name"))
    destination = mog.pointer(loader + 6, "town name cell")
    if destination.hunk != 4 or not 0 <= destination.offset <= len(mog.hunk_data(4)) - 4:
        raise MogVersionError("mog: town name cell is outside the data allocation")
    picture = _string(mog, mog.lea(loader + 44, 0x41F9, "town picture"))
    x, y, pointer = _cursor(mog, cursor)
    data = TownData(
        name,
        picture,
        _string(mog, Pointer(4, pointer.offset + 10)),
        x,
        y,
        _space_sheet(mog, highwood),
        _buttons(mog, buttons),
    )
    return data, pointer, destination


def parse_town_data(executable: DecodedHunkExecutable) -> tuple[TownData, TownData]:
    """Decode Highwood then Waterdeep from checked mog operands (§12.27)."""
    mog = MogCode(executable)
    highwood, cursor_a, name_a = _town(mog, highwood=True)
    waterdeep, cursor_b, name_b = _town(mog, highwood=False)
    if (cursor_a, name_a) != (cursor_b, name_b):
        raise MogVersionError("mog: town loaders do not share pointer and name cells")
    return highwood, waterdeep
