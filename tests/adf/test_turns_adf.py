"""Real-disk calendar tables, host round boundaries and persistent flight (§11.19)."""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

import pytest

from pymoonstone.app.game import GameScene, GameSession
from pymoonstone.app.input import InputFrame, PlayerInput
from pymoonstone.assets.adf import DiskSet
from pymoonstone.assets.hunk import parse_hunk_executable
from pymoonstone.assets.mog_code import MogCode
from pymoonstone.sim.overworld import EncounterTrigger, TriggerKind
from pymoonstone.sim.types import KnightId

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.adf


def _session(adf_dir: Path, *, four: bool = True) -> GameSession:
    return GameSession(
        disk_set=DiskSet.from_directory(adf_dir),
        start_scene=GameScene.OVERWORLD,
        players=tuple(KnightId) if four else (KnightId.RED,),
    )


def _ticks(session: GameSession, count: int, *, fire: bool = False) -> None:
    for _ in range(count):
        session.step_tick(InputFrame(players={KnightId.RED: PlayerInput(fire=fire)}))


def test_loaded_calendar_flight_and_gift_tables_match_independent_reads(adf_dir: Path) -> None:
    session = _session(adf_dir)
    rules = session.overworld.turn_rules
    assert rules is not None
    mog = MogCode(parse_hunk_executable("mog", DiskSet.from_directory(adf_dir).read_file("mog")))
    pointer = mog.pointer(0x666, "independent moon")
    assert rules.moons == tuple(mog.hunk_data(pointer.hunk)[pointer.offset : pointer.offset + 8])
    pointer = mog.pointer(0x718, "independent flight table", 36)
    assert rules.dragon.scripts == tuple(
        struct.unpack_from(">I", mog.hunk_data(pointer.hunk), pointer.offset + i * 4)[0]
        for i in range(16)
    )
    assert rules.dragon.initial_job == struct.unpack_from(
        ">HHH", mog.hunk_data(4), rules.dragon.scripts[0] + 4
    )
    pointer = mog.pointer(0x9AA2, "independent item bands")
    assert rules.gifts.bands == tuple(
        struct.iter_unpack(">II", mog.hunk_data(pointer.hunk)[pointer.offset : pointer.offset + 80])
    )
    assert rules.dragon.day == mog.hunk_word(36, 0x694)
    assert rules.dragon.progress == mog.hunk_word(0, 0x1242)
    assert [line.text for line in rules.next_day] == ["Next Day", "Press fire to continue"]


def test_host_eight_rounds_show_next_day_each_time_and_spawn_dragon_on_day_two(
    adf_dir: Path,
) -> None:
    session = _session(adf_dir)
    world = session.overworld
    assert world.turn_rules is not None
    for round_index in range(8):
        visited = []
        for _ in range(4):
            visited.append(world.current_knight_id)
            session.step_tick(InputFrame(key=ord("E")))
            session.step_tick(InputFrame(key=ord("E")))
        assert visited == list(KnightId)
        assert session.scene == GameScene.NEW_DAY
        assert world.day == (round_index + 1) // 4
        _ticks(session, 50, fire=True)
        assert session.scene == GameScene.NEW_DAY
        _ticks(session, 1)
        _ticks(session, 1, fire=True)
        assert session.scene == GameScene.NEW_DAY
        _ticks(session, 1)
        assert session.scene == GameScene.OVERWORLD
    assert world.moon_index == 2 and world.moon_frame == world.turn_rules.moons[2]
    assert world.dragon_active and session.map_runtime is not None


def test_real_flight_jobs_replay_and_pause_without_catchup(adf_dir: Path) -> None:
    left, right = _session(adf_dir), _session(adf_dir)
    for session in (left, right):
        session.overworld.day = 2
        session._reenter_map()
    assert left.map_runtime is not None
    frames = set()
    for _ in range(180):
        left.step_tick(InputFrame())
        right.step_tick(InputFrame())
        assert left.state_hash() == right.state_hash()
        frames.update(draw.frame for draw in left.map_runtime.scene.fight.engine.output.draws)
    assert len(frames) >= 4
    before = left.map_runtime.snapshot()
    pause = InputFrame(players={KnightId.RED: PlayerInput(pause=True)})
    left.step_tick(pause)
    _ticks(left, 20)
    assert left.map_runtime.snapshot() == before
    left.step_tick(pause)
    _ticks(left, 2)
    assert left.map_runtime.snapshot() != before


@pytest.mark.parametrize("lost", [False, True])
def test_map_attack_uses_real_dragon_runtime_and_settles_at_host_boundary(
    adf_dir: Path, lost: bool
) -> None:
    """Fixture the caller boundary; existing fight tests cover natural outcomes."""
    session = _session(adf_dir)
    world = session.overworld
    world.day = 2
    session._reenter_map()
    world.dragon.engaged_with = 1
    world.dragon_touch[:] = [1]
    world.current_actor.gold = 30
    session._sync_legacy_actor(KnightId.RED)
    _ticks(session, 2)
    active = session.active_encounter
    assert session.scene == GameScene.COMBAT and active is not None
    assert active.trigger.kind == TriggerKind.DRAGON_INTERCEPT
    assert not world.dragon_active and session.map_runtime is not None
    assert not any(job.active for job in session.map_runtime.scene.fight.engine.jobs)
    assert active.runtime.fight.dragon is world.dragon
    world.current_actor.hp = 0 if lost else 20
    lives = world.current_actor.lives
    active.runtime.finished = True
    session._finish_combat(active)
    assert session.scene == GameScene.OVERWORLD and world.spent == world.budget
    if lost:
        assert world.current_actor.lives == lives - 1 and world.dragon.gold > 0
    else:
        assert world.dragon.lives == 255 and not world.dragon_active
        assert world.turn_rules is not None
        assert world.current_actor.progress == world.turn_rules.dragon.progress


def test_fight_receives_loaded_calendar_day_and_moon(adf_dir: Path) -> None:
    session = _session(adf_dir, four=False)
    world = session.overworld
    world.day, world.moon_frame = 3, 46
    trigger = EncounterTrigger(
        TriggerKind.PVP_DUEL,
        KnightId.RED,
        -1,
        world.day,
        world.moon_phase,
        opponent_knight_id=KnightId.BLUE,
    )
    session._start_combat(trigger, KnightId.RED)
    assert session.active_encounter is not None
    fight = session.active_encounter.runtime.fight
    assert fight.ai_day == 3 and fight.moon_frame == 46


@pytest.mark.gl
def test_calendar_and_flight_rendering_never_advance_replay_state(adf_dir: Path) -> None:
    from pymoonstone.app.audit import AuditRig

    rig = AuditRig(adf_dir)
    try:
        session = _session(adf_dir)
        session.overworld.day = 2
        session._reenter_map()
        _ticks(session, 40)
        before = session.state_hash()
        rig.capture(session, "flight_once")
        rig.capture(session, "flight_twice")
        assert session.state_hash() == before
        session._show_next_day()
        _ticks(session, 40)
        before = session.state_hash()
        rig.capture(session, "calendar_once")
        rig.capture(session, "calendar_twice")
        assert session.state_hash() == before
    finally:
        rig.release()


def test_initial_map_job_writes_back_its_loaded_position(adf_dir: Path) -> None:
    session = _session(adf_dir)
    world = session.overworld
    world.day = 2
    session._reenter_map()
    assert world.turn_rules is not None and session.map_runtime is not None
    assert (world.dragon.x, world.dragon.height, world.dragon.y) == (10, 0, 100)
    _ticks(session, 2)
    assert (
        world.dragon.x,
        world.dragon.height,
        world.dragon.y,
    ) == world.turn_rules.dragon.initial_job


def test_changed_map_handler_instruction_is_rejected(adf_dir: Path) -> None:
    from dataclasses import replace

    from pymoonstone.assets.fight_palette import MogVersionError
    from pymoonstone.assets.turn_rules import parse_turn_rules

    executable = parse_hunk_executable("mog", DiskSet.from_directory(adf_dir).read_file("mog"))
    sections = list(executable.sections)
    data = bytearray(sections[36].data)
    data[0x6B8:0x6BA] = bytes(2)
    sections[36] = replace(sections[36], data=bytes(data))
    with pytest.raises(MogVersionError, match="unexpected map instruction"):
        parse_turn_rules(replace(executable, sections=tuple(sections)))
