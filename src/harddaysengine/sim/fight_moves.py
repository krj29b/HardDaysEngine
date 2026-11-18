"""What stops a walking fighter: movement probe, arena clip, obstacle probe (§10.19).

Each function clears direction bits of a mover's ``input`` word (bit 0 right, 1 left, 2 down,
3 up) or returns the mask that clears them. All arithmetic is on 16-bit words.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harddaysengine.sim.fight_contact import depth_close, overlap
from harddaysengine.sim.m68k import s16, u16

if TYPE_CHECKING:
    from harddaysengine.sim.actor import Actor
    from harddaysengine.sim.fight import Fight
    from harddaysengine.sim.fighter_rules import ArenaClip, Probes

RIGHT = 0x01
LEFT = 0x02
DOWN = 0x04
UP = 0x08
FIRE = 0x10
ALL_DIRECTIONS = 0x1F
_FACING_MIRROR = 0x02


def _boxes_meet(a: Actor, b: Actor, dx: int) -> bool:
    """Both interval tests of the probe: x boxes (``a``'s shifted by ``dx``) and y boxes."""
    xa0, xa1 = u16(a.box_min_x + dx), u16(a.box_max_x + dx)
    return overlap(xa0, xa1, b.box_min_x, b.box_max_x) and overlap(
        a.box_min_y, a.box_max_y, b.box_min_y, b.box_max_y
    )


def _side_bit(mover: Actor, other: Actor, facing: int, dx: int) -> int:
    """The facing direction's bit when the mover would walk into ``other``, else 0."""
    if not depth_close(mover.y, other.y):
        return 0
    if facing == 1:
        bit, beyond = RIGHT, s16(mover.x) > s16(other.x)
    else:
        bit, beyond = LEFT, s16(mover.x) < s16(other.x)
    return bit if not beyond and _boxes_meet(mover, other, dx) else 0


def _vertical_bit(mover: Actor, other: Actor, probes: Probes) -> int:
    """Up or down when ``other`` stands just in front of or behind the mover, else 0."""
    diff = u16(mover.y - other.y)
    bit = UP
    if diff & 0x8000:
        bit, diff = DOWN, u16(-diff)
    if s16(diff) > probes.vertical_reach:
        return 0
    return bit if _boxes_meet(mover, other, 0) else 0


def movement_mask(fight: Fight, mover_handle: int, dx: int, probes: Probes) -> int:
    """The movement probe: ``$1F`` without the bits other fighters block (§10.19)."""
    mover = fight.actor(mover_handle)
    facing = mover.facing & 3
    mask = ALL_DIRECTIONS
    for job in fight.engine.jobs:
        if not job.active or job.owner == mover_handle:
            continue
        other = fight.actor(job.owner)
        if other.box_min_x == 0 or s16(other.hp) <= 0:
            continue
        mask &= ~_side_bit(mover, other, facing, dx)
        mask &= ~_vertical_bit(mover, other, probes)
    return mask


def arena_clip(actor: Actor, clip: ArenaClip) -> None:
    """Clear the directions that would leave the arena (§10.19)."""
    reach = -clip.reach if actor.facing & _FACING_MIRROR else clip.reach
    edge = s16(u16(actor.x + reach))
    foot = s16(u16(actor.y + clip.foot))
    if edge < clip.left:
        actor.input &= ~LEFT
    if edge > clip.right:
        actor.input &= ~RIGHT
    if foot > clip.bottom:
        actor.input &= ~DOWN
    if foot < clip.top:
        actor.input &= ~UP


def obstacle_probe(fight: Fight, actor: Actor, dx: int, dy: int, probes: Probes) -> None:
    """Clear the directions the arena's walls block for a step of ``(dx, dy)`` (§10.19)."""
    limit = s16(u16(actor.y + dy + probes.obstacle_reach))
    lo, hi = u16(actor.box_min_x + dx), u16(actor.box_max_x + dx)
    depth = actor.box_max_y
    for wall in fight.walls:
        if not overlap(wall.x1, wall.x2, lo, hi):
            continue
        if overlap(probes.wall_top, wall.y, depth, u16(depth + 1)):
            if actor.facing & _FACING_MIRROR:
                if not s16(hi) < s16(actor.box_min_x):
                    actor.input &= ~LEFT
            elif not s16(lo) > s16(actor.box_max_x):
                actor.input &= ~RIGHT
        if not s16(wall.y) < limit:
            actor.input &= ~UP
