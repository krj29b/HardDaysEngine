"""Integer VBL screen-shake state (ARCHITECTURE.md §10.27)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harddaysengine.sim.fight import Fight
    from harddaysengine.sim.fight_script import RoutineCall

SCREEN_SHAKE = "screen_shake"


@dataclass(slots=True, frozen=True)
class ShakeRules:
    """The decoded reload period and signed vertical displacement sequence (§10.27)."""

    period: int
    offsets: tuple[int, ...]

    def __post_init__(self) -> None:
        if self.period <= 0:
            raise ValueError("screen-shake period must be positive")


@dataclass(slots=True)
class ShakeState:
    """A single restartable VBL hook; rendering only reads offset (§10.27)."""

    active: bool = False
    wait: int = 0
    index: int = 0
    offset: int = 0

    def start(self, rules: ShakeRules) -> None:
        self.active, self.wait, self.index = True, rules.period, 0

    def advance(self, ticks: int, rules: ShakeRules) -> None:
        """Consume elapsed VBLs, stepping at table boundaries without a per-VBL loop."""
        while self.active and ticks >= self.wait:
            ticks -= self.wait
            if self.index == len(rules.offsets):
                self.active, self.offset, self.wait = False, 0, 0
            else:
                self.offset = rules.offsets[self.index]
                self.index += 1
                self.wait = rules.period
        if self.active:
            self.wait -= ticks


def screen_shake(rules: ShakeRules, fight: Fight, _call: RoutineCall | None) -> None:
    """Restart the decoded heavy-impact screen effect (§10.27)."""
    fight.shake.start(rules)
