"""Arena-crossing Flyer behavior (ARCHITECTURE.md §10.26)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

from pymoonstone.sim.fight import HandlerResult, end_fight, mark_first_dead, restart_from
from pymoonstone.sim.fight_hurt import contact_damage
from pymoonstone.sim.m68k import s8, s16, u8, u16

if TYPE_CHECKING:
    from pymoonstone.sim.actor import Actor
    from pymoonstone.sim.fight import Fight
    from pymoonstone.sim.fighter_rules import KnightRules

FLYER_TYPE = 0
_HEAVY_ACTION = 0x20


@dataclass(slots=True, frozen=True)
class FlyerRules:
    """Executable operands, step table and finishing scripts (§10.26)."""

    steps: tuple[int, ...]
    finish_same: int
    finish_opposite: int
    initial_toggle: int
    right_edge: int
    enter_right: int
    enter_left: int
    facing_left: int
    facing_right: int
    toggle_xor: int
    beam_mask: int
    beam_shift: int
    pause_mask: int
    pause_or: int
    pause_step: int
    phase_step: int
    phase_count: int
    phase_reset: int


def _turn(fight: Fight, me: Actor, rules: FlyerRules) -> bool:
    """Turn at an edge and select the next lane; one draw only on a turn (§10.26)."""
    if me.facing == rules.facing_left:
        if s16(me.x) >= 0:
            return False
        me.x, me.facing = rules.enter_left, rules.facing_right
    else:
        if s16(me.x) < s16(rules.right_edge):
            return False
        me.x, me.facing = rules.enter_right, rules.facing_left
    fight.flyer.toggle = u16(fight.flyer.toggle ^ rules.toggle_xor)
    me.y = fight.actor(fight.first).y
    if fight.flyer.toggle == 0:
        me.y = u16(me.y + ((fight.beam_word & rules.beam_mask) << rules.beam_shift))
    me.anim_timer = u8((fight.rng.draw() & rules.pause_mask) | rules.pause_or)
    me.anim_timer = u8(me.anim_timer - rules.pause_step)
    return me.anim_timer != 0


def _motion(fight: Fight, me: Actor, rules: FlyerRules) -> HandlerResult:
    if _turn(fight, me, rules):
        return restart_from(me, me.idle_script)
    me.anim_phase = u8(me.anim_phase + rules.phase_step)
    if me.anim_phase == rules.phase_count:
        me.anim_phase = rules.phase_reset
    phase = s8(me.anim_phase)
    if not 0 <= phase < len(rules.steps):
        raise ValueError(f"Flyer phase {phase} is outside the decoded step table")
    script = fight.table_long(me.walk_scripts, u16(phase << 2))
    step = rules.steps[phase]
    me.x = u16(me.x + (-step if me.facing & 2 else step))
    return restart_from(me, script)


def _hit(fight: Fight, me: Actor, rules: FlyerRules) -> HandlerResult | None:
    if not me.hit_target:
        return None
    victim = fight.actor(me.hit_target)
    if victim.type == FLYER_TYPE:
        return None
    if victim.hit_target or s16(victim.hp) > 0 or fight.demo:
        return restart_from(me, me.after_hit_script)
    fight.kill_job(fight.first)
    mark_first_dead(fight)
    end_fight(fight)
    script = rules.finish_same if me.facing == victim.facing else rules.finish_opposite
    return restart_from(me, script)


def flyer_handler(
    rules: FlyerRules, knight_rules: KnightRules, fight: Fight, handle: int
) -> HandlerResult:
    """Resolve blows before edge turns and phase-first flight steps (§10.26)."""
    me = fight.actor(handle)
    me.input, me.action = 0, _HEAVY_ACTION
    if me.attacker:
        attacker = fight.actor(me.attacker)
        if attacker.type != FLYER_TYPE:
            me.hp = u16(me.hp - contact_damage(fight, knight_rules, attacker))
            return restart_from(me, fight.table_long(me.hurt_scripts, attacker.action))
    result = _hit(fight, me, rules)
    return result if result is not None else _motion(fight, me, rules)


def install_flyer(fight: Fight, rules: FlyerRules, knight_rules: KnightRules) -> None:
    """Register the original type $00 handler (§10.26)."""
    fight.handlers[FLYER_TYPE] = partial(flyer_handler, rules, knight_rules)
