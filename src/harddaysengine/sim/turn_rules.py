"""Decoded calendar, text, gift and map-dragon records (ARCHITECTURE.md §11.19)."""

from dataclasses import dataclass

from harddaysengine.sim.shop_types import StockRules


@dataclass(slots=True, frozen=True)
class TextLine:
    text: str
    x: int
    y: int
    style: int


@dataclass(slots=True, frozen=True)
class GiftRules:
    bands: tuple[tuple[int, int], ...]
    stats: tuple[int, ...]
    limits: tuple[int, int, int]
    transfer: tuple[int, ...]


@dataclass(slots=True, frozen=True)
class MapDragonRules:
    day: int
    stores: tuple[tuple[int, int, int], ...]
    scripts: tuple[int, ...]
    hover: int
    initial_job: tuple[int, int, int]
    speed_x: int
    speed_y: int
    timer: int
    hover_limit: int
    right: int
    right_reset: int
    left: int
    left_reset: int
    bottom: int
    top_reset: int
    progress: int


@dataclass(slots=True, frozen=True)
class TurnRules:
    moons: tuple[int, ...]
    next_day: tuple[TextLine, ...]
    farewell: tuple[TextLine, ...]
    span: int
    moon_x: int
    moon_y: int
    dragon: MapDragonRules
    gifts: GiftRules
    dragon_defaults: tuple[tuple[int, int, int], ...]
    stock: StockRules | None = None
