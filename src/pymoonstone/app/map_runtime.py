"""Map jobs on the existing script engine (ARCHITECTURE.md §11.19)."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING

import numpy as np

from pymoonstone.app.fight_scene import FightScene
from pymoonstone.assets.fight_program import cel_shape, fight_constants, script_image
from pymoonstone.assets.hunk import parse_hunk_executable
from pymoonstone.assets.mog_code import MogCode
from pymoonstone.sim.fight import Fight
from pymoonstone.sim.map_dragon import spawn

if TYPE_CHECKING:
    from pymoonstone.app.scene_assets import GameAssetBundle
    from pymoonstone.sim.overworld import OverworldState
    from pymoonstone.sim.turn_rules import TurnRules


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
        self.rules = rules

    def enter(self, world: OverworldState, vbl: int) -> None:
        """Existing jobs run before the spawn attempt (§11.4)."""
        self.advance(world, vbl)
        if spawn(world, self.scene.fight, self.rules.dragon):
            self.scene.canvas.accept([])

    def advance(self, world: OverworldState, vbl: int) -> None:
        """One map job pass, with no separate animation counter (§11.19)."""
        if world.dragon_active:
            self.scene.advance(vbl)

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
