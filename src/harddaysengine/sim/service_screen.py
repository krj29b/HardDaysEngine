"""The healer and mystic screens, one PAL tick at a time (ARCHITECTURE.md §12.31).

The phases follow the original routine: greeting and a fire wait, the donation loop, the reply
(where the transaction of §12.30 runs), a fixed wait, a fire wait. The state holds every cell the
replay hash needs; the presenter only draws it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING

from harddaysengine.sim.m68k import s16
from harddaysengine.sim.service_screen_types import (
    ACCEPT,
    LEAVE,
    LESS,
    MORE,
    ServiceKind,
    ServiceScreens,
)
from harddaysengine.sim.services import Donation, heal, mystic_gamble
from harddaysengine.sim.sound import SoundKind, SoundRequest

if TYPE_CHECKING:
    from harddaysengine.sim.actor import Actor
    from harddaysengine.sim.inventory import Inventory
    from harddaysengine.sim.rng import Rng
    from harddaysengine.sim.service_types import HealerRules, MysticRules
    from harddaysengine.sim.turn_rules import GiftRules, TextLine

POINTER_MAX_X = 314
POINTER_MAX_Y = 195
POINTER_STEP = 2
_NUMBER_MODULO = 1000
_NUMBER_WIDTH = 3


class Phase(IntEnum):
    """Where the original routine is (§12.31)."""

    GREETING = 0
    DONATION = 1
    CLOSE_WAIT = 2
    CLOSE_FIRE = 3
    DONE = 4


@dataclass(slots=True, frozen=True)
class ServiceWorld:
    """The persistent records a service changes, and the rules it applies (§12.30)."""

    actor: Actor
    inventory: Inventory
    rng: Rng
    gifts: GiftRules
    healer: HealerRules
    mystic: MysticRules


@dataclass(slots=True)
class ServiceState:
    """Every mutable cell of one screen visit."""

    kind: ServiceKind
    knight: int
    pointer_x: int
    pointer_y: int
    lines: tuple[TextLine, ...]
    phase: Phase = Phase.GREETING
    fire_held: bool = False
    wait: int = 0
    donation: Donation = field(default_factory=lambda: Donation(0))
    outcome: str = ""
    sounds: list[SoundRequest] = field(default_factory=list)


def number3(value: int) -> str:
    """The original three-character number field (§12.31)."""
    return str(value % _NUMBER_MODULO).ljust(_NUMBER_WIDTH)


def open_service(kind: ServiceKind, knight: int, screens: ServiceScreens) -> ServiceState:
    """Greeting shown, synth sequences started, waiting for fire (§12.31)."""
    opening = screens.opening(kind)
    state = ServiceState(kind, knight, opening.pointer_x, opening.pointer_y, opening.greeting)
    state.sounds = [SoundRequest(SoundKind.START, seq, ch) for seq, ch in opening.sounds]
    return state


def _fire_released(state: ServiceState, fire: bool) -> bool:
    """The original fire wait: a press, then a release."""
    if fire:
        state.fire_held = True
        return False
    released, state.fire_held = state.fire_held, False
    return released


def _healer_reply(state: ServiceState, screens: ServiceScreens, world: ServiceWorld) -> None:
    if state.outcome == "leave":
        state.lines = screens.refused
        return
    donated = state.donation.amount
    if donated == 0:
        state.lines = screens.nothing_given
        return
    _, changed = heal(world.actor, donated, world.healer)
    if s16(donated) <= s16(world.healer.reply_limit):
        state.lines = screens.healer_small
    else:
        state.lines = screens.healer_healed if changed & 1 else screens.healer_plain


def _rows(rows: tuple[tuple[int, tuple[TextLine, ...]], ...], stat: int) -> tuple[TextLine, ...]:
    return next((lines for key, lines in rows if key == stat), ())


def _mystic_reply(state: ServiceState, screens: ServiceScreens, world: ServiceWorld) -> None:
    donated = state.donation.amount
    if donated == 0:
        state.lines = screens.nothing_given
        return
    if state.outcome == "leave":
        state.lines = screens.refused
        return
    result = mystic_gamble(
        world.actor, world.inventory, donated, world.rng, world.gifts, world.mystic
    )
    state.outcome = result.outcome
    found: tuple[TextLine, ...] = ()
    if result.outcome == "raise":
        found = _rows(screens.mystic_raise, result.stat)
    elif result.outcome == "lower":
        found = _rows(screens.mystic_lower, result.stat)
    elif result.outcome == "nothing":
        found = screens.mystic_nothing
    state.lines = found or screens.mystic_unchanged


def _finish(
    state: ServiceState, screens: ServiceScreens, world: ServiceWorld, *, accept: bool
) -> None:
    state.donation.close(world.actor, accept=accept)
    state.outcome = "accept" if accept else "leave"
    if state.kind == ServiceKind.HEALER:
        _healer_reply(state, screens, world)
    else:
        _mystic_reply(state, screens, world)
    state.phase, state.wait = Phase.CLOSE_WAIT, screens.close_wait


def _donation_tick(
    state: ServiceState, screens: ServiceScreens, world: ServiceWorld, fire: bool
) -> None:
    if state.wait:
        state.wait -= 1
        return
    layout = screens.donation
    region = next((r for r in layout.regions if r.contains(state.pointer_x, state.pointer_y)), None)
    if region is None or not fire:
        return
    if region.code in (LEAVE, ACCEPT):
        _finish(state, screens, world, accept=region.code == ACCEPT)
    elif region.code in (LESS, MORE) and state.donation.move(more=region.code == MORE):
        state.wait = layout.repeat_wait


def step(
    state: ServiceState,
    screens: ServiceScreens,
    world: ServiceWorld,
    inputs: tuple[int, int, bool],
    *,
    fading: bool = False,
) -> None:
    """One VBL: the pointer always moves; the routine advances unless a fade runs (§12.31)."""
    dx, dy, fire = inputs
    state.sounds.clear()
    state.pointer_x = max(0, min(POINTER_MAX_X, state.pointer_x + dx * POINTER_STEP))
    state.pointer_y = max(0, min(POINTER_MAX_Y, state.pointer_y + dy * POINTER_STEP))
    if fading or state.phase == Phase.DONE:
        return
    if state.phase == Phase.GREETING:
        if _fire_released(state, fire):
            state.phase = Phase.DONATION
            state.donation = Donation(world.actor.gold)
    elif state.phase == Phase.DONATION:
        _donation_tick(state, screens, world, fire)
    elif state.phase == Phase.CLOSE_WAIT:
        state.wait -= 1
        if state.wait <= 0:
            state.phase, state.wait = Phase.CLOSE_FIRE, 0
    elif _fire_released(state, fire):
        state.phase = Phase.DONE
