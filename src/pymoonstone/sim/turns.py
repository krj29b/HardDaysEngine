"""Original four-record turn scheduler and calendar (ARCHITECTURE.md §11.11/12/19)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING

from pymoonstone.sim.m68k import s8, s16, u8, u16
from pymoonstone.sim.rewards import wizard_gift

if TYPE_CHECKING:
    from pymoonstone.sim.actor import Actor
    from pymoonstone.sim.overworld import OverworldState
    from pymoonstone.sim.turn_rules import TurnRules


class TurnResult(IntEnum):
    PLAY = 0
    REPEAT = 1
    NEXT_DAY = 2
    GAME_OVER = 3


@dataclass(slots=True)
class TurnCells:
    skipped: int = 0
    running: bool = False
    pending_setup: bool = False
    wait_stage: int = 0

    def wait_fire(self, held: bool) -> bool:
        """Require release, press, release at the round screen (§11.19)."""
        if self.wait_stage == 0 and not held:
            self.wait_stage = 1
        elif self.wait_stage == 1 and held:
            self.wait_stage = 2
        elif self.wait_stage == 2 and not held:
            return True
        return False


def overnight(actor: Actor) -> None:
    """Wrapped curse decay and regeneration, including above-max HP (§11.19)."""
    if actor.recency != 255:
        value = u8(actor.recency - 10)
        actor.recency = 0 if s8(value) < 0 else value
        if actor.frog_days:
            actor.frog_days = u8(actor.frog_days - 1)
    difference = u16(actor.hp_max - actor.hp)
    if difference:
        actor.hp = u16(actor.hp + ((difference >> 2) | 1))
    if s16(actor.hp) >= s16(actor.hp_max):
        actor.hp = actor.hp_max


def clock(world: OverworldState, rules: TurnRules) -> None:
    """Gift/life-loss turnover every fourth round, then upkeep on all five (§11.12)."""
    world.moon_subcount = u16(world.moon_subcount + 1)
    if s16(world.moon_subcount) > 3:
        world.moon_subcount = 0
        world.day = u16(world.day + 1)
        world.moon_index = (world.moon_index + 1) & 7
        day_turnover(world, rules)
        world.moon_frame = rules.moons[world.moon_index]
    for actor in (*world.ordered_actors(), world.dragon):
        overnight(actor)


def day_turnover(world: OverworldState, rules: TurnRules) -> None:
    """Visit record order; computer gift precedes its pending life loss (§11.12)."""
    for actor in world.ordered_actors():
        if actor.kind == 4:
            actor.recency = 255
            if s8(actor.lives) > 0:
                if s16(actor.gold) < 0:
                    actor.gold = 0
                wizard_gift(
                    actor, world.inventories[actor.inventory], world.prng, rules.gifts, world.gifts
                )
        if actor.life_loss:
            actor.lives = u8(actor.lives - 1)


def _classify(world: OverworldState) -> TurnResult:
    world.setup_turn()
    actor = world.current_actor
    actor.engaged_with = 0
    if s8(actor.frog_days) > 0:
        return TurnResult.REPEAT
    if s8(actor.lives) > 0:
        world.turns.running = False
        return TurnResult.PLAY
    if actor.kind != 4:
        world.turns.skipped = u16(world.turns.skipped + 1)
        if world.turns.skipped == len(world.human_order):
            world.game_over = True
            world.turns.running = False
            return TurnResult.GAME_OVER
    return TurnResult.REPEAT


def advance(world: OverworldState, rules: TurnRules) -> TurnResult:
    """Continue a scheduler invocation, yielding at every round screen (§11.19)."""
    cells = world.turns
    if not cells.running:
        cells.running, cells.skipped = True, 0
    if cells.pending_setup:
        cells.pending_setup = False
        result = _classify(world)
        if result != TurnResult.REPEAT:
            return result
    for _ in range(4):
        world.travel = world.ambush = False
        world.turn_index = (world.turn_index + 1) & 3
        world.total_turns += 1
        world.spent = 0
        world.turn_over = False
        if world.turn_index == 0:
            clock(world, rules)
            cells.pending_setup, cells.wait_stage = True, 0
            return TurnResult.NEXT_DAY
        result = _classify(world)
        if result != TurnResult.REPEAT:
            return result
    raise ValueError("turn scheduler passed a round boundary without yielding")
