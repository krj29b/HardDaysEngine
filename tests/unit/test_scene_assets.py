"""Unit tests for `app.scene_assets` and `app.input` intro skip UX."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

import numpy as np
import pygame

if TYPE_CHECKING:
    from pymoonstone.assets.adf import DiskSet

from pymoonstone.app.audio import AudioEngine
from pymoonstone.app.input import InputRouter, PlayerInput
from pymoonstone.app.scene_assets import COMBAT_SFX_KEYS, GameAssetBundle, select_knight_frame
from pymoonstone.assets.adf import OFSFileEntry
from pymoonstone.assets.cel import CelFrame, CelFrameMeta, DecodedCel
from pymoonstone.assets.tables import (
    BuiltinFontTable,
    CombatActionTables,
    CreatureNodeRecord,
    EncounterBandRecord,
    LairRegionMetadata,
    MapSiteRecord,
    MogRuntimeTables,
    SynthSequenceTables,
)
from pymoonstone.sim.knight_combat import KnightAction
from pymoonstone.sim.overworld import NodeKind
from pymoonstone.sim.types import KnightId


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


def test_build_overworld_graph_maps_sites_and_all_nodes() -> None:
    disks = _StubDiskSet()
    bundle = GameAssetBundle(disk_set=cast("DiskSet", disks))
    sites = (
        MapSiteRecord(site_id=21, x=40, y=40),
        MapSiteRecord(site_id=22, x=260, y=40),
        MapSiteRecord(site_id=23, x=40, y=160),
        MapSiteRecord(site_id=24, x=260, y=160),
        MapSiteRecord(site_id=25, x=90, y=60),
        MapSiteRecord(site_id=26, x=200, y=130),
        MapSiteRecord(site_id=27, x=150, y=100),
        MapSiteRecord(site_id=28, x=152, y=97),
        MapSiteRecord(site_id=30, x=217, y=11),
    )
    bundle._mog_tables_checked = True
    bundle._mog_tables = MogRuntimeTables(
        knight_names=("Red", "Blue", "Green", "Yellow"),
        combat_actions=CombatActionTables(right_offsets=(0,) * 11, left_offsets=(0,) * 11),
        lair_region=LairRegionMetadata(
            map_sites=sites,
            creature_nodes=(
                CreatureNodeRecord(0, 1, 1, 60, 50, 0, "fol1.t"),
                CreatureNodeRecord(6, 2, 1, 240, 50, 0, "gl1.t"),
            ),
            encounter_bands=(EncounterBandRecord(limit=50, kind=0),),
        ),
        builtin_font=BuiltinFontTable(glyphs=(b"\x00" * 8,) * 128),
        synth=SynthSequenceTables(
            blob_base=0,
            blob=b"",
            vibrato_rows=(),
            envelope_rows=(),
            sequences=(),
            waveforms=b"",
            instruments=(),
        ),
    )

    graph = bundle.build_overworld_graph()
    assert graph is not None
    assert graph.nodes[21].kind == NodeKind.START
    assert graph.nodes[22].kind == NodeKind.START
    assert graph.nodes[23].kind == NodeKind.START
    assert graph.nodes[24].kind == NodeKind.START
    assert graph.nodes[25].kind == NodeKind.HIGHWOOD
    assert graph.nodes[26].kind == NodeKind.WATERDEEP
    assert graph.nodes[27].kind == NodeKind.STONEHENGE
    assert 28 in graph.nodes
    assert 30 in graph.nodes


def test_compose_knight_sprites_and_select_knight_frame() -> None:
    frames = tuple(
        CelFrame(
            meta=CelFrameMeta(
                index=i,
                data_offset=0,
                width=7 if i % 2 == 1 else 54,
                height=33 if i % 2 == 1 else 23,
                hot_and_flags=0x20,
                plane_bits=0x1F,
            ),
            pixels=np.ones((33 if i % 2 == 1 else 23, 7 if i % 2 == 1 else 54), dtype=np.uint8),
            mask=np.ones((33 if i % 2 == 1 else 23, 7 if i % 2 == 1 else 54), dtype=bool),
        )
        for i in range(60)
    )
    kcel = DecodedCel(frame_count=60, packed_size=0, unpacked_bits=0, frames=frames, raw_planes=b"")
    fidx = select_knight_frame(kcel, KnightAction.OVERHEAD_SLASH, 6)
    assert 0 <= fidx < 26
    assert kcel.frames[fidx].meta.width == 54

    bundle = GameAssetBundle(disk_set=cast("DiskSet", _StubDiskSet()))
    bundle.cels["kn1.ob"] = kcel
    sprites = bundle.compose_knight_sprites(0, KnightAction.OVERHEAD_SLASH, 6, x=120, y=95, z=0)
    assert len(sprites) == 1
    assert sprites[0].frame.meta.width == 54
