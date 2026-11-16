"""Present a persistent fight without interpreting scripts in the app (ADR-009, §10.17).

The caller owns PAL pacing and fight entry/outcomes. Each advance consumes exactly one
fight frame. Displaying its most recent canvas neither runs scripts nor changes palette state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pymoonstone.render.fight import FightCanvas

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    import numpy as np
    import numpy.typing as npt

    from pymoonstone.assets.cel import DecodedCel
    from pymoonstone.assets.palette import RGBColor
    from pymoonstone.render.scene import SceneRenderer
    from pymoonstone.sim.fight import Fight, FrameOutput


class FightScene:
    """Bridge one persistent :class:`Fight` to the scene renderer (§10.17, §10.18)."""

    def __init__(
        self,
        fight: Fight,
        background: npt.NDArray[np.uint8],
        cels: Mapping[str, DecodedCel],
        palette: Sequence[RGBColor],
    ) -> None:
        self.fight = fight
        self.palette = tuple(palette)
        self.canvas = FightCanvas(background, cels)

    def advance(self, vbl: int) -> FrameOutput:
        """Run one fight frame and consume its ordered draws exactly once (§10.17)."""
        output = self.fight.step(vbl)
        self.canvas.accept(output.tick.draws)
        return output

    def render(self, renderer: SceneRenderer) -> None:
        """Display the retained frame, without touching engine or palette simulation state."""
        renderer.set_palette(self.palette)
        renderer.compose_scene(background=self.canvas.pixels())
