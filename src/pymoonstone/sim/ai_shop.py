"""Computer town wishes and purchases (ARCHITECTURE.md §11.13/20)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pymoonstone.sim.m68k import s8, s16, s32, u8, u16
from pymoonstone.sim.rewards import recalc_endurance, recalc_hp

if TYPE_CHECKING:
    from pymoonstone.sim.actor import Actor
    from pymoonstone.sim.ai_map_state import MapAiRules, ShopWish
    from pymoonstone.sim.inventory import Inventory

LIFE = 73
DAGGERS = 76
SWORD = 88
ARMOUR = 92


def _armour(actor: Actor, wish: ShopWish, rules: MapAiRules) -> bool:
    for index, (price, item) in enumerate(rules.armour):
        if s16(actor.gold) < s16(price):
            continue
        wants = actor.armour != item if index == 0 else s32(actor.armour) < s32(item)
        if wants:
            wish.cost, wish.kind, wish.item = price, ARMOUR, item
        return wants
    return False


def _sword(actor: Actor, wish: ShopWish, rules: MapAiRules) -> bool:
    price, item = rules.claymore
    if s16(actor.gold) >= s16(price):
        if actor.sword == item:
            return False
        wish.cost, wish.kind, wish.item = price, SWORD, item
    price, item = rules.broad
    if s16(actor.gold) >= s16(price) and s32(actor.sword) < s32(item):
        wish.cost, wish.kind = price, SWORD
        return True
    return False


def shop_wish(actor: Actor, wish: ShopWish, rules: MapAiRules) -> bool:
    """Preserve stale item cells and the sword's fallthrough (§11.13)."""
    if s16(actor.gold) <= s16(rules.minimum_gold):
        return False
    if s16(actor.gold) > s16(rules.life_price) and s8(actor.lives) <= rules.life_threshold:
        wish.cost, wish.kind = rules.life_price, LIFE
        return True
    if _armour(actor, wish, rules) or _sword(actor, wish, rules):
        return True
    if s8(actor.daggers) > rules.dagger_threshold:
        return False
    wish.cost, wish.kind, wish.item = rules.dagger_price, DAGGERS, wish.item & 65535
    return True


def _daggers(actor: Actor, rules: MapAiRules) -> None:
    for _ in range(256):
        actor.daggers = u8(actor.daggers + 1)
        if s16(actor.gold) < s16(rules.dagger_price):
            return
        if (actor.daggers << 8 | actor.type) == rules.dagger_cap:
            return
        actor.gold = u8((actor.gold >> 8) - rules.dagger_price) << 8 | (actor.gold & 255)
    raise ValueError("computer dagger purchase did not terminate")


def purchase(actor: Actor, inventory: Inventory, wish: ShopWish, rules: MapAiRules) -> None:
    """Apply the original wish, including the high-byte dagger debit (§11.13)."""
    actor.gold = u16(actor.gold - wish.cost)
    if wish.kind == SWORD:
        actor.sword = wish.item
    elif wish.kind == ARMOUR:
        actor.armour = wish.item
        recalc_hp(actor, inventory)
        recalc_endurance(actor)
    elif wish.kind == LIFE:
        actor.hp, actor.lives = actor.hp_max, u8(actor.lives + 1)
    elif wish.kind == DAGGERS:
        _daggers(actor, rules)
