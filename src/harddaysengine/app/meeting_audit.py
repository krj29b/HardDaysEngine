"""Natural encounter callers and original shared-sheet audit controls (§12.40)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harddaysengine.app.fight_audit import FightAudit
from harddaysengine.app.game import GameScene
from harddaysengine.app.input import InputFrame, PlayerInput
from harddaysengine.sim.fight_moves import DOWN, FIRE, LEFT, RIGHT, UP
from harddaysengine.sim.meeting import MeetingPhase
from harddaysengine.sim.overworld import Arrival
from harddaysengine.sim.rewards import recalc_endurance
from harddaysengine.sim.types import KnightId

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from harddaysengine.app.audit import AuditRig, AuditShot
    from harddaysengine.app.game import GameSession
    from harddaysengine.sim.sheet_types import SheetRegion


def meeting_input(session: GameSession) -> InputFrame:
    active, loader = session.active_encounter, session.fight_loader
    if active is None or loader is None:
        raise ValueError("encounter audit has no fight")
    human = int(session.players[0]) + 1
    other = active.runtime.opponent if active.runtime.first == human else active.runtime.first
    if not other:
        other = 5
    word = FightAudit(active.runtime.scene, loader.knight_rules, other, human).input_word()
    return InputFrame(
        players={
            session.players[0]: PlayerInput(
                dx=int(bool(word & RIGHT)) - int(bool(word & LEFT)),
                dy=int(bool(word & DOWN)) - int(bool(word & UP)),
                fire=bool(word & FIRE),
            )
        }
    )


def meeting_until(
    session: GameSession, predicate: Callable[[], bool], *, win: bool = False
) -> None:
    for _ in range(12000):
        if predicate():
            return
        inp = meeting_input(session) if win and session.scene == GameScene.COMBAT else InputFrame()
        session.step_tick(inp)
    raise ValueError("encounter audit continuation did not finish")


def meeting_ready(session: GameSession, phase: MeetingPhase) -> bool:
    screen = session.meeting_screen
    return (
        screen is not None
        and screen.state.phase == phase
        and not screen.state.wait
        and not session.palette_fx.is_fading
    )


def meeting_click(session: GameSession, region: SheetRegion) -> None:
    sheet = session.sheet_screen
    if sheet is None:
        raise ValueError("encounter audit has no shared sheet")
    while sheet.state.wait:
        session.step_tick(InputFrame())
    sheet.state.pointer_x, sheet.state.pointer_y = region.x + 1, region.y + 1
    kid = KnightId(sheet.state.knight - 1)
    session.step_tick(InputFrame(players={kid: PlayerInput(fire=True)}))


def meeting_done(session: GameSession) -> None:
    sheet = session.sheet_screen
    if sheet is None:
        raise ValueError("encounter audit has no sheet to close")
    meeting_click(session, next(r for r in sheet.state.regions if r.id == 7))


def meeting_visit(session: GameSession, *, defender_wins: bool = False) -> None:
    """Explicit equipment fixture, followed only by normal map/duel inputs."""
    for kid in (KnightId.RED, KnightId.BLUE):
        actor = session.actors[kid]
        session.inventories[int(kid) + 1].counts[:] = [0] * 12
        actor.lives, actor.frog_days, actor.armour = 3, 0, 27
        actor.hp = actor.hp_max = 200 if kid == KnightId.RED else 20
        actor.strength, actor.constitution, actor.endurance, actor.sword = 3, 2, 2, 25
        recalc_endurance(actor)
    first, other = (KnightId.BLUE, KnightId.RED) if defender_wins else (KnightId.RED, KnightId.BLUE)
    session.overworld.turn_index = session.overworld.turn_order.index(first)
    session.overworld.spent = 17
    session.overworld.prng.state = 0x2468ACE1
    session._dispatch_arrival(Arrival(int(other) + 1, 1))


def hoard_visit(session: GameSession) -> None:
    actor, world, inv = session.actors[0], session.overworld, session.inventories[1]
    actor.hp = actor.hp_max = 80
    actor.strength, actor.constitution, actor.endurance = 5, 2, 2
    actor.armour, actor.sword, actor.lives, actor.daggers = 30, 25, 3, 10
    inv.counts[:] = [0] * 12
    inv.counts[4], inv.counts[11] = 1, 2
    recalc_endurance(actor)
    world.setup_turn()
    world.spent, world.moon_frame, world.prng.state = 17, 45, 0x2468ACE1
    session._dragon_attack()


def _duel_shots(rig: AuditRig, *, defender: bool) -> Iterator[AuditShot]:
    session = rig.new_session()
    meeting_visit(session, defender_wins=defender)
    meeting_until(session, lambda: session.scene == GameScene.STATUS, win=True)
    for _ in range(40):
        session.step_tick(InputFrame())
    yield rig.capture(session, f"meeting_human_winner_{int(defender)}")
    meeting_done(session)
    meeting_until(session, lambda: session.scene == GameScene.OVERWORLD)
    for _ in range(15):
        session.step_tick(InputFrame())
    yield rig.capture(session, f"meeting_return_{int(defender)}")


def meeting_shots(rig: AuditRig) -> Iterator[AuditShot]:
    session = rig.new_session(players=(KnightId.RED, KnightId.BLUE))
    session.inventories[2].counts[9] = 1
    session._dispatch_arrival(Arrival(2, 1))
    meeting_until(session, lambda: meeting_ready(session, MeetingPhase.PROTECT))
    yield rig.capture(session, "meeting_protection_message")
    session.step_tick(InputFrame(players={KnightId.BLUE: PlayerInput(fire=True)}))
    session.step_tick(InputFrame())
    meeting_until(session, lambda: meeting_ready(session, MeetingPhase.PROTECT_SHEET))
    yield rig.capture(session, "meeting_defender_protection_sheet")
    yield from _duel_shots(rig, defender=False)
    yield from _duel_shots(rig, defender=True)
    session = rig.new_session()
    hoard_visit(session)
    meeting_until(session, lambda: session.scene == GameScene.STATUS, win=True)
    for _ in range(40):
        session.step_tick(InputFrame())
    yield rig.capture(session, "dragon_hoard")
    meeting_done(session)
    meeting_until(session, lambda: session.scene == GameScene.OVERWORLD)
    for _ in range(15):
        session.step_tick(InputFrame())
    yield rig.capture(session, "dragon_hoard_spent_return")
