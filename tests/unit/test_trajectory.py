"""Independent fixed-point examples of the original flight slots (§10.23)."""

from dataclasses import asdict

import pytest

from harddaysengine.sim.actor import Actor
from harddaysengine.sim.trajectory import (
    FlightRequest,
    FlightSlot,
    FlightState,
    TrajectoryError,
    aim_flight,
    start_flight,
    step_flight,
)
from tests.unit.caster_setup import FLIGHT


def test_level_arc_uses_old_velocity_and_frees_only_on_final_step() -> None:
    state = FlightState(FlightRequest(7, 100, 80, 0, 120, 90, 0, 10, 5), [FlightSlot()])
    start_flight(state, FLIGHT)
    slot = state.slots[0]
    assert (slot.velocity, slot.acceleration, slot.dx, slot.dy) == (512, 128, 128, 64)
    actor = Actor()
    assert not step_flight(state, 7, actor)
    assert (actor.x, actor.y, actor.height) == (102, 81, 0xFFFE)
    for _ in range(8):
        assert not step_flight(state, 7, actor)
    assert step_flight(state, 7, actor)
    assert (actor.x, actor.y, actor.height, slot.owner) == (120, 90, 2, 0)


@pytest.mark.parametrize(("height", "velocity", "acceleration"), [(20, 1024, 146), (-20, 0, 146)])
def test_unequal_heights_use_signed_division(height: int, velocity: int, acceleration: int) -> None:
    state = FlightState(FlightRequest(2, 100, 100, height, 100, 100, 4, 8, 0), [FlightSlot()])
    start_flight(state, FLIGHT)
    # D is +16 or -24; the latter has a different signed truncated quotient.
    expected = acceleration if height > 0 else 219
    assert (state.slots[0].velocity, state.slots[0].acceleration) == (velocity, expected)


def test_first_free_slot_precedes_an_existing_owner_and_missing_step_keeps_position() -> None:
    state = FlightState(FlightRequest(7, steps=10), [FlightSlot(), FlightSlot(owner=7)])
    start_flight(state, FLIGHT)
    assert state.slots[0].owner == state.slots[1].owner == 7
    actor = Actor(x=19, y=20, height=21)
    assert step_flight(state, 9, actor)
    assert (actor.x, actor.y, actor.height) == (19, 20, 21)


def test_pool_and_arithmetic_errors_leave_slots_unchanged() -> None:
    state = FlightState(FlightRequest(7, steps=10), [FlightSlot(owner=8)])
    before = asdict(state.slots[0])
    with pytest.raises(TrajectoryError, match="occupied"):
        start_flight(state, FLIGHT)
    assert asdict(state.slots[0]) == before
    state.slots[0].owner = 0
    state.request.steps = 0
    with pytest.raises(TrajectoryError, match="zero"):
        start_flight(state, FLIGHT)


def test_horizontal_word_overflow_and_arithmetic_shift_are_preserved() -> None:
    state = FlightState(FlightRequest(7, 510, 100, 0, 520, 100, 0, 10, 0), [FlightSlot()])
    start_flight(state, FLIGHT)
    actor = Actor()
    assert not step_flight(state, 7, actor)
    assert actor.x == 511
    assert not step_flight(state, 7, actor)
    assert actor.x == 0xFE00


def test_aim_sets_destination_cooldown_and_minimum_arc_from_rules() -> None:
    state = FlightState()
    actor, target = Actor(x=100, y=100, reach_x=20), Actor(x=120, y=100)
    assert aim_flight(state, FLIGHT, 7, actor, target) == (0, 6, 12)
    assert (actor.map_x, actor.map_y, actor.cooldown) == (100, 100, 6)
    target.x = 20
    assert aim_flight(state, FLIGHT, 7, actor, target) == (60, 7, 30)
    assert state.request.target_x == 40
