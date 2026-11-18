"""Valley caller state and original reward mutations (ARCHITECTURE.md §12.38)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING

from harddaysengine.sim.m68k import u8, u16
from harddaysengine.sim.rewards import pick_stat

if TYPE_CHECKING:
    from harddaysengine.sim.actor import Actor
    from harddaysengine.sim.inventory import Inventory
    from harddaysengine.sim.rng import Rng
    from harddaysengine.sim.turn_rules import GiftRules


@dataclass(slots=True, frozen=True)
class ValleyRules:
    keys: int
    life_loss: int
    stat_floor: int
    stat_loss: int
    progress: int
    key_clear: int
    reward_wait: int
    moon_mask: int
    fade_period: int
    fade_wait: int


class ValleyPhase(IntEnum):
    MAP_OUT = 0
    MISSING = 1
    LOADING = 2
    LOADING_OUT = 3
    COMBAT = 4
    COMBAT_OUT = 5
    VICTORY = 6
    REWARD_WAIT = 7
    EXIT_OUT = 8
    TEMPLE = 9
    TEMPLE_OUT = 10


@dataclass(slots=True)
class ValleyState:
    knight: int
    matched: bool
    loading_index: int
    phase: ValleyPhase = ValleyPhase.MAP_OUT
    wait: int = 0
    fire_held: bool = False
    won: bool = False

    def released(self, fire: bool) -> bool:
        if fire:
            self.fire_held = True
            return False
        released, self.fire_held = self.fire_held, False
        return released


def defeat(actor: Actor, rng: Rng, gifts: GiftRules, rules: ValleyRules) -> int:
    """Stat pick precedes the extra byte life loss; the equality floor is kept (§12.38)."""
    stat = pick_stat(actor, rng, gifts)
    actor.lives = u8(actor.lives - rules.life_loss)
    if stat and actor.read(stat, 1) != rules.stat_floor:
        actor.write(stat, 1, u8(actor.read(stat, 1) - rules.stat_loss))
    return stat


def victory(actor: Actor, inventory: Inventory, rules: ValleyRules) -> None:
    actor.progress = u16(actor.progress + rules.progress)
    inventory.counts[20 // 2] = rules.key_clear


def moonstone(inventory: Inventory, rng: Rng, rules: ValleyRules) -> int:
    bit = rng.draw() & rules.moon_mask
    inventory.counts[22 // 2] = u8(inventory.byte(22) | (1 << bit))
    return bit
