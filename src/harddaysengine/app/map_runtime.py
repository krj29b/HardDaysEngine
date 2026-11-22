"""Map jobs on the existing script engine (ARCHITECTURE.md §11.19)."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING

import numpy as np

from harddaysengine.app.fight_scene import FightScene
from harddaysengine.assets.fight_program import cel_shape, fight_constants, script_image
from harddaysengine.assets.hunk import parse_hunk_executable
from harddaysengine.assets.mog_code import MogCode
from harddaysengine.sim.fight import Fight
from harddaysengine.sim.map_dragon import bind_flight, spawn

if TYPE_CHECKING:
    from harddaysengine.app.scene_assets import GameAssetBundle
    from harddaysengine.sim.fight_script import Draw
    from harddaysengine.sim.overworld import OverworldState
    from harddaysengine.sim.turn_rules import TurnRules


class MapRuntime:
    """Retain real dragon jobs and their ordered canvas between map frames (§11.19)."""

    def __init__(self, bundle: GameAssetBundle, world: OverworldState, rules: TurnRules) -> None:
        cel = bundle.get_cel("mi.c")
        picture = bundle.get_test_picture(8)
        if cel is None or picture is None:
            raise ValueError("map jobs require mi.c and the original map")
        mog = MogCode(parse_hunk_executable("mog", bundle.disk_set.read_file("mog")))
        fight = Fight(
            script_image(mog),
            {"mi.c": cel_shape("mi.c", cel, None)},
            {3: ("mi.c",) * 5},
            world.actors,
            world.dragon,
            fight_constants(mog),
            rng=world.prng,
        )
        self.scene = FightScene(
            fight, np.zeros((200, 320), dtype=np.uint8), {"mi.c": cel}, picture.palette
        )
        bind_flight(world, fight, rules.dragon)
        self.rules = rules
        self.stamps: list[Draw] = []
        self.draws: list[Draw] = []

    def enter(self, world: OverworldState, vbl: int) -> None:
        """Existing jobs run before the spawn attempt (§11.4)."""
        self.advance(world, vbl)
        if spawn(world, self.scene.fight, self.rules.dragon):
            self._accept_draws([])

    def advance(self, world: OverworldState, vbl: int) -> None:
        """One map job pass, with no separate animation counter (§11.19)."""
        if world.dragon_active:
            self._accept_draws(self.scene.fight.step(vbl).tick.draws)

    def _accept_draws(self, draws: list[Draw]) -> None:
        self.stamps.extend(draw for draw in self.draws if draw.stamp)
        self.draws = draws
        self.scene.canvas.accept(draws)

    def restore_draws(self, stamps: list[Draw], draws: list[Draw]) -> None:
        """Recompose the retained map frame from original cels (§13.11)."""
        self.stamps, self.draws = stamps, draws
        self.scene.canvas.accept(stamps)
        self.scene.canvas.accept(draws)

    def clear(self, world: OverworldState) -> None:
        """Arena/place reset frees map jobs and deactivates flight (§11.10)."""
        world.dragon_active = False
        for job in self.scene.fight.engine.jobs:
            job.active = job.running = False

    def snapshot(self) -> dict[str, object]:
        """Mutable job/interpreter cells, without re-decoding or stepping (§11.19)."""
        engine = self.scene.fight.engine
        return {
            "jobs": [asdict(job) for job in engine.jobs],
            "box": asdict(engine.box),
            "scratch": asdict(engine.scratch_work),
            "mirrored": sorted(engine.mirrored),
            "vbl": engine.vbl,
            "attack": [None if row is None else asdict(row) for row in engine.attack.entries],
            "hurt": [None if row is None else asdict(row) for row in engine.hurt.entries],
            "cursors": [engine.attack.cursor, engine.hurt.cursor],
            "effect_vbl": self.scene.fight.effect_vbl,
        }
