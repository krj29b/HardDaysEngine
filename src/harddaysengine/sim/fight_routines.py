"""The thrown dagger and the hop: three engine routines and a handler (ARCHITECTURE.md §10.19)."""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

from harddaysengine.sim.fight import END, FightError, HandlerResult, restart_from
from harddaysengine.sim.fight_moves import LEFT, RIGHT, arena_clip
from harddaysengine.sim.m68k import s16, u8, u16

if TYPE_CHECKING:
    from harddaysengine.sim.fight import Fight
    from harddaysengine.sim.fight_script import RoutineCall
    from harddaysengine.sim.fighter_rules import KnightRules

DAGGER_THROW = "dagger_throw"
HOP = "hop"
HOP_STEP = "hop_step"
DAGGER_TYPE = 0x34
_FACING_RIGHT = 1
_HOP_START = 0xFFFF


def dagger_throw(rules: KnightRules, damage_table: int, fight: Fight, call: RoutineCall) -> None:
    """One dagger fewer; a type-``$34`` record flies from the caller's position (§10.19)."""
    thrower = fight.actor(call.owner)
    thrower.daggers = u8(thrower.daggers - 1)
    position = (call.x, call.height, call.depth)
    handle = fight.spawn_actor(
        rules.dagger.script, call.frame_list, position, call.facing, DAGGER_TYPE
    )
    dagger = fight.actor(handle)
    dagger.damage_table = damage_table
    dagger.action = rules.dagger.action


def dagger_handler(rules: KnightRules, fight: Fight, handle: int) -> HandlerResult:
    """Handler of type ``$34``: fly on until a hit or the screen edge (§10.19)."""
    dagger = fight.actor(handle)
    if dagger.hit_target != 0:
        return END
    x = s16(dagger.x)
    flying = x < rules.dagger.right_edge if dagger.facing == _FACING_RIGHT else x > 0
    return restart_from(dagger, rules.dagger.flight_script) if flying else END


def hop(fight: Fight, _call: RoutineCall) -> None:
    """Start a hop: the counter becomes ``$FFFF`` (§10.19)."""
    fight.hop_counter = _HOP_START


def hop_step(rules: KnightRules, fight: Fight, call: RoutineCall) -> None:
    """One step of a hop: move the owner's job by the next word of the hop table (§10.19)."""
    fight.hop_counter = u16(fight.hop_counter + 1)
    owner = fight.actor(call.owner)
    owner.input = 0
    owner.facing = fight.hop_facing
    bit = RIGHT if fight.hop_facing == _FACING_RIGHT else LEFT
    owner.input |= bit
    arena_clip(owner, rules.clip)
    if not owner.input & bit:
        return
    job = fight.find_job(call.owner)
    if job is None:
        return
    step = _hop_word(fight, s16(fight.hop_counter))
    job.x = u16(job.x - step) if bit == LEFT else u16(job.x + step)


def _hop_word(fight: Fight, index: int) -> int:
    pos = fight.hop_table + 2 * index
    data = fight.image.data
    if not 0 <= pos <= len(data) - 2:
        raise FightError(f"hop table read at {pos}, outside the script hunk")
    value: int = struct.unpack_from(">H", data, pos)[0]
    return value
