"""Real-disk campaign-host combat and replay checks (T11.8b2, §10.22)."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from harddaysengine.app.fight_audit import TROGG_ARENAS, fight_host_input
from harddaysengine.app.fight_runtime import FightLoader
from harddaysengine.app.game import GameScene, GameSession, run_headless_session
from harddaysengine.app.input import (
    InputFrame,
    PlayerInput,
    ReplayRecording,
    load_replay,
    save_replay,
)
from harddaysengine.assets.adf import DiskSet
from harddaysengine.sim.fight import (
    CasterPhase,
    Fight,
    HandlerResult,
    Restart,
    SnatchEncounterPhase,
    SnatcherPhase,
)
from harddaysengine.sim.fight_ai.caster import CASTER_TYPE
from harddaysengine.sim.fight_ai.snatcher import SNATCHER_TYPE
from harddaysengine.sim.fight_moves import DOWN, FIRE
from harddaysengine.sim.m68k import s16
from harddaysengine.sim.overworld import EncounterTrigger, TriggerKind
from harddaysengine.sim.types import KnightId

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
    session = _session(adf_dir, name, hp=50)
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


def test_fight_checkpoint_distinguishes_lair_and_knight_order(adf_dir: Path) -> None:
    session = _session(adf_dir, TROGG_ARENAS[0])
    active = session.active_encounter
    assert active is not None
    active.trigger = replace(active.trigger, kind=TriggerKind.LAIR_BOSS, lair_index=19)
    initial_hash = session.state_hash()
    active.trigger = replace(active.trigger, lair_index=20)
    assert session.state_hash() != initial_hash

    active.knight_ids = (KnightId.BLUE, KnightId.YELLOW)
    ordered_hash = session.state_hash()
    active.knight_ids = (KnightId.YELLOW, KnightId.BLUE)
    assert session.state_hash() != ordered_hash


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
def test_practice_death_of_either_knight_returns_to_main_menu(
    adf_dir: Path, loser_first: bool
) -> None:
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
    held_fire = InputFrame(players={KnightId.RED: PlayerInput(fire=True)})
    for _ in range(1200):
        session.step_tick(held_fire if session.blocking_fade else InputFrame())
        if session.scene != GameScene.COMBAT:
            break
    assert session.scene == GameScene.KNIGHT_SELECT
    assert not session.knight_pick_active
    assert session.active_encounter is None
    assert not session.practice_mode
    assert session.selected_player_count == 1
    assert session.palette_fx.is_fading
    session.step_tick(held_fire)
    assert session.scene == GameScene.KNIGHT_SELECT
    session.step_tick(InputFrame())
    session.step_tick(held_fire)
    assert session.scene == GameScene.COMBAT


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


def _step_until_unpaused(session: GameSession, handle: int) -> None:
    active = session.active_encounter
    assert active is not None
    job = active.runtime.fight.find_job(handle)
    assert job is not None
    for _ in range(300):
        if job.paused == 0:
            return
        session.step_tick(InputFrame())
    raise AssertionError("fighter job never unpaused after entry")


def test_campaign_host_ratmen_hung_upward_stab_frees_knight(adf_dir: Path) -> None:
    session = _session(adf_dir, "ratmen")
    active, loader = session.active_encounter, session.fight_loader
    assert active is not None and loader is not None
    fight, first = active.runtime.fight, active.runtime.first
    assert fight.actor(first).input_port == 2
    ratman_job = next(j for j in fight.engine.jobs if j.active and j.type == CASTER_TYPE)
    _step_until_unpaused(session, ratman_job.owner)

    ratman = fight.actor(ratman_job.owner)
    ratman.hp = 50
    fight.caster.phases[ratman_job.owner] = CasterPhase.SLAM
    fight.caster.victim_held = True
    fight.toggle_pause(first)
    scripts = loader.caster_rules.scripts
    fight.restart_job(ratman_job.owner, scripts.slam_start)

    seen: list[int] = []
    original = fight.handlers[CASTER_TYPE]

    def logged(current: Fight, owner: int) -> HandlerResult:
        result = original(current, owner)
        if owner == ratman_job.owner and isinstance(result, Restart):
            seen.append(result.script)
        return result

    fight.handlers[CASTER_TYPE] = logged
    escape = InputFrame(players={KnightId.RED: PlayerInput(dy=1, fire=True)})
    for _ in range(600):
        session.step_tick(escape)
        if not fight.caster.victim_held:
            break

    knight_job = fight.find_job(first)
    assert knight_job is not None and knight_job.paused == 0
    assert fight.ports == [0, FIRE | DOWN]
    assert scripts.struggle in seen
    assert scripts.drop in seen
    assert s16(ratman.hp) <= 0
    assert not fight.caster.victim_held


def test_campaign_host_ratmen_grab_and_mudmen_carry_escapes(adf_dir: Path) -> None:
    escape = InputFrame(players={KnightId.RED: PlayerInput(dy=1, fire=True)})

    rat_session = _session(adf_dir, "ratmen")
    rat_active, rat_loader = rat_session.active_encounter, rat_session.fight_loader
    assert rat_active is not None and rat_loader is not None
    rat_fight, rat_first = rat_active.runtime.fight, rat_active.runtime.first
    rat_job = next(j for j in rat_fight.engine.jobs if j.active and j.type == CASTER_TYPE)
    _step_until_unpaused(rat_session, rat_job.owner)
    rat = rat_fight.actor(rat_job.owner)
    rat_fight.caster.phases[rat_job.owner] = CasterPhase.GRABBED
    rat.cooldown = 10
    rat_fight.caster.victim_grabbed = True
    rat_fight.toggle_pause(rat_first)
    rat_fight.restart_job(rat_job.owner, rat_loader.caster_rules.scripts.grab)
    for _ in range(180):
        rat_session.step_tick(escape)
        if not rat_fight.caster.victim_grabbed:
            break
    rat_knight = rat_fight.find_job(rat_first)
    assert rat_knight is not None and rat_knight.paused == 0
    assert not rat_fight.caster.victim_grabbed

    mud_session = _session(adf_dir, "mudmen")
    mud_active, mud_loader = mud_session.active_encounter, mud_session.fight_loader
    assert mud_active is not None and mud_loader is not None
    mud_fight, mud_first = mud_active.runtime.fight, mud_active.runtime.first
    assert mud_fight.actor(mud_first).input_port == 2
    mud_job = next(j for j in mud_fight.engine.jobs if j.active and j.type == SNATCHER_TYPE)
    _step_until_unpaused(mud_session, mud_job.owner)
    mud = mud_fight.actor(mud_job.owner)
    mud_fight.snatcher.phases[mud_job.owner] = SnatcherPhase.CARRYING
    mud.anim_timer = 40
    mud_fight.snatcher.encounter = SnatchEncounterPhase.CARRYING
    mud_fight.toggle_pause(mud_first)
    mud_fight.restart_job(mud_job.owner, mud_loader.snatcher_rules.scripts.carry)
    for _ in range(240):
        mud_session.step_tick(escape)
        if mud_fight.snatcher.encounter not in (
            SnatchEncounterPhase.CARRYING,
            SnatchEncounterPhase.RELEASED,
        ):
            break
    mud_knight = mud_fight.find_job(mud_first)
    assert mud_knight is not None and mud_knight.paused == 0
    assert mud_fight.snatcher.encounter not in (
        SnatchEncounterPhase.CARRYING,
        SnatchEncounterPhase.RELEASED,
    )
