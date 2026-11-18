"""Original home-village temple and spent-return captures (§12.37)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harddaysengine.app.game import GameScene
from harddaysengine.app.input import InputFrame, PlayerInput
from harddaysengine.sim.overworld import Arrival
from harddaysengine.sim.types import KnightId

if TYPE_CHECKING:
    from collections.abc import Iterator

    from harddaysengine.app.audit import AuditRig, AuditShot
    from harddaysengine.app.game import GameSession


def village_temple(session: GameSession) -> None:
    """Advance ordinary PAL ticks until the original temple has faded in."""
    for _ in range(200):
        if session.sheet_screen is not None and not session.palette_fx.is_fading:
            return
        session.step_tick(InputFrame())
    raise ValueError("village temple did not open")


def village_exit(session: GameSession) -> None:
    """Choose the shared sheet's Done region and wait for the retained fade."""
    sheet = session.sheet_screen
    if sheet is None:
        raise ValueError("village audit has no temple")
    done = next(row for row in sheet.state.regions if row.id == 7)
    sheet.state.pointer_x, sheet.state.pointer_y = done.x, done.y
    session.step_tick(
        InputFrame(players={KnightId(sheet.state.knight - 1): PlayerInput(fire=True)})
    )
    for _ in range(200):
        if session.scene == GameScene.OVERWORLD:
            return
        session.step_tick(InputFrame())
    raise ValueError("village temple did not return to the map")


def village_shots(rig: AuditRig) -> Iterator[AuditShot]:
    for kind in (21, 22, 23, 24):
        session = rig.new_session()
        session.actors[0].lives = 2
        session.actors[0].hp = 7
        session._dispatch_arrival(Arrival(1, kind))
        village_temple(session)
        yield rig.capture(session, f"village_{kind}_life_reward_temple")
        village_exit(session)
        for _ in range(15):
            session.step_tick(InputFrame())
        yield rig.capture(session, f"village_{kind}_spent_return")
