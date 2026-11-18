"""Playability v2 integration tests for `app.game.GameSession`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

import numpy as np

from harddaysengine.app.game import GameScene, GameSession
from harddaysengine.app.input import InputFrame, PlayerInput
from harddaysengine.assets.cel import CelFrame, CelFrameMeta, DecodedCel
from harddaysengine.assets.palette import RGBColor, default_combat_palette
from harddaysengine.assets.picture import DecodedPicture
from harddaysengine.render.renderer import CANVAS_HEIGHT, CANVAS_WIDTH, PLAYFIELD_HEIGHT
from harddaysengine.render.scene import default_bitmap_font
from harddaysengine.sim.palette_fx import rgb12_to_rgb24
from harddaysengine.sim.types import KnightId
from tests.unit.map_setup import KINDS, RULES

if TYPE_CHECKING:
    from collections.abc import Sequence

    import numpy.typing as npt

    from harddaysengine.app.scene_assets import GameAssetBundle
    from harddaysengine.render.scene import SceneRenderer, SceneSprite


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
    """Explicit synthetic images for the original map renderer."""

    def get_test_picture(self, index: int) -> DecodedPicture:
        return _make_picture()

    def get_cel(self, *names: str) -> DecodedCel:
        return _make_cel(width=6, height=7, frames=48)


def _render_stub(session: GameSession, renderer: _StubSceneRenderer) -> None:
    session.render_to_scene(cast("SceneRenderer", renderer), default_bitmap_font())


def test_pixel_map_render_does_not_step_palette_or_simulation() -> None:
    session = GameSession(start_scene=GameScene.OVERWORLD)
    session.overworld.install(RULES, KINDS, 0)
    session.asset_bundle = cast("GameAssetBundle", _StubSceneBundle())
    session._start_map_colors()
    renderer = _StubSceneRenderer()
    before = session.state_hash()
    palette = session.overworld.colors.colors.copy()
    _render_stub(session, renderer)
    _render_stub(session, renderer)
    assert session.state_hash() == before
    assert session.overworld.colors.colors == palette
    assert renderer.flushed == 2
    assert np.all(renderer.canvas[:28] == 0)
    assert np.any(renderer.canvas[28:228] != 2)
