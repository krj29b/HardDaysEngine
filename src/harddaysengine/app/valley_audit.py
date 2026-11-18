"""Natural Valley visit outcomes and original loading/contact frames (§12.38)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harddaysengine.app.fight_audit import fight_host_melee_input
from harddaysengine.app.game import GameScene
from harddaysengine.app.input import InputFrame, PlayerInput
from harddaysengine.app.village_audit import village_exit
from harddaysengine.sim.overworld import Arrival
from harddaysengine.sim.rewards import recalc_endurance
from harddaysengine.sim.types import KnightId
from harddaysengine.sim.valley import ValleyPhase

if TYPE_CHECKING:
    from collections.abc import Iterator

    from harddaysengine.app.audit import AuditRig, AuditShot
    from harddaysengine.app.game import GameSession


def _arrived(session: GameSession, phase: ValleyPhase) -> bool:
    screen = session.valley_screen
    if screen is None:
        raise ValueError("Valley visit ended before the requested phase")
    ready = not screen.state.wait or phase in (ValleyPhase.LOADING, ValleyPhase.COMBAT_OUT)
    return screen.state.phase == phase and ready and not session.palette_fx.is_fading


def valley_until(session: GameSession, phase: ValleyPhase, *, win: bool = False) -> None:
    for _ in range(12000):
        if _arrived(session, phase):
            return
        inp = (
            fight_host_melee_input(session)
            if session.scene == GameScene.COMBAT and win
            else InputFrame()
        )
        session.step_tick(inp)
    raise ValueError("Valley continuation did not finish")


def valley_visit(session: GameSession, *, keys: int = 15, win: bool = False) -> None:
    """Explicit audit equipment; every fight thereafter uses ordinary inputs."""
    actor = session.actors[0]
    actor.hp = actor.hp_max = 200 if win else 20
    actor.strength, actor.constitution, actor.endurance = 3, 2, 2
    actor.lives, actor.sword, actor.armour = 5, 25, 27
    recalc_endurance(actor)
    session.inventories[1].counts[:] = [0] * 12
    session.inventories[1].counts[10] = keys
    session.overworld.prng.state = 0x2468ACE1
    session._dispatch_arrival(Arrival(1, 28))


def _message_exit(session: GameSession) -> None:
    session.step_tick(InputFrame(players={KnightId.RED: PlayerInput(fire=True)}))
    session.step_tick(InputFrame())
    for _ in range(200):
        if session.scene == GameScene.OVERWORLD:
            return
        session.step_tick(InputFrame())
    raise ValueError("Valley message did not return to the map")


def valley_shots(rig: AuditRig) -> Iterator[AuditShot]:
    session = rig.new_session()
    valley_visit(session, keys=7)
    valley_until(session, ValleyPhase.MISSING)
    yield rig.capture(session, "valley_missing_keys")
    _message_exit(session)
    for _ in range(15):
        session.step_tick(InputFrame())
    yield rig.capture(session, "valley_retained_turn")
    for win in (False, True):
        yield from _fight_shots(rig, win=win)


def _fight_shots(rig: AuditRig, *, win: bool) -> Iterator[AuditShot]:
    outcome = "win" if win else "loss"
    session = rig.new_session()
    valley_visit(session, win=win)
    valley_until(session, ValleyPhase.LOADING)
    yield rig.capture(session, f"valley_loading_{outcome}")
    for _ in range(200):
        session.step_tick(InputFrame())
        if session.scene == GameScene.COMBAT:
            break
    for _ in range(120):
        session.step_tick(InputFrame())
    yield rig.capture(session, f"valley_guardian_stage_{outcome}")
    phase = ValleyPhase.VICTORY if win else ValleyPhase.TEMPLE
    valley_until(session, phase, win=win)
    yield rig.capture(session, f"valley_{outcome}_outcome")
    if win:
        _message_exit(session)
    else:
        village_exit(session)
    for _ in range(15):
        session.step_tick(InputFrame())
    yield rig.capture(session, f"valley_{outcome}_return")
