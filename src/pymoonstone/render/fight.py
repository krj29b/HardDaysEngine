"""Ordered fight draw-list compositor (ARCHITECTURE.md §3.2, §10.17).

The engine already sorted the jobs and ran the second-script pass. Its output must not be
sorted again by sprite screen Y. Background stamps are applied when a frame is accepted,
not when the frame is displayed, so rendering zero or many times cannot change the fight.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pymoonstone.render.renderer import CANVAS_WIDTH, PLAYFIELD_HEIGHT
from pymoonstone.render.scene import blit_indexed_sprite
from pymoonstone.sim.m68k import s16

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    import numpy as np
    import numpy.typing as npt

    from pymoonstone.assets.cel import DecodedCel
    from pymoonstone.sim.fight_script import Draw

_CLIP = (0, 0, CANVAS_WIDTH, PLAYFIELD_HEIGHT)


class FightCanvas:
    """The stamped background and last displayed fight frame (§10.17).

    Decoded pixels stay in disk orientation. Mirroring the visible width is equivalent to
    reversing the aligned bitplanes and subtracting the mirrored header's alignment shift
    (§3.2). Stored-orientation draws retain the disk header's original horizontal shift.
    """

    def __init__(
        self,
        background: npt.NDArray[np.uint8],
        cels: Mapping[str, DecodedCel],
    ) -> None:
        if background.shape != (PLAYFIELD_HEIGHT, CANVAS_WIDTH):
            raise ValueError("fight background must be 320 by 200 pixels")
        self._background = background.copy()
        self._frame = background.copy()
        self._cels = dict(cels)

    def accept(self, draws: Sequence[Draw]) -> None:
        """Compose one engine output, preserving draw order and permanent stamps (§10.17)."""
        resolved = [(draw, self._pixels(draw)) for draw in draws]
        self._frame = self._background.copy()
        for draw, (pixels, x, y) in resolved:
            blit_indexed_sprite(self._frame, pixels, x, y, mirrored=draw.mirrored, clip_rect=_CLIP)
            if draw.stamp:
                blit_indexed_sprite(
                    self._background, pixels, x, y, mirrored=draw.mirrored, clip_rect=_CLIP
                )

    def pixels(self) -> npt.NDArray[np.uint8]:
        """Return a detached copy of the current frame; reading never advances anything."""
        return self._frame.copy()

    def _pixels(self, draw: Draw) -> tuple[npt.NDArray[np.uint8], int, int]:
        cel = self._cels[draw.cel]
        if not 0 <= draw.frame < len(cel.frames):
            raise ValueError(f"fight draw frame {draw.frame} outside {draw.cel}")
        frame = cel.frames[draw.frame]
        shift = 0 if draw.mirrored else frame.meta.hot_x
        return frame.pixels, s16(draw.x) - shift, s16(draw.y)
