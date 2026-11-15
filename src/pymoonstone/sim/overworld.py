"""Pure-integer Overworld board-game loop (ARCHITECTURE.md §5, §7; IMPLEMENTATION_PLAN T7.1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING

from pymoonstone.sim.prng import DEFAULT_SEED, SimPrng
from pymoonstone.sim.types import KnightId

if TYPE_CHECKING:
    from collections.abc import Sequence

DAYS_PER_LUNAR_CYCLE: int = 28
MOONSTONE_COUNT: int = 4
ALL_MOONSTONES_MASK: int = 0x0F
ALL_KEYS_MASK: int = 0x0F
DEFAULT_KNIGHT_LIVES: int = 3
DEFAULT_ENCOUNTER_ROLL_MAX: int = 100
DEFAULT_ROAD_MOVES_PER_TURN: int = 4
MOG_ROAD_SITE_THRESHOLD: int = 20
VALLEY_BLESSING_XP: int = 15
MATHS_TOWER_XP: int = 20
MATHS_TOWER_GOLD: int = 25


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
    ROAD = 6
    VALLEY_OF_THE_GODS = 7
    MATHS_TOWER = 8


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
class MapSiteInput:
    """Pure-integer map site input matching ``MapSiteRecord`` coordinates (320x200)."""

    site_id: int
    x: int
    y: int
    kind: NodeKind | None = None
    region_id: int | None = None
    neighbors: tuple[int, ...] = ()
    lair_index: int = -1
    creature_type: int = 0
    defence: int = 0
    arena_script: str = ""


@dataclass(slots=True, frozen=True)
class OverworldGraph:
    """Immutable region graph, patrol route, and encounter bands."""

    nodes: dict[int, OverworldNode]
    stonehenge_node_id: int
    start_nodes: dict[KnightId, int]
    dragon_patrol_route: tuple[int, ...]
    encounter_bands: tuple[EncounterBand, ...] = ()
    moves_per_turn: int = 1

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
    """Summary of one knight movement step or turn on the overworld board."""

    knight_id: KnightId
    from_node_id: int
    to_node_id: int
    day_advanced: bool
    new_day: int
    moon_phase: MoonPhase
    dragon_node_id: int
    triggers: tuple[EncounterTrigger, ...]
    moves_remaining: int = 0
    turn_ended: bool = True


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
    """Patrolling and flying Dragon state on the overworld map."""

    node_id: int
    patrol_index: int = 0
    active: bool = True
    x: int = 10
    y: int = 100
    speed_x: int = 2
    speed_y: int = 1
    timer: int = 100
    anim_phase: int = 0
    facing_left: bool = False


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
    moves_remaining: int = 1
    claimed_moonstones_mask: int = 0
    cleared_lair_nodes: set[int] = field(default_factory=set)
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


_QUADRANT_TARGETS: tuple[tuple[KnightId, int, int], ...] = (
    (KnightId.RED, 60, 50),
    (KnightId.BLUE, 260, 50),
    (KnightId.GREEN, 60, 160),
    (KnightId.YELLOW, 260, 160),
)


def _dist2(ax: int, ay: int, bx: int, by: int) -> int:
    dx = ax - bx
    dy = ay - by
    return dx * dx + dy * dy


def _region_for_coords(x: int, y: int) -> RegionId:
    if 100 <= x <= 220 and 70 <= y <= 130:
        return RegionId.VALLEY
    if x < 160:
        return RegionId.FOREST if y < 100 else RegionId.SWAMP
    return RegionId.GLEN if y < 100 else RegionId.WASTELAND


def _infer_stonehenge_id(sites: Sequence[MapSiteInput]) -> int:
    for site in sites:
        if site.kind == NodeKind.STONEHENGE:
            return site.site_id
    center = min(sites, key=lambda s: (_dist2(s.x, s.y, 160, 100), s.site_id))
    return center.site_id


def _infer_start_nodes(
    nodes: dict[int, OverworldNode],
    stonehenge_id: int,
) -> dict[KnightId, int]:
    start_tagged = [n for n in nodes.values() if n.kind == NodeKind.START]
    candidates = (
        start_tagged
        if len(start_tagged) >= 4
        else [n for n in nodes.values() if n.node_id != stonehenge_id and n.kind != NodeKind.LAIR]
    )
    if not candidates:
        candidates = [nodes[stonehenge_id]]
    starts: dict[KnightId, int] = {}
    for kid, tx, ty in _QUADRANT_TARGETS:
        best = min(candidates, key=lambda n: (_dist2(n.x, n.y, tx, ty), n.node_id))
        starts[kid] = best.node_id
    return starts


_PATROL_QUADRANTS: tuple[tuple[RegionId, int, int], ...] = (
    (RegionId.FOREST, 60, 50),
    (RegionId.GLEN, 260, 50),
    (RegionId.WASTELAND, 260, 160),
    (RegionId.SWAMP, 60, 160),
)


def _patrol_candidates(
    nodes: dict[int, OverworldNode],
    stonehenge_id: int,
) -> list[OverworldNode]:
    non_lair = [n for n in nodes.values() if n.node_id != stonehenge_id and n.kind != NodeKind.LAIR]
    if non_lair:
        return non_lair
    return [n for n in nodes.values() if n.node_id != stonehenge_id]


def _pick_regional_patrol_node(
    candidates: list[OverworldNode],
    region_id: RegionId,
    tx: int,
    ty: int,
    chosen: list[int],
) -> int | None:
    regional = [n for n in candidates if n.region_id == region_id and n.node_id not in chosen]
    if not regional:
        return None
    best = min(
        regional,
        key=lambda n: (
            0 if n.kind in (NodeKind.ROAD, NodeKind.WILDERNESS) else 1,
            _dist2(n.x, n.y, tx, ty),
            n.node_id,
        ),
    )
    return best.node_id


def _infer_dragon_patrol_route(
    nodes: dict[int, OverworldNode],
    stonehenge_id: int,
) -> tuple[int, ...]:
    """Build a deterministic clockwise outer-region patrol route for the Dragon."""
    candidates = _patrol_candidates(nodes, stonehenge_id)
    if not candidates:
        return ()

    route: list[int] = []
    for region_id, tx, ty in _PATROL_QUADRANTS:
        picked = _pick_regional_patrol_node(candidates, region_id, tx, ty, route)
        if picked is not None:
            route.append(picked)

    if len(route) >= 2:
        return tuple(route)

    for cand in sorted(candidates, key=lambda n: n.node_id):
        if cand.node_id not in route:
            route.append(cand.node_id)
        if len(route) >= 4:
            break
    return tuple(route)


def _connect_mst(
    node_ids: list[int],
    coords: dict[int, tuple[int, int]],
    adj: dict[int, set[int]],
) -> None:
    if len(node_ids) < 2:
        return
    visited = {node_ids[0]}
    unvisited = set(node_ids[1:])
    while unvisited:
        best_key: tuple[int, int, int] | None = None
        best_pair = (node_ids[0], node_ids[0])
        for u in visited:
            ux, uy = coords[u]
            for v in unvisited:
                vx, vy = coords[v]
                key = (_dist2(ux, uy, vx, vy), u, v)
                if best_key is None or key < best_key:
                    best_key = key
                    best_pair = (u, v)
        u, v = best_pair
        adj[u].add(v)
        adj[v].add(u)
        visited.add(v)
        unvisited.remove(v)


def _connect_nearest_sites(
    site_ids: list[int],
    coords: dict[int, tuple[int, int]],
    adj: dict[int, set[int]],
    degree_target: int = 2,
) -> None:
    for u in site_ids:
        ux, uy = coords[u]
        candidates = sorted(
            (v for v in site_ids if v != u),
            key=lambda v: (_dist2(ux, uy, coords[v][0], coords[v][1]), v),
        )
        for v in candidates[:degree_target]:
            adj[u].add(v)
            adj[v].add(u)


def _apply_explicit_neighbors(
    explicit_neighbors: dict[int, tuple[int, ...]],
    coords: dict[int, tuple[int, int]],
    adj: dict[int, set[int]],
) -> None:
    for u, nbrs in explicit_neighbors.items():
        for v in nbrs:
            if v not in coords:
                msg = f"Neighbor {v} of site {u} not found in graph"
                raise ValueError(msg)
            if v != u:
                adj[u].add(v)
                adj[v].add(u)


def _connect_isolated_lairs(
    lair_ids: list[int],
    site_ids: list[int],
    coords: dict[int, tuple[int, int]],
    adj: dict[int, set[int]],
) -> None:
    for lid in lair_ids:
        if adj[lid]:
            continue
        lx, ly = coords[lid]
        nearest = min(
            site_ids, key=lambda sid: (_dist2(lx, ly, coords[sid][0], coords[sid][1]), sid)
        )
        adj[lid].add(nearest)
        adj[nearest].add(lid)


def _default_site_kind(site_id: int, stonehenge_id: int, has_road_network: bool) -> NodeKind:
    if site_id == stonehenge_id:
        return NodeKind.STONEHENGE
    if has_road_network and (0 <= site_id <= 20 or site_id == 29):
        return NodeKind.ROAD
    return NodeKind.WILDERNESS


def _build_site_and_lair_nodes(
    sites: Sequence[MapSiteInput],
    lair_entries: dict[int, tuple[int, int, int]],
    adj: dict[int, set[int]],
    stonehenge_id: int,
    has_road_network: bool,
) -> dict[int, OverworldNode]:
    nodes: dict[int, OverworldNode] = {}
    next_site_lair_idx = 0
    for site in sites:
        kind = (
            site.kind
            if site.kind is not None
            else _default_site_kind(site.site_id, stonehenge_id, has_road_network)
        )
        lair_idx = -1
        if kind == NodeKind.LAIR:
            lair_idx = site.lair_index if site.lair_index >= 0 else next_site_lair_idx
            _validate_lair_index(lair_idx)
            next_site_lair_idx += 1
        region_id = (
            site.region_id if site.region_id is not None else _region_for_coords(site.x, site.y)
        )
        nodes[site.site_id] = OverworldNode(
            node_id=site.site_id,
            region_id=region_id,
            kind=kind,
            x=site.x,
            y=site.y,
            neighbors=tuple(sorted(adj[site.site_id])),
            lair_index=lair_idx,
            creature_type=site.creature_type,
            defence=site.defence,
            arena_script=site.arena_script,
        )
    for lid, (lair_index, lx, ly) in lair_entries.items():
        nodes[lid] = OverworldNode(
            node_id=lid,
            region_id=_region_for_coords(lx, ly),
            kind=NodeKind.LAIR,
            x=lx,
            y=ly,
            neighbors=tuple(sorted(adj[lid])),
            lair_index=lair_index,
        )
    return nodes


def _resolve_moves_per_turn(sites: Sequence[MapSiteInput], moves_per_turn: int | None) -> int:
    if moves_per_turn is not None:
        if moves_per_turn < 1:
            msg = f"moves_per_turn must be >= 1, got {moves_per_turn}"
            raise ValueError(msg)
        return moves_per_turn
    has_roads = len(sites) >= MOG_ROAD_SITE_THRESHOLD or any(s.kind == NodeKind.ROAD for s in sites)
    return DEFAULT_ROAD_MOVES_PER_TURN if has_roads else 1


def _index_sites(
    sites: Sequence[MapSiteInput],
) -> tuple[dict[int, tuple[int, int]], dict[int, tuple[int, ...]], list[int], int]:
    coords: dict[int, tuple[int, int]] = {}
    explicit_neighbors: dict[int, tuple[int, ...]] = {}
    site_ids: list[int] = []
    site_lair_count = 0
    for site in sites:
        if site.site_id in coords:
            msg = f"Duplicate site_id {site.site_id}"
            raise ValueError(msg)
        coords[site.site_id] = (site.x, site.y)
        site_ids.append(site.site_id)
        if site.neighbors:
            explicit_neighbors[site.site_id] = site.neighbors
        if site.kind == NodeKind.LAIR:
            site_lair_count += 1
    return coords, explicit_neighbors, site_ids, site_lair_count


def _collect_lair_entries(
    lairs: Sequence[tuple[int, int, int]],
    next_id: int,
    coords: dict[int, tuple[int, int]],
) -> dict[int, tuple[int, int, int]]:
    lair_entries: dict[int, tuple[int, int, int]] = {}
    for lair_index, lx, ly in lairs:
        _validate_lair_index(lair_index)
        lair_entries[next_id] = (lair_index, lx, ly)
        coords[next_id] = (lx, ly)
        next_id += 1
    return lair_entries


def build_overworld_graph_from_sites(
    sites: Sequence[MapSiteInput],
    *,
    lairs: Sequence[tuple[int, int, int]] = (),
    dragon_route: Sequence[int] = (),
    encounter_bands: Sequence[EncounterBand] = (),
    moves_per_turn: int | None = None,
) -> OverworldGraph:
    """Build an ``OverworldGraph`` from 320x200 ``MapSiteInput`` entries (ARCHITECTURE.md §5)."""
    if not sites:
        msg = "sites must not be empty"
        raise ValueError(msg)

    coords, explicit_neighbors, site_ids, site_lair_count = _index_sites(sites)
    lair_entries = (
        _collect_lair_entries(lairs, max(site_ids) + 1, coords) if site_lair_count == 0 else {}
    )

    all_ids = [*site_ids, *lair_entries]
    adj: dict[int, set[int]] = {nid: set() for nid in all_ids}
    if explicit_neighbors:
        _apply_explicit_neighbors(explicit_neighbors, coords, adj)
        _connect_isolated_lairs(list(lair_entries), site_ids, coords, adj)
    else:
        _connect_mst(all_ids, coords, adj)
        _connect_nearest_sites(site_ids, coords, adj)

    stonehenge_id = _infer_stonehenge_id(sites)
    resolved_moves = _resolve_moves_per_turn(sites, moves_per_turn)
    has_road_network = resolved_moves > 1
    nodes = _build_site_and_lair_nodes(sites, lair_entries, adj, stonehenge_id, has_road_network)

    for nid in dragon_route:
        if nid not in nodes:
            msg = f"Dragon route node {nid} not in graph"
            raise ValueError(msg)

    resolved_route = (
        tuple(dragon_route) if dragon_route else _infer_dragon_patrol_route(nodes, stonehenge_id)
    )
    return OverworldGraph(
        nodes=nodes,
        stonehenge_node_id=stonehenge_id,
        start_nodes=_infer_start_nodes(nodes, stonehenge_id),
        dragon_patrol_route=resolved_route,
        encounter_bands=tuple(encounter_bands),
        moves_per_turn=resolved_moves,
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
    dnode = active_graph.nodes.get(dragon_start)
    dx = dnode.x if dnode is not None else 10
    dy = dnode.y if dnode is not None else 100
    return OverworldState(
        graph=active_graph,
        prng=SimPrng(seed),
        turn_order=order,
        knights=knight_states,
        dragon=DragonState(
            node_id=dragon_start,
            patrol_index=0,
            active=True,
            x=dx,
            y=dy,
        ),
        moves_remaining=active_graph.moves_per_turn,
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
    else:
        neighbors = state.graph.neighbors(state.dragon.node_id)
        if neighbors:
            choice = state.prng.rand_range(len(neighbors))
            state.dragon.node_id = neighbors[choice]
    dnode = state.graph.nodes.get(state.dragon.node_id)
    if dnode is not None:
        state.dragon.x = dnode.x
        state.dragon.y = dnode.y
    return state.dragon.node_id


def _update_dragon_coords(dragon: DragonState, target_y: int) -> None:
    dragon.timer -= 1
    if dragon.timer < 0:
        dragon.timer = 100
    dragon.x += dragon.speed_x
    if dragon.timer > 60:
        if dragon.y < target_y:
            dragon.y += dragon.speed_y
        elif dragon.y > target_y:
            dragon.y -= dragon.speed_y
        dragon.anim_phase = (dragon.anim_phase + 1) & 0x0F
    if dragon.x > 304:
        dragon.x = 300
        dragon.speed_x = -abs(dragon.speed_x)
        dragon.facing_left = True
    elif dragon.x < -10:
        dragon.x = -6
        dragon.speed_x = abs(dragon.speed_x)
        dragon.facing_left = False
    if dragon.y > 192:
        dragon.y = 0
    elif dragon.y < 0:
        dragon.y = 192


def step_dragon_flight(
    state: OverworldState,
    target_y: int | None = None,
) -> EncounterTrigger | None:
    """Advance continuous overworld Dragon flight by one step (ARCHITECTURE.md §5, §7)."""
    if not state.dragon.active or state.game_over:
        return None
    cur_node = state.graph.nodes.get(state.current_knight.node_id)
    ty = target_y if target_y is not None else (cur_node.y if cur_node is not None else 100)
    _update_dragon_coords(state.dragon, ty)
    if state.graph.nodes:
        nearest = min(
            state.graph.nodes.values(),
            key=lambda n: (_dist2(state.dragon.x, state.dragon.y, n.x, n.y), n.node_id),
        )
        state.dragon.node_id = nearest.node_id
    if cur_node is None or cur_node.kind == NodeKind.START:
        return None
    if abs(state.dragon.x - cur_node.x) <= 10 and abs(state.dragon.y - cur_node.y) <= 8:
        return EncounterTrigger(
            kind=TriggerKind.DRAGON_INTERCEPT,
            knight_id=state.current_knight_id,
            node_id=cur_node.node_id,
            day=state.day,
            moon_phase=state.moon_phase,
        )
    return None


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


def _collect_special_site_trigger(
    state: OverworldState,
    knight_id: KnightId,
    node: OverworldNode,
) -> EncounterTrigger | None:
    """Evaluate town, Home Castle, Valley of the Gods, and Math's Tower triggers."""
    phase = state.moon_phase
    if node.kind in (NodeKind.HIGHWOOD, NodeKind.WATERDEEP):
        return EncounterTrigger(
            kind=TriggerKind.TOWN_VISIT,
            knight_id=knight_id,
            node_id=node.node_id,
            day=state.day,
            moon_phase=phase,
        )
    if node.kind == NodeKind.START and node.node_id == state.graph.start_nodes.get(knight_id):
        knight = state.knights[knight_id]
        if knight.lives < DEFAULT_KNIGHT_LIVES:
            knight.lives += 1
        return EncounterTrigger(
            kind=TriggerKind.HOME_CASTLE,
            knight_id=knight_id,
            node_id=node.node_id,
            day=state.day,
            moon_phase=phase,
        )
    if node.kind == NodeKind.VALLEY_OF_THE_GODS:
        return EncounterTrigger(
            kind=TriggerKind.VALLEY_OF_THE_GODS,
            knight_id=knight_id,
            node_id=node.node_id,
            day=state.day,
            moon_phase=phase,
        )
    if node.kind == NodeKind.MATHS_TOWER:
        return EncounterTrigger(
            kind=TriggerKind.MATHS_TOWER,
            knight_id=knight_id,
            node_id=node.node_id,
            day=state.day,
            moon_phase=phase,
        )
    return None


def _collect_location_trigger(
    state: OverworldState,
    knight_id: KnightId,
    node: OverworldNode,
) -> EncounterTrigger | None:
    """Evaluate the site/wilderness trigger for ``node``."""
    phase = state.moon_phase
    if node.kind == NodeKind.LAIR and node.lair_index >= 0:
        if node.node_id in state.cleared_lair_nodes:
            return None
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
        if node.arena_script:
            return EncounterTrigger(
                kind=TriggerKind.WANDERING_MONSTER,
                knight_id=knight_id,
                node_id=node.node_id,
                day=state.day,
                moon_phase=phase,
                lair_index=node.lair_index,
                creature_type=node.creature_type,
                encounter_kind=max(1, node.creature_type),
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
    return _collect_special_site_trigger(state, knight_id, node)


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


def _finalize_turn_end(
    state: OverworldState,
    knight: KnightOverworldState,
    triggers: list[EncounterTrigger],
) -> tuple[bool, int]:
    """Advance turn/day counters, reset movement budget, and step Dragon on new day."""
    knight.turns_taken += 1
    dragon_node = state.dragon.node_id
    day_advanced = _advance_turn_counter(state)
    state.moves_remaining = state.graph.moves_per_turn
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
    return day_advanced, dragon_node


def end_turn(state: OverworldState) -> TurnResult:
    """Voluntarily end the active knight's turn without moving further."""
    if state.game_over:
        msg = "Cannot end turn after campaign victory"
        raise ValueError(msg)

    knight_id = state.current_knight_id
    knight = state.knights[knight_id]
    triggers: list[EncounterTrigger] = []
    day_advanced, dragon_node = _finalize_turn_end(state, knight, triggers)
    trigger_tuple = tuple(triggers)
    state.last_triggers = trigger_tuple
    return TurnResult(
        knight_id=knight_id,
        from_node_id=knight.node_id,
        to_node_id=knight.node_id,
        day_advanced=day_advanced,
        new_day=state.day,
        moon_phase=state.moon_phase,
        dragon_node_id=dragon_node,
        triggers=trigger_tuple,
        moves_remaining=state.moves_remaining,
        turn_ended=True,
    )


def move_knight(state: OverworldState, target_node_id: int) -> TurnResult:
    """Move the active knight to ``target_node_id``, update budget/day, and emit triggers."""
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
    state.moves_remaining = max(0, state.moves_remaining - 1)

    dest_node = state.graph.node(target_node_id)
    triggers = _collect_node_triggers(state, knight_id, dest_node)

    turn_ended = state.game_over or state.moves_remaining <= 0 or bool(triggers)
    if turn_ended:
        day_advanced, dragon_node = _finalize_turn_end(state, knight, triggers)
    else:
        day_advanced = False
        dragon_node = state.dragon.node_id

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
        moves_remaining=state.moves_remaining,
        turn_ended=turn_ended,
    )


def resolve_valley_of_the_gods(state: OverworldState, knight_id: KnightId) -> int:
    """Apply the Valley of the Gods blessing (restore 1 life up to 3, grant XP)."""
    knight = state.knights[knight_id]
    if knight.lives < DEFAULT_KNIGHT_LIVES:
        knight.lives += 1
    knight.xp += VALLEY_BLESSING_XP
    return VALLEY_BLESSING_XP


def resolve_maths_tower(state: OverworldState, knight_id: KnightId) -> int:
    """Apply Math's Tower visit reward (grant XP and gold)."""
    knight = state.knights[knight_id]
    knight.xp += MATHS_TOWER_XP
    knight.gold += MATHS_TOWER_GOLD
    return MATHS_TOWER_XP


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
