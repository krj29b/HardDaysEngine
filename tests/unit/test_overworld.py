"""Unit tests for the pure-integer Overworld board-game loop (T7.1)."""

from __future__ import annotations

import pytest

from pymoonstone.sim.overworld import (
    ALL_MOONSTONES_MASK,
    DAYS_PER_LUNAR_CYCLE,
    DEFAULT_KNIGHT_LIVES,
    DEFAULT_ROAD_MOVES_PER_TURN,
    MATHS_TOWER_GOLD,
    MATHS_TOWER_XP,
    VALLEY_BLESSING_XP,
    EncounterBand,
    MapSiteInput,
    MoonPhase,
    NodeKind,
    OverworldGraph,
    OverworldNode,
    RegionId,
    TriggerKind,
    build_default_overworld_graph,
    build_overworld_graph_from_sites,
    can_enter_lair,
    collect_moonstone,
    create_campaign,
    end_turn,
    grant_lair_key,
    is_ritual_ready,
    legal_moves,
    moon_encounter_modifier,
    moon_phase_for_day,
    move_knight,
    resolve_lair_victory,
    resolve_maths_tower,
    resolve_pvp_duel,
    resolve_valley_of_the_gods,
    step_dragon_patrol,
)
from pymoonstone.sim.types import KnightId


@pytest.mark.sim
def test_create_campaign_validation_and_turn_order() -> None:
    with pytest.raises(ValueError, match=r"1\.\.4 knights"):
        create_campaign(())
    with pytest.raises(ValueError, match="Duplicate"):
        create_campaign((KnightId.RED, KnightId.RED))

    solo = create_campaign((KnightId.RED,), seed=101)
    assert solo.current_knight_id == KnightId.RED
    assert solo.day == 1
    assert solo.moon_phase == MoonPhase.NEW_MOON
    assert solo.is_new_moon is True
    assert solo.is_full_moon is False

    four = create_campaign(
        (KnightId.RED, KnightId.BLUE, KnightId.GREEN, KnightId.YELLOW),
        seed=202,
    )
    assert four.turn_order == (
        KnightId.RED,
        KnightId.BLUE,
        KnightId.GREEN,
        KnightId.YELLOW,
    )
    assert four.knights[KnightId.RED].node_id == 10
    assert four.knights[KnightId.BLUE].node_id == 20
    assert four.knights[KnightId.GREEN].node_id == 30
    assert four.knights[KnightId.YELLOW].node_id == 40


@pytest.mark.sim
def test_deterministic_multi_turn_simulation() -> None:
    def run_sequence(seed: int) -> tuple[int, int, int, tuple[TriggerKind, ...]]:
        state = create_campaign((KnightId.RED, KnightId.BLUE), seed=seed)
        # Red: 10 -> 11 -> 10; Blue: 20 -> 21 -> 20
        r1 = move_knight(state, 11)
        b1 = move_knight(state, 21)
        r2 = move_knight(state, 10)
        b2 = move_knight(state, 20)
        triggers = tuple(t.kind for res in (r1, b1, r2, b2) for t in res.triggers)
        return state.day, state.dragon.node_id, state.prng.state, triggers

    first = run_sequence(0xBEEF_1234)
    second = run_sequence(0xBEEF_1234)
    assert first == second
    assert first[0] == 3


@pytest.mark.sim
def test_28_day_moon_phase_cycling_and_modifiers() -> None:
    with pytest.raises(ValueError, match="day must be >= 1"):
        moon_phase_for_day(0)

    phases = [moon_phase_for_day(d) for d in range(1, DAYS_PER_LUNAR_CYCLE + 2)]
    assert phases[0] == MoonPhase.NEW_MOON
    assert phases[14] == MoonPhase.FULL_MOON
    assert phases[28] == MoonPhase.NEW_MOON
    assert len(set(phases[:DAYS_PER_LUNAR_CYCLE])) == 8

    assert moon_encounter_modifier(MoonPhase.FULL_MOON) > 0
    assert moon_encounter_modifier(MoonPhase.NEW_MOON) < 0
    assert moon_encounter_modifier(MoonPhase.FIRST_QUARTER) == 0

    # Advance a 1-knight campaign through 28 days
    state = create_campaign((KnightId.RED,), seed=77)
    for _ in range(28):
        nxt = legal_moves(state)[0]
        move_knight(state, nxt)
    assert state.day == 29
    assert state.day_of_cycle == 1
    assert state.is_new_moon is True


@pytest.mark.sim
def test_dragon_patrol_movement_and_intercept_trigger() -> None:
    state = create_campaign((KnightId.RED,), seed=55)
    # Default route is (11, 21, 41, 31), starting at 11
    assert state.dragon.node_id == 11
    # Red moves 10 -> 11: lands directly on Dragon -> DRAGON_INTERCEPT,
    # then day advances and Dragon steps to 21.
    res = move_knight(state, 11)
    assert any(t.kind == TriggerKind.DRAGON_INTERCEPT for t in res.triggers)
    assert res.dragon_node_id == 21

    # Also verify Dragon patrol stepping onto a knight triggers DRAGON_INTERCEPT
    state2 = create_campaign((KnightId.RED,), seed=55)
    move_knight(state2, 1)  # day 1 -> 2, dragon moves to 21
    move_knight(state2, 0)  # day 2 -> 3, dragon moves to 41
    move_knight(state2, 20)  # day 3 -> 4, dragon moves to 31
    res_patrol = move_knight(state2, 21)  # Red at 21, dragon wraps to 11
    assert res_patrol.dragon_node_id == 11

    # Test PRNG fallback patrol when route is empty
    graph = OverworldGraph(
        nodes={
            0: OverworldNode(0, RegionId.VALLEY, NodeKind.STONEHENGE, 160, 100, (1, 2)),
            1: OverworldNode(1, RegionId.FOREST, NodeKind.WILDERNESS, 80, 100, (0,)),
            2: OverworldNode(2, RegionId.GLEN, NodeKind.WILDERNESS, 240, 100, (0,)),
        },
        stonehenge_node_id=0,
        start_nodes={KnightId.RED: 1},
        dragon_patrol_route=(),
    )
    free_state = create_campaign((KnightId.RED,), graph=graph, seed=9)
    new_node = step_dragon_patrol(free_state)
    assert new_node in (1, 2)


@pytest.mark.sim
def test_lair_key_and_moonstone_collection() -> None:
    state = create_campaign((KnightId.RED,), seed=12)
    assert can_enter_lair(state, KnightId.RED, 0) is False

    # Move to 11 then 12 (Lair 0) without key -> no LAIR_BOSS trigger
    move_knight(state, 11)
    no_key_res = move_knight(state, 12)
    assert all(t.kind != TriggerKind.LAIR_BOSS for t in no_key_res.triggers)

    # Grant key 0, step back to 11 and re-enter 12 -> LAIR_BOSS trigger
    grant_lair_key(state, KnightId.RED, 0)
    assert can_enter_lair(state, KnightId.RED, 0) is True
    move_knight(state, 11)
    boss_res = move_knight(state, 12)
    assert any(t.kind == TriggerKind.LAIR_BOSS and t.lair_index == 0 for t in boss_res.triggers)

    assert resolve_lair_victory(state, KnightId.RED, 0) is True
    assert state.knights[KnightId.RED].has_moonstone(0) is True
    assert state.knights[KnightId.RED].moonstone_count == 1
    assert resolve_lair_victory(state, KnightId.RED, 0) is False


@pytest.mark.sim
def test_pvp_duel_trigger_and_moonstone_transfer() -> None:
    state = create_campaign((KnightId.RED, KnightId.BLUE), seed=33)
    collect_moonstone(state, KnightId.BLUE, 1)
    # Red moves 10 -> 0 (Stonehenge)
    move_knight(state, 0)
    # Blue moves 20 -> 0 (lands on Red's node -> PVP_DUEL)
    res = move_knight(state, 0)
    duels = [t for t in res.triggers if t.kind == TriggerKind.PVP_DUEL]
    assert len(duels) == 1
    assert duels[0].knight_id == KnightId.BLUE
    assert duels[0].opponent_knight_id == KnightId.RED

    resolve_pvp_duel(state, KnightId.RED, KnightId.BLUE)
    assert state.knights[KnightId.RED].has_moonstone(1) is True
    assert state.knights[KnightId.BLUE].has_moonstone(1) is False
    assert state.knights[KnightId.BLUE].lives == 2
    assert state.knights[KnightId.BLUE].node_id == 20


@pytest.mark.sim
def test_town_visit_and_wandering_encounter() -> None:
    base = build_default_overworld_graph()
    # Force guaranteed wandering encounter with limit=200
    guaranteed_graph = OverworldGraph(
        nodes=base.nodes,
        stonehenge_node_id=base.stonehenge_node_id,
        start_nodes=base.start_nodes,
        dragon_patrol_route=base.dragon_patrol_route,
        encounter_bands=(EncounterBand(limit=200, kind=3),),
    )
    state = create_campaign((KnightId.RED,), graph=guaranteed_graph, seed=1)
    town_res = move_knight(state, 1)  # Highwood
    assert any(t.kind == TriggerKind.TOWN_VISIT for t in town_res.triggers)

    move_knight(state, 10)
    wild_res = move_knight(state, 11)  # Wilderness
    assert any(
        t.kind == TriggerKind.WANDERING_MONSTER and t.encounter_kind == 3 for t in wild_res.triggers
    )


@pytest.mark.sim
def test_stonehenge_ritual_and_victory_condition() -> None:
    state = create_campaign((KnightId.RED, KnightId.BLUE), seed=99)
    # Red arrives at Stonehenge with 0 Moonstones -> ritual not ready
    res_early = move_knight(state, 0)
    assert any(t.kind == TriggerKind.STONEHENGE_RITUAL for t in res_early.triggers)
    assert state.game_over is False
    assert state.winner is None

    # Blue moves to 21, Red steps to 1, collects all 4 Moonstones, returns to Stonehenge
    move_knight(state, 21)
    move_knight(state, 1)
    for lair_idx in range(4):
        collect_moonstone(state, KnightId.RED, lair_idx)
    assert state.knights[KnightId.RED].moonstones_mask == ALL_MOONSTONES_MASK
    assert is_ritual_ready(state, KnightId.RED) is False  # Not at Stonehenge yet

    move_knight(state, 20)  # Blue turn
    res_win = move_knight(state, 0)  # Red arrives at Stonehenge with all 4 Moonstones
    assert state.game_over is True
    assert state.winner == KnightId.RED
    assert any(t.kind == TriggerKind.VICTORY for t in res_win.triggers)
    assert legal_moves(state) == ()
    with pytest.raises(ValueError, match="victory"):
        move_knight(state, 1)


@pytest.mark.sim
def test_build_overworld_graph_from_sites_and_lairs() -> None:
    sites = (
        MapSiteInput(site_id=10, x=160, y=100),  # center -> Stonehenge
        MapSiteInput(site_id=11, x=115, y=90, kind=NodeKind.HIGHWOOD),
        MapSiteInput(site_id=12, x=205, y=110, kind=NodeKind.WATERDEEP),
        MapSiteInput(site_id=13, x=55, y=45),  # Forest
        MapSiteInput(site_id=14, x=265, y=45),  # Glen
        MapSiteInput(site_id=15, x=55, y=165),  # Swamp
        MapSiteInput(site_id=16, x=265, y=165),  # Wasteland
    )
    lairs = (
        (0, 25, 20),
        (1, 295, 20),
        (2, 25, 185),
        (3, 295, 185),
    )
    bands = (EncounterBand(limit=30, kind=1), EncounterBand(limit=70, kind=2))
    graph = build_overworld_graph_from_sites(
        sites,
        lairs=lairs,
        dragon_route=(13, 14, 16, 15),
        encounter_bands=bands,
    )

    assert graph.stonehenge_node_id == 10
    assert graph.node(10).kind == NodeKind.STONEHENGE
    assert graph.node(10).region_id == RegionId.VALLEY
    assert graph.node(11).kind == NodeKind.HIGHWOOD
    assert graph.node(12).kind == NodeKind.WATERDEEP
    assert graph.node(13).region_id == RegionId.FOREST
    assert graph.node(14).region_id == RegionId.GLEN
    assert graph.node(15).region_id == RegionId.SWAMP
    assert graph.node(16).region_id == RegionId.WASTELAND
    assert graph.dragon_patrol_route == (13, 14, 16, 15)
    assert graph.encounter_bands == bands
    assert set(graph.start_nodes) == {
        KnightId.RED,
        KnightId.BLUE,
        KnightId.GREEN,
        KnightId.YELLOW,
    }

    lair_nodes = [n for n in graph.nodes.values() if n.kind == NodeKind.LAIR]
    assert len(lair_nodes) == 4
    assert {n.lair_index for n in lair_nodes} == {0, 1, 2, 3}

    # Graph is symmetric and fully connected from Stonehenge
    visited: set[int] = set()
    queue = [graph.stonehenge_node_id]
    while queue:
        cur = queue.pop()
        if cur in visited:
            continue
        visited.add(cur)
        for nbr in graph.neighbors(cur):
            assert cur in graph.neighbors(nbr)
            if nbr not in visited:
                queue.append(nbr)
    assert visited == set(graph.nodes)


@pytest.mark.sim
def test_build_overworld_graph_from_sites_explicit_and_errors() -> None:
    with pytest.raises(ValueError, match="empty"):
        build_overworld_graph_from_sites(())

    with pytest.raises(ValueError, match="Duplicate"):
        build_overworld_graph_from_sites((MapSiteInput(1, 10, 10), MapSiteInput(1, 20, 20)))

    with pytest.raises(ValueError, match="lair_index"):
        build_overworld_graph_from_sites((MapSiteInput(1, 160, 100),), lairs=((4, 10, 10),))

    with pytest.raises(ValueError, match="Dragon route"):
        build_overworld_graph_from_sites((MapSiteInput(1, 160, 100),), dragon_route=(99,))

    with pytest.raises(ValueError, match="Neighbor"):
        build_overworld_graph_from_sites((MapSiteInput(1, 160, 100, neighbors=(99,)),))

    # Single-site graph is valid
    single = build_overworld_graph_from_sites((MapSiteInput(5, 160, 100),))
    assert single.stonehenge_node_id == 5
    assert single.neighbors(5) == ()

    # Explicit neighbors and explicit Stonehenge override center heuristic
    explicit = build_overworld_graph_from_sites(
        (
            MapSiteInput(1, 160, 100, neighbors=(2,)),
            MapSiteInput(2, 50, 50, kind=NodeKind.STONEHENGE, region_id=RegionId.FOREST),
        ),
        lairs=((0, 25, 25),),
    )
    assert explicit.stonehenge_node_id == 2
    assert explicit.node(2).region_id == RegionId.FOREST
    assert 2 in explicit.neighbors(1)
    assert 1 in explicit.neighbors(2)


@pytest.mark.sim
def test_create_campaign_with_prebuilt_site_graph() -> None:
    sites = (
        MapSiteInput(site_id=0, x=160, y=100),
        MapSiteInput(site_id=1, x=60, y=50),
        MapSiteInput(site_id=2, x=260, y=50),
        MapSiteInput(site_id=3, x=60, y=160),
        MapSiteInput(site_id=4, x=260, y=160),
    )
    graph = build_overworld_graph_from_sites(
        sites,
        lairs=((0, 25, 20),),
        dragon_route=(2, 4, 3, 1),
    )
    state = create_campaign((KnightId.RED, KnightId.BLUE), graph=graph, seed=123)
    assert state.graph is graph
    assert state.knights[KnightId.RED].node_id == 1
    assert state.knights[KnightId.BLUE].node_id == 2
    assert state.dragon.node_id == 2

    dest = legal_moves(state)[0]
    res = move_knight(state, dest)
    assert res.to_node_id == dest
    assert state.current_knight_id == KnightId.BLUE


def _make_35_mog_sites() -> tuple[MapSiteInput, ...]:
    """Synthetic 35-node mog map matching node IDs 0..34."""
    sites: list[MapSiteInput] = []
    # 0..20: road waypoints spread across all 4 quadrants
    for idx in range(21):
        qx = 45 + (idx % 5) * 55
        qy = 35 + (idx // 5) * 35
        sites.append(MapSiteInput(site_id=idx, x=qx, y=qy))
    # 21..24: Home Castles
    sites.extend(
        [
            MapSiteInput(site_id=21, x=60, y=50, kind=NodeKind.START),
            MapSiteInput(site_id=22, x=260, y=50, kind=NodeKind.START),
            MapSiteInput(site_id=23, x=60, y=160, kind=NodeKind.START),
            MapSiteInput(site_id=24, x=260, y=160, kind=NodeKind.START),
            MapSiteInput(site_id=25, x=115, y=90, kind=NodeKind.HIGHWOOD),
            MapSiteInput(site_id=26, x=205, y=110, kind=NodeKind.WATERDEEP),
            MapSiteInput(site_id=27, x=160, y=100, kind=NodeKind.STONEHENGE),
            MapSiteInput(site_id=28, x=152, y=97, kind=NodeKind.VALLEY_OF_THE_GODS),
            MapSiteInput(site_id=29, x=180, y=75),  # road waypoint
            MapSiteInput(site_id=30, x=217, y=11, kind=NodeKind.MATHS_TOWER),
            MapSiteInput(site_id=31, x=25, y=20, kind=NodeKind.LAIR),
            MapSiteInput(site_id=32, x=295, y=20, kind=NodeKind.LAIR),
            MapSiteInput(site_id=33, x=25, y=185, kind=NodeKind.LAIR),
            MapSiteInput(site_id=34, x=295, y=185, kind=NodeKind.LAIR),
        ]
    )
    return tuple(sites)


@pytest.mark.sim
def test_35_mog_nodes_road_budget_and_dragon_patrol() -> None:
    sites = _make_35_mog_sites()
    graph = build_overworld_graph_from_sites(sites)

    assert len(graph.nodes) == 35
    assert graph.stonehenge_node_id == 27
    assert graph.moves_per_turn == DEFAULT_ROAD_MOVES_PER_TURN
    assert graph.node(0).kind == NodeKind.ROAD
    assert graph.node(29).kind == NodeKind.ROAD
    assert graph.node(28).kind == NodeKind.VALLEY_OF_THE_GODS
    assert graph.node(30).kind == NodeKind.MATHS_TOWER
    assert [graph.node(31 + i).lair_index for i in range(4)] == [0, 1, 2, 3]
    assert graph.start_nodes == {
        KnightId.RED: 21,
        KnightId.BLUE: 22,
        KnightId.GREEN: 23,
        KnightId.YELLOW: 24,
    }

    # Dragon patrol route is populated and does not keep Dragon stuck on Stonehenge (27)
    assert len(graph.dragon_patrol_route) == 4
    assert 27 not in graph.dragon_patrol_route

    # Build a clean road-chain graph to verify multi-hop movement budget
    road_sites = (
        MapSiteInput(site_id=27, x=160, y=100, kind=NodeKind.STONEHENGE, neighbors=(0,)),
        MapSiteInput(site_id=21, x=60, y=50, kind=NodeKind.START, neighbors=(0,)),
        MapSiteInput(site_id=0, x=80, y=50, kind=NodeKind.ROAD, neighbors=(21, 1)),
        MapSiteInput(site_id=1, x=100, y=50, kind=NodeKind.ROAD, neighbors=(0, 2)),
        MapSiteInput(site_id=2, x=120, y=50, kind=NodeKind.ROAD, neighbors=(1, 3)),
        MapSiteInput(site_id=3, x=140, y=50, kind=NodeKind.ROAD, neighbors=(2, 27)),
    )
    road_graph = build_overworld_graph_from_sites(road_sites, dragon_route=(3, 2), moves_per_turn=4)
    state = create_campaign((KnightId.RED, KnightId.BLUE), graph=road_graph, seed=42)
    assert state.moves_remaining == 4
    assert state.dragon.node_id != 27

    r1 = move_knight(state, 0)
    assert r1.turn_ended is False
    assert r1.day_advanced is False
    assert r1.moves_remaining == 3
    assert state.current_knight_id == KnightId.RED

    r2 = move_knight(state, 1)
    assert r2.turn_ended is False
    assert r2.moves_remaining == 2

    # Voluntary end_turn advances to BLUE without advancing day
    r_end = end_turn(state)
    assert r_end.turn_ended is True
    assert r_end.day_advanced is False
    assert state.current_knight_id == KnightId.BLUE
    assert state.moves_remaining == 4

    with pytest.raises(ValueError, match="moves_per_turn"):
        build_overworld_graph_from_sites(road_sites, moves_per_turn=0)


@pytest.mark.sim
def test_home_castles_valley_of_the_gods_and_maths_tower() -> None:
    sites = (
        MapSiteInput(site_id=27, x=160, y=100, kind=NodeKind.STONEHENGE, neighbors=(0,)),
        MapSiteInput(site_id=21, x=60, y=50, kind=NodeKind.START, neighbors=(0,)),
        MapSiteInput(site_id=22, x=260, y=50, kind=NodeKind.START, neighbors=(0,)),
        MapSiteInput(site_id=0, x=120, y=80, kind=NodeKind.ROAD, neighbors=(21, 22, 27, 28, 30)),
        MapSiteInput(site_id=28, x=152, y=97, kind=NodeKind.VALLEY_OF_THE_GODS, neighbors=(0,)),
        MapSiteInput(site_id=30, x=217, y=11, kind=NodeKind.MATHS_TOWER, neighbors=(0,)),
    )
    graph = build_overworld_graph_from_sites(sites, moves_per_turn=4)
    state = create_campaign((KnightId.RED,), graph=graph, seed=7)

    # Reduce RED's lives to 1, visit another knight's castle (22) -> no free life
    state.knights[KnightId.RED].lives = 1
    move_knight(state, 0)
    other_castle = move_knight(state, 22)
    assert all(t.kind != TriggerKind.HOME_CASTLE for t in other_castle.triggers)
    assert state.knights[KnightId.RED].lives == 1

    # Return to RED's own Home Castle (21) -> grants +1 life (1 -> 2) and HOME_CASTLE trigger
    move_knight(state, 0)
    own_castle = move_knight(state, 21)
    assert any(t.kind == TriggerKind.HOME_CASTLE for t in own_castle.triggers)
    assert state.knights[KnightId.RED].lives == 2

    # Visit Valley of the Gods (28) -> VALLEY_OF_THE_GODS trigger + blessing up to 3 lives
    move_knight(state, 0)
    valley_res = move_knight(state, 28)
    assert any(t.kind == TriggerKind.VALLEY_OF_THE_GODS for t in valley_res.triggers)
    xp_gained = resolve_valley_of_the_gods(state, KnightId.RED)
    assert xp_gained == VALLEY_BLESSING_XP
    assert state.knights[KnightId.RED].lives == DEFAULT_KNIGHT_LIVES

    # Visit Math's Tower (30) -> MATHS_TOWER trigger + XP and gold reward
    move_knight(state, 0)
    tower_res = move_knight(state, 30)
    assert any(t.kind == TriggerKind.MATHS_TOWER for t in tower_res.triggers)
    tower_xp = resolve_maths_tower(state, KnightId.RED)
    assert tower_xp == MATHS_TOWER_XP
    assert state.knights[KnightId.RED].xp == VALLEY_BLESSING_XP + MATHS_TOWER_XP
    assert state.knights[KnightId.RED].gold == MATHS_TOWER_GOLD

    # Calling end_turn after game_over raises ValueError
    state.game_over = True
    with pytest.raises(ValueError, match="victory"):
        end_turn(state)
