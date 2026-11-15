"""End-to-end deterministic game session, input router, and replay tests (T8.3)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import numpy as np
import pygame

from pymoonstone.app.audio import AudioEngine
from pymoonstone.app.game import GameScene, GameSession, run_headless_session
from pymoonstone.app.input import (
    InputFrame,
    InputRouter,
    PlayerInput,
    ReplayRecording,
    load_replay,
    save_replay,
)
from pymoonstone.app.intro_player import assemble_stile_panorama
from pymoonstone.app.location_screens import LocationKind
from pymoonstone.cli.main import main
from pymoonstone.sim.types import KnightId

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _frame_p1(
    *,
    dx: int = 0,
    dy: int = 0,
    fire: bool = False,
    secondary: bool = False,
    weapon_switch: bool = False,
    run: bool = False,
    confirm: bool = False,
    pause: bool = False,
    skip_intro: bool = False,
) -> InputFrame:
    return InputFrame(
        players={
            KnightId.RED: PlayerInput(
                dx=dx,
                dy=dy,
                fire=fire,
                secondary=secondary,
                weapon_switch=weapon_switch,
                run=run,
                confirm=confirm,
                pause=pause,
                skip_intro=skip_intro,
            )
        }
    )


def test_input_router_keyboard_four_players_headless() -> None:
    router = InputRouter(init_joysticks=False)
    router.process_pygame_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RIGHT))
    router.process_pygame_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE))
    router.process_pygame_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_a))
    router.process_pygame_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_j))
    router.process_pygame_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_KP8))
    router.process_pygame_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN))

    frame1 = router.sample_frame()
    assert frame1.for_player(KnightId.RED).dx == 1
    assert frame1.for_player(KnightId.RED).fire is True
    assert frame1.for_player(KnightId.RED).confirm is True
    assert frame1.for_player(KnightId.BLUE).dx == -1
    assert frame1.for_player(KnightId.GREEN).dx == -1
    assert frame1.for_player(KnightId.YELLOW).dy == -1

    # Edge-triggered confirm clears on next sample while held fire/dx remain.
    frame2 = router.sample_frame()
    assert frame2.for_player(KnightId.RED).dx == 1
    assert frame2.for_player(KnightId.RED).fire is True
    assert frame2.for_player(KnightId.RED).confirm is False

    router.process_pygame_event(pygame.event.Event(pygame.KEYUP, key=pygame.K_RIGHT))
    frame3 = router.sample_frame()
    assert frame3.for_player(KnightId.RED).dx == 0


def test_input_router_joystick_events_and_deadzone() -> None:
    router = InputRouter(init_joysticks=False)
    router.process_pygame_event(pygame.event.Event(pygame.JOYAXISMOTION, joy=1, axis=0, value=0.2))
    router.process_pygame_event(pygame.event.Event(pygame.JOYAXISMOTION, joy=1, axis=1, value=-0.8))
    router.process_pygame_event(pygame.event.Event(pygame.JOYBUTTONDOWN, joy=1, button=0))

    frame1 = router.sample_frame()
    p2 = frame1.for_player(KnightId.BLUE)
    assert p2.dx == 0
    assert p2.dy == -1
    assert p2.fire is True
    assert p2.confirm is True

    router.process_pygame_event(pygame.event.Event(pygame.JOYHATMOTION, joy=1, value=(1, 0)))
    router.process_pygame_event(pygame.event.Event(pygame.JOYBUTTONUP, joy=1, button=0))
    frame2 = router.sample_frame()
    assert frame2.for_player(KnightId.BLUE).dx == 1
    assert frame2.for_player(KnightId.BLUE).fire is False


def test_replay_serialization_roundtrip(tmp_path: Path) -> None:
    rec = ReplayRecording(
        seed=0xCAFE_BABE,
        players=(KnightId.RED, KnightId.BLUE),
        frames=(
            _frame_p1(confirm=True),
            _frame_p1(dx=1, fire=True),
        ),
    )
    path = tmp_path / "sample.replay.json"
    save_replay(path, rec)
    loaded = load_replay(path)
    assert loaded == rec


def _build_scripted_campaign_frames() -> list[InputFrame]:
    """Script Title -> Knight Select -> Overworld -> Wilderness Combat -> Overworld."""
    frames: list[InputFrame] = []
    # Tick 0: Title -> Knight Select
    frames.append(_frame_p1(confirm=True))
    # Tick 1: Knight Select (keep 1 knight) -> Overworld
    frames.append(_frame_p1(confirm=True))
    # Tick 2-4: Overworld, edge-triggered cycle to wilderness node 11 and confirm move
    # Start node for RED is 10 with neighbors (0, 1, 11); index 2 is 11 (wilderness).
    frames.append(_frame_p1(dx=1))
    frames.append(_frame_p1())
    frames.append(_frame_p1(dx=1))
    frames.append(_frame_p1(confirm=True))
    # Walk right in combat and slash repeatedly to defeat the monster.
    for tick in range(180):
        if tick < 70:
            frames.append(_frame_p1(dx=1, run=True))
        elif tick % 20 == 0:
            frames.append(_frame_p1(dx=1, fire=True))
        else:
            frames.append(_frame_p1(dx=0, fire=False))
    return frames


def test_end_to_end_session_and_replay_determinism(tmp_path: Path) -> None:
    # Seed chosen so wilderness arrival triggers a wandering monster encounter.
    chosen_seed = 7
    probe = GameSession(seed=chosen_seed, start_scene=GameScene.OVERWORLD)
    # Verify our seed triggers combat when moving RED from 10 -> 11 -> 21 if needed.
    probe.step_tick(_frame_p1(dx=1))
    probe.step_tick(_frame_p1())
    probe.step_tick(_frame_p1(dx=1))
    probe.step_tick(_frame_p1(confirm=True))

    script = _build_scripted_campaign_frames()
    replay_path = tmp_path / "campaign.json"

    run1 = run_headless_session(
        ticks=len(script),
        seed=chosen_seed,
        frames=script,
        record_path=replay_path,
    )
    run2 = run_headless_session(
        replay_path=replay_path,
    )

    assert run1.ticks_executed == len(script)
    assert run2.ticks_executed == len(script)
    assert run1.final_hash == run2.final_hash
    assert run1.session.overworld.total_turns >= 1
    assert run1.scene in (GameScene.OVERWORLD, GameScene.COMBAT, GameScene.VICTORY)


def test_combat_encounter_victory_and_pause_determinism() -> None:
    session = GameSession(seed=101, start_scene=GameScene.TITLE)
    session.step_tick(_frame_p1(confirm=True))
    assert session.scene == GameScene.KNIGHT_SELECT

    session.step_tick(_frame_p1(dx=1))
    assert session.selected_player_count == 2
    session.step_tick(_frame_p1(dx=-1))
    assert session.selected_player_count == 1
    session.step_tick(_frame_p1(confirm=True))
    assert session.scene == GameScene.OVERWORLD

    # Pause toggles cleanly without advancing simulation state.
    session.step_tick(_frame_p1(pause=True))
    assert session.paused is True
    session.step_tick(_frame_p1(confirm=True))
    assert session.overworld.total_turns == 0
    session.step_tick(_frame_p1(pause=True))
    assert session.paused is False


def test_cli_headless_record_and_replay(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    replay_file = tmp_path / "cli_replay.json"
    rc1 = main(
        [
            "--headless-ticks",
            "12",
            "--seed",
            "4242",
            "--record",
            str(replay_file),
        ]
    )
    out1 = capsys.readouterr().out.strip()
    assert rc1 == 0
    assert replay_file.is_file()

    rc2 = main(
        [
            "--headless-ticks",
            "12",
            "--replay",
            str(replay_file),
        ]
    )
    out2 = capsys.readouterr().out.strip()
    assert rc2 == 0
    assert out1 == out2


def test_assemble_stile_panorama_vertical_scroll() -> None:
    pic_a = np.full((200, 320), 3, dtype=np.uint8)
    pic_c = np.full((200, 320), 7, dtype=np.uint8)
    pic_b = np.full((200, 320), 11, dtype=np.uint8)
    words = [0] * 160 + [80] * 160 + [160] * 160
    pano = assemble_stile_panorama(words, (pic_a, pic_c, pic_b))
    assert pano.shape == (1200, 320)
    assert int(pano[0, 0]) == 3
    assert int(pano[450, 0]) == 7
    assert int(pano[1100, 0]) == 11


def test_knight_pick_routes_p1_input_and_edge_triggers_fire() -> None:
    session = GameSession(seed=7, start_scene=GameScene.KNIGHT_SELECT)
    session.knight_pick_active = True
    session.knight_pick_slot = 0  # SIR RICHARD -> KnightId.BLUE

    # Holding fire on two consecutive ticks only confirms once.
    session.selected_player_count = 2
    session.step_tick(_frame_p1(fire=True))
    assert session.picked_knights == [KnightId.BLUE]
    assert session.scene == GameScene.KNIGHT_SELECT
    session.step_tick(_frame_p1(fire=True))
    assert session.picked_knights == [KnightId.BLUE]

    # Single-player pick starts OVERWORLD and routes P1 (RED slot) input to BLUE knight.
    session2 = GameSession(seed=7, start_scene=GameScene.KNIGHT_SELECT)
    session2.knight_pick_active = True
    session2.knight_pick_slot = 0
    session2.step_tick(_frame_p1(confirm=True))
    assert session2.scene == GameScene.OVERWORLD
    assert session2.players == (KnightId.BLUE,)
    assert session2.overworld.current_knight_id == KnightId.BLUE

    session2.step_tick(_frame_p1(confirm=True))
    assert session2.overworld.total_turns == 1


def test_practice_mode_returns_to_knight_select() -> None:
    session = GameSession(seed=11, start_scene=GameScene.KNIGHT_SELECT)
    session.step_tick(_frame_p1(dy=1))
    session.step_tick(_frame_p1())
    session.step_tick(_frame_p1(dy=1))
    assert session.menu_cursor == 2

    session.step_tick(_frame_p1(confirm=True))
    assert session.scene == GameScene.COMBAT
    assert session.practice_mode is True
    assert session.active_encounter is not None

    session.active_encounter.encounter.completed = True
    session.step_tick(_frame_p1())
    assert session.scene == GameScene.KNIGHT_SELECT
    assert session.practice_mode is False
    assert session.active_encounter is None

    # Practice defeat also returns to KNIGHT_SELECT without game over.
    session.step_tick(_frame_p1(confirm=True))
    assert session.scene == GameScene.COMBAT
    assert session.practice_mode is True
    kstate = session.knight_combat[KnightId.RED]
    kstate.lives = 1
    kstate.hp = 0
    session.step_tick(_frame_p1())
    assert session.scene == GameScene.KNIGHT_SELECT
    assert session.practice_mode is False
    assert session.overworld.game_over is False


def test_status_new_day_and_location_transitions() -> None:
    session = GameSession(seed=1, start_scene=GameScene.OVERWORLD)
    session.step_tick(_frame_p1(secondary=True))
    assert session.scene == GameScene.STATUS

    session.step_tick(_frame_p1(confirm=True))
    assert session.scene == GameScene.OVERWORLD

    # Move RED to Highwood (node 1) to open LOCATION menu, then leave.
    session.step_tick(_frame_p1(dx=1))
    session.step_tick(_frame_p1(confirm=True))
    assert session.scene == GameScene.LOCATION
    assert session.location_menu is not None
    assert session.location_menu.location == LocationKind.HIGHWOOD

    for _ in range(3):
        session.step_tick(_frame_p1(dy=1))
        session.step_tick(_frame_p1())
    session.step_tick(_frame_p1(confirm=True))
    assert session.scene == GameScene.OVERWORLD

    # Move from Highwood (1) back to start node (10) -> day advances to 3, shows NEW_DAY.
    session.step_tick(_frame_p1(dx=1))
    session.step_tick(_frame_p1(confirm=True))
    assert session.overworld.day == 3
    assert session.scene == GameScene.NEW_DAY

    session.step_tick(_frame_p1(confirm=True))
    assert session.scene == GameScene.OVERWORLD


def test_victory_and_game_over_reset_to_menu_and_music() -> None:
    calls: list[str] = []

    class _FakeBundle:
        def build_overworld_graph(self) -> None:
            return None

        def load_victory_music(self) -> None:
            calls.append("victory")
            return None

        def load_title_music(self) -> None:
            calls.append("title")
            return None

    session = GameSession(
        seed=5,
        start_scene=GameScene.OVERWORLD,
        audio=AudioEngine(headless=True),
    )
    with patch.object(session, "asset_bundle", _FakeBundle()):
        session.overworld.game_over = True
        session.overworld.winner = KnightId.RED
        session.step_tick(_frame_p1())
        assert session.scene == GameScene.VICTORY
        assert "victory" in calls

        session.step_tick(_frame_p1(confirm=True))
        assert session.scene == GameScene.KNIGHT_SELECT
        assert session.overworld.game_over is False
        assert "title" in calls

        session.scene = GameScene.GAME_OVER
        session.step_tick(_frame_p1(fire=True))
        assert session.scene == GameScene.KNIGHT_SELECT
        assert calls.count("title") == 2


def test_combat_dagger_throw_and_palette_fx_step() -> None:
    session = GameSession(seed=7, start_scene=GameScene.KNIGHT_SELECT)
    session.step_tick(_frame_p1(dy=1))
    session.step_tick(_frame_p1())
    session.step_tick(_frame_p1(dy=1))
    session.step_tick(_frame_p1(confirm=True))
    assert session.scene == GameScene.COMBAT
    assert session.active_encounter is not None

    start_daggers = session.knight_combat[KnightId.RED].daggers
    start_vbl = session.palette_fx.vbl_count
    session.step_tick(_frame_p1(secondary=True))
    assert session.knight_combat[KnightId.RED].daggers == start_daggers - 1
    assert len(session.active_encounter.knight_projectiles) == 1
    assert session.palette_fx.vbl_count == start_vbl + 1


def test_intro_skip_input() -> None:
    session = GameSession(seed=3, start_scene=GameScene.TITLE)
    session.step_tick(_frame_p1(skip_intro=True))
    assert session.scene == GameScene.KNIGHT_SELECT


def test_overworld_edge_triggered_direction_and_repeat() -> None:
    session = GameSession(seed=7, start_scene=GameScene.OVERWORLD)
    assert session.selected_move_index == 0

    # Holding dx=1 for 6 ticks (a normal human tap) cycles only once.
    for _ in range(6):
        session.step_tick(_frame_p1(dx=1))
    assert session.selected_move_index == 1

    # Releasing and tapping again cycles to 2.
    session.step_tick(_frame_p1())
    session.step_tick(_frame_p1(dx=1))
    assert session.selected_move_index == 2

    # Holding past the initial repeat cooldown (12 ticks) repeats once.
    for _ in range(12):
        session.step_tick(_frame_p1(dx=1))
    assert session.selected_move_index == 0


def test_overworld_road_budget_and_end_turn() -> None:
    from pymoonstone.sim.overworld import (
        MapSiteInput,
        NodeKind,
        RegionId,
        build_overworld_graph_from_sites,
        create_campaign,
    )

    sites = (
        MapSiteInput(
            site_id=0, x=60, y=50, kind=NodeKind.START, region_id=RegionId.FOREST, neighbors=(1,)
        ),
        MapSiteInput(
            site_id=1,
            x=80,
            y=50,
            kind=NodeKind.ROAD,
            region_id=RegionId.FOREST,
            neighbors=(0, 2),
        ),
        MapSiteInput(
            site_id=2,
            x=100,
            y=50,
            kind=NodeKind.ROAD,
            region_id=RegionId.FOREST,
            neighbors=(1, 3),
        ),
        MapSiteInput(
            site_id=3,
            x=160,
            y=100,
            kind=NodeKind.STONEHENGE,
            region_id=RegionId.VALLEY,
            neighbors=(2,),
        ),
    )
    graph = build_overworld_graph_from_sites(sites, dragon_route=(2,))
    session = GameSession(seed=1, start_scene=GameScene.OVERWORLD)
    session.overworld = create_campaign((KnightId.RED,), graph=graph, seed=1)
    assert session.overworld.moves_remaining == 4

    # Single road hop decrements moves_remaining without ending the day or popping NEW_DAY.
    session.step_tick(_frame_p1(confirm=True))
    assert session.overworld.day == 1
    assert session.overworld.moves_remaining == 3
    assert session.scene == GameScene.OVERWORLD

    # Pressing weapon_switch ends the turn voluntarily and advances the day.
    session.step_tick(_frame_p1(weapon_switch=True))
    assert session.overworld.day == 2
    assert session.scene == GameScene.NEW_DAY


def test_overworld_home_castle_valley_and_maths_tower_triggers() -> None:
    from pymoonstone.sim.overworld import EncounterTrigger, MoonPhase, TriggerKind

    session = GameSession(seed=1, start_scene=GameScene.OVERWORLD)
    kstate = session.knight_combat[KnightId.RED]
    kstate.hp = 50
    session.overworld.knights[KnightId.RED].lives = 2

    session._handle_overworld_triggers(
        KnightId.RED,
        (
            EncounterTrigger(
                kind=TriggerKind.HOME_CASTLE,
                knight_id=KnightId.RED,
                node_id=10,
                day=1,
                moon_phase=MoonPhase.NEW_MOON,
            ),
        ),
    )
    assert kstate.lives == 2
    assert kstate.hp == kstate.max_hp
    assert session.last_status == "HOME CASTLE"

    session._handle_overworld_triggers(
        KnightId.RED,
        (
            EncounterTrigger(
                kind=TriggerKind.VALLEY_OF_THE_GODS,
                knight_id=KnightId.RED,
                node_id=28,
                day=1,
                moon_phase=MoonPhase.NEW_MOON,
            ),
        ),
    )
    assert kstate.lives == 3
    assert kstate.xp > 0
    assert "VALLEY BLESSING" in session.last_status

    prev_xp = kstate.xp
    session._handle_overworld_triggers(
        KnightId.RED,
        (
            EncounterTrigger(
                kind=TriggerKind.MATHS_TOWER,
                knight_id=KnightId.RED,
                node_id=30,
                day=1,
                moon_phase=MoonPhase.NEW_MOON,
            ),
        ),
    )
    assert kstate.xp > prev_xp
    assert kstate.gold > 0
    assert "MATHS TOWER" in session.last_status


def test_combat_sfx_keys_and_region_arena_selection() -> None:
    from pymoonstone.assets.arena import DecodedArena
    from pymoonstone.assets.palette import default_combat_palette
    from pymoonstone.assets.picture import DecodedPicture
    from pymoonstone.sim.knight_combat import KnightAction
    from pymoonstone.sim.overworld import EncounterTrigger, MoonPhase, TriggerKind
    from pymoonstone.sim.types import EntityId, Pos25D

    requested_arenas: list[str] = []
    played_sfx: list[str] = []

    class _FakeBundle:
        def build_overworld_graph(self) -> None:
            return None

        def get_mog_tables(self) -> None:
            return None

        def get_fight_palette_tables(self) -> None:
            return None

        def get_arena(self, name: str) -> tuple[DecodedArena, DecodedPicture]:
            requested_arenas.append(name)
            arena = DecodedArena(name=name, region=0, is_lair=False, obstacles=(), tiles=())
            pic = DecodedPicture(
                width=320,
                height=200,
                planes=5,
                packed_size=0,
                raw_palette=b"",
                raw_planes=b"",
                palette=default_combat_palette(),
                pixels=np.zeros((200, 320), dtype=np.uint8),
            )
            return arena, pic

    session = GameSession(seed=7, start_scene=GameScene.OVERWORLD)
    with (
        patch.object(session, "asset_bundle", _FakeBundle()),
        patch.object(
            session, "_try_play_sample", side_effect=lambda *names: played_sfx.extend(names)
        ),
    ):
        # Wandering monsters across the 4 regions pick fo1.t, gl1.t, sw1.t, wa1.t.
        for node_id in (11, 21, 31, 41):
            session._select_combat_arena(
                EncounterTrigger(
                    kind=TriggerKind.WANDERING_MONSTER,
                    knight_id=KnightId.RED,
                    node_id=node_id,
                    day=1,
                    moon_phase=MoonPhase.NEW_MOON,
                )
            )
        # Lairs across the 4 regions pick fol1.t, gll1.t, swl1.t, wal1.t.
        for lair_idx, node_id in enumerate((12, 22, 32, 42)):
            session._select_combat_arena(
                EncounterTrigger(
                    kind=TriggerKind.LAIR_BOSS,
                    knight_id=KnightId.RED,
                    node_id=node_id,
                    day=1,
                    moon_phase=MoonPhase.NEW_MOON,
                    lair_index=lair_idx,
                )
            )
        assert requested_arenas == [
            "fo1.t",
            "gl1.t",
            "sw1.t",
            "wa1.t",
            "fol1.t",
            "gll1.t",
            "swl1.t",
            "wal1.t",
        ]

        # Start combat and verify COMBAT_SFX_KEYS are used.
        session._start_combat(
            EncounterTrigger(
                kind=TriggerKind.WANDERING_MONSTER,
                knight_id=KnightId.RED,
                node_id=11,
                day=1,
                moon_phase=MoonPhase.NEW_MOON,
            ),
            KnightId.RED,
        )
        assert session.active_encounter is not None
        kstate = session.knight_combat[KnightId.RED]
        session._spawn_knight_dagger(kstate, session.active_encounter)
        assert "throw" in played_sfx

        # Block and hurt/death SFX.
        kstate.action = KnightAction.BLOCK
        session._damage_knight_with_fx(
            kstate,
            session.active_encounter,
            10,
            EntityId(100),
            Pos25D(x=kstate.x + 10, y=kstate.y, z=0),
        )
        assert "block" in played_sfx
        kstate.action = KnightAction.IDLE
        session._damage_knight_with_fx(
            kstate,
            session.active_encounter,
            10,
            EntityId(100),
            Pos25D(x=kstate.x + 10, y=kstate.y, z=0),
        )
        assert "hurt" in played_sfx
