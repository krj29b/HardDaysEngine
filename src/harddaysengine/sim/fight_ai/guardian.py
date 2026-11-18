"""Guardian decisions and body job plumbing (ARCHITECTURE.md §10.29)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

from harddaysengine.sim.fight import KEEP, HandlerResult, restart_from
from harddaysengine.sim.fight_ai.approach import abs_word, approach
from harddaysengine.sim.fight_hurt import contact_damage
from harddaysengine.sim.fight_moves import DOWN, LEFT, RIGHT, UP
from harddaysengine.sim.m68k import s16, u8, u16

if TYPE_CHECKING:
    from harddaysengine.sim.actor import Actor
    from harddaysengine.sim.fight import Fight
    from harddaysengine.sim.fight_script import RoutineCall
    from harddaysengine.sim.fighter_rules import KnightRules

GUARDIAN_TYPE = 8
STAGE1, STAGE9, STAGE3, STAGE7, STAGE5, HOLD = 1, 2, 8, 16, 32, 64


@dataclass(slots=True, frozen=True)
class GuardianScripts:
    """Fixed scripts read from Guardian instructions (§10.29)."""

    lunge: int
    strike8: int
    strike4: int
    stage1: int
    stage3: int
    stage5: int
    grab5: int
    stage7: int
    grab7: int
    hurt: int
    body: int
    release: int
    victim_death: int


@dataclass(slots=True, frozen=True)
class GuardianRules:
    """Checked original Guardian immediates and pointers (§10.29)."""

    scripts: GuardianScripts
    lunge_range: int
    strike8_range: int
    strike4_range: int
    step: int
    lunge_wait: int
    strike8_wait: int
    strike4_wait: int
    grab5_min: int
    grab5_max: int
    grab5_wait: int
    grab7_min: int
    grab7_max: int
    grab7_wait: int
    snap_range: int
    snap_offset: int
    hit_wait: int
    grab_damage: int
    release_offset: int
    release_limit: int
    release_max: int
    release_min: int
    hop_table: int
    hop2_table: int
    sounds: tuple[int, ...]


@dataclass(slots=True)
class _Turn:
    fight: Fight
    owner: int
    rules: GuardianRules

    @property
    def me(self) -> Actor:
        return self.fight.actor(self.owner)

    def result(self, script: int, *, sync: bool = True) -> HandlerResult:
        if sync:
            _follow(self.fight, self.me)
        return restart_from(self.me, script)


def _follow(fight: Fight, actor: Actor) -> None:
    job = fight.find_job(fight.guardian.body)
    if job is not None:
        job.x, job.depth = actor.x, actor.y


def _wait(actor: Actor, value: int) -> None:
    actor.write(106, 1, value)


def _walk(turn: _Turn, script: int) -> HandlerResult:
    me, step = turn.me, turn.rules.step
    dx = step if me.input & RIGHT else -step if me.input & LEFT else 0
    dy = -step if me.input & UP else step if me.input & DOWN else 0
    me.x, me.y = u16(me.x + dx), u16(me.y + dy)
    return turn.result(script)


def _release(turn: _Turn) -> None:
    f = turn.fight
    f.hop_facing = f.actor(f.guardian.main).facing
    f.toggle_pause(f.first)
    f.restart_job(f.first, turn.rules.scripts.release)
    f.guardian.flags &= ~HOLD
    # The original's timer store addresses script bytes, not an actor (§10.29).


def _stages(turn: _Turn) -> HandlerResult | None:
    f, rules = turn.fight, turn.rules
    flags, scripts = f.guardian.flags, rules.scripts
    if flags & STAGE1:
        f.guardian.flags = (flags & ~STAGE1) | STAGE7
        if flags & HOLD:
            _release(turn)
        return turn.result(scripts.stage1)
    if flags & STAGE3:
        f.guardian.flags = flags & ~STAGE3
        if flags & HOLD:
            _release(turn)
        return turn.result(scripts.stage3)
    if flags & STAGE9:
        f.guardian.flags &= ~STAGE9
        return turn.result(turn.me.idle_script)
    if flags & STAGE7:
        return _grab_stage(turn, stage7=True)
    if flags & STAGE5:
        return _grab_stage(turn, stage7=False)
    return None


def _grab_stage(turn: _Turn, *, stage7: bool) -> HandlerResult:
    f, me, r = turn.fight, turn.me, turn.rules
    if stage7:
        bit, following = STAGE7, STAGE3
        low, high, wait = r.grab7_min, r.grab7_max, r.grab7_wait
        script, grabbed = r.scripts.stage7, r.scripts.grab7
    else:
        bit, following = STAGE5, STAGE1
        low, high, wait = r.grab5_min, r.grab5_max, r.grab5_wait
        script, grabbed = r.scripts.stage5, r.scripts.grab5
    f.guardian.flags = (f.guardian.flags & ~bit) | following
    distance = s16(abs_word(me.x - f.actor(f.guardian.target).x))
    if s16(low) <= distance <= s16(high):
        f.toggle_pause(f.first)
        f.guardian.flags |= HOLD
        _wait(me, wait)
        script = grabbed
    return turn.result(script)


def _think(turn: _Turn) -> HandlerResult:
    f, me = turn.fight, turn.me
    target = f.actor(f.guardian.target)
    if s16(target.hp) <= 0:
        return turn.result(me.idle_script, sync=False)
    moving, distance = approach(f, me, target)
    if moving:
        if not f.approach.near:
            return _walk(turn, me.idle_script)
        distance = abs_word(me.x - target.x)
    wait = me.read(106, 1)
    if wait:
        _wait(me, u8(wait - 1))
        if me.read(106, 1):
            return _walk(turn, me.idle_script)
    return _attack(turn, s16(distance))


def _attack(turn: _Turn, distance: int) -> HandlerResult:
    f, me, r = turn.fight, turn.me, turn.rules
    if distance <= s16(r.lunge_range):
        me.action = 0x20
        f.hop_facing, f.hop_table = me.facing, r.hop_table
        _wait(me, r.lunge_wait)
        return turn.result(r.scripts.lunge, sync=False)
    if distance <= s16(r.strike8_range):
        me.action = 8
        f.guardian.flags |= STAGE9
        _wait(me, r.strike8_wait)
        return turn.result(r.scripts.strike8)
    script = me.idle_script
    if distance <= s16(r.strike4_range):
        me.action = 4
        f.guardian.flags |= STAGE5
        f.hop_facing, f.hop_table = me.facing, r.hop2_table
        _wait(me, r.strike4_wait)
        script = r.scripts.strike4
    return _walk(turn, script)


def guardian_handler(
    rules: GuardianRules, knight_rules: KnightRules, fight: Fight, owner: int
) -> HandlerResult:
    """Hurt, hit, staged grab and ordinary decisions in original order (§10.29)."""
    turn = _Turn(fight, owner, rules)
    me, cells = turn.me, fight.guardian
    me.input = 0
    _follow(fight, me)
    if me.attacker:
        cells.flags = cells.spare = 0
        me.hp = u16(me.hp - contact_damage(fight, knight_rules, fight.actor(me.attacker)))
        target = fight.actor(cells.target)
        if s16(abs_word(me.x - target.x)) <= s16(rules.snap_range):
            offset = rules.snap_offset if me.facing == 3 else -rules.snap_offset
            me.x = u16(target.x + offset)
        return turn.result(rules.scripts.hurt)
    if me.hit_target:
        _wait(me, rules.hit_wait)
        cells.flags = cells.spare = 0
        return KEEP
    cells.target = fight.first
    staged = _stages(turn)
    return _think(turn) if staged is None else staged


def body_create(rules: GuardianRules, fight: Fight, call: RoutineCall) -> None:
    """Create the reserved body at main-job x/height/x, preserving the depth typo (§10.29)."""
    cells = fight.guardian
    job = fight.find_job(cells.main)
    if job is None:
        return
    actor = fight.actor(cells.body)
    actor.active, actor.type, actor.frame_list = 1, GUARDIAN_TYPE, call.frame_list
    actor.x, actor.height, actor.y, actor.facing = job.x, job.height, job.x, job.facing
    fight.start_job(
        rules.scripts.body,
        cells.body,
        call.frame_list,
        (job.x, job.height, job.x),
        job.facing,
        GUARDIAN_TYPE,
    )


def body_facing(fight: Fight, _call: RoutineCall) -> None:
    """Copy job facing, which may differ from the actor during script execution (§10.29)."""
    body, main = fight.find_job(fight.guardian.body), fight.find_job(fight.guardian.main)
    if body is not None and main is not None:
        body.facing = main.facing


def body_remove(fight: Fight, _call: RoutineCall) -> None:
    """Release the reserved body's job and record (§10.29)."""
    fight.kill_job(fight.guardian.body)


def grab_damage(rules: GuardianRules, fight: Fight, _call: RoutineCall) -> None:
    """The scripted grab damages and toggles the first fighter (§10.29)."""
    actor = fight.actor(fight.first)
    actor.hp = u16(actor.hp - rules.grab_damage)
    fight.toggle_pause(fight.first)


def victim_release(rules: GuardianRules, fight: Fight, _call: RoutineCall) -> None:
    """Release and reposition the victim job, then restart idle or the death script (§10.29)."""
    fight.toggle_pause(fight.first)
    job = fight.find_job(fight.first)
    main, target = fight.actor(fight.guardian.main), fight.actor(fight.first)
    if job is not None:
        job.facing, job.depth = main.facing ^ 2, main.y
        offset = -rules.release_offset if job.facing == 1 else rules.release_offset
        x = s16(u16(main.x + offset))
        if x >= s16(rules.release_limit):
            x = rules.release_max
        job.x = rules.release_min if x < 0 else x
    script = target.idle_script
    if s16(target.hp) <= 0:
        fight.guardian.fade = True
        script = rules.scripts.victim_death
    fight.restart_job(fight.first, script)


def _sounds(rules: GuardianRules, fight: Fight, _call: RoutineCall) -> None:
    fight.sounds.extend(rules.sounds)


def install_guardian(fight: Fight, rules: GuardianRules, knight_rules: KnightRules) -> None:
    """Register Guardian and all body/grab job routines (§10.29)."""
    fight.handlers[GUARDIAN_TYPE] = partial(guardian_handler, rules, knight_rules)
    for name, routine in (
        ("guardian_2", partial(body_create, rules)),
        ("guardian_body_facing", body_facing),
        ("guardian_body_remove", body_remove),
        ("guardian_grab_damage", partial(grab_damage, rules)),
        ("guardian_release", partial(victim_release, rules)),
        ("guardian_fixed_sounds", partial(_sounds, rules)),
    ):
        fight.routines[name] = routine
