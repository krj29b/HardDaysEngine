"""Dice house presenter and asset boundary (ARCHITECTURE.md §12.32)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harddaysengine.app.hd_host import FightHdHistory
from harddaysengine.app.map_scene import draw_map_text
from harddaysengine.assets.hd_pack_schema import HdAssetKind
from harddaysengine.render.fight import FightCanvas
from harddaysengine.render.renderer import PLAYFIELD_Y_OFFSET
from harddaysengine.render.scene import blit_indexed_sprite, palette_fx_to_rgb
from harddaysengine.sim.dice_house import DicePhase, DiceWorld, open_dice, step
from harddaysengine.sim.m68k import s16, u32
from harddaysengine.sim.service_screen import number3

if TYPE_CHECKING:
    import numpy as np
    import numpy.typing as npt

    from harddaysengine.app.input import PlayerInput
    from harddaysengine.app.scene_assets import GameAssetBundle
    from harddaysengine.assets.cel import DecodedCel
    from harddaysengine.assets.palette import RGBColor
    from harddaysengine.assets.picture import DecodedPicture
    from harddaysengine.render.scene import SceneRenderer
    from harddaysengine.sim.dice_house import DiceEngine, DiceHouse
    from harddaysengine.sim.fight_script import Draw


class DiceScreen:
    """Own decoded assets; the simulation keeps every mutable cell (§12.32)."""

    def __init__(
        self,
        bundle: GameAssetBundle,
        data: tuple[DiceHouse, DiceEngine],
        *,
        knight: tuple[int, int],
        pointer: tuple[npt.NDArray[np.uint8], int],
        text: tuple[bytes, int],
    ) -> None:
        house, engine = data
        tavern, table = bundle.get_picture(house.tavern), bundle.get_picture(house.table)
        cel, font = bundle.get_cel(house.cel_file), bundle.get_cel("Small.font")
        if tavern is None or table is None or cel is None or font is None:
            raise ValueError("the dice house requires its pictures, dice cels and Small.font")
        self.tavern: DecodedPicture = tavern
        self.table: DecodedPicture = table
        self.cel: DecodedCel = cel
        self.font: DecodedCel = font
        self.pointer, pointer_y = pointer
        self.char_map, self.span = text
        number, kind = knight
        self.run = open_dice(house, engine, number, pointer_y)
        self.round_palette = self._round_palette(house.ramp(kind), house.ramp_entry)
        self.template_palette = self._round_palette(house.ramp(0), house.ramp_entry)
        self._canvas: FightCanvas | None = None
        self.hd_history: FightHdHistory | None = None
        self._shown: list[Draw] | None = None

    def _round_palette(self, ramp: tuple[int, ...], entry: int) -> tuple[RGBColor, ...]:
        colors = list(self.tavern.palette)
        colors[entry : entry + len(ramp)] = palette_fx_to_rgb(ramp)
        return tuple(colors)

    def step(self, world: DiceWorld, inp: PlayerInput, *, fading: bool) -> bool:
        """Advance one PAL tick on the owning knight's port; ``True`` once the screen ends."""
        inp = inp.normalized()
        before = self.run.fight
        step(self.run, world, (inp.dx, inp.dy, inp.fire or inp.confirm), fading=fading)
        if self.run.fight is not before:
            if self.run.fight is None:
                self._canvas = None
                self.hd_history = None
            else:
                bg = self.tavern.pixels[:200, :320]
                cels = {self.run.house.cel_file: self.cel}
                self._canvas = FightCanvas(bg, cels)
                self.hd_history = FightHdHistory(
                    arena_name=self.run.house.tavern,
                    arena_kind=HdAssetKind.PICTURE,
                    arena_variant=None,
                    background=bg,
                    cels=cels,
                    base_palette=self.template_palette,
                    fade_reference=self.round_palette,
                )
        if self._canvas is not None and self.run.draws is not self._shown:
            self._canvas.accept(self.run.draws)
            if self.hd_history is not None:
                self.hd_history.record(self.run.draws)
            self._shown = self.run.draws
        return self.run.state.phase == DicePhase.DONE

    def palette(self) -> tuple[RGBColor, ...]:
        """The round palette with the class shades, or the table palette during a roll."""
        rolling = self.run.state.phase in (DicePhase.ROLL_WAIT, DicePhase.ROLL_FIRE)
        return self.table.palette if rolling else self.round_palette

    def _text(self, canvas: npt.NDArray[np.uint8], text: str, at: tuple[int, int, int]) -> None:
        x, y, style = at
        draw_map_text(canvas, self.font, self.char_map, text, x, y, style=style, span=self.span)

    def _roll(self, renderer: SceneRenderer) -> None:
        run = self.run
        renderer.compose_scene(background=self.table.pixels)
        for face, (x, y) in zip(run.state.dice, run.house.dice_at, strict=True):
            frame = self.cel.frames[face]
            blit_indexed_sprite(
                renderer.canvas, frame.pixels, x - frame.meta.hot_x, y + PLAYFIELD_Y_OFFSET
            )
        for line in run.state.lines:
            self._text(renderer.canvas, line.text, (line.x, line.y, line.style))

    def render(self, renderer: SceneRenderer, gold: int) -> None:
        """The round (tavern, thrower, gold, pointer) or the roll (table, dice, text) (§12.32)."""
        state = self.run.state
        renderer.set_palette(self.palette())
        if state.phase in (DicePhase.ROLL_WAIT, DicePhase.ROLL_FIRE):
            self._roll(renderer)
            return
        pixels = self.tavern.pixels if self._canvas is None else self._canvas.pixels()
        renderer.compose_scene(background=pixels)
        self._text(renderer.canvas, number3(u32(s16(gold))), self.run.house.gold_at)
        blit_indexed_sprite(
            renderer.canvas, self.pointer, state.pointer_x, state.pointer_y + PLAYFIELD_Y_OFFSET
        )
