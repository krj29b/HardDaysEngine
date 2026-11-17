"""Checked calendar, gift, text and flight operands from mog (§11.19)."""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

from pymoonstone.assets.fight_defs import parse_fight_defs
from pymoonstone.assets.fight_palette import MogVersionError
from pymoonstone.assets.map_rules import _bytes, _pointer
from pymoonstone.assets.mog_code import MogCode, Pointer
from pymoonstone.assets.shop_data import parse_stock_rules
from pymoonstone.sim.turn_rules import GiftRules, MapDragonRules, TextLine, TurnRules

if TYPE_CHECKING:
    from pymoonstone.assets.hunk import DecodedHunkExecutable


def _texts(mog: MogCode, pointer: Pointer) -> tuple[TextLine, ...]:
    """Read relocated linked text records, rejecting loops and truncated records (§11.19)."""
    rows: list[TextLine] = []
    seen: set[Pointer] = set()
    while True:
        if pointer in seen:
            raise MogVersionError("mog: cyclic calendar text list")
        seen.add(pointer)
        data = _bytes(mog, pointer, 14)
        text = mog.pointer(pointer.offset, "calendar text", pointer.hunk)
        x, y, style = struct.unpack_from(">HHH", data, 4)
        rows.append(TextLine(mog.cstring(text, "calendar text"), x, y, style))
        if (
            struct.unpack_from(">I", data, 10)[0] == 0
            and mog.reloc(pointer.offset + 10, pointer.hunk) is None
        ):
            return tuple(rows)
        pointer = mog.pointer(pointer.offset + 10, "next calendar text", pointer.hunk)


def _long_operand(mog: MogCode, pos: int, opcode: int) -> int:
    mog.expect(pos, (opcode,), "gift threshold")
    return mog.long(pos + 2)


def _gifts(mog: MogCode) -> GiftRules:
    bands_at = _pointer(mog, 0, 0x9AA0, 0x41F9, 4)
    bands = tuple(struct.iter_unpack(">II", _bytes(mog, bands_at, 80)))
    stat_at = _pointer(mog, 0, 0x99E4, 0x41F9, 4)
    stats = tuple(row[0] for row in struct.iter_unpack(">II", _bytes(mog, stat_at, 72)))
    transfer_at = _pointer(mog, 0, 0x52C, 0x49F9, 0)
    transfer = struct.unpack(">13H", _bytes(mog, transfer_at, 26))
    if transfer[-1] != 65535 or set(transfer[:-1]) != set(range(0, 24, 2)):
        raise MogVersionError("mog: invalid loot transfer table")
    if any(slot not in (70, 71, 72) for slot in stats):
        raise MogVersionError("mog: invalid gift stat table")
    if any(slot not in range(0, 24, 2) for _, slot in bands) or bands[-1][0] < 100:
        raise MogVersionError("mog: invalid gift item bands")
    return GiftRules(
        bands,
        stats,
        (
            _long_operand(mog, 0x98AC, 0xB0BC),
            _long_operand(mog, 0x98B6, 0xB0BC),
            _long_operand(mog, 0x98C0, 0xB0BC),
        ),
        transfer[:-1],
    )


def _dragon(mog: MogCode) -> MapDragonRules:
    identity = _pointer(mog, 36, 0x6B8, 0x217C, 36)
    if identity.offset != 0x7B8 or mog.hunk_word(36, identity.offset) != 0x23C8:
        raise MogVersionError("mog: unsupported map dragon handler")
    table = _pointer(mog, 36, 0x716, 0x217C, 4)
    scripts = tuple(mog.pointer(table.offset + i * 4, "map flight", 4) for i in range(16))
    if any(p.hunk != 4 for p in scripts):
        raise MogVersionError("mog: map flight points outside script hunk")
    hover = _pointer(mog, 36, 0x7DA, 0x23FC, 4)
    speed = _pointer(mog, 36, 0x7FE, 0x3A39, 36)
    stores = tuple(
        (offset, size, mog.operand(pos, opcode, "map dragon store", displacement=offset, hunk=36))
        for pos, opcode, offset, size in (
            (0x6EA, 0x317C, 4, 2),
            (0x6F0, 0x317C, 6, 2),
            (0x6F6, 0x317C, 8, 2),
            (0x6FC, 0x117C, 10, 1),
            (0x70A, 0x117C, 77, 1),
            (0x710, 0x117C, 12, 1),
        )
    )

    def operand(pos: int, opcode: int) -> int:
        return mog.operand(pos, opcode, "map dragon", hunk=36)

    return MapDragonRules(
        day=operand(0x692, 0x0C79),
        stores=stores,
        scripts=tuple(p.offset for p in scripts),
        hover=hover.offset,
        initial_job=struct.unpack(">HHH", _bytes(mog, Pointer(4, scripts[0].offset + 4), 6)),
        speed_x=operand(0x74A, 0x33FC),
        speed_y=struct.unpack(">h", _bytes(mog, speed, 2))[0],
        timer=operand(0x752, 0x33FC),
        hover_limit=operand(0x7CE, 0x0C79),
        right=operand(0x820, 0x0C68),
        right_reset=operand(0x828, 0x317C),
        left=operand(0x83A, 0x0C68),
        left_reset=operand(0x842, 0x317C),
        bottom=operand(0x854, 0x0C68),
        top_reset=operand(0x85C, 0x317C),
        progress=mog.operand(0x1240, 0x0668, "dragon progress", displacement=78),
    )


def parse_turn_rules(executable: DecodedHunkExecutable) -> TurnRules:
    """Read original tables via checked loads and relocations (§11.19)."""
    mog = MogCode(executable)
    moon = _pointer(mog, 0, 0x664, 0x43F9, 4)
    span = _pointer(mog, 0, 0x91F0, 0x3239, 4)
    margin = _pointer(mog, 0, 0x91F6, 0x9279, 4)
    dragon = next(
        a.creature for a in parse_fight_defs(executable).arenas if a.creature.actor_type == 20
    )
    if dragon.hp is None or dragon.hp_max is None:
        raise MogVersionError("mog: missing persistent dragon health")
    return TurnRules(
        moons=tuple(_bytes(mog, moon, 8)),
        next_day=_texts(mog, _pointer(mog, 0, 0x2BA8, 0x41F9, 4)),
        farewell=_texts(mog, _pointer(mog, 0, 0xD5C, 0x41F9, 4)),
        span=struct.unpack(">H", _bytes(mog, span, 2))[0]
        - struct.unpack(">H", _bytes(mog, margin, 2))[0],
        moon_x=mog.operand(0x2BD0, 0x323C, "moon x"),
        moon_y=mog.operand(0x2BD4, 0x343C, "moon y"),
        dragon=_dragon(mog),
        gifts=_gifts(mog),
        dragon_defaults=(
            (73, 1, mog.operand(0x4C64, 0x137C, "dragon lives", displacement=73)),
            (80, 2, dragon.hp),
            (84, 2, dragon.hp_max),
            (54, 4, 5),
            (77, 1, 20),
            (96, 4, 5),
        ),
        stock=parse_stock_rules(executable),
    )
