"""Unit tests for `app.scene_assets` and `app.input` intro skip UX."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

import pygame

if TYPE_CHECKING:
    from harddaysengine.assets.adf import DiskSet

from harddaysengine.app.audio import AudioEngine
from harddaysengine.app.input import InputRouter, PlayerInput
from harddaysengine.app.scene_assets import GameAssetBundle
from harddaysengine.assets.adf import OFSFileEntry
from harddaysengine.sim.types import KnightId


@dataclass
class _StubDiskSet:
    files: dict[str, bytes] = field(default_factory=dict)

    def has_file(self, name: str) -> bool:
        return name.casefold() in self.files

    def read_file(self, name: str) -> bytes:
        return self.files[name.casefold()]

    def list_entries(self) -> list[OFSFileEntry]:
        return [
            OFSFileEntry(
                name=name,
                norm_name=name.casefold(),
                size=len(data),
                header_block=1,
                disk_id="Disk2",
            )
            for name, data in self.files.items()
        ]


def test_input_router_skip_intro_escape_enter_space_and_button_a() -> None:
    router = InputRouter(init_joysticks=False)

    # Escape triggers skip_intro without quitting the application
    router.process_pygame_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE))
    f_esc = router.sample_frame()
    assert f_esc.quit_requested is False
    assert f_esc.for_player(KnightId.RED).skip_intro is True

    # Next frame clears edge-triggered skip_intro
    f_idle = router.sample_frame()
    assert f_idle.for_player(KnightId.RED).skip_intro is False

    # Enter and Space trigger skip_intro
    router.process_pygame_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN))
    assert router.sample_frame().for_player(KnightId.RED).skip_intro is True
    router.process_pygame_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE))
    assert router.sample_frame().for_player(KnightId.RED).skip_intro is True

    # Gamepad button A (button 0) triggers skip_intro
    router.process_pygame_event(pygame.event.Event(pygame.JOYBUTTONDOWN, joy=0, button=0))
    assert router.sample_frame().for_player(KnightId.RED).skip_intro is True

    # Serialization round-trip preserves skip_intro
    pin = PlayerInput(skip_intro=True)
    assert PlayerInput.from_dict(pin.to_dict()) == pin


def test_attach_synth_without_mog_leaves_the_engine_silent() -> None:
    bundle = GameAssetBundle(disk_set=cast("DiskSet", _StubDiskSet()))
    audio = AudioEngine(headless=True)
    bundle.attach_synth(audio)
    assert audio.synth is None and audio.front is None
    assert not audio.step_vbl().any()
