"""Original meeting/protection, exchange and hoard callers on user disks (§12.40)."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING
from unittest.mock import patch

import numpy as np
import pytest

from harddaysengine.app.audit import AuditRig
from harddaysengine.app.game import GameScene, GameSession
from harddaysengine.app.input import InputFrame, PlayerInput
from harddaysengine.app.meeting_audit import (
    hoard_visit,
    meeting_click,
    meeting_done,
    meeting_input,
    meeting_ready,
    meeting_until,
    meeting_visit,
)
from harddaysengine.assets.adf import DiskSet
from harddaysengine.assets.hunk import parse_hunk_executable
from harddaysengine.assets.meeting_data import parse_meeting_data
from harddaysengine.sim.meeting import MeetingPhase
from harddaysengine.sim.overworld import Arrival
from harddaysengine.sim.rewards import transfer_loot
from harddaysengine.sim.types import KnightId

if TYPE_CHECKING:
    from pathlib import Path

    from harddaysengine.sim.sheet_types import SheetRegion

pytestmark = pytest.mark.adf


def _session(adf_dir: Path, *, two: bool = False) -> GameSession:
    session = GameSession(
        disk_set=DiskSet.from_directory(adf_dir),
        players=(KnightId.RED, KnightId.BLUE) if two else (KnightId.RED,),
    )
    for handle in (1, 2):
        session.inventories[handle].counts[:] = [0] * 12
    session.overworld.spent = 17
    return session


def _ready(session: GameSession, phase: MeetingPhase, *, win: bool = False) -> None:
    meeting_until(session, lambda: meeting_ready(session, phase), win=win)


def _region(session: GameSession, field: int, side: int = 0) -> SheetRegion:
    assert session.sheet_screen is not None
    return next(
        r
        for r in session.sheet_screen.state.regions
        if r.field == field and r.side == side and not r.id
    )


def _protected(adf_dir: Path) -> GameSession:
    session = _session(adf_dir, two=True)
    session.inventories[2].counts[9] = 1
    session._dispatch_arrival(Arrival(2, 1))
    _ready(session, MeetingPhase.PROTECT)
    session.step_tick(InputFrame(players={KnightId.BLUE: PlayerInput(fire=True)}))
    session.step_tick(InputFrame())
    _ready(session, MeetingPhase.PROTECT_SHEET)
    return session


def _returned(session: GameSession) -> None:
    meeting_until(session, lambda: session.scene == GameScene.OVERWORLD)
    assert session.meeting_screen is None and session.sheet_screen is None


def test_original_operands_dialogue_and_name_substitution_are_independently_checked(
    adf_dir: Path,
) -> None:
    exe = parse_hunk_executable("mog", DiskSet.from_directory(adf_dir).read_file("mog"))
    data, code = parse_meeting_data(exe), exe.sections[0].data
    assert data.rules.computer_kind == int.from_bytes(code[0xC0C:0xC10], "big")
    assert data.rules.progress == int.from_bytes(code[0xBF4:0xBF6], "big")
    assert data.rules.protection_slot == int.from_bytes(code[0xC22:0xC24], "big")
    assert data.rules.hoard_sheet == code[0x1247]
    pointer = int.from_bytes(code[0xC3E:0xC42], "big")
    raw = exe.sections[4].data
    named = int.from_bytes(code[0xC2A:0xC2E], "big")
    for index, line in enumerate(data.message):
        start = int.from_bytes(raw[pointer : pointer + 4], "big")
        assert line.text == raw[start : raw.index(0, start)].decode("ascii")
        if index == data.name_row:
            assert start == named
        pointer = int.from_bytes(raw[pointer + 10 : pointer + 14], "big")
    assert pointer == 0
    session = _protected(adf_dir)
    screen = session.meeting_screen
    assert screen is not None
    assert screen.lines[data.name_row].text == session.overworld.names[1]
    assert screen.data.message == data.message


@pytest.mark.parametrize("success", [False, True])
def test_only_defender_uses_protection_and_failure_confuses_correct_fighter(
    adf_dir: Path, success: bool
) -> None:
    session = _protected(adf_dir)
    world, inv = session.overworld, session.inventories[2]
    screen = session.sheet_screen
    assert screen is not None and screen.state.knight == 2 and screen.state.last_item == 65535
    region = _region(session, 18)
    screen.state.pointer_x, screen.state.pointer_y = region.x + 1, region.y + 1
    session.step_tick(InputFrame(players={KnightId.RED: PlayerInput(fire=True)}))
    assert inv.byte(18) == 1
    world.prng.state = 0x4000 if success else 0
    meeting_click(session, region)
    assert inv.byte(18) == 0
    if success:
        _returned(session)
        assert session.active_encounter is None
    else:
        meeting_until(session, lambda: session.scene == GameScene.COMBAT)
        assert session.active_encounter is not None
        fight = session.active_encounter.runtime.fight
        assert fight.confused == 2 and fight.confused_on
        assert not any(actor.type == 16 for actor in session.actors[:2])
    assert world.current_knight_id == KnightId.RED and world.spent == 17


def test_protection_message_requires_defender_press_release_and_cancel_continues_duel(
    adf_dir: Path,
) -> None:
    session = _session(adf_dir, two=True)
    session.inventories[2].counts[9] = 1
    session._dispatch_arrival(Arrival(2, 1))
    _ready(session, MeetingPhase.PROTECT)
    for fire in (True, False):
        session.step_tick(InputFrame(players={KnightId.RED: PlayerInput(fire=fire)}))
    assert session.sheet_screen is None
    session.step_tick(InputFrame(players={KnightId.BLUE: PlayerInput(fire=True)}))
    assert session.sheet_screen is None
    session.step_tick(InputFrame())
    _ready(session, MeetingPhase.PROTECT_SHEET)
    meeting_done(session)
    meeting_until(session, lambda: session.scene == GameScene.COMBAT)
    assert session.active_encounter is not None
    assert not session.active_encounter.runtime.fight.confused_on
    assert session.inventories[2].byte(18) == 1


@pytest.mark.parametrize("defender", [False, True])
def test_natural_human_winner_owns_loot_sheet_without_changing_turn(
    adf_dir: Path, defender: bool
) -> None:
    session = _session(adf_dir)
    meeting_visit(session, defender_wins=defender)
    original = session.overworld.current_knight_id
    _ready(session, MeetingPhase.RESULT_SHEET, win=True)
    sheet = session.sheet_screen
    assert sheet is not None and sheet.state.number == 1
    assert sheet.state.knight == 1 and sheet.state.other == 2
    assert session.last_defeat_mask == (1 if defender else 2)
    assert session.actors[1].lives == 2 and session.actors[0].progress == 0
    assert session.overworld.current_knight_id == original and session.overworld.spent == 17
    meeting_done(session)
    _returned(session)
    assert session.overworld.current_knight_id == original and session.overworld.spent == 17


def test_natural_computer_winner_gets_one_original_transfer_and_no_human_sheet(
    adf_dir: Path,
) -> None:
    session = _session(adf_dir)
    meeting_visit(session)
    actor = session.actors[0]
    actor.hp = actor.hp_max = 20
    actor.gold = 40
    session.actors[1].gold = 0
    session._sync_legacy_actor(KnightId.RED)
    with patch("harddaysengine.app.game.transfer_loot", wraps=transfer_loot) as transfer:
        _returned(session)
        transfer.assert_called_once()
    assert session.last_defeat_mask == 1 and actor.lives == 2
    assert session.actors[1].progress == 1 and session.actors[1].gold == 20


@pytest.mark.parametrize("dead", [False, True])
def test_frogged_or_dead_loser_loot_scope_and_living_take_becomes_temple(
    adf_dir: Path, dead: bool
) -> None:
    session = _session(adf_dir)
    other = session.actors[1]
    other.lives, other.frog_days = (0, 0) if dead else (3, 1)
    session.inventories[2].counts[0] = 2
    session._dispatch_arrival(Arrival(2, 33 if dead else 1))
    _ready(session, MeetingPhase.RESULT_SHEET)
    assert session.active_encounter is None
    meeting_click(session, _region(session, 0, 1))
    assert session.inventories[1].byte(0) == 1
    assert session.sheet_screen is not None
    assert session.sheet_screen.state.number == (1 if dead else 9)
    if dead:
        meeting_click(session, _region(session, 0, 1))
        assert session.inventories[1].byte(0) == 2 and session.inventories[2].byte(0) == 0
    meeting_done(session)
    _returned(session)
    assert session.overworld.spent == 17


def test_acquisition_and_wyrm_remain_in_meeting_sheet_and_commit_original_target(
    adf_dir: Path,
) -> None:
    session = _session(adf_dir)
    session.actors[1].lives, session.actors[1].frog_days = 3, 1
    session.inventories[1].counts[7] = session.inventories[1].counts[8] = 1
    session.inventories[2].counts[0] = 2
    session._dispatch_arrival(Arrival(2, 1))
    _ready(session, MeetingPhase.RESULT_SHEET)
    # One take from the living, frogged loser naturally changes sheet1 to sheet9.
    meeting_click(session, _region(session, 0, 1))
    assert session.sheet_screen is not None and session.sheet_screen.state.number == 9
    meeting_click(session, _region(session, 14))
    assert session.sheet_screen is not None and session.sheet_screen.state.number == 8
    other = session.sheet_screen.state.other
    session.inventories[other].counts[0] = 2
    meeting_click(session, _region(session, 0, 1))
    assert session.sheet_screen.state.number == 9
    meeting_click(session, _region(session, 16))
    assert session.sheet_screen.state.number == 11
    next_button = next(r for r in session.sheet_screen.state.regions if r.id == 8)
    before = session.sheet_screen.state.other
    meeting_click(session, next_button)
    assert session.sheet_screen.state.other != before
    target = session.sheet_screen.state.other
    meeting_done(session)
    assert session.dragon_actor.engaged_with == target
    _returned(session)


def test_mutual_defeat_opens_initiator_temple_without_any_loot_award(adf_dir: Path) -> None:
    session = _session(adf_dir, two=True)
    session._dispatch_arrival(Arrival(2, 1))
    meeting_until(session, lambda: session.scene == GameScene.COMBAT)
    active = session.active_encounter
    assert active is not None
    lives = (session.actors[0].lives, session.actors[1].lives)
    session.actors[0].hp = session.actors[1].hp = 0
    session._finish_combat(active)
    _ready(session, MeetingPhase.RESULT_SHEET)
    assert session.last_defeat_mask == 3
    assert session.sheet_screen is not None and session.sheet_screen.state.number == 9
    assert session.sheet_screen.state.knight == 1
    assert (session.actors[0].lives, session.actors[1].lives) == (lives[0] - 1, lives[1] - 1)
    assert session.actors[0].progress == session.actors[1].progress == 0


def test_natural_dragon_win_opens_persistent_hoard_and_spends_only_on_close(adf_dir: Path) -> None:
    session = _session(adf_dir)
    world = session.overworld
    hoard_before = deepcopy(session.inventories[session.dragon_actor.inventory])
    gold = session.dragon_actor.gold
    hoard_visit(session)
    meeting_until(session, lambda: session.scene == GameScene.STATUS, win=True)
    _ready(session, MeetingPhase.RESULT_SHEET)
    assert session.dragon_actor.lives == 255 and session.actors[0].progress == 2
    assert session.sheet_screen is not None and session.sheet_screen.state.number == 10
    assert world.spent == 17 and session.dragon_actor.gold == gold
    assert session.inventories[session.dragon_actor.inventory] == hoard_before
    rng = world.prng.state
    if gold:
        meeting_click(session, _region(session, 74, 1))
        assert session.dragon_actor.gold == 0
    meeting_done(session)
    _returned(session)
    assert world.spent == world.budget and world.prng.state == rng


def test_two_computers_return_without_fight_or_reward(adf_dir: Path) -> None:
    session = _session(adf_dir)
    world = session.overworld
    world.turn_index = world.turn_order.index(KnightId.BLUE)
    before = world.prng.state, session.actors[1].progress, session.actors[2].progress
    session._dispatch_arrival(Arrival(3, 1))
    _returned(session)
    assert session.active_encounter is None
    assert (world.prng.state, session.actors[1].progress, session.actors[2].progress) == before


def test_computer_no_fight_award_wraps_and_uses_existing_transfer(adf_dir: Path) -> None:
    session = _session(adf_dir)
    world = session.overworld
    world.turn_index = world.turn_order.index(KnightId.BLUE)
    session.actors[0].lives = 0
    session.actors[1].progress = 65535
    session.inventories[1].counts[0] = 3
    session._dispatch_arrival(Arrival(1, 33))
    _returned(session)
    assert session.actors[1].progress == 0
    assert session.inventories[1].byte(0) == 0 and session.inventories[2].byte(0) == 3


def test_natural_dragon_loss_returns_without_hoard_and_transfers_once(adf_dir: Path) -> None:
    session = _session(adf_dir)
    hoard_visit(session)
    actor = session.actors[0]
    actor.hp = 20
    actor.gold = 40
    before = session.dragon_actor.gold
    with patch("harddaysengine.sim.map_dragon.transfer_loot", wraps=transfer_loot) as transfer:
        _returned(session)
        transfer.assert_called_once()
    assert session.last_defeat_mask == 1 and actor.lives == 2
    assert session.dragon_actor.lives != 255 and session.dragon_actor.gold == before + 20
    assert session.overworld.spent == session.overworld.budget


def _trace(adf_dir: Path) -> list[str]:
    session = _session(adf_dir)
    meeting_visit(session, defender_wins=True)
    trace = []
    for _ in range(6000):
        inp = meeting_input(session) if session.scene == GameScene.COMBAT else InputFrame()
        if session.sheet_screen is not None:
            sheet = session.sheet_screen
            done = next(r for r in sheet.state.regions if r.id == 7)
            sheet.state.pointer_x, sheet.state.pointer_y = done.x + 1, done.y + 1
            inp = InputFrame(players={KnightId.RED: PlayerInput(fire=True)})
        session.step_tick(inp)
        trace.append(session.state_hash())
        if session.meeting_screen is None:
            return trace
    raise AssertionError("meeting replay did not finish")


def test_natural_defender_win_and_return_replay_hashes_match(adf_dir: Path) -> None:
    assert _trace(adf_dir) == _trace(adf_dir)


@pytest.mark.gl
def test_message_loot_hoard_rendering_is_pure_and_caller_cells_are_hashed(adf_dir: Path) -> None:
    rig = AuditRig(adf_dir)
    try:
        session = _protected(adf_dir)
        screen = session.meeting_screen
        assert screen is not None
        before = session.state_hash()
        screen.state.confused = 2
        assert before != session.state_hash()
        session._reset_campaign(1)
        assert session.meeting_screen is None
        for hoard in (False, True):
            session = _session(adf_dir)
            if hoard:
                hoard_visit(session)
            else:
                meeting_visit(session, defender_wins=True)
            meeting_until(session, lambda s=session: s.scene == GameScene.STATUS, win=True)
            for _ in range(40):
                session.step_tick(InputFrame())
            before = session.state_hash()
            one, two = rig.capture(session, "one"), rig.capture(session, "two")
            assert before == session.state_hash() and np.array_equal(one.rgb, two.rgb)
            assert len(np.unique(one.rgb.reshape(-1, 3), axis=0)) > 3
    finally:
        rig.release()
