"""Home-village reward and retained temple caller (ARCHITECTURE.md §12.37)."""

from dataclasses import dataclass
from enum import IntEnum

from harddaysengine.sim.actor import Actor
from harddaysengine.sim.m68k import s8, u8


@dataclass(slots=True, frozen=True)
class VillageRules:
    life_cap: int
    life_gain: int
    sheet: int
    fade_period: int
    fade_wait: int


class VillagePhase(IntEnum):
    MAP_OUT = 0
    TEMPLE = 1
    TEMPLE_OUT = 2


@dataclass(slots=True)
class VillageVisit:
    knight: int
    rules: VillageRules
    phase: VillagePhase = VillagePhase.MAP_OUT
    wait: int = 0

    def snapshot(self) -> tuple[int, int, int]:
        return self.knight, int(self.phase), self.wait


def reward(actor: Actor, rules: VillageRules) -> None:
    """Original signed cap and byte addition, with no healing or RNG (§12.37)."""
    if s8(actor.lives) < s8(rules.life_cap):
        actor.lives = u8(actor.lives + rules.life_gain)
