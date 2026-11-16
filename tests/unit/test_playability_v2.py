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
from pymoonstone.render.renderer import CANVAS_HEIGHT, CANVAS_WIDTH, PLAYFIELD_HEIGHT
from pymoonstone.render.scene import default_bitmap_font
from pymoonstone.sim.overworld import (
    MapSiteInput,
    NodeKind,
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


class _StubSceneBundle:
    """Missing pictures/cels for retained headless map tests; no combat scripts."""

    def get_picture(self, *names: str) -> DecodedPicture | None:
        return None

    def get_test_picture(self, index: int) -> DecodedPicture | None:
        return None

    def get_cel(self, *names: str) -> DecodedCel | None:
        return None

    def build_overworld_graph(self) -> object:
        return None


def _render_stub(session: GameSession, renderer: _StubSceneRenderer) -> None:
    session.render_to_scene(cast("SceneRenderer", renderer), default_bitmap_font())


def test_overworld_entry_fade_first_frame_is_visible() -> None:
    # Transition KNIGHT_SELECT -> OVERWORLD via confirm tick.
    session = GameSession(players=(KnightId.RED,), start_scene=GameScene.KNIGHT_SELECT)
    session.step_tick(_p1_frame(confirm=True))
    assert session.scene == GameScene.OVERWORLD

    ow_renderer = _StubSceneRenderer()
    _render_stub(session, ow_renderer)
    assert any(rgb != (0, 0, 0) for rgb in ow_renderer.palette)


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
def test_adf_overworld_3_moves_and_stay_in_overworld(adf_dir: Path) -> None:
    from pymoonstone.assets.adf import DiskSet

    disks = DiskSet.from_directory(adf_dir)
    session = GameSession(players=(KnightId.BLUE,), start_scene=GameScene.OVERWORLD, disk_set=disks)
    assert len(session.overworld.graph.nodes) == 35
    assert session.overworld.moves_remaining == 3
    session.step_tick(_p1_frame(confirm=True, kid=KnightId.BLUE))
    assert session.overworld.moves_remaining == 2
    assert session.scene == GameScene.OVERWORLD


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


def test_overworld_renders_monster_dens_and_moves_dragon() -> None:
    mi_cel = _make_cel(9, 5, frames=42)
    sites = (
        MapSiteInput(site_id=0, x=160, y=100, kind=NodeKind.STONEHENGE, neighbors=(1, 2, 25)),
        MapSiteInput(site_id=1, x=220, y=100, kind=NodeKind.ROAD, neighbors=(0, 2, 25)),
        MapSiteInput(
            site_id=2,
            x=48,
            y=120,
            kind=NodeKind.LAIR,
            region_id=0,
            lair_index=0,
            creature_type=24,
            defence=1,
            arena_script="fo1.t",
            neighbors=(0, 1, 25),
        ),
        MapSiteInput(site_id=25, x=26, y=156, kind=NodeKind.START, neighbors=(0, 1, 2)),
    )
    ow_graph = build_overworld_graph_from_sites(sites, moves_per_turn=3)

    class _OverworldDenBundle(_StubSceneBundle):
        def get_cel(self, *names: str) -> DecodedCel | None:
            for name in names:
                if name.casefold() == "mi.c":
                    return mi_cel
            return super().get_cel(*names)

        def build_overworld_graph(self) -> object:
            return ow_graph

    session = GameSession(players=(KnightId.RED,), start_scene=GameScene.OVERWORLD)
    session.asset_bundle = cast("GameAssetBundle", _OverworldDenBundle())
    session._reset_campaign(1)

    moves = legal_moves(session.overworld, KnightId.RED)
    den_idx = moves.index(2)
    non_den_idx = next(i for i, nid in enumerate(moves) if nid != 2)

    session.selected_move_index = non_den_idx
    unselected_frames = {sp.frame.meta.index for sp in session._overworld_sprites()}
    assert 20 in unselected_frames

    session.selected_move_index = den_idx
    highlighted_frames = {sp.frame.meta.index for sp in session._overworld_sprites()}
    assert 31 in highlighted_frames
    assert any(
        sp.frame.meta.index in range(34, 42) and sp.top_left for sp in session._overworld_sprites()
    )

    dragon_start = (session.overworld.dragon.x, session.overworld.dragon.y)
    for _ in range(10):
        session.step_tick(InputFrame())
    assert (session.overworld.dragon.x, session.overworld.dragon.y) != dragon_start


@pytest.mark.adf
def test_adf_overworld_monster_dens_and_flying_dragon(adf_dir: Path) -> None:
    from pymoonstone.assets.adf import DiskSet

    disks = DiskSet.from_directory(adf_dir)
    session = GameSession(players=(KnightId.BLUE,), start_scene=GameScene.OVERWORLD, disk_set=disks)
    den_nodes = [n for n in session.overworld.graph.nodes.values() if n.kind == NodeKind.LAIR]
    assert len(den_nodes) == 20

    sprites = session._overworld_sprites()
    den_sprites = [sp for sp in sprites if sp.frame.meta.index in (20, 31)]
    assert len(den_sprites) == 20
    assert all(sp.top_left for sp in den_sprites)
    assert any(sp.frame.meta.index in range(34, 42) and sp.top_left for sp in sprites)

    start_pos = (session.overworld.dragon.x, session.overworld.dragon.y)
    for _ in range(12):
        session.step_tick(InputFrame())
    assert (session.overworld.dragon.x, session.overworld.dragon.y) != start_pos
