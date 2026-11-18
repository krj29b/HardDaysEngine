"""Healer and mystic screen presenter and asset boundary (ARCHITECTURE.md §12.31)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harddaysengine.app.map_scene import draw_map_text
from harddaysengine.render.renderer import PLAYFIELD_Y_OFFSET
from harddaysengine.render.scene import blit_indexed_sprite
from harddaysengine.sim.service_screen import (
    Phase,
    ServiceState,
    ServiceWorld,
    number3,
    open_service,
    step,
)
from harddaysengine.sim.service_screen_types import CelDraw, LabelDraw, NumberDraw

if TYPE_CHECKING:
    import numpy as np
    import numpy.typing as npt

    from harddaysengine.app.input import PlayerInput
    from harddaysengine.app.scene_assets import GameAssetBundle
    from harddaysengine.assets.cel import DecodedCel
    from harddaysengine.assets.picture import DecodedPicture
    from harddaysengine.render.scene import SceneRenderer
    from harddaysengine.sim.service_screen_types import ServiceKind, ServiceScreens


class ServiceScreen:
    """Own decoded assets while the simulation keeps every mutable cell (§12.31)."""

    def __init__(
        self,
        bundle: GameAssetBundle,
        screens: ServiceScreens,
        kind: ServiceKind,
        *,
        knight: int,
        pointer: npt.NDArray[np.uint8],
        text: tuple[bytes, int],
    ) -> None:
        opening = screens.opening(kind)
        picture = bundle.get_picture(opening.picture)
        cel = bundle.get_cel(screens.donation.cel_file)
        font = bundle.get_cel("Small.font")
        if picture is None or cel is None or font is None:
            raise ValueError("service screens require their picture, cel file and Small.font")
        self.screens = screens
        self.picture: DecodedPicture = picture
        self.cel: DecodedCel = cel
        self.font: DecodedCel = font
        self.pointer = pointer
        self.char_map, self.span = text
        self.state = open_service(kind, knight, screens)

    def step(self, world: ServiceWorld, inp: PlayerInput, *, fading: bool) -> bool:
        """Advance one PAL tick on the owning knight's port; ``True`` once the screen ends."""
        inp = inp.normalized()
        step(
            self.state,
            self.screens,
            world,
            (inp.dx, inp.dy, inp.fire or inp.confirm),
            fading=fading,
        )
        return self.state.phase == Phase.DONE

    def _text(self, canvas: npt.NDArray[np.uint8], text: str, x: int, y: int, style: int) -> None:
        draw_map_text(canvas, self.font, self.char_map, text, x, y, style=style, span=self.span)

    def _cel(self, canvas: npt.NDArray[np.uint8], frame: int, x: int, y: int) -> None:
        if not 0 <= frame < len(self.cel.frames):
            raise ValueError(f"service cel frame {frame} is not loaded")
        loaded = self.cel.frames[frame]
        blit_indexed_sprite(canvas, loaded.pixels, x - loaded.meta.hot_x, y + PLAYFIELD_Y_OFFSET)

    def _donation(self, canvas: npt.NDArray[np.uint8], state: ServiceState) -> None:
        portrait = self.screens.opening(state.kind).portrait
        for draw in self.screens.donation.draws:
            if isinstance(draw, CelDraw):
                frame = portrait if draw.frame is None else draw.frame
                self._cel(canvas, frame, draw.x, draw.y)
            elif isinstance(draw, LabelDraw):
                self._text(canvas, draw.text, draw.x, draw.y, draw.style)
            elif isinstance(draw, NumberDraw):
                value = state.donation.purse if draw.purse else state.donation.amount
                self._text(canvas, number3(value), draw.x, draw.y, draw.style)

    def render(self, renderer: SceneRenderer) -> None:
        """Picture, then the phase's text or donation screen, then the pointer (§12.31)."""
        state = self.state
        renderer.compose_scene(background=self.picture.pixels)
        renderer.set_palette(self.picture.palette)
        canvas = renderer.canvas
        if state.phase == Phase.DONATION:
            self._donation(canvas, state)
        else:
            for line in state.lines:
                self._text(canvas, line.text, line.x, line.y, line.style)
        blit_indexed_sprite(
            canvas, self.pointer, state.pointer_x, state.pointer_y + PLAYFIELD_Y_OFFSET
        )
