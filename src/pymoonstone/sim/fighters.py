"""The knight in a fight: joystick to action, walking, and the wiring of knight control.

Implements the knight handler of ARCHITECTURE.md §10.19 (type ``$0C``). The reactions to blows are
:mod:`pymoonstone.sim.fight_hurt`, the probes :mod:`pymoonstone.sim.fight_moves` and the dagger
and hop :mod:`pymoonstone.sim.fight_routines`. :func:`install_knight_control` registers all of
them with a :class:`~pymoonstone.sim.fight.Fight`.
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from pymoonstone.sim.fight import FightError, HandlerResult, restart_from
from pymoonstone.sim.fight_hurt import knight_hit, knight_hurt
from pymoonstone.sim.fight_moves import (
    ALL_DIRECTIONS,
    DOWN,
    FIRE,
    LEFT,
    RIGHT,
    UP,
    arena_clip,
    movement_mask,
    obstacle_probe,
)
from pymoonstone.sim.fight_routines import (
    DAGGER_THROW,
    DAGGER_TYPE,
    HOP,
    HOP_STEP,
    dagger_handler,
    dagger_throw,
    hop,
    hop_step,
)
from pymoonstone.sim.m68k import u16

if TYPE_CHECKING:
    from pymoonstone.sim.actor import Actor
    from pymoonstone.sim.fight import Fight
    from pymoonstone.sim.fighter_rules import KnightRules

KNIGHT_TYPE = 0x0C
PORT_0 = 1
_VERTICAL = UP | DOWN
_HORIZONTAL = LEFT | RIGHT
_FACING_RIGHT = 1
_FACING_LEFT = 3
_BLOCK_UP = 0x20
_BLOCK_DOWN = 0x40
_BLOCK_LATCH = 0x80


def read_port(fight: Fight, actor: Actor) -> int:
    """The joystick word of the actor's port: port 0 when ``input_port`` is 1, else port 1."""
    word = fight.ports[0] if actor.input_port == PORT_0 else fight.ports[1]
    if word & ~ALL_DIRECTIONS:
        raise FightError(f"joystick word ${word:X} has bits outside right/left/down/up/fire")
    return word


def _confuse(word: int) -> int:
    """Swap up with down and left with right, each pair only when one of it is pressed."""
    if word & _VERTICAL:
        word ^= _VERTICAL
    if word & _HORIZONTAL:
        word ^= _HORIZONTAL
    return word


def _fire(fight: Fight, rules: KnightRules, actor: Actor) -> HandlerResult:
    table = rules.actions_left if actor.facing == _FACING_LEFT else rules.actions_right
    actor.action = table[actor.input & ~FIRE]
    return restart_from(actor, fight.table_long(actor.action_scripts, actor.action))


def _steps(rules: KnightRules, actor: Actor) -> tuple[int, int]:
    """Set the new facing and return ``(dx, dy)`` for the actor's phase and input (§10.19)."""
    phase, word = actor.anim_phase, actor.input
    dy = 0
    if word & UP:
        dy = -rules.steps.up[phase]
    elif word & DOWN:
        dy = rules.steps.down[phase]
    dx = 0
    if word & RIGHT:
        actor.facing = _FACING_RIGHT
        dx = rules.steps.side[phase]
    elif word & LEFT:
        actor.facing = _FACING_LEFT
        dx = -rules.steps.side[phase]
    return dx, dy


def _apply_steps(actor: Actor, dx: int, dy: int) -> int | None:
    """Move by the bits left in ``input``; the walk block, or ``None`` when nothing moved."""
    block: int | None = None
    word = actor.input
    if word & UP:
        actor.y, block = u16(actor.y + dy), _BLOCK_UP
    if word & DOWN:
        actor.y, block = u16(actor.y + dy), _BLOCK_DOWN
    if word & LEFT:
        actor.x, block = u16(actor.x + dx), 0
    if word & RIGHT:
        actor.x, block = u16(actor.x + dx), 0
    return block


def _walk(fight: Fight, rules: KnightRules, handle: int) -> HandlerResult:
    actor = fight.actor(handle)
    saved_phase = actor.anim_phase
    actor.anim_phase = (actor.anim_phase + 1) & 3
    dx, dy = _steps(rules, actor)
    actor.input &= movement_mask(fight, handle, u16(dx), rules.probes)
    arena_clip(actor, rules.clip)
    obstacle_probe(fight, actor, dx, dy, rules.probes)
    block = _apply_steps(actor, dx, dy)
    if block is None:
        actor.anim_phase = saved_phase
        return restart_from(actor, actor.idle_script)
    actor.behaviour_flags &= ~_BLOCK_LATCH
    offset = block + (actor.anim_phase << 2)
    return restart_from(actor, fight.table_long(actor.walk_scripts, offset))


def knight_handler(rules: KnightRules, fight: Fight, handle: int) -> HandlerResult:
    """Handler of type ``$0C`` (§10.19): hurt, hit, then the joystick."""
    actor = fight.actor(handle)
    if actor.attacker != 0:
        return knight_hurt(fight, rules, handle)
    if actor.hit_target != 0:
        return knight_hit(fight, rules, handle)
    actor.action = 0
    actor.input = read_port(fight, actor)
    if actor.input == 0:
        return restart_from(actor, actor.idle_script)
    if handle == fight.confused and fight.confused_on:
        actor.input = _confuse(actor.input)
    if actor.input & FIRE:
        return _fire(fight, rules, actor)
    return _walk(fight, rules, handle)


def install_knight_control(fight: Fight, rules: KnightRules) -> int:
    """Register the knight and dagger handlers and the dagger and hop routines (§10.19).

    Stores the dagger damage table and returns its key.
    """
    dagger_table = fight.add_table(rules.dagger.damage)
    fight.handlers[KNIGHT_TYPE] = partial(knight_handler, rules)
    fight.handlers[DAGGER_TYPE] = partial(dagger_handler, rules)
    fight.routines[DAGGER_THROW] = partial(dagger_throw, rules, dagger_table)
    fight.routines[HOP] = hop
    fight.routines[HOP_STEP] = partial(hop_step, rules)
    return dagger_table
