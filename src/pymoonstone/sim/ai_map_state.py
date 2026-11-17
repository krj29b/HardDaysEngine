"""Decoded rules and mutable computer map cells (ARCHITECTURE.md §11.20)."""

from dataclasses import dataclass, field


@dataclass(slots=True, frozen=True)
class MapAiRules:
    engage: int
    stat_price: int
    rest_lives: int
    rest_cap_test: int
    rest_cap: int
    minimum_gold: int
    life_price: int
    life_threshold: int
    armour: tuple[tuple[int, int], ...]
    claymore: tuple[int, int]
    broad: tuple[int, int]
    dagger_price: int
    dagger_threshold: int
    dagger_cap: int
    towns: tuple[tuple[int, int, int, int], ...]


@dataclass(slots=True)
class WalkLine:
    initialized: bool = False
    dx: int = 0
    dy: int = 0
    x_bit: int = 0
    y_bit: int = 0
    y_major: bool = False
    error: int = 0


@dataclass(slots=True)
class ShopWish:
    cost: int = 0
    kind: int = 0
    item: int = 0


@dataclass(slots=True)
class MapAiCells:
    built: bool = False
    roam: list[tuple[int, int]] = field(default_factory=list)
    target: int = 0
    hidden: bool = False
    neighbors: list[tuple[int, int]] = field(default_factory=list)
    gate: int = 0
    town: tuple[int, int] | None = None
    town_distances: tuple[int, int] = (0, 0)
    wish: ShopWish = field(default_factory=ShopWish)
    walk: WalkLine = field(default_factory=WalkLine)
    pending: bool = False
    after_dragon: bool = False
