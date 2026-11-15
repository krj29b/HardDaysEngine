"""Amiga 12-bit OCS palette decoding and 32-colour combat palettes (ARCHITECTURE.md §3.1, §8)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import numpy.typing as npt

if TYPE_CHECKING:
    from collections.abc import Sequence

RGBColor = tuple[int, int, int]

# Default 32-colour combat fallback palette (ARCHITECTURE.md §8.1):
# index 0 black/transparent, 1..5 stone/steel greys, 6..8 knight crimson,
# 9..14 creature bone/hide tones, 15 blood red ($A00), 16..28 forest greens, 29..31 HUD gold/white.
DEFAULT_COMBAT_CELL_WORDS: tuple[int, ...] = (
    0x000,
    0x0222,
    0x0444,
    0x0666,
    0x0999,
    0x0CCC,
    0x0600,
    0x0A11,
    0x0E33,
    0x0321,
    0x0542,
    0x0764,
    0x0986,
    0x0BA8,
    0x0DCA,
    0x0A00,
    0x0120,
    0x0241,
    0x0362,
    0x0483,
    0x06A4,
    0x08C6,
    0x0210,
    0x0421,
    0x0631,
    0x0852,
    0x0A74,
    0x0C96,
    0x0EB8,
    0x0860,
    0x0CA2,
    0x0FFF,
)


def decode_palette_word(raw_word: int) -> int:
    """Decode an on-disk BE16 palette word into a 12-bit $0RGB cell word (ARCHITECTURE.md §3.1)."""
    word = raw_word & 0xFFFF
    if word & 0x8000:
        return word & 0x0FFF
    return (word << 1) & 0x0FFF


def cell_word_to_rgb(cell_word: int) -> RGBColor:
    """Expand a 12-bit Amiga $0RGB word (4-4-4) to 8-bit sRGB (8-8-8) via * 17."""
    r4 = (cell_word >> 8) & 0x0F
    g4 = (cell_word >> 4) & 0x0F
    b4 = cell_word & 0x0F
    return (r4 * 17, g4 * 17, b4 * 17)


def decode_palette(raw_words: Sequence[int]) -> tuple[RGBColor, ...]:
    """Decode a sequence of raw BE16 palette words into (R, G, B) 8-bit tuples."""
    return tuple(cell_word_to_rgb(decode_palette_word(w)) for w in raw_words)


def default_combat_palette() -> tuple[RGBColor, ...]:
    """Return the default 32-entry RGB888 combat palette."""
    return tuple(cell_word_to_rgb(w) for w in DEFAULT_COMBAT_CELL_WORDS)


def palette_to_rgba_array(
    palette: Sequence[RGBColor],
    *,
    size: int = 32,
    zero_transparent: bool = False,
) -> npt.NDArray[np.uint8]:
    """Convert an RGB tuple sequence into a (size, 4) uint8 RGBA lookup table."""
    lut = np.zeros((size, 4), dtype=np.uint8)
    lut[:, 3] = 255
    count = min(len(palette), size)
    for idx in range(count):
        r, g, b = palette[idx]
        lut[idx, 0] = r
        lut[idx, 1] = g
        lut[idx, 2] = b
    if zero_transparent and size > 0:
        lut[0, 3] = 0
    return lut
