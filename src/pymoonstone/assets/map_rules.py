"""Checked executable map tables and starting fields (ARCHITECTURE.md §11.18)."""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

from pymoonstone.assets.fight_defs import parse_fight_defs
from pymoonstone.assets.fight_palette import MogVersionError
from pymoonstone.assets.mog_code import MogCode, Pointer, StoreRun
from pymoonstone.sim.map_rules import LairStart, MapNode, MapRules

if TYPE_CHECKING:
    from pymoonstone.assets.hunk import DecodedHunkExecutable


def _pointer(mog: MogCode, hunk: int, pos: int, opcode: int, target: int) -> Pointer:
    if mog.hunk_word(hunk, pos) != opcode:
        raise MogVersionError(f"mog: unexpected map instruction at hunk{hunk} +{pos}")
    pointer = mog.pointer(pos + 2, "map", hunk)
    if pointer.hunk != target:
        raise MogVersionError("mog: map pointer names another hunk")
    return pointer


def _bytes(mog: MogCode, pointer: Pointer, count: int) -> bytes:
    data = mog.hunk_data(pointer.hunk)
    if not 0 <= pointer.offset <= len(data) - count:
        raise MogVersionError("mog: map table outside its data hunk")
    return data[pointer.offset : pointer.offset + count]


def _nodes(mog: MogCode) -> tuple[MapNode, ...]:
    pointer = _pointer(mog, 0, 0xE7E, 0x41F9, 2)
    data = mog.hunk_data(pointer.hunk)
    rows: list[MapNode] = []
    for pos in range(pointer.offset, len(data) - 5, 6):
        kind, x, y = struct.unpack_from(">hHH", data, pos)
        if kind < 0:
            return tuple(rows)
        rows.append(MapNode(kind, x, y))
    raise MogVersionError("mog: map node table has no negative terminator")


def _starts(mog: MogCode, human: bool) -> tuple[tuple[tuple[int, int], ...], tuple[str, ...]]:
    positions, names = [], []
    entries = (
        ((0x4FD4, 0x4FC6), (0x4FFC, 0x4FEE), (0x5022, 0x5014), (0x4FAC, 0x4F9E))
        if human
        else ((0x4BC8, 0x4BAC), (0x4BF6, 0x4BDA), (0x4C24, 0x4C08), (0x4C52, 0x4C36))
    )
    for pos, name in entries:
        opcode = 0x33BC if human else 0x337C
        positions.append(
            (mog.operand(pos, opcode, "map start x"), mog.operand(pos + 6, opcode, "map start y"))
        )
        names.append(
            mog.cstring(_pointer(mog, 0, name, 0x23BC if human else 0x237C, 4), "map knight name")
        )
    return tuple(positions), tuple(names)


def _defaults(mog: MogCode) -> tuple[tuple[int, int, int], ...]:
    run = StoreRun(mog)
    run.run_record(0x5092, 0x50E6, "map knight defaults", terminator=0x2069)
    return tuple((offset, cell.size, cell.value) for offset, cell in run.record_fields().items())


def _strings(mog: MogCode) -> tuple[str, str, str, tuple[str, ...]]:
    texts = tuple(
        mog.cstring(_pointer(mog, 36, pos, 0x43F9, 4), "map menu text")
        for pos in (0x12E8, 0x13AC, 0x13C0)
    )
    table = _pointer(mog, 36, 0x13DE, 0x43F9, 4)
    places = tuple(
        mog.cstring(mog.pointer(table.offset + i * 4, "map place text", 4), "map place name")
        for i in range(14)
    )
    return texts[0], texts[1], texts[2], places


def _palette_jobs(mog: MogCode) -> tuple[int, int]:
    """Validate the map job forms before reading their periods (§11.18)."""
    ramp = mog.hunk_word(36, 0x5F4)
    if ramp & 0xFF00 != 0x7400 or mog.hunk_word(36, 0x5F6) != 0x7600:
        raise MogVersionError("mog: unsupported map ramp job")
    if mog.hunk_word(36, 0x60C) != 0x7401 or mog.hunk_word(36, 0x60E) != 0x263C:
        raise MogVersionError("mog: unsupported map cycle job")
    period = struct.unpack(">I", _bytes(mog, Pointer(36, 0x610), 4))[0]
    return ramp & 255, period


def parse_map_rules(
    executable: DecodedHunkExecutable, sizes: tuple[tuple[int, int], ...]
) -> MapRules:
    """Decode supported map data through checked loads, pointers and stores (§11.18)."""
    mog = MogCode(executable)
    ramp_period, cycle_period = _palette_jobs(mog)
    human, human_names = _starts(mog, True)
    computers, computer_names = _starts(mog, False)
    may, lair, battle, places = _strings(mog)
    seeds = struct.unpack(">4I", _bytes(mog, _pointer(mog, 0, 0xA302, 0x41F9, 4), 16))
    keys = _bytes(mog, _pointer(mog, 34, 0x7AE, 0x41F9, 35), 0x68)
    return MapRules(
        nodes=_nodes(mog),
        lairs=tuple(
            LairStart(
                lair.handler_offset,
                lair.creatures_left,
                lair.map_x,
                lair.map_y,
                lair.param14,
                lair.arena_name,
            )
            for lair in parse_fight_defs(executable).lairs
        ),
        terrain=_bytes(mog, _pointer(mog, 36, 0x8BA, 0x41F9, 4), 1000),
        regions=_bytes(mog, _pointer(mog, 36, 0x13FE, 0x41F9, 4), 1000),
        sizes=sizes,
        seeds=seeds,
        defaults=_defaults(mog),
        human_positions=human,
        computer_positions=computers,
        human_names=human_names,
        computer_names=computer_names,
        may_text=may,
        lair_text=lair,
        battle_text=battle,
        place_texts=places,
        key_table=keys,
        char_map=_bytes(mog, _pointer(mog, 0, 0x9252, 0x43F9, 4), 104),
        ramp_index=mog.operand(0x5EC, 0x303C, "map ramp index", hunk=36),
        ramp_target=mog.operand(0x5F0, 0x323C, "map ramp target", hunk=36),
        ramp_period=ramp_period,
        cycle_start=mog.operand(0x604, 0x303C, "map cycle start", hunk=36),
        cycle_end=mog.operand(0x608, 0x323C, "map cycle end", hunk=36),
        cycle_period=cycle_period,
    )
