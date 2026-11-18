"""Original shared sheet presenter and asset boundary (ARCHITECTURE.md §12.26)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harddaysengine.app.map_scene import draw_map_text
from harddaysengine.assets.hunk import parse_hunk_executable
from harddaysengine.assets.sheet_data import parse_sheet_data, parse_sheet_rules
from harddaysengine.render.renderer import PLAYFIELD_Y_OFFSET
from harddaysengine.render.scene import blit_indexed_sprite, palette_fx_to_rgb
from harddaysengine.sim.sheet_actions import next_knight
from harddaysengine.sim.sheet_types import SheetState
from harddaysengine.sim.sheets import close, redraw, step

if TYPE_CHECKING:
    from harddaysengine.app.input import PlayerInput
    from harddaysengine.app.scene_assets import GameAssetBundle
    from harddaysengine.render.scene import SceneRenderer
    from harddaysengine.sim.overworld import OverworldState


class SheetScreen:
    """Retain decoded assets and a tick-owned shared sheet state (§12.26)."""

    def __init__(
        self,
        bundle: GameAssetBundle,
        world: OverworldState,
        number: int,
        *,
        knight: int,
        lair: int = -1,
        other: int = 0,
    ) -> None:
        executable = parse_hunk_executable("mog", bundle.disk_set.read_file("mog"))
        self.data = parse_sheet_data(executable)
        cel, pointer, font = (bundle.get_cel(name) for name in ("ki.cel", "po.cel", "Small.font"))
        if cel is None or pointer is None or font is None:
            raise ValueError("sheets require ki.cel, po.cel and Small.font")
        self.cel, self.pointer, self.font = cel, pointer, font
        self.rules = parse_sheet_rules(
            executable, self.data, tuple((f.meta.width, f.meta.height) for f in cel.frames)
        )
        self.mapping = world.data().char_map
        self.state = SheetState(
            number, knight, other=other, lair=lair, read_only=number == 2 and world.travel
        )
        if number in (1, 8, 11) and not other:
            next_knight(self.state, world)
        redraw(self.state, self.rules, world)

    def step(self, world: OverworldState, inp: PlayerInput) -> bool:
        """Advance one PAL tick using the owning knight's port (§12.2)."""
        inp = inp.normalized()
        step(self.state, self.rules, world, inp.dx, inp.dy, inp.fire or inp.confirm)
        if self.state.done:
            close(self.state, world)
        return self.state.done

    def palette(self, world: OverworldState) -> tuple[int, ...]:
        colors = list(self.data.palette)
        actor = world.actors[self.state.knight - 1]
        colors[28:30] = self.data.class_pairs[actor.kind]
        if self.state.number in (1, 8, 11):
            other = world.actors[self.state.other - 1]
            colors[30:32] = self.data.class_pairs[other.kind]
        return tuple(colors)

    def render(self, renderer: SceneRenderer, world: OverworldState) -> None:
        """Draw stored output without recalculation or random draws (§12.26)."""
        state = self.state
        renderer.clear_canvas()
        renderer.set_palette(palette_fx_to_rgb(self.palette(world)))
        for icon in state.icons:
            frame = self.cel.frames[icon.frame]
            hot = 0 if icon.mirror else frame.meta.hot_x
            blit_indexed_sprite(
                renderer.canvas,
                frame.pixels,
                icon.x - hot,
                icon.y + PLAYFIELD_Y_OFFSET,
                mirrored=icon.mirror,
            )
        for text in state.texts:
            draw_map_text(renderer.canvas, self.font, self.mapping, text.text, text.x, text.y)
        draw_map_text(renderer.canvas, self.font, self.mapping, state.hover, 4, 2)
        blit_indexed_sprite(
            renderer.canvas,
            self.pointer.frames[0].pixels,
            state.pointer_x,
            state.pointer_y + PLAYFIELD_Y_OFFSET,
        )
