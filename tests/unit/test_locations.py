"""Unit tests for pure-integer sacred places, towns, shops, and Lair loot."""

from __future__ import annotations

import pytest

from pymoonstone.sim.knight_combat import KnightState, SecondaryWeapon
from pymoonstone.sim.locations import (
    BLESSING_COST,
    DAGGER_UNIT_COST,
    FIREBALL_SCROLL_CHARGES,
    FIREBALL_SCROLL_COST,
    HEALING_POTION_COST,
    HEALING_POTION_HP,
    HINT_COST,
    LAIR_BASE_GOLD,
    LAIR_BASE_XP,
    LAIR_KEY_COST,
    LIFT_CURSE_COST,
    MAX_ARMOUR_LEVEL,
    MAX_BLESSED_HP,
    MAX_BLESSED_STAMINA,
    MAX_DAGGERS,
    MAX_LIVES,
    MAX_SHIELD_LEVEL,
    MAX_STAT_LEVEL,
    MAX_SWORD_TIER,
    MAX_TAVERN_WAGER,
    RESTORE_LIFE_COST,
    RITUAL_VICTORY_XP,
    STAMINA_TONIC_COST,
    TRAINING_GOLD_PER_LEVEL,
    TRAINING_VITALITY_STEP,
    TRAINING_XP_PER_LEVEL,
    DiceOutcome,
    HealerService,
    MerchantItemKind,
    MysticService,
    TrainableStat,
    buy_merchant_item,
    distribute_lair_loot,
    healer_hp_cost,
    merchant_buy_price,
    perform_stonehenge_ritual,
    play_tavern_dice,
    sell_merchant_item,
    train_knight_stat,
    visit_healer,
    visit_mystic,
)
from pymoonstone.sim.overworld import (
    MoonPhase,
    collect_moonstone,
    create_campaign,
)
from pymoonstone.sim.prng import SimPrng
from pymoonstone.sim.types import KnightId


def _make_knight(*, gold: int = 500, xp: int = 200) -> KnightState:
    return KnightState(knight_id=KnightId.RED, gold=gold, xp=xp)


def test_highwood_merchant_buy_and_sell_equipment() -> None:
    ow = create_campaign((KnightId.RED,))
    knight = _make_knight(gold=500)

    sword_res = buy_merchant_item(knight, MerchantItemKind.SWORD_UPGRADE, overworld=ow)
    assert sword_res.accepted
    assert sword_res.gold_delta == -100
    assert knight.sword_tier == 2
    assert knight.gold == 400
    assert ow.knights[KnightId.RED].gold == 400

    armour_res = buy_merchant_item(knight, MerchantItemKind.ARMOUR_UPGRADE)
    assert armour_res.accepted
    assert armour_res.gold_delta == -80
    assert knight.armour == 1

    shield_res = buy_merchant_item(knight, MerchantItemKind.SHIELD_UPGRADE)
    assert shield_res.accepted
    assert shield_res.gold_delta == -60
    assert knight.shield == 1

    knives_res = buy_merchant_item(knight, MerchantItemKind.THROWING_KNIFE, quantity=4)
    assert knives_res.accepted
    assert knives_res.gold_delta == -(4 * DAGGER_UNIT_COST)
    assert knight.daggers == 9

    sell_knives = sell_merchant_item(
        knight, MerchantItemKind.THROWING_KNIFE, quantity=2, overworld=ow
    )
    assert sell_knives.accepted
    assert sell_knives.gold_delta == (2 * DAGGER_UNIT_COST) // 2
    assert knight.daggers == 7
    assert ow.knights[KnightId.RED].gold == knight.gold

    sell_sword = sell_merchant_item(knight, MerchantItemKind.SWORD_UPGRADE)
    assert sell_sword.accepted
    assert sell_sword.gold_delta == 50
    assert knight.sword_tier == 1


def test_highwood_merchant_rejections_and_consumables() -> None:
    knight = _make_knight(gold=10)
    poor = buy_merchant_item(knight, MerchantItemKind.SWORD_UPGRADE)
    assert not poor.accepted
    assert poor.reason == "insufficient_gold"

    bad_qty = buy_merchant_item(knight, MerchantItemKind.THROWING_KNIFE, quantity=0)
    assert not bad_qty.accepted
    assert bad_qty.reason == "invalid_quantity"
    assert merchant_buy_price(knight, MerchantItemKind.THROWING_KNIFE, 0) == 0

    knight.gold = 2000
    knight.sword_tier = MAX_SWORD_TIER
    assert buy_merchant_item(knight, MerchantItemKind.SWORD_UPGRADE).reason == "inventory_limit"
    knight.armour = MAX_ARMOUR_LEVEL
    assert buy_merchant_item(knight, MerchantItemKind.ARMOUR_UPGRADE).reason == "inventory_limit"
    knight.shield = MAX_SHIELD_LEVEL
    assert buy_merchant_item(knight, MerchantItemKind.SHIELD_UPGRADE).reason == "inventory_limit"
    knight.daggers = MAX_DAGGERS
    assert buy_merchant_item(knight, MerchantItemKind.THROWING_KNIFE).reason == "inventory_limit"

    assert buy_merchant_item(knight, MerchantItemKind.HEALING_POTION).reason == "already_full_hp"
    knight.hp = 40
    pot = buy_merchant_item(knight, MerchantItemKind.HEALING_POTION)
    assert pot.accepted
    assert pot.gold_delta == -HEALING_POTION_COST
    assert knight.hp == 40 + HEALING_POTION_HP

    assert (
        buy_merchant_item(knight, MerchantItemKind.STAMINA_TONIC).reason == "already_full_stamina"
    )
    knight.stamina = 30
    tonic = buy_merchant_item(knight, MerchantItemKind.STAMINA_TONIC)
    assert tonic.accepted
    assert tonic.gold_delta == -STAMINA_TONIC_COST
    assert knight.stamina == 70

    assert sell_merchant_item(knight, MerchantItemKind.HEALING_POTION).reason == "not_sellable"
    knight.sword_tier = 1
    assert sell_merchant_item(knight, MerchantItemKind.SWORD_UPGRADE).reason == "nothing_to_sell"


def test_highwood_healer_services() -> None:
    knight = _make_knight(gold=300)
    assert visit_healer(knight, HealerService.RESTORE_HP).reason == "already_full_hp"

    knight.hp = 60
    cost = healer_hp_cost(knight)
    assert cost == 20
    res = visit_healer(knight, HealerService.RESTORE_HP)
    assert res.accepted
    assert res.hp_restored == 40
    assert res.gold_spent == 20
    assert knight.hp == knight.max_hp

    life_res = visit_healer(knight, HealerService.RESTORE_LIFE)
    assert life_res.accepted
    assert life_res.lives_restored == 1
    assert life_res.gold_spent == RESTORE_LIFE_COST
    assert knight.lives == 4

    knight.lives = MAX_LIVES
    assert visit_healer(knight, HealerService.RESTORE_LIFE).reason == "max_lives_reached"

    assert visit_healer(knight, HealerService.LIFT_CURSE, cursed=False).reason == "not_cursed"
    knight.hp = 50
    knight.stamina = 20
    curse_res = visit_healer(knight, HealerService.LIFT_CURSE, cursed=True)
    assert curse_res.accepted
    assert curse_res.curse_lifted
    assert curse_res.gold_spent == LIFT_CURSE_COST
    assert knight.hp == knight.max_hp
    assert knight.stamina == knight.max_stamina


def test_highwood_tavern_dice_determinism_and_outcomes() -> None:
    knight = _make_knight(gold=100)
    prng = SimPrng(42)

    bad_low = play_tavern_dice(knight, 0, prng)
    assert not bad_low.accepted
    assert bad_low.outcome == DiceOutcome.INVALID_WAGER

    bad_high = play_tavern_dice(knight, MAX_TAVERN_WAGER + 1, prng)
    assert not bad_high.accepted
    assert bad_high.outcome == DiceOutcome.INVALID_WAGER

    poor = play_tavern_dice(knight, 150, prng)
    assert not poor.accepted
    assert poor.outcome == DiceOutcome.INSUFFICIENT_GOLD

    seen: set[DiceOutcome] = set()
    for seed in range(1, 200):
        k_a = _make_knight(gold=100)
        k_b = _make_knight(gold=100)
        r_a = play_tavern_dice(k_a, 25, SimPrng(seed))
        r_b = play_tavern_dice(k_b, 25, SimPrng(seed))
        assert r_a == r_b
        assert r_a.accepted
        assert r_a.player_total == sum(r_a.player_dice)
        assert r_a.house_total == sum(r_a.house_dice)
        seen.add(r_a.outcome)
        if r_a.outcome == DiceOutcome.WIN:
            assert r_a.gold_delta == 25
            assert k_a.gold == 125
        elif r_a.outcome == DiceOutcome.LOSS:
            assert r_a.gold_delta == -25
            assert k_a.gold == 75
        else:
            assert r_a.outcome == DiceOutcome.PUSH
            assert r_a.gold_delta == 0
            assert k_a.gold == 100

    assert {DiceOutcome.WIN, DiceOutcome.LOSS, DiceOutcome.PUSH} <= seen


def test_waterdeep_mystic_services() -> None:
    ow = create_campaign((KnightId.RED,))
    knight = _make_knight(gold=400)

    scroll = visit_mystic(knight, MysticService.FIREBALL_SCROLL, overworld=ow)
    assert scroll.accepted
    assert scroll.gold_spent == FIREBALL_SCROLL_COST
    assert knight.secondary_weapon == SecondaryWeapon.FIREBALL_SCROLL
    assert knight.daggers == 5 + FIREBALL_SCROLL_CHARGES

    blessing = visit_mystic(knight, MysticService.BLESSING, overworld=ow)
    assert blessing.accepted
    assert blessing.gold_spent == BLESSING_COST
    assert knight.max_hp == 110
    assert knight.max_stamina == 110

    hint = visit_mystic(knight, MysticService.KEY_HINT, overworld=ow)
    assert hint.accepted
    assert hint.gold_spent == HINT_COST
    assert hint.hinted_lair_index == 0

    key_res = visit_mystic(knight, MysticService.LAIR_KEY, overworld=ow)
    assert key_res.accepted
    assert key_res.gold_spent == LAIR_KEY_COST
    assert key_res.granted_key_index == 0
    assert ow.knights[KnightId.RED].has_key(0)

    knight.gold = 500
    knight.max_hp = MAX_BLESSED_HP
    knight.max_stamina = MAX_BLESSED_STAMINA
    assert visit_mystic(knight, MysticService.BLESSING).reason == "blessing_cap_reached"


def test_waterdeep_stat_training() -> None:
    knight = _make_knight(gold=500, xp=200)

    str_res = train_knight_stat(knight, TrainableStat.STRENGTH)
    assert str_res.accepted
    assert str_res.old_level == 1
    assert str_res.new_level == 2
    assert str_res.gold_spent == TRAINING_GOLD_PER_LEVEL
    assert str_res.xp_spent == TRAINING_XP_PER_LEVEL
    assert knight.strength == 2

    con_res = train_knight_stat(knight, TrainableStat.CONSTITUTION)
    assert con_res.accepted
    assert knight.constitution == 2
    assert knight.max_hp == 100 + TRAINING_VITALITY_STEP

    end_res = train_knight_stat(knight, TrainableStat.ENDURANCE)
    assert end_res.accepted
    assert knight.endurance == 2
    assert knight.max_stamina == 100 + TRAINING_VITALITY_STEP

    knight.xp = 0
    assert train_knight_stat(knight, TrainableStat.STRENGTH).reason == "insufficient_xp"

    knight.xp = 500
    knight.gold = 0
    assert train_knight_stat(knight, TrainableStat.STRENGTH).reason == "insufficient_gold"

    knight.gold = 500
    knight.strength = MAX_STAT_LEVEL
    assert train_knight_stat(knight, TrainableStat.STRENGTH).reason == "max_stat_reached"


def test_stonehenge_ritual_validation_and_victory() -> None:
    ow = create_campaign((KnightId.RED,))
    knight = _make_knight()

    not_there = perform_stonehenge_ritual(ow, KnightId.RED)
    assert not not_there.accepted
    assert not_there.reason == "not_at_stonehenge"

    ow.knights[KnightId.RED].node_id = ow.graph.stonehenge_node_id
    missing_ms = perform_stonehenge_ritual(ow, KnightId.RED)
    assert not missing_ms.accepted
    assert missing_ms.reason == "missing_moonstones"

    for lair_idx in range(4):
        collect_moonstone(ow, KnightId.RED, lair_idx)

    wrong_phase = perform_stonehenge_ritual(ow, KnightId.RED)
    assert not wrong_phase.accepted
    assert wrong_phase.reason == "wrong_moon_phase"

    ow.day = 15
    assert ow.moon_phase == MoonPhase.FULL_MOON
    victory = perform_stonehenge_ritual(ow, KnightId.RED, knight_combat=knight)
    assert victory.accepted
    assert victory.victory
    assert victory.reason == "ritual_complete"
    assert ow.winner == KnightId.RED
    assert ow.game_over
    assert knight.xp == 200 + RITUAL_VICTORY_XP

    already_over = perform_stonehenge_ritual(ow, KnightId.RED)
    assert not already_over.accepted
    assert already_over.reason == "game_already_over"


def test_lair_loot_distribution_is_deterministic() -> None:
    ow_a = create_campaign((KnightId.RED,), seed=99)
    ow_b = create_campaign((KnightId.RED,), seed=99)
    k_a = KnightState(knight_id=KnightId.RED, hp=70)
    k_b = KnightState(knight_id=KnightId.RED, hp=70)

    loot_a = distribute_lair_loot(k_a, 0, SimPrng(777), overworld=ow_a)
    loot_b = distribute_lair_loot(k_b, 0, SimPrng(777), overworld=ow_b)
    assert loot_a == loot_b
    assert loot_a.gold_awarded >= LAIR_BASE_GOLD
    assert loot_a.xp_awarded >= LAIR_BASE_XP
    assert loot_a.moonstone_claimed
    assert loot_a.key_granted == 1
    assert ow_a.knights[KnightId.RED].has_moonstone(0)
    assert ow_a.knights[KnightId.RED].has_key(1)
    assert k_a.hp == 90

    repeat = distribute_lair_loot(k_a, 0, SimPrng(777), overworld=ow_a)
    assert not repeat.moonstone_claimed
    assert repeat.key_granted is None

    with pytest.raises(ValueError, match="lair_index"):
        distribute_lair_loot(k_a, 4, SimPrng(1))
