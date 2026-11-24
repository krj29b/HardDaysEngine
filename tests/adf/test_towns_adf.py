"""Original town callers, port isolation and replay (ARCHITECTURE.md §12.15/27)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pytest

from harddaysengine.app.audit import AuditRig
from harddaysengine.app.game import GameScene, GameSession
from harddaysengine.app.input import InputFrame, PlayerInput
from harddaysengine.assets.adf import DiskSet
from harddaysengine.sim.overworld import Arrival
from harddaysengine.sim.service_screen import Phase
from harddaysengine.sim.towns import TownAction, TownEntry
from harddaysengine.sim.types import KnightId

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.adf


def _session(adf_dir: Path, waterdeep: bool = False) -> GameSession:
    session = GameSession(disk_set=DiskSet.from_directory(adf_dir))
    session._dispatch_arrival(Arrival(1, 26 if waterdeep else 25))
    assert session.scene == GameScene.LOCATION
    _settle(session)
    return session


def _settle(session: GameSession) -> None:
    for _ in range(128):
        if not session.palette_fx.is_fading:
            return
        session.step_tick(InputFrame())
    raise AssertionError("town fade did not finish")


def _click(session: GameSession, button: int) -> None:
    _settle(session)
    screen = session.town_screen
    assert screen is not None
    region = screen.data.buttons[button - 1]
    screen.state.pointer_x, screen.state.pointer_y = region.x, region.y
    session.step_tick(InputFrame(players={KnightId.RED: PlayerInput(fire=True)}))


def _close_sheet(session: GameSession) -> None:
    _settle(session)
    screen = session.sheet_screen
    assert screen is not None
    done = next(r for r in screen.state.regions if r.id == 7)
    screen.state.pointer_x, screen.state.pointer_y = done.x, done.y
    session.step_tick(InputFrame(players={KnightId.RED: PlayerInput(fire=True)}))


def _finish_town_exit(session: GameSession) -> None:
    assert session.asset_bundle is not None
    retained = session.town_screen
    for _ in range(session.asset_bundle.get_scene_sounds().fade_wait - 1):
        session.step_tick(InputFrame())
        assert session.town_screen is retained and session.scene == GameScene.LOCATION
    session.step_tick(InputFrame())


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
    _finish_town_exit(session)
    assert session.scene == GameScene.OVERWORLD and session.town_screen is None
    assert world.spent == world.budget


@pytest.mark.parametrize("waterdeep,button", [(False, 1), (True, 1), (False, 4)])
def test_held_fire_keeps_town_sheet_open_with_inherited_pointer(
    adf_dir: Path, waterdeep: bool, button: int
) -> None:
    """A human press spans many VBLs; the sheet must not close under it (§12.2)."""
    session = _session(adf_dir, waterdeep)
    town = session.town_screen
    assert town is not None
    region = town.data.buttons[button - 1]
    town.state.pointer_x = region.x + region.width // 2
    town.state.pointer_y = region.y + region.height // 2
    held = InputFrame(players={KnightId.RED: PlayerInput(fire=True)})
    session.step_tick(held)
    sheet = session.sheet_screen
    assert sheet is not None
    assert (sheet.state.pointer_x, sheet.state.pointer_y) == (
        town.state.pointer_x,
        town.state.pointer_y,
    )
    for _ in range(60):
        session.step_tick(held)
    assert session.scene == GameScene.STATUS and session.sheet_screen is sheet
    assert not sheet.state.done


@pytest.mark.parametrize("waterdeep", [False, True])
def test_space_uses_decoded_sheet_and_exit_consumes_turn(adf_dir: Path, waterdeep: bool) -> None:
    session = _session(adf_dir, waterdeep)
    session.step_tick(InputFrame(key=32))
    assert session.sheet_screen is not None
    assert session.sheet_screen.state.number == 9
    _close_sheet(session)
    assert session.scene == GameScene.LOCATION
    _click(session, 5)
    _finish_town_exit(session)
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
def test_services_open_real_screens_and_return_to_the_unspent_town(
    adf_dir: Path,
    waterdeep: bool,
    button: int,
    action: TownAction,
) -> None:
    session = _session(adf_dir, waterdeep)
    session.overworld.current_actor.gold = 40
    session.overworld.spent = 3
    budget = session.overworld.budget
    _click(session, button)
    assert session.town_screen is not None and session.town_screen.state.pending == action
    if action == TownAction.DICE:
        assert session.scene == GameScene.DICE and session.dice_screen is not None
        _leave_dice(session)
    else:
        assert session.scene == GameScene.SERVICE and session.service_screen is not None
        _leave_service(session)
    _settle(session)
    assert session.scene == GameScene.LOCATION
    assert session.overworld.spent == 3 and session.overworld.budget == budget
    town = session.town_screen
    assert town.state.entry == TownEntry.READY and town.state.pending == TownAction.NONE
    assert (town.state.pointer_x, town.state.pointer_y) == (
        town.data.pointer_x,
        town.data.pointer_y,
    )
    _click(session, 5)
    _finish_town_exit(session)
    assert session.scene == GameScene.OVERWORLD and session.overworld.spent == budget


def _leave_dice(session: GameSession) -> None:
    _settle(session)
    screen = session.dice_screen
    assert screen is not None
    leave = next(r for r in screen.run.house.regions if r.kind == 2)
    screen.run.state.pointer_x, screen.run.state.pointer_y = leave.x, leave.y
    for _ in range(120):
        session.step_tick(InputFrame(players={KnightId.RED: PlayerInput(fire=True)}))
        if session.scene == GameScene.LOCATION:
            return
    raise AssertionError("dice leave did not return to town")


def _leave_service(session: GameSession) -> None:
    _settle(session)
    screen = session.service_screen
    assert screen is not None
    session.step_tick(InputFrame(players={KnightId.RED: PlayerInput(fire=True)}))
    session.step_tick(InputFrame())
    assert screen.state.phase == Phase.DONATION
    leave = next(r for r in screen.screens.donation.regions if r.code == 2)
    screen.state.pointer_x, screen.state.pointer_y = leave.x, leave.y
    session.step_tick(InputFrame(players={KnightId.RED: PlayerInput(fire=True)}))
    for _ in range(screen.screens.close_wait):
        session.step_tick(InputFrame())
    session.step_tick(InputFrame(players={KnightId.RED: PlayerInput(fire=True)}))
    session.step_tick(InputFrame())
    assert session.asset_bundle is not None
    for _ in range(session.asset_bundle.get_scene_sounds().fade_wait):
        session.step_tick(InputFrame())
    assert session.service_screen is None


def test_the_dice_house_without_gold_returns_to_the_town_without_effects(adf_dir: Path) -> None:
    session = _session(adf_dir)
    actor = session.overworld.current_actor
    actor.gold = 0
    hp, gold, rng = actor.hp, actor.gold, session.overworld.prng.state
    _click(session, 2)
    assert session.dice_screen is None
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
