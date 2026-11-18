"""Retained original Valley/loading messages (ARCHITECTURE.md §12.38)."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING

from harddaysengine.app.map_scene import draw_map_text
from harddaysengine.assets.hunk import parse_hunk_executable
from harddaysengine.assets.valley_data import parse_valley_data
from harddaysengine.render.scene import palette_fx_to_rgb
from harddaysengine.sim.valley import ValleyPhase, ValleyState

if TYPE_CHECKING:
    from harddaysengine.app.scene_assets import GameAssetBundle
    from harddaysengine.assets.palette import RGBColor
    from harddaysengine.render.scene import SceneRenderer


class ValleyScreen:
    def __init__(self, bundle: GameAssetBundle, knight: int, keys: int, loading_index: int) -> None:
        self.data = parse_valley_data(
            parse_hunk_executable("mog", bundle.disk_set.read_file("mog"))
        )
        self.rules = self.data.rules
        picture = bundle.get_picture(self.data.loading.picture)
        font = bundle.get_cel(self.data.loading.font)
        if picture is None or font is None:
            raise ValueError("the Valley requires its original message picture and font")
        self.picture, self.font = picture, font
        self.mapping, self.span = bundle.get_map_rules().char_map, bundle.get_turn_rules().span
        self.state = ValleyState(
            knight, keys == self.rules.keys, loading_index, wait=self.rules.fade_wait
        )

    def snapshot(self) -> dict[str, object]:
        return asdict(self.state)

    def palette(self) -> tuple[RGBColor, ...]:
        colors = list(self.picture.palette)
        if self.state.won:
            colors[1 : 1 + len(self.data.recolor)] = palette_fx_to_rgb(self.data.recolor)
        return tuple(colors)

    def render(self, renderer: SceneRenderer) -> None:
        state = self.state
        loading = state.phase in (ValleyPhase.LOADING, ValleyPhase.LOADING_OUT)
        lines = (
            self.data.loading.messages[state.loading_index]
            if loading
            else self.data.victory
            if state.won
            else self.data.missing
        )
        renderer.compose_scene(background=self.picture.pixels)
        renderer.set_palette(self.palette())
        for line in lines:
            draw_map_text(
                renderer.canvas,
                self.font,
                self.mapping,
                line.text,
                line.x,
                line.y,
                style=line.style | self.data.style,
                span=self.span,
            )
