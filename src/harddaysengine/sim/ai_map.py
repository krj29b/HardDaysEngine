"""Computer map decisions and persistent walk lines (ARCHITECTURE.md §11.13/20)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harddaysengine.sim.actor import StatKind
from harddaysengine.sim.ai_shop import purchase, shop_wish
from harddaysengine.sim.fight_moves import DOWN, LEFT, RIGHT, UP
from harddaysengine.sim.m68k import s8, s16, u8, u16, u32
from harddaysengine.sim.map_dragon import attacks
from harddaysengine.sim.rewards import pick_stat, transfer_loot

if TYPE_CHECKING:
    from harddaysengine.sim.actor import Actor
    from harddaysengine.sim.ai_map_state import MapAiRules, WalkLine
    from harddaysengine.sim.overworld import Arrival, OverworldState


def distance(x: int, y: int, target_x: int, target_y: int) -> int:
    """Signed result of word subtraction, absolute values and sum (§11.20)."""
    return s16(u16(abs(s16(target_x - x)) + abs(s16(target_y - y))))


def build_roam(world: OverworldState) -> None:
    """Build once per turn, sorting lairs by (hidden, distance, index) (§11.13)."""
    cells, actor = world.ai, world.current_actor
    if cells.built:
        return
    if len(world.lairs) != 24:
        raise ValueError("computer roaming requires 24 original lairs")
    cells.built = True
    rows = [
        (
            65535
            if s16(lair.map_x) < 0
            else u16(distance(actor.map_x, actor.map_y, lair.map_x, lair.map_y)),
            i,
        )
        for i, lair in enumerate(world.lairs)
    ]
    cells.roam = sorted(rows, key=lambda row: (s16(world.lairs[row[1]].map_x) < 0, row[0], row[1]))


def pick_roam(world: OverworldState) -> None:
    """Always draw, even after the walk line has been initialized (§11.14)."""
    for _ in range(4096):
        row = world.prng.draw() & 3
        if row:
            world.ai.target = world.ai.roam[row][1]
            world.ai.hidden = s16(world.lairs[world.ai.target].map_x) < 0
            return
    raise ValueError("computer roam selection did not terminate")


def pick_opponent(world: OverworldState, rules: MapAiRules) -> None:
    """Stable neighbor ordering over the other knights (§11.13)."""
    actor, cells = world.current_actor, world.ai
    cells.neighbors = sorted(
        (
            (u32(distance(actor.map_x, actor.map_y, other.map_x, other.map_y)), other.inventory)
            for other in world.ordered_actors()
            if other is not actor
        ),
        key=lambda row: row[0],
    )
    if actor.engaged_with:
        return
    for _, handle in cells.neighbors:
        if world.actors[handle - 1].kind == 4:
            continue
        if cells.hidden or world.prng.draw() & 127 <= rules.engage:
            actor.engaged_with = handle
            return


def _stat_and_rest(world: OverworldState, rules: MapAiRules) -> None:
    actor = world.current_actor
    if world.turn_rules is None:
        raise ValueError("computer map decisions require gift tables")
    if s16(actor.progress) >= s16(rules.stat_price):
        offset = pick_stat(actor, world.prng, world.turn_rules.gifts)
        if offset:
            actor.add_stat(StatKind(offset), 1)
            actor.progress = u16(actor.progress - rules.stat_price)
    world.ai.gate = 0
    inventory = world.inventories[actor.inventory]
    needs_rest = s8(actor.lives) <= rules.rest_lives or s16(actor.hp) < actor.hp_max >> 2
    if needs_rest and inventory.byte(0):
        inventory.counts[0] = u8(inventory.byte(0) - 1)
        actor.life_loss = 0
        if actor.hp == actor.hp_max:
            actor.lives = u8(actor.lives + 1)
            if s8(actor.lives) >= rules.rest_cap_test:
                actor.lives = rules.rest_cap
        actor.hp = actor.hp_max


def _items(world: OverworldState) -> None:
    actor = world.current_actor
    if not actor.engaged_with:
        return
    opponent = world.actors[actor.engaged_with - 1]
    inventory = world.inventories[actor.inventory]
    if inventory.byte(14):
        inventory.counts[7] = u8(inventory.byte(14) - 1)
        if world.turn_rules is None:
            raise ValueError("acquisition requires transfer tables")
        transfer_loot(
            actor, opponent, world.ordered_actors(), world.inventories, world.turn_rules.gifts
        )
    threshold = s16(u16(s8(actor.derived_endurance) << 4))
    if (
        inventory.byte(10)
        and distance(actor.map_x, actor.map_y, opponent.map_x, opponent.map_y) >= threshold
    ):
        inventory.counts[5] = u8(inventory.byte(10) - 1)
        world.budget = u16(world.budget << 1)


def _town(world: OverworldState, rules: MapAiRules) -> None:
    cells, actor = world.ai, world.current_actor
    cells.town = None
    if shop_wish(actor, cells.wish, rules):
        distances = tuple(
            u16(distance(actor.tile_x, actor.tile_y, x, y)) for x, y, _, _ in rules.towns
        )
        cells.town_distances = distances[0], distances[1]
        first, second = cells.town_distances
        row = rules.towns[int(s16(second) < s16(first))]
        cells.town = row[2], row[3]


def _arrival_rows(world: OverworldState) -> list[Arrival]:
    rows: list[Arrival] = []
    for row in world.arrivals[:7]:
        if not row.key:
            break
        rows.append(row)
    return rows


def _arrival(world: OverworldState, rules: MapAiRules) -> Arrival | None:
    cells, actor = world.ai, world.current_actor
    rows = _arrival_rows(world)
    if cells.town is not None:
        if any(row.kind in (25, 26) for row in rows):
            world.spent = world.budget
            purchase(actor, world.inventories[actor.inventory], cells.wish, rules)
            cells.town = None
        return None
    if actor.engaged_with:
        match = next((row for row in rows if row.key == actor.engaged_with), None)
        if match is None:
            return None
        world.spent = world.budget
        return match
    if any(row.key == cells.target + 6 for row in rows):
        world.spent = world.budget
    return None


def _target(world: OverworldState) -> tuple[int, int]:
    actor = world.current_actor
    if actor.engaged_with:
        opponent = world.actors[actor.engaged_with - 1]
        return opponent.map_x, opponent.map_y
    if world.ai.town is not None:
        return world.ai.town
    lair = world.lairs[world.ai.target]
    return lair.map_x, lair.map_y


def walk_step(line: WalkLine, actor: Actor, target: tuple[int, int]) -> int:
    """Advance a word-wrapped line, retaining it beyond the target (§11.13)."""
    if not line.initialized:
        dx, dy = s16(target[0] - actor.map_x), s16(target[1] - actor.map_y)
        line.dx, line.dy = u16(abs(dx)), u16(abs(dy))
        line.x_bit, line.y_bit = (LEFT if dx < 0 else RIGHT), (UP if dy < 0 else DOWN)
        line.y_major = s16(line.dy) > s16(line.dx)
        line.error = line.dy if line.y_major else line.dx
        line.initialized = True
    major, minor = (line.dy, line.dx) if line.y_major else (line.dx, line.dy)
    word = line.y_bit if line.y_major else line.x_bit
    line.error = u16(line.error - minor)
    if s16(line.error) < 0:
        line.error = u16(line.error + major)
        word |= line.x_bit if line.y_major else line.y_bit
    return word


def _decide(world: OverworldState, rules: MapAiRules) -> Arrival | None:
    build_roam(world)
    pick_roam(world)
    _stat_and_rest(world, rules)
    if not world.ai.gate:
        pick_opponent(world, rules)
        _items(world)
        _town(world, rules)
    return _arrival(world, rules)


def _charge(world: OverworldState) -> None:
    world.terrain()
    world.spent = u16(world.spent + 1)
    world.move = (
        0 if world.blocked else walk_step(world.ai.walk, world.current_actor, _target(world))
    )


def _budget_tail(world: OverworldState) -> None:
    if s16(world.spent) >= s16(world.budget):
        world.turn_over = True
    else:
        world.move_step()


def computer_frame(world: OverworldState, rules: MapAiRules, key: int = 0) -> Arrival | None:
    """Decide, then run the common map tail; suspend for caller encounters (§11.20)."""
    if world.ai.after_dragon:
        world.ai.after_dragon = False
        world.dragon_encounter = False
        _budget_tail(world)
        return None
    if world.ai.pending:
        world.spent = world.budget
    else:
        row = _decide(world, rules)
        if row is not None:
            world.ai.pending = True
            return row
    world.ai.pending = False
    _charge(world)
    world.dragon_encounter = key != ord("Q") and attacks(world)
    world.ai.after_dragon = world.dragon_encounter
    if world.dragon_encounter or key == ord("Q"):
        return None
    _budget_tail(world)
    return None
