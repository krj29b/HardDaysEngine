"""Original town callers, port isolation and replay (ARCHITECTURE.md §12.15/27)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import numpy as np
import pytest

from harddaysengine.app.audit import AuditRig
from harddaysengine.app.game import GameScene, GameSession
from harddaysengine.app.input import InputFrame, PlayerInput
from harddaysengine.assets.adf import DiskSet
from harddaysengine.sim.overworld import Arrival
from harddaysengine.sim.towns import TownAction
from harddaysengine.sim.types import KnightId

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.adf


def _session(adf_dir: Path, waterdeep: bool = False) -> GameSession:
    session = GameSession(disk_set=DiskSet.from_directory(adf_dir))
    session._dispatch_arrival(Arrival(1, 26 if waterdeep else 25))
    assert session.scene == GameScene.LOCATION
    return session


def _click(session: GameSession, button: int) -> None:
    screen = session.town_screen
    assert screen is not None
    region = screen.data.buttons[button - 1]
    screen.state.pointer_x, screen.state.pointer_y = region.x, region.y
    session.step_tick(InputFrame(players={KnightId.RED: PlayerInput(fire=True)}))


def _close_sheet(session: GameSession) -> None:
    screen = session.sheet_screen
    assert screen is not None
    done = next(r for r in screen.state.regions if r.id == 7)
    screen.state.pointer_x, screen.state.pointer_y = done.x, done.y
    session.step_tick(InputFrame(players={KnightId.RED: PlayerInput(fire=True)}))


@pytest.mark.parametrize("waterdeep,button", [(False, 1), (True, 1), (False, 4)])
def test_town_sheet_call_returns_to_town_without_spending(
    adf_dir: Path,
    waterdeep: bool,
    button: int,
) -> None:
    session = _session(adf_dir, waterdeep)
    world = session.overworld
    world.spent = 3
    rng, actor = world.prng.state, world.current_actor
    hp, gold = actor.hp, actor.gold
    _click(session, button)
    assert session.sheet_screen is not None
    assert session.sheet_screen.state.number == (5 if button == 1 else 6)
    assert session.scene == GameScene.STATUS
    _close_sheet(session)
    assert session.sheet_screen is None and session.scene == GameScene.LOCATION
    screen = session.town_screen
    assert screen is not None
    assert (screen.state.pointer_x, screen.state.pointer_y) == (
        screen.data.pointer_x,
        screen.data.pointer_y,
    )
    assert (actor.hp, actor.gold, world.prng.state, world.spent) == (hp, gold, rng, 3)
    _click(session, 5)
    assert session.scene == GameScene.OVERWORLD and session.town_screen is None
    assert world.spent == world.budget


@pytest.mark.parametrize("waterdeep", [False, True])
def test_space_uses_decoded_sheet_and_exit_consumes_turn(adf_dir: Path, waterdeep: bool) -> None:
    session = _session(adf_dir, waterdeep)
    session.step_tick(InputFrame(key=32))
    assert session.sheet_screen is not None
    assert session.sheet_screen.state.number == (32 if waterdeep else 9)
    _close_sheet(session)
    assert session.scene == GameScene.LOCATION
    _click(session, 5)
    assert session.overworld.spent == session.overworld.budget
    assert session.scene == GameScene.OVERWORLD
    for _ in range(2):
        session.step_tick(InputFrame())
    assert session.overworld.current_knight_id != KnightId.RED or session.scene == GameScene.NEW_DAY


@pytest.mark.parametrize(
    "waterdeep,button,action",
    [
        (False, 2, TownAction.DICE),
        (True, 2, TownAction.DICE),
        (False, 3, TownAction.HEALER),
        (True, 3, TownAction.HEALER),
        (True, 4, TownAction.MYSTIC),
    ],
)
def test_services_route_to_explicit_host_boundary(
    adf_dir: Path,
    waterdeep: bool,
    button: int,
    action: TownAction,
) -> None:
    session = _session(adf_dir, waterdeep)
    with patch.object(session, "_open_town_service") as service:
        _click(session, button)
        service.assert_called_once_with(action)
    assert session.town_screen is not None and session.town_screen.state.pending == action


def test_unavailable_services_do_not_apply_legacy_effects(adf_dir: Path) -> None:
    session = _session(adf_dir)
    actor = session.overworld.current_actor
    hp, gold, rng = actor.hp, actor.gold, session.overworld.prng.state
    _click(session, 3)
    assert session.last_status == "HEALER UNAVAILABLE (T11.18)"
    assert (actor.hp, actor.gold, session.overworld.prng.state) == (hp, gold, rng)
    assert session.scene == GameScene.LOCATION


def test_owner_input_pointer_and_suspended_town_enter_hash(adf_dir: Path) -> None:
    traces = []
    for _ in range(2):
        session = _session(adf_dir)
        screen = session.town_screen
        assert screen is not None
        initial = screen.state.pointer_x, screen.state.pointer_y
        session.step_tick(InputFrame(players={KnightId.BLUE: PlayerInput(dx=1, fire=True)}))
        assert (screen.state.pointer_x, screen.state.pointer_y) == initial
        hashes = []
        for _ in range(12):
            session.step_tick(InputFrame(players={KnightId.RED: PlayerInput(dx=-1, dy=-1)}))
            hashes.append(session.state_hash())
        _click(session, 1)
        before = session.state_hash()
        screen.state.pointer_x += 1
        assert session.state_hash() != before
        traces.append(hashes)
    assert traces[0] == traces[1]


@pytest.mark.parametrize("waterdeep", [False, True])
def test_held_joystick_reaches_original_smith_button(adf_dir: Path, waterdeep: bool) -> None:
    session = _session(adf_dir, waterdeep)
    for _ in range(30):
        session.step_tick(InputFrame(players={KnightId.RED: PlayerInput(dy=-1)}))
    session.step_tick(InputFrame(players={KnightId.RED: PlayerInput(fire=True)}))
    assert session.scene == GameScene.STATUS and session.sheet_screen is not None
    assert session.sheet_screen.state.number == 5


@pytest.mark.gl
@pytest.mark.parametrize("waterdeep", [False, True])
def test_render_matches_loaded_picture_and_hardware_pointer_without_mutation(
    adf_dir: Path,
    waterdeep: bool,
) -> None:
    session = _session(adf_dir, waterdeep)
    for _ in range(24):
        session.step_tick(InputFrame())
    screen = session.town_screen
    assert screen is not None and session.asset_bundle is not None
    cel = session.asset_bundle.get_cel(screen.data.pointer)
    assert cel is not None
    source = cel.frames[0].pixels
    assert np.array_equal(screen.pointer_pixels, np.where(source, source + 16, 0))
    expected = screen.picture.pixels.copy()
    x, y = screen.state.pointer_x, screen.state.pointer_y
    height, width = source.shape
    target = expected[y : y + height, x : x + width]
    target[source != 0] = source[source != 0] + 16
    palette = np.array(screen.picture.palette, dtype=np.uint8)
    rig = AuditRig(adf_dir)
    try:
        before = session.state_hash()
        first = rig.capture(session, "town")
        second = rig.capture(session, "town_again")
        assert session.state_hash() == before
        assert np.array_equal(first.rgb, second.rgb)
        assert np.array_equal(first.rgb[28:228], palette[expected])
    finally:
        rig.release()
