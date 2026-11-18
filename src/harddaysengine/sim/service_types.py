"""Runtime service rule records (ARCHITECTURE.md §12.30)."""

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class HealerRules:
    heal_price: int
    life_price: int
    life_cap: int
    reply_limit: int


@dataclass(slots=True, frozen=True)
class MysticRules:
    bands: tuple[tuple[int, int], ...]
    win_at: int
    hp_step: int
    hp_floor: int
    stat_floor: int


@dataclass(slots=True, frozen=True)
class DiceRules:
    mask: int
    face_max: int
    rows: tuple[tuple[int, int, int, int], ...]


@dataclass(slots=True, frozen=True)
class MysticResult:
    outcome: str
    stat: int


@dataclass(slots=True, frozen=True)
class DiceResult:
    dice: tuple[int, int, int]
    multiplier: int
    winnings: int
