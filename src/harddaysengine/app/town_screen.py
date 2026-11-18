"""Original town assets and read-only presenter (ARCHITECTURE.md §12.15/27)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from harddaysengine.app.map_scene import draw_map_text
from harddaysengine.assets.hunk import parse_hunk_executable
from harddaysengine.assets.town_data import parse_town_data
from harddaysengine.assets.town_loading_data import parse_town_welcome
from harddaysengine.render.renderer import PLAYFIELD_Y_OFFSET
from harddaysengine.render.scene import blit_indexed_sprite
from harddaysengine.sim.towns import TownAction, TownState, reset_pointer, step

if TYPE_CHECKING:
    from harddaysengine.app.input import PlayerInput
    from harddaysengine.app.scene_assets import GameAssetBundle
    from harddaysengine.render.scene import SceneRenderer


class TownScreen:
    """Own decoded town assets while simulation retains all mutable cells (§12.27)."""

    def __init__(self, bundle: GameAssetBundle, *, waterdeep: bool, knight: int) -> None:
        executable = parse_hunk_executable("mog", bundle.disk_set.read_file("mog"))
        self.data = parse_town_data(executable)[int(waterdeep)]
        self.welcome = parse_town_welcome(executable, waterdeep=waterdeep)
        self.message = bundle.get_picture(self.welcome.picture)
        self.font = bundle.get_cel(self.welcome.font)
        if self.message is None or self.font is None:
            raise ValueError("town welcome requires its original message picture and font")
        self.char_map = bundle.get_map_rules().char_map
        self.span = bundle.get_turn_rules().span
        picture, pointer = bundle.get_picture(self.data.picture), bundle.get_cel(self.data.pointer)
        if picture is None or pointer is None or not pointer.frames:
            raise ValueError("town requires its original picture and pointer")
        self.picture = picture
        pixels = pointer.frames[0].pixels
        if np.any(pixels > 3):
            raise ValueError("town pointer requires the original two-plane hardware sprite")
        self.pointer_pixels = np.where(pixels, pixels + 16, 0).astype(np.uint8)
        self.state = TownState(waterdeep, knight, self.data.pointer_x, self.data.pointer_y)

    def step(self, inp: PlayerInput, key: int, *, polling: bool = True) -> TownAction:
        """Poll the owning port once per PAL tick (§12.15)."""
        inp = inp.normalized()
        return step(
            self.state,
            self.data,
            inp.dx,
            inp.dy,
            (inp.fire or inp.confirm) and polling,
            key if polling else 0,
        )

    def render_welcome(self, renderer: SceneRenderer) -> None:
        """Draw the decoded fixed list in the message picture's own palette (§12.33)."""
        if self.message is None or self.font is None:
            raise ValueError("town welcome assets are not loaded")
        renderer.compose_scene(background=self.message.pixels)
        renderer.set_palette(self.message.palette)
        for line in self.welcome.lines:
            draw_map_text(
                renderer.canvas,
                self.font,
                self.char_map,
                line.text,
                line.x,
                line.y,
                style=line.style,
                span=self.span,
            )

    def resume(self) -> None:
        """Restore the original pointer start on return from a child screen."""
        reset_pointer(self.state, self.data)

    def render(self, renderer: SceneRenderer) -> None:
        """Render the unaltered painted buttons and loaded pointer (§12.15)."""
        renderer.compose_scene(background=self.picture.pixels)
        renderer.set_palette(self.picture.palette)
        blit_indexed_sprite(
            renderer.canvas,
            self.pointer_pixels,
            self.state.pointer_x,
            self.state.pointer_y + PLAYFIELD_Y_OFFSET,
        )
