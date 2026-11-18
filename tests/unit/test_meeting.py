"""Original meeting decision boundaries and protection ordering (§12.40)."""

import pytest

from harddaysengine.app.game import GameScene, GameSession
from harddaysengine.sim.actor import Actor
from harddaysengine.sim.inventory import Inventory
from harddaysengine.sim.meeting import MeetingAction, MeetingRules, MeetingState, decide
from harddaysengine.sim.overworld import Arrival

RULES = MeetingRules(4, 18, 1, 1, 9, 10, 2, 36)


@pytest.mark.parametrize(
    "first_kind,second_kind,lives,frog,scroll,result",
    [
        (0, 1, 0, 0, 1, MeetingAction.SETTLE),
        (0, 1, 3, 1, 1, MeetingAction.SETTLE),
        (0, 1, 255, 0, 0, MeetingAction.FIGHT),
        (0, 1, 3, 0, 1, MeetingAction.PROTECT),
        (4, 1, 3, 0, 1, MeetingAction.PROTECT),
        (4, 4, 3, 0, 1, MeetingAction.RETURN),
        (0, 4, 3, 0, 1, MeetingAction.FIGHT),
        (4, 4, 0, 0, 0, MeetingAction.SETTLE),
        (4, 4, 3, 2, 1, MeetingAction.SETTLE),
    ],
)
def test_original_decision_order(
    first_kind: int, second_kind: int, lives: int, frog: int, scroll: int, result: MeetingAction
) -> None:
    first, second, inv = (
        Actor(kind=first_kind),
        Actor(kind=second_kind, lives=lives, frog_days=frog),
        Inventory(),
    )
    inv.counts[9] = scroll
    assert decide(first, second, inv, RULES) == result


def test_press_release_and_missing_disks_do_not_change_turn_or_inventory() -> None:
    state = MeetingState(1, 2)
    assert not state.released(False) and not state.released(True)
    assert state.released(False) and not state.released(False)
    session = GameSession(start_scene=GameScene.OVERWORLD)
    before = session.state_hash()
    session._dispatch_arrival(Arrival(2, 1))
    assert session.scene == GameScene.OVERWORLD and session.meeting_screen is None
    assert session.state_hash() == before and "ORIGINAL DISKS REQUIRED" in session.last_status
