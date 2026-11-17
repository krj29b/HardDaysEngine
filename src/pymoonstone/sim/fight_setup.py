"""Fight entry lanes, scaling, warning ramps and defeat settlement (§10.24)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pymoonstone.sim.fight_hurt import contact_damage
from pymoonstone.sim.m68k import s8, s16, u8, u16
from pymoonstone.sim.palette_fx import step_color

if TYPE_CHECKING:
    from pymoonstone.sim.fight import Fight
    from pymoonstone.sim.fighter_rules import KnightRules


@dataclass(slots=True, frozen=True)
class FightSetupRules:
    """Runtime-decoded original fight-loop and setup operands (ARCHITECTURE.md §10.24)."""

    frame_vbls: int
    enter_script: int
    knight_x: int
    knight_height: int
    knight_facing: int
    opponent_x: int
    opponent_facing: int
    lane_bottom: int
    lane_offset: int
    strength_limit: int
    hp_limits: tuple[int, int, int]
    class_base: int
    class_limit: int
    class_cap: int
    death_delay: int
    warning_hp: int
    warning_period: int
    warning_rows: tuple[tuple[int, ...], ...]


@dataclass(slots=True)
class SpawnLanes:
    """The original session-persistent 1,2,3 entry cursor; an anchor does not advance it."""

    counter: int = 0

    def next(self, edge: int, rules: FightSetupRules) -> int:
        self.counter = (self.counter + 1) & 3
        if self.counter == 0:
            self.counter = 1
        return lane_depth(self.counter, edge, rules)


def lane_depth(lane: int, edge: int, rules: FightSetupRules) -> int:
    """The word-wrapped three entry depths (§10.5, §10.24)."""
    gap = u16(rules.lane_bottom - edge)
    offsets = {3: gap >> 1, 2: gap >> 2, 1: (gap >> 1) + (gap >> 2)}
    return u16(edge + offsets[lane] - rules.lane_offset)


def wave_class(fight: Fight, knight_rules: KnightRules, rules: FightSetupRules) -> int:
    """Force action 8, compute contact damage and the original clamped table column."""
    knight = fight.actor(fight.first)
    knight.action = 8
    value = u16(contact_damage(fight, knight_rules, knight) + (u16(knight.hp_max) >> 2))
    value = u16((value >> 1) - rules.class_base)
    if s16(value) < 0:
        value = 0
    if s16(value) >= rules.class_limit:
        value = rules.class_cap
    return value >> 1


def scale_wave(
    fight: Fight,
    knight_rules: KnightRules,
    rules: FightSetupRules,
    cut: tuple[int, ...],
    *,
    lair_count: int | None = None,
    single: bool = False,
    cap: int | None = None,
) -> None:
    """Scale decoded base cells, then lair override, concurrency limits and signed cut."""
    knight = fight.actor(fight.first)
    hp = s16(knight.hp_max)
    fight.max_alive = u16(fight.max_alive + int(s8(knight.strength) > rules.strength_limit))
    fight.total = u16(fight.total + int(hp >= rules.hp_limits[0]) + int(hp >= rules.hp_limits[2]))
    fight.max_alive = u16(fight.max_alive + int(hp >= rules.hp_limits[1]))
    if lair_count is not None:
        fight.total = u16(lair_count)
    if single:
        fight.max_alive = 1
    if cap is not None and s16(fight.max_alive) > cap:
        fight.max_alive = cap
    _cut_wave(fight, knight_rules, rules, cut)


def _cut_wave(
    fight: Fight, knight_rules: KnightRules, rules: FightSetupRules, cut: tuple[int, ...]
) -> None:
    if s16(fight.total) <= 0:
        return
    column = wave_class(fight, knight_rules, rules)
    if cut:
        remaining = s16(fight.total) - cut[column]
        if remaining > 0:
            fight.total = u16(remaining)


@dataclass(slots=True)
class WarningRamp:
    """A palette ramp independent of the ten fight jobs; repeats indefinitely."""

    index: int
    target: int
    back: int
    counter: int


@dataclass(slots=True)
class FightWarnings:
    """Six possible low-health palette ramps, triggered once per fighter (§10.14)."""

    colors: list[int]
    ramps: list[WarningRamp] = field(default_factory=list)
    started: list[bool] = field(default_factory=lambda: [False, False])

    def register(self, fight: Fight, opponent: int, rules: FightSetupRules) -> None:
        for slot, handle in enumerate((fight.first, opponent)):
            if handle == 0 or self.started[slot]:
                continue
            actor = fight.actor(handle)
            if s16(actor.hp) > s16(rules.warning_hp):
                continue
            self.started[slot] = True
            row = rules.warning_rows[actor.kind if actor.kind < 4 else 4]
            for index, target in enumerate(row, start=6 + slot * 3):
                self.ramps.append(
                    WarningRamp(index, target, self.colors[index], rules.warning_period)
                )

    def step(self, rules: FightSetupRules) -> None:
        for ramp in self.ramps:
            ramp.counter = u16(ramp.counter - 1)
            if ramp.counter:
                continue
            ramp.counter = rules.warning_period
            color = step_color(self.colors[ramp.index], ramp.target)
            self.colors[ramp.index] = color
            if color == ramp.target:
                ramp.target, ramp.back = ramp.back, ramp.target


def settle_defeats(fight: Fight, opponent: int) -> int:
    """Restore defeated fighters' HP and decrement their life byte, returning the defeat mask."""
    mask = 0
    for bit, handle in enumerate((fight.first, opponent)):
        if handle and s16(fight.actor(handle).hp) <= 0:
            actor = fight.actor(handle)
            actor.hp, actor.lives = actor.hp_max, u8(actor.lives - 1)
            mask |= 1 << bit
    return mask
