"""Original meeting decisions and retained encounter state (§12.40)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harddaysengine.sim.actor import Actor
    from harddaysengine.sim.inventory import Inventory


@dataclass(slots=True, frozen=True)
class MeetingRules:
    computer_kind: int
    protection_slot: int
    progress: int
    loot_sheet: int
    temple_sheet: int
    hoard_sheet: int
    fade_period: int
    fade_wait: int


class MeetingAction(IntEnum):
    SETTLE = 0
    PROTECT = 1
    FIGHT = 2
    RETURN = 3


class MeetingPhase(IntEnum):
    MAP_OUT = 0
    PROTECT = 1
    PROTECT_OUT = 2
    PROTECT_SHEET = 3
    PROTECT_CLOSING = 4
    COMBAT = 5
    COMBAT_OUT = 6
    RESULT_SHEET = 7
    RESULT_CLOSING = 8


@dataclass(slots=True)
class MeetingState:
    first: int
    second: int
    phase: MeetingPhase = MeetingPhase.MAP_OUT
    wait: int = 0
    fire_held: bool = False
    defeat: int = 0
    confused: int = 0
    hoard: bool = False

    def released(self, fire: bool) -> bool:
        if fire:
            self.fire_held = True
            return False
        released, self.fire_held = self.fire_held, False
        return released


def decide(first: Actor, second: Actor, inventory: Inventory, rules: MeetingRules) -> MeetingAction:
    """Exactly-zero lives bypasses combat; protection precedes the two-computer test (§12.40)."""
    if second.frog_days or second.lives == 0:
        return MeetingAction.SETTLE
    if second.kind != rules.computer_kind and inventory.byte(rules.protection_slot):
        return MeetingAction.PROTECT
    return after_protection(first, second, rules)


def after_protection(first: Actor, second: Actor, rules: MeetingRules) -> MeetingAction:
    if first.kind == second.kind == rules.computer_kind:
        return MeetingAction.RETURN
    return MeetingAction.FIGHT
