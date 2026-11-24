"""Original village caller, independent operands and replay/render invariance (§12.37)."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING
from unittest.mock import patch

import numpy as np
import pygame
import pytest

from harddaysengine.app.audit import AuditRig
from harddaysengine.app.game import GameScene, GameSession
from harddaysengine.app.input import InputFrame, InputRouter, PlayerInput
from harddaysengine.app.village_audit import village_exit, village_temple
from harddaysengine.assets.adf import DiskSet
from harddaysengine.assets.hunk import parse_hunk_executable
from harddaysengine.assets.village_data import parse_village_rules
from harddaysengine.sim.overworld import Arrival
from harddaysengine.sim.types import KnightId
from harddaysengine.sim.village import VillagePhase, reward

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.adf


def _session(adf_dir: Path, kid: KnightId = KnightId.RED) -> GameSession:
    session = GameSession(disk_set=DiskSet.from_directory(adf_dir), players=(kid,))
    assert session.overworld.current_knight_id == kid
    session.actors[kid].lives, session.actors[kid].hp = 2, 7
    return session


def test_rules_match_independent_disk_instructions(adf_dir: Path) -> None:
    exe = parse_hunk_executable("mog", DiskSet.from_directory(adf_dir).read_file("mog"))
    rules = parse_village_rules(exe)
    code = exe.sections[0].data
    assert rules.life_cap == int.from_bytes(code[0x18B8:0x18BA], "big")
    assert rules.life_gain == int.from_bytes(code[0x18C0:0x18C2], "big")
    assert code[0x18BC:0x18BE] == b"\x6c\x06"  # signed BGE
    assert rules.sheet == code[0x18C5]
    assert rules.fade_period == code[0x8B2F]
    assert rules.fade_wait == int.from_bytes(code[0x8B38:0x8B3C], "big")


@pytest.mark.parametrize("kind", (21, 22, 23, 24))
@pytest.mark.parametrize("kid", list(KnightId))
def test_all_village_colours_reward_active_visitor_once_and_spend_after_temple(
    adf_dir: Path, kind: int, kid: KnightId
) -> None:
    session = _session(adf_dir, kid)
    world, actor = session.overworld, session.actors[kid]
    before = asdict(actor)
    others = [asdict(row) for index, row in enumerate(session.actors) if index != kid]
    inventories, rng = {k: asdict(v) for k, v in session.inventories.items()}, world.prng.state
    world.dragon_active = True
    session._map_key = 31
    session._dispatch_arrival(Arrival(1, kind))
    visit = session.village_visit
    assert visit is not None and visit.knight == int(kid) + 1
    assert visit.phase == VillagePhase.MAP_OUT and session.scene == GameScene.VILLAGE
    assert not world.dragon_active and not world.colors.active and session._map_key == 0
    assert actor.lives == 2
    with patch("harddaysengine.app.game.village_reward", wraps=reward) as apply:
        village_temple(session)
        for _ in range(10):
            session.step_tick(InputFrame())
        apply.assert_called_once()
    sheet = session.sheet_screen
    assert sheet is not None and sheet.state.number == 9 and sheet.state.knight == int(kid) + 1
    assert actor.lives == 3 and actor.hp == before["hp"] and world.spent < world.budget
    assert inventories == {k: asdict(v) for k, v in session.inventories.items()}
    assert others == [asdict(row) for index, row in enumerate(session.actors) if index != kid]
    assert world.prng.state == rng
    village_exit(session)
    assert session.village_visit is None and session.sheet_screen is None
    assert world.spent == world.budget and session.scene == GameScene.OVERWORLD


@pytest.mark.parametrize("lives,expected", [(3, 3), (4, 4), (127, 127), (128, 129), (255, 0)])
def test_signed_life_boundaries_use_original_caller(
    adf_dir: Path, lives: int, expected: int
) -> None:
    session = _session(adf_dir)
    session.actors[0].lives = lives
    session._dispatch_arrival(Arrival(1, 21))
    village_temple(session)
    assert session.actors[0].lives == expected and session.actors[0].hp == 7


def test_inputs_cannot_skip_entry_or_exit_wait_and_only_owner_closes_temple(adf_dir: Path) -> None:
    session = _session(adf_dir)
    session._dispatch_arrival(Arrival(1, 21))
    visit = session.village_visit
    assert visit is not None
    held = InputFrame(players={KnightId.RED: PlayerInput(fire=True)})
    for _ in range(visit.rules.fade_wait - 1):
        session.step_tick(held)
        assert session.sheet_screen is None and session.actors[0].lives == 2
    village_temple(session)
    sheet = session.sheet_screen
    assert sheet is not None
    done = next(row for row in sheet.state.regions if row.id == 7)
    sheet.state.pointer_x, sheet.state.pointer_y = done.x, done.y
    session.step_tick(InputFrame(players={KnightId.YELLOW: PlayerInput(fire=True)}))
    assert visit.phase == VillagePhase.TEMPLE
    session.step_tick(held)
    assert visit.phase == VillagePhase.TEMPLE_OUT
    for _ in range(visit.rules.fade_wait - 1):
        session.step_tick(held)
        assert session.scene == GameScene.STATUS
    session.step_tick(InputFrame())
    assert session.scene == GameScene.OVERWORLD


def test_full_visit_replays_identically_including_sheet_exit(adf_dir: Path) -> None:
    traces = []
    for _ in range(2):
        session = _session(adf_dir)
        session._dispatch_arrival(Arrival(1, 24))
        trace = []
        for tick in range(180):
            sheet = session.sheet_screen
            if sheet is not None:
                done = next(row for row in sheet.state.regions if row.id == 7)
                sheet.state.pointer_x, sheet.state.pointer_y = done.x, done.y
            session.step_tick(InputFrame(players={KnightId.RED: PlayerInput(fire=tick == 90)}))
            trace.append(session.state_hash())
        assert session.village_visit is None
        traces.append(trace)
    assert traces[0] == traces[1]


@pytest.mark.gl
def test_rendering_map_temple_and_black_wait_tail_is_pure(adf_dir: Path) -> None:
    rig = AuditRig(adf_dir)
    try:
        session = _session(adf_dir)
        session._dispatch_arrival(Arrival(1, 21))
        before = session.state_hash()
        first, second = rig.capture(session, "entry"), rig.capture(session, "entry_again")
        assert before == session.state_hash() and np.array_equal(first.rgb, second.rgb)
        visit = session.village_visit
        assert visit is not None
        for _ in range(visit.rules.fade_wait - 1):
            session.step_tick(InputFrame())
        assert not session.palette_fx.is_fading and visit.wait == 1
        assert not np.any(rig.capture(session, "black_entry_tail").rgb)
        village_temple(session)
        before = session.state_hash()
        first, second = rig.capture(session, "temple"), rig.capture(session, "temple_again")
        assert before == session.state_hash() and np.array_equal(first.rgb, second.rgb)
        assert len(np.unique(first.rgb.reshape(-1, 3), axis=0)) > 3
    finally:
        rig.release()


@pytest.mark.parametrize("via_pick", [False, True])
def test_held_gamepad_fire_from_knight_select_does_not_enter_home_village(
    adf_dir: Path, via_pick: bool
) -> None:
    """Holding gamepad fire while starting a game must not enter the home village (§11.4, §13.9)."""
    disks = DiskSet.from_directory(adf_dir)
    session = GameSession(disk_set=disks, start_scene=GameScene.KNIGHT_SELECT)
    if via_pick:
        session.knight_pick_active = True
        session.knight_pick_slot = 0  # SIR GODBER -> KnightId.BLUE
    router = InputRouter(init_joysticks=False)
    fire_down = pygame.event.Event(pygame.JOYBUTTONDOWN, joy=0, button=0)
    fire_up = pygame.event.Event(pygame.JOYBUTTONUP, joy=0, button=0)

    router.process_pygame_event(fire_down)
    session.step_tick(router.sample_frame())
    assert session.scene == GameScene.OVERWORLD
    expected_knight = KnightId.BLUE if via_pick else KnightId.RED
    assert session.overworld.current_knight_id == expected_knight

    # Save/restore while the map fire latch is active must keep identical state and behavior.
    checkpoint = session.campaign_checkpoint()
    assert checkpoint.map_fire_latch is True
    restored = GameSession.from_campaign_checkpoint(checkpoint, disks)
    assert restored.state_hash() == session.state_hash()

    for _ in range(60):
        frame = router.sample_frame()
        session.step_tick(frame)
        restored.step_tick(frame)
        assert restored.state_hash() == session.state_hash()
    assert not session.palette_fx.is_fading
    assert session.scene == GameScene.OVERWORLD
    assert session.village_visit is None and session.sheet_screen is None
    assert session.overworld.spent == 0

    router.process_pygame_event(fire_up)
    session.step_tick(router.sample_frame())
    router.process_pygame_event(fire_down)
    for _ in range(8):
        session.step_tick(router.sample_frame())
        if session.scene != GameScene.OVERWORLD:
            break
    assert session.scene == GameScene.VILLAGE and session.village_visit is not None
