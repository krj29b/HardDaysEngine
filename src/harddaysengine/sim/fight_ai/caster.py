"""Ratmen handler and shared grab state (ARCHITECTURE.md §10.23)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

from harddaysengine.sim.fight import END, KEEP, CasterPhase, HandlerResult, restart_from
from harddaysengine.sim.fight_ai.approach import abs_word, advance_phase, face_target
from harddaysengine.sim.fight_hurt import contact_damage
from harddaysengine.sim.fight_moves import DOWN, FIRE
from harddaysengine.sim.m68k import s8, s16, u8, u16
from harddaysengine.sim.trajectory import (
    FlightRules,
    FlightSlot,
    aim_flight,
    start_flight,
    step_flight,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from harddaysengine.sim.actor import Actor
    from harddaysengine.sim.fight import Fight
    from harddaysengine.sim.fighter_rules import KnightRules

CASTER_TYPE = 0x24
_AIRBORNE_PHASES = frozenset({CasterPhase.INITIAL_FLIGHT, CasterPhase.OUT_FLIGHT})


@dataclass(slots=True, frozen=True)
class CasterScripts:
    """Fixed scripts decoded from checked relocated operands (§10.23)."""

    strike8: int
    strike4: int
    flight: int
    thrown: int
    hold_far: int
    hold_near: int
    grab: int
    escape: int
    windup: int
    struggle: int
    victim_released: int
    drop: int
    squeeze: int
    finish: int
    victim_fall: int
    death: int
    flight_hurt: int
    flight_death: int
    counter8: int
    counter4: int
    slam_start: int


@dataclass(slots=True, frozen=True)
class CasterRules:
    """Original immediates and scripts; no executable tunables live in the handler."""

    scripts: CasterScripts
    flight: FlightRules
    strike8_range: int
    strike4_range: int
    initial_wait: int
    initial_height: int
    initial_depth: int
    initial_steps: int
    initial_arc: int
    minimum_steps: int
    quick_range: int
    quick_arc: int
    airborne_height: int
    hold_range: int
    throw_wait: int
    throw_height: int
    throw_x: int
    throw_steps: int
    throw_arc: int
    displayed_height: int
    throw_damage: int
    squeeze_damage: int
    dead_hp: int
    shared_wait: int
    endurance_bonus: int
    object_x: int
    object_height: int
    object_facing: int


@dataclass(slots=True)
class _Turn:
    fight: Fight
    rules: CasterRules
    knight_rules: KnightRules
    handle: int

    @property
    def me(self) -> Actor:
        return self.fight.actor(self.handle)

    @property
    def target(self) -> Actor:
        return self.fight.actor(self.fight.first)

    @property
    def phase(self) -> CasterPhase:
        return self.fight.caster.phases.get(self.handle, CasterPhase.IDLE)

    def set_phase(self, phase: CasterPhase) -> None:
        if phase is CasterPhase.IDLE:
            self.fight.caster.phases.pop(self.handle, None)
        else:
            self.fight.caster.phases[self.handle] = phase

    def script(self, script: int) -> HandlerResult:
        return restart_from(self.me, script)

    def damage(self, attacker: Actor | None = None) -> int:
        return contact_damage(
            self.fight, self.knight_rules, self.target if attacker is None else attacker
        )


def _subtract(actor: Actor, amount: int) -> bool:
    difference = s16(actor.hp) - s16(amount)
    actor.hp = u16(difference)
    return difference > 0


def _walk(turn: _Turn, block: int) -> HandlerResult:
    advance_phase(turn.fight, turn.me, block, 1)
    script = turn.fight.table_long(turn.me.walk_scripts, block + (turn.me.anim_phase << 2))
    return turn.script(script)


def _aim(turn: _Turn) -> HandlerResult:
    f, me, rules = turn.fight, turn.me, turn.rules
    distance, steps, _ = aim_flight(f.flights, rules.flight, turn.handle, me, turn.target)
    f.flights.request.steps = max(steps >> 1, rules.minimum_steps)
    if s16(distance) <= s16(rules.quick_range):
        turn.set_phase(CasterPhase.QUICK_FLIGHT)
        f.flights.request.arc = rules.quick_arc
        start_flight(f.flights, rules.flight)
        return _walk(turn, 32)
    start_flight(f.flights, rules.flight)
    turn.set_phase(CasterPhase.OUT_FLIGHT)
    return turn.script(rules.scripts.flight)


def _begin(turn: _Turn) -> HandlerResult:
    f, me, rules = turn.fight, turn.me, turn.rules
    me.action = me.anim_phase = 0
    if f.caster.initial_active:
        return _aim(turn)
    f.caster.initial_active = True
    turn.set_phase(CasterPhase.INITIAL_FLIGHT)
    me.cooldown = rules.initial_wait
    job = f.find_job(f.second)
    request = f.flights.request
    request.owner = turn.handle
    request.x, request.depth, request.height = me.x, me.y, rules.initial_height
    request.target_x = 0 if job is None else job.x
    request.target_depth = u16((0 if job is None else job.depth) + rules.initial_depth)
    request.target_height = 0 if job is None else job.height
    request.steps, request.arc = rules.initial_steps, rules.initial_arc
    start_flight(f.flights, rules.flight)
    return turn.script(rules.scripts.flight)


def _choose(turn: _Turn) -> HandlerResult:
    f, me, target = turn.fight, turn.me, turn.target
    face_target(me, target)
    if s16(abs_word(target.y - me.y)) > s16(me.depth_reach):
        return _begin(turn)
    if f.caster.victim_grabbed or f.caster.victim_held or s16(target.hp) <= 0:
        return turn.script(me.idle_script)
    if f.caster.wait:
        f.caster.wait = u16(f.caster.wait - 1)
        return turn.script(me.idle_script)
    distance = s16(abs_word(target.x - me.x))
    if distance <= s16(turn.rules.strike8_range):
        me.action = 8
        return turn.script(turn.rules.scripts.strike8)
    if distance <= s16(turn.rules.strike4_range):
        me.action = 4
        return turn.script(turn.rules.scripts.strike4)
    return _begin(turn)


def _hold(turn: _Turn) -> HandlerResult:
    me = turn.me
    me.cooldown = u16(me.cooldown - 1)
    if me.cooldown == 0:
        turn.set_phase(CasterPhase.IDLE)
        turn.fight.caster.initial_active = False
        return _aim(turn)
    near = s16(abs_word(turn.target.x - me.x)) < s16(turn.rules.hold_range)
    scripts = turn.rules.scripts
    return turn.script(scripts.hold_near if near else scripts.hold_far)


def _flight(turn: _Turn) -> HandlerResult:
    me = turn.me
    if step_flight(turn.fight.flights, turn.handle, me):
        if turn.phase is CasterPhase.INITIAL_FLIGHT:
            turn.set_phase(CasterPhase.HOLD)
            return _hold(turn)
        turn.set_phase(CasterPhase.IDLE)
        me.height = 0
        return turn.script(me.idle_script)
    block = 32 if s16(me.height) <= s16(turn.rules.airborne_height) else 0
    return _walk(turn, block)


def _escape_input(turn: _Turn) -> bool:
    return turn.fight.ports[1] & (FIRE | DOWN) == FIRE | DOWN


def _grab(turn: _Turn) -> HandlerResult:
    me, scripts = turn.me, turn.rules.scripts
    if _escape_input(turn):
        turn.set_phase(CasterPhase.RELEASE)
        return turn.script(scripts.escape)
    me.cooldown = u16(me.cooldown - 1)
    if me.cooldown:
        return turn.script(scripts.grab)
    turn.set_phase(CasterPhase.THROW)
    return turn.script(scripts.windup)


def _release_victim(turn: _Turn, script: int) -> None:
    target = turn.target
    turn.fight.toggle_pause(turn.fight.first)
    target.hit_target = target.attacker = 0
    turn.fight.restart_job(turn.fight.first, script)


def _release(turn: _Turn) -> HandlerResult:
    _release_victim(turn, turn.target.after_hit_script)
    turn.fight.caster.victim_grabbed = False
    turn.set_phase(CasterPhase.IDLE)
    return END


def _throw(turn: _Turn) -> HandlerResult:
    f, me, rules = turn.fight, turn.me, turn.rules
    f.caster.victim_grabbed = False
    me.cooldown = rules.throw_wait
    request = f.flights.request
    request.owner = turn.handle
    request.x, request.depth = me.x, me.y
    request.height = u16(me.height + rules.throw_height)
    request.target_x, request.target_depth = u16(me.x + rules.throw_x), me.y
    request.target_height = me.height
    request.steps, request.arc = rules.throw_steps, rules.throw_arc
    start_flight(f.flights, rules.flight)
    turn.set_phase(CasterPhase.OUT_FLIGHT)
    me.anim_phase = 0
    me.height = rules.displayed_height
    job = f.find_job(f.first)
    if job is not None:
        job.facing = me.facing
    alive = _subtract(turn.target, rules.throw_damage)
    _release_victim(turn, turn.target.idle_script if alive else rules.scripts.victim_fall)
    return turn.script(rules.scripts.thrown)


def _slam(turn: _Turn) -> HandlerResult:
    scripts = turn.rules.scripts
    if _escape_input(turn):
        if _subtract(turn.me, turn.damage()):
            return turn.script(scripts.struggle)
        _release_victim(turn, scripts.victim_released)
        turn.fight.caster.victim_held = False
        turn.set_phase(CasterPhase.IDLE)
        return turn.script(scripts.drop)
    alive = _subtract(turn.target, turn.rules.squeeze_damage)
    return turn.script(scripts.squeeze if alive else scripts.finish)


_THINK_DISPATCH: dict[CasterPhase, Callable[[_Turn], HandlerResult]] = {
    CasterPhase.INITIAL_FLIGHT: _flight,
    CasterPhase.OUT_FLIGHT: _flight,
    CasterPhase.QUICK_FLIGHT: _flight,
    CasterPhase.GRABBED: _grab,
    CasterPhase.RELEASE: _release,
    CasterPhase.THROW: _throw,
    CasterPhase.HOLD: _hold,
    CasterPhase.SLAM: _slam,
}


def _think(turn: _Turn) -> HandlerResult:
    turn.me.action = 0
    return _THINK_DISPATCH.get(turn.phase, _choose)(turn)


def _hurt(turn: _Turn) -> HandlerResult:
    me, scripts = turn.me, turn.rules.scripts
    attacker = turn.fight.actor(me.attacker)
    if attacker.type != 0x0C:
        return _think(turn)
    airborne = turn.phase in _AIRBORNE_PHASES
    lethal = attacker.action == 0x18 if airborne else attacker.action in (0x10, 0x1C)
    if lethal:
        turn.set_phase(CasterPhase.IDLE)
        me.height = 0
        me.hp = turn.rules.dead_hp
        return turn.script(scripts.flight_death if airborne else scripts.death)
    _subtract(me, turn.damage(attacker))
    if airborne:
        turn.set_phase(CasterPhase.IDLE)
        me.height = 0
        return turn.script(scripts.flight_hurt)
    return turn.script(turn.fight.table_long(me.hurt_scripts, attacker.action))


def _catch(turn: _Turn, victim: Actor) -> HandlerResult:
    f, me = turn.fight, turn.me
    if f.caster.victim_grabbed:
        return _flight(turn)
    turn.set_phase(CasterPhase.GRABBED)
    f.caster.victim_grabbed = True
    me.cooldown = u16(s8(u8(victim.endurance + turn.rules.endurance_bonus)))
    me.height = 0
    f.toggle_pause(me.hit_target)
    return turn.script(turn.rules.scripts.grab)


def _hit(turn: _Turn) -> HandlerResult:
    f, me = turn.fight, turn.me
    victim = f.actor(me.hit_target)
    if victim.type == CASTER_TYPE:
        return _think(turn)
    if turn.phase in _AIRBORNE_PHASES:
        return _catch(turn, victim)
    if turn.phase is CasterPhase.HOLD:
        turn.set_phase(CasterPhase.SLAM)
        f.caster.victim_held = True
        f.toggle_pause(me.hit_target)
        return turn.script(turn.rules.scripts.slam_start)
    turn.set_phase(CasterPhase.IDLE)
    f.caster.wait = turn.rules.shared_wait
    if me.facing == victim.facing:
        turn.target.facing ^= 2
        job = f.find_job(f.first)
        if job is not None:
            job.facing ^= 2
    scripts = {8: turn.rules.scripts.counter8, 4: turn.rules.scripts.counter4}
    return turn.script(scripts[me.action]) if me.action in scripts else KEEP


def caster_handler(
    rules: CasterRules, knight_rules: KnightRules, fight: Fight, handle: int
) -> HandlerResult:
    """Dispatch hurt before hit before phase-driven thinking (§10.23)."""
    turn = _Turn(fight, rules, knight_rules, handle)
    if turn.me.attacker:
        return _hurt(turn)
    if turn.me.hit_target:
        return _hit(turn)
    return _think(turn)


def install_caster(fight: Fight, rules: CasterRules, knight_rules: KnightRules) -> None:
    """Install the Ratmen handler and initialize the fight's shared trajectory pool."""
    fight.handlers[CASTER_TYPE] = partial(caster_handler, rules, knight_rules)
    fight.flights.slots = [FlightSlot() for _ in range(rules.flight.slots)]
