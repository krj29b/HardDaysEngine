"""Balok's pounce, grab and recovery handler (ARCHITECTURE.md §10.28)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

from harddaysengine.sim.fight import HandlerResult, mark_first_dead, restart_from
from harddaysengine.sim.fight_ai.approach import abs_word, face_target
from harddaysengine.sim.fight_ai.effects import HitSparkRules, hit_spark
from harddaysengine.sim.fight_hurt import contact_damage
from harddaysengine.sim.fight_script import RoutineCall
from harddaysengine.sim.m68k import s8, s16, u8, u16
from harddaysengine.sim.screen_fx import SCREEN_SHAKE
from harddaysengine.sim.trajectory import (
    FlightRules,
    FlightSlot,
    aim_flight,
    start_flight,
    step_flight,
)

if TYPE_CHECKING:
    from harddaysengine.sim.actor import Actor
    from harddaysengine.sim.fight import Fight
    from harddaysengine.sim.fighter_rules import KnightRules

DRAKE_TYPE = 0x30
GRABBED, FLYING, RECOVER, FINISHED = 1, 2, 0x20, 0x40
STRIKE, HEAVY = 8, 0x20


@dataclass(slots=True, frozen=True)
class DrakeScripts:
    """Fixed script pointers in the Balok handler (§10.28)."""

    strike: int
    heavy: int
    landing: int
    flight: int
    victim_landing: int
    hurt: int
    after_strike: int
    grab: int
    carry: int
    finish_first: int
    finish_second: int


@dataclass(slots=True, frozen=True)
class DrakeRules:
    """Decoded Balok thresholds and shared helper definitions (§10.28)."""

    scripts: DrakeScripts
    flight: FlightRules
    spark: HitSparkRules
    close: int
    hop_range: int
    heavy_range: int
    wait_range: int
    aim_reach: int
    minimum_steps: int
    maximum_steps: int
    landing_height: int
    landing_range: int
    landing_wait: int
    hard_arc: int
    release_offset: int
    toggle_xor: int
    hop_table: int
    sound_soft: int
    sound_hard_first: int
    sound_hard_second: int
    sound_grab_first: int
    sound_grab_second: int
    counter_step: int
    counter_reload: int


@dataclass(slots=True)
class _Turn:
    fight: Fight
    handle: int
    rules: DrakeRules

    @property
    def me(self) -> Actor:
        return self.fight.actor(self.handle)

    @property
    def target(self) -> Actor:
        return self.fight.actor(self.fight.first)

    def script(self, script: int) -> HandlerResult:
        return restart_from(self.me, script)


def counter(rules: DrakeRules, fight: Fight, call: RoutineCall) -> None:
    """Decrement the byte timer; signed underflow draws once then reloads a fixed value (§10.28)."""
    actor = fight.actor(call.owner)
    difference = s8(actor.anim_timer) - rules.counter_step
    actor.anim_timer = u8(actor.anim_timer - rules.counter_step)
    if difference < 0:
        fight.rng.draw()
        actor.anim_timer = rules.counter_reload


def _sound_pair(sounds: tuple[int, int], fight: Fight, _call: RoutineCall) -> None:
    fight.sounds.extend(sounds)


def _land(turn: _Turn) -> HandlerResult:
    f, me, rules = turn.fight, turn.me, turn.rules
    me.behaviour_flags2, me.height, f.drake.flags = rules.landing_wait, 0, 0
    if s16(f.caster.aim_arc) < s16(rules.hard_arc):
        f.sounds.append(rules.sound_soft)
    else:
        f.routines[SCREEN_SHAKE](
            f,
            RoutineCall(SCREEN_SHAKE, turn.handle, me.frame_list, me.x, me.height, me.y, me.facing),
        )
        f.sounds.extend((rules.sound_hard_first, rules.sound_hard_second))
    return turn.script(rules.scripts.landing)


def _flying(turn: _Turn) -> HandlerResult:
    f, me, rules = turn.fight, turn.me, turn.rules
    me.behaviour_flags2 = 0
    step_flight(f.flights, turn.handle, me)
    f.drake.remaining = u16(f.drake.remaining - 1)
    if f.drake.remaining == 0:
        return _land(turn)
    distance = s16(abs_word(me.x - turn.target.x))
    reached = s16(f.caster.aim_steps >> 1) >= s16(f.drake.remaining)
    low = s16(me.height) >= s16(rules.landing_height)
    if reached and low and distance <= s16(rules.landing_range):
        me.x, me.y = turn.target.x, turn.target.y
        f.restart_job(f.first, rules.scripts.victim_landing)
        mark_first_dead(f)
        return _land(turn)
    return turn.script(rules.scripts.flight)


def _hit(turn: _Turn) -> HandlerResult:
    f, me, scripts = turn.fight, turn.me, turn.rules.scripts
    if me.action == HEAVY:
        f.toggle_pause(me.hit_target)
        f.drake.flags |= GRABBED
        return turn.script(scripts.grab)
    return turn.script(scripts.after_strike if me.action == STRIKE else me.after_hit_script)


def _recover(turn: _Turn) -> HandlerResult:
    f, me, target, rules = turn.fight, turn.me, turn.target, turn.rules
    if s16(target.hp) <= 0:
        f.drake.toggle = u16(f.drake.toggle ^ rules.toggle_xor)
        f.drake.flags = FINISHED
        script = rules.scripts.finish_first if f.drake.toggle else rules.scripts.finish_second
        return turn.script(script)
    target.hit_target = target.attacker = 0
    f.toggle_pause(f.first)
    f.restart_job(f.first, target.idle_script)
    job = f.find_job(f.first)
    if job is not None:
        offset = -rules.release_offset if me.facing & 2 else rules.release_offset
        job.x = u16(me.x + offset)
    f.drake.flags = 0
    return turn.script(me.after_hit_script)


def _jump(turn: _Turn) -> HandlerResult:
    f, me, rules = turn.fight, turn.me, turn.rules
    distance, steps, arc = aim_flight(
        f.flights, rules.flight, turn.handle, me, turn.target, reach_x=rules.aim_reach
    )
    f.caster.aim_distance, f.caster.aim_steps, f.caster.aim_arc = distance, steps, arc
    if s16(steps) <= s16(rules.minimum_steps):
        return turn.script(me.idle_script)
    if me.behaviour_flags2:
        me.behaviour_flags2 = u8(me.behaviour_flags2 - 1)
        if me.behaviour_flags2:
            return turn.script(me.idle_script)
    me.action = 0
    f.drake.remaining = min(s16(steps), s16(rules.maximum_steps))
    f.flights.request.steps = f.drake.remaining
    start_flight(f.flights, rules.flight)
    f.drake.flags = FLYING
    return turn.script(rules.scripts.landing)


def _melee(turn: _Turn, distance: int) -> HandlerResult:
    me, f, rules = turn.me, turn.fight, turn.rules
    if distance <= s16(rules.close):
        me.behaviour_flags2 = 0
        return _jump(turn)
    if distance <= s16(rules.hop_range) and me.action != STRIKE:
        me.action = STRIKE
        f.hop_facing, f.hop_table = me.facing, rules.hop_table
        return turn.script(rules.scripts.strike)
    if distance <= s16(rules.heavy_range):
        me.action = HEAVY
        return turn.script(rules.scripts.heavy)
    if turn.target.daggers == 0 and distance <= s16(rules.wait_range):
        return turn.script(me.idle_script)
    return _jump(turn)


def _think(turn: _Turn) -> HandlerResult:
    f, me, target = turn.fight, turn.me, turn.target
    if s16(target.hp) <= 0:
        return turn.script(me.idle_script)
    f.drake.target_x, f.drake.target_y = target.x, target.y
    face_target(me, target)
    if s16(abs_word(me.y - target.y)) <= s16(me.depth_reach):
        f.drake.flags = 0
        return _melee(turn, s16(abs_word(me.x - target.x)))
    return _jump(turn)


def drake_handler(
    rules: DrakeRules, knight_rules: KnightRules, fight: Fight, handle: int
) -> HandlerResult:
    """Flight, hurt, hit and grab/recovery states precede normal decisions (§10.28)."""
    turn = _Turn(fight, handle, rules)
    me = turn.me
    if fight.drake.flags & FLYING:
        return _flying(turn)
    if me.attacker:
        me.hp = u16(me.hp - contact_damage(fight, knight_rules, fight.actor(me.attacker)))
        hit_spark(fight, me, rules.spark)
        return turn.script(rules.scripts.hurt)
    if me.hit_target:
        return _hit(turn)
    if fight.drake.flags & GRABBED:
        fight.drake.flags = (fight.drake.flags & ~GRABBED) | RECOVER
        return turn.script(rules.scripts.carry)
    return _recover(turn) if fight.drake.flags & RECOVER else _think(turn)


def install_drake(fight: Fight, rules: DrakeRules, knight_rules: KnightRules) -> None:
    """Install Balok and its shared counter routine, retaining an existing flight pool (§10.28)."""
    fight.handlers[DRAKE_TYPE] = partial(drake_handler, rules, knight_rules)
    fight.routines["counter"] = partial(counter, rules)
    fight.routines["drake_grab_sounds"] = partial(
        _sound_pair, (rules.sound_grab_first, rules.sound_grab_second)
    )
    if not fight.flights.slots:
        fight.flights.slots = [FlightSlot() for _ in range(rules.flight.slots)]
