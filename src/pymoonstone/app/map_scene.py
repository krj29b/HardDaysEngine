"""Ordered map composition and original small-font menu (ARCHITECTURE.md §11.15/18)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pymoonstone.render.renderer import PLAYFIELD_Y_OFFSET
from pymoonstone.render.scene import blit_indexed_sprite
from pymoonstone.sim.m68k import s16
from pymoonstone.sim.overworld import list_rows

if TYPE_CHECKING:
    from collections.abc import Sequence

    import numpy as np
    import numpy.typing as npt

    from pymoonstone.app.scene_assets import GameAssetBundle
    from pymoonstone.assets.cel import DecodedCel
    from pymoonstone.sim.overworld import Arrival, MapSprite, OverworldState


def _draw(canvas: npt.NDArray[np.uint8], cel: DecodedCel, frame: int, x: int, y: int) -> None:
    if not 0 <= frame < len(cel.frames):
        raise ValueError(f"map cel frame {frame} is not loaded")
    blit_indexed_sprite(canvas, cel.frames[frame].pixels, s16(x), s16(y) + PLAYFIELD_Y_OFFSET)


def compose_map(
    canvas: npt.NDArray[np.uint8],
    picture: npt.NDArray[np.uint8],
    cel: DecodedCel,
    sprites: Sequence[MapSprite],
) -> None:
    """Compose in original list order, without the arena's Y sorting (§11.15)."""
    canvas[:] = 0
    canvas[PLAYFIELD_Y_OFFSET : PLAYFIELD_Y_OFFSET + 200, :320] = picture[:200, :320]
    for sprite in sprites:
        _draw(canvas, cel, sprite.frame, sprite.x, sprite.y)


def _text(
    canvas: npt.NDArray[np.uint8], font: DecodedCel, mapping: bytes, text: str, x: int, y: int
) -> None:
    home_x, home_y = x, y
    for character in text:
        index = (ord(character) - 32) & 255
        if index >= len(mapping):
            raise ValueError("map menu character outside loaded lookup")
        frame = mapping[index]
        _draw(canvas, font, frame, x, y)
        glyph = font.frames[frame]
        x += glyph.meta.width
        if x >= 320:
            x, y = home_x, y + glyph.meta.height
            if y >= 200:
                y = home_y


def _row_text(world: OverworldState, row: Arrival) -> str:
    rules = world.data()
    if row.kind == 2:
        return rules.lair_text
    if row.kind == 1:
        return rules.battle_text + world.names[row.key - 1]
    index = row.kind - 21
    if not 0 <= index < len(rules.place_texts):
        raise ValueError("map menu place text outside its original table")
    return rules.place_texts[index]


def draw_map_menu(
    canvas: npt.NDArray[np.uint8], cel: DecodedCel, world: OverworldState, bundle: GameAssetBundle
) -> None:
    """Draw panel32, portrait and numbered rows in the original small font (§11.8)."""
    font = bundle.get_cel("Small.font")
    if font is None:
        raise ValueError("Small.font is required for the node menu")
    _draw(canvas, cel, 32, 50, 100)
    _draw(canvas, cel, world.current_actor.kind, 55, 105)
    rules = world.data()
    headline = world.names[int(world.current_knight_id)] + rules.may_text
    _text(canvas, font, rules.char_map, headline, 65, 105)
    for index, row in enumerate(list_rows(world)):
        _text(
            canvas,
            font,
            rules.char_map,
            f"{index + 1} " + _row_text(world, row),
            55,
            115 + index * 6,
        )
