"""Shared word-wrapping trajectory slots (ARCHITECTURE.md §10.23)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pymoonstone.sim.m68k import divs_w, divu_w, s16, u16

if TYPE_CHECKING:
    from pymoonstone.sim.actor import Actor


class TrajectoryError(ValueError):
    """An exhausted flight pool or arithmetic the original cannot safely complete."""


@dataclass(slots=True, frozen=True)
class FlightRules:
    """Trajectory tunables decoded from the original executable (§10.23)."""

    slots: int
    level_threshold: int
    arc_limit: int
    minimum_arc: int
    step_limit: int
    minimum_steps: int
    fallback_arc: int


@dataclass(slots=True)
class FlightRequest:
    """The shared start/destination block, in integer screen coordinates."""

    owner: int = 0
    x: int = 0
    depth: int = 0
    height: int = 0
    target_x: int = 0
    target_depth: int = 0
    target_height: int = 0
    steps: int = 0
    arc: int = 0


@dataclass(slots=True)
class FlightSlot:
    """A keyed 10.6 horizontal / 8.8 vertical trajectory; all stored values are words."""

    owner: int = 0
    remaining: int = 0
    velocity: int = 0
    acceleration: int = 0
    dx: int = 0
    dy: int = 0
    x: int = 0
    depth: int = 0
    height: int = 0


@dataclass(slots=True)
class FlightState:
    """Fight-owned request and slots, shared by every caster and later flight users."""

    request: FlightRequest = field(default_factory=FlightRequest)
    slots: list[FlightSlot] = field(default_factory=list)


def _divide(value: int, divisor: int, *, unsigned: bool = False) -> int:
    if u16(divisor) == 0:
        raise TrajectoryError("zero trajectory divisor")
    result = divu_w(value, divisor) if unsigned else divs_w(value, divisor)
    if result is None:
        raise TrajectoryError("trajectory quotient overflow")
    return u16(result.quotient)


def _vertical(request: FlightRequest, rules: FlightRules) -> tuple[int, int]:
    difference = s16(u16(request.height - request.target_height))
    distance = s16(u16(-difference)) if difference < 0 else difference
    n = request.steps
    if distance <= s16(rules.level_threshold):
        half = u16(n + 1) >> 1
        velocity = u16(2 * _divide(u16(request.arc << 8), half, unsigned=True))
        acceleration = _divide(s16(velocity), u16(half - 1), unsigned=True)
        return velocity, acceleration
    doubled = u16(2 * _divide(difference << 8, n))
    if difference >= 0:
        return doubled, _divide(s16(doubled), u16(n - 1))
    return 0, u16(-_divide(s16(doubled), u16(n - 1)))


def start_flight(state: FlightState, rules: FlightRules) -> None:
    """Fill the first free-or-matching slot, retaining original fixed-point overflow (§10.23)."""
    request = state.request
    slot = next((s for s in state.slots if s.owner in (0, request.owner)), None)
    if slot is None:
        raise TrajectoryError("all trajectory slots are occupied")
    velocity, acceleration = _vertical(request, rules)
    dx = _divide(s16(u16((request.target_x - request.x) << 6)), request.steps)
    dy = _divide(s16(u16((request.target_depth - request.depth) << 6)), request.steps)
    slot.owner, slot.remaining = request.owner, u16(request.steps)
    slot.velocity, slot.acceleration = velocity, acceleration
    slot.dx, slot.dy = dx, dy
    slot.x, slot.depth, slot.height = (
        u16(request.x << 6),
        u16(request.depth << 6),
        u16(request.height << 8),
    )


def step_flight(state: FlightState, owner: int, actor: Actor) -> bool:
    """Advance an owner's slot; true means completed or absent, with absent coordinates kept."""
    slot = next((s for s in state.slots if s.owner == owner), None)
    if slot is None:
        return True
    old_velocity = slot.velocity
    slot.velocity = u16(slot.velocity - slot.acceleration)
    slot.height = u16(slot.height - old_velocity)
    slot.x, slot.depth = u16(slot.x + slot.dx), u16(slot.depth + slot.dy)
    actor.x, actor.y, actor.height = (
        u16(s16(slot.x) >> 6),
        u16(s16(slot.depth) >> 6),
        u16(s16(slot.height) >> 8),
    )
    slot.remaining = u16(slot.remaining - 1)
    if slot.remaining == 0:
        slot.owner = 0
        return True
    return False


def aim_flight(
    state: FlightState,
    rules: FlightRules,
    owner: int,
    actor: Actor,
    target: Actor,
    map_y_actor: Actor | None = None,
) -> tuple[int, int, int]:
    """Fill a request aimed at the first fighter; return distance, steps and arc (§10.23)."""
    offset = u16(actor.reach_x if u16(target.x - actor.x) & 0x8000 else -actor.reach_x)
    request = state.request
    request.owner = owner
    request.x, request.depth, request.height = actor.x, actor.y, actor.height
    request.target_x = u16(target.x + offset)
    request.target_depth, request.target_height = target.y, target.height
    actor.map_x = request.target_x
    (actor if map_y_actor is None else map_y_actor).map_y = target.y
    dx, dy = s16(u16(request.target_x - actor.x)), s16(u16(target.y - actor.y))
    ax, ay = u16(-dx) if dx < 0 else dx, u16(-dy) if dy < 0 else dy
    distance = ay if s16(ay) >= s16(ax) else ax
    steps, arc = distance >> 3, distance >> 1
    if s16(arc) <= s16(rules.arc_limit):
        arc = rules.minimum_arc
    if s16(steps) <= s16(rules.step_limit):
        steps, arc = rules.minimum_steps, rules.fallback_arc
    request.steps, request.arc, actor.cooldown = steps, arc, steps
    return distance, steps, arc
