"""Original pixel map, terrain and proximity (ARCHITECTURE.md §11.4-9/18)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING

from harddaysengine.sim.ai_map_state import MapAiCells, MapAiRules
from harddaysengine.sim.fight_moves import DOWN, FIRE, LEFT, RIGHT, UP
from harddaysengine.sim.inventory import Inventory
from harddaysengine.sim.m68k import s8, s16, u8, u16
from harddaysengine.sim.map_dragon import DragonFlight, attacks
from harddaysengine.sim.map_rules import MapColors, MapRules
from harddaysengine.sim.rewards import GiftState, recalc_endurance, recalc_hp
from harddaysengine.sim.turns import TurnCells
from harddaysengine.sim.types import KnightId

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from harddaysengine.sim.actor import Actor
    from harddaysengine.sim.rng import Rng
    from harddaysengine.sim.turn_rules import TurnRules

MOONSTONE_COUNT = 4


class MoonPhase(IntEnum):
    """8-phase breakdown of the 28-day lunar cycle."""

    NEW_MOON = 0
    WAXING_CRESCENT = 1
    FIRST_QUARTER = 2
    WAXING_GIBBOUS = 3
    FULL_MOON = 4
    WANING_GIBBOUS = 5
    LAST_QUARTER = 6
    WANING_CRESCENT = 7


class TriggerKind(IntEnum):
    """Overworld encounter and ritual trigger outcomes."""

    NONE = 0
    WANDERING_MONSTER = 1
    LAIR_BOSS = 2
    DRAGON_INTERCEPT = 3
    PVP_DUEL = 4
    TOWN_VISIT = 5
    STONEHENGE_RITUAL = 6
    VICTORY = 7
    HOME_CASTLE = 8
    VALLEY_OF_THE_GODS = 9
    MATHS_TOWER = 10


@dataclass(slots=True, frozen=True)
class EncounterTrigger:
    """Event emitted when a knight or the Dragon triggers an overworld encounter."""

    kind: TriggerKind
    knight_id: KnightId
    node_id: int
    day: int
    moon_phase: MoonPhase
    opponent_knight_id: KnightId | None = None
    lair_index: int = -1
    creature_type: int = 0
    encounter_kind: int = 0


@dataclass(slots=True)
class Lair:
    """Mutable original lair fields with linked inventory (§9.5)."""

    handler: int
    creatures_left: int
    map_x: int
    map_y: int
    region: int
    arena: str
    gold: int = 0
    loot: Inventory = field(default_factory=Inventory)

    def tidy(self) -> None:
        """Hide only after empty loot settlement (§11.9)."""
        if self.gold == 0 and not any(self.loot.counts):
            self.map_x = self.map_y = 65535


@dataclass(slots=True)
class KnightView:
    """Legacy sheet resource view over the authoritative records (§11.18)."""

    actor: Actor
    inventory: Inventory

    @property
    def lives(self) -> int:
        return s8(self.actor.lives)

    @lives.setter
    def lives(self, value: int) -> None:
        self.actor.lives = u8(value)

    @property
    def alive(self) -> bool:
        return self.lives > 0

    @property
    def gold(self) -> int:
        return s16(self.actor.gold)

    @gold.setter
    def gold(self, value: int) -> None:
        self.actor.gold = u16(value)

    @property
    def xp(self) -> int:
        return self.actor.progress

    @xp.setter
    def xp(self, value: int) -> None:
        self.actor.progress = u16(value)

    @property
    def keys_mask(self) -> int:
        return self.inventory.byte(20)

    @keys_mask.setter
    def keys_mask(self, value: int) -> None:
        self.inventory.counts[10] = u8(value)

    @property
    def moonstones_mask(self) -> int:
        return self.inventory.byte(22)

    @moonstones_mask.setter
    def moonstones_mask(self, value: int) -> None:
        self.inventory.counts[11] = u8(value)

    @property
    def moonstone_count(self) -> int:
        return self.moonstones_mask.bit_count()

    @property
    def has_all_moonstones(self) -> bool:
        return self.moonstones_mask & 15 == 15

    def has_moonstone(self, index: int) -> bool:
        """Query one original moonstone mask bit (§9.4)."""
        return bool(self.moonstones_mask & (1 << index))

    def has_key(self, index: int) -> bool:
        """Legacy sheet query of the original mask (§9.4)."""
        return bool(self.keys_mask & (1 << index))


@dataclass(slots=True, frozen=True)
class Arrival:
    """Raw key and kind of one proximity row (§11.7)."""

    key: int = 0
    kind: int = 0


@dataclass(slots=True, frozen=True)
class MapSprite:
    """Exact mi.c frame and top-left position (§11.15)."""

    frame: int
    x: int
    y: int


@dataclass(slots=True)
class OverworldState:
    """Original map cells shared with persistent fight records (§11.1)."""

    actors: Sequence[Actor]
    dragon: Actor
    inventories: Mapping[int, Inventory]
    prng: Rng
    turn_order: tuple[KnightId, ...]
    rules: MapRules | None = None
    turn_rules: TurnRules | None = None
    ai_rules: MapAiRules | None = None
    ai: MapAiCells = field(default_factory=MapAiCells)
    human_order: tuple[KnightId, ...] = field(init=False)
    turns: TurnCells = field(default_factory=TurnCells)
    gifts: GiftState = field(default_factory=GiftState)
    market: Inventory = field(default_factory=Inventory)
    knights: dict[KnightId, KnightView] = field(init=False)
    lairs: list[Lair] = field(default_factory=list)
    arrivals: list[Arrival] = field(default_factory=lambda: [Arrival() for _ in range(10)])
    highlights: list[MapSprite] = field(default_factory=list)
    dragon_touch: list[int] = field(default_factory=list)
    colors: MapColors = field(default_factory=MapColors)
    turn_index: int = 0
    spent: int = 0
    budget: int = 0
    move: int = 0
    terrain_counter: int = 0
    blocked: bool = False
    travel: bool = False
    ambush: bool = False
    saved_x: int = 0
    saved_y: int = 0
    region_code: int = 0
    phase: int = 0
    menu: bool = False
    turn_over: bool = False
    dragon_encounter: bool = False
    dragon_active: bool = False
    flight: DragonFlight = field(default_factory=DragonFlight)
    day: int = 0
    moon_index: int = 0
    moon_frame: int = 45
    moon_subcount: int = 0
    total_turns: int = 0
    claimed_moonstones_mask: int = 0
    winner: KnightId | None = None
    game_over: bool = False
    names: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if len(self.actors) != 4:
            raise ValueError("the map requires four knight records")
        self.human_order = self.turn_order
        self.turn_order = (
            *self.human_order,
            *(kid for kid in KnightId if kid not in self.human_order),
        )
        self.knights = {
            kid: KnightView(self.actors[int(kid)], self.inventories[int(kid) + 1])
            for kid in KnightId
        }

    @property
    def current_knight_id(self) -> KnightId:
        """Record identity whose turn is active (§11.1)."""
        return self.turn_order[self.turn_index]

    @property
    def current_actor(self) -> Actor:
        """Persistent actor of the current knight (§9.2)."""
        return self.actors[int(self.current_knight_id)]

    @property
    def moon_phase(self) -> MoonPhase:
        """Legacy sheet label of the eight-step moon index (§11.12)."""
        return MoonPhase(self.moon_index)

    @property
    def is_full_moon(self) -> bool:
        return self.moon_phase == MoonPhase.FULL_MOON

    def ordered_actors(self) -> tuple[Actor, ...]:
        """Records in original selection/scheduler order (§11.19)."""
        return tuple(self.actors[int(kid)] for kid in self.turn_order)

    def data(self) -> MapRules:
        """Require decoded data instead of inventing a map (§11.18)."""
        if self.rules is None:
            raise ValueError("original disks are required for the map")
        return self.rules

    def install(self, rules: MapRules, kinds: Mapping[KnightId, int], seed: int) -> None:
        """Allocate decoded starting records; gifts belong to inventory rules (§11.18)."""
        self.rules = rules
        self.prng.state = rules.seeds[seed & 3]
        self.lairs = [
            Lair(lair.handler, lair.count, u16(lair.x), u16(lair.y), lair.region, lair.arena)
            for lair in rules.lairs
        ]
        self.names.clear()
        for kid in KnightId:
            actor = self.actors[int(kid)]
            for offset, size, value in rules.defaults:
                actor.write(offset, size, value)
            actor.inventory = int(kid) + 1
            human = kid in self.human_order
            actor.kind = kinds[kid] if human else 4
            actor.type, actor.input_port = (12, 2) if human else (16, 4)
            actor.map_x, actor.map_y = (
                rules.human_positions[actor.kind]
                if human
                else rules.computer_positions[self.turn_order.index(kid)]
            )
            self.names.append(
                rules.human_names[actor.kind]
                if human
                else rules.computer_names[self.turn_order.index(kid)]
            )
            recalc_endurance(actor)
            recalc_hp(actor, self.inventories[actor.inventory])
        self.enter()

    def tile(self) -> int:
        """Wrapped cell under the sprite's bottom centre (§11.5)."""
        actor = self.current_actor
        width, height = self.data().size(0)
        actor.tile_x = u16(actor.map_x + (width >> 1)) >> 3
        actor.tile_y = u16(actor.map_y + height) >> 3
        return u16(actor.tile_y * 40 + actor.tile_x)

    def grid_byte(self, table: bytes, suffix: bytes = b"") -> int:
        """Signed indexing into the grid and its bounded executable suffix (§11.21)."""
        index = s16(self.tile())
        if len(table) <= index < len(table) + len(suffix):
            return suffix[index - len(table)]
        if not 0 <= index < len(table):
            raise ValueError(f"map tile {index} outside loaded grid")
        return table[index]

    def setup_turn(self) -> None:
        """Recompute budget and region without moving (§11.11)."""
        actor = self.current_actor
        actor.input_port = 2
        if actor.kind != 4:
            actor.type = 12
        self.budget = u16(actor.derived_endurance << 4)
        self.region_code = self.grid_byte(self.data().regions, self.data().region_tail)

    def enter(self) -> None:
        """Recompute turn cells and apply stale move without charging (§11.4)."""
        self.setup_turn()
        self.menu = False
        self.colors.active = False
        self.move_step()

    def terrain(self) -> None:
        """Persistent terrain counter, tested before input sampling (§11.6)."""
        self.blocked = False
        if self.travel or self.ambush:
            return
        mask = self.grid_byte(self.data().terrain, self.data().terrain_tail)
        if mask:
            self.terrain_counter = u16(self.terrain_counter + 1)
            self.blocked = bool(self.terrain_counter & u16(s8(mask)))

    def move_step(self) -> None:
        """Clip, move one pixel, update tile and proximity (§11.5)."""
        actor = self.current_actor
        self.move = clip_move(self.move, actor.map_x, actor.map_y)
        actor.map_x = u16(actor.map_x + int(bool(self.move & RIGHT)) - int(bool(self.move & LEFT)))
        actor.map_y = u16(actor.map_y + int(bool(self.move & DOWN)) - int(bool(self.move & UP)))
        self.tile()
        scan(self)

    def frame(self, word: int, key: int = 0) -> Arrival | None:
        """One human map frame; selection returns a row, never a random encounter (§11.4)."""
        if self.menu:
            return select_row(self, key)
        if self.turn_over:
            return None
        self.dragon_encounter = False
        self.sample(word, key)
        if key in (ord(" "), ord("Q")):
            return None
        if self.move & FIRE:
            row = self.fire()
            if row is not None or self.menu:
                return row
        if attacks(self):
            self.dragon_encounter = True
            return None
        if s16(self.spent) >= s16(self.budget):
            self.turn_over = True
            return None
        self.move_step()
        return None

    def sample(self, word: int, key: int) -> None:
        """Sample after terrain and charge before key dispatch (§11.4)."""
        self.terrain()
        self.move = 0 if self.blocked else word
        if not (self.travel or self.ambush) and self.move & 15:
            self.spent = u16(self.spent + 1)
        if key == ord("E"):
            self.spent = self.budget

    def start_mode(self, *, ambush: bool) -> None:
        """Save the position before a map item enables its mode (§11.9)."""
        self.saved_x, self.saved_y = self.current_actor.map_x, self.current_actor.map_y
        if ambush:
            self.ambush = True
        else:
            self.travel = True

    def restore_position(self) -> None:
        """Return from travel inspection and clear both flags (§11.9)."""
        self.current_actor.map_x, self.current_actor.map_y = self.saved_x, self.saved_y
        self.travel = self.ambush = False

    def random_spot(self) -> None:
        """Failed teleport draws x before y from the shared generator (§11.9)."""
        self.current_actor.map_x = (self.prng.draw() & 255) + 32
        self.current_actor.map_y = (self.prng.draw() & 127) + 36

    def fire(self) -> Arrival | None:
        """Clear ambush before testing the arrival list (§11.8)."""
        if self.ambush:
            self.travel = self.ambush = False
        return open_menu(self)


def clip_move(word: int, x: int, y: int) -> int:
    """Signed edge tests before stepping (§11.5)."""
    for blocked, bit in (
        (s16(x) <= 0, LEFT),
        (s16(x) >= 310, RIGHT),
        (s16(y) <= 0, UP),
        (s16(y) >= 190, DOWN),
    ):
        if blocked:
            word &= ~bit
    return word


def _interval(a: int, width: int, b: int, other_width: int) -> bool:
    end, other_end = u16(a + width), u16(b + other_width)
    if b < a:
        return other_end >= a
    if b < end:
        return True
    return other_end >= a and other_end < end


def boxes_touch(rules: MapRules, frame: int, ax: int, ay: int, bx: int, by: int) -> bool:
    """Original asymmetric interval test on wrapped word endpoints (§11.7)."""
    width, height = rules.size(frame)
    bw, bh = rules.size(0)
    return _interval(u16(ax), width, u16(bx), bw) and _interval(u16(ay), height, u16(by), bh)


def _highlight(actor: Actor) -> MapSprite:
    return MapSprite(actor.kind + 5 if s8(actor.lives) > 0 else 42, actor.map_x, actor.map_y)


def _scan_places(world: OverworldState) -> list[Arrival]:
    rules, actor = world.data(), world.current_actor
    rows = []
    for node in rules.nodes:
        if 21 <= node.kind <= 24 and actor.kind != node.kind - 21:
            continue
        if boxes_touch(rules, node.kind, node.x, node.y, actor.map_x, actor.map_y):
            rows.append(Arrival(node.x << 16 | node.y, node.kind))
            world.highlights.append(MapSprite(node.kind, node.x, node.y))
    return rows


def _scan_knights(world: OverworldState) -> list[Arrival]:
    rules, current = world.data(), world.current_actor
    rows = []
    for kid in world.turn_order:
        index, actor = int(kid), world.actors[int(kid)]
        if actor is not current and boxes_touch(
            rules, 0, current.map_x, current.map_y, actor.map_x, actor.map_y
        ):
            rows.append(Arrival(index + 1, 1 if s8(actor.lives) > 0 else 33))
            world.highlights.append(_highlight(actor))
    world.dragon_touch.clear()
    if world.dragon_active:
        for kid in world.turn_order:
            index, actor = int(kid), world.actors[int(kid)]
            if boxes_touch(
                rules, 20, u16(world.dragon.x - 10), world.dragon.y, actor.map_x, actor.map_y
            ):
                world.dragon_touch.append(index + 1)
                world.highlights.append(_highlight(actor))
    return rows


def scan(world: OverworldState) -> None:
    """Clear five rows, append places/knights/lairs in order, retain trailing rows (§11.7)."""
    world.arrivals[:5] = [Arrival() for _ in range(5)]
    world.highlights.clear()
    rows = [] if world.travel else [*_scan_places(world), *_scan_knights(world)]
    actor, rules = world.current_actor, world.data()
    for index, lair in enumerate(world.lairs):
        if s16(lair.map_x) >= 0 and boxes_touch(
            rules, 31, lair.map_x, lair.map_y, actor.map_x, actor.map_y
        ):
            rows.append(Arrival(index + 6, 2))
            world.highlights.append(MapSprite(31, lair.map_x, lair.map_y))
    if len(rows) >= len(world.arrivals):
        raise ValueError("map arrival list has no room for its end marker")
    world.arrivals[: len(rows)] = rows


def list_rows(world: OverworldState) -> tuple[Arrival, ...]:
    """Rows before the first zero key (§11.7)."""
    rows = []
    for row in world.arrivals:
        if row.key == 0:
            break
        rows.append(row)
    return tuple(rows)


def open_menu(world: OverworldState) -> Arrival | None:
    """Dispatch one row immediately; multiple rows wait for digits (§11.8)."""
    rows = list_rows(world)
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]
    if world.travel:
        for row in world.arrivals:
            if row.kind == 2:
                return row
            if row.key == 0:
                return None
        return None
    world.menu = True
    return None


def select_row(world: OverworldState, key: int) -> Arrival | None:
    """Accept existing rows1..9; the original has no cancel (§11.8)."""
    index = key - ord("1")
    if 0 <= index < 9 and world.arrivals[index].key:
        world.menu = False
        return world.arrivals[index]
    return None


def map_sprites(world: OverworldState) -> tuple[MapSprite, ...]:
    """Background lairs/other knights, current knight and highlights (§11.15)."""
    sprites = [
        MapSprite(20, lair.map_x, lair.map_y) for lair in world.lairs if s16(lair.map_x) >= 0
    ]
    for actor in world.ordered_actors():
        if actor is world.current_actor:
            continue
        frame = 33 if s8(actor.lives) <= 0 else actor.kind + (43 if actor.frog_days else 0)
        sprites.append(MapSprite(frame, actor.map_x, actor.map_y))
    actor = world.current_actor
    sprites.append(
        MapSprite(
            actor.kind + 5 + (5 if world.travel else 0) + (10 if world.ambush else 0),
            actor.map_x,
            actor.map_y,
        )
    )
    sprites.extend(world.highlights)
    return tuple(sprites)


def grant_lair_key(world: OverworldState, kid: KnightId, index: int) -> None:
    """Legacy sheet bridge to the original inventory mask (§9.4)."""
    world.knights[kid].keys_mask |= 1 << index


def collect_moonstone(world: OverworldState, kid: KnightId, index: int) -> bool:
    """Legacy sheet bridge to the original inventory mask (§9.4)."""
    bit = 1 << index
    if world.claimed_moonstones_mask & bit:
        return False
    world.knights[kid].moonstones_mask |= bit
    world.claimed_moonstones_mask |= bit
    return True
