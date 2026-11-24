"""Healer and mystic screen phases with invented records (ARCHITECTURE.md §12.31)."""

from __future__ import annotations

from dataclasses import asdict, replace

import pytest

from harddaysengine.sim.actor import Actor
from harddaysengine.sim.inventory import Inventory
from harddaysengine.sim.rng import Rng
from harddaysengine.sim.service_screen import (
    Phase,
    ServiceState,
    ServiceWorld,
    number3,
    open_service,
    step,
)
from harddaysengine.sim.service_screen_types import (
    ACCEPT,
    LEAVE,
    LESS,
    MORE,
    DonationLayout,
    ScreenRegion,
    ServiceKind,
    ServiceOpening,
    ServiceScreens,
)
from harddaysengine.sim.service_types import HealerRules, MysticRules
from harddaysengine.sim.services import mystic_gamble
from harddaysengine.sim.sound import SoundKind, SoundRequest
from harddaysengine.sim.turn_rules import GiftRules, TextLine

GIFTS = GiftRules((), (70, 71, 72) * 3, (0, 0, 0), ())

REGIONS = {
    LESS: ScreenRegion(10, 10, 5, 5, LESS),
    MORE: ScreenRegion(20, 10, 5, 5, MORE),
    ACCEPT: ScreenRegion(30, 10, 5, 5, ACCEPT),
    LEAVE: ScreenRegion(40, 10, 5, 5, LEAVE),
}


def _lines(text: str) -> tuple[TextLine, ...]:
    return (TextLine(text, 0, 100, 1),)


def _opening(portrait: int) -> ServiceOpening:
    return ServiceOpening("P", 150, 160, _lines(f"hello{portrait}"), ((7, 0), (8, 3)), portrait)


SCREENS = ServiceScreens(
    donation=DonationLayout("cels", tuple(REGIONS.values()), (), 3),
    mystic=_opening(0),
    healer=_opening(1),
    refused=_lines("refused"),
    nothing_given=_lines("nothing"),
    close_wait=4,
    healer_small=_lines("small"),
    healer_plain=_lines("plain"),
    healer_healed=_lines("healed"),
    mystic_nothing=_lines("no stat"),
    mystic_unchanged=_lines("unchanged"),
    mystic_raise=((70, _lines("+70")), (71, _lines("+71")), (72, _lines("+72"))),
    mystic_lower=((70, _lines("-70")), (71, _lines("-71"))),
)
HEALER = HealerRules(heal_price=7, life_price=11, life_cap=4, reply_limit=8)
MYSTIC = MysticRules(((8, 17), (25, -13)), 60, 6, 2, 1)


def _world(actor: Actor, seed: int = 5) -> ServiceWorld:
    return ServiceWorld(actor, Inventory(), Rng(seed), GIFTS, HEALER, MYSTIC)


def _tick(state: ServiceState, world: ServiceWorld, fire: bool = False, **kw: bool) -> None:
    step(state, SCREENS, world, (0, 0, fire), **kw)


def _at(state: ServiceState, code: int) -> None:
    state.pointer_x, state.pointer_y = REGIONS[code].x, REGIONS[code].y


def _donating(kind: ServiceKind, actor: Actor) -> tuple[ServiceState, ServiceWorld]:
    state, world = open_service(kind, 1, SCREENS), _world(actor)
    _tick(state, world, fire=True)
    _tick(state, world)
    assert state.phase == Phase.DONATION
    return state, world


def _donate(state: ServiceState, world: ServiceWorld, amount: int) -> None:
    _at(state, MORE)
    while state.donation.amount < amount:
        _tick(state, world, fire=True)


def _accept(state: ServiceState, world: ServiceWorld, code: int = ACCEPT) -> None:
    _at(state, code)
    while state.phase == Phase.DONATION:
        _tick(state, world, fire=True)


def test_numbers_are_three_left_aligned_characters_modulo_1000() -> None:
    assert [number3(v) for v in (0, 7, 40, 999, 1234)] == ["0  ", "7  ", "40 ", "999", "234"]


def test_opening_starts_the_synth_directly_and_waits_for_press_then_release() -> None:
    state = open_service(ServiceKind.HEALER, 2, SCREENS)
    assert state.sounds == [
        SoundRequest(SoundKind.START, 7, 0),
        SoundRequest(SoundKind.START, 8, 3),
    ]
    assert (state.pointer_x, state.pointer_y, state.lines) == (150, 160, _lines("hello1"))
    world = _world(Actor(gold=9))
    _tick(state, world)
    assert state.sounds == [] and state.phase == Phase.GREETING
    _tick(state, world, fire=True)
    assert state.phase == Phase.GREETING
    _tick(state, world)
    assert state.phase == Phase.DONATION and state.donation.purse == 9


def test_a_fade_freezes_the_routine_but_not_the_pointer() -> None:
    state, world = open_service(ServiceKind.MYSTIC, 1, SCREENS), _world(Actor())
    step(state, SCREENS, world, (1, -1, True), fading=True)
    step(state, SCREENS, world, (1, -1, False), fading=True)
    assert (state.pointer_x, state.pointer_y) == (154, 156) and state.phase == Phase.GREETING
    for _ in range(200):
        step(state, SCREENS, world, (1, 1, False), fading=True)
    assert (state.pointer_x, state.pointer_y) == (314, 195)


def test_a_held_button_moves_one_gold_per_repeat_wait_and_failures_do_not_wait() -> None:
    state, world = _donating(ServiceKind.HEALER, Actor(gold=2))
    _at(state, MORE)
    for _ in range(9):
        _tick(state, world, fire=True)
    assert (state.donation.purse, state.donation.amount) == (0, 2)
    _at(state, LESS)
    _tick(state, world, fire=True)
    assert state.donation.amount == 1 and state.wait == 3
    state.donation.amount, state.wait = 0, 0
    _tick(state, world, fire=True)
    assert state.wait == 0
    _at(state, MORE)
    _tick(state, world)
    assert state.donation.amount == 0


def test_healer_leave_refuses_and_keeps_the_knight_untouched() -> None:
    actor = Actor(gold=20, hp=1, hp_max=30)
    state, world = _donating(ServiceKind.HEALER, actor)
    _donate(state, world, 9)
    _accept(state, world, LEAVE)
    assert state.lines == SCREENS.refused and (actor.gold, actor.hp) == (20, 1)
    assert (state.phase, state.wait) == (Phase.CLOSE_WAIT, 4)


@pytest.mark.parametrize(
    ("donation", "curse", "reply", "hp"),
    [(0, 0, "nothing", 1), (8, 0, "small", 30), (9, 0, "plain", 30), (9, 1, "healed", 30)],
)
def test_healer_replies_follow_amount_limit_and_curse_bit(
    donation: int, curse: int, reply: str, hp: int
) -> None:
    actor = Actor(gold=20, hp=1, hp_max=30, life_loss=curse)
    state, world = _donating(ServiceKind.HEALER, actor)
    _donate(state, world, donation)
    _accept(state, world)
    assert state.lines == _lines(reply) and actor.gold == 20 - donation and actor.hp == hp


def test_mystic_leave_clears_the_donation_so_it_answers_nothing_given() -> None:
    actor = Actor(gold=20)
    state, world = _donating(ServiceKind.MYSTIC, actor)
    _donate(state, world, 5)
    rng = world.rng.state
    _accept(state, world, LEAVE)
    assert state.lines == SCREENS.nothing_given and world.rng.state == rng and actor.gold == 20


@pytest.mark.parametrize("stat", [3, 5])
@pytest.mark.parametrize("index", range(1, 40))
def test_mystic_replies_come_from_the_tables_by_outcome_and_stat(index: int, stat: int) -> None:
    seed = (index * 0x9E3779B9) & 0xFFFFFFFF
    actor = Actor(gold=30, strength=stat, constitution=stat, endurance=stat, hp=40, hp_max=40)
    twin, twin_rng = replace(actor), Rng(seed)
    expected = mystic_gamble(twin, Inventory(), 6, twin_rng, GIFTS, MYSTIC)
    state, _ = _donating(ServiceKind.MYSTIC, actor)
    world = _world(actor, seed)
    _donate(state, world, 6)
    _accept(state, world)
    table = {"raise": SCREENS.mystic_raise, "lower": SCREENS.mystic_lower}.get(expected.outcome)
    lines = dict(table or ()).get(expected.stat, SCREENS.mystic_unchanged)
    if expected.outcome == "nothing":
        lines = SCREENS.mystic_nothing
    assert state.lines == lines and world.rng.state == twin_rng.state
    assert asdict(actor) == asdict(replace(twin, gold=twin.gold - 6))


def test_close_waits_then_needs_a_fresh_press_and_release() -> None:
    state, world = _donating(ServiceKind.HEALER, Actor(gold=3))
    _accept(state, world, LEAVE)
    for _ in range(3):
        _tick(state, world, fire=True)
    assert state.phase == Phase.CLOSE_WAIT
    _tick(state, world, fire=True)
    assert state.phase == Phase.CLOSE_FIRE
    _tick(state, world)
    assert state.phase == Phase.CLOSE_FIRE
    _tick(state, world, fire=True)
    _tick(state, world)
    assert state.phase == Phase.DONE
    _tick(state, world, fire=True)
    assert state.phase == Phase.DONE
