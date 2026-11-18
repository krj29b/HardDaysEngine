"""Fight definitions read from the real ``mog``, checked by walking every script (T11.7).

The walker here is written from ARCHITECTURE.md §7.2 independently of the decoder: it follows
every branch a fight script can take and checks that each draw instruction names a cel slot the
fight loaded and a frame inside that cel (no modulo, no slot fallback).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from harddaysengine.app.scene_assets import GameAssetBundle
from harddaysengine.assets.adf import DiskSet
from harddaysengine.assets.fight_defs import (
    NOT_A_SCRIPT,
    ActorInit,
    ArenaDef,
    CelFile,
    FightDefs,
    parse_fight_defs,
)
from harddaysengine.assets.hunk import parse_hunk_executable
from harddaysengine.assets.mog_code import DATA_HUNK, MogCode

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

pytestmark = pytest.mark.adf

KNIGHT_SET = 1
CREATURE_SET = 2
EFFECT_SET = 4

# Instruction lengths of the control opcodes (§7.2), and which of them carry a script address.
_LENGTHS = {
    0x80: 2, 0x84: 6, 0x88: 2, 0x8C: 8, 0x94: 2, 0x98: 6, 0xA0: 8, 0xA4: 2, 0xA8: 8,
    0xAC: 6, 0xB0: 6, 0xB4: 6, 0xB8: 6, 0xBC: 2, 0xC0: 2, 0xC4: 6, 0xC8: 8, 0xCC: 8, 0xD0: 2,
}  # fmt: skip
_TARGET_AT = {0x84: 2, 0x98: 2, 0xAC: 2, 0xB4: 2, 0xB8: 2, 0xC4: 2, 0xC8: 4, 0xCC: 4}
_PATH_ENDS = {0xBC, 0xFD, 0xFE}
_JUMP_NOW = 3
_SECOND_SCRIPT = 0xAC  # with a zero "armed" byte it disarms and its address is unused


@dataclass(slots=True, frozen=True)
class Draw:
    script: int
    pc: int
    frame_set: int
    slot: int
    frame: int


class Walker:
    """Collects every draw reachable from a script, per active frame set (§7.2)."""

    def __init__(self, mog: MogCode) -> None:
        self.mog = mog
        self.data = mog.hunk_data(DATA_HUNK)

    def draws(self, script: int, frame_set: int) -> Iterator[Draw]:
        seen: set[tuple[int, int]] = set()
        pending = [(script, frame_set)]
        while pending:
            pc, current = pending.pop()
            while (pc, current) not in seen:
                seen.add((pc, current))
                op = self.data[pc]
                if op < 0x80:
                    slot, frame = (op & 0x1F) >> 2, self.data[pc + 1]
                    yield Draw(script, pc, current, slot, frame)
                    pc += 6
                    continue
                if op in _PATH_ENDS or (op == 0xFF and self.data[pc + 1] == 0xFF):
                    break
                if op == 0xFF:
                    pc += 2
                    continue
                if op == 0xC0:
                    current = self.data[pc + 1]
                disarm = op == _SECOND_SCRIPT and self.data[pc + 1] == 0
                if op in _TARGET_AT and not disarm:
                    pending.append((self._target(pc + _TARGET_AT[op]), current))
                    if op == 0x84 and self.data[pc + 1] == _JUMP_NOW:
                        break
                pc += _LENGTHS[op]

    def _target(self, pos: int) -> int:
        assert self.mog.reloc(pos, DATA_HUNK) == DATA_HUNK, f"unrelocated target at {pos}"
        value: int = struct.unpack_from(">I", self.data, pos)[0]
        return value


@pytest.fixture(scope="module")
def bundle(adf_dir: Path) -> GameAssetBundle:
    return GameAssetBundle(DiskSet.from_directory(adf_dir))


@pytest.fixture(scope="module")
def mog(bundle: GameAssetBundle) -> MogCode:
    return MogCode(parse_hunk_executable("mog", bundle.disk_set.read_file("mog")))


@pytest.fixture(scope="module")
def defs(mog: MogCode) -> FightDefs:
    return parse_fight_defs(mog.executable)


def _frame_counts(bundle: GameAssetBundle, files: tuple[CelFile | None, ...]) -> list[int | None]:
    counts: list[int | None] = []
    for cel_file in files:
        if cel_file is None:
            counts.append(None)
            continue
        cel = bundle.get_cel(cel_file.name)
        assert cel is not None, f"{cel_file.name} not on the disks"
        counts.append(len(cel.frames))
    return counts


def _merge(*lists: tuple[CelFile | None, ...]) -> tuple[CelFile | None, ...]:
    """Overlay loader slot lists: a later loader replaces the slots it fills."""
    merged: list[CelFile | None] = [None] * 5
    for slots in lists:
        for index, cel_file in enumerate(slots):
            if cel_file is not None:
                merged[index] = cel_file
    return tuple(merged)


def _scripts(actor: ActorInit) -> set[int]:
    tables = (actor.hurt_scripts, actor.action_scripts, actor.walk_scripts)
    found = {actor.idle_script, actor.after_hit_script}
    found.update(entry for table in tables for entry in table)
    return found - {0, NOT_A_SCRIPT}


def _check(
    walker: Walker,
    scripts: set[int],
    start_set: int,
    sets: dict[int, list[int | None]],
) -> list[str]:
    problems: list[str] = []
    drawn = 0
    for script in sorted(scripts):
        for draw in walker.draws(script, start_set):
            drawn += 1
            counts = sets.get(draw.frame_set)
            count = None if counts is None or draw.slot >= len(counts) else counts[draw.slot]
            if count is None or draw.frame >= count:
                problems.append(f"{draw} (slot frame count {count})")
    if scripts and drawn == 0:
        problems.append(f"scripts {sorted(scripts)} draw nothing")
    return problems


def _arena_sets(
    bundle: GameAssetBundle, defs: FightDefs, arena: ArenaDef
) -> dict[int, list[int | None]]:
    return {
        KNIGHT_SET: _frame_counts(
            bundle, _merge(defs.knight_cels.knight_slots, arena.cels.knight_slots)
        ),
        CREATURE_SET: _frame_counts(bundle, arena.cels.slots),
        EFFECT_SET: _frame_counts(bundle, defs.knight_cels.effect_slots),
    }


def test_every_arena_is_reachable(defs: FightDefs) -> None:
    handlers = {lair.handler_offset for lair in defs.lairs}
    assert handlers <= set(defs.arena_by_handler)
    assert len(defs.lairs) == 24


def test_values_the_spec_pins(defs: FightDefs) -> None:
    """Values ARCHITECTURE.md §10.3 / §10.4 quotes, read back from the file."""
    by_name = {arena.name: arena for arena in defs.arenas}
    axe = by_name["trogg_axe"].creature
    assert (axe.reach_x, axe.too_close_x, axe.depth_reach, axe.hp) == (100, 90, 5, 20)
    axe2 = by_name["trogg_axe_2"].creature
    assert (axe2.reach_x, axe2.too_close_x) == (70, 65)
    ratmen = by_name["ratmen"]
    assert ratmen.creature.hp == 5
    assert [(v.moon_frame, v.hp) for v in ratmen.moon_variants] == [(45, 7), (49, 12)]
    assert by_name["mudmen"].region_override == 8
    assert by_name["troll"].alive_cap == 2
    assert {arena.name: arena.scene for arena in defs.arenas}["dragon"] == 0x14


def test_knight_scripts_draw_loaded_frames(
    bundle: GameAssetBundle, mog: MogCode, defs: FightDefs
) -> None:
    sets = {
        KNIGHT_SET: _frame_counts(bundle, defs.knight_cels.knight_slots),
        EFFECT_SET: _frame_counts(bundle, defs.knight_cels.effect_slots),
    }
    assert defs.knight.frame_set == KNIGHT_SET
    assert _check(Walker(mog), _scripts(defs.knight), KNIGHT_SET, sets) == []


@pytest.mark.parametrize(
    "name",
    [
        "flyer",
        "mudmen",
        "trogg_axe",
        "trogg_axe_2",
        "trogg_spear",
        "ratmen",
        "balok",
        "troll",
        "guardian",
        "dragon",
    ],
)
def test_creature_scripts_draw_loaded_frames(
    bundle: GameAssetBundle, mog: MogCode, defs: FightDefs, name: str
) -> None:
    arena = next(arena for arena in defs.arenas if arena.name == name)
    sets = _arena_sets(bundle, defs, arena)
    walker = Walker(mog)
    problems: list[str] = []
    for actor in (arena.creature, *arena.objects):
        assert actor.frame_set is not None
        problems += _check(walker, _scripts(actor), actor.frame_set, sets)
    pokes = {poke.script for poke in arena.knight_pokes}
    problems += _check(walker, pokes, KNIGHT_SET, sets)
    assert problems == []
