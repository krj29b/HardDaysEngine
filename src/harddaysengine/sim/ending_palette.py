"""The ``program`` palette: live colours, colour registers, one fade and six ramps (§12.39.10).

The live palette is what fades and ramps move; the colour registers are what the screen shows.
A tick copies the live palette to the registers only when something moved, so a direct register
write (a flash) lasts until the next change or until it is written back.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING

from harddaysengine.sim.m68k import u16
from harddaysengine.sim.palette_fx import PALETTE_SIZE, step_color

if TYPE_CHECKING:
    from collections.abc import Sequence

RAMP_SLOTS = 6
BLACK = (0,) * PALETTE_SIZE


@dataclass(slots=True)
class Ramp:
    """One colour moving to ``target`` every ``period`` ticks, then back again."""

    colour: int
    target: int
    period: int
    counter: int
    back: int
    repeat: int


@dataclass(slots=True)
class ProgramPalette:
    live: list[int] = field(default_factory=lambda: [0] * PALETTE_SIZE)
    regs: list[int] = field(default_factory=lambda: [0] * PALETTE_SIZE)
    target: tuple[int, ...] | None = None
    period: int = 0
    counter: int = 0
    ramps: list[Ramp | None] = field(default_factory=lambda: [None] * RAMP_SLOTS)

    def write(self, colours: Sequence[int]) -> None:
        """Load the colour registers now; the live palette is unchanged."""
        self.regs = list(colours)

    def copy(self, colours: Sequence[int]) -> None:
        """Replace the live palette; the registers follow at the next change."""
        self.live = list(colours)

    def blackout(self) -> None:
        self.regs = [0] * PALETTE_SIZE
        self.live = [0] * PALETTE_SIZE

    def fade(self, target: Sequence[int], period: int) -> None:
        self.target = tuple(target)
        self.period = self.counter = period

    def ramp(self, colour: int, target: int, period: int, repeat: int) -> int | None:
        """Start a ramp in the first free slot; the colour starts from the live palette."""
        for slot, ramp in enumerate(self.ramps):
            if ramp is None:
                self.ramps[slot] = Ramp(colour, target, period, period, self.live[colour], repeat)
                return slot
        return None

    def free(self, slot: int) -> None:
        self.ramps[slot] = None

    def tick(self) -> None:
        """One VBL: fade step, ramp steps, then the registers if anything moved."""
        changed = False
        if self.target is not None:
            self.counter = u16(self.counter - 1)
            if self.counter == 0:
                self.counter = self.period
                stepped = [step_color(c, t) for c, t in zip(self.live, self.target, strict=True)]
                changed = stepped != self.live
                self.live = stepped
                if not changed:
                    self.target = None
        for slot in range(RAMP_SLOTS):
            changed = self._ramp_step(slot) or changed
        if changed:
            self.regs = list(self.live)

    def _ramp_step(self, slot: int) -> bool:
        ramp = self.ramps[slot]
        if ramp is None:
            return False
        ramp.counter = u16(ramp.counter - 1)
        if ramp.counter:
            return False
        ramp.counter = ramp.period
        colour = step_color(self.live[ramp.colour], ramp.target)
        self.live[ramp.colour] = colour
        if colour == ramp.target:
            ramp.target, ramp.back = ramp.back, ramp.target
            if ramp.repeat:
                ramp.repeat -= 1
                if not ramp.repeat:
                    self.ramps[slot] = None
        return True

    def snapshot(self) -> dict[str, object]:
        return {
            "live": tuple(self.live),
            "regs": tuple(self.regs),
            "target": self.target,
            "period": self.period,
            "counter": self.counter,
            "ramps": tuple(None if r is None else asdict(r) for r in self.ramps),
        }
