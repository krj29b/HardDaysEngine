"""Valley byte/word boundaries, stat rejection and deferred moonstones (§12.38)."""

from dataclasses import asdict
from unittest.mock import patch

import pytest

from harddaysengine.app.game import GameScene, GameSession
from harddaysengine.sim.actor import Actor
from harddaysengine.sim.inventory import Inventory
from harddaysengine.sim.overworld import Arrival
from harddaysengine.sim.rng import Rng
from harddaysengine.sim.turn_rules import GiftRules
from harddaysengine.sim.valley import (
    ValleyRules,
    ValleyState,
    defeat,
    moonstone,
    victory,
)

RULES = ValleyRules(15, 2, 1, 1, 3, 0, 10, 3, 2, 36)
GIFTS = GiftRules((), (70, 71, 72) * 3, (25, 50, 75), ())


@pytest.mark.parametrize(
    "stat,expected", [(0, 255), (1, 1), (2, 1), (4, 3), (128, 127), (255, 254)]
)
def test_loss_picks_before_life_loss_and_keeps_equality_floor(stat: int, expected: int) -> None:
    actor = Actor(strength=stat, lives=1, hp=20, progress=8)
    before = asdict(actor)

    def pick(record: Actor, _rng: Rng, _gifts: GiftRules) -> int:
        assert record.lives == 1
        return 70

    with patch("harddaysengine.sim.valley.pick_stat", side_effect=pick):
        assert defeat(actor, Rng(13), GIFTS, RULES) == 70
    before.update(lives=255, strength=expected)
    assert asdict(actor) == before


def test_no_pickable_stat_omits_unrelated_record_write_and_draws_nothing() -> None:
    actor = Actor(strength=5, constitution=6, endurance=7, lives=0, active=3)
    rng = Rng(19)
    before = asdict(actor)
    assert defeat(actor, rng, GIFTS, RULES) == 0
    before["lives"] = 254
    assert asdict(actor) == before and rng.state == 19


def test_loss_reuses_real_stat_rejection_stream() -> None:
    actor = Actor(strength=5, constitution=5, endurance=3, lives=4)
    rng, oracle = Rng(0x12345678), Rng(0x12345678)
    attempts = 0
    while True:
        row = oracle.draw() & 15
        row -= 7 if row > 8 else 0
        attempts += 1
        if GIFTS.stats[row] == 72:
            break
    assert attempts > 1
    assert defeat(actor, rng, GIFTS, RULES) == 72
    assert actor.endurance == 2 and actor.lives == 2 and rng.state == oracle.state


@pytest.mark.parametrize("progress,expected", [(0, 3), (65534, 1), (65535, 2)])
def test_victory_only_wraps_progress_and_clears_keys(progress: int, expected: int) -> None:
    actor, inv = Actor(progress=progress, lives=4, hp=7), Inventory()
    inv.counts[:] = [i + 1 for i in range(12)]
    before, counts = asdict(actor), inv.counts[:]
    victory(actor, inv, RULES)
    before["progress"], counts[10] = expected, 0
    assert asdict(actor) == before and inv.counts == counts


@pytest.mark.parametrize("seed,bit", [(0, 0), (0x100, 1), (0x200, 2), (0x300, 3)])
def test_one_moonstone_draw_ors_existing_bits_including_zero(seed: int, bit: int) -> None:
    inv, rng, oracle = Inventory(), Rng(seed), Rng(seed)
    inv.counts[11] = 8
    expected = oracle.draw()
    assert expected & 3 == bit
    assert moonstone(inv, rng, RULES) == bit
    assert inv.byte(22) == 8 | (1 << bit) and rng.state == oracle.state


def test_press_release_latch_and_no_disks_leave_map_unchanged() -> None:
    state = ValleyState(1, False, 0)
    assert not state.released(False)
    assert not state.released(True) and not state.released(True)
    assert state.released(False) and not state.released(False)
    session = GameSession(start_scene=GameScene.OVERWORLD)
    before = asdict(session.overworld)
    session._dispatch_arrival(Arrival(1, 28))
    assert session.valley_screen is None and session.scene == GameScene.OVERWORLD
    assert "ORIGINAL DISKS REQUIRED" in session.last_status
    assert asdict(session.overworld) == before
