"""Ordered map composition and original small-font menu (ARCHITECTURE.md §11.15/18)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harddaysengine.render.renderer import PLAYFIELD_Y_OFFSET
from harddaysengine.render.scene import blit_indexed_sprite
from harddaysengine.sim.m68k import s16
from harddaysengine.sim.overworld import list_rows

if TYPE_CHECKING:
    from collections.abc import Sequence

    import numpy as np
    import numpy.typing as npt

    from harddaysengine.app.scene_assets import GameAssetBundle
    from harddaysengine.assets.cel import DecodedCel
    from harddaysengine.sim.overworld import Arrival, MapSprite, OverworldState


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


def draw_map_text(
    canvas: npt.NDArray[np.uint8],
    font: DecodedCel,
    mapping: bytes,
    text: str,
    x: int,
    y: int,
    *,
    style: int = 0,
    span: int = 320,
) -> None:
    """Original glyph lookup, style spacing and alignment (§11.18/19)."""
    if style & 5:
        width = sum(
            font.frames[mapping[(ord(ch) - 32) & 255]].meta.width - (3 if style & 8 else 0)
            for ch in text
        )
        x = (span - width) >> 1 if style & 1 else span - width
    home_x, home_y = x, y
    for character in text:
        index = (ord(character) - 32) & 255
        if index >= len(mapping):
            raise ValueError("map menu character outside loaded lookup")
        frame = mapping[index]
        _draw(canvas, font, frame, x, y)
        glyph = font.frames[frame]
        x += glyph.meta.width - (3 if style & 8 else 0)
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
    draw_map_text(canvas, font, rules.char_map, headline, 65, 105)
    for index, row in enumerate(list_rows(world)):
        draw_map_text(
            canvas,
            font,
            rules.char_map,
            f"{index + 1} " + _row_text(world, row),
            55,
            115 + index * 6,
        )


def draw_calendar_screen(
    canvas: npt.NDArray[np.uint8],
    bundle: GameAssetBundle,
    world: OverworldState,
    *,
    farewell: bool = False,
) -> None:
    """Original ch.piv, bold.f and moon, or the farewell message (§11.19)."""
    rules = world.turn_rules
    if rules is None:
        raise ValueError("calendar rules are required")
    picture = bundle.get_picture("message.piv" if farewell else "ch.piv")
    font = bundle.get_cel("bold.f")
    if picture is None or font is None:
        raise ValueError("calendar picture and bold.f are required")
    canvas[:] = 0
    canvas[PLAYFIELD_Y_OFFSET : PLAYFIELD_Y_OFFSET + 200, :320] = picture.pixels[:200, :320]
    for row in rules.farewell if farewell else rules.next_day:
        draw_map_text(
            canvas,
            font,
            world.data().char_map,
            row.text,
            row.x,
            row.y,
            style=row.style | 8,
            span=rules.span,
        )
    if not farewell:
        icons = bundle.get_cel("ki.cel")
        if icons is None:
            raise ValueError("ki.cel is required for the moon")
        _draw(canvas, icons, world.moon_frame, rules.moon_x, rules.moon_y)
