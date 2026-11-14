"""Pure-integer Overworld board-game loop (ARCHITECTURE.md §5, §7; IMPLEMENTATION_PLAN T7.1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

from pymoonstone.sim.prng import DEFAULT_SEED, SimPrng
from pymoonstone.sim.types import KnightId

DAYS_PER_LUNAR_CYCLE: int = 28
MOONSTONE_COUNT: int = 4
ALL_MOONSTONES_MASK: int = 0x0F
ALL_KEYS_MASK: int = 0x0F
DEFAULT_KNIGHT_LIVES: int = 3
DEFAULT_ENCOUNTER_ROLL_MAX: int = 100


class RegionId(IntEnum):
    """Overworld regions matching the 4 arena/lair zones plus central Valley."""

    FOREST = 0
    GLEN = 1
    SWAMP = 2
    WASTELAND = 3
    VALLEY = 4


class NodeKind(IntEnum):
    """Overworld map node classification."""

    WILDERNESS = 0
    LAIR = 1
    HIGHWOOD = 2
    WATERDEEP = 3
    STONEHENGE = 4
    START = 5


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


@dataclass(slots=True, frozen=True)
class OverworldNode:
    """Pure-integer overworld map node (decoupled from ``assets.tables``)."""

    node_id: int
    region_id: int
    kind: NodeKind
    x: int
    y: int
    neighbors: tuple[int, ...]
    lair_index: int = -1
    creature_type: int = 0
    defence: int = 0
    arena_script: str = ""


@dataclass(slots=True, frozen=True)
class EncounterBand:
    """Random 0..99 encounter threshold and monster kind."""

    limit: int
    kind: int


@dataclass(slots=True, frozen=True)
class OverworldGraph:
    """Immutable region graph, patrol route, and encounter bands."""

    nodes: dict[int, OverworldNode]
    stonehenge_node_id: int
    start_nodes: dict[KnightId, int]
    dragon_patrol_route: tuple[int, ...]
    encounter_bands: tuple[EncounterBand, ...] = ()

    def node(self, node_id: int) -> OverworldNode:
        """Return the node for ``node_id`` or raise ``KeyError``."""
        return self.nodes[node_id]

    def neighbors(self, node_id: int) -> tuple[int, ...]:
        """Return adjacent node IDs reachable in one move from ``node_id``."""
        return self.nodes[node_id].neighbors


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


@dataclass(slots=True, frozen=True)
class TurnResult:
    """Summary of one knight movement turn on the overworld board."""

    knight_id: KnightId
    from_node_id: int
    to_node_id: int
    day_advanced: bool
    new_day: int
    moon_phase: MoonPhase
    dragon_node_id: int
    triggers: tuple[EncounterTrigger, ...]


@dataclass(slots=True)
class KnightOverworldState:
    """Per-knight campaign state on the overworld map."""

    knight_id: KnightId
    node_id: int
    keys_mask: int = 0
    moonstones_mask: int = 0
    lives: int = DEFAULT_KNIGHT_LIVES
    gold: int = 0
    xp: int = 0
    turns_taken: int = 0
    alive: bool = True

    @property
    def moonstone_count(self) -> int:
        """Number of Moonstones currently carried by this knight."""
        return self.moonstones_mask.bit_count()

    @property
    def key_count(self) -> int:
        """Number of Lair keys currently carried by this knight."""
        return self.keys_mask.bit_count()

    @property
    def has_all_moonstones(self) -> bool:
        """True when this knight carries all 4 Moonstones."""
        return (self.moonstones_mask & ALL_MOONSTONES_MASK) == ALL_MOONSTONES_MASK

    def has_key(self, lair_index: int) -> bool:
        """True when this knight holds the key for ``lair_index`` (0..3)."""
        _validate_lair_index(lair_index)
        return bool(self.keys_mask & (1 << lair_index))

    def has_moonstone(self, lair_index: int) -> bool:
        """True when this knight carries the Moonstone from ``lair_index`` (0..3)."""
        _validate_lair_index(lair_index)
        return bool(self.moonstones_mask & (1 << lair_index))


@dataclass(slots=True)
class DragonState:
    """Patrolling Dragon state on the overworld map."""

    node_id: int
    patrol_index: int = 0
    active: bool = True


@dataclass(slots=True)
class OverworldState:
    """Complete deterministic overworld board-game state."""

    graph: OverworldGraph
    prng: SimPrng
    turn_order: tuple[KnightId, ...]
    knights: dict[KnightId, KnightOverworldState]
    dragon: DragonState
    turn_index: int = 0
    total_turns: int = 0
    day: int = 1
    claimed_moonstones_mask: int = 0
    winner: KnightId | None = None
    game_over: bool = False
    last_triggers: tuple[EncounterTrigger, ...] = field(default=())

    @property
    def current_knight_id(self) -> KnightId:
        """Active knight whose turn is next."""
        return self.turn_order[self.turn_index]

    @property
    def current_knight(self) -> KnightOverworldState:
        """State record for the currently active knight."""
        return self.knights[self.current_knight_id]

    @property
    def day_of_cycle(self) -> int:
        """1-based day within the 28-day lunar cycle (``1..28``)."""
        return ((self.day - 1) % DAYS_PER_LUNAR_CYCLE) + 1

    @property
    def moon_phase(self) -> MoonPhase:
        """Current lunar phase derived from ``day``."""
        return moon_phase_for_day(self.day)

    @property
    def is_full_moon(self) -> bool:
        """True during the full-moon window of the 28-day cycle."""
        return self.moon_phase == MoonPhase.FULL_MOON

    @property
    def is_new_moon(self) -> bool:
        """True during the new-moon window of the 28-day cycle."""
        return self.moon_phase == MoonPhase.NEW_MOON


def _validate_lair_index(lair_index: int) -> None:
    if not 0 <= lair_index < MOONSTONE_COUNT:
        msg = f"lair_index must be in 0..3, got {lair_index}"
        raise ValueError(msg)


def moon_phase_for_day(day: int) -> MoonPhase:
    """Map a 1-based campaign day to one of the 8 phases in a 28-day lunar cycle."""
    if day < 1:
        msg = f"day must be >= 1, got {day}"
        raise ValueError(msg)
    cycle_day = (day - 1) % DAYS_PER_LUNAR_CYCLE
    # Partition 0..27 across 8 phases using integer arithmetic only:
    # day 1 (0) -> NEW_MOON; day 15 (14) -> FULL_MOON.
    phase_index = (cycle_day * 8) // DAYS_PER_LUNAR_CYCLE
    return MoonPhase(phase_index)


def moon_encounter_modifier(phase: MoonPhase) -> int:
    """Integer encounter-roll adjustment for lunar phase (full moon +, new moon -)."""
    if phase == MoonPhase.FULL_MOON:
        return 15
    if phase == MoonPhase.NEW_MOON:
        return -10
    return 0


def build_default_overworld_graph() -> OverworldGraph:
    """Construct a standard 4-region overworld graph with Lairs, towns, and Stonehenge."""
    nodes = {
        # Central Valley: Stonehenge (0), Highwood (1), Waterdeep (2)
        0: OverworldNode(0, RegionId.VALLEY, NodeKind.STONEHENGE, 160, 100, (1, 2, 10, 20)),
        1: OverworldNode(1, RegionId.VALLEY, NodeKind.HIGHWOOD, 110, 90, (0, 10, 30)),
        2: OverworldNode(2, RegionId.VALLEY, NodeKind.WATERDEEP, 210, 110, (0, 20, 40)),
        # Region 0: Forest (10 start/wilderness, 11 wilderness, 12 lair 0)
        10: OverworldNode(10, RegionId.FOREST, NodeKind.START, 60, 50, (0, 1, 11)),
        11: OverworldNode(11, RegionId.FOREST, NodeKind.WILDERNESS, 40, 30, (10, 12, 21)),
        12: OverworldNode(
            12,
            RegionId.FOREST,
            NodeKind.LAIR,
            25,
            20,
            (11,),
            lair_index=0,
            creature_type=1,
            defence=3,
            arena_script="fol1",
        ),
        # Region 1: Glen (20 start/wilderness, 21 wilderness, 22 lair 1)
        20: OverworldNode(20, RegionId.GLEN, NodeKind.START, 260, 50, (0, 2, 21)),
        21: OverworldNode(21, RegionId.GLEN, NodeKind.WILDERNESS, 280, 30, (20, 22, 11)),
        22: OverworldNode(
            22,
            RegionId.GLEN,
            NodeKind.LAIR,
            300,
            20,
            (21,),
            lair_index=1,
            creature_type=2,
            defence=4,
            arena_script="gll1",
        ),
        # Region 2: Swamp (30 start/wilderness, 31 wilderness, 32 lair 2)
        30: OverworldNode(30, RegionId.SWAMP, NodeKind.START, 60, 160, (1, 31)),
        31: OverworldNode(31, RegionId.SWAMP, NodeKind.WILDERNESS, 40, 180, (30, 32, 41)),
        32: OverworldNode(
            32,
            RegionId.SWAMP,
            NodeKind.LAIR,
            25,
            190,
            (31,),
            lair_index=2,
            creature_type=3,
            defence=5,
            arena_script="swl1",
        ),
        # Region 3: Wasteland (40 start/wilderness, 41 wilderness, 42 lair 3)
        40: OverworldNode(40, RegionId.WASTELAND, NodeKind.START, 260, 160, (2, 41)),
        41: OverworldNode(41, RegionId.WASTELAND, NodeKind.WILDERNESS, 280, 180, (40, 42, 31)),
        42: OverworldNode(
            42,
            RegionId.WASTELAND,
            NodeKind.LAIR,
            300,
            190,
            (41,),
            lair_index=3,
            creature_type=4,
            defence=6,
            arena_script="wal1",
        ),
    }
    start_nodes = {
        KnightId.RED: 10,
        KnightId.BLUE: 20,
        KnightId.GREEN: 30,
        KnightId.YELLOW: 40,
    }
    patrol_route = (11, 21, 41, 31)
    bands = (
        EncounterBand(limit=35, kind=1),
        EncounterBand(limit=60, kind=2),
    )
    return OverworldGraph(
        nodes=nodes,
        stonehenge_node_id=0,
        start_nodes=start_nodes,
        dragon_patrol_route=patrol_route,
        encounter_bands=bands,
    )


def create_campaign(
    knights: tuple[KnightId, ...] | list[KnightId],
    graph: OverworldGraph | None = None,
    *,
    seed: int = DEFAULT_SEED,
) -> OverworldState:
    """Initialize a 1-4 knight overworld campaign from a deterministic seed."""
    order = tuple(knights)
    if not 1 <= len(order) <= 4:
        msg = f"Campaign requires 1..4 knights, got {len(order)}"
        raise ValueError(msg)
    if len(set(order)) != len(order):
        msg = "Duplicate KnightId in turn order"
        raise ValueError(msg)

    active_graph = graph if graph is not None else build_default_overworld_graph()
    knight_states: dict[KnightId, KnightOverworldState] = {}
    for kid in order:
        start_id = active_graph.start_nodes.get(kid, active_graph.stonehenge_node_id)
        if start_id not in active_graph.nodes:
            msg = f"Start node {start_id} for {kid.name} not in graph"
            raise ValueError(msg)
        knight_states[kid] = KnightOverworldState(knight_id=kid, node_id=start_id)

    route = active_graph.dragon_patrol_route
    dragon_start = route[0] if route else active_graph.stonehenge_node_id
    return OverworldState(
        graph=active_graph,
        prng=SimPrng(seed),
        turn_order=order,
        knights=knight_states,
        dragon=DragonState(node_id=dragon_start, patrol_index=0, active=True),
    )


def legal_moves(state: OverworldState, knight_id: KnightId | None = None) -> tuple[int, ...]:
    """Return legal destination node IDs for ``knight_id`` (defaults to active knight)."""
    if state.game_over:
        return ()
    target = state.current_knight_id if knight_id is None else knight_id
    knight = state.knights[target]
    if not knight.alive:
        return ()
    return state.graph.neighbors(knight.node_id)


def can_enter_lair(state: OverworldState, knight_id: KnightId, lair_index: int) -> bool:
    """True if ``knight_id`` holds the key for ``lair_index`` and the Moonstone is unclaimed."""
    _validate_lair_index(lair_index)
    knight = state.knights[knight_id]
    moonstone_unclaimed = not bool(state.claimed_moonstones_mask & (1 << lair_index))
    return knight.alive and knight.has_key(lair_index) and moonstone_unclaimed


def grant_lair_key(state: OverworldState, knight_id: KnightId, lair_index: int) -> None:
    """Grant the key for ``lair_index`` (0..3) to ``knight_id``."""
    _validate_lair_index(lair_index)
    state.knights[knight_id].keys_mask |= 1 << lair_index


def collect_moonstone(state: OverworldState, knight_id: KnightId, lair_index: int) -> None:
    """Assign Moonstone ``lair_index`` (0..3) to ``knight_id`` and mark it claimed."""
    _validate_lair_index(lair_index)
    bit = 1 << lair_index
    for kstate in state.knights.values():
        kstate.moonstones_mask &= ~bit
    state.knights[knight_id].moonstones_mask |= bit
    state.claimed_moonstones_mask |= bit


def is_ritual_ready(state: OverworldState, knight_id: KnightId) -> bool:
    """True when ``knight_id`` carries all 4 Moonstones and stands at Stonehenge."""
    knight = state.knights[knight_id]
    at_stonehenge = knight.node_id == state.graph.stonehenge_node_id
    return knight.alive and knight.has_all_moonstones and at_stonehenge


def step_dragon_patrol(state: OverworldState) -> int:
    """Advance the patrolling Dragon by one step and return its new node ID."""
    if not state.dragon.active:
        return state.dragon.node_id
    route = state.graph.dragon_patrol_route
    if route:
        next_idx = (state.dragon.patrol_index + 1) % len(route)
        state.dragon.patrol_index = next_idx
        state.dragon.node_id = route[next_idx]
        return state.dragon.node_id

    neighbors = state.graph.neighbors(state.dragon.node_id)
    if neighbors:
        choice = state.prng.rand_range(len(neighbors))
        state.dragon.node_id = neighbors[choice]
    return state.dragon.node_id


def _roll_wilderness_encounter(
    state: OverworldState, knight_id: KnightId, node: OverworldNode
) -> int:
    """Roll 0..99 against encounter bands with lunar modifier; return kind or 0."""
    bands = state.graph.encounter_bands
    if not bands:
        return 0
    raw_roll = state.prng.rand_range(DEFAULT_ENCOUNTER_ROLL_MAX)
    adjusted = max(0, raw_roll - moon_encounter_modifier(state.moon_phase))
    for band in bands:
        if adjusted < band.limit:
            return band.kind
    return 0


def _collect_pvp_and_dragon_triggers(
    state: OverworldState,
    knight_id: KnightId,
    node_id: int,
) -> list[EncounterTrigger]:
    """Collect PvP duel and Dragon intercept triggers at ``node_id``."""
    triggers: list[EncounterTrigger] = []
    phase = state.moon_phase
    for other_id in state.turn_order:
        if other_id == knight_id:
            continue
        other = state.knights[other_id]
        if other.alive and other.node_id == node_id:
            triggers.append(
                EncounterTrigger(
                    kind=TriggerKind.PVP_DUEL,
                    knight_id=knight_id,
                    node_id=node_id,
                    day=state.day,
                    moon_phase=phase,
                    opponent_knight_id=other_id,
                )
            )
    if state.dragon.active and state.dragon.node_id == node_id:
        triggers.append(
            EncounterTrigger(
                kind=TriggerKind.DRAGON_INTERCEPT,
                knight_id=knight_id,
                node_id=node_id,
                day=state.day,
                moon_phase=phase,
            )
        )
    return triggers


def _collect_location_trigger(
    state: OverworldState,
    knight_id: KnightId,
    node: OverworldNode,
) -> EncounterTrigger | None:
    """Evaluate the site/wilderness trigger for ``node``."""
    phase = state.moon_phase
    if node.kind == NodeKind.LAIR and node.lair_index >= 0:
        if can_enter_lair(state, knight_id, node.lair_index):
            return EncounterTrigger(
                kind=TriggerKind.LAIR_BOSS,
                knight_id=knight_id,
                node_id=node.node_id,
                day=state.day,
                moon_phase=phase,
                lair_index=node.lair_index,
                creature_type=node.creature_type,
            )
        return None
    if node.kind == NodeKind.STONEHENGE:
        if is_ritual_ready(state, knight_id):
            state.winner = knight_id
            state.game_over = True
            return EncounterTrigger(
                kind=TriggerKind.VICTORY,
                knight_id=knight_id,
                node_id=node.node_id,
                day=state.day,
                moon_phase=phase,
            )
        return EncounterTrigger(
            kind=TriggerKind.STONEHENGE_RITUAL,
            knight_id=knight_id,
            node_id=node.node_id,
            day=state.day,
            moon_phase=phase,
        )
    if node.kind in (NodeKind.HIGHWOOD, NodeKind.WATERDEEP):
        return EncounterTrigger(
            kind=TriggerKind.TOWN_VISIT,
            knight_id=knight_id,
            node_id=node.node_id,
            day=state.day,
            moon_phase=phase,
        )
    if node.kind == NodeKind.WILDERNESS:
        enc_kind = _roll_wilderness_encounter(state, knight_id, node)
        if enc_kind > 0:
            return EncounterTrigger(
                kind=TriggerKind.WANDERING_MONSTER,
                knight_id=knight_id,
                node_id=node.node_id,
                day=state.day,
                moon_phase=phase,
                encounter_kind=enc_kind,
            )
    return None


def _collect_node_triggers(
    state: OverworldState,
    knight_id: KnightId,
    node: OverworldNode,
) -> list[EncounterTrigger]:
    """Evaluate all overworld triggers upon ``knight_id`` arriving at ``node``."""
    triggers = _collect_pvp_and_dragon_triggers(state, knight_id, node.node_id)
    location_trigger = _collect_location_trigger(state, knight_id, node)
    if location_trigger is not None:
        triggers.append(location_trigger)
    return triggers


def _advance_turn_counter(state: OverworldState) -> bool:
    """Advance turn index to the next living knight; return True when a new day starts."""
    state.total_turns += 1
    if state.game_over:
        return False

    count = len(state.turn_order)
    prev_index = state.turn_index
    day_wrapped = False
    for step in range(1, count + 1):
        cand_index = (prev_index + step) % count
        if cand_index <= prev_index or step == count:
            day_wrapped = True
        cand_id = state.turn_order[cand_index]
        if state.knights[cand_id].alive:
            state.turn_index = cand_index
            break

    if day_wrapped:
        state.day += 1
    return day_wrapped


def move_knight(state: OverworldState, target_node_id: int) -> TurnResult:
    """Move the active knight to ``target_node_id``, advance patrol/day, and emit triggers."""
    if state.game_over:
        msg = "Cannot move knight after campaign victory"
        raise ValueError(msg)

    knight_id = state.current_knight_id
    knight = state.knights[knight_id]
    allowed = legal_moves(state, knight_id)
    if target_node_id not in allowed:
        msg = f"Node {target_node_id} is not reachable from {knight.node_id}"
        raise ValueError(msg)

    from_node_id = knight.node_id
    knight.node_id = target_node_id
    knight.turns_taken += 1

    dest_node = state.graph.node(target_node_id)
    triggers = _collect_node_triggers(state, knight_id, dest_node)

    dragon_node = state.dragon.node_id
    day_advanced = _advance_turn_counter(state)
    if day_advanced and not state.game_over:
        dragon_node = step_dragon_patrol(state)
        for kid in state.turn_order:
            kstate = state.knights[kid]
            if kstate.alive and kstate.node_id == dragon_node:
                triggers.append(
                    EncounterTrigger(
                        kind=TriggerKind.DRAGON_INTERCEPT,
                        knight_id=kid,
                        node_id=dragon_node,
                        day=state.day,
                        moon_phase=state.moon_phase,
                    )
                )

    trigger_tuple = tuple(triggers)
    state.last_triggers = trigger_tuple
    return TurnResult(
        knight_id=knight_id,
        from_node_id=from_node_id,
        to_node_id=target_node_id,
        day_advanced=day_advanced,
        new_day=state.day,
        moon_phase=state.moon_phase,
        dragon_node_id=dragon_node,
        triggers=trigger_tuple,
    )


def resolve_lair_victory(state: OverworldState, knight_id: KnightId, lair_index: int) -> bool:
    """Award the Moonstone from ``lair_index`` to ``knight_id`` if still unclaimed."""
    _validate_lair_index(lair_index)
    if state.claimed_moonstones_mask & (1 << lair_index):
        return False
    collect_moonstone(state, knight_id, lair_index)
    return True


def resolve_pvp_duel(state: OverworldState, winner_id: KnightId, loser_id: KnightId) -> None:
    """Transfer carried Moonstones from ``loser_id`` to ``winner_id`` and deduct a life."""
    if winner_id == loser_id:
        msg = "winner_id and loser_id must be distinct"
        raise ValueError(msg)
    winner = state.knights[winner_id]
    loser = state.knights[loser_id]

    winner.moonstones_mask |= loser.moonstones_mask
    loser.moonstones_mask = 0
    loser.lives = max(0, loser.lives - 1)
    if loser.lives == 0:
        loser.alive = False
    else:
        loser.node_id = state.graph.start_nodes.get(loser_id, state.graph.stonehenge_node_id)
