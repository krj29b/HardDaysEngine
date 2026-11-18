"""Retained meeting message assets and caller state (§12.40)."""

from __future__ import annotations

from dataclasses import asdict, replace
from typing import TYPE_CHECKING

from harddaysengine.app.map_scene import draw_map_text
from harddaysengine.assets.hunk import parse_hunk_executable
from harddaysengine.assets.meeting_data import parse_meeting_data
from harddaysengine.render.scene import palette_fx_to_rgb
from harddaysengine.sim.meeting import MeetingState

if TYPE_CHECKING:
    from harddaysengine.app.scene_assets import GameAssetBundle
    from harddaysengine.assets.palette import RGBColor
    from harddaysengine.render.scene import SceneRenderer


class MeetingScreen:
    def __init__(self, bundle: GameAssetBundle, first: int, second: int, name: str) -> None:
        self.data = parse_meeting_data(
            parse_hunk_executable("mog", bundle.disk_set.read_file("mog"))
        )
        picture, font = bundle.get_picture(self.data.picture), bundle.get_cel(self.data.font)
        if picture is None or font is None:
            raise ValueError("meetings require the original message picture and font")
        self.picture, self.font, self.rules = picture, font, self.data.rules
        self.mapping, self.span = bundle.get_map_rules().char_map, bundle.get_turn_rules().span
        self.state = MeetingState(first, second, wait=self.rules.fade_wait)
        self.lines = tuple(
            replace(line, text=name) if index == self.data.name_row else line
            for index, line in enumerate(self.data.message)
        )

    def snapshot(self) -> dict[str, object]:
        return {"state": asdict(self.state), "lines": [asdict(line) for line in self.lines]}

    def palette(self) -> tuple[RGBColor, ...]:
        colors = list(self.picture.palette)
        colors[1 : 1 + len(self.data.recolor)] = palette_fx_to_rgb(self.data.recolor)
        return tuple(colors)

    def render(self, renderer: SceneRenderer) -> None:
        renderer.compose_scene(background=self.picture.pixels)
        renderer.set_palette(self.palette())
        for line in self.lines:
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
