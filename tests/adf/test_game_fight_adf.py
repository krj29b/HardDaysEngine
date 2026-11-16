"""Real-disk campaign-host combat and replay checks (T11.8b2, §10.22)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from pymoonstone.app.fight_audit import TROGG_ARENAS, fight_host_input
from pymoonstone.app.fight_runtime import FightLoader
from pymoonstone.app.game import GameScene, GameSession, run_headless_session
from pymoonstone.app.input import InputFrame, PlayerInput, ReplayRecording, load_replay, save_replay
from pymoonstone.assets.adf import DiskSet
from pymoonstone.sim.overworld import EncounterTrigger, TriggerKind
from pymoonstone.sim.types import KnightId

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.adf


def _session(adf_dir: Path, arena_name: str, *, hp: int = 200) -> GameSession:
    session = GameSession(
        disk_set=DiskSet.from_directory(adf_dir),
        players=(KnightId.BLUE,),
        start_scene=GameScene.OVERWORLD,
        seed=0x2468ACE1,
    )
    assert session.asset_bundle is not None
    session.fight_loader = FightLoader(session.asset_bundle)
    arena = next(a for a in session.fight_loader.defs.arenas if a.name == arena_name)
    legacy = session.knight_combat[KnightId.BLUE]
    legacy.hp = legacy.max_hp = hp
    legacy.strength, legacy.sword_tier, legacy.daggers, legacy.lives = 3, 4, 99, 2
    session._handle_overworld_triggers(
        KnightId.BLUE,
        (
            EncounterTrigger(
                TriggerKind.WANDERING_MONSTER,
                KnightId.BLUE,
                -1,
                1,
                session.overworld.moon_phase,
                creature_type=arena.handler_offsets[0],
            ),
        ),
    )
    assert session.scene == GameScene.COMBAT
    assert session.active_encounter is not None
    return session


@pytest.mark.parametrize("name", TROGG_ARENAS)
def test_campaign_host_wins_each_trogg_encounter_and_returns_record_changes(
    adf_dir: Path, name: str
) -> None:
    session = _session(adf_dir, name)
    active = session.active_encounter
    assert active is not None
    fight = active.runtime.fight
    assert fight.actor(active.runtime.first) is session.actors[int(KnightId.BLUE)]
    for _ in range(36000):
        session.step_tick(fight_host_input(session))
        if session.scene != GameScene.COMBAT:
            break
    assert active.runtime.finished
    assert session.scene == GameScene.OVERWORLD
    assert session.active_encounter is None
    assert fight.total == 0
    assert session.knight_combat[KnightId.BLUE].hp == fight.actor(active.runtime.first).hp
    assert session.knight_combat[KnightId.BLUE].daggers < 99
    assert session.knight_combat[KnightId.BLUE].lives == 2


def test_campaign_host_loses_through_the_death_script_and_settles_one_life(adf_dir: Path) -> None:
    session = _session(adf_dir, TROGG_ARENAS[0], hp=20)
    active = session.active_encounter
    assert active is not None
    for _ in range(36000):
        session.step_tick(InputFrame())
        if session.scene != GameScene.COMBAT:
            break
    assert active.runtime.finished
    assert session.scene == GameScene.OVERWORLD
    assert session.last_status == "KNIGHT FALLEN"
    assert session.knight_combat[KnightId.BLUE].lives == 1
    assert session.knight_combat[KnightId.BLUE].hp == 20


def test_campaign_host_replays_recorded_inputs_with_identical_hashes(
    adf_dir: Path, tmp_path: Path
) -> None:
    first = _session(adf_dir, TROGG_ARENAS[0])
    frames, hashes = [], []
    for _ in range(36000):
        frame = fight_host_input(first)
        first.step_tick(frame)
        frames.append(frame)
        if first.tick_count % 6 == 0:
            hashes.append(first.state_hash())
        if first.scene != GameScene.COMBAT:
            break
    assert first.scene == GameScene.OVERWORLD
    path = tmp_path / "fight.json"
    save_replay(path, ReplayRecording(first.seed, first.players, tuple(frames)))
    second = _session(adf_dir, TROGG_ARENAS[0])
    replay_hashes = []
    for frame in load_replay(path).frames:
        second.step_tick(frame)
        if second.tick_count % 6 == 0:
            replay_hashes.append(second.state_hash())
    assert hashes == replay_hashes
    assert first.state_hash() == second.state_hash()


def test_practice_menu_starts_two_real_knights_on_distinct_ports(adf_dir: Path) -> None:
    session = GameSession(
        disk_set=DiskSet.from_directory(adf_dir), start_scene=GameScene.KNIGHT_SELECT
    )
    session.menu_cursor = 2
    session.step_tick(InputFrame(players={KnightId.RED: PlayerInput(confirm=True)}))
    assert session.players == (KnightId.BLUE, KnightId.YELLOW)
    assert session.scene == GameScene.COMBAT
    active = session.active_encounter
    assert active is not None
    assert active.runtime.opponent != active.runtime.first
    session.step_tick(
        InputFrame(players={KnightId.RED: PlayerInput(dx=-1), KnightId.BLUE: PlayerInput(dx=1)})
    )
    assert active.runtime.fight.ports == [1, 2]


@pytest.mark.parametrize("loser_first", [False, True])
def test_practice_death_of_either_knight_returns_to_title(adf_dir: Path, loser_first: bool) -> None:
    session = GameSession(
        disk_set=DiskSet.from_directory(adf_dir), start_scene=GameScene.KNIGHT_SELECT
    )
    session.menu_cursor = 2
    session.step_tick(InputFrame(players={KnightId.RED: PlayerInput(confirm=True)}))
    active = session.active_encounter
    assert active is not None
    handle = active.runtime.first if loser_first else active.runtime.opponent
    actor = active.runtime.fight.actor(handle)
    actor.hp = 0
    script = active.runtime.fight.table_long(actor.hurt_scripts, 8)
    active.runtime.fight.restart_job(handle, script)
    for _ in range(1200):
        session.step_tick(InputFrame())
        if session.scene != GameScene.COMBAT:
            break
    assert session.scene == GameScene.TITLE
    assert session.active_encounter is None
    assert not session.practice_mode


def test_disk_backed_menu_to_practice_replay_preserves_the_initial_roster(
    adf_dir: Path, tmp_path: Path
) -> None:
    frames = [
        InputFrame(players={KnightId.RED: PlayerInput(skip_intro=True)}),
        InputFrame(players={KnightId.RED: PlayerInput(dy=1)}),
        InputFrame(),
        InputFrame(players={KnightId.RED: PlayerInput(dy=1)}),
        InputFrame(players={KnightId.RED: PlayerInput(confirm=True)}),
        *[InputFrame() for _ in range(60)],
    ]
    path = tmp_path / "practice.json"
    first = run_headless_session(
        ticks=len(frames), frames=frames, adf_dir=adf_dir, record_path=path
    )
    assert first.scene == GameScene.COMBAT
    assert first.session.players == (KnightId.BLUE, KnightId.YELLOW)
    assert first.recording.players == (KnightId.RED,)
    replay = run_headless_session(replay_path=path, adf_dir=adf_dir)
    assert replay.scene == first.scene
    assert replay.final_hash == first.final_hash
