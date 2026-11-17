"""Dragon phases, persistent actor and linked attack objects (ARCHITECTURE.md §10.30)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

from pymoonstone.sim.fight import DRAGON, END, KEEP, FightError, HandlerResult, restart_from
from pymoonstone.sim.fight_ai.approach import abs_word, approach
from pymoonstone.sim.fight_ai.effects import HitSparkRules, hit_spark
from pymoonstone.sim.fight_hurt import contact_damage
from pymoonstone.sim.fight_moves import DOWN, LEFT, RIGHT, UP
from pymoonstone.sim.m68k import s8, s16, u8, u16
from pymoonstone.sim.trajectory import FlightRules, FlightSlot, start_flight, step_flight

if TYPE_CHECKING:
    from pymoonstone.sim.actor import Actor
    from pymoonstone.sim.fight import Fight
    from pymoonstone.sim.fight_script import Job, RoutineCall
    from pymoonstone.sim.fighter_rules import KnightRules

DRAGON_TYPE, PART_TYPE = 0x14, 0x2C
FLIGHT, PHASE2, REDUCED, HIT = 0x10, 0x20, 0x40, 0x80


@dataclass(slots=True, frozen=True)
class DragonScripts:
    """Fixed Dragon scripts decoded from executable operands (§10.30)."""

    strike8: int
    breath: int
    bite: int
    finish: int
    fire: int
    part_attack: int
    part_death: int


@dataclass(slots=True, frozen=True)
class DragonFlight:
    """One decoded phase's flight request and walk-table block (§10.30)."""

    block: int
    steps: int
    x: int
    height: int
    arc: int


@dataclass(slots=True, frozen=True)
class DragonRules:
    """Runtime Dragon tunables, scripts and shared helper definitions (§10.30)."""

    scripts: DragonScripts
    near: DragonFlight
    far: DragonFlight
    flight: FlightRules
    spark: HitSparkRules
    phase_range: int
    breath_range: int
    step: int
    minimum_x: int
    maximum_x: int
    reduced_reach: int
    reduced_close: int
    dagger_damage: int
    link_offset: int
    link_gap: int
    fire_x: int
    fire_y: int
    fire_height: int
    fire_facing: int
    fire_kind: int
    fire_list: int
    hop_table: int
    part_limit: int
    part_action: int
    sounds: tuple[int, int]


def _script(actor: Actor, script: int) -> HandlerResult:
    return END if script == 0 else restart_from(actor, script)


def _links(fight: Fight, rules: DragonRules) -> None:
    depth = u16(fight.actor(DRAGON).y + rules.link_offset)
    for handle in (fight.dragon_cells.link_a, fight.dragon_cells.link_b):
        if handle:
            fight.actor(handle).y = depth
        depth = u16(depth - rules.link_gap)


def _later_job(fight: Fight, job: Job) -> Job | None:
    index = next(i for i, current in enumerate(fight.engine.jobs) if current is job) + 2
    return fight.engine.jobs[index] if index < len(fight.engine.jobs) else None


def _horizontal(actor: Actor, rules: DragonRules) -> None:
    if actor.input & RIGHT:
        x = u16(actor.x + rules.step)
        if s16(x) < s16(rules.maximum_x):
            actor.x = x
    if actor.input & LEFT:
        x = u16(actor.x - rules.step)
        if s16(x) > s16(rules.minimum_x):
            actor.x = x


def dragon_step(rules: DragonRules, fight: Fight, _call: RoutineCall | None = None) -> None:
    """Approach, bound x and retain the misplaced restoration into a later job (§10.30)."""
    actor, cells = fight.actor(DRAGON), fight.dragon_cells
    actor.input = 0
    reduced = bool(cells.flags & REDUCED)
    if reduced:
        cells.saved_reach, cells.saved_close = actor.reach_x, actor.too_close_x
        actor.reach_x, actor.too_close_x = rules.reduced_reach, rules.reduced_close
    approach(fight, actor, fight.actor(fight.first))
    actor.facing = 1
    _horizontal(actor, rules)
    if actor.input & UP:
        actor.y = u16(actor.y - rules.step)
    if actor.input & DOWN:
        actor.y = u16(actor.y + rules.step)
    job = fight.find_job(DRAGON)
    if job is not None:
        job.x, job.depth = actor.x, actor.y
        later = _later_job(fight, job)
        if reduced and later is not None:
            later.width, later.frame_height = cells.saved_reach, cells.saved_close


def _spawn_fire(fight: Fight, rules: DragonRules) -> None:
    dragon = fight.actor(DRAGON)
    fight.spawn_actor(
        rules.scripts.fire,
        rules.fire_list,
        (u16(dragon.x + rules.fire_x), rules.fire_height, u16(dragon.y + rules.fire_y)),
        rules.fire_facing,
        rules.fire_kind,
    )


def _pick(fight: Fight, rules: DragonRules, idle: int) -> HandlerResult:
    cells, actor, target = fight.dragon_cells, fight.actor(DRAGON), fight.actor(fight.first)
    cells.flags &= ~REDUCED
    script = idle
    if s16(target.hp) > 0 and fight.approach.near:
        if not cells.flags & PHASE2:
            actor.action, script = 8, rules.scripts.strike8
        elif s16(cells.distance) <= s16(rules.breath_range) and not cells.flags & HIT:
            cells.flags &= ~HIT
            actor.action, script = 4, rules.scripts.bite
        else:
            cells.flags = (cells.flags & ~HIT) | REDUCED
            actor.action, script = 0x20, rules.scripts.breath
            _spawn_fire(fight, rules)
    _links(fight, rules)
    return _script(actor, script)


def _start(fight: Fight, rules: DragonRules, *, far: bool) -> HandlerResult:
    cells, actor = fight.dragon_cells, fight.actor(DRAGON)
    plan = rules.far if far else rules.near
    cells.flags |= FLIGHT
    cells.flags = cells.flags & ~PHASE2 if far else cells.flags | PHASE2
    actor.anim_phase, actor.cooldown = 0, plan.steps
    script = fight.table_long(actor.walk_scripts, plan.block)
    request = fight.flights.request
    request.owner = DRAGON
    request.x, request.depth, request.height = actor.x, actor.y, actor.height
    request.target_x, request.target_height = plan.x, plan.height
    request.target_depth = fight.actor(fight.first).y
    request.steps, request.arc = plan.steps, plan.arc
    start_flight(fight.flights, rules.flight)
    _links(fight, rules)
    return _script(actor, script)


def _tracking_depth(depth: int, target: int, step: int) -> int:
    return u16(depth + step if s16(depth) < s16(target) else depth - step)


def _flight(fight: Fight, rules: DragonRules, alias: Job | None = None) -> HandlerResult:
    actor, cells = fight.actor(DRAGON), fight.dragon_cells
    target = fight.actor(fight.first)
    if alias is None:
        actor.cooldown = u16(actor.cooldown - 1)
        remaining = actor.cooldown
        depth = _tracking_depth(actor.y, target.y, rules.step)
        step_flight(fight.flights, DRAGON, actor)
        actor.y = depth
    else:
        later = _later_job(fight, alias)
        if later is None:
            raise FightError("Dragon alias flight addresses beyond the job table")
        later.x = u16(later.x - 1)
        remaining = later.x
        alias.height = _tracking_depth(alias.height, target.y, rules.step)
        # The job address cannot match a trajectory keyed by an actor handle.
    if remaining == 0:
        cells.flags &= ~FLIGHT
    actor.anim_phase = u8(actor.anim_phase + 1)
    phase = 7 if s8(actor.anim_phase) >= 8 else actor.anim_phase
    block = rules.near.block if cells.flags & PHASE2 else rules.far.block
    script = fight.table_long(actor.walk_scripts, block + phase * 4)
    _links(fight, rules)
    return _script(actor, script)


def _think(fight: Fight, rules: DragonRules, idle: int, alias: Job | None = None) -> HandlerResult:
    if fight.dragon_cells.flags & FLIGHT:
        return _flight(fight, rules, alias)
    dragon_step(rules, fight)
    actor, cells = fight.actor(DRAGON), fight.dragon_cells
    cells.distance = abs_word(actor.x - fight.actor(fight.first).x)
    far = s16(cells.distance) >= s16(rules.phase_range)
    if far == bool(cells.flags & PHASE2):
        return _start(fight, rules, far=far)
    return _pick(fight, rules, idle)


def _hurt(
    fight: Fight, rules: DragonRules, knight_rules: KnightRules, actor: Actor
) -> HandlerResult:
    attacker = fight.actor(actor.attacker)
    if attacker.type not in (0x0C, 0x34):
        job = fight.find_job(DRAGON)
        if job is not None and job.running:
            return KEEP
        return _think(fight, rules, actor.idle_script, job)
    fight.dragon_cells.flags |= HIT
    offset = attacker.action if attacker.type == 0x0C else 12
    amount = (
        contact_damage(fight, knight_rules, attacker)
        if attacker.type == 0x0C
        else rules.dagger_damage
    )
    actor.hp = u16(actor.hp - amount)
    script = fight.table_long(actor.hurt_scripts, offset)
    hit_spark(fight, fight.actor(DRAGON), rules.spark)
    return _script(actor, script)


def dragon_handler(
    rules: DragonRules, knight_rules: KnightRules, fight: Fight, owner: int
) -> HandlerResult:
    """Resolve hurt and hit links before the two flight/attack phases (§10.30)."""
    actor = fight.actor(owner)
    if actor.attacker:
        return _hurt(fight, rules, knight_rules, actor)
    if actor.hit_target:
        if actor.action != 4:
            return KEEP
        fight.kill_job(actor.hit_target)
        return _script(actor, rules.scripts.finish)
    actor.action = 0
    return _think(fight, rules, actor.idle_script)


def part_handler(rules: DragonRules, fight: Fight, owner: int) -> HandlerResult:
    """Linked object's attack uses action $14, distinct from Dragon breath $20 (§10.30)."""
    actor, target = fight.actor(owner), fight.actor(fight.first)
    script = actor.idle_script
    if actor.attacker:
        return _script(actor, script)
    if actor.hit_target:
        if fight.actor(actor.hit_target).type != DRAGON_TYPE:
            fight.hop_facing, fight.hop_table = 1, rules.hop_table
        return _script(actor, script)
    if s16(fight.actor(DRAGON).hp) <= 0:
        return _script(actor, rules.scripts.part_death)
    near = s16(abs_word(actor.y - target.y)) <= s16(actor.depth_reach)
    if s16(target.hp) > 0 and near and s16(target.x) <= s16(rules.part_limit):
        actor.action = rules.part_action
        return _script(actor, rules.scripts.part_attack)
    _links(fight, rules)
    return _script(actor, script)


def linked_jobs(rules: DragonRules, fight: Fight, _call: RoutineCall) -> None:
    """Put both jobs at +10, preserving the wrong-register subtraction (§10.30)."""
    a, b = fight.find_job(fight.dragon_cells.link_a), fight.find_job(fight.dragon_cells.link_b)
    depth = u16(fight.actor(DRAGON).y + rules.link_offset)
    if a is not None:
        a.depth = depth
    if b is not None:
        b.depth = depth
    if a is not None:
        for handle in (fight.dragon_cells.link_a, fight.dragon_cells.link_b):
            if handle:
                fight.actor(handle).y = a.depth


def _sound(sound: int, fight: Fight, _call: RoutineCall) -> None:
    fight.sounds.append(sound)


def install_dragon(fight: Fight, rules: DragonRules, knight_rules: KnightRules) -> None:
    """Install both original types and their movement/link/sound callbacks (§10.30)."""
    fight.handlers[DRAGON_TYPE] = partial(dragon_handler, rules, knight_rules)
    fight.handlers[PART_TYPE] = partial(part_handler, rules)
    fight.routines["dragon_step"] = partial(dragon_step, rules)
    fight.routines["dragon_link_jobs"] = partial(linked_jobs, rules)
    fight.routines["dragon_sound_0"] = partial(_sound, rules.sounds[0])
    fight.routines["dragon_sound_1"] = partial(_sound, rules.sounds[1])
    if not fight.flights.slots:
        fight.flights.slots = [FlightSlot() for _ in range(rules.flight.slots)]
