"""The original map flight on the shared job engine (ARCHITECTURE.md §11.10/19)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

from harddaysengine.sim.fight import DRAGON, FightError, Restart, restart_from
from harddaysengine.sim.m68k import s8, s16, u8, u16
from harddaysengine.sim.rewards import transfer_loot

if TYPE_CHECKING:
    from harddaysengine.sim.actor import Actor
    from harddaysengine.sim.fight import Fight
    from harddaysengine.sim.overworld import OverworldState
    from harddaysengine.sim.turn_rules import MapDragonRules, TurnRules


@dataclass(slots=True)
class DragonFlight:
    timer: int = 0
    speed_x: int = 0
    speed_y: int = 0


def flight_step(actor: Actor, cells: DragonFlight, target_y: int, rules: MapDragonRules) -> int:
    """Move in record coordinates; hover omits all edge tests and phase advance (§11.10)."""
    cells.timer = u16(cells.timer - 1)
    if s16(cells.timer) < 0:
        cells.timer = rules.timer
    actor.x = u16(actor.x + cells.speed_x)
    if s16(cells.timer) <= rules.hover_limit:
        return rules.hover
    if actor.y != target_y:
        actor.y = u16(actor.y + (cells.speed_y if s16(target_y) > s16(actor.y) else -cells.speed_y))
    _clip(actor, cells, rules)
    actor.anim_phase = (actor.anim_phase + 1) & 15
    return rules.scripts[actor.anim_phase]


def _clip(actor: Actor, cells: DragonFlight, rules: MapDragonRules) -> None:
    if s16(actor.x) > s16(rules.right):
        actor.x = u16(rules.right_reset)
        actor.facing ^= 2
        cells.speed_x = s16(-cells.speed_x)
    if s16(actor.x) <= s16(rules.left):
        actor.x = u16(rules.left_reset)
        actor.facing ^= 2
        cells.speed_x = s16(-cells.speed_x)
    if s16(actor.y) > s16(rules.bottom):
        actor.y = u16(rules.top_reset)
    if s16(actor.y) < 0:
        actor.y = u16(rules.bottom)


def _handler(world: OverworldState, rules: MapDragonRules, fight: Fight, owner: int) -> Restart:
    actor = fight.actor(owner)
    target = actor.engaged_with
    target_y = fight.actor(target).map_y if target else 0
    script = flight_step(actor, world.flight, target_y, rules)
    return restart_from(actor, script)


def bind_flight(world: OverworldState, fight: Fight, rules: MapDragonRules) -> None:
    """Link the dragon job handler to this map's mutable flight cells (§11.19, §13.11)."""
    fight.handlers[40] = partial(_handler, world, rules)


def spawn(world: OverworldState, fight: Fight, rules: MapDragonRules) -> bool:
    """Spawn after the map-entry job pass, then sample living targets (§11.10/19)."""
    actor = world.dragon
    if world.dragon_active or s16(world.day) < s16(rules.day) or s8(actor.lives) < 0:
        return False
    for job in fight.engine.jobs:
        job.active = job.running = False
    for offset, size, value in rules.stores:
        actor.write(offset, size, value)
    actor.frame_list = 3
    bind_flight(world, fight, rules)
    if fight.start_job(rules.scripts[0], DRAGON, 3, rules.initial_job, actor.facing, 40) is None:
        raise FightError("no free map-dragon job")
    world.flight.timer, world.flight.speed_x, world.flight.speed_y = (
        rules.timer,
        rules.speed_x,
        rules.speed_y,
    )
    world.dragon_active = True
    actor.engaged_with = 0
    return _pick_target(world, fight)


def _pick_target(world: OverworldState, fight: Fight) -> bool:
    actor = world.dragon
    if not any(s8(a.lives) > 0 for a in world.actors):
        world.dragon_active = False
        fight.kill_job(DRAGON)
        return False
    for _ in range(4096):
        kid = world.turn_order[world.prng.draw() & 3]
        if s8(world.actors[int(kid)].lives) > 0:
            actor.engaged_with = int(kid) + 1
            return True
    raise ValueError("dragon target selection did not terminate")


def attacks(world: OverworldState) -> bool:
    """Test the previous proximity scan before this frame moves anything (§11.10)."""
    handle = int(world.current_knight_id) + 1
    return (
        s8(world.dragon.lives) >= 0
        and world.dragon_active
        and world.dragon.engaged_with == handle
        and handle in world.dragon_touch
    )


def settle(world: OverworldState, rules: TurnRules, *, lost: bool, spend: bool = True) -> None:
    """Loot on loss, slain marker/progress on win, exhaust budget either way (§11.10)."""
    actor = world.current_actor
    if lost:
        transfer_loot(world.dragon, actor, world.ordered_actors(), world.inventories, rules.gifts)
    else:
        world.dragon.lives = 255
        actor.progress = u16(actor.progress + rules.dragon.progress)
    if spend:
        world.spent = world.budget


def automatic_loss(world: OverworldState, rules: TurnRules) -> bool:
    """Computer loses one life; frogs/zero-lives humans lose without fighting (§11.19)."""
    actor = world.current_actor
    if actor.kind == 4:
        actor.lives = u8(actor.lives - 1)
    elif not actor.frog_days and actor.lives:
        return False
    settle(world, rules, lost=True)
    return True
