"""Wizard outcome identity and unchanged mutation/draw semantics (§12.12/35)."""

from dataclasses import replace
from unittest.mock import patch

import pytest

from harddaysengine.sim.actor import Actor
from harddaysengine.sim.inventory import Inventory
from harddaysengine.sim.rewards import GiftKind, GiftResult, GiftState, pick_stat_row, wizard_gift
from harddaysengine.sim.rng import Rng
from tests.unit.turn_setup import GIFTS

RULES = replace(GIFTS, bands=((30, 0), (60, 4), (100, 6)), limits=(25, 65, 95))


@pytest.mark.parametrize(("cycle", "message"), [(0, 1), (1, 2), (2, 3), (3, 0)])
def test_item_outcome_uses_updated_shared_cycle_and_final_slot(cycle: int, message: int) -> None:
    state = GiftState(last=0, item_cycle=cycle, gold_cycle=2)
    inventory = Inventory()
    actor = Actor()
    with patch.object(Rng, "draw", side_effect=[0, 0, 40]) as draw:
        result = wizard_gift(actor, inventory, Rng(1), RULES, state)
    assert result == GiftResult(GiftKind.ITEM, message, item=4)
    assert actor.sword == 25 and inventory.byte(4) == 1
    assert (state.last, state.item_cycle, state.gold_cycle) == (4, message, 2)
    assert draw.call_count == 3


@pytest.mark.parametrize(("cycle", "message"), [(0, 1), (1, 2), (2, 0)])
def test_gold_outcome_keeps_uncapped_wrapped_amount(cycle: int, message: int) -> None:
    state = GiftState(last=6, gold_cycle=cycle, item_cycle=3)
    actor = Actor(gold=65530)
    with patch.object(Rng, "draw", side_effect=[90, 31]) as draw:
        result = wizard_gift(actor, Inventory(), Rng(1), GIFTS, state)
    assert result == GiftResult(GiftKind.GOLD, message, amount=31)
    assert actor.gold == 25
    assert (state.last, state.item_cycle, state.gold_cycle) == (6, 3, message)
    assert draw.call_count == 2


@pytest.mark.parametrize("row", range(9))
def test_stat_message_retains_selected_row_including_duplicate_offsets(row: int) -> None:
    rules = replace(RULES, stats=(70,) * 9)
    actor = Actor(strength=1)
    state = GiftState(last=6, item_cycle=2, gold_cycle=1)
    with patch.object(Rng, "draw", side_effect=[65, row]) as draw:
        result = wizard_gift(actor, Inventory(), Rng(1), rules, state)
    assert result == GiftResult(GiftKind.STAT, row, stat=70)
    assert actor.strength == 2 and draw.call_count == 2
    assert state == GiftState(last=6, item_cycle=2, gold_cycle=1)


def test_stat_rejection_returns_final_row_without_an_extra_draw() -> None:
    rules = replace(GIFTS, stats=(70, 71, 72, 70, 71, 72, 72, 71, 72))
    actor = Actor(strength=5, constitution=2)
    with patch.object(Rng, "draw", side_effect=[0, 13]) as draw:
        assert pick_stat_row(actor, Rng(1), rules) == (6, 72)
    assert draw.call_count == 2


def test_saturated_stat_restarts_reward_and_curse_keeps_cycles() -> None:
    actor = Actor(strength=5, constitution=5, endurance=5, recency=0)
    state = GiftState(last=4, item_cycle=3, gold_cycle=2)
    with patch.object(Rng, "draw", side_effect=[50, 96]) as draw:
        result = wizard_gift(actor, Inventory(), Rng(1), GIFTS, state)
    assert result == GiftResult(GiftKind.CURSE)
    assert actor.frog_days == 3 and draw.call_count == 2
    assert state == GiftState(last=4, item_cycle=3, gold_cycle=2)


def test_recency_wrap_and_ai_curse_rejection_return_final_reward() -> None:
    actor = Actor(recency=255)
    with patch.object(Rng, "draw", side_effect=[0, 91, 0]) as draw:
        result = wizard_gift(actor, Inventory(), Rng(1), GIFTS, GiftState())
    assert result == GiftResult(GiftKind.GOLD, 1, amount=10)
    assert actor.frog_days == 0 and actor.gold == 10 and draw.call_count == 3
