"""Unit tests for `app.scene_assets` and `app.input` intro skip UX."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

import pygame

if TYPE_CHECKING:
    from harddaysengine.assets.adf import DiskSet

from harddaysengine.app.audio import AudioEngine
from harddaysengine.app.input import InputRouter, PlayerInput
from harddaysengine.app.scene_assets import COMBAT_SFX_KEYS, GameAssetBundle
from harddaysengine.assets.adf import OFSFileEntry
from harddaysengine.sim.types import KnightId


def _build_8svx_chunk(pcm: bytes, rate: int = 8287) -> bytes:
    vhdr = struct.pack(">IIIHHBB", 0, 0, 0, rate, 1, 0, 0) + struct.pack(">I", 0x10000)
    body = pcm if len(pcm) % 2 == 0 else pcm + b"\x00"
    inner = (
        b"8SVX"
        + b"VHDR"
        + struct.pack(">I", len(vhdr))
        + vhdr
        + b"BODY"
        + struct.pack(">I", len(body))
        + body
    )
    return b"FORM" + struct.pack(">I", len(inner)) + inner


def _wrap_hunk_code(payload: bytes) -> bytes:
    padded = payload.ljust((len(payload) + 3) & ~3, b"\x00")
    return (
        struct.pack(">5I", 0x3F3, 0, 1, 0, 0)
        + struct.pack(">I", len(padded) // 4)
        + struct.pack(">2I", 0x3E9, len(padded) // 4)
        + padded
        + struct.pack(">I", 0x3F2)
    )


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


def test_preload_audio_exposes_combat_sfx_keys_and_8svx_chunks() -> None:
    kn_payload = _build_8svx_chunk(b"\x10\x20\x30\x40", rate=11025) + _build_8svx_chunk(
        b"\x50\x60\x70\x7f", rate=8287
    )
    disks = _StubDiskSet(files={"kn.a": _wrap_hunk_code(kn_payload)})
    bundle = GameAssetBundle(disk_set=cast("DiskSet", disks))
    audio = AudioEngine(headless=True)

    bundle.preload_audio(audio)

    for key in (*COMBAT_SFX_KEYS, "kn.a", "kn:0", "kn:1", "hit.a", "splat.a", "hurt.a", "throw.a"):
        ch = audio.play_sample(key)
        assert 0 <= ch < 4
