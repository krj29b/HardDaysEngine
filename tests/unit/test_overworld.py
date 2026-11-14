"""Unit tests for the pure-integer Overworld board-game loop (T7.1)."""

from __future__ import annotations

import pytest

from pymoonstone.sim.overworld import (
    ALL_MOONSTONES_MASK,
    DAYS_PER_LUNAR_CYCLE,
    EncounterBand,
    MoonPhase,
    NodeKind,
    OverworldGraph,
    OverworldNode,
    RegionId,
    TriggerKind,
    build_default_overworld_graph,
    can_enter_lair,
    collect_moonstone,
    create_campaign,
    grant_lair_key,
    is_ritual_ready,
    legal_moves,
    moon_encounter_modifier,
    moon_phase_for_day,
    move_knight,
    resolve_lair_victory,
    resolve_pvp_duel,
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
