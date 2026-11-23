"""Wizard assets and pure retained-frame presentation (ARCHITECTURE.md §12.36)."""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from typing import TYPE_CHECKING

import numpy as np

from harddaysengine.app.hd_host import FightHdHistory
from harddaysengine.app.map_scene import draw_map_text
from harddaysengine.assets.audio import decode_sample_bank
from harddaysengine.assets.fight_program import cel_shape, fight_constants, script_image
from harddaysengine.assets.hd_pack_schema import HdAssetKind
from harddaysengine.assets.hunk import parse_hunk_executable
from harddaysengine.assets.mog_code import MogCode
from harddaysengine.assets.wizard_data import parse_wizard_dialogue
from harddaysengine.assets.wizard_screen_data import parse_wizard_rules
from harddaysengine.render.fight import FightCanvas
from harddaysengine.render.renderer import CANVAS_HEIGHT, CANVAS_WIDTH, PLAYFIELD_Y_OFFSET
from harddaysengine.render.scene import palette_fx_to_rgb
from harddaysengine.sim.rewards import GiftKind
from harddaysengine.sim.service_screen import number3
from harddaysengine.sim.wizard import (
    WizardAnimation,
    WizardEngine,
    WizardPhase,
    WizardState,
    open_animation,
)

if TYPE_CHECKING:
    import numpy.typing as npt

    from harddaysengine.app.scene_assets import GameAssetBundle
    from harddaysengine.assets.palette import RGBColor
    from harddaysengine.render.scene import SceneRenderer
    from harddaysengine.sim.rng import Rng


class WizardScreen:
    """Keep original decoded assets and the visit, with no render-driven state (§12.36)."""

    def __init__(self, bundle: GameAssetBundle, knight: int, kind: int, loading_index: int) -> None:
        executable = parse_hunk_executable("mog", bundle.disk_set.read_file("mog"))
        self.rules, self.dialogue = (
            parse_wizard_rules(executable),
            parse_wizard_dialogue(executable),
        )
        tower, gift = bundle.get_picture(self.rules.tower), bundle.get_picture(self.rules.gift)
        cel, font = bundle.get_cel(self.rules.cel), bundle.get_cel(self.rules.font)
        message, loading_font = (
            bundle.get_picture(self.rules.loading.picture),
            bundle.get_cel(self.rules.loading.font),
        )
        if tower is None or gift is None or cel is None or font is None:
            raise ValueError("the wizard requires its original pictures, cels and font")
        if message is None or loading_font is None:
            raise ValueError("the wizard requires its original loading picture and font")
        self.tower, self.gift, self.cel, self.font = tower, gift, cel, font
        self.message, self.loading_font = message, loading_font
        self.bank = decode_sample_bank(self.rules.bank, bundle.disk_set.read_file(self.rules.bank))
        self.engine = WizardEngine(
            script_image(MogCode(executable)),
            {self.rules.cel: cel_shape(self.rules.cel, cel, None)},
            fight_constants(MogCode(executable)),
        )
        self.mapping, self.span = bundle.get_map_rules().char_map, bundle.get_turn_rules().span
        self.state = WizardState(knight, kind, loading_index, wait=self.rules.fade_wait)
        self.animation: WizardAnimation | None = None
        self.canvas: FightCanvas | None = None
        self.hd_history: FightHdHistory | None = None
        self.hd_stage: int | None = None
        colors = list(tower.palette)
        shades = next((row for key, row in self.rules.ramps if key == kind), ())
        start = self.rules.palette_entry
        colors[start : start + len(shades)] = palette_fx_to_rgb(shades)
        self.tower_palette = tuple(colors)

    def snapshot(self) -> dict[str, object]:
        return {
            "state": asdict(self.state),
            "animation": None if self.animation is None else self.animation.snapshot(),
            "canvas": None
            if self.canvas is None
            else hashlib.sha256(self.canvas.pixels().tobytes()).hexdigest(),
        }

    def palette(self) -> tuple[RGBColor, ...]:
        if self.state.phase in (WizardPhase.LOADING, WizardPhase.LOADING_OUT):
            return self.message.palette
        return self.gift.palette if self.state.phase == WizardPhase.GIFT else self.tower_palette

    def speech(self, stage: int) -> tuple[str, ...]:
        if stage == 0:
            return self.dialogue.greeting.lines
        result = self.state.result
        if result is None:
            raise ValueError("wizard gift speech requires its rolled result")
        if stage == 1:
            return self.dialogue.gift(result).lines
        value = ""
        if result.kind == GiftKind.ITEM:
            value = dict(self.rules.items)[result.item]
        elif result.kind == GiftKind.GOLD:
            value = number3(result.amount) + self.rules.gold_suffix
        return self.dialogue.farewell(result.kind).substitute(value)

    def _background(self, stage: int) -> npt.NDArray[np.uint8]:
        picture = self.gift if stage == 1 else self.tower
        pixels = np.zeros((CANVAS_HEIGHT, CANVAS_WIDTH), dtype=np.uint8)
        pixels[PLAYFIELD_Y_OFFSET : PLAYFIELD_Y_OFFSET + 200] = picture.pixels[:200]
        x, y, style = self.rules.speech_at[stage]
        for line in self.speech(stage):
            draw_map_text(pixels, self.font, self.mapping, line, x, y, style=style, span=self.span)
            y += self.dialogue.line_step
        return pixels[PLAYFIELD_Y_OFFSET : PLAYFIELD_Y_OFFSET + 200].copy()

    def start_stage(self, stage: int, rng: Rng, vbl: int) -> None:
        cels = {self.rules.cel: self.cel}
        self.canvas = FightCanvas(self._background(stage), cels)
        picture = self.gift if stage == 1 else self.tower
        pic_name = self.rules.gift if stage == 1 else self.rules.tower
        base_pal = self.gift.palette if stage == 1 else self.tower_palette
        self.hd_stage = stage
        self.hd_history = FightHdHistory(
            arena_name=pic_name,
            arena_kind=HdAssetKind.PICTURE,
            arena_variant=None,
            background=picture.pixels[:200, :320],
            cels=cels,
            base_palette=base_pal,
        )
        self.animation = open_animation(self.rules, self.engine, stage, rng, vbl)
        self._accept()

    def _accept(self) -> None:
        if self.animation is None or self.canvas is None:
            raise ValueError("wizard animation is not started")
        for draws in self.animation.batches:
            self.canvas.accept(draws)
            if self.hd_history is not None:
                self.hd_history.record(draws)

    def advance(self, vbl: int, fire: bool) -> bool:
        if self.animation is None:
            raise ValueError("wizard animation is not started")
        done = self.animation.advance(vbl, fire)
        self._accept()
        return done

    def render(self, renderer: SceneRenderer) -> None:
        """Draw retained text and animation without advancing jobs, waits or RNG (§12.36)."""
        if self.state.phase in (WizardPhase.LOADING, WizardPhase.LOADING_OUT):
            renderer.compose_scene(background=self.message.pixels)
            for line in self.rules.loading.messages[self.state.loading_index]:
                draw_map_text(
                    renderer.canvas,
                    self.loading_font,
                    self.mapping,
                    line.text,
                    line.x,
                    line.y,
                    style=line.style | 8,
                    span=self.span,
                )
        else:
            if self.canvas is None:
                raise ValueError("wizard animation canvas is not started")
            renderer.compose_scene(background=self.canvas.pixels())
        renderer.set_palette(self.palette())
