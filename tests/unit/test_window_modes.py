"""Unit tests for fullscreen / window modes and pre-router hotkey consumption (T13.3)."""

from __future__ import annotations

import pygame
import pytest

from harddaysengine.app.audio import AudioEngine
from harddaysengine.app.game import GameScene, GameSession
from harddaysengine.app.input import InputFrame, InputRouter, ReplayRecording
from harddaysengine.app.window import WindowController
from harddaysengine.render.presentation import (
    CrtMaskMode,
    CrtSettings,
    DisplaySettings,
    FilterPreset,
    FullscreenMode,
)
from harddaysengine.sim.types import KnightId


class _FakeWindowBackend:
    def __init__(self, initial_size: tuple[int, int] = (960, 768)) -> None:
        self.size = initial_size
        self.calls: list[tuple[str, tuple[int, int] | None]] = []

    def set_windowed(self, size: tuple[int, int]) -> None:
        self.size = size
        self.calls.append(("windowed", size))

    def set_desktop_fullscreen(self) -> None:
        self.size = (1920, 1080)
        self.calls.append(("desktop", None))

    def set_exclusive_fullscreen(self) -> None:
        self.size = (1920, 1080)
        self.calls.append(("exclusive", None))

    def get_window_size(self) -> tuple[int, int]:
        return self.size

    def get_refresh_rate(self) -> int:
        return 60


def _dispatch(
    window: WindowController, router: InputRouter, events: list[pygame.event.Event]
) -> None:
    for event in events:
        if not window.consume_event(event):
            router.process_pygame_event(event)


def test_f11_toggles_desktop_fullscreen_and_preserves_resized_window() -> None:
    backend = _FakeWindowBackend((960, 768))
    window = WindowController((960, 768), FullscreenMode.OFF, backend=backend)
    window.open()
    assert window.mode is FullscreenMode.OFF
    assert backend.calls == [("windowed", (960, 768))]

    # User resizes window while windowed
    backend.size = (1200, 900)
    assert not window.consume_event(pygame.event.Event(pygame.VIDEORESIZE, w=1200, h=900))
    assert window.windowed_size == (1200, 900)

    # F11 enters desktop fullscreen; repeat press is ignored; keyup is consumed
    assert window.consume_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F11, mod=0))
    assert window.mode is FullscreenMode.DESKTOP
    assert window.consume_event(
        pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F11, mod=0, repeat=1)
    )
    assert window.mode is FullscreenMode.DESKTOP
    assert window.consume_event(pygame.event.Event(pygame.KEYUP, key=pygame.K_F11, mod=0))

    # Second F11 restores the 1200x900 resized window
    assert window.consume_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F11, mod=0))
    assert window.mode is FullscreenMode.OFF
    assert backend.calls[-1] == ("windowed", (1200, 900))


def test_exclusive_fullscreen_startup_toggles_to_windowed_and_back() -> None:
    backend = _FakeWindowBackend((960, 768))
    window = WindowController((960, 768), FullscreenMode.EXCLUSIVE, backend=backend)
    window.open()
    assert window.mode is FullscreenMode.EXCLUSIVE
    assert backend.calls == [("exclusive", None)]

    assert window.toggle_fullscreen() is FullscreenMode.OFF
    assert backend.calls[-1] == ("windowed", (960, 768))

    assert window.toggle_fullscreen() is FullscreenMode.EXCLUSIVE
    assert backend.calls[-1] == ("exclusive", None)


@pytest.mark.parametrize(
    ("enter_key", "alt_mod"),
    [
        (pygame.K_RETURN, pygame.KMOD_LALT),
        (pygame.K_RETURN, pygame.KMOD_RALT),
        (pygame.K_KP_ENTER, pygame.KMOD_LALT),
    ],
)
def test_alt_enter_press_and_release_are_consumed_before_router(
    enter_key: int, alt_mod: int
) -> None:
    backend = _FakeWindowBackend((960, 768))
    window = WindowController((960, 768), FullscreenMode.OFF, backend=backend)
    router = InputRouter()

    # Alt+Enter down, Alt up, Enter up (mod=0 on release)
    _dispatch(
        window,
        router,
        [
            pygame.event.Event(pygame.KEYDOWN, key=pygame.K_LALT, mod=alt_mod),
            pygame.event.Event(pygame.KEYDOWN, key=enter_key, mod=alt_mod),
        ],
    )
    assert window.mode is FullscreenMode.DESKTOP
    frame_down = router.sample_frame()
    assert not frame_down.for_player(KnightId.RED).confirm
    assert not frame_down.for_player(KnightId.YELLOW).confirm
    assert not frame_down.for_player(KnightId.RED).skip_intro

    _dispatch(
        window,
        router,
        [
            pygame.event.Event(pygame.KEYUP, key=pygame.K_LALT, mod=0),
            pygame.event.Event(pygame.KEYUP, key=enter_key, mod=0),
        ],
    )
    frame_up = router.sample_frame()
    assert not frame_up.for_player(KnightId.RED).confirm
    assert not frame_up.for_player(KnightId.YELLOW).confirm

    # Plain Return afterwards still confirms normally
    _dispatch(window, router, [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0)])
    frame_plain = router.sample_frame()
    assert frame_plain.for_player(KnightId.RED).confirm


def test_alt_enter_on_menu_does_not_confirm() -> None:
    backend = _FakeWindowBackend((960, 768))
    window = WindowController((960, 768), FullscreenMode.OFF, backend=backend)
    router = InputRouter()
    audio = AudioEngine(headless=True)
    try:
        session = GameSession(audio=audio)
        assert session.scene is GameScene.TITLE

        # Press and release Alt+Enter on the title menu: toggles fullscreen, does NOT confirm
        _dispatch(
            window,
            router,
            [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=pygame.KMOD_LALT)],
        )
        session.step_tick(router.sample_frame())
        _dispatch(
            window,
            router,
            [pygame.event.Event(pygame.KEYUP, key=pygame.K_RETURN, mod=0)],
        )
        session.step_tick(router.sample_frame())
        assert window.mode is FullscreenMode.DESKTOP
        assert session.scene is GameScene.TITLE

        # Plain Return afterwards confirms the title menu into KNIGHT_SELECT
        _dispatch(window, router, [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0)])
        session.step_tick(router.sample_frame())
        _dispatch(window, router, [pygame.event.Event(pygame.KEYUP, key=pygame.K_RETURN, mod=0)])
        session.step_tick(router.sample_frame())
        assert session.scene is GameScene.KNIGHT_SELECT
    finally:
        audio.close()


def test_replay_with_fullscreen_toggles_produces_identical_hashes() -> None:
    backend = _FakeWindowBackend((960, 768))
    window = WindowController((960, 768), FullscreenMode.OFF, backend=backend)
    router = InputRouter()
    audio_live = AudioEngine(headless=True)
    audio_replay = AudioEngine(headless=True)
    try:
        live = GameSession(seed=0x12345678, audio=audio_live)
        recorded: list[InputFrame] = []
        live_hashes: list[str] = []

        script: dict[int, list[pygame.event.Event]] = {
            1: [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F11, mod=0)],
            2: [pygame.event.Event(pygame.KEYUP, key=pygame.K_F11, mod=0)],
            3: [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0)],
            4: [pygame.event.Event(pygame.KEYUP, key=pygame.K_RETURN, mod=0)],
            6: [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=pygame.KMOD_LALT)],
            7: [pygame.event.Event(pygame.KEYUP, key=pygame.K_RETURN, mod=0)],
            9: [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_DOWN, mod=0)],
            10: [pygame.event.Event(pygame.KEYUP, key=pygame.K_DOWN, mod=0)],
            12: [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F11, mod=0)],
            13: [pygame.event.Event(pygame.KEYUP, key=pygame.K_F11, mod=0)],
        }

        for tick in range(16):
            _dispatch(window, router, script.get(tick, []))
            frame = router.sample_frame()
            recorded.append(frame)
            live.step_tick(frame)
            live_hashes.append(live.state_hash())

        assert window.mode is FullscreenMode.DESKTOP

        rec = ReplayRecording(seed=0x12345678, players=(KnightId.RED,), frames=tuple(recorded))
        restored = ReplayRecording.from_dict(rec.to_dict())
        replayed = GameSession(seed=restored.seed, players=restored.players, audio=audio_replay)
        replay_hashes: list[str] = []
        for frame in restored.frames:
            replayed.step_tick(frame)
            replay_hashes.append(replayed.state_hash())

        assert replay_hashes == live_hashes
    finally:
        audio_live.close()
        audio_replay.close()


def test_display_preview_applies_or_discards_without_recording_controls() -> None:
    settings = DisplaySettings(crt=CrtSettings(curvature=0.29))
    window = WindowController((960, 768), initial_crt=settings.crt, backend=_FakeWindowBackend())
    router = InputRouter()
    baseline = GameSession()
    preview_session = GameSession()

    def press(key: int) -> None:
        for event_type in (pygame.KEYDOWN, pygame.KEYUP):
            _dispatch(window, router, [pygame.event.Event(event_type, key=key)])
            frame = router.sample_frame()
            assert frame == InputRouter().sample_frame()
            preview_session.step_tick(frame)
            baseline.step_tick(InputFrame())
            assert preview_session.state_hash() == baseline.state_hash()

    def edit_crt() -> DisplaySettings:
        press(pygame.K_F10)
        press(pygame.K_F9)
        press(pygame.K_RIGHT)
        for _ in range(5):
            press(pygame.K_DOWN)
            press(pygame.K_RIGHT)
        return window.apply_display_settings(settings)

    draft = edit_crt()
    assert draft.filter is FilterPreset.CRT
    assert draft.crt == CrtSettings(
        mask=CrtMaskMode.SHADOW, mask_strength=0.30, scanlines=0.70, curvature=0.30, bloom=0.27
    )
    assert window.filter_preset is FilterPreset.FAITHFUL
    press(pygame.K_ESCAPE)
    assert window.display_preview is None
    assert window.apply_display_settings(settings) == settings

    assert edit_crt() == draft
    press(pygame.K_F11)
    assert window.mode is FullscreenMode.DESKTOP
    press(pygame.K_RETURN)
    assert window.display_preview is None
    assert window.filter_preset is FilterPreset.CRT
    assert window.apply_display_settings(settings).crt == draft.crt

    press(pygame.K_F10)
    press(pygame.K_LEFT)
    press(pygame.K_F10)
    assert window.display_preview is None
    assert window.apply_display_settings(settings).crt == draft.crt
    assert window.filter_preset is FilterPreset.CRT
