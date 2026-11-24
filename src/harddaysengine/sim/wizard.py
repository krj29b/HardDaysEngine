"""Persistent wizard jobs and wait-before-input animation (ARCHITECTURE.md §12.36)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING

from harddaysengine.sim.actor import Actor
from harddaysengine.sim.fight import Fight

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harddaysengine.sim.fight import FightConstants
    from harddaysengine.sim.fight_script import CelShape, Draw, ScriptImage
    from harddaysengine.sim.rewards import GiftResult
    from harddaysengine.sim.rng import Rng
    from harddaysengine.sim.sound import SoundRequest
    from harddaysengine.sim.wizard_types import WizardRules


class WizardPhase(IntEnum):
    MAP_OUT = 0
    LOADING = 1
    LOADING_OUT = 2
    ARRIVAL = 3
    ARRIVAL_OUT = 4
    GIFT = 5
    FAREWELL = 6
    EXIT_OUT = 7
    TEMPLE = 8
    TEMPLE_OUT = 9


@dataclass(slots=True)
class WizardState:
    knight: int
    kind: int
    loading_index: int
    phase: WizardPhase = WizardPhase.MAP_OUT
    wait: int = 0
    result: GiftResult | None = None


@dataclass(slots=True, frozen=True)
class WizardEngine:
    image: ScriptImage
    cels: Mapping[str, CelShape]
    constants: FightConstants


@dataclass(slots=True)
class WizardAnimation:
    fight: Fight
    frame_wait: int
    countdown: int
    batches: list[list[Draw]] = field(default_factory=list)
    sounds: list[SoundRequest] = field(default_factory=list)

    def snapshot(self) -> dict[str, object]:
        """Retain jobs, motion work, per-job lists and the displayed engine output (§12.36)."""
        engine = self.fight.engine
        return {
            "countdown": self.countdown,
            "frame_wait": self.frame_wait,
            "jobs": [asdict(job) for job in engine.jobs],
            "actors": [asdict(actor) for actor in self.fight.actors],
            "box": asdict(engine.box),
            "mirrored": sorted(engine.mirrored),
            "output": asdict(engine.output),
            "vbl": engine.vbl,
            "effect_vbl": self.fight.effect_vbl,
            "beam": self.fight.beam_word,
            "rng": self.fight.rng.state,
        }

    def advance(self, vbl: int, fire: bool) -> bool:
        """Sample held fire only after the entire previous frame wait (§12.36)."""
        self.batches, self.sounds = [], []
        self.countdown -= 1
        if self.countdown:
            return False
        if fire:
            return True
        self.draw_pass(vbl)
        self.countdown = self.frame_wait
        return False

    def draw_pass(self, vbl: int) -> None:
        output = self.fight.step(vbl)
        if output.skipped_calls:
            raise ValueError("wizard scripts called an unimplemented engine routine")
        self.batches.append(list(output.tick.draws))
        self.sounds.extend(output.sounds)


def open_animation(
    rules: WizardRules, engine: WizardEngine, stage: int, rng: Rng, vbl: int
) -> WizardAnimation:
    """Build the original fresh stage pool, including the tower's two initial passes (§12.36)."""
    if stage not in (0, 1, 2):
        raise ValueError("wizard animation stage outside 0..2")
    fight = Fight(
        engine.image,
        engine.cels,
        {1: (rules.cel,) * 5},
        [Actor() for _ in range(4)],
        Actor(),
        engine.constants,
        rng=rng,
    )
    spawns = rules.spawns[1:] if stage == 1 else rules.spawns[:1]
    for spawn in spawns:
        fight.spawn_actor(spawn.script, 1, spawn.position, spawn.facing, spawn.kind)
    run = WizardAnimation(fight, rules.waits[stage], rules.waits[stage])
    if stage == 0:
        run.draw_pass(vbl)
        run.sounds.extend(rules.sounds)
    run.draw_pass(vbl)
    return run
