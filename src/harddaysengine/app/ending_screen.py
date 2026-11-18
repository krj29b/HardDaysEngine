"""Pixels of the ``program`` ending and its screen wrapper (ARCHITECTURE.md §12.39.10).

:class:`EndingPixels` implements the simulation's :class:`EndingCanvas` with numpy buffers: ten
320x200 picture buffers and two screens that swap on flip. :class:`EndingScreen` owns the decoded
plan, the run and the pixels; rendering never advances the run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from harddaysengine.app.intro_player import assemble_stile_panorama
from harddaysengine.assets.arena import decode_stile
from harddaysengine.assets.ending_data import parse_ending_plan
from harddaysengine.assets.hunk import parse_hunk_executable
from harddaysengine.render.renderer import CANVAS_WIDTH, PLAYFIELD_HEIGHT
from harddaysengine.render.scene import blit_indexed_sprite, palette_fx_to_rgb
from harddaysengine.sim.ending import SCREEN, WORK, EndingInputs, EndingRun

if TYPE_CHECKING:
    import numpy.typing as npt

    from harddaysengine.app.scene_assets import GameAssetBundle
    from harddaysengine.assets.cel import DecodedCel
    from harddaysengine.assets.picture import DecodedPicture
    from harddaysengine.render.scene import SceneRenderer
    from harddaysengine.sim.ending_types import EndingPlan
    from harddaysengine.sim.turn_rules import TextLine

_BUFFERS = 10
_CLIP = (0, 0, CANVAS_WIDTH, PLAYFIELD_HEIGHT)
_PANORAMA_PICTURES = 3


def _blank() -> npt.NDArray[np.uint8]:
    return np.zeros((PLAYFIELD_HEIGHT, CANVAS_WIDTH), dtype=np.uint8)


def _screen_pixels(picture: DecodedPicture) -> npt.NDArray[np.uint8]:
    return picture.pixels[:PLAYFIELD_HEIGHT, :CANVAS_WIDTH].copy()


class EndingPixels:
    """Picture buffers and the two screens of the ending (§12.39.2)."""

    def __init__(self, bundle: GameAssetBundle, plan: EndingPlan) -> None:
        self.plan = plan
        self.buffers = [_blank() for _ in range(_BUFFERS)]
        self.screens = [_blank(), _blank()]
        self.displayed = 0
        self.message_picture = _picture(bundle, plan.message)
        for load in plan.load.pictures:
            self.buffers[load.buffer] = _screen_pixels(_picture(bundle, load.name))
        self.cels = {slot: _cel(bundle, name) for slot, name in plan.load.cels}
        self.overlay_cel = _cel(bundle, plan.load.overlay)
        self.font = _cel(bundle, plan.font.file)
        self.panorama = self._panorama(bundle)

    def _panorama(self, bundle: GameAssetBundle) -> npt.NDArray[np.uint8]:
        """The reverse wipe's block picture from the stile map over buffers P2..P4 (§12.39.8)."""
        load = self.plan.load
        words = decode_stile(bundle.disk_set.read_file(load.stile))
        sources = [p.buffer for p in load.pictures if p.buffer in (2, 3, 4)]
        if any(word // 80 >= _PANORAMA_PICTURES or 2 + word // 80 not in sources for word in words):
            raise ValueError("ending wipe map uses a buffer that holds no picture")
        return assemble_stile_panorama(words, (self.buffers[2], self.buffers[3], self.buffers[4]))

    def _buffer(self, index: int) -> npt.NDArray[np.uint8]:
        if index == SCREEN:
            return self.screens[self.displayed]
        if index == WORK:
            return self.screens[1 - self.displayed]
        return self.buffers[index]

    def displayed_pixels(self) -> npt.NDArray[np.uint8]:
        return self.screens[self.displayed]

    def flip(self) -> None:
        self.displayed = 1 - self.displayed

    def copy(self, source: int, target: int) -> None:
        self._buffer(target)[:] = self._buffer(source)

    def clear(self, target: int) -> None:
        self._buffer(target)[:] = 0

    def draw(self, target: int, slot: int, frame: int, x: int, y: int, mirrored: bool) -> None:
        pixels = self.cels[slot].frames[frame].pixels
        blit_indexed_sprite(self._buffer(target), pixels, x, y, mirrored=mirrored, clip_rect=_CLIP)

    def overlay(self, frame: int, x: int, y: int) -> None:
        pixels = self.overlay_cel.frames[frame].pixels
        blit_indexed_sprite(self._buffer(WORK), pixels, x, y, clip_rect=_CLIP)

    def message(self, target: int) -> None:
        self._buffer(target)[:] = _screen_pixels(self.message_picture)

    def wipe(self, target: int, progress: int) -> None:
        window = self.panorama[progress : progress + PLAYFIELD_HEIGHT]
        self._buffer(target)[: window.shape[0]] = window

    def text(self, target: int, lines: tuple[TextLine, ...]) -> None:
        """The text routine: glyphs by charmap, advance width - bias, style 1 centres (§12.39.7)."""
        font, data = self.plan.font, self.plan.scripts.data
        buffer = self._buffer(target)
        for line in lines:
            if line.style & ~1:
                raise ValueError(f"ending text style {line.style} is not used by the ending")
            glyphs = [self.font.frames[data[font.charmap + ord(ch) - 0x20]] for ch in line.text]
            width = sum(glyph.meta.width - font.bias for glyph in glyphs)
            x = ((font.width - width) & 0xFFFF) >> 1 if line.style & 1 else line.x
            for glyph in glyphs:
                blit_indexed_sprite(buffer, glyph.pixels, x, line.y, clip_rect=_CLIP)
                x += glyph.meta.width - font.bias


def _picture(bundle: GameAssetBundle, name: str) -> DecodedPicture:
    picture = bundle.get_picture(name)
    if picture is None:
        raise ValueError(f"the ending needs the original picture {name}")
    return picture


def _cel(bundle: GameAssetBundle, name: str) -> DecodedCel:
    cel = bundle.get_cel(name)
    if cel is None:
        raise ValueError(f"the ending needs the original cel {name}")
    return cel


def load_plan(bundle: GameAssetBundle) -> EndingPlan:
    return parse_ending_plan(parse_hunk_executable("program", bundle.disk_set.read_file("program")))


class EndingScreen:
    """The whole ending for one hand-over; ``advance`` is one VBL (§12.39.10)."""

    def __init__(self, bundle: GameAssetBundle, flags: int) -> None:
        self.plan = load_plan(bundle)
        if not flags & self.plan.flag_bit:
            raise ValueError("the ending runs only with the ending flag set")
        self.pixels = EndingPixels(bundle, self.plan)
        palettes = {
            load.name: _picture(bundle, load.name).raw_palette for load in self.plan.load.pictures
        }
        widths = {
            slot: tuple(frame.meta.width for frame in cel.frames)
            for slot, cel in self.pixels.cels.items()
        }
        inputs = EndingInputs(palettes, self.pixels.message_picture.raw_palette, widths)
        self.run = EndingRun(self.plan, flags, inputs, self.pixels)

    def advance(self) -> bool:
        return self.run.advance()

    def snapshot(self) -> dict[str, object]:
        return self.run.snapshot()

    def render(self, renderer: SceneRenderer) -> None:
        renderer.compose_scene(background=self.pixels.displayed_pixels())
        renderer.set_palette(palette_fx_to_rgb(self.run.palette.regs))
