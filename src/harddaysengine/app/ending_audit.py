"""The ending reached naturally from Stonehenge, sampled across its scenes (§12.39.10)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harddaysengine.app.game import GameScene
from harddaysengine.app.input import InputFrame, PlayerInput
from harddaysengine.sim.overworld import Arrival
from harddaysengine.sim.stonehenge import StonePhase
from harddaysengine.sim.types import KnightId

if TYPE_CHECKING:
    from collections.abc import Iterator

    from harddaysengine.app.audit import AuditRig, AuditShot
    from harddaysengine.app.game import GameSession

# VBLs after the hand-over: caption, the four scenes, the closing text and "The End".
ENDING_SAMPLES: tuple[int, ...] = (
    20,
    160,
    400,
    700,
    1000,
    1400,
    1800,
    2200,
    2500,
    2700,
    2900,
    3150,
    3500,
    3900,
    4040,
)


def _step(session: GameSession, *, fire: bool = False) -> None:
    session.step_tick(InputFrame(players={KnightId.RED: PlayerInput(fire=fire)}))


def _quest_ready(session: GameSession) -> bool:
    screen = session.stone_screen
    if screen is None or screen.state.phase != StonePhase.QUEST or screen.state.wait:
        return False
    return not session.palette_fx.is_fading


def reach_ending(session: GameSession, *, frame: int, bit: int, kind: int) -> None:
    """Match the stones at Stonehenge and release fire on the quest text (§12.34)."""
    session.overworld.moon_frame = frame
    session.actors[0].kind = kind
    session.inventories[1].counts[11] = 1 << bit
    session._dispatch_arrival(Arrival(1, 27))
    for _ in range(2000):
        if _quest_ready(session):
            break
        _step(session)
    else:
        raise ValueError("Stonehenge never reached the quest text")
    _step(session, fire=True)
    _step(session)
    if session.scene != GameScene.ENDING:
        raise ValueError("the matching visit did not hand over to the ending")


def ending_shots(rig: AuditRig) -> Iterator[AuditShot]:
    """Contact frames of one ending (blue knight, frame-46 moon) and the title after it."""
    session = rig.new_session()
    reach_ending(session, frame=46, bit=3, kind=0)
    tick = 0
    for sample in ENDING_SAMPLES:
        while tick < sample:
            _step(session)
            tick += 1
        yield rig.capture(session, f"ending_t{sample}")
    while session.scene == GameScene.ENDING:
        _step(session)
    _step(session)
    _step(session)
    yield rig.capture(session, f"ending_then_{session.scene.name.lower()}")
