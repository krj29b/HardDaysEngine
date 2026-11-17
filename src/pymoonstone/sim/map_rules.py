"""Immutable original map data and palette jobs (ARCHITECTURE.md §11.18)."""

from dataclasses import dataclass, field

from pymoonstone.sim.m68k import u16
from pymoonstone.sim.palette_fx import cycle_step


@dataclass(slots=True, frozen=True)
class MapNode:
    """A place row, in executable table order (§11.7)."""

    kind: int
    x: int
    y: int


@dataclass(slots=True, frozen=True)
class LairStart:
    """Decoded initial fields of a twenty-byte lair (§9.5)."""

    handler: int
    count: int
    x: int
    y: int
    region: int
    arena: str


@dataclass(slots=True, frozen=True)
class MapRules:
    """Loaded map tables, record stores, frame sizes and text (§11.16/18)."""

    nodes: tuple[MapNode, ...]
    lairs: tuple[LairStart, ...]
    terrain: bytes
    regions: bytes
    sizes: tuple[tuple[int, int], ...]
    seeds: tuple[int, ...]
    defaults: tuple[tuple[int, int, int], ...]
    human_positions: tuple[tuple[int, int], ...]
    computer_positions: tuple[tuple[int, int], ...]
    human_names: tuple[str, ...]
    computer_names: tuple[str, ...]
    may_text: str
    lair_text: str
    battle_text: str
    place_texts: tuple[str, ...]
    char_map: bytes
    key_table: bytes
    ramp_index: int
    ramp_target: int
    ramp_period: int
    cycle_start: int
    cycle_end: int
    cycle_period: int
    terrain_tail: bytes = b""
    region_tail: bytes = b""

    def __post_init__(self) -> None:
        if len(self.terrain) != 1000 or len(self.regions) != 1000:
            raise ValueError("map grids must each contain 40 by 25 cells")
        if len(self.seeds) != 4 or len(self.human_positions) != 4:
            raise ValueError("map needs four seeds and four human start positions")
        if len(self.computer_positions) != 4 or len(self.lairs) != 24:
            raise ValueError("map needs four computer positions and twenty-four lairs")

    def size(self, frame: int) -> tuple[int, int]:
        """Exact decoded frame size; missing frame IDs raise (§11.7)."""
        if not 0 <= frame < len(self.sizes):
            raise ValueError(f"map sprite frame {frame} is not loaded")
        return self.sizes[frame]


def _palette_step(current: int, target: int) -> int:
    for mask, step in ((15, 1), (240, 16), (65280, 256)):
        old, new = current & mask, target & mask
        if old < new:
            current += step
        elif old > new:
            current -= step
    return u16(current)


@dataclass(slots=True)
class MapColors:
    """PAL-owned ramp and cycle state; renders only read colors (§11.18)."""

    colors: list[int] = field(default_factory=list)
    saved: int = 0
    target: int = 0
    ramp_clock: int = 0
    cycle_clock: int = 0
    active: bool = False

    def start(self, palette: tuple[int, ...], rules: MapRules) -> None:
        """Start fresh jobs from the original map palette (§11.15)."""
        self.colors = list(palette)
        self.saved, self.target = self.colors[rules.ramp_index], rules.ramp_target
        self.ramp_clock, self.cycle_clock = rules.ramp_period, rules.cycle_period
        self.active = True

    def step(self, rules: MapRules) -> None:
        """Advance both jobs once per PAL VBL, independently of map frames (§11.18)."""
        if not self.active:
            return
        self.ramp_clock = u16(self.ramp_clock - 1)
        if self.ramp_clock == 0:
            self.ramp_clock = rules.ramp_period
            index = rules.ramp_index
            self.colors[index] = _palette_step(self.colors[index], self.target)
            if self.colors[index] == self.target:
                self.saved, self.target = self.target, self.saved
        self.cycle_clock = (self.cycle_clock - 1) & 255
        if self.cycle_clock == 0:
            self.cycle_clock = rules.cycle_period
            self.colors[:] = cycle_step(self.colors, rules.cycle_start, rules.cycle_end)
