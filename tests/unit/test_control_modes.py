"""Unit tests for host control modes: standard (Mode A) and shared-pad (Mode B) (T17.1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pygame
import pytest

from harddaysengine.app.config import ConfigError, resolve_control_mode
from harddaysengine.app.game import ActiveEncounter, GameScene, GameSession
from harddaysengine.app.input import (
    ControlMode,
    ControlTargets,
    InputFrame,
    InputRouter,
    PlayerInput,
    resolve_active_control_mode,
)
from harddaysengine.cli.main import build_parser
from harddaysengine.sim.overworld import EncounterTrigger, TriggerKind
from harddaysengine.sim.types import KnightId

if TYPE_CHECKING:
    from pathlib import Path


@dataclass
class _FakeJoystick:
    instance_id: int

    def get_instance_id(self) -> int:
        return self.instance_id


def _install_fake_joysticks(monkeypatch: pytest.MonkeyPatch, count: int) -> None:
    monkeypatch.setattr(pygame.joystick, "get_init", lambda: True)
    monkeypatch.setattr(pygame.joystick, "get_count", lambda: count)
    monkeypatch.setattr(pygame.joystick, "Joystick", lambda idx: _FakeJoystick(idx + 100))


def test_resolve_active_control_mode_table() -> None:
    assert resolve_active_control_mode(ControlMode.AUTO, 0) == ControlMode.STANDARD
    assert resolve_active_control_mode(ControlMode.AUTO, 1) == ControlMode.SHARED_PAD
    assert resolve_active_control_mode(ControlMode.AUTO, 2) == ControlMode.STANDARD
    assert resolve_active_control_mode(ControlMode.AUTO, 4) == ControlMode.STANDARD
    assert resolve_active_control_mode(ControlMode.STANDARD, 1) == ControlMode.STANDARD
    assert resolve_active_control_mode(ControlMode.SHARED_PAD, 0) == ControlMode.SHARED_PAD
    assert resolve_active_control_mode(ControlMode.SHARED_PAD, 2) == ControlMode.SHARED_PAD


def test_auto_mode_updates_dynamically_on_hotplug(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_joysticks(monkeypatch, 0)
    router = InputRouter(init_joysticks=True, control_mode=ControlMode.AUTO)
    assert router.configured_control_mode == ControlMode.AUTO
    assert router.active_control_mode == ControlMode.STANDARD

    # 0 -> 1 pad switches auto to shared-pad
    router.process_pygame_event(pygame.event.Event(pygame.JOYDEVICEADDED, {"device_index": 0}))
    assert router.active_control_mode == ControlMode.SHARED_PAD

    # 1 -> 2 pads switches auto back to standard
    router.process_pygame_event(pygame.event.Event(pygame.JOYDEVICEADDED, {"device_index": 1}))
    assert router.active_control_mode == ControlMode.STANDARD

    # 2 -> 1 pad switches auto back to shared-pad
    router.process_pygame_event(pygame.event.Event(pygame.JOYDEVICEREMOVED, {"instance_id": 101}))
    assert router.active_control_mode == ControlMode.SHARED_PAD

    # 1 -> 0 pads switches auto back to standard
    router.process_pygame_event(pygame.event.Event(pygame.JOYDEVICEREMOVED, {"instance_id": 100}))
    assert router.active_control_mode == ControlMode.STANDARD

    # Explicit standard and shared-pad modes ignore hotplug transitions
    explicit_std = InputRouter(init_joysticks=True, control_mode=ControlMode.STANDARD)
    explicit_std.process_pygame_event(
        pygame.event.Event(pygame.JOYDEVICEADDED, {"device_index": 0})
    )
    assert explicit_std.active_control_mode == ControlMode.STANDARD

    explicit_shared = InputRouter(init_joysticks=True, control_mode=ControlMode.SHARED_PAD)
    explicit_shared.process_pygame_event(
        pygame.event.Event(pygame.JOYDEVICEADDED, {"device_index": 0})
    )
    explicit_shared.process_pygame_event(
        pygame.event.Event(pygame.JOYDEVICEADDED, {"device_index": 1})
    )
    assert explicit_shared.active_control_mode == ControlMode.SHARED_PAD


def test_config_and_cli_control_mode_resolution(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    assert resolve_control_mode(cfg) == ControlMode.AUTO

    cfg.write_text('[input]\nmode = "shared-pad"\n', encoding="utf-8")
    assert resolve_control_mode(cfg) == ControlMode.SHARED_PAD
    assert resolve_control_mode(cfg, cli_mode="standard") == ControlMode.STANDARD

    cfg.write_text('[input]\nmode = "bogus"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="mode must be one of"):
        resolve_control_mode(cfg)

    cfg.write_text("[input]\nextra = 1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown \\[input\\] key"):
        resolve_control_mode(cfg)

    cfg.write_text('input = "bad"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="\\[input\\] must be a table"):
        resolve_control_mode(cfg)

    args = build_parser().parse_args(["--control-mode", "shared-pad"])
    assert args.control_mode == "shared-pad"


def test_shared_pad_routes_pad_and_p1_keyboard_backup_to_active_player(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_joysticks(monkeypatch, 1)
    router = InputRouter(init_joysticks=True, control_mode=ControlMode.AUTO)

    # Pad moves right + fires during Player 3's turn (slot GREEN)
    router.process_pygame_event(
        pygame.event.Event(pygame.JOYAXISMOTION, {"instance_id": 100, "axis": 0, "value": 0.9})
    )
    router.process_pygame_event(
        pygame.event.Event(pygame.JOYBUTTONDOWN, {"instance_id": 100, "button": 0})
    )
    frame = router.sample_frame(targets=ControlTargets(primary=KnightId.GREEN))
    assert frame.for_player(KnightId.GREEN).dx == 1
    assert frame.for_player(KnightId.GREEN).fire is True
    assert frame.for_player(KnightId.RED) == PlayerInput()
    assert frame.for_player(KnightId.BLUE) == PlayerInput()

    # Release pad; Player 1 keyboard arrows + Space still work as backup for active Player 2
    router.process_pygame_event(
        pygame.event.Event(pygame.JOYAXISMOTION, {"instance_id": 100, "axis": 0, "value": 0.0})
    )
    router.process_pygame_event(
        pygame.event.Event(pygame.JOYBUTTONUP, {"instance_id": 100, "button": 0})
    )
    router.process_pygame_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_LEFT}))
    router.process_pygame_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_SPACE}))
    backup = router.sample_frame(targets=ControlTargets(primary=KnightId.BLUE))
    assert backup.for_player(KnightId.BLUE).dx == -1
    assert backup.for_player(KnightId.BLUE).fire is True
    assert backup.for_player(KnightId.RED) == PlayerInput()
    assert backup.key == pygame.K_SPACE


def test_shared_pad_duel_splits_pad_for_initiator_and_wasd_space_for_opponent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_joysticks(monkeypatch, 1)
    router = InputRouter(init_joysticks=True, control_mode=ControlMode.SHARED_PAD)

    # Player 2 (slot BLUE) initiated a duel against Player 1 (slot RED)
    targets = ControlTargets(primary=KnightId.BLUE, duel_opponent=KnightId.RED)

    # Initiator moves right + fires on pad; opponent moves left + fires on A + Space
    router.process_pygame_event(
        pygame.event.Event(pygame.JOYAXISMOTION, {"instance_id": 100, "axis": 0, "value": 0.9})
    )
    router.process_pygame_event(
        pygame.event.Event(pygame.JOYBUTTONDOWN, {"instance_id": 100, "button": 0})
    )
    router.process_pygame_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_a}))
    router.process_pygame_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_SPACE}))
    frame = router.sample_frame(targets=targets)

    initiator = frame.for_player(KnightId.BLUE)
    opponent = frame.for_player(KnightId.RED)
    assert initiator.dx == 1
    assert initiator.fire is True
    assert opponent.dx == -1
    assert opponent.fire is True

    # Release pad; opponent pressing W + Space must NOT leak into initiator
    router.process_pygame_event(
        pygame.event.Event(pygame.JOYAXISMOTION, {"instance_id": 100, "axis": 0, "value": 0.0})
    )
    router.process_pygame_event(
        pygame.event.Event(pygame.JOYBUTTONUP, {"instance_id": 100, "button": 0})
    )
    router.process_pygame_event(pygame.event.Event(pygame.KEYUP, {"key": pygame.K_a}))
    router.process_pygame_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_w}))
    frame2 = router.sample_frame(targets=targets)
    assert frame2.for_player(KnightId.BLUE) == PlayerInput()
    assert frame2.for_player(KnightId.RED).dy == -1
    assert frame2.for_player(KnightId.RED).fire is True


def test_shared_pad_forwards_pause_to_slot_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_joysticks(monkeypatch, 1)
    router = InputRouter(init_joysticks=True, control_mode=ControlMode.SHARED_PAD)

    # Active player is slot YELLOW; pressing Start (button 7) pauses via slot RED
    router.process_pygame_event(
        pygame.event.Event(pygame.JOYBUTTONDOWN, {"instance_id": 100, "button": 7})
    )
    frame = router.sample_frame(targets=ControlTargets(primary=KnightId.YELLOW))
    assert frame.for_player(KnightId.YELLOW).pause is True
    assert frame.for_player(KnightId.RED).pause is True
    assert frame.for_player(KnightId.RED).fire is False

    # During a duel between GREEN and YELLOW, pressing P pauses without moving RED
    router.process_pygame_event(
        pygame.event.Event(pygame.JOYBUTTONUP, {"instance_id": 100, "button": 7})
    )
    router.process_pygame_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_p}))
    duel_frame = router.sample_frame(
        targets=ControlTargets(primary=KnightId.GREEN, duel_opponent=KnightId.YELLOW)
    )
    assert duel_frame.for_player(KnightId.RED).pause is True
    assert duel_frame.for_player(KnightId.GREEN).dx == 0


def test_game_session_control_targets_and_slot_isolation() -> None:
    # Roster where portrait picks permute KnightId vs player slot:
    # slot 0 (RED) -> BLUE, slot 1 (BLUE) -> YELLOW
    session = GameSession(players=(KnightId.BLUE, KnightId.YELLOW), start_scene=GameScene.OVERWORLD)
    session.overworld.turn_index = session.overworld.turn_order.index(KnightId.YELLOW)
    assert session.control_targets() == ControlTargets(primary=KnightId.BLUE)

    # Full 4-slot InputFrame from router must never leak slot 1 (BLUE) into slot 0 (BLUE knight)
    full_frame = InputFrame(
        players={
            KnightId.RED: PlayerInput(),
            KnightId.BLUE: PlayerInput(dx=1, fire=True),
            KnightId.GREEN: PlayerInput(),
            KnightId.YELLOW: PlayerInput(),
        }
    )
    assert session._player_input(full_frame, KnightId.BLUE) == PlayerInput()
    assert session._player_input(full_frame, KnightId.YELLOW) == PlayerInput(dx=1, fire=True)

    # Sparse synthetic InputFrame keyed directly by KnightId still works
    sparse = InputFrame(players={KnightId.BLUE: PlayerInput(dy=-1)})
    assert session._player_input(sparse, KnightId.BLUE) == PlayerInput(dy=-1)

    # Two-human duel: initiator (YELLOW, slot 1 = BLUE) gets primary, defender (BLUE, slot 0 = RED)
    # gets duel_opponent
    trigger = EncounterTrigger(
        TriggerKind.PVP_DUEL, KnightId.YELLOW, -1, 0, 0, opponent_knight_id=KnightId.BLUE
    )
    stub_runtime: object = object()
    session.scene = GameScene.COMBAT
    session.active_encounter = ActiveEncounter(
        trigger=trigger,
        runtime=stub_runtime,
        knight_ids=(KnightId.YELLOW, KnightId.BLUE),
    )
    assert session.control_targets() == ControlTargets(
        primary=KnightId.BLUE, duel_opponent=KnightId.RED
    )

    # Practice duel (BLUE vs YELLOW): slot 0 (RED) gets primary, slot 1 (BLUE) gets duel_opponent
    session.active_encounter = ActiveEncounter(
        trigger=trigger,
        runtime=stub_runtime,
        knight_ids=(KnightId.BLUE, KnightId.YELLOW),
    )
    assert session.control_targets() == ControlTargets(
        primary=KnightId.RED, duel_opponent=KnightId.BLUE
    )

    # Single-human fight (e.g. creature lair): only primary is set
    session.active_encounter = ActiveEncounter(
        trigger=trigger,
        runtime=stub_runtime,
        knight_ids=(KnightId.YELLOW,),
    )
    assert session.control_targets() == ControlTargets(primary=KnightId.BLUE, duel_opponent=None)
