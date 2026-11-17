"""What the fight engine needs from the disks: scripts, routines, cel shapes, frame lists.

Builds the inputs of :mod:`pymoonstone.sim.fight` (ARCHITECTURE.md §10.17, §10.18) from the
user's ``mog`` and cel files. Like :mod:`pymoonstone.assets.fight_defs` it holds only positions
for the supported ``mog`` version; every value is read from the file, and every routine entry
and operand is checked against its instruction word.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

import numpy as np

from pymoonstone.assets.fight_palette import MogVersionError
from pymoonstone.assets.mog_code import CODE_HUNK, DATA_HUNK, MogCode
from pymoonstone.sim.fight import (
    END_FIGHT,
    FIGHTER_DIED,
    MARK_FIRST_DEAD,
    PAUSE_ALL,
    SET_GLOAT,
    FightConstants,
    ScriptTables,
)
from pymoonstone.sim.fight_script import CelShape, FrameShape, HitShape, ScriptImage

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pymoonstone.assets.cel import CelFrame, DecodedCel
    from pymoonstone.assets.fight_defs import ActorInit, CelFile, CelSet
    from pymoonstone.assets.hit import HitSet

GUARDIAN_HUNK = 40

# (hunk, entry offset, first instruction word, name) of every routine a fight script calls (§10.18).
_ROUTINES: tuple[tuple[int, int, int, str], ...] = (
    (CODE_HUNK, 0x035A, 0x2079, FIGHTER_DIED),
    (CODE_HUNK, 0x0380, 0x45F9, END_FIGHT),
    (CODE_HUNK, 0x03BA, 0x2039, PAUSE_ALL),
    (CODE_HUNK, 0x03F4, 0x2F08, MARK_FIRST_DEAD),
    (CODE_HUNK, 0x50FC, 0x0429, "counter"),
    (CODE_HUNK, 0x58B8, 0x33FC, "hop"),
    (CODE_HUNK, 0x58C2, 0x0679, "hop_step"),
    (CODE_HUNK, 0x6FFC, 0x207C, "dagger_throw"),
    (CODE_HUNK, 0x71C8, 0x33FC, SET_GLOAT),
    (CODE_HUNK, 0x71D2, 0x4EB9, "sound_pick_1"),
    (CODE_HUNK, 0x721C, 0x4EB9, "sound_pick_2"),
    (CODE_HUNK, 0x7234, 0x4EB9, "sound_pick_8"),
    (CODE_HUNK, 0x724C, 0x4EB9, "sound_pick_9"),
    (CODE_HUNK, 0x7264, 0x4EB9, "sound_pick_3"),
    (CODE_HUNK, 0x727C, 0x4EB9, "sound_pick_4"),
    (CODE_HUNK, 0x7296, 0x0679, "sound_pick_5"),
    (CODE_HUNK, 0x72C0, 0x41F9, "sound_pick_6"),
    (CODE_HUNK, 0x72C8, 0x41F9, "sound_pick_7"),
    (CODE_HUNK, 0x90B8, 0x7002, "screen_shake"),
    (GUARDIAN_HUNK, 0x041C, 0x303C, "guardian_1"),
    (GUARDIAN_HUNK, 0x0428, 0x4EB9, "sound_pick_10"),
    (GUARDIAN_HUNK, 0x049E, 0x4EB9, "guardian_sound"),
    (GUARDIAN_HUNK, 0x04BC, 0x0679, "sound_pick_11"),
    (GUARDIAN_HUNK, 0x0682, 0x7E01, "sound_pick_12"),
    (GUARDIAN_HUNK, 0x0A0E, 0x2039, "guardian_2"),
)

# MOVE.B #imm,16(A2) in "end the fight" and MOVE.W #imm,80(A0) in "mark the first fighter dead".
_END_DELAY = (0x038C, 0x157C, 0x0010)
_DEAD_HP = (0x0400, 0x317C, 0x0050)

KNIGHT_LIST = 1
CREATURE_LIST = 2
MAP_LIST = 3
EFFECT_LIST = 4
_LIST_SLOTS = 5


def _word(data: bytes, pos: int) -> int:
    value: int = struct.unpack_from(">H", data, pos)[0]
    return value


def _check_routine_entries(mog: MogCode) -> None:
    for hunk, offset, opcode, name in _ROUTINES:
        found = _word(mog.hunk_data(hunk), offset)
        if found != opcode:
            msg = (
                f"mog: routine {name}: expected ${opcode:04X} at hunk {hunk} offset {offset}, "
                f"found ${found:04X}"
            )
            raise MogVersionError(msg)


def script_image(mog: MogCode) -> ScriptImage:
    """The script hunk and the name of every engine routine its longs point at (§10.18)."""
    _check_routine_entries(mog)
    known = {(hunk, offset): name for hunk, offset, _, name in _ROUTINES}
    data = mog.hunk_data(DATA_HUNK)
    routines: dict[int, str] = {}
    for pos, hunk in mog.relocations(DATA_HUNK).items():
        name = known.get((hunk, struct.unpack_from(">I", data, pos)[0]))
        if name is not None:
            routines[pos] = name
    return ScriptImage(data=data, routines=routines)


def _operand(mog: MogCode, spec: tuple[int, int, int], what: str) -> int:
    pos, opcode, displacement = spec
    mog.expect(pos, (opcode,), what)
    if mog.word(pos + 4) != displacement:
        raise MogVersionError(f"mog: {what}: unexpected displacement at code offset {pos + 4}")
    return mog.word(pos + 2)


def fight_constants(mog: MogCode) -> FightConstants:
    """The end delay and the dead marker of the fight-level routines (§10.12)."""
    return FightConstants(
        end_delay=_operand(mog, _END_DELAY, "end-of-fight delay") & 0xFF,
        dead_hp=_operand(mog, _DEAD_HP, "dead marker"),
    )


def script_tables(init: ActorInit) -> ScriptTables:
    """The scripts and tables an initialiser writes, ready for :meth:`Fight.register_tables`."""
    return ScriptTables(
        idle_script=init.idle_script,
        after_hit_script=init.after_hit_script,
        hurt_scripts=init.hurt_scripts,
        action_scripts=init.action_scripts,
        walk_scripts=init.walk_scripts,
        damage_table=init.damage_table,
        defence_table=init.defence_table,
    )


def _mask_rows(frame: CelFrame) -> tuple[int, ...]:
    width, height = frame.meta.width, frame.meta.height
    if width == 0 or height == 0:
        return ()
    aligned = ((width + 15) >> 4) << 4
    padded = np.zeros((height, aligned), dtype=np.bool_)
    padded[:, :width] = frame.mask
    packed = np.packbits(padded, axis=1)
    return tuple(int.from_bytes(row.tobytes(), "big") for row in packed)


def _hit_shapes(hit_set: HitSet) -> tuple[HitShape | None, ...]:
    return tuple(
        HitShape(frame.max_x, frame.max_y, tuple((p.x, p.y) for p in frame.points))
        if frame.points
        else None
        for frame in hit_set.frames
    )


def cel_shape(name: str, cel: DecodedCel, hit_set: HitSet | None) -> CelShape:
    """Frame sizes and masks of a cel, with the hit set when the loader registers one (§10.17)."""
    frames = tuple(FrameShape(f.meta.width, f.meta.height, _mask_rows(f)) for f in cel.frames)
    return CelShape(name, frames, None if hit_set is None else _hit_shapes(hit_set))


def _overlay(*slot_lists: tuple[CelFile | None, ...]) -> tuple[CelFile | None, ...]:
    merged: list[CelFile | None] = [None] * _LIST_SLOTS
    for slots in slot_lists:
        for index, cel_file in enumerate(slots):
            if cel_file is not None:
                merged[index] = cel_file
    return tuple(merged)


def _names(slots: tuple[CelFile | None, ...]) -> tuple[str | None, ...]:
    return tuple(None if cel_file is None else cel_file.name for cel_file in slots)


def frame_lists(knight: CelSet, arena: CelSet | None) -> dict[int, tuple[str | None, ...]]:
    """The four frame-set lists after the knight loader and an arena's loader ran (§10.15)."""
    creature: tuple[CelFile | None, ...] = (None,) * _LIST_SLOTS
    knight_slots = knight.knight_slots
    effect_slots = knight.effect_slots
    if arena is not None:
        creature = _overlay(arena.slots)
        knight_slots = _overlay(knight_slots, arena.knight_slots)
        effect_slots = _overlay(effect_slots, arena.effect_slots)
    return {
        KNIGHT_LIST: _names(knight_slots),
        CREATURE_LIST: _names(creature),
        MAP_LIST: (None,) * _LIST_SLOTS,
        EFFECT_LIST: _names(effect_slots),
    }


def cel_files(knight: CelSet, arena: CelSet | None) -> dict[str, CelFile]:
    """Every cel file in the frame lists of :func:`frame_lists`, by name."""
    sets = [knight] if arena is None else [knight, arena]
    found: dict[str, CelFile] = {}
    for cel_set in sets:
        for cel_file in (*cel_set.slots, *cel_set.knight_slots, *cel_set.effect_slots):
            if cel_file is not None:
                found[cel_file.name] = cel_file
    return found


def cel_shapes(
    files: Mapping[str, CelFile],
    cels: Mapping[str, DecodedCel],
    hit_sets: Mapping[str, HitSet],
) -> dict[str, CelShape]:
    """Shapes of the named cels; a cel's hit set is attached only when its loader registers it."""
    shapes: dict[str, CelShape] = {}
    for name, cel_file in files.items():
        stem = name.rsplit(".", 1)[0].casefold()
        hit_set = hit_sets.get(stem) if cel_file.has_hit_set else None
        if cel_file.has_hit_set and hit_set is None:
            raise MogVersionError(f"collide.hit has no hit set for {name}")
        shapes[name] = cel_shape(name, cels[name], hit_set)
    return shapes
