"""The brawler: the Trogg handler, types ``$18``, ``$1C`` and ``$20`` (ARCHITECTURE.md §10.20).

A Trogg walks up to the first fighter with the approach helpers
(:mod:`pymoonstone.sim.fight_ai.approach`) and, once within reach, waits out its cool-down or
attacks: the axe types strike or swing the heavy blow, the spear type thrusts. Every number and
fixed script comes from ``mog`` through :class:`BrawlerRules`.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

from pymoonstone.sim.fight import END, KEEP, HandlerResult, restart_from
from pymoonstone.sim.fight_ai.approach import ApproachRules, abs_word, approach, walk
from pymoonstone.sim.fight_hurt import contact_damage
from pymoonstone.sim.m68k import s16, u16, u32

if TYPE_CHECKING:
    from pymoonstone.sim.actor import Actor
    from pymoonstone.sim.fight import Fight
    from pymoonstone.sim.fighter_rules import KnightRules

AXE_TYPE = 0x18
AXE_TYPE_2 = 0x1C
SPEAR_TYPE = 0x20
BRAWLER_TYPES = (AXE_TYPE, AXE_TYPE_2, SPEAR_TYPE)
KNIGHT_TYPE = 0x0C
DAGGER_TYPE = 0x34
NO_SCRIPT = 0xFFFFFFFF
_COOLDOWN_SHIFT = 8
_LOW_BYTE = 0xFF


@dataclass(slots=True, frozen=True)
class BrawlerRules:
    """The brawler's numbers and fixed scripts, as read from ``mog`` (§10.20)."""

    approach: ApproachRules
    down_range: int
    close_range: int
    heavy_range: int
    gloat_odds: int
    strike_cooldown: int
    heavy_cooldown: int
    thrust_cooldown: int
    after_hit_cooldown: int
    strike_action: int
    heavy_action: int
    thrust_action: int
    defend_action: int
    dagger_damage: int
    thrust_script: int
    finish_script: int


@dataclass(slots=True)
class _Turn:
    """One handler call: the record, its rules and the next script built so far."""

    fight: Fight
    rules: BrawlerRules
    knight_rules: KnightRules
    handle: int
    next_script: int

    @property
    def me(self) -> Actor:
        return self.fight.actor(self.handle)

    @property
    def target(self) -> Actor:
        return self.fight.actor(self.fight.first)

    def result(self) -> HandlerResult:
        """The exit: ``$FFFFFFFF`` keeps the job, 0 ends it, anything else restarts it."""
        script = u32(self.next_script)
        if script == NO_SCRIPT:
            return KEEP
        if script == 0:
            return END
        return restart_from(self.me, script)


def cooldown(actor: Actor) -> int:
    """The cool-down byte: the high byte of the word at +106."""
    return actor.cooldown >> _COOLDOWN_SHIFT


def set_cooldown(actor: Actor, value: int) -> None:
    """Write the cool-down byte and leave the low byte of the word alone."""
    actor.cooldown = (value & _LOW_BYTE) << _COOLDOWN_SHIFT | actor.cooldown & _LOW_BYTE


def _walk(turn: _Turn) -> HandlerResult:
    rules = turn.rules
    probes = turn.knight_rules.probes
    return walk(turn.fight, rules.approach, turn.handle, probes, turn.result())


def _attack(turn: _Turn, wait: int, action: int, script: int) -> HandlerResult:
    me = turn.me
    set_cooldown(me, wait)
    me.action = action
    turn.next_script = script
    return turn.result()


def _strike(turn: _Turn) -> HandlerResult:
    rules, me = turn.rules, turn.me
    script = turn.fight.table_long(me.action_scripts, rules.strike_action)
    return _attack(turn, rules.strike_cooldown, rules.strike_action, script)


def _heavy(turn: _Turn, distance: int) -> HandlerResult:
    rules, me = turn.rules, turn.me
    if s16(distance) > s16(rules.heavy_range):
        return _walk(turn)
    script = turn.fight.table_long(me.action_scripts, rules.heavy_action)
    return _attack(turn, rules.heavy_cooldown, rules.heavy_action, script)


def _count_down(turn: _Turn) -> HandlerResult | None:
    """Spend one cool-down tick and stand, or ``None`` when the cool-down is over."""
    me = turn.me
    if cooldown(me) == 0:
        return None
    set_cooldown(me, cooldown(me) - 1)
    return turn.result()


def _target_down(turn: _Turn, distance: int) -> HandlerResult:
    """Decide over a knight without hit points: walk up, gloat once, then stand (§10.20)."""
    fight = turn.fight
    if fight.demo:
        return turn.result()
    if s16(distance) > s16(turn.rules.down_range):
        return _walk(turn)
    if fight.gloated:
        return turn.result()
    waiting = _count_down(turn)
    if waiting is not None:
        return waiting
    fight.gloated = True
    return _strike(turn)


def _axe(turn: _Turn, distance: int) -> HandlerResult:
    rules = turn.rules
    if s16(distance) > s16(rules.close_range):
        return _heavy(turn, distance)
    if s16(turn.fight.rng.percent()) > s16(rules.gloat_odds):
        return _strike(turn)
    if turn.target.action == rules.defend_action:
        return _heavy(turn, distance)
    return _strike(turn)


def _decide(turn: _Turn, distance: int) -> HandlerResult:
    """Choose by the distance to the target (§10.20, decide)."""
    me, rules = turn.me, turn.rules
    if s16(distance) <= s16(me.too_close_x):
        return _walk(turn)
    if s16(turn.target.hp) <= 0:
        return _target_down(turn, distance)
    waiting = _count_down(turn)
    if waiting is not None:
        return waiting
    if me.type != SPEAR_TYPE:
        return _axe(turn, distance)
    if s16(distance) > s16(me.reach_x):
        return _walk(turn)
    return _attack(turn, rules.thrust_cooldown, rules.thrust_action, rules.thrust_script)


def _think(turn: _Turn, cleared: Actor) -> HandlerResult:
    """Clear ``cleared``'s input and action, approach the target, then walk or decide."""
    cleared.input = cleared.action = 0
    me, target = turn.me, turn.target
    flag, distance = approach(turn.fight, me, target)
    if flag:
        if turn.fight.approach.near == 0:
            return _walk(turn)
        distance = abs_word(me.x - target.x)
    return _decide(turn, distance)


def _hurt(turn: _Turn) -> HandlerResult:
    """A blow taken: the attacker's type picks the amount, then the hurt script (§10.20)."""
    me = turn.me
    attacker = turn.fight.actor(me.attacker)
    set_cooldown(me, 0)
    if attacker.type in (KNIGHT_TYPE, SPEAR_TYPE):
        amount = contact_damage(turn.fight, turn.knight_rules, attacker)
    elif attacker.type == DAGGER_TYPE:
        amount = turn.rules.dagger_damage
    else:
        return turn.result()
    me.hp = u16(me.hp - amount)
    turn.next_script = turn.fight.table_long(me.hurt_scripts, attacker.action)
    return turn.result()


def _hit(turn: _Turn) -> HandlerResult:
    """A blow landed (§10.20, hit branch)."""
    me, fight = turn.me, turn.fight
    turn.next_script = me.after_hit_script
    set_cooldown(me, turn.rules.after_hit_cooldown)
    victim = fight.actor(me.hit_target)
    if victim.type != KNIGHT_TYPE:
        return _think(turn, victim)
    if victim.hit_target == 0 and s16(victim.hp) <= 0:
        turn.next_script = NO_SCRIPT
        if me.type == SPEAR_TYPE and not fight.demo:
            fight.kill_job(me.hit_target)
            turn.next_script = turn.rules.finish_script
            return turn.result()
    if me.type == SPEAR_TYPE:
        turn.next_script = me.idle_script
    return turn.result()


def brawler_handler(
    rules: BrawlerRules, knight_rules: KnightRules, fight: Fight, handle: int
) -> HandlerResult:
    """Handler of types ``$18``, ``$1C`` and ``$20`` (§10.20): hurt, hit, then think."""
    turn = _Turn(fight, rules, knight_rules, handle, fight.actor(handle).idle_script)
    if turn.me.attacker != 0:
        return _hurt(turn)
    if turn.me.hit_target != 0:
        return _hit(turn)
    return _think(turn, turn.me)


def install_brawler(fight: Fight, rules: BrawlerRules, knight_rules: KnightRules) -> None:
    """Register the brawler handler for the three Trogg types."""
    for kind in BRAWLER_TYPES:
        fight.handlers[kind] = partial(brawler_handler, rules, knight_rules)
