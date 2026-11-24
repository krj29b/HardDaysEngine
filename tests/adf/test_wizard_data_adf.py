"""Independent disk reads and reward recurrence for wizard dialogue (§12.35)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harddaysengine.assets.adf import DiskSet
from harddaysengine.assets.hunk import parse_hunk_executable
from harddaysengine.assets.turn_rules import parse_turn_rules
from harddaysengine.assets.wizard_data import parse_wizard_dialogue
from harddaysengine.sim.actor import Actor, StatKind
from harddaysengine.sim.inventory import Inventory
from harddaysengine.sim.rewards import GiftKind, GiftResult, GiftState, wizard_gift
from harddaysengine.sim.rng import Rng

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.adf


def test_dialogue_matches_independent_counted_string_and_pointer_walk(adf_dir: Path) -> None:
    executable = parse_hunk_executable("mog", DiskSet.from_directory(adf_dir).read_file("mog"))
    code, data = executable.sections[0].data, executable.sections[4].data
    dialogue = parse_wizard_dialogue(executable)

    def long(part: bytes, offset: int) -> int:
        return int.from_bytes(part[offset : offset + 4], "big")

    def speech(offset: int) -> tuple[str, ...]:
        count = data[offset]
        return tuple(
            line.decode("ascii") for line in data[offset + 1 :].split(b"\0", count)[:count]
        )

    assert dialogue.greeting.lines == speech(long(code, 0x9604))
    assert dialogue.omen.lines == speech(long(code, 0x97D2))
    assert dialogue.curse.lines == speech(long(code, 0x98E4))
    for position, rows in ((0x991A, dialogue.gold), (0x994A, dialogue.items)):
        table = long(code, position)
        assert tuple(row.lines for row in rows) == tuple(
            speech(long(data, table + i * 4)) for i in range(len(rows))
        )
    table = long(code, 0x99E6)
    assert tuple((offset, row.lines) for offset, row in dialogue.stats) == tuple(
        (long(data, table + i * 8), speech(long(data, table + i * 8 + 4))) for i in range(9)
    )
    for pos, (kind, row) in zip((0x97D6, 0x97E6, 0x97F6, 0x9806), dialogue.farewells, strict=True):
        assert kind == int.from_bytes(code[pos + 2 : pos + 4], "big")
        start = long(code, pos + 12)
        assert row.lines == speech(start)
        if row.field is not None:
            field = long(code, 0x9B54 if kind == 1 else 0x9A4E)
            line, column = row.field
            assert field == start + 1 + sum(len(s) + 1 for s in row.lines[:line]) + column
    assert dialogue.line_step == int.from_bytes(code[0xA2A4:0xA2A6], "big")
    assert dialogue.recency == int.from_bytes(code[0x9878:0x987A], "big")


def _draw(seed: int) -> int:
    for _ in range(8):
        rotated = ((seed >> 3) | (seed << 29)) & 0xFFFFFFFF
        feedback = (rotated ^ seed) >> 1 & 1
        seed = seed >> 1 | feedback << 31
    return seed


def _percent(seed: int) -> int:
    low = seed & 127
    return low if low < 100 else low - 27


def test_gift_result_and_final_seed_match_independent_disk_table_oracle(adf_dir: Path) -> None:
    executable = parse_hunk_executable("mog", DiskSet.from_directory(adf_dir).read_file("mog"))
    code, data = executable.sections[0].data, executable.sections[4].data
    rules = parse_turn_rules(executable).gifts
    dialogue = parse_wizard_dialogue(executable)
    limits = tuple(int.from_bytes(code[p : p + 4], "big") for p in (0x98AE, 0x98B8, 0x98C2))
    table = int.from_bytes(code[0x9AA2:0x9AA6], "big")
    bands = tuple(
        (int.from_bytes(data[p : p + 4], "big"), int.from_bytes(data[p + 4 : p + 8], "big"))
        for p in range(table, table + 80, 8)
    )
    kinds = set()
    for n in range(1, 257):
        seed = n * 0x1020305 & 0xFFFFFFFF
        actor = Actor(strength=2, constitution=2, endurance=2, hp=20, armour=27)
        inventory, state, rng = Inventory(), GiftState(item_cycle=2, gold_cycle=2), Rng(seed)
        result = wizard_gift(actor, inventory, rng, rules, state)
        cursor = _draw(seed)
        roll = _percent(cursor)
        if roll <= limits[0]:
            cursor = _draw(cursor)
            slot = next(slot for limit, slot in bands if _percent(cursor) <= limit)
            expected = GiftResult(GiftKind.ITEM, 3, item=slot)
            assert inventory.byte(slot) == 1 and state.last == slot
        elif roll <= limits[1]:
            cursor = _draw(cursor)
            row = cursor & 15
            row = row - 7 if row > 8 else row
            offset = dialogue.stats[row][0]
            expected = GiftResult(GiftKind.STAT, row, stat=offset)
            assert actor.get_stat(StatKind(offset)) == 3
        elif roll <= limits[2]:
            cursor = _draw(cursor)
            gold = cursor & 31
            amount = (gold - 10 if gold > 21 else gold) + 10
            expected = GiftResult(GiftKind.GOLD, 0, amount=amount)
            assert actor.gold == amount
        else:
            expected = GiftResult(GiftKind.CURSE)
            assert actor.frog_days == 3
        assert result == expected and rng.state == cursor
        assert dialogue.gift(result).lines
        kinds.add(result.kind)
    assert kinds == set(GiftKind)
