"""End-to-end deterministic game session, input router, and replay tests (T8.3)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pygame

from pymoonstone.app.game import GameScene, GameSession, run_headless_session
from pymoonstone.app.input import (
    InputFrame,
    InputRouter,
    PlayerInput,
    ReplayRecording,
    load_replay,
    save_replay,
)
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
    # Tick 2-3: Overworld, cycle move to wilderness node 11 and confirm move
    # Start node for RED is 10 with neighbors (0, 1, 11); index 2 is 11 (wilderness).
    frames.append(_frame_p1(dx=1))
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
