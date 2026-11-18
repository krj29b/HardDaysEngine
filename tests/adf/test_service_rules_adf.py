"""Independent reads of service tables and original random draw order (§12.30)."""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

import pytest

from harddaysengine.assets.adf import DiskSet
from harddaysengine.assets.hunk import parse_hunk_executable
from harddaysengine.assets.service_data import parse_service_rules
from harddaysengine.sim.actor import Actor
from harddaysengine.sim.inventory import Inventory
from harddaysengine.sim.rng import Rng
from harddaysengine.sim.services import Donation, dice_bet, dice_roll, heal, mystic_gamble
from harddaysengine.sim.turn_rules import GiftRules

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.adf


def test_service_data_matches_independent_operands_and_raw_tables(adf_dir: Path) -> None:
    exe = parse_hunk_executable("mog", DiskSet.from_directory(adf_dir).read_file("mog"))
    healer, mystic, dice = parse_service_rules(exe)
    code = exe.sections[0].data

    def word(pos: int) -> int:
        return int.from_bytes(code[pos : pos + 2], "big")

    def target(pos: int, hunk: int) -> int:
        assert pos + 2 in exe.sections[0].relocs[hunk]
        return int.from_bytes(code[pos + 2 : pos + 6], "big")

    assert (healer.heal_price, healer.life_price, healer.life_cap, healer.reply_limit) == tuple(
        word(pos) for pos in (0x9ED6, 0x9F18, 0x9F0A, 0x9F26)
    )
    start = target(0x9D92, 0)
    assert mystic.bands == tuple(struct.iter_unpack(">hh", code[start : start + 24]))
    assert (mystic.win_at, mystic.hp_step, mystic.hp_floor, mystic.stat_floor) == tuple(
        word(pos) for pos in (0x9DAC, 0x9CAA, 0x9CF2, 0x9CCE)
    )
    start = target(0xA7CA, 42)
    raw = exe.sections[42].data[start : start + 66]
    assert dice.rows == tuple(
        (a, b, c, pay) for a, b, c, _, pay, _ in struct.iter_unpack(">6B", raw)
    )
    assert (dice.mask, dice.face_max) == (word(0xA732), word(0xA736))


def _draw(state: int) -> int:
    for _ in range(8):
        rotated = ((state >> 3) | (state << 29)) & 0xFFFFFFFF
        state = (state >> 1) | ((((rotated ^ state) >> 1) & 1) << 31)
    return state


@pytest.mark.parametrize("seed", (1, 0x12345678, 0xDEADBEEF))
def test_original_dice_rounds_match_raw_table_and_independent_rng(adf_dir: Path, seed: int) -> None:
    exe = parse_hunk_executable("mog", DiskSet.from_directory(adf_dir).read_file("mog"))
    _, _, rules = parse_service_rules(exe)
    code = exe.sections[0].data
    start = int.from_bytes(code[0xA7CC:0xA7D0], "big")
    raw = exe.sections[42].data[start : start + 66]
    payouts = {tuple(row[:3]): row[4] for row in struct.iter_unpack(">6B", raw)}
    actor, rng, expected_gold, state = Actor(gold=200), Rng(seed), 200, seed
    for round_index in range(50):
        bet = round_index % 5 + 1
        assert dice_bet(actor, bet)
        expected_gold -= bet
        faces = []
        for _ in range(3):
            state = _draw(state)
            while state & 7 > 5:
                state = _draw(state)
            faces.append(state & 7)
        win = payouts.get(tuple(sorted(faces)), 0) * bet
        expected_gold = (expected_gold + win) & 65535
        result = dice_roll(actor, bet, rng, rules)
        assert result.dice == tuple(faces)
        assert (result.winnings, actor.gold, rng.state) == (win, expected_gold, state)


def test_original_paid_healing_and_mystic_draw_order(adf_dir: Path) -> None:
    exe = parse_hunk_executable("mog", DiskSet.from_directory(adf_dir).read_file("mog"))
    healer, mystic, _ = parse_service_rules(exe)
    actor = Actor(gold=40, hp=1, hp_max=30, lives=3, life_loss=1)
    state = Donation(actor.gold)
    for _ in range(20):
        assert state.move(more=True)
    state.close(actor, accept=True)
    remaining, changed = heal(actor, state.amount, healer)
    assert (actor.gold, actor.hp, actor.lives, actor.life_loss, changed) == (20, 30, 4, 0, 7)
    assert remaining == 65531  # life purchase tests 10 remaining and then subtracts 15
    actor.strength = actor.constitution = actor.endurance = 2
    rng = Rng(0x12345678)
    oracle = _draw(_draw(rng.state))  # one accepted stat draw, then one percent draw
    percent = oracle & 127
    percent = percent - 27 if percent >= 100 else percent
    bonus = next(bonus for bound, bonus in mystic.bands if bound >= 20)
    result = mystic_gamble(
        actor, Inventory(), 20, rng, GiftRules((), (70,) * 9, (0, 0, 0), ()), mystic
    )
    assert result.outcome == ("raise" if percent + bonus >= mystic.win_at else "lower")
    assert actor.strength == (3 if result.outcome == "raise" else 1)
    assert rng.state == oracle
