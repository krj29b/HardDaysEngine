"""Deterministic town service transactions (ARCHITECTURE.md §12.19/21/30)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from harddaysengine.sim.m68k import s16, u16
from harddaysengine.sim.rewards import pick_stat, recalc_endurance, recalc_hp
from harddaysengine.sim.service_types import DiceResult, MysticResult

if TYPE_CHECKING:
    from harddaysengine.sim.actor import Actor
    from harddaysengine.sim.inventory import Inventory
    from harddaysengine.sim.rng import Rng
    from harddaysengine.sim.service_types import DiceRules, HealerRules, MysticRules
    from harddaysengine.sim.turn_rules import GiftRules


@dataclass(slots=True)
class Donation:
    """The two word cells, independent of actor gold until accept (§12.30)."""

    purse: int
    amount: int = 0

    def move(self, *, more: bool) -> bool:
        """Move one gold; the screen owns repeat timing."""
        source = self.purse if more else self.amount
        if u16(source) == 0:
            return False
        change = 1 if more else -1
        self.purse = u16(self.purse - change)
        self.amount = u16(self.amount + change)
        return True

    def close(self, actor: Actor, *, accept: bool) -> None:
        if accept:
            actor.gold = u16(self.purse)
        else:
            self.purse = self.amount = 0


def heal(actor: Actor, donation: int, rules: HealerRules) -> tuple[int, int]:
    """Return remaining donation and curse/HP/life change bits (§12.19/30)."""
    if min(s16(rules.heal_price), s16(rules.life_price)) <= 0:
        raise ValueError("healer prices must make progress")
    amount, changed = u16(donation), 0
    while s16(amount) >= s16(rules.heal_price):
        if actor.life_loss:
            actor.life_loss = 0
            changed |= 1
        if actor.hp != actor.hp_max:
            actor.hp = actor.hp_max
            amount = u16(amount - rules.heal_price)
            changed |= 2
        elif actor.lives != rules.life_cap:
            actor.lives = (actor.lives + 1) & 255
            amount = u16(amount - rules.life_price)
            changed |= 4
        else:
            break
    return amount, changed


def _mystic_change(
    actor: Actor, inventory: Inventory, stat: int, rules: MysticRules, *, won: bool
) -> MysticResult:
    if won:
        if not stat:
            return MysticResult("nothing", 0)
        actor.write(stat, 1, actor.read(stat, 1) + 1)
        if stat == 71:
            actor.hp = u16(actor.hp + rules.hp_step)
        recalc_hp(actor, inventory)
        recalc_endurance(actor)
        return MysticResult("raise", stat)
    if stat and actor.read(stat, 1) == rules.stat_floor:
        return MysticResult("unchanged", stat)
    if stat:
        actor.write(stat, 1, actor.read(stat, 1) - 1)
        if stat == 71:
            hp = u16(actor.hp - rules.hp_step)
            actor.hp = hp if s16(hp) > 0 else rules.hp_floor
    recalc_endurance(actor)
    recalc_hp(actor, inventory)
    return MysticResult("lower" if stat else "unchanged", stat)


def mystic_gamble(
    actor: Actor,
    inventory: Inventory,
    donation: int,
    rng: Rng,
    gifts: GiftRules,
    rules: MysticRules,
) -> MysticResult:
    """Pick before drawing the wrapped signed gamble; above-table bonus is zero (§12.30)."""
    stat = pick_stat(actor, rng, gifts)
    bonus = next((bonus for bound, bonus in rules.bands if s16(donation) <= bound), 0)
    won = s16(rng.percent() + bonus) >= rules.win_at
    return _mystic_change(actor, inventory, stat, rules, won=won)


def dice_bet(actor: Actor, bet: int) -> bool:
    """Signed-word affordability followed by a word debit (§12.21)."""
    if s16(bet) > s16(actor.gold):
        return False
    actor.gold = u16(actor.gold - bet)
    return True


def _die(rng: Rng, rules: DiceRules) -> int:
    for _ in range(4096):
        face = rng.draw() & rules.mask
        if face <= rules.face_max:
            return face
    raise ValueError("dice rejection did not terminate")


def dice_roll(actor: Actor, bet: int, rng: Rng, rules: DiceRules) -> DiceResult:
    """Resolve a previously debited stake, keeping display order and unsigned product (§12.30)."""
    dice = (_die(rng, rules), _die(rng, rules), _die(rng, rules))
    key = tuple(sorted(dice))
    multiplier = next((pay for a, b, c, pay in rules.rows if (a, b, c) == key), 0)
    winnings = u16(bet) * multiplier
    actor.gold = u16(actor.gold + winnings)
    return DiceResult(dice, multiplier, winnings)
