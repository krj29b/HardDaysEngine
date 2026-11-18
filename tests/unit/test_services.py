"""Synthetic service rules, signed boundaries and random draw order (§12.30)."""

from dataclasses import asdict

import pytest

from harddaysengine.sim.actor import Actor
from harddaysengine.sim.inventory import Inventory
from harddaysengine.sim.rng import Rng
from harddaysengine.sim.service_types import DiceRules, HealerRules, MysticRules
from harddaysengine.sim.services import Donation, dice_bet, dice_roll, heal, mystic_gamble
from harddaysengine.sim.turn_rules import GiftRules


class _Draws(Rng):
    def __init__(self, *values: int) -> None:
        super().__init__(0)
        self.values = iter(values)
        self.count = 0

    def draw(self) -> int:
        self.count += 1
        return next(self.values, 7)


HEALER = HealerRules(7, 11, 4, 6)
MYSTIC = MysticRules(((8, 17), (25, -13)), 60, 6, 2, 1)
DICE = DiceRules(7, 5, ((0, 2, 5, 9), (1, 1, 1, 3)))


def _gamble(actor: Actor, stat: int, donation: int, roll: int) -> tuple[str, int, int]:
    gifts = GiftRules((), (stat,) * 9, (0, 0, 0), ())
    rng = _Draws(0, roll) if stat else _Draws(roll)
    result = mystic_gamble(actor, Inventory(), donation, rng, gifts, MYSTIC)
    return result.outcome, result.stat, rng.count


def test_donation_commits_only_on_accept_and_keeps_the_donation() -> None:
    actor = Actor(gold=2)
    state = Donation(actor.gold)
    assert not state.move(more=False)
    assert state.move(more=True) and state.move(more=True)
    assert not state.move(more=True)
    assert actor.gold == 2
    assert state.move(more=False)
    state.close(actor, accept=True)
    assert (actor.gold, state.purse, state.amount) == (1, 1, 1)


def test_donation_leave_never_pays_and_word_transfers_wrap() -> None:
    actor = Actor(gold=31)
    state = Donation(0, 65535)
    assert state.move(more=False)
    state.close(actor, accept=False)
    assert (actor.gold, state.purse, state.amount) == (31, 0, 0)
    state = Donation(1, 65535)
    assert state.move(more=True)
    assert (state.purse, state.amount) == (0, 0)


def test_healer_curse_hp_and_life_order_uses_decoded_prices() -> None:
    actor = Actor(hp=1, hp_max=40, life_loss=1, frog_days=2, lives=2)
    assert heal(actor, 21, HEALER) == (3, 7)
    assert (actor.hp, actor.life_loss, actor.frog_days, actor.lives) == (40, 0, 2, 3)


def test_healer_life_can_overdraw_and_exact_cap_can_clear_curse_without_debit() -> None:
    actor = Actor(hp=40, hp_max=40, lives=3)
    assert heal(actor, 7, HEALER) == (65532, 4)
    assert actor.lives == 4
    actor.life_loss = 1
    assert heal(actor, 7, HEALER) == (7, 1)


@pytest.mark.parametrize("amount", (0, 6, 32768, 65535))
def test_healer_small_or_signed_negative_donation_changes_nothing(amount: int) -> None:
    actor = Actor(hp=1, hp_max=40, lives=2, life_loss=1)
    before = asdict(actor)
    assert heal(actor, amount, HEALER) == (amount, 0)
    assert asdict(actor) == before


def test_healer_above_cap_buys_and_byte_wraps() -> None:
    actor = Actor(hp=1, hp_max=1, lives=255)
    assert heal(actor, 7, HEALER) == (65532, 4)
    assert actor.lives == 0
    with pytest.raises(ValueError, match="progress"):
        heal(actor, 7, HealerRules(0, 1, 4, 0))


@pytest.mark.parametrize("stat", (70, 71, 72))
def test_mystic_raises_selected_stat_before_recalculation(stat: int) -> None:
    actor = Actor(strength=2, constitution=2, endurance=2, hp=10, hp_max=30)
    assert _gamble(actor, stat, 8, 43) == ("raise", stat, 2)
    assert actor.read(stat, 1) == 3
    assert actor.hp == (16 if stat == 71 else 10)
    assert actor.hp_max == (40 if stat == 71 else 30)
    assert actor.derived_endurance == (10 if stat == 72 else 8)


@pytest.mark.parametrize("donation,outcome", ((8, "raise"), (9, "lower"), (26, "lower")))
def test_mystic_bonus_bounds_and_beyond_table(donation: int, outcome: str) -> None:
    actor = Actor(strength=2, constitution=2, endurance=2, hp=15)
    assert _gamble(actor, 70, donation, 50) == (outcome, 70, 2)


def test_mystic_constitution_loss_clamps_hp_and_floor_leaves_actor_untouched() -> None:
    actor = Actor(strength=2, constitution=2, endurance=2, hp=4)
    assert _gamble(actor, 71, 9, 0) == ("lower", 71, 2)
    assert (actor.hp, actor.hp_max, actor.constitution) == (2, 20, 1)
    before = asdict(actor)
    assert _gamble(actor, 71, 9, 0) == ("unchanged", 71, 2)
    assert asdict(actor) == before


def test_mystic_no_pick_still_draws_but_never_writes_stale_record_memory() -> None:
    actor = Actor(active=0x12345678, strength=5, constitution=5, endurance=5, hp=90)
    assert _gamble(actor, 0, 8, 80) == ("nothing", 0, 1)
    assert actor.hp == 90  # winning no-stat path does not recalculate
    assert _gamble(actor, 0, 9, 0) == ("unchanged", 0, 1)
    assert actor.active == 0x12345678 and actor.hp == 60


def test_dice_affordability_is_signed_and_roll_preserves_display_order() -> None:
    actor = Actor(gold=3)
    assert not dice_bet(actor, 4)
    assert actor.gold == 3
    assert dice_bet(actor, 2)
    rng = _Draws(7, 5, 6, 0, 2)
    result = dice_roll(actor, 2, rng, DICE)
    assert (result.dice, result.multiplier, result.winnings) == ((5, 0, 2), 9, 18)
    assert actor.gold == 19 and rng.count == 5
    actor.gold = 32768
    assert not dice_bet(actor, 1)


def test_dice_unlisted_triple_loses_and_payout_wraps_gold() -> None:
    actor = Actor(gold=65535)
    assert dice_roll(actor, 20, _Draws(0, 0, 0), DICE).winnings == 0
    result = dice_roll(actor, 65535, _Draws(2, 5, 0), DICE)
    assert result.winnings == 589815
    assert actor.gold == 65526


def test_dice_rejection_is_bounded() -> None:
    with pytest.raises(ValueError, match="did not terminate"):
        dice_roll(Actor(), 1, _Draws(), DICE)
