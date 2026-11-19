"""Independent Stonehenge operands and complete original visit branches (§12.34)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pytest

from harddaysengine.app.audit import AuditRig
from harddaysengine.app.game import GameScene, GameSession
from harddaysengine.app.input import InputFrame, PlayerInput
from harddaysengine.assets.adf import DiskSet
from harddaysengine.assets.hunk import parse_hunk_executable
from harddaysengine.assets.stonehenge_data import parse_stone_rules
from harddaysengine.sim.overworld import Arrival
from harddaysengine.sim.stonehenge import StonePhase
from harddaysengine.sim.types import KnightId

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

pytestmark = pytest.mark.adf


def _tick(session: GameSession, *, fire: bool = False) -> None:
    session.step_tick(InputFrame(players={KnightId.RED: PlayerInput(fire=fire)}))


def _until(session: GameSession, predicate: Callable[[], bool]) -> None:
    for _ in range(2000):
        if predicate():
            return
        _tick(session)
    raise AssertionError("Stonehenge continuation did not finish")


def _ready(session: GameSession) -> bool:
    screen = session.stone_screen
    return screen is not None and not screen.state.wait and not session.palette_fx.is_fading


def _offer(adf_dir: Path, slot: int = 0) -> GameSession:
    session = GameSession(disk_set=DiskSet.from_directory(adf_dir))
    actor = session.actors[KnightId.RED]
    actor.lives, actor.hp, actor.life_loss = 3, 4, 1
    session.inventories[1].counts[slot // 2] = 1
    session.last_sheet_item = 17
    session._dispatch_arrival(Arrival(1, 27))
    _until(session, lambda: _ready(session))
    assert session.stone_screen is not None
    assert session.stone_screen.state.phase == StonePhase.OFFER
    _tick(session, fire=True)
    _tick(session)
    _until(session, lambda: session.sheet_screen is not None)
    assert session.sheet_screen is not None and session.sheet_screen.state.number == 3
    assert session.last_sheet_item == session.sheet_screen.state.last_item == 65535
    return session


def _close_sheet(session: GameSession) -> None:
    screen = session.sheet_screen
    assert screen is not None
    _until(session, lambda: not session.palette_fx.is_fading)
    done = next(r for r in screen.state.regions if r.id == 7)
    screen.state.pointer_x, screen.state.pointer_y = done.x, done.y
    _tick(session, fire=True)


def test_decoded_rules_match_independent_instruction_and_shade_reads(adf_dir: Path) -> None:
    exe = parse_hunk_executable("mog", DiskSet.from_directory(adf_dir).read_file("mog"))
    rules = parse_stone_rules(exe)
    code = exe.sections[0].data
    bold_style = int.from_bytes(code[0x9352:0x9354], "big")
    assert bold_style == 8
    assert all(line.style & bold_style for line in (*rules.offer, *rules.quest, *rules.ritual_text))

    def word(pos: int) -> int:
        return int.from_bytes(code[pos : pos + 2], "big")

    assert rules.matches == tuple(
        (word(p + 2), word(p + 8)) for p in (0x177E, 0x178C, 0x179A, 0x17A8)
    )
    assert rules.moon_bits == tuple((word(p + 2), word(p + 8)) for p in (0x1822, 0x182C, 0x1836))
    assert rules.class_bits == tuple(
        (int.from_bytes(code[p + 2 : p + 6], "big"), word(p + 10))
        for p in (0x1844, 0x1850, 0x185C, 0x1868)
    )
    shades = [word(p + 2) for p in range(0xAB74, 0xABE8, 2) if word(p) in (0x30FC, 0x30BC)]
    assert shades == [value for _, row in rules.ritual.ramps for value in row]
    assert (rules.fade_period, rules.fade_wait) == (
        word(0x8B2E) & 255,
        int.from_bytes(code[0x8B38:0x8B3C], "big"),
    )
    assert (rules.life_cap, rules.life_gain, rules.ending_flag) == (
        word(0x17FA),
        word(0x1802),
        word(0x189C),
    )
    assert [s.script for s in rules.ritual.spawns] == [
        int.from_bytes(code[p : p + 4], "big") for p in (0xA9B8, 0xA9DC)
    ]
    bank = int.from_bytes(exe.sections[16].data[0x1EA:0x1EE], "big")
    data = exe.sections[17].data
    assert rules.ritual.bank == data[bank : data.index(0, bank)].decode("ascii")


def test_declined_offer_has_no_blessing_and_temple_exit_spends_turn(adf_dir: Path) -> None:
    session = _offer(adf_dir)
    actor, world = session.actors[0], session.overworld
    before = actor.lives, actor.hp, actor.life_loss, world.prng.state
    _close_sheet(session)
    _until(
        session, lambda: session.sheet_screen is not None and session.sheet_screen.state.number == 9
    )
    assert (actor.lives, actor.hp, actor.life_loss, world.prng.state) == before
    assert world.spent < world.budget
    _close_sheet(session)
    _until(session, lambda: session.scene == GameScene.OVERWORLD)
    assert session.stone_screen is None and world.spent == world.budget


@pytest.mark.parametrize("slot", (0, 6))
def test_offered_item_runs_natural_ritual_once_and_blesses_before_temple(
    adf_dir: Path,
    slot: int,
) -> None:
    session = _offer(adf_dir, slot)
    screen = session.sheet_screen
    assert screen is not None
    item = next(r for r in screen.state.regions if r.type == 12 and r.field == slot)
    screen.state.pointer_x, screen.state.pointer_y = item.x, item.y
    before = session.state_hash()
    _tick(session, fire=True)
    assert session.inventories[1].byte(slot) == 0 and session.state_hash() != before
    _until(session, lambda: session.scene == GameScene.STONEHENGE and _ready(session))
    stone = session.stone_screen
    assert stone is not None
    _until(
        session, lambda: session.sheet_screen is not None and session.sheet_screen.state.number == 9
    )
    assert stone.run is not None and stone.run.done and stone.run.started
    actor = session.actors[0]
    assert (actor.lives, actor.hp, actor.life_loss) == (4, actor.hp_max, 0)
    assert session.last_sheet_item == slot
    assert session.overworld.spent < session.overworld.budget
    assert not stone.run.fight.skipped
    _close_sheet(session)
    _until(session, lambda: session.scene == GameScene.OVERWORLD)
    assert session.overworld.spent == session.overworld.budget


@pytest.mark.parametrize("frame,bit,moon_code", ((46, 2, 1), (46, 3, 1), (45, 1, 4), (49, 0, 2)))
@pytest.mark.parametrize("kind,class_code", ((0, 16), (1, 32), (2, 64), (3, 8)))
def test_matching_stone_hands_over_exact_program_flags_and_never_returns(
    adf_dir: Path,
    frame: int,
    bit: int,
    moon_code: int,
    kind: int,
    class_code: int,
) -> None:
    session = GameSession(disk_set=DiskSet.from_directory(adf_dir))
    session.overworld.moon_frame = frame
    session.actors[0].kind = kind
    session.inventories[1].counts[11] = 1 << bit
    session._dispatch_arrival(Arrival(1, 27))
    _until(session, lambda: _ready(session))
    screen = session.stone_screen
    assert screen is not None and screen.state.phase == StonePhase.QUEST
    _tick(session, fire=True)
    assert session.ending_request is None
    _tick(session)
    request = session.ending_request
    assert request is not None and request.flags == 128 | moon_code | class_code
    assert request.program == screen.rules.program
    assert session.sheet_screen is None and screen.run is None
    for _ in range(40):
        _tick(session, fire=True)
    assert session.ending_request == request and session.scene == GameScene.ENDING
    assert session.ending_screen is not None and session.ending_screen.run.flags == request.flags


def test_visit_hash_replays_through_script_and_includes_work_and_wait_cells(adf_dir: Path) -> None:
    traces = []
    for _ in range(2):
        session = _offer(adf_dir)
        sheet = session.sheet_screen
        assert sheet is not None
        item = next(r for r in sheet.state.regions if r.type == 12 and r.field == 0)
        sheet.state.pointer_x, sheet.state.pointer_y = item.x, item.y
        _tick(session, fire=True)
        trace = []
        for tick in range(720):
            _tick(session)
            if tick % 25 == 0:
                trace.append(session.state_hash())
        traces.append(trace)
        screen = session.stone_screen
        assert screen is not None and screen.run is not None
        original = session.state_hash()
        screen.run.fight.engine.jobs[0].work.loop_count ^= 1
        assert session.state_hash() != original
    assert traces[0] == traces[1]


@pytest.mark.gl
def test_stonehenge_offer_and_ritual_render_without_mutating_the_visit(adf_dir: Path) -> None:
    session = _offer(adf_dir)
    sheet = session.sheet_screen
    assert sheet is not None
    item = next(r for r in sheet.state.regions if r.type == 12 and r.field == 0)
    sheet.state.pointer_x, sheet.state.pointer_y = item.x, item.y
    _tick(session, fire=True)
    rig = AuditRig(adf_dir)
    try:
        for phase in (StonePhase.RITUAL_MESSAGE, StonePhase.RITUAL):
            _until(
                session,
                lambda phase=phase: (
                    session.stone_screen is not None
                    and session.stone_screen.state.phase == phase
                    and not session.palette_fx.is_fading
                ),
            )
            before = session.state_hash()
            first, second = rig.capture(session, "stone"), rig.capture(session, "stone_again")
            assert before == session.state_hash() and np.array_equal(first.rgb, second.rgb)
            assert len(np.unique(first.rgb.reshape(-1, 3), axis=0)) > 3
    finally:
        rig.release()
