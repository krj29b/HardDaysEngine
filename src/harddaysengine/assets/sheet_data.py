"""Runtime sheet button tables and panel lists (ARCHITECTURE.md §12.25)."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING

from harddaysengine.assets.ai_map_rules import parse_ai_map_rules
from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.mog_code import MogCode, Pointer, StoreRun
from harddaysengine.assets.shop_data import parse_shop_rules
from harddaysengine.sim.sheet_types import SheetRules

if TYPE_CHECKING:
    from harddaysengine.assets.hunk import DecodedHunkExecutable


@dataclass(slots=True, frozen=True)
class PanelPiece:
    """One panel-list frame, position and orientation word (§12.25)."""

    frame: int
    x: int
    y: int
    orientation: int


@dataclass(slots=True, frozen=True)
class SheetData:
    """Seven complete button tables and the narrow/wide panel lists (§12.25)."""

    buttons: tuple[tuple[str | None, ...], ...]
    panels: tuple[tuple[PanelPiece, ...], ...]
    palette: tuple[int, ...]
    class_pairs: tuple[tuple[int, int], ...]


def _buttons(mog: MogCode) -> tuple[tuple[str | None, ...], ...]:
    stores = StoreRun(mog)
    stores.run(0xC6E0, 0xCBF4, "sheet button initialization", terminator=0x41F9)
    starts = sorted(stores.starts, key=lambda pointer: pointer.offset)
    if len(starts) != 7 or any(pointer.hunk != 1 for pointer in starts):
        raise MogVersionError("mog: expected seven sheet button tables in BSS")
    rows = []
    for pointer in starts:
        cells = stores.longs(pointer, 27)
        if len(cells) != 27:
            raise MogVersionError("mog: sheet button table overlaps the next table")
        texts: list[str | None] = []
        for cell in cells:
            if cell is None:
                texts.append(None)
            elif cell.size == 4 and cell.target == 4:
                texts.append(mog.cstring(Pointer(cell.target, cell.value), "sheet button text"))
            else:
                raise MogVersionError("mog: sheet button text is not a relocated data pointer")
        rows.append(tuple(texts))
    return tuple(rows)


def _panel(mog: MogCode, load: int) -> tuple[PanelPiece, ...]:
    pointer = mog.lea(load, 0x43F9, "sheet panel list")
    if pointer.hunk != 0:
        raise MogVersionError("mog: panel list is outside the code hunk")
    pieces: list[PanelPiece] = []
    position = pointer.offset
    while position + 2 <= len(mog.code):
        frame = mog.word(position)
        if frame & 32768:
            return tuple(pieces)
        if position + 8 > len(mog.code):
            break
        frame, x, y, orientation = struct.unpack_from(">HhhH", mog.code, position)
        pieces.append(PanelPiece(frame, x, y, orientation))
        position += 8
    raise MogVersionError("mog: unterminated sheet panel list")


def parse_sheet_data(executable: DecodedHunkExecutable) -> SheetData:
    """Execute bounded checked stores and decode relocated panel lists (§12.25)."""
    mog = MogCode(executable)
    palette = mog.lea(0xAE56, 0x41F9, "sheet palette")
    if palette.hunk != 4:
        raise MogVersionError("mog: sheet palette is outside the data hunk")
    colors: tuple[int, ...] = struct.unpack_from(">32H", mog.hunk_data(4), palette.offset)
    pairs = tuple(
        (
            mog.operand(first, 0x30FC, "sheet class color"),
            mog.operand(first + 4, 0x30FC, "sheet class shade"),
        )
        for first in (0xAE88, 0xAE9C, 0xAEC4, 0xAEB0, 0xAECE)
    )
    return SheetData(_buttons(mog), (_panel(mog, 0xAF32), _panel(mog, 0xAF4A)), colors, pairs)


def parse_sheet_rules(
    executable: DecodedHunkExecutable, data: SheetData, sizes: tuple[tuple[int, int], ...]
) -> SheetRules:
    """Read checked sheet transaction operands and shop rules (§12.26)."""
    mog = MogCode(executable)
    ai = parse_ai_map_rules(executable)
    mog.expect(0xCEA2, (0x7008,), "bad jingle wait")
    mog.expect(0xCEDC, (0x7008,), "good jingle wait")
    return SheetRules(
        data.buttons,
        sizes,
        tuple(tuple((p.frame, p.x, p.y, p.orientation) for p in panel) for panel in data.panels),
        ai.stat_price,
        mog.operand(0xC264, 0x0C68, "sheet gold cap", displacement=74),
        ai.rest_cap_test,
        ai.rest_cap,
        mog.operand(0xBEC8, 0xB07C, "haste threshold"),
        mog.operand(0xBF4C, 0xB07C, "hawk threshold"),
        mog.operand(0xBFB6, 0xB07C, "protection threshold"),
        parse_shop_rules(executable),
        jingle_wait=mog.word(0xCEA2) & 255,
    )
