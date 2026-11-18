"""The Troll's stalker handler (ARCHITECTURE.md §10.27)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

from harddaysengine.sim.fight import HandlerResult, restart_from
from harddaysengine.sim.fight_ai.approach import abs_word, approach, take_step
from harddaysengine.sim.fight_ai.effects import HitSparkRules, hit_spark
from harddaysengine.sim.fight_hurt import contact_damage
from harddaysengine.sim.fight_moves import DOWN, LEFT, RIGHT, UP
from harddaysengine.sim.m68k import s16, u16

if TYPE_CHECKING:
    from harddaysengine.sim.actor import Actor
    from harddaysengine.sim.fight import Fight
    from harddaysengine.sim.fighter_rules import KnightRules

STALKER_TYPE = 0x40
_STRIKE = 8
_HEAVY = 0x20


@dataclass(slots=True, frozen=True)
class StalkerRules:
    """Decoded Troll attack thresholds, steps and fixed scripts (§10.27)."""

    steps: tuple[int, ...]
    up: int
    down: int
    strike_near: int
    strike_far: int
    strike_script: int
    heavy_script: int
    hurt_script: int
    hop_table: int
    spark: HitSparkRules


def _walk(
    fight: Fight, handle: int, rules: StalkerRules, knight_rules: KnightRules
) -> HandlerResult:
    me, cells = fight.actor(handle), fight.approach
    phase = me.anim_phase
    if me.input & (RIGHT | LEFT):
        cells.dx, cells.dy = rules.steps[phase * 2 : phase * 2 + 2]
        if not me.input & RIGHT:
            cells.dx = u16(-cells.dx)
    if me.input & UP:
        cells.dy = rules.up
    elif me.input & DOWN:
        cells.dy = rules.down
    forward = RIGHT if me.facing == 1 else LEFT
    return take_step(fight, handle, 0, 1 if me.input & forward else -1, knight_rules.probes)


def _attack(fight: Fight, me: Actor, rules: StalkerRules) -> HandlerResult:
    distance = s16(abs_word(me.x - fight.actor(fight.first).x))
    if s16(rules.strike_near) <= distance < s16(rules.strike_far) and me.action != _HEAVY:
        me.action = _HEAVY
        return restart_from(me, rules.heavy_script)
    me.action = _STRIKE
    fight.hop_facing, fight.hop_table = me.facing, rules.hop_table
    return restart_from(me, rules.strike_script)


def stalker_handler(
    rules: StalkerRules, knight_rules: KnightRules, fight: Fight, handle: int
) -> HandlerResult:
    """Hit before hurt; fixed hurt script, otherwise approach and strike or walk (§10.27)."""
    me = fight.actor(handle)
    me.input = 0
    if me.hit_target:
        return restart_from(me, me.after_hit_script)
    if me.attacker:
        me.hp = u16(me.hp - contact_damage(fight, knight_rules, fight.actor(me.attacker)))
        hit_spark(fight, me, rules.spark)
        return restart_from(me, rules.hurt_script)
    target = fight.actor(fight.first)
    if s16(target.hp) <= 0:
        return restart_from(me, me.idle_script)
    walking, _ = approach(fight, me, target)
    return _walk(fight, handle, rules, knight_rules) if walking else _attack(fight, me, rules)


def install_stalker(fight: Fight, rules: StalkerRules, knight_rules: KnightRules) -> None:
    """Register the original Troll type $40 (§10.27)."""
    fight.handlers[STALKER_TYPE] = partial(stalker_handler, rules, knight_rules)
