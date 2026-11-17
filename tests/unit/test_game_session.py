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
from pymoonstone.sim.overworld import scan
from pymoonstone.sim.types import KnightId
from tests.unit.map_setup import KINDS, RULES
from tests.unit.turn_setup import TURN_RULES

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from pymoonstone.sim.map_rules import MapRules
    from pymoonstone.sim.turn_rules import TurnRules


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
    """Script title, menu and map navigation without original disk assets."""
    frames: list[InputFrame] = []
    # Tick 0: Title -> Knight Select
    frames.append(_frame_p1(confirm=True))
    # Tick 1: Knight Select (keep 1 knight) -> Overworld
    frames.append(_frame_p1(confirm=True))
    # Missing original assets must not fabricate navigation or encounters.
    frames.append(_frame_p1(dx=1))
    frames.append(_frame_p1())
    frames.append(_frame_p1(dx=1))
    frames.append(_frame_p1(confirm=True))
    # Continue the input stream; real-disk combat has separate host replay tests.
    for tick in range(180):
        if tick < 70:
            frames.append(_frame_p1(dx=1, run=True))
        elif tick % 20 == 0:
            frames.append(_frame_p1(dx=1, fire=True))
        else:
            frames.append(_frame_p1(dx=0, fire=False))
    return frames


def test_campaign_navigation_and_replay_determinism_without_disks(tmp_path: Path) -> None:
    # Missing assets are deterministic for every replay seed.
    chosen_seed = 7
    probe = GameSession(seed=chosen_seed, start_scene=GameScene.OVERWORLD)
    # Probe navigation without inventing a replacement map.
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
    assert run1.session.overworld.rules is None
    assert run1.session.last_status == "ORIGINAL DISKS REQUIRED"
    assert run1.scene in (GameScene.OVERWORLD, GameScene.COMBAT, GameScene.VICTORY)


def test_menu_roster_and_overworld_pause() -> None:
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

    session2.overworld.install(RULES, KINDS, 7)
    x = session2.overworld.current_actor.map_x
    session2.step_tick(_frame_p1(dx=1))
    session2.step_tick(_frame_p1(dx=1))
    assert session2.overworld.current_actor.map_x == x + 1
    assert session2.overworld.spent == 1


def test_status_and_proximity_location_transitions() -> None:
    session = GameSession(seed=1, start_scene=GameScene.OVERWORLD)
    session.overworld.install(RULES, KINDS, 1)
    session.step_tick(_frame_p1(secondary=True))
    session.step_tick(_frame_p1(secondary=True))
    assert session.scene == GameScene.STATUS
    session.step_tick(_frame_p1(confirm=True))
    assert session.scene == GameScene.OVERWORLD
    actor = session.overworld.current_actor
    actor.map_x = actor.map_y = 50
    scan(session.overworld)
    session.step_tick(_frame_p1(confirm=True))
    session.step_tick(_frame_p1(confirm=True))
    assert session.scene == GameScene.LOCATION
    assert session.location_menu is not None
    assert session.location_menu.location == LocationKind.HIGHWOOD
    assert session.overworld.day == 0


def test_victory_and_game_over_reset_to_menu_and_music() -> None:
    calls: list[str] = []

    class _FakeBundle:
        def get_map_rules(self) -> MapRules:
            return RULES

        def get_turn_rules(self) -> TurnRules:
            return TURN_RULES

        def get_test_picture(self, index: int) -> None:
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

        session.overworld.turn_rules = None
        session.scene = GameScene.GAME_OVER
        session.step_tick(_frame_p1(fire=True))
        assert session.scene == GameScene.KNIGHT_SELECT
        assert calls.count("title") == 2


def test_intro_skip_input() -> None:
    session = GameSession(seed=3, start_scene=GameScene.TITLE)
    session.step_tick(_frame_p1(skip_intro=True))
    assert session.scene == GameScene.KNIGHT_SELECT


def test_map_key_translation_and_replay_roundtrip() -> None:
    router = InputRouter(init_joysticks=False)
    for raw, translated in (
        (pygame.K_3, "3"),
        (pygame.K_e, "E"),
        (pygame.K_q, "Q"),
        (pygame.K_SPACE, " "),
    ):
        router.process_pygame_event(pygame.event.Event(pygame.KEYDOWN, key=raw))
        frame = router.sample_frame()
        assert frame.key == ord(translated)
        assert InputFrame.from_dict(frame.to_dict()) == frame
        assert router.sample_frame().key == 0
        router.process_pygame_event(pygame.event.Event(pygame.KEYUP, key=raw))
    assert InputFrame.from_dict({"players": {}}).key == 0
