"""Pure-integer sacred places, towns, shops, and Lair loot logic (ARCHITECTURE.md §5, §7)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING

from pymoonstone.sim.location_state import KnightState, SecondaryWeapon
from pymoonstone.sim.overworld import (
    MOONSTONE_COUNT,
    MoonPhase,
    OverworldState,
    collect_moonstone,
    grant_lair_key,
)

if TYPE_CHECKING:
    from pymoonstone.sim.prng import SimPrng
    from pymoonstone.sim.types import KnightId

# Equipment and stat caps.
MAX_SWORD_TIER: int = 4
MIN_SWORD_TIER: int = 1
MAX_ARMOUR_LEVEL: int = 4
MAX_SHIELD_LEVEL: int = 3
MAX_DAGGERS: int = 20
MAX_LIVES: int = 5
MAX_STAT_LEVEL: int = 5
MAX_BLESSED_HP: int = 160
MAX_BLESSED_STAMINA: int = 160

# Highwood merchant pricing (integer gold).
SWORD_UPGRADE_BASE_COST: int = 100
ARMOUR_UPGRADE_BASE_COST: int = 80
SHIELD_UPGRADE_BASE_COST: int = 60
DAGGER_UNIT_COST: int = 5
HEALING_POTION_COST: int = 30
HEALING_POTION_HP: int = 35
STAMINA_TONIC_COST: int = 20
STAMINA_TONIC_AMOUNT: int = 40

# Highwood healer & tavern constants.
MIN_HEAL_COST: int = 5
RESTORE_LIFE_COST: int = 150
LIFT_CURSE_COST: int = 40
MIN_TAVERN_WAGER: int = 1
MAX_TAVERN_WAGER: int = 500

# Waterdeep mystic & training constants.
FIREBALL_SCROLL_COST: int = 60
FIREBALL_SCROLL_CHARGES: int = 3
BLESSING_COST: int = 80
BLESSING_HP_BONUS: int = 10
BLESSING_STAMINA_BONUS: int = 10
HINT_COST: int = 30
LAIR_KEY_COST: int = 120
TRAINING_GOLD_PER_LEVEL: int = 50
TRAINING_XP_PER_LEVEL: int = 25
TRAINING_VITALITY_STEP: int = 15

# Stonehenge & Lair constants.
RITUAL_VICTORY_XP: int = 500
LAIR_BASE_GOLD: int = 80
LAIR_GOLD_PER_TIER: int = 40
LAIR_GOLD_VARIANCE: int = 41
LAIR_BASE_XP: int = 50
LAIR_XP_PER_TIER: int = 25
LAIR_XP_VARIANCE: int = 21
LAIR_HEAL_BONUS: int = 20


class MerchantItemKind(IntEnum):
    """Items traded by the Highwood merchant."""

    SWORD_UPGRADE = 0
    ARMOUR_UPGRADE = 1
    SHIELD_UPGRADE = 2
    THROWING_KNIFE = 3
    HEALING_POTION = 4
    STAMINA_TONIC = 5


class HealerService(IntEnum):
    """Restoration services offered by the Highwood healer."""

    RESTORE_HP = 0
    RESTORE_LIFE = 1
    LIFT_CURSE = 2


class DiceOutcome(IntEnum):
    """Outcome of a Highwood tavern dice round."""

    WIN = 0
    LOSS = 1
    PUSH = 2
    INVALID_WAGER = 3
    INSUFFICIENT_GOLD = 4


class MysticService(IntEnum):
    """Arcane services offered by the Waterdeep mystic."""

    FIREBALL_SCROLL = 0
    BLESSING = 1
    KEY_HINT = 2
    LAIR_KEY = 3


class TrainableStat(IntEnum):
    """Knight attributes trainable at Waterdeep."""

    STRENGTH = 0
    CONSTITUTION = 1
    ENDURANCE = 2


@dataclass(slots=True, frozen=True)
class MerchantTransactionResult:
    """Immutable result of a Highwood merchant buy or sell action."""

    accepted: bool
    item: MerchantItemKind
    is_buy: bool
    quantity: int
    gold_delta: int
    remaining_gold: int
    reason: str


@dataclass(slots=True, frozen=True)
class HealerResult:
    """Immutable result of a Highwood healer visit."""

    accepted: bool
    service: HealerService
    gold_spent: int
    hp_restored: int
    lives_restored: int
    curse_lifted: bool
    remaining_gold: int
    reason: str


@dataclass(slots=True, frozen=True)
class TavernDiceResult:
    """Immutable result of a Highwood tavern 2d6 dice wager."""

    accepted: bool
    outcome: DiceOutcome
    wager: int
    player_dice: tuple[int, int]
    house_dice: tuple[int, int]
    player_total: int
    house_total: int
    gold_delta: int
    remaining_gold: int
    reason: str


@dataclass(slots=True, frozen=True)
class MysticResult:
    """Immutable result of a Waterdeep mystic service."""

    accepted: bool
    service: MysticService
    gold_spent: int
    hinted_lair_index: int | None
    granted_key_index: int | None
    remaining_gold: int
    reason: str


@dataclass(slots=True, frozen=True)
class TrainingResult:
    """Immutable result of a Waterdeep stat training session."""

    accepted: bool
    stat: TrainableStat
    old_level: int
    new_level: int
    gold_spent: int
    xp_spent: int
    remaining_gold: int
    remaining_xp: int
    reason: str


@dataclass(slots=True, frozen=True)
class RitualResult:
    """Immutable result of attempting the Stonehenge Moonstone ritual."""

    accepted: bool
    victory: bool
    knight_id: KnightId
    moonstones_held: int
    moon_phase: MoonPhase
    at_stonehenge: bool
    reason: str


@dataclass(slots=True, frozen=True)
class LairLootResult:
    """Immutable record of deterministic rewards from clearing a Lair."""

    lair_index: int
    knight_id: KnightId
    gold_awarded: int
    xp_awarded: int
    moonstone_claimed: bool
    key_granted: int | None
    daggers_awarded: int
    hp_restored: int


def _sync_to_overworld(state: OverworldState | None, knight: KnightState) -> None:
    """Synchronize mutable resources from ``knight`` to ``state.knights`` when present."""
    if state is None:
        return
    ow_knight = state.knights.get(knight.knight_id)
    if ow_knight is not None:
        ow_knight.gold = knight.gold
        ow_knight.xp = knight.xp
        ow_knight.lives = knight.lives


def merchant_buy_price(knight: KnightState, item: MerchantItemKind, quantity: int = 1) -> int:
    """Compute the integer gold price to buy ``quantity`` of ``item``."""
    if quantity <= 0:
        return 0
    if item == MerchantItemKind.SWORD_UPGRADE:
        return knight.sword_tier * SWORD_UPGRADE_BASE_COST * quantity
    if item == MerchantItemKind.ARMOUR_UPGRADE:
        return (knight.armour + 1) * ARMOUR_UPGRADE_BASE_COST * quantity
    if item == MerchantItemKind.SHIELD_UPGRADE:
        return (knight.shield + 1) * SHIELD_UPGRADE_BASE_COST * quantity
    if item == MerchantItemKind.THROWING_KNIFE:
        return DAGGER_UNIT_COST * quantity
    if item == MerchantItemKind.HEALING_POTION:
        return HEALING_POTION_COST * quantity
    return STAMINA_TONIC_COST * quantity


def _can_buy_item(knight: KnightState, item: MerchantItemKind, quantity: int) -> str | None:
    """Return a rejection reason if ``knight`` cannot buy ``quantity`` of ``item``."""
    if quantity <= 0:
        return "invalid_quantity"
    gear_caps = {
        MerchantItemKind.SWORD_UPGRADE: (knight.sword_tier, MAX_SWORD_TIER),
        MerchantItemKind.ARMOUR_UPGRADE: (knight.armour, MAX_ARMOUR_LEVEL),
        MerchantItemKind.SHIELD_UPGRADE: (knight.shield, MAX_SHIELD_LEVEL),
    }
    if item in gear_caps:
        current, cap = gear_caps[item]
        return "inventory_limit" if quantity != 1 or current >= cap else None
    if item == MerchantItemKind.THROWING_KNIFE:
        return "inventory_limit" if knight.daggers + quantity > MAX_DAGGERS else None
    if item == MerchantItemKind.HEALING_POTION:
        return "already_full_hp" if knight.hp >= knight.max_hp else None
    return "already_full_stamina" if knight.stamina >= knight.max_stamina else None


def _apply_merchant_purchase(knight: KnightState, item: MerchantItemKind, quantity: int) -> None:
    """Apply purchased item effect to ``knight``."""
    if item == MerchantItemKind.SWORD_UPGRADE:
        knight.sword_tier += 1
    elif item == MerchantItemKind.ARMOUR_UPGRADE:
        knight.armour += 1
    elif item == MerchantItemKind.SHIELD_UPGRADE:
        knight.shield += 1
    elif item == MerchantItemKind.THROWING_KNIFE:
        knight.daggers += quantity
    elif item == MerchantItemKind.HEALING_POTION:
        knight.hp = min(knight.max_hp, knight.hp + HEALING_POTION_HP * quantity)
    else:
        knight.stamina = min(knight.max_stamina, knight.stamina + STAMINA_TONIC_AMOUNT * quantity)


def buy_merchant_item(
    knight: KnightState,
    item: MerchantItemKind,
    quantity: int = 1,
    *,
    overworld: OverworldState | None = None,
) -> MerchantTransactionResult:
    """Purchase equipment or supplies from the Highwood merchant."""
    rejection = _can_buy_item(knight, item, quantity)
    if rejection is not None:
        return MerchantTransactionResult(False, item, True, quantity, 0, knight.gold, rejection)

    cost = merchant_buy_price(knight, item, quantity)
    if knight.gold < cost:
        return MerchantTransactionResult(
            False, item, True, quantity, 0, knight.gold, "insufficient_gold"
        )

    knight.gold -= cost
    _apply_merchant_purchase(knight, item, quantity)
    _sync_to_overworld(overworld, knight)
    return MerchantTransactionResult(True, item, True, quantity, -cost, knight.gold, "purchased")


def _sell_refund_and_validate(
    knight: KnightState, item: MerchantItemKind, quantity: int
) -> tuple[int, str | None]:
    """Validate a sale and return ``(refund_gold, rejection_reason)``."""
    if quantity <= 0:
        return 0, "invalid_quantity"
    gear_sell = {
        MerchantItemKind.SWORD_UPGRADE: (
            knight.sword_tier,
            MIN_SWORD_TIER,
            SWORD_UPGRADE_BASE_COST,
        ),
        MerchantItemKind.ARMOUR_UPGRADE: (knight.armour, 0, ARMOUR_UPGRADE_BASE_COST),
        MerchantItemKind.SHIELD_UPGRADE: (knight.shield, 0, SHIELD_UPGRADE_BASE_COST),
    }
    if item in gear_sell:
        level, floor_level, base_cost = gear_sell[item]
        if quantity != 1 or level <= floor_level:
            return 0, "nothing_to_sell"
        sell_tier = level - 1 if item == MerchantItemKind.SWORD_UPGRADE else level
        return (sell_tier * base_cost) // 2, None
    if item == MerchantItemKind.THROWING_KNIFE:
        if knight.daggers < quantity:
            return 0, "nothing_to_sell"
        return (DAGGER_UNIT_COST * quantity) // 2, None
    return 0, "not_sellable"


def sell_merchant_item(
    knight: KnightState,
    item: MerchantItemKind,
    quantity: int = 1,
    *,
    overworld: OverworldState | None = None,
) -> MerchantTransactionResult:
    """Sell carried equipment or throwing knives to the Highwood merchant."""
    refund, rejection = _sell_refund_and_validate(knight, item, quantity)
    if rejection is not None:
        return MerchantTransactionResult(False, item, False, quantity, 0, knight.gold, rejection)

    if item == MerchantItemKind.SWORD_UPGRADE:
        knight.sword_tier -= 1
    elif item == MerchantItemKind.ARMOUR_UPGRADE:
        knight.armour -= 1
    elif item == MerchantItemKind.SHIELD_UPGRADE:
        knight.shield -= 1
    elif item == MerchantItemKind.THROWING_KNIFE:
        knight.daggers -= quantity

    knight.gold += refund
    _sync_to_overworld(overworld, knight)
    return MerchantTransactionResult(True, item, False, quantity, refund, knight.gold, "sold")


def healer_hp_cost(knight: KnightState) -> int:
    """Compute integer gold cost to restore ``knight`` to full HP."""
    missing = max(0, knight.max_hp - knight.hp)
    if missing == 0:
        return 0
    return max(MIN_HEAL_COST, (missing + 1) // 2)


def visit_healer(
    knight: KnightState,
    service: HealerService,
    *,
    cursed: bool = False,
    overworld: OverworldState | None = None,
) -> HealerResult:
    """Use the Highwood healer for HP restoration, life recovery, or curse removal."""
    if service == HealerService.RESTORE_HP:
        return _healer_restore_hp(knight, overworld)
    if service == HealerService.RESTORE_LIFE:
        return _healer_restore_life(knight, overworld)
    return _healer_lift_curse(knight, cursed=cursed, overworld=overworld)


def _healer_restore_hp(knight: KnightState, overworld: OverworldState | None) -> HealerResult:
    """Restore missing HP for gold at the Highwood healer."""
    missing = max(0, knight.max_hp - knight.hp)
    if missing == 0:
        return HealerResult(
            False, HealerService.RESTORE_HP, 0, 0, 0, False, knight.gold, "already_full_hp"
        )
    cost = healer_hp_cost(knight)
    if knight.gold < cost:
        return HealerResult(
            False, HealerService.RESTORE_HP, 0, 0, 0, False, knight.gold, "insufficient_gold"
        )
    knight.gold -= cost
    knight.hp = knight.max_hp
    _sync_to_overworld(overworld, knight)
    return HealerResult(
        True, HealerService.RESTORE_HP, cost, missing, 0, False, knight.gold, "healed"
    )


def _healer_restore_life(knight: KnightState, overworld: OverworldState | None) -> HealerResult:
    """Restore one life up to ``MAX_LIVES`` for gold."""
    if knight.lives >= MAX_LIVES:
        return HealerResult(
            False, HealerService.RESTORE_LIFE, 0, 0, 0, False, knight.gold, "max_lives_reached"
        )
    if knight.gold < RESTORE_LIFE_COST:
        return HealerResult(
            False, HealerService.RESTORE_LIFE, 0, 0, 0, False, knight.gold, "insufficient_gold"
        )
    knight.gold -= RESTORE_LIFE_COST
    knight.lives += 1
    _sync_to_overworld(overworld, knight)
    return HealerResult(
        True, HealerService.RESTORE_LIFE, RESTORE_LIFE_COST, 0, 1, False, knight.gold, "life_added"
    )


def _healer_lift_curse(
    knight: KnightState, *, cursed: bool, overworld: OverworldState | None
) -> HealerResult:
    """Lift a curse and restore vitality and stamina at the Highwood healer."""
    if not cursed:
        return HealerResult(
            False, HealerService.LIFT_CURSE, 0, 0, 0, False, knight.gold, "not_cursed"
        )
    if knight.gold < LIFT_CURSE_COST:
        return HealerResult(
            False, HealerService.LIFT_CURSE, 0, 0, 0, False, knight.gold, "insufficient_gold"
        )
    knight.gold -= LIFT_CURSE_COST
    restored_hp = max(0, knight.max_hp - knight.hp)
    knight.hp = knight.max_hp
    knight.stamina = knight.max_stamina
    _sync_to_overworld(overworld, knight)
    return HealerResult(
        True,
        HealerService.LIFT_CURSE,
        LIFT_CURSE_COST,
        restored_hp,
        0,
        True,
        knight.gold,
        "curse_lifted",
    )


def play_tavern_dice(
    knight: KnightState,
    wager: int,
    prng: SimPrng,
    *,
    overworld: OverworldState | None = None,
) -> TavernDiceResult:
    """Play one deterministic 2d6 Highwood tavern dice round against the house."""
    if wager < MIN_TAVERN_WAGER or wager > MAX_TAVERN_WAGER:
        return TavernDiceResult(
            False,
            DiceOutcome.INVALID_WAGER,
            wager,
            (0, 0),
            (0, 0),
            0,
            0,
            0,
            knight.gold,
            "invalid_wager",
        )
    if knight.gold < wager:
        return TavernDiceResult(
            False,
            DiceOutcome.INSUFFICIENT_GOLD,
            wager,
            (0, 0),
            (0, 0),
            0,
            0,
            0,
            knight.gold,
            "insufficient_gold",
        )

    p_die1 = prng.rand_range(1, 7)
    p_die2 = prng.rand_range(1, 7)
    h_die1 = prng.rand_range(1, 7)
    h_die2 = prng.rand_range(1, 7)
    p_total = p_die1 + p_die2
    h_total = h_die1 + h_die2

    if p_total > h_total:
        outcome = DiceOutcome.WIN
        delta = wager
        reason = "player_won"
    elif p_total < h_total:
        outcome = DiceOutcome.LOSS
        delta = -wager
        reason = "house_won"
    else:
        outcome = DiceOutcome.PUSH
        delta = 0
        reason = "push"

    knight.gold += delta
    _sync_to_overworld(overworld, knight)
    return TavernDiceResult(
        True,
        outcome,
        wager,
        (p_die1, p_die2),
        (h_die1, h_die2),
        p_total,
        h_total,
        delta,
        knight.gold,
        reason,
    )


def _find_first_unclaimed_lair(overworld: OverworldState | None) -> int | None:
    """Return the lowest lair index ``0..3`` whose Moonstone is still unclaimed."""
    if overworld is None:
        return 0
    for idx in range(MOONSTONE_COUNT):
        if not (overworld.claimed_moonstones_mask & (1 << idx)):
            return idx
    return None


def _find_first_missing_key(knight: KnightState, overworld: OverworldState | None) -> int | None:
    """Return the lowest lair index ``0..3`` for which the knight lacks a key."""
    if overworld is not None:
        ow_knight = overworld.knights.get(knight.knight_id)
        if ow_knight is not None:
            for idx in range(MOONSTONE_COUNT):
                if not ow_knight.has_key(idx):
                    return idx
            return None
    for idx in range(MOONSTONE_COUNT):
        if not (knight.keys & (1 << idx)):
            return idx
    return None


def visit_mystic(
    knight: KnightState,
    service: MysticService,
    *,
    overworld: OverworldState | None = None,
) -> MysticResult:
    """Purchase arcane scrolls, blessings, hints, or Lair keys at Waterdeep."""
    if service == MysticService.FIREBALL_SCROLL:
        return _mystic_fireball_scroll(knight, overworld)
    if service == MysticService.BLESSING:
        return _mystic_blessing(knight, overworld)
    if service == MysticService.KEY_HINT:
        return _mystic_key_hint(knight, overworld)
    return _mystic_lair_key(knight, overworld)


def _mystic_fireball_scroll(knight: KnightState, overworld: OverworldState | None) -> MysticResult:
    """Grant fireball scroll secondary weapon and charges."""
    if knight.daggers + FIREBALL_SCROLL_CHARGES > MAX_DAGGERS:
        return MysticResult(
            False,
            MysticService.FIREBALL_SCROLL,
            0,
            None,
            None,
            knight.gold,
            "inventory_limit",
        )
    if knight.gold < FIREBALL_SCROLL_COST:
        return MysticResult(
            False,
            MysticService.FIREBALL_SCROLL,
            0,
            None,
            None,
            knight.gold,
            "insufficient_gold",
        )
    knight.gold -= FIREBALL_SCROLL_COST
    knight.secondary_weapon = SecondaryWeapon.FIREBALL_SCROLL
    knight.daggers += FIREBALL_SCROLL_CHARGES
    _sync_to_overworld(overworld, knight)
    return MysticResult(
        True,
        MysticService.FIREBALL_SCROLL,
        FIREBALL_SCROLL_COST,
        None,
        None,
        knight.gold,
        "scroll_granted",
    )


def _mystic_blessing(knight: KnightState, overworld: OverworldState | None) -> MysticResult:
    """Bestow a vitality and stamina blessing up to ``MAX_BLESSED_HP``."""
    if knight.max_hp >= MAX_BLESSED_HP and knight.max_stamina >= MAX_BLESSED_STAMINA:
        return MysticResult(
            False, MysticService.BLESSING, 0, None, None, knight.gold, "blessing_cap_reached"
        )
    if knight.gold < BLESSING_COST:
        return MysticResult(
            False, MysticService.BLESSING, 0, None, None, knight.gold, "insufficient_gold"
        )
    knight.gold -= BLESSING_COST
    knight.max_hp = min(MAX_BLESSED_HP, knight.max_hp + BLESSING_HP_BONUS)
    knight.hp = min(knight.max_hp, knight.hp + BLESSING_HP_BONUS)
    knight.max_stamina = min(MAX_BLESSED_STAMINA, knight.max_stamina + BLESSING_STAMINA_BONUS)
    knight.stamina = knight.max_stamina
    _sync_to_overworld(overworld, knight)
    return MysticResult(
        True, MysticService.BLESSING, BLESSING_COST, None, None, knight.gold, "blessed"
    )


def _mystic_key_hint(knight: KnightState, overworld: OverworldState | None) -> MysticResult:
    """Reveal the next unclaimed Lair index for gold."""
    target_lair = _find_first_unclaimed_lair(overworld)
    if target_lair is None:
        return MysticResult(
            False, MysticService.KEY_HINT, 0, None, None, knight.gold, "no_unclaimed_lairs"
        )
    if knight.gold < HINT_COST:
        return MysticResult(
            False, MysticService.KEY_HINT, 0, None, None, knight.gold, "insufficient_gold"
        )
    knight.gold -= HINT_COST
    _sync_to_overworld(overworld, knight)
    return MysticResult(
        True, MysticService.KEY_HINT, HINT_COST, target_lair, None, knight.gold, "hint_revealed"
    )


def _mystic_lair_key(knight: KnightState, overworld: OverworldState | None) -> MysticResult:
    """Sell the next missing Lair key to ``knight``."""
    key_index = _find_first_missing_key(knight, overworld)
    if key_index is None:
        return MysticResult(
            False, MysticService.LAIR_KEY, 0, None, None, knight.gold, "all_keys_owned"
        )
    if knight.gold < LAIR_KEY_COST:
        return MysticResult(
            False, MysticService.LAIR_KEY, 0, None, None, knight.gold, "insufficient_gold"
        )
    knight.gold -= LAIR_KEY_COST
    knight.keys |= 1 << key_index
    if overworld is not None and knight.knight_id in overworld.knights:
        grant_lair_key(overworld, knight.knight_id, key_index)
    _sync_to_overworld(overworld, knight)
    return MysticResult(
        True, MysticService.LAIR_KEY, LAIR_KEY_COST, None, key_index, knight.gold, "key_granted"
    )


def _get_stat_level(knight: KnightState, stat: TrainableStat) -> int:
    """Read the current integer level of ``stat`` from ``knight``."""
    if stat == TrainableStat.STRENGTH:
        return knight.strength
    if stat == TrainableStat.CONSTITUTION:
        return knight.constitution
    return knight.endurance


def _apply_stat_upgrade(knight: KnightState, stat: TrainableStat) -> int:
    """Increment ``stat`` and apply secondary HP/stamina gains; return new level."""
    if stat == TrainableStat.STRENGTH:
        knight.strength += 1
        return knight.strength
    if stat == TrainableStat.CONSTITUTION:
        knight.constitution += 1
        knight.max_hp += TRAINING_VITALITY_STEP
        knight.hp += TRAINING_VITALITY_STEP
        return knight.constitution
    knight.endurance += 1
    knight.max_stamina += TRAINING_VITALITY_STEP
    knight.stamina += TRAINING_VITALITY_STEP
    return knight.endurance


def train_knight_stat(
    knight: KnightState,
    stat: TrainableStat,
    *,
    overworld: OverworldState | None = None,
) -> TrainingResult:
    """Spend gold and XP at Waterdeep to raise strength, constitution, or endurance."""
    old_level = _get_stat_level(knight, stat)
    if old_level >= MAX_STAT_LEVEL:
        return TrainingResult(
            False,
            stat,
            old_level,
            old_level,
            0,
            0,
            knight.gold,
            knight.xp,
            "max_stat_reached",
        )

    gold_cost = old_level * TRAINING_GOLD_PER_LEVEL
    xp_cost = old_level * TRAINING_XP_PER_LEVEL
    if knight.gold < gold_cost:
        return TrainingResult(
            False,
            stat,
            old_level,
            old_level,
            0,
            0,
            knight.gold,
            knight.xp,
            "insufficient_gold",
        )
    if knight.xp < xp_cost:
        return TrainingResult(
            False,
            stat,
            old_level,
            old_level,
            0,
            0,
            knight.gold,
            knight.xp,
            "insufficient_xp",
        )

    knight.gold -= gold_cost
    knight.xp -= xp_cost
    new_level = _apply_stat_upgrade(knight, stat)
    _sync_to_overworld(overworld, knight)
    return TrainingResult(
        True,
        stat,
        old_level,
        new_level,
        gold_cost,
        xp_cost,
        knight.gold,
        knight.xp,
        "trained",
    )


def perform_stonehenge_ritual(
    overworld: OverworldState,
    knight_id: KnightId,
    *,
    knight_combat: KnightState | None = None,
    require_full_moon: bool = True,
) -> RitualResult:
    """Validate Moonstone altar ritual requirements at Stonehenge and award victory."""
    ow_knight = overworld.knights[knight_id]
    at_stonehenge = ow_knight.node_id == overworld.graph.stonehenge_node_id
    phase = overworld.moon_phase
    held = ow_knight.moonstone_count

    rejection = _validate_ritual_preconditions(
        overworld,
        at_stonehenge=at_stonehenge,
        alive=ow_knight.alive,
        has_all_moonstones=ow_knight.has_all_moonstones,
        require_full_moon=require_full_moon,
    )
    if rejection is not None:
        return RitualResult(False, False, knight_id, held, phase, at_stonehenge, rejection)

    overworld.winner = knight_id
    overworld.game_over = True
    ow_knight.xp += RITUAL_VICTORY_XP
    if knight_combat is not None:
        knight_combat.xp += RITUAL_VICTORY_XP
    return RitualResult(True, True, knight_id, held, phase, True, "ritual_complete")


def _validate_ritual_preconditions(
    overworld: OverworldState,
    *,
    at_stonehenge: bool,
    alive: bool,
    has_all_moonstones: bool,
    require_full_moon: bool,
) -> str | None:
    """Return a rejection reason if Stonehenge ritual preconditions are not met."""
    if overworld.game_over:
        return "game_already_over"
    if not alive:
        return "knight_not_alive"
    if not at_stonehenge:
        return "not_at_stonehenge"
    if not has_all_moonstones:
        return "missing_moonstones"
    if require_full_moon and not overworld.is_full_moon:
        return "wrong_moon_phase"
    return None


def distribute_lair_loot(
    knight: KnightState,
    lair_index: int,
    prng: SimPrng,
    *,
    overworld: OverworldState | None = None,
) -> LairLootResult:
    """Distribute deterministic gold, XP, key, Moonstone, and item loot for a Lair."""
    if not 0 <= lair_index < MOONSTONE_COUNT:
        msg = f"lair_index must be in 0..3, got {lair_index}"
        raise ValueError(msg)

    gold_awarded = (
        LAIR_BASE_GOLD + lair_index * LAIR_GOLD_PER_TIER + prng.rand_range(LAIR_GOLD_VARIANCE)
    )
    xp_awarded = LAIR_BASE_XP + lair_index * LAIR_XP_PER_TIER + prng.rand_range(LAIR_XP_VARIANCE)
    daggers_awarded = min(MAX_DAGGERS - knight.daggers, 2 + prng.rand_range(3))
    daggers_awarded = max(0, daggers_awarded)
    hp_restored = min(LAIR_HEAL_BONUS, max(0, knight.max_hp - knight.hp))

    knight.gold += gold_awarded
    knight.xp += xp_awarded
    knight.daggers += daggers_awarded
    knight.hp += hp_restored

    moonstone_claimed = _claim_lair_moonstone(knight, lair_index, overworld)
    key_granted = _grant_next_lair_key(knight, lair_index, overworld)
    _sync_to_overworld(overworld, knight)

    return LairLootResult(
        lair_index=lair_index,
        knight_id=knight.knight_id,
        gold_awarded=gold_awarded,
        xp_awarded=xp_awarded,
        moonstone_claimed=moonstone_claimed,
        key_granted=key_granted,
        daggers_awarded=daggers_awarded,
        hp_restored=hp_restored,
    )


def _claim_lair_moonstone(
    knight: KnightState, lair_index: int, overworld: OverworldState | None
) -> bool:
    """Claim the Moonstone for ``lair_index`` if unclaimed."""
    bit = 1 << lair_index
    if overworld is not None:
        if overworld.claimed_moonstones_mask & bit:
            return False
        collect_moonstone(overworld, knight.knight_id, lair_index)
        knight.moonstones |= bit
        return True
    if knight.moonstones & bit:
        return False
    knight.moonstones |= bit
    return True


def _grant_next_lair_key(
    knight: KnightState, lair_index: int, overworld: OverworldState | None
) -> int | None:
    """Grant the next unowned Lair key following ``lair_index`` if available."""
    next_index = (lair_index + 1) % MOONSTONE_COUNT
    bit = 1 << next_index
    if knight.keys & bit:
        return None
    knight.keys |= bit
    if overworld is not None and knight.knight_id in overworld.knights:
        grant_lair_key(overworld, knight.knight_id, next_index)
    return next_index
