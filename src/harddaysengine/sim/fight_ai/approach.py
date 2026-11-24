"""Approach and step helpers shared by the brawler and the dragon (ARCHITECTURE.md §10.20).

**Approach** turns a record towards the first fighter and sets the input bits that walk it into
reach. The **step helpers** turn those bits into a step from the creature's step tables. The
**step** advances the walk phase, applies the movement probe of §10.19 and picks the walk script.
They pass values to each other through :class:`~harddaysengine.sim.fight.ApproachCells`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from harddaysengine.sim.fight import FightError, HandlerResult, restart_from
from harddaysengine.sim.fight_moves import DOWN, LEFT, RIGHT, UP, movement_mask
from harddaysengine.sim.m68k import s16, u8, u16

if TYPE_CHECKING:
    from harddaysengine.sim.actor import Actor
    from harddaysengine.sim.fight import Fight
    from harddaysengine.sim.fighter_rules import Probes

STEP_TABLE_WORDS = 8
_PHASE_MASK = 3
_PHASES = 4
_FACING_RIGHT = 1
_FACING_LEFT = 3
_BLOCK_SIDE = 0
_BLOCK_UP = 0x20
_BLOCK_DOWN = 0x40
_SIDE_BITS = RIGHT | LEFT
_VERTICAL_BITS = UP | DOWN
_LOW_BYTE = 0xFF


@dataclass(slots=True, frozen=True)
class ApproachRules:
    """The three step tables and the two depth overrides of the step helpers (§10.20).

    Each table holds 8 words: four ``{x, y}`` pairs for phases ``0..3``.
    """

    side: tuple[int, ...]
    up: tuple[int, ...]
    down: tuple[int, ...]
    up_override: int
    down_override: int

    def __post_init__(self) -> None:
        for table in (self.side, self.up, self.down):
            if len(table) != STEP_TABLE_WORDS:
                raise ValueError(f"a step table holds {STEP_TABLE_WORDS} words")


def abs_word(value: int) -> int:
    """The 68000 absolute value of a word: negate when bit 15 is set (``$8000`` stays)."""
    word = u16(value)
    return u16(-word) if word & 0x8000 else word


def face_target(actor: Actor, target: Actor) -> None:
    """Facing 1 when the target stands to the right (signed), else 3."""
    actor.facing = _FACING_RIGHT if s16(actor.x) < s16(target.x) else _FACING_LEFT


def approach(fight: Fight, actor: Actor, target: Actor) -> tuple[bool, int]:
    """Face the target and set the input bits that walk into reach (§10.20).

    Returns the flag and the distance word: flag clear means "in reach, depth right" with the
    distance ``|target.x - x|``.
    """
    cells = fight.approach
    cells.dx = cells.dy = cells.near = cells.dy_override = 0
    face_target(actor, target)
    cells.vertical = 0
    if s16(abs_word(target.y - actor.y)) <= s16(actor.depth_reach):
        cells.near = 1
    else:
        actor.input |= DOWN if s16(actor.y) <= s16(target.y) else UP
        cells.vertical = 1
    offset = u16(target.x - actor.x)
    distance = abs_word(offset)
    if s16(distance) <= s16(actor.too_close_x):
        actor.input |= RIGHT if offset & 0x8000 else LEFT
        return True, offset
    if s16(distance) <= s16(actor.reach_x):
        return cells.vertical != 0, distance
    actor.input |= RIGHT if s16(actor.x) < s16(target.x) else LEFT
    return True, actor.reach_x


def _pair(table: tuple[int, ...], phase: int) -> tuple[int, int]:
    idx = phase & _PHASE_MASK
    return table[idx * 2], table[idx * 2 + 1]


def _side_sign(actor: Actor) -> int:
    """+1 when the key of the facing direction is held (walking forwards), else -1."""
    forward = RIGHT if actor.facing == _FACING_RIGHT else LEFT
    return 1 if actor.input & forward else -1


def _side(fight: Fight, rules: ApproachRules, actor: Actor, *, mirror: bool) -> tuple[int, int]:
    cells = fight.approach
    dx, dy = _pair(rules.side, actor.anim_phase)
    cells.dx = u16(-dx) if mirror else dx
    cells.dy = cells.dy_override if cells.dy_override else dy
    return _BLOCK_SIDE, _side_sign(actor)


def _vertical(fight: Fight, rules: ApproachRules, actor: Actor, *, up: bool) -> tuple[int, int]:
    cells = fight.approach
    if up:
        cells.dx, dy = _pair(rules.up, actor.anim_phase)
        cells.dy, cells.dy_override = u16(-dy), rules.up_override
        return _BLOCK_UP, 1
    cells.dx, cells.dy = _pair(rules.down, actor.anim_phase)
    cells.dy_override = rules.down_override
    return _BLOCK_DOWN, 1


def step_helpers(fight: Fight, rules: ApproachRules, actor: Actor) -> tuple[int, int]:
    """Run the helper of every set direction bit (up, down, right, left); ``(block, step)``.

    Without a direction bit the original goes on with whatever its registers held; the
    implementation raises.
    """
    if not actor.input & (_SIDE_BITS | _VERTICAL_BITS):
        raise FightError(f"input ${actor.input:X} has no direction bit to walk by")
    chosen = (_BLOCK_SIDE, 1)
    if actor.input & UP:
        chosen = _vertical(fight, rules, actor, up=True)
    if actor.input & DOWN:
        chosen = _vertical(fight, rules, actor, up=False)
    if actor.input & RIGHT:
        chosen = _side(fight, rules, actor, mirror=False)
    if actor.input & LEFT:
        chosen = _side(fight, rules, actor, mirror=True)
    return chosen


def advance_phase(fight: Fight, actor: Actor, block: int, step: int) -> None:
    """Step the phase by ``step`` until the walk block has a script there (§10.20)."""
    for _ in range(_PHASES):
        actor.anim_phase = u8(actor.anim_phase + step) & _PHASE_MASK
        if fight.table_long(actor.walk_scripts, block + (actor.anim_phase << 2)) != 0:
            fight.approach.block = block
            return
    raise FightError(f"walk block {block} has no script: the original loops for ever here")


def take_step(fight: Fight, handle: int, block: int, step: int, probes: Probes) -> HandlerResult:
    """Advance the phase, mask by the movement probe, move and pick the walk script."""
    actor, cells = fight.actor(handle), fight.approach
    advance_phase(fight, actor, block, step)
    actor.input &= movement_mask(fight, handle, cells.dx, probes)
    if actor.input == 0:
        actor.anim_phase = 0
        return restart_from(actor, actor.idle_script)
    if not actor.input & _SIDE_BITS:
        cells.dx = 0
    if not actor.input & _VERTICAL_BITS:
        cells.dy = 0
    script = fight.table_long(actor.walk_scripts, cells.block + (actor.anim_phase << 2))
    actor.x, actor.y = u16(actor.x + cells.dx), u16(actor.y + cells.dy)
    return restart_from(actor, script)


def walk(
    fight: Fight, rules: ApproachRules, handle: int, probes: Probes, standing: HandlerResult
) -> HandlerResult:
    """Walk by the input bits; ``standing`` when the low byte of ``input`` is clear (§10.20)."""
    actor = fight.actor(handle)
    if actor.input & _LOW_BYTE == 0:
        return standing
    block, step = step_helpers(fight, rules, actor)
    return take_step(fight, handle, block, step, probes)
