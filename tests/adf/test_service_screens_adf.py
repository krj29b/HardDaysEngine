"""Healer and mystic screens on the user's disks (ARCHITECTURE.md §12.31)."""

from __future__ import annotations

from dataclasses import asdict, replace
from typing import TYPE_CHECKING

import numpy as np
import pytest

from harddaysengine.app.audit import AuditRig
from harddaysengine.app.game import GameScene, GameSession
from harddaysengine.app.input import InputFrame, PlayerInput
from harddaysengine.assets.adf import DiskSet
from harddaysengine.assets.hunk import parse_hunk_executable
from harddaysengine.assets.service_screen_data import parse_service_screens
from harddaysengine.sim.rng import Rng
from harddaysengine.sim.service_screen import Phase
from harddaysengine.sim.service_screen_types import (
    ACCEPT,
    LEAVE,
    MORE,
    CelDraw,
    NumberDraw,
    ServiceScreens,
)
from harddaysengine.sim.services import mystic_gamble
from harddaysengine.sim.types import KnightId

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.adf

KNIGHT = KnightId.RED
HEALER_BUTTON, MYSTIC_BUTTON = 3, 4


@pytest.fixture(scope="module")
def code(adf_dir: Path) -> bytes:
    return (
        parse_hunk_executable("mog", DiskSet.from_directory(adf_dir).read_file("mog"))
        .sections[0]
        .data
    )


@pytest.fixture(scope="module")
def screens(adf_dir: Path) -> ServiceScreens:
    disks = DiskSet.from_directory(adf_dir)
    return parse_service_screens(parse_hunk_executable("mog", disks.read_file("mog")))


def _words(code: bytes, start: int, stop: int) -> list[int]:
    return [int.from_bytes(code[p : p + 2], "big") for p in range(start, stop, 2)]


def _scan(code: bytes, start: int, stop: int, pattern: tuple[int | None, ...]) -> list[list[int]]:
    """Every match of ``pattern`` (``None`` = any word) on word boundaries, as captured words."""
    words, found = _words(code, start, stop), []
    for i in range(len(words) - len(pattern) + 1):
        window = words[i : i + len(pattern)]
        if all(p is None or p == w for p, w in zip(pattern, window, strict=True)):
            found.append([w for p, w in zip(pattern, window, strict=True) if p is None])
    return found


def test_regions_and_synth_starts_match_an_independent_scan(
    code: bytes, screens: ServiceScreens
) -> None:
    stores = _scan(code, 0x9F70, 0xA028, (0x317C, None, None))
    assert {d for _, d in stores} == {4, 6, 12, 14, 20, 22}
    codes = [v for v, d in stores if d == 20]
    assert [r.code for r in screens.donation.regions] == codes
    for opening, (start, stop) in (
        (screens.mystic, (0x9B66, 0x9C30)),
        (screens.healer, (0x9DD0, 0x9E94)),
    ):
        sounds = _scan(code, start, stop, (0x303C, None, 0x323C, None, 0x4EB9))
        assert [tuple(s) for s in sounds] == list(opening.sounds)
        assert opening.pointer_x == _scan(code, start, stop, (0x33FC, None))[0][0]


def test_the_redraw_covers_every_button_and_both_amounts(screens: ServiceScreens) -> None:
    frames = [d.frame for d in screens.donation.draws if isinstance(d, CelDraw)]
    assert frames.count(None) == 2 and sorted(f for f in frames if f is not None) == [2, 3, 4, 5]
    assert sorted(d.purse for d in screens.donation.draws if isinstance(d, NumberDraw)) == [
        False,
        True,
    ]
    assert {r.code for r in screens.donation.regions} == {2, 3, 4, 5}
    assert (screens.mystic.portrait, screens.healer.portrait) == (0, 1)
    assert [s for s, _ in screens.mystic_raise] == [s for s, _ in screens.mystic_lower]
    for lines in (
        screens.refused,
        screens.nothing_given,
        screens.healer_small,
        screens.healer_plain,
    ):
        assert lines and all(line.text for line in lines)


def _session(adf_dir: Path, button: int, gold: int) -> GameSession:
    session = GameSession(disk_set=DiskSet.from_directory(adf_dir))
    actor = session.actors[KNIGHT]
    actor.gold, actor.hp, actor.hp_max = gold, 4, 30
    session._open_town(KNIGHT, waterdeep=True)
    _settle(session)
    town = session.town_screen
    assert town is not None
    region = next(b for b in town.data.buttons if b.id == button)
    town.state.pointer_x, town.state.pointer_y = region.x, region.y
    _tick(session, fire=True)
    assert session.scene == GameScene.SERVICE
    _settle(session)
    _tick(session, fire=True)
    _tick(session)
    assert session.service_screen is not None
    assert session.service_screen.state.phase == Phase.DONATION
    return session


def _tick(session: GameSession, *, fire: bool = False, count: int = 1) -> None:
    for _ in range(count):
        session.step_tick(InputFrame(players={KNIGHT: PlayerInput(fire=fire)}))


def _settle(session: GameSession) -> None:
    while session.palette_fx.is_fading:
        _tick(session)


def _press(session: GameSession, code: int, *, ticks: int = 1) -> None:
    screen = session.service_screen
    assert screen is not None
    region = next(r for r in screen.screens.donation.regions if r.code == code)
    screen.state.pointer_x, screen.state.pointer_y = region.x, region.y
    _tick(session, fire=True, count=ticks)


def _end(session: GameSession, code: int) -> None:
    """Hold fire on accept or leave until the screen leaves the donation loop (§12.31)."""
    screen = session.service_screen
    assert screen is not None
    for _ in range(4):
        _press(session, code)
        if screen.state.phase != Phase.DONATION:
            return
    raise AssertionError("the donation screen did not end")


def _close(session: GameSession, screens: ServiceScreens) -> None:
    _tick(session, count=screens.close_wait)
    _tick(session, fire=True)
    _tick(session)


def test_healer_heals_for_the_donation_and_returns_to_the_unspent_town(
    adf_dir: Path, screens: ServiceScreens
) -> None:
    session = _session(adf_dir, HEALER_BUTTON, 40)
    actor, spent, rng = (
        session.actors[KNIGHT],
        session.overworld.spent,
        session.overworld.prng.state,
    )
    _press(session, MORE, ticks=3 * 20 - 2)
    _end(session, ACCEPT)
    screen = session.service_screen
    assert screen is not None and screen.state.lines == screens.healer_plain
    assert (actor.gold, actor.hp) == (20, 30)
    _close(session, screens)
    assert session.scene == GameScene.LOCATION and session.service_screen is None
    assert session.overworld.spent == spent and session.overworld.prng.state == rng


def test_mystic_gamble_draws_like_the_rules_and_leave_answers_nothing_given(
    adf_dir: Path, screens: ServiceScreens
) -> None:
    session = _session(adf_dir, MYSTIC_BUTTON, 40)
    actor, world = session.actors[KNIGHT], session.overworld
    _press(session, MORE, ticks=3 * 15 - 2)
    twin, twin_rng = replace(actor, gold=25), Rng(world.prng.state)
    rules = session._services()[1]
    assert world.turn_rules is not None
    expected = mystic_gamble(
        twin, world.inventories[int(KNIGHT) + 1], 15, twin_rng, world.turn_rules.gifts, rules[1]
    )
    _end(session, ACCEPT)
    assert asdict(actor) == asdict(twin) and world.prng.state == twin_rng.state
    screen = session.service_screen
    assert screen is not None and screen.state.outcome == expected.outcome
    _close(session, screens)
    session = _session(adf_dir, MYSTIC_BUTTON, 40)
    rng = session.overworld.prng.state
    _press(session, MORE, ticks=7)
    _end(session, LEAVE)
    screen = session.service_screen
    assert screen is not None and screen.state.lines == screens.nothing_given
    assert session.actors[KNIGHT].gold == 40 and session.overworld.prng.state == rng


def test_service_visits_replay_to_identical_hashes(adf_dir: Path) -> None:
    traces = []
    for _ in range(2):
        session = _session(adf_dir, MYSTIC_BUTTON, 40)
        trace = [session.state_hash()]
        _press(session, MORE, ticks=10)
        trace.append(session.state_hash())
        _end(session, ACCEPT)
        trace.append(session.state_hash())
        traces.append(trace)
    assert traces[0] == traces[1] and len(set(traces[0])) == 3


@pytest.mark.gl
def test_service_render_is_pure_in_every_phase(adf_dir: Path, screens: ServiceScreens) -> None:
    session = _session(adf_dir, HEALER_BUTTON, 40)
    rig = AuditRig(adf_dir)
    try:
        for action in (
            lambda: None,
            lambda: _press(session, MORE, ticks=4),
            lambda: _end(session, ACCEPT),
        ):
            action()
            before = session.state_hash()
            first, second = rig.capture(session, "a"), rig.capture(session, "b")
            assert session.state_hash() == before and np.array_equal(first.rgb, second.rgb)
    finally:
        rig.release()
