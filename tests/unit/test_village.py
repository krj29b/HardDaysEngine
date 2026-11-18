"""Signed village reward boundaries and retained caller state (§12.37)."""

from dataclasses import asdict

import pytest

from harddaysengine.app.game import GameScene, GameSession
from harddaysengine.sim.actor import Actor
from harddaysengine.sim.overworld import Arrival
from harddaysengine.sim.village import VillagePhase, VillageRules, VillageVisit, reward


@pytest.mark.parametrize(
    "lives,expected", [(0, 1), (1, 2), (2, 3), (3, 3), (4, 4), (127, 127), (128, 129), (255, 0)]
)
def test_reward_changes_only_lives_using_signed_comparison(lives: int, expected: int) -> None:
    actor = Actor(lives=lives, hp=7, hp_max=30, life_loss=1, gold=12)
    before = asdict(actor)
    reward(actor, VillageRules(3, 1, 9, 2, 36))
    before["lives"] = expected
    assert asdict(actor) == before


def test_reward_uses_decoded_cap_and_gain_and_wraps_addition() -> None:
    actor = Actor(lives=254)
    reward(actor, VillageRules(5, 4, 9, 2, 36))
    assert actor.lives == 2
    reward(actor, VillageRules(5, 4, 9, 2, 36))
    assert actor.lives == 6
    reward(actor, VillageRules(5, 4, 9, 2, 36))
    assert actor.lives == 6


def test_visit_owner_phase_and_wait_are_hashed_and_reset() -> None:
    session = GameSession()
    visit = VillageVisit(1, VillageRules(3, 1, 9, 2, 36), wait=36)
    session.village_visit = visit
    hashes = {session.state_hash()}
    for attr, value in (("knight", 2), ("phase", VillagePhase.TEMPLE), ("wait", 35)):
        setattr(visit, attr, value)
        hashes.add(session.state_hash())
    assert len(hashes) == 4
    session._reset_campaign(1)
    assert session.village_visit is None


@pytest.mark.parametrize("kind", (21, 22, 23, 24))
def test_missing_disks_preserve_map_and_reward(kind: int) -> None:
    session = GameSession()
    session.scene = GameScene.OVERWORLD
    before = [asdict(actor) for actor in session.actors]
    session._dispatch_arrival(Arrival(1, kind))
    assert session.scene == GameScene.OVERWORLD and session.village_visit is None
    assert "ORIGINAL DISKS REQUIRED" in session.last_status
    assert [asdict(actor) for actor in session.actors] == before
