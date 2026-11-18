"""Original day gifts and dragon loot settlement with fabricated tables (§11.19)."""

from dataclasses import replace
from unittest.mock import patch

import pytest

from harddaysengine.sim.actor import Actor
from harddaysengine.sim.inventory import Inventory
from harddaysengine.sim.rewards import (
    GiftState,
    item_gift,
    pick_stat,
    recalc_hp,
    transfer_loot,
    wizard_gift,
)
from harddaysengine.sim.rng import Rng
from tests.unit.map_setup import make_world
from tests.unit.turn_setup import GIFTS


@pytest.mark.parametrize(
    ("rolls", "stat", "hp", "gold", "frog"),
    [
        ([40, 1], 3, 30, 0, 0),
        ([80, 31], 2, 20, 31, 0),
        ([99], 2, 20, 0, 3),
    ],
)
def test_wizard_stat_gold_and_curse_paths(
    rolls: list[int], stat: int, hp: int, gold: int, frog: int
) -> None:
    actor = Actor(strength=2, constitution=2, endurance=2, hp=20, hp_max=30, armour=27)
    with patch.object(Rng, "draw", side_effect=rolls) as draw:
        wizard_gift(actor, Inventory(), Rng(1), GIFTS, GiftState())
    assert actor.constitution == stat
    assert (actor.hp, actor.gold, actor.frog_days) == (hp, gold, frog)
    assert draw.call_count == len(rolls)


def test_computer_rejects_curse_after_wrapped_recency_roll() -> None:
    actor = Actor(recency=255)
    with patch.object(Rng, "draw", side_effect=[0, 81, 31]) as draw:
        wizard_gift(actor, Inventory(), Rng(1), GIFTS, GiftState())
    assert actor.gold == 31 and actor.frog_days == 0 and draw.call_count == 3


def test_item_rerolls_previous_gift_then_adds_protection_before_recalc() -> None:
    actor = Actor(constitution=1, armour=27, hp=10)
    inventory, state = Inventory(), GiftState(last=0)
    with patch.object(Rng, "draw", side_effect=[50, 20]) as draw:
        item_gift(actor, inventory, Rng(1), GIFTS, state)
    assert state.last == 6 and inventory.byte(6) == 1
    assert actor.hp == 30 and actor.hp_max == 40 and draw.call_count == 2


def test_refused_duplicate_sword_updates_shared_last_gift_memory() -> None:
    rules = replace(GIFTS, bands=((25, 4), (75, 6), (100, 0)))
    actor, inventory, state = Actor(constitution=1), Inventory(), GiftState()
    inventory.counts[2] = 1
    with patch.object(Rng, "draw", side_effect=[10, 10, 60]) as draw:
        item_gift(actor, inventory, Rng(1), rules, state)
    assert draw.call_count == 3 and state.last == 6 and inventory.byte(4) == 1


def test_stat_pick_cap_exact_five_rejection_and_saturated_no_draw() -> None:
    actor = Actor(strength=5, constitution=2, endurance=3)
    with patch.object(Rng, "draw", side_effect=[0, 1]) as draw:
        assert pick_stat(actor, Rng(1), GIFTS) == 71
    assert draw.call_count == 2
    actor.constitution = actor.endurance = 5
    with patch.object(Rng, "draw") as draw:
        assert pick_stat(actor, Rng(1), GIFTS) == 0
    draw.assert_not_called()


def test_reroll_exhaustion_is_explicit() -> None:
    rules = replace(GIFTS, bands=((100, 0),))
    with pytest.raises(ValueError, match="did not terminate"):
        item_gift(Actor(), Inventory(), Rng(0), rules, GiftState(last=0))


def test_dragon_gold_first_and_empty_inventory_halves_twice() -> None:
    world = make_world()
    winner, loser = world.dragon, world.current_actor
    winner.type, winner.inventory, loser.gold = 20, 5, 9
    transfer_loot(winner, loser, world.ordered_actors(), world.inventories, GIFTS)
    assert winner.gold == 6 and loser.gold == 2


def test_surviving_loser_loses_first_owned_slot_in_loaded_order() -> None:
    world = make_world()
    winner, loser = world.dragon, world.current_actor
    winner.inventory = 5
    world.inventories[1].counts[1] = 3
    world.inventories[1].counts[11] = 5
    transfer_loot(winner, loser, world.ordered_actors(), world.inventories, GIFTS)
    assert world.inventories[1].counts[1] == 2
    assert world.inventories[5].counts[1] == 1
    assert world.inventories[1].byte(22) == 5


def test_zero_lives_transfers_all_counts_masks_wrap_and_preserves_sword_typo() -> None:
    world = make_world()
    winner, loser = world.dragon, world.current_actor
    winner.inventory, loser.lives = 5, 0
    source, target = world.inventories[1], world.inventories[5]
    source.counts[0], target.counts[0] = 2, 255
    source.counts[2], source.counts[10], target.counts[10] = 1, 3, 4
    transfer_loot(winner, loser, world.ordered_actors(), world.inventories, GIFTS)
    assert not any(source.counts)
    assert target.counts[0] == 1 and target.counts[10] == 7
    assert loser.sword == 25


def test_derived_hp_wraps_caps_signed_and_forces_sharp_sword() -> None:
    actor, inventory = Actor(constitution=3, armour=30, hp=200), Inventory()
    inventory.counts[2], inventory.counts[3] = 1, 2
    recalc_hp(actor, inventory)
    assert actor.hp == actor.hp_max == 110 and actor.sword == 25
