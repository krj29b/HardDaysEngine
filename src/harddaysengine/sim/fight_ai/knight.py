"""Computer knight decisions and movement (ARCHITECTURE.md §10.31)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

from harddaysengine.sim.fight import KEEP, HandlerResult, restart_from
from harddaysengine.sim.fight_ai.approach import ApproachRules, abs_word, approach, walk
from harddaysengine.sim.fight_hurt import Blow, knight_duel_hurt
from harddaysengine.sim.m68k import s8, s16

if TYPE_CHECKING:
    from harddaysengine.sim.actor import Actor
    from harddaysengine.sim.fight import Fight
    from harddaysengine.sim.fighter_rules import KnightRules

COMPUTER_KNIGHT_TYPE = 0x10


@dataclass(slots=True, frozen=True)
class ComputerKnightRules:
    """Checked ranges, bounded step tables and day-indexed odds (§10.31)."""

    steps: ApproachRules
    odds: bytes
    strike8_range: int
    heavy_range: int
    strike4_range: int
    guard_range: int

    def __post_init__(self) -> None:
        if not self.odds:
            raise ValueError("computer knight odds table must be non-empty")


def _walk(
    fight: Fight, rules: ComputerKnightRules, knight: KnightRules, handle: int
) -> HandlerResult:
    actor = fight.actor(handle)
    if actor.input & 0xFF:
        actor.behaviour_flags &= 0x7F
    return walk(fight, rules.steps, handle, knight.probes, restart_from(actor, actor.idle_script))


def _guard(fight: Fight, rules: ComputerKnightRules, actor: Actor, target: Actor, odds: int) -> int:
    if actor.behaviour_flags & 0x80:
        return 0
    roll = fight.rng.percent()
    if s8(roll) < s8(odds) or actor.facing == target.facing:
        return 0
    action = {0x08: 0x10, 0x20: 0x1C, 0x04: 0x1C}.get(target.action, 0)
    if action and s16(abs_word(target.x - actor.x)) <= s16(rules.guard_range):
        return action
    return 0


def _attack(
    fight: Fight, rules: ComputerKnightRules, actor: Actor, distance: int, odds: int
) -> int:
    if s8(fight.rng.percent()) <= s8(odds):
        fight.rng.draw()
        return 0
    last = fight.ai_last_action
    if s16(distance) <= s16(rules.strike8_range) and last != 0x08:
        return 0x08
    if s16(distance) <= s16(rules.heavy_range) and last != 0x20:
        return 0x20
    if s16(distance) <= s16(rules.strike4_range) and (actor.daggers == 0 or last != 0x04):
        return 0x04
    return 0x0C if actor.daggers else 0


def _decide(
    fight: Fight, rules: ComputerKnightRules, actor: Actor, target: Actor, distance: int
) -> int:
    if s16(target.hp) <= 0:
        actor.set_cooldown_byte(0)
        if s16(distance) <= s16(rules.strike8_range) and not fight.gloated:
            return 0x08
        return 0
    day = max(0, min(s16(fight.ai_day), len(rules.odds) - 1))
    odds = rules.odds[day]
    action = _guard(fight, rules, actor, target, odds)
    return action or _attack(fight, rules, actor, distance, odds)


def _hit(fight: Fight, actor: Actor) -> HandlerResult:
    victim = fight.actor(actor.hit_target)
    if victim.action == 0x1C and (victim.type == 0x0C or actor.action in (0x20, 0x04)):
        return KEEP
    return restart_from(actor, actor.after_hit_script)


def computer_knight_handler(
    rules: ComputerKnightRules, knight: KnightRules, fight: Fight, handle: int
) -> HandlerResult:
    """Hurt, hit, then classic opponent selection, approach and decision (§10.31)."""
    actor = fight.actor(handle)
    actor.input = 0
    target = fight.actor(fight.second if handle == fight.first else fight.first)
    if actor.attacker:
        return knight_duel_hurt(Blow(fight, knight, handle, actor.attacker))
    if actor.hit_target:
        return _hit(fight, actor)
    fight.ai_last_action = actor.action
    moving, distance = approach(fight, actor, target)
    if moving and not fight.approach.near:
        return _walk(fight, rules, knight, handle)
    if moving:
        distance = abs_word(target.x - actor.x)
    action = _decide(fight, rules, actor, target, distance)
    if action:
        actor.action = action
        return restart_from(actor, fight.table_long(actor.action_scripts, action))
    if s16(target.hp) <= 0 and s16(distance) <= s16(rules.strike8_range):
        return restart_from(actor, actor.idle_script)
    return _walk(fight, rules, knight, handle)


def install_computer_knight(fight: Fight, rules: ComputerKnightRules, knight: KnightRules) -> None:
    """Register type $10 alongside human knight control (§10.31)."""
    fight.handlers[COMPUTER_KNIGHT_TYPE] = partial(computer_knight_handler, rules, knight)
