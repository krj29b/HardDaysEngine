"""Immutable smith, market and starting-stock rules (ARCHITECTURE.md §12.29)."""

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class ShopRules:
    armour: tuple[tuple[int, int, int], ...]
    swords: tuple[tuple[int, int], ...]
    dagger_price: int
    dagger_cap: int
    prices: tuple[int, ...]
    gold_cap: int
    sell_shift: int
    split_x: int


@dataclass(slots=True, frozen=True)
class StockRules:
    hoard_rolls: int
    market_rolls: int
    extra_slot: int
    extra_count: int
    key_mask: int
    key_limit: int
    key_groups: tuple[int, ...]
    loot_bands: tuple[tuple[int, int], ...]
