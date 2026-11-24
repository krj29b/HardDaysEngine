"""Mudmen grab, carry and approach rules (ARCHITECTURE.md §10.25)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

from harddaysengine.sim.fight import (
    HandlerResult,
    SnatchEncounterPhase,
    SnatcherPhase,
    mark_first_dead,
    restart_from,
)
from harddaysengine.sim.fight_ai.approach import abs_word, advance_phase, approach
from harddaysengine.sim.fight_hurt import contact_damage
from harddaysengine.sim.fight_moves import DOWN, FIRE, LEFT, RIGHT, UP, movement_mask
from harddaysengine.sim.m68k import s16, u8, u16

if TYPE_CHECKING:
    from collections.abc import Callable

    from harddaysengine.sim.actor import Actor
    from harddaysengine.sim.fight import Fight
    from harddaysengine.sim.fighter_rules import KnightRules

SNATCHER_TYPE = 0x04


@dataclass(slots=True, frozen=True)
class SnatcherScripts:
    """Fixed script operands in handler order (§10.25)."""

    reach: int
    appear: int
    strike: int
    sink: int
    emerge: int
    release: int
    carry: int
    swallow: int
    hurt: int


@dataclass(slots=True, frozen=True)
class SnatcherRules:
    """Decoded executable tunables and scripts (§10.25)."""

    scripts: SnatcherScripts
    steps: tuple[int, ...]
    up: int
    down: int
    walk_range: int
    strike_range: int
    reach_range: int
    appear_offset: int
    appear_left: int
    appear_side: int
    facing_left: int
    facing_right: int
    grab_min: int
    grab_max: int
    release_damage: int
    hold_timer: int
    hurt_timer: int


@dataclass(slots=True)
class _Turn:
    fight: Fight
    rules: SnatcherRules
    knight_rules: KnightRules
    handle: int

    @property
    def me(self) -> Actor:
        return self.fight.actor(self.handle)

    @property
    def target(self) -> Actor:
        return self.fight.actor(self.fight.first)

    @property
    def phase(self) -> SnatcherPhase:
        return self.fight.snatcher.phases.get(self.handle, SnatcherPhase.HIDDEN)

    def set_phase(self, phase: SnatcherPhase) -> None:
        if phase is SnatcherPhase.HIDDEN:
            self.fight.snatcher.phases.pop(self.handle, None)
        else:
            self.fight.snatcher.phases[self.handle] = phase

    def script(self, script: int) -> HandlerResult:
        return restart_from(self.me, script)

    def idle(self) -> HandlerResult:
        return self.script(self.me.idle_script)

    def near(self) -> bool:
        return s16(abs_word(self.me.y - self.target.y)) <= s16(self.me.depth_reach)


def _walk(turn: _Turn) -> HandlerResult:
    f, me, rules = turn.fight, turn.me, turn.rules
    advance_phase(f, me, 0, 1)
    cells = f.approach
    if me.input & RIGHT:
        cells.dx = rules.steps[me.anim_phase]
    if me.input & LEFT:
        cells.dx = u16(-rules.steps[me.anim_phase])
    if me.input & UP:
        cells.dy = rules.up
    if me.input & DOWN:
        cells.dy = rules.down
    me.input &= movement_mask(f, turn.handle, cells.dx, turn.knight_rules.probes)
    if not me.input & (LEFT | RIGHT):
        cells.dx = 0
    if not me.input & (UP | DOWN):
        cells.dy = 0
    me.x, me.y = u16(me.x + cells.dx), u16(me.y + cells.dy)
    if cells.dx == cells.dy == 0:
        me.anim_phase = 0
        return turn.idle()
    return turn.script(f.table_long(me.walk_scripts, me.anim_phase << 2))


def _freed(turn: _Turn) -> HandlerResult:
    if turn.phase is not SnatcherPhase.FREED:
        return turn.idle()
    turn.fight.kill_job(turn.fight.first)
    turn.fight.snatcher.encounter = SnatchEncounterPhase.NONE
    turn.set_phase(SnatcherPhase.APPROACHING)
    return turn.script(turn.rules.scripts.swallow)


def _carrying(turn: _Turn) -> HandlerResult:
    f, me = turn.fight, turn.me
    if turn.phase is not SnatcherPhase.CARRYING:
        return turn.idle()
    me.anim_timer = u8(me.anim_timer - 1)
    if me.anim_timer == 0:
        f.snatcher.encounter = SnatchEncounterPhase.FREED
        turn.set_phase(SnatcherPhase.FREED)
        mark_first_dead(f)
        return _freed(turn)
    if f.ports[1] & (FIRE | DOWN) == FIRE | DOWN:
        f.snatcher.encounter = SnatchEncounterPhase.RELEASED
        turn.set_phase(SnatcherPhase.RELEASED)
        return turn.script(turn.rules.scripts.release)
    return turn.script(turn.rules.scripts.carry)


def _released(turn: _Turn) -> HandlerResult:
    if turn.phase is not SnatcherPhase.RELEASED:
        return turn.idle()
    turn.fight.snatcher.encounter = SnatchEncounterPhase.NONE
    turn.set_phase(SnatcherPhase.APPROACHING)
    turn.fight.toggle_pause(turn.fight.first)
    turn.target.hit_target = turn.target.attacker = 0
    turn.fight.restart_job(turn.fight.first, turn.target.idle_script)
    turn.me.hp = u16(turn.me.hp - turn.rules.release_damage)
    return turn.script(turn.rules.scripts.hurt)


def _appear(turn: _Turn) -> HandlerResult:
    me, target, rules = turn.me, turn.target, turn.rules
    me.anim_timer = u8(me.anim_timer - 1)
    if me.anim_timer == 0:
        return turn.idle()
    left = s16(target.x) < s16(rules.appear_side)
    me.x = u16(target.x + (rules.appear_offset if left else rules.appear_left))
    me.y = target.y
    me.facing = rules.facing_left if left else rules.facing_right
    turn.set_phase(SnatcherPhase.READY)
    return turn.script(rules.scripts.appear)


def _grab(turn: _Turn) -> HandlerResult:
    distance = s16(abs_word(turn.me.x - turn.target.x))
    if turn.near() and s16(turn.rules.grab_min) <= distance <= s16(turn.rules.grab_max):
        turn.fight.toggle_pause(turn.fight.first)
        mark_first_dead(turn.fight)
        turn.fight.snatcher.encounter = SnatchEncounterPhase.GONE
        return turn.script(turn.rules.scripts.sink)
    turn.set_phase(SnatcherPhase.APPROACHING)
    return turn.script(turn.rules.scripts.emerge)


def _approach(turn: _Turn) -> HandlerResult:
    flag, distance = approach(turn.fight, turn.me, turn.target)
    if flag:
        if turn.fight.approach.near == 0:
            return _walk(turn)
        distance = abs_word(turn.me.x - turn.target.x)
    distance, rules = s16(distance), turn.rules
    if distance <= s16(rules.walk_range):
        return _walk(turn)
    if distance < s16(rules.strike_range):
        turn.set_phase(SnatcherPhase.READY)
        return turn.script(rules.scripts.strike)
    if distance < s16(rules.reach_range) and turn.near():
        return turn.script(rules.scripts.reach)
    return _walk(turn)


_ENCOUNTER_DISPATCH: dict[SnatchEncounterPhase, Callable[[_Turn], HandlerResult]] = {
    SnatchEncounterPhase.CARRYING: _carrying,
    SnatchEncounterPhase.FREED: _freed,
    SnatchEncounterPhase.RELEASED: _released,
}


def _decide(turn: _Turn) -> HandlerResult:
    encounter = turn.fight.snatcher.encounter
    if encounter is SnatchEncounterPhase.GONE:
        return turn.idle()
    shared = _ENCOUNTER_DISPATCH.get(encounter)
    if shared is not None:
        return shared(turn)
    phase = turn.phase
    if phase is SnatcherPhase.HIDDEN:
        return _appear(turn)
    if phase is SnatcherPhase.READY:
        return _grab(turn)
    return _approach(turn)


def snatcher_handler(
    rules: SnatcherRules, knight_rules: KnightRules, fight: Fight, handle: int
) -> HandlerResult:
    """Dispatch hurt, hit, shared grab state, appearance and approach (§10.25)."""
    turn = _Turn(fight, rules, knight_rules, handle)
    me = turn.me
    me.input = 0
    if me.attacker:
        amount = contact_damage(fight, knight_rules, fight.actor(me.attacker))
        me.hp = u16(me.hp - amount)
        me.anim_timer = rules.hurt_timer
        return turn.script(rules.scripts.hurt)
    if me.hit_target:
        fight.toggle_pause(me.hit_target)
        fight.snatcher.encounter = SnatchEncounterPhase.CARRYING
        turn.set_phase(SnatcherPhase.CARRYING)
        me.anim_timer = rules.hold_timer
        return turn.script(rules.scripts.carry)
    return _decide(turn)


def install_snatcher(fight: Fight, rules: SnatcherRules, knight_rules: KnightRules) -> None:
    """Install the original type $04 handler (§10.25)."""
    fight.handlers[SNATCHER_TYPE] = partial(snatcher_handler, rules, knight_rules)
