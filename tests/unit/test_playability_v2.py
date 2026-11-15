"""Playability v2 integration tests for `app.game.GameSession`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

import numpy as np
import pytest

from pymoonstone.app.game import GameScene, GameSession
from pymoonstone.app.input import InputFrame, PlayerInput
from pymoonstone.app.location_screens import LocationKind
from pymoonstone.assets.cel import CelFrame, CelFrameMeta, DecodedCel
from pymoonstone.assets.palette import RGBColor, default_combat_palette
from pymoonstone.assets.picture import DecodedPicture
from pymoonstone.assets.tables import (
    BuiltinFontTable,
    CombatActionTables,
    CreatureNodeRecord,
    EncounterBandRecord,
    KnightScriptTables,
    LairRegionMetadata,
    MapSiteRecord,
    MogRuntimeTables,
    MonsterScriptTables,
    SynthSequenceTables,
)
from pymoonstone.render.renderer import CANVAS_HEIGHT, CANVAS_WIDTH, PLAYFIELD_HEIGHT
from pymoonstone.render.scene import default_bitmap_font
from pymoonstone.sim.monster_ai import MonsterArchetype
from pymoonstone.sim.overworld import (
    EncounterTrigger,
    MapSiteInput,
    MoonPhase,
    NodeKind,
    TriggerKind,
    build_overworld_graph_from_sites,
    legal_moves,
)
from pymoonstone.sim.palette_fx import rgb12_to_rgb24
from pymoonstone.sim.types import KnightId

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    import numpy.typing as npt

    from pymoonstone.app.scene_assets import GameAssetBundle
    from pymoonstone.render.scene import SceneRenderer, SceneSprite


@dataclass
class _StubSceneRenderer:
    """Headless SceneRenderer stub capturing palette, sprites, and canvas."""

    canvas: npt.NDArray[np.uint8] = field(
        default_factory=lambda: np.zeros((CANVAS_HEIGHT, CANVAS_WIDTH), dtype=np.uint8)
    )
    palette: tuple[RGBColor, ...] = ()
    sprites: list[SceneSprite] = field(default_factory=list)
    flushed: int = 0

    def set_palette(self, palette: Sequence[RGBColor]) -> None:
        self.palette = tuple(palette)

    def set_palette_fx(self, words: Sequence[int]) -> None:
        self.palette = tuple(rgb12_to_rgb24(w) for w in words)

    def compose_scene(
        self,
        *,
        background: npt.NDArray[np.uint8] | None = None,
        sprites: Sequence[SceneSprite] = (),
        border_index: int = 0,
    ) -> list[SceneSprite]:
        self.canvas.fill(np.uint8(border_index & 0x1F))
        if background is not None:
            bh = min(PLAYFIELD_HEIGHT, background.shape[0])
            bw = min(CANVAS_WIDTH, background.shape[1])
            self.canvas[28 : 28 + bh, :bw] = background[:bh, :bw]
        self.sprites = list(sprites)
        return self.sprites

    def flush_to_fbo(self) -> None:
        self.flushed += 1


def _p1_frame(
    *,
    dx: int = 0,
    dy: int = 0,
    fire: bool = False,
    confirm: bool = False,
    kid: KnightId = KnightId.RED,
) -> InputFrame:
    return InputFrame(players={kid: PlayerInput(dx=dx, dy=dy, fire=fire, confirm=confirm)})


def _make_cel(width: int = 24, height: int = 32, frames: int = 4) -> DecodedCel:
    cel_frames: list[CelFrame] = []
    for idx in range(frames):
        pixels = np.full((height, width), np.uint8(6 + (idx % 3)), dtype=np.uint8)
        mask = pixels != 0
        meta = CelFrameMeta(
            index=idx,
            data_offset=0,
            width=width,
            height=height,
            hot_and_flags=1,
            plane_bits=0x1F,
        )
        cel_frames.append(CelFrame(meta=meta, pixels=pixels, mask=mask))
    return DecodedCel(
        frame_count=frames,
        packed_size=0,
        unpacked_bits=0,
        frames=tuple(cel_frames),
        raw_planes=b"",
    )


def _make_picture(fill: int = 2) -> DecodedPicture:
    pixels = np.full((PLAYFIELD_HEIGHT, CANVAS_WIDTH), np.uint8(fill), dtype=np.uint8)
    pal = default_combat_palette()
    return DecodedPicture(
        width=CANVAS_WIDTH,
        height=PLAYFIELD_HEIGHT,
        planes=5,
        packed_size=0,
        raw_palette=(),
        palette=pal,
        pixels=pixels,
        raw_planes=b"",
    )


def _build_script_bytes() -> tuple[bytes, int, int]:
    """Build synthetic `mog` S_4 bytecode for knight and monster 2-piece draws."""
    buf = bytearray(64)
    # Offset 10: knight 2-part draw (slot 0 frame 1 + slot 3 frame 2 attack) + $FF $FF
    buf[10:16] = bytes((0x00, 0x01, 0xEC, 0x01, 0x00, 0x00))
    buf[16:22] = bytes((0x0C, 0x02, 0xF0, 0x03, 0x00, 0x08))
    buf[22:24] = bytes((0xFF, 0xFF))
    # Offset 30: monster 2-part draw (slot 0 frame 0 + slot 1 frame 1) + $FF $FF
    buf[30:36] = bytes((0x00, 0x00, 0xEC, 0x01, 0x00, 0x00))
    buf[36:42] = bytes((0x04, 0x01, 0xE8, 0x01, 0x00, 0x02))
    buf[42:44] = bytes((0xFF, 0xFF))
    return bytes(buf), 10, 30


class _StubCombatBundle:
    """Minimal bundle stub for testing multi-part combat script wiring."""

    def __init__(self) -> None:
        s4, k_off, m_off = _build_script_bytes()
        self._s4 = s4
        self._kn_cels = {f"KN{i}.ob".casefold(): _make_cel() for i in range(1, 5)}
        self._monster_cels = (_make_cel(22, 30, 4), _make_cel(22, 24, 4))
        self._mog_tables = MogRuntimeTables(
            knight_names=("Sir Red", "Sir Blue", "Sir Green", "Sir Yellow"),
            combat_actions=CombatActionTables(right_offsets=(0,) * 11, left_offsets=(0,) * 11),
            lair_region=LairRegionMetadata(
                map_sites=(MapSiteRecord(site_id=0, x=10, y=20),),
                creature_nodes=(
                    CreatureNodeRecord(
                        index=0,
                        creature_type=24,
                        defence=2,
                        map_x=50,
                        map_y=50,
                        aux_value=0,
                        arena_script="fo1",
                    ),
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
            knight_scripts=KnightScriptTables(
                idle_script=k_off,
                alt_script=k_off,
                action_scripts=(k_off,) * 9,
                hurt_scripts=(k_off,) * 9,
                walk_scripts=((k_off,),),
            ),
            monster_scripts=(
                MonsterScriptTables(
                    creature_type=24,
                    idle_script=m_off,
                    alt_script=m_off,
                    action_scripts=(m_off,) * 9,
                    hurt_scripts=(m_off,) * 9,
                    walk_scripts=((m_off,),),
                ),
            ),
        )

    def get_mog_tables(self) -> MogRuntimeTables:
        return self._mog_tables

    def get_mog_s4_bytes(self) -> bytes:
        return self._s4

    def get_knight_cel(self, knight_index: int) -> DecodedCel:
        return self._kn_cels[f"kn{(knight_index % 4) + 1}.ob"]

    def get_cel(self, *names: str) -> DecodedCel | None:
        for name in names:
            key = name.casefold()
            if key in self._kn_cels:
                return self._kn_cels[key]
        return None

    def get_monster_cels(self, archetype: MonsterArchetype) -> tuple[DecodedCel, ...]:
        del archetype
        return self._monster_cels

    def get_hit_sets(self) -> None:
        return None

    def get_arena(self, name: str) -> None:
        del name
        return None

    def get_combat_arena(self, lair_index: int) -> None:
        del lair_index
        return None

    def get_picture(self, *names: str) -> None:
        del names
        return None

    def get_test_picture(self, index: int) -> None:
        del index
        return None

    def build_overworld_graph(self) -> None:
        return None

    def get_blood_cel(self) -> None:
        return None


def _render_stub(session: GameSession, renderer: _StubSceneRenderer) -> None:
    session.render_to_scene(cast("SceneRenderer", renderer), default_bitmap_font())


def test_blue_knight_combat_palette_indices_6_to_8() -> None:
    session = GameSession(players=(KnightId.BLUE,), start_scene=GameScene.OVERWORLD)
    session._start_combat(
        EncounterTrigger(
            kind=TriggerKind.WANDERING_MONSTER,
            knight_id=KnightId.BLUE,
            node_id=0,
            day=1,
            moon_phase=MoonPhase.NEW_MOON,
            encounter_kind=0,
        ),
        KnightId.BLUE,
    )
    for _ in range(16):
        session.step_tick(InputFrame())

    renderer = _StubSceneRenderer()
    _render_stub(session, renderer)
    assert len(renderer.palette) == 32
    for idx in (6, 7, 8):
        r, g, b = renderer.palette[idx]
        assert b > r and b > g, f"expected blue tunic at index {idx}, got {(r, g, b)}"
    # Outside dragon scene, index 15 is blood red.
    r15, g15, b15 = renderer.palette[15]
    assert r15 > g15 and r15 > b15


def test_dragon_combat_palette_overrides_index_15() -> None:
    session = GameSession(players=(KnightId.BLUE,), start_scene=GameScene.OVERWORLD)
    session._start_combat(
        EncounterTrigger(
            kind=TriggerKind.DRAGON_INTERCEPT,
            knight_id=KnightId.BLUE,
            node_id=0,
            day=1,
            moon_phase=MoonPhase.NEW_MOON,
        ),
        KnightId.BLUE,
    )
    for _ in range(16):
        session.step_tick(InputFrame())

    renderer = _StubSceneRenderer()
    _render_stub(session, renderer)
    assert renderer.palette[15] == rgb12_to_rgb24(0x0FE8)


def test_scene_entry_fade_in_first_rendered_frame_not_all_black() -> None:
    # Transition KNIGHT_SELECT -> OVERWORLD via confirm tick.
    session = GameSession(players=(KnightId.RED,), start_scene=GameScene.KNIGHT_SELECT)
    session.step_tick(_p1_frame(confirm=True))
    assert session.scene == GameScene.OVERWORLD

    ow_renderer = _StubSceneRenderer()
    _render_stub(session, ow_renderer)
    assert any(rgb != (0, 0, 0) for rgb in ow_renderer.palette)

    # Transition OVERWORLD -> COMBAT and step 1 tick before rendering.
    session._start_combat(
        EncounterTrigger(
            kind=TriggerKind.WANDERING_MONSTER,
            knight_id=KnightId.RED,
            node_id=0,
            day=1,
            moon_phase=MoonPhase.NEW_MOON,
            encounter_kind=0,
        ),
        KnightId.RED,
    )
    session.step_tick(InputFrame())

    combat_renderer = _StubSceneRenderer()
    _render_stub(session, combat_renderer)
    assert any(rgb != (0, 0, 0) for rgb in combat_renderer.palette)


def test_overworld_move_decrements_moves_3_to_2_and_stays_in_overworld() -> None:
    sites = (
        *(
            MapSiteInput(
                site_id=i, x=40 + (i % 6) * 40, y=40 + (i // 6) * 30, kind=NodeKind.WILDERNESS
            )
            for i in range(21)
        ),
        MapSiteInput(site_id=21, x=60, y=50, kind=NodeKind.START),
        MapSiteInput(site_id=22, x=240, y=50, kind=NodeKind.START),
        MapSiteInput(site_id=23, x=60, y=150, kind=NodeKind.START),
        MapSiteInput(site_id=24, x=240, y=150, kind=NodeKind.START),
        MapSiteInput(site_id=25, x=120, y=60, kind=NodeKind.HIGHWOOD),
        MapSiteInput(site_id=26, x=200, y=60, kind=NodeKind.WATERDEEP),
        MapSiteInput(site_id=27, x=160, y=100, kind=NodeKind.STONEHENGE),
    )

    class _GraphBundle:
        def build_overworld_graph(self) -> object:
            return build_overworld_graph_from_sites(sites, moves_per_turn=3)

    session = GameSession(players=(KnightId.RED,), start_scene=GameScene.OVERWORLD)
    session.asset_bundle = cast("GameAssetBundle", _GraphBundle())
    session._reset_campaign(1)
    assert session.overworld.moves_remaining == 3
    start_node = session.overworld.knights[KnightId.RED].node_id
    session.overworld.dragon.node_id = 31
    moves = legal_moves(session.overworld, KnightId.RED)
    for idx, target in enumerate(moves):
        if target != session.overworld.dragon.node_id:
            session.selected_move_index = idx
            break

    session.step_tick(_p1_frame(confirm=True))
    assert session.overworld.knights[KnightId.RED].node_id != start_node
    assert session.overworld.moves_remaining == 2
    assert session.scene == GameScene.OVERWORLD


def test_combat_knight_attacks_approaching_trogg_and_defeats_it() -> None:
    session = GameSession(players=(KnightId.RED,), start_scene=GameScene.OVERWORLD)
    session._start_combat(
        EncounterTrigger(
            kind=TriggerKind.WANDERING_MONSTER,
            knight_id=KnightId.RED,
            node_id=0,
            day=1,
            moon_phase=MoonPhase.NEW_MOON,
            encounter_kind=0,
        ),
        KnightId.RED,
    )
    assert session.active_encounter is not None
    trogg = session.active_encounter.encounter.monsters[0]
    assert trogg.params.archetype == MonsterArchetype.TROGG_SPEAR
    initial_hp = trogg.hp

    # Walk toward Trogg then repeatedly swing melee attacks until defeated.
    for tick in range(280):
        if session.scene != GameScene.COMBAT:
            break
        kstate = session.knight_combat[KnightId.RED]
        dx = 1 if trogg.pos.x - kstate.x > 22 else 0
        fire = (trogg.pos.x - kstate.x <= 32) and (tick % 16 == 0)
        session.step_tick(_p1_frame(dx=dx, fire=fire))

    assert trogg.hp < initial_hp
    assert trogg.hp == 0
    assert session.scene == GameScene.OVERWORLD
    assert session.last_status == "ENCOUNTER WON"


def test_multipart_combat_scripts_wired_in_game_session() -> None:
    session = GameSession(players=(KnightId.BLUE,), start_scene=GameScene.OVERWORLD)
    session.asset_bundle = cast("GameAssetBundle", _StubCombatBundle())
    session._start_combat(
        EncounterTrigger(
            kind=TriggerKind.WANDERING_MONSTER,
            knight_id=KnightId.BLUE,
            node_id=0,
            day=1,
            moon_phase=MoonPhase.NEW_MOON,
            encounter_kind=0,
        ),
        KnightId.BLUE,
    )
    assert session.active_encounter is not None
    sprites = session._combat_sprites(session.active_encounter)
    # 2 pieces for knight + 2 pieces for Trogg = 4 top_left sprites.
    assert len(sprites) == 4
    assert all(sp.top_left for sp in sprites)


def test_waterdeep_sublocation_preserves_waterdeep_backdrop() -> None:
    class _TownBundle:
        def __init__(self) -> None:
            self.requested: list[tuple[str, ...]] = []
            self._wd = _make_picture(4)
            self._hw = _make_picture(7)

        def get_picture(self, *names: str) -> DecodedPicture | None:
            self.requested.append(names)
            for name in names:
                if name.casefold() == "waterdeep.piv":
                    return self._wd
                if name.casefold() == "highwood.piv":
                    return self._hw
            return None

    bundle = _TownBundle()
    session = GameSession(players=(KnightId.RED,), start_scene=GameScene.OVERWORLD)
    session.asset_bundle = cast("GameAssetBundle", bundle)
    session._open_location(KnightId.RED, LocationKind.WATERDEEP)
    # Confirm cursor 0 -> enters MERCHANT (LocationKind.SHOP) inside WATERDEEP.
    session.step_tick(_p1_frame(confirm=True))
    assert session.location_menu is not None
    assert session.location_menu.location == LocationKind.SHOP
    assert session.location_menu.parent_location == LocationKind.WATERDEEP

    renderer = _StubSceneRenderer()
    _render_stub(session, renderer)
    assert bundle.requested
    assert bundle.requested[-1][0] == "WaterDeep.PIV"


@pytest.mark.adf
def test_adf_blue_knight_combat_palette_and_fade_in(adf_dir: Path) -> None:
    from pymoonstone.assets.adf import DiskSet

    disks = DiskSet.from_directory(adf_dir)
    session = GameSession(players=(KnightId.BLUE,), start_scene=GameScene.OVERWORLD, disk_set=disks)
    session._start_combat(
        EncounterTrigger(
            kind=TriggerKind.WANDERING_MONSTER,
            knight_id=KnightId.BLUE,
            node_id=0,
            day=1,
            moon_phase=MoonPhase.NEW_MOON,
            encounter_kind=0,
        ),
        KnightId.BLUE,
    )
    session.step_tick(InputFrame())
    first_renderer = _StubSceneRenderer()
    _render_stub(session, first_renderer)
    assert any(rgb != (0, 0, 0) for rgb in first_renderer.palette)

    for _ in range(16):
        session.step_tick(InputFrame())
    full_renderer = _StubSceneRenderer()
    _render_stub(session, full_renderer)
    for idx in (6, 7, 8):
        r, g, b = full_renderer.palette[idx]
        assert b > r and b > g


@pytest.mark.adf
def test_adf_overworld_3_moves_and_stay_in_overworld(adf_dir: Path) -> None:
    from pymoonstone.assets.adf import DiskSet

    disks = DiskSet.from_directory(adf_dir)
    session = GameSession(players=(KnightId.BLUE,), start_scene=GameScene.OVERWORLD, disk_set=disks)
    assert len(session.overworld.graph.nodes) == 35
    assert session.overworld.moves_remaining == 3
    session.step_tick(_p1_frame(confirm=True, kid=KnightId.BLUE))
    assert session.overworld.moves_remaining == 2
    assert session.scene == GameScene.OVERWORLD


@pytest.mark.adf
def test_adf_knight_attacks_and_defeats_trogg(adf_dir: Path) -> None:
    from pymoonstone.assets.adf import DiskSet

    disks = DiskSet.from_directory(adf_dir)
    session = GameSession(players=(KnightId.BLUE,), start_scene=GameScene.OVERWORLD, disk_set=disks)
    session._start_combat(
        EncounterTrigger(
            kind=TriggerKind.WANDERING_MONSTER,
            knight_id=KnightId.BLUE,
            node_id=0,
            day=1,
            moon_phase=MoonPhase.NEW_MOON,
            encounter_kind=0,
        ),
        KnightId.BLUE,
    )
    assert session.active_encounter is not None
    trogg = session.active_encounter.encounter.monsters[0]
    for tick in range(300):
        if session.scene != GameScene.COMBAT or not trogg.is_alive:
            break
        kstate = session.knight_combat[KnightId.BLUE]
        dx = 1 if trogg.pos.x - kstate.x > 20 else 0
        fire = (trogg.pos.x - kstate.x <= 36) and (tick % 18 == 0)
        session.step_tick(_p1_frame(dx=dx, fire=fire, kid=KnightId.BLUE))
    assert trogg.hp == 0


def test_compose_knight_pieces_multipart_fallback_without_mog_s4() -> None:
    from pymoonstone.app.combat_bridge import compose_knight_pieces
    from pymoonstone.sim.knight_combat import KnightAction

    cel0 = _make_cel(12, 24, frames=2)
    cel1 = _make_cel(12, 24, frames=2)
    cel2 = _make_cel(12, 24, frames=2)
    cel3 = _make_cel(18, 16, frames=2)
    pieces = compose_knight_pieces(
        cel0,
        KnightAction.THRUST,
        tick_count=6,
        mog_s4=None,
        cel_slots=(cel0, cel1, cel2, cel3),
    )
    assert len(pieces) == 3
    assert all(piece.top_left for piece in pieces)
    assert [piece.cel_slot for piece in pieces] == [0, 0, 3]
    assert any(piece.attack and piece.cel_slot == 3 for piece in pieces)


def test_overworld_marker_palette_replaces_magenta_index_31() -> None:
    base = list(default_combat_palette())
    base[31] = (255, 17, 255)
    session = GameSession(players=(KnightId.BLUE,), start_scene=GameScene.OVERWORLD)
    resolved = session._overworld_palette(base)
    r, g, b = resolved[31]
    assert r == g == b
    assert 170 <= r <= 255


@pytest.mark.adf
def test_adf_overworld_marker_frames_and_highlight_palette(adf_dir: Path) -> None:
    from pymoonstone.assets.adf import DiskSet

    disks = DiskSet.from_directory(adf_dir)
    for kid, expected_slot in (
        (KnightId.BLUE, 0),
        (KnightId.YELLOW, 1),
        (KnightId.GREEN, 2),
        (KnightId.RED, 3),
    ):
        session = GameSession(players=(kid,), start_scene=GameScene.OVERWORLD, disk_set=disks)
        for _ in range(16):
            session.step_tick(InputFrame())
        renderer = _StubSceneRenderer()
        _render_stub(session, renderer)
        r, g, b = renderer.palette[31]
        assert r == g == b and 170 <= r <= 255
        mi_cel = session.asset_bundle.get_cel("mi.c") if session.asset_bundle else None
        assert mi_cel is not None
        active_frame = mi_cel.frames[expected_slot + 5]
        flag_frame = mi_cel.frames[expected_slot + 15]
        assert any(sp.frame == active_frame for sp in renderer.sprites)
        assert any(sp.frame == flag_frame for sp in renderer.sprites)
