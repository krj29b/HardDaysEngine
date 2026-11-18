"""The dice house on the user's disks (ARCHITECTURE.md §12.32)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pytest

from harddaysengine.app.audit import AuditRig
from harddaysengine.app.game import GameScene, GameSession
from harddaysengine.app.input import InputFrame, PlayerInput
from harddaysengine.assets.adf import DiskSet
from harddaysengine.assets.dice_house_data import parse_dice_house
from harddaysengine.assets.hunk import parse_hunk_executable
from harddaysengine.sim.dice_house import DiceHouse, DicePhase
from harddaysengine.sim.types import KnightId
from tests.adf.test_service_screens_adf import _scan

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.adf

KNIGHT = KnightId.RED
DICE_BUTTON = 2


@pytest.fixture(scope="module")
def code(adf_dir: Path) -> bytes:
    exe = parse_hunk_executable("mog", DiskSet.from_directory(adf_dir).read_file("mog"))
    return exe.sections[0].data


@pytest.fixture(scope="module")
def house(adf_dir: Path) -> DiceHouse:
    disks = DiskSet.from_directory(adf_dir)
    return parse_dice_house(parse_hunk_executable("mog", disks.read_file("mog")))


def test_regions_sounds_and_dice_match_an_independent_scan(code: bytes, house: DiceHouse) -> None:
    stores = _scan(code, 0xA3FA, 0xA4CC, (0x317C, None, None))
    assert [v for v, d in stores if d == 14] == [r.y for r in house.regions]
    sounds = _scan(code, 0xA4EC, 0xA50A, (0x303C, None, 0x4EB9))
    assert [s[0] for s in sounds] == [seq for seq, _ in house.sounds]
    assert [ch for _, ch in house.sounds] == [0, 1, 2]
    draws = _scan(code, 0xA76A, 0xA7C0, (0x323C, None, 0x343C, None, 0x4EB9))
    assert [tuple(d) for d in draws] == list(house.dice_at)
    assert [r.kind for r in house.regions] == [1] * 5 + [2]
    assert [r.code for r in house.regions[:5]] == [1, 2, 3, 4, 5]
    assert {kind for kind, _ in house.ramps} == {0, 1, 2, 3}
    assert all(len(words) == 5 for _, words in house.ramps)


def _tick(session: GameSession, *, fire: bool = False, count: int = 1) -> None:
    for _ in range(count):
        session.step_tick(InputFrame(players={KNIGHT: PlayerInput(fire=fire)}))


def _session(adf_dir: Path, gold: int) -> GameSession:
    session = GameSession(disk_set=DiskSet.from_directory(adf_dir))
    session.actors[KNIGHT].gold = gold
    session._open_town(KNIGHT, waterdeep=False)
    while session.palette_fx.is_fading:
        _tick(session)
    town = session.town_screen
    assert town is not None
    region = next(b for b in town.data.buttons if b.id == DICE_BUTTON)
    town.state.pointer_x, town.state.pointer_y = region.x, region.y
    _tick(session, fire=True)
    while session.palette_fx.is_fading:
        _tick(session)
    return session


def test_no_gold_never_opens_the_dice_house(adf_dir: Path) -> None:
    session = _session(adf_dir, 0)
    assert session.scene == GameScene.LOCATION and session.dice_screen is None


def _bet_and_roll(session: GameSession, bet: int) -> None:
    screen = session.dice_screen
    assert screen is not None
    region = next(r for r in screen.run.house.regions if r.kind == 1 and r.code == bet)
    screen.run.state.pointer_x, screen.run.state.pointer_y = region.x, region.y
    for _ in range(60):
        if screen.run.state.throw:
            break
        _tick(session, fire=True)
    for _ in range(600):
        if screen.run.state.phase != DicePhase.PLAY:
            break
        _tick(session)
    assert screen.run.state.phase == DicePhase.ROLL_WAIT


@pytest.mark.parametrize("seed", [0x1111, 0x2222, 0x3333, 0x4444, 0x5555])
def test_a_round_pays_by_the_decoded_table(adf_dir: Path, seed: int) -> None:
    session = _session(adf_dir, 40)
    session.overworld.prng.state = seed
    _bet_and_roll(session, 4)
    screen, rules = session.dice_screen, session._services()[1][2]
    assert screen is not None
    dice = tuple(sorted(screen.run.state.dice))
    multiplier = next((pay for a, b, c, pay in rules.rows if (a, b, c) == dice), 0)
    assert session.actors[KNIGHT].gold == 40 - 4 + 4 * multiplier
    house = screen.run.house
    first = (house.won if multiplier else house.lost)[0]
    assert screen.run.state.lines[0].text.startswith(first.text)
    assert screen.run.state.lines[0].text.endswith(house.suffix)


def test_dice_visits_replay_to_identical_hashes(adf_dir: Path) -> None:
    traces = []
    for _ in range(2):
        session = _session(adf_dir, 40)
        session.overworld.prng.state = 0xBEEF
        trace = [session.state_hash()]
        _bet_and_roll(session, 2)
        trace.append(session.state_hash())
        _tick(session, count=25)
        _tick(session, fire=True)
        _tick(session, count=3)
        trace.append(session.state_hash())
        traces.append(trace)
    assert traces[0] == traces[1] and len(set(traces[0])) == 3


@pytest.mark.gl
def test_dice_render_is_pure_in_rounds_and_rolls(adf_dir: Path) -> None:
    session = _session(adf_dir, 40)
    rig = AuditRig(adf_dir)
    try:
        for action in (lambda: _tick(session, count=8), lambda: _bet_and_roll(session, 1)):
            action()
            before = session.state_hash()
            first, second = rig.capture(session, "a"), rig.capture(session, "b")
            assert session.state_hash() == before and np.array_equal(first.rgb, second.rgb)
    finally:
        rig.release()
