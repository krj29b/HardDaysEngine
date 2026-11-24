"""Stonehenge message and persistent ritual presenter (ARCHITECTURE.md §12.34)."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING

from harddaysengine.app.hd_host import FightHdHistory
from harddaysengine.app.map_scene import draw_map_text
from harddaysengine.assets.audio import decode_sample_bank
from harddaysengine.assets.fight_program import cel_shape
from harddaysengine.assets.hd_pack_schema import HdAssetKind
from harddaysengine.assets.hunk import parse_hunk_executable
from harddaysengine.assets.mog_code import MogCode
from harddaysengine.assets.stonehenge_data import parse_stone_rules, ritual_engine
from harddaysengine.render.fight import FightCanvas
from harddaysengine.render.scene import palette_fx_to_rgb
from harddaysengine.sim.stonehenge import (
    RitualRun,
    StonePhase,
    StoneState,
    ending_code,
    matches,
    open_ritual,
)

if TYPE_CHECKING:
    from harddaysengine.app.scene_assets import GameAssetBundle
    from harddaysengine.assets.palette import RGBColor
    from harddaysengine.render.scene import SceneRenderer
    from harddaysengine.sim.actor import Actor
    from harddaysengine.sim.inventory import Inventory
    from harddaysengine.sim.rng import Rng


class StoneScreen:
    """Own original assets; all visit and script state is serialized independently (§12.34)."""

    def __init__(
        self,
        bundle: GameAssetBundle,
        knight: int,
        actor: Actor,
        inventory: Inventory,
        moon: int,
    ) -> None:
        executable = parse_hunk_executable("mog", bundle.disk_set.read_file("mog"))
        self.rules = parse_stone_rules(executable)
        self.message = bundle.get_picture(self.rules.message)
        self.font = bundle.get_cel(self.rules.font)
        self.picture = bundle.get_picture(self.rules.ritual.picture)
        self.cel = bundle.get_cel(self.rules.ritual.cel)
        if self.message is None or self.font is None or self.picture is None or self.cel is None:
            raise ValueError("Stonehenge requires its original pictures, font and ritual cels")
        self.bank = decode_sample_bank(
            self.rules.ritual.bank, bundle.disk_set.read_file(self.rules.ritual.bank)
        )
        self.engine = ritual_engine(
            MogCode(executable),
            {
                self.rules.ritual.cel: cel_shape(self.rules.ritual.cel, self.cel, None),
            },
        )
        self.char_map, self.span = bundle.get_map_rules().char_map, bundle.get_turn_rules().span
        self.state = StoneState(
            knight,
            matches(self.rules, moon, inventory.byte(22)),
            ending_code(self.rules, moon, actor.kind),
            wait=self.rules.fade_wait,
        )
        self.kind = actor.kind
        self.run: RitualRun | None = None
        self.canvas: FightCanvas | None = None
        self.hd_history: FightHdHistory | None = None

    def snapshot(self) -> dict[str, object]:
        return {
            "state": asdict(self.state),
            "kind": self.kind,
            "run": None if self.run is None else self.run.snapshot(),
        }

    def message_palette(self) -> tuple[RGBColor, ...]:
        if self.message is None:
            raise ValueError("Stonehenge message picture missing")
        colors = list(self.message.palette)
        if self.state.phase not in (StonePhase.QUEST, StonePhase.HAND_OVER):
            colors[1 : 1 + len(self.rules.recolor)] = palette_fx_to_rgb(self.rules.recolor)
        return tuple(colors)

    def _ritual_palette_for_kind(self, kind: int) -> tuple[RGBColor, ...]:
        if self.picture is None:
            raise ValueError("Stonehenge ritual picture missing")
        colors = list(self.picture.palette)
        ramp = next((words for key, words in self.rules.ritual.ramps if key == kind), ())
        first = self.rules.ritual.palette_entry
        colors[first : first + len(ramp)] = palette_fx_to_rgb(ramp)
        return tuple(colors)

    def ritual_palette(self) -> tuple[RGBColor, ...]:
        return self._ritual_palette_for_kind(self.kind)

    def start_ritual(self, rng: Rng) -> None:
        if self.picture is None or self.cel is None:
            raise ValueError("Stonehenge ritual assets missing")
        self.run = open_ritual(self.rules.ritual, self.engine, rng)
        cels = {self.rules.ritual.cel: self.cel}
        self.canvas = FightCanvas(self.picture.pixels, cels)
        self.hd_history = FightHdHistory(
            arena_name=self.rules.ritual.picture,
            arena_kind=HdAssetKind.PICTURE,
            arena_variant=None,
            background=self.picture.pixels,
            cels=cels,
            base_palette=self._ritual_palette_for_kind(0),
            fade_reference=self.ritual_palette(),
        )

    def advance_ritual(self, vbl: int) -> bool:
        if self.run is None or self.canvas is None:
            raise ValueError("Stonehenge ritual not started")
        self.run.advance(vbl)
        for draws in self.run.batches:
            self.canvas.accept(draws)
            if self.hd_history is not None:
                self.hd_history.record(draws)
        return self.run.done

    def render(self, renderer: SceneRenderer) -> None:
        """Render retained messages or ordered ritual draws without advancing state."""
        if self.state.phase in (StonePhase.RITUAL, StonePhase.RITUAL_OUT):
            if self.canvas is None:
                raise ValueError("Stonehenge ritual canvas missing")
            renderer.compose_scene(background=self.canvas.pixels())
            renderer.set_palette(self.ritual_palette())
            return
        if self.message is None or self.font is None:
            raise ValueError("Stonehenge message assets missing")
        renderer.compose_scene(background=self.message.pixels)
        renderer.set_palette(self.message_palette())
        lines = {
            StonePhase.OFFER: self.rules.offer,
            StonePhase.OFFER_OUT: self.rules.offer,
            StonePhase.RITUAL_MESSAGE: self.rules.ritual_text,
            StonePhase.RITUAL_MESSAGE_OUT: self.rules.ritual_text,
        }.get(self.state.phase, self.rules.quest)
        for line in lines:
            draw_map_text(
                renderer.canvas,
                self.font,
                self.char_map,
                line.text,
                line.x,
                line.y,
                style=line.style,
                span=self.span,
            )
