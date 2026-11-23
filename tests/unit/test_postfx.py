"""Unit tests for post-process pass specifications, presets, and F9 hotkey cycling (T13.5)."""

from __future__ import annotations

import pygame
import pytest

from harddaysengine.app.audio import AudioEngine
from harddaysengine.app.game import GameSession
from harddaysengine.app.input import InputFrame, InputRouter, ReplayRecording
from harddaysengine.app.window import WindowController
from harddaysengine.render.postfx import (
    CRT_BLOOM_H_PASS,
    CRT_BLOOM_V_PASS,
    DEFAULT_POST_VERT,
    IDENTITY_PASS,
    IDENTITY_POST_FRAG,
    SCALEFX_INTERP_PASS,
    SCALEFX_METRIC_PASS,
    SCALEFX_PASSES,
    SCALEFX_RESOLVE_PASS,
    SCALEFX_STRENGTH_PASS,
    SMOOTH_PASS,
    PassSampling,
    PostPassSpec,
    make_identity_pass,
    preset_passes,
)
from harddaysengine.render.presentation import (
    CrtSettings,
    DisplaySettings,
    FilterPreset,
    FullscreenMode,
)
from harddaysengine.sim.types import KnightId


class _DummyWindowBackend:
    def __init__(self) -> None:
        self.size = (960, 768)

    def set_windowed(self, size: tuple[int, int]) -> None:
        self.size = size

    def set_desktop_fullscreen(self) -> None:
        self.size = (1920, 1080)

    def set_exclusive_fullscreen(self) -> None:
        self.size = (1920, 1080)

    def get_window_size(self) -> tuple[int, int]:
        return self.size

    def get_refresh_rate(self) -> int:
        return 60


def test_post_pass_spec_defaults_and_validation() -> None:
    assert IDENTITY_PASS.name == "identity"
    assert IDENTITY_PASS.fragment_shader == IDENTITY_POST_FRAG
    assert IDENTITY_PASS.vertex_shader == DEFAULT_POST_VERT
    assert IDENTITY_PASS.scale_x == 1
    assert IDENTITY_PASS.scale_y == 1
    assert IDENTITY_PASS.sampling is PassSampling.NEAREST
    assert IDENTITY_PASS.uniforms == ()

    custom = make_identity_pass("upscale2x", scale_x=2, scale_y=2, sampling=PassSampling.LINEAR)
    assert custom.name == "upscale2x"
    assert custom.scale_x == 2
    assert custom.scale_y == 2
    assert custom.sampling is PassSampling.LINEAR

    with pytest.raises(ValueError, match="name"):
        PostPassSpec(name="", fragment_shader=IDENTITY_POST_FRAG)
    with pytest.raises(ValueError, match="fragment_shader"):
        PostPassSpec(name="bad", fragment_shader="")
    with pytest.raises(ValueError, match="scale"):
        PostPassSpec(name="bad", fragment_shader=IDENTITY_POST_FRAG, scale_x=0)
    with pytest.raises(ValueError, match="scale"):
        PostPassSpec(name="bad", fragment_shader=IDENTITY_POST_FRAG, scale_y=-1)


def test_preset_passes_for_built_in_presets() -> None:
    assert preset_passes(FilterPreset.FAITHFUL) == ()
    assert preset_passes(FilterPreset.SCANLINES) == ()
    assert preset_passes(FilterPreset.CRT) == (CRT_BLOOM_H_PASS, CRT_BLOOM_V_PASS)
    assert preset_passes(FilterPreset.CRT, crt_bloom_enabled=False) == ()
    assert preset_passes(FilterPreset.SMOOTH) == (SMOOTH_PASS,)
    assert SMOOTH_PASS.scale_x == 4
    assert SMOOTH_PASS.scale_y == 4
    assert SMOOTH_PASS.sampling is PassSampling.NEAREST
    assert preset_passes(FilterPreset.SCALEFX) == SCALEFX_PASSES
    assert SCALEFX_PASSES == (
        SCALEFX_METRIC_PASS,
        SCALEFX_STRENGTH_PASS,
        SCALEFX_RESOLVE_PASS,
        SCALEFX_INTERP_PASS,
    )
    assert [(p.scale_x, p.scale_y, p.sampling) for p in SCALEFX_PASSES] == [
        (1, 1, PassSampling.NEAREST),
        (1, 1, PassSampling.NEAREST),
        (1, 1, PassSampling.NEAREST),
        (3, 3, PassSampling.NEAREST),
    ]


def test_multi_pass_spec_accepts_original_source_shader_chain() -> None:
    mask_pass = PostPassSpec(
        name="mask",
        fragment_shader=IDENTITY_POST_FRAG,
        scale_x=1,
        scale_y=1,
        sampling=PassSampling.NEAREST,
    )
    combine_pass = PostPassSpec(
        name="combine_orig",
        fragment_shader="uniform sampler2D u_input_tex; uniform sampler2D u_orig_tex;",
        scale_x=3,
        scale_y=3,
        sampling=PassSampling.NEAREST,
    )
    assert (mask_pass.scale_x, mask_pass.scale_y) == (1, 1)
    assert (combine_pass.scale_x, combine_pass.scale_y) == (3, 3)
    assert "u_orig_tex" in combine_pass.fragment_shader


def test_f9_cycles_filter_preset_and_is_consumed_before_router() -> None:
    backend = _DummyWindowBackend()
    window = WindowController(
        (960, 768),
        FullscreenMode.OFF,
        initial_filter=FilterPreset.FAITHFUL,
        initial_crt=CrtSettings(),
        backend=backend,
    )
    router = InputRouter()
    base_settings = DisplaySettings(filter=FilterPreset.FAITHFUL)
    assert window.filter_preset is FilterPreset.FAITHFUL
    assert window.apply_display_settings(base_settings) == base_settings

    # F9 keydown cycles FAITHFUL -> SCANLINES -> CRT -> FAITHFUL
    assert window.consume_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F9, mod=0))
    assert window.filter_preset is FilterPreset.SCANLINES
    assert window.apply_display_settings(base_settings).filter is FilterPreset.SCANLINES

    # Held key repeat is ignored
    assert window.consume_event(
        pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F9, mod=0, repeat=1)
    )
    assert window.filter_preset is FilterPreset.SCANLINES

    # Matching KEYUP is consumed
    assert window.consume_event(pygame.event.Event(pygame.KEYUP, key=pygame.K_F9, mod=0))
    assert router.sample_frame() == InputFrame(
        players={kid: router.sample_frame().for_player(kid) for kid in KnightId}
    )

    # Second F9 cycles SCANLINES -> CRT, third CRT -> SMOOTH, fourth SMOOTH -> SCALEFX, fifth wraps
    assert window.consume_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F9, mod=0))
    assert window.filter_preset is FilterPreset.CRT
    assert window.consume_event(pygame.event.Event(pygame.KEYUP, key=pygame.K_F9, mod=0))

    assert window.consume_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F9, mod=0))
    assert window.filter_preset is FilterPreset.SMOOTH
    assert window.consume_event(pygame.event.Event(pygame.KEYUP, key=pygame.K_F9, mod=0))

    assert window.consume_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F9, mod=0))
    assert window.filter_preset is FilterPreset.SCALEFX
    assert window.consume_event(pygame.event.Event(pygame.KEYUP, key=pygame.K_F9, mod=0))

    assert window.consume_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F9, mod=0))
    assert window.filter_preset is FilterPreset.FAITHFUL
    assert window.consume_event(pygame.event.Event(pygame.KEYUP, key=pygame.K_F9, mod=0))

    # Shift+F9 cycles backwards FAITHFUL -> SCALEFX -> SMOOTH -> CRT
    assert window.consume_event(
        pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F9, mod=pygame.KMOD_LSHIFT)
    )
    assert window.filter_preset is FilterPreset.SCALEFX
    assert window.consume_event(pygame.event.Event(pygame.KEYUP, key=pygame.K_F9, mod=0))
    assert window.consume_event(
        pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F9, mod=pygame.KMOD_LSHIFT)
    )
    assert window.filter_preset is FilterPreset.SMOOTH
    assert window.consume_event(pygame.event.Event(pygame.KEYUP, key=pygame.K_F9, mod=0))
    assert window.consume_event(
        pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F9, mod=pygame.KMOD_LSHIFT)
    )
    assert window.filter_preset is FilterPreset.CRT
    assert window.consume_event(pygame.event.Event(pygame.KEYUP, key=pygame.K_F9, mod=0))

    # F10 live display preview can select and apply SCALEFX
    assert window.consume_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F10, mod=0))
    assert window.consume_event(pygame.event.Event(pygame.KEYUP, key=pygame.K_F10, mod=0))
    for _ in range(2):  # CRT -> SMOOTH -> SCALEFX
        assert window.consume_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RIGHT, mod=0))
        assert window.consume_event(pygame.event.Event(pygame.KEYUP, key=pygame.K_RIGHT, mod=0))
    assert window.apply_display_settings(base_settings).filter is FilterPreset.SCALEFX
    assert window.consume_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0))
    assert window.consume_event(pygame.event.Event(pygame.KEYUP, key=pygame.K_RETURN, mod=0))
    assert window.filter_preset is FilterPreset.SCALEFX


def test_f9_preset_hotkey_is_never_recorded_in_replays() -> None:
    backend = _DummyWindowBackend()
    window = WindowController((960, 768), FullscreenMode.OFF, backend=backend)
    router = InputRouter()
    audio_live = AudioEngine(headless=True)
    audio_replay = AudioEngine(headless=True)
    try:
        live = GameSession(seed=0xCAFEBABE, audio=audio_live)
        recorded: list[InputFrame] = []
        live_hashes: list[str] = []

        script: dict[int, list[pygame.event.Event]] = {
            1: [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F9, mod=0)],
            2: [pygame.event.Event(pygame.KEYUP, key=pygame.K_F9, mod=0)],
            3: [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0)],
            4: [pygame.event.Event(pygame.KEYUP, key=pygame.K_RETURN, mod=0)],
            6: [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F9, mod=0)],
            7: [pygame.event.Event(pygame.KEYUP, key=pygame.K_F9, mod=0)],
            8: [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_DOWN, mod=0)],
            9: [pygame.event.Event(pygame.KEYUP, key=pygame.K_DOWN, mod=0)],
        }

        for tick in range(12):
            for event in script.get(tick, []):
                if not window.consume_event(event):
                    router.process_pygame_event(event)
            frame = router.sample_frame()
            recorded.append(frame)
            live.step_tick(frame)
            live_hashes.append(live.state_hash())

        assert window.filter_preset is FilterPreset.CRT

        rec = ReplayRecording(seed=0xCAFEBABE, players=(KnightId.RED,), frames=tuple(recorded))
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
