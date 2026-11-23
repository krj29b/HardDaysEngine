"""Present a persistent fight without interpreting scripts in the app (ADR-009, §10.17).

The caller owns PAL pacing and fight entry/outcomes. Each advance consumes exactly one
fight frame. Displaying its most recent canvas neither runs scripts nor changes palette state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harddaysengine.app.hd_host import FightHdHistory
from harddaysengine.assets.hd_pack_schema import HdAssetKind
from harddaysengine.render.fight import FightCanvas

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    import numpy as np
    import numpy.typing as npt

    from harddaysengine.assets.cel import DecodedCel
    from harddaysengine.assets.palette import RGBColor
    from harddaysengine.render.scene import SceneRenderer
    from harddaysengine.sim.fight import Fight, FrameOutput
    from harddaysengine.sim.fight_script import Draw


class FightScene:
    """Bridge one persistent :class:`Fight` to the scene renderer (§10.17, §10.18, §14.6)."""

    def __init__(
        self,
        fight: Fight,
        background: npt.NDArray[np.uint8],
        cels: Mapping[str, DecodedCel],
        palette: Sequence[RGBColor],
        *,
        arena_name: str = "arena",
        arena_kind: HdAssetKind = HdAssetKind.ARENA,
        arena_variant: str | None = None,
        cel_variant_fn: Callable[[Draw], str | None] | None = None,
    ) -> None:
        self.fight = fight
        self.palette = tuple(palette)
        self.canvas = FightCanvas(background, cels)
        self.hd_history = FightHdHistory(
            arena_name=arena_name,
            arena_kind=arena_kind,
            arena_variant=arena_variant,
            background=background,
            cels=cels,
            base_palette=self.palette,
            cel_variant_fn=cel_variant_fn,
        )

    def advance(self, vbl: int) -> FrameOutput:
        """Run one fight frame and consume its ordered draws exactly once (§10.17)."""
        output = self.fight.step(vbl)
        self.canvas.accept(output.tick.draws)
        self.hd_history.record(output.tick.draws)
        return output

    def render(self, renderer: SceneRenderer) -> None:
        """Display the retained frame, without touching engine or palette simulation state."""
        renderer.set_palette(self.palette)
        renderer.compose_scene(
            background=self.canvas.pixels(), background_y_offset=self.fight.shake.offset
        )
