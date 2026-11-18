"""Natural wizard visits for every gift and class ramp (ARCHITECTURE.md §12.36)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harddaysengine.app.game import GameScene
from harddaysengine.app.input import InputFrame, PlayerInput
from harddaysengine.sim.overworld import Arrival
from harddaysengine.sim.rewards import GiftKind, GiftState, recalc_endurance
from harddaysengine.sim.rng import Rng
from harddaysengine.sim.types import KnightId
from harddaysengine.sim.wizard import WizardPhase

if TYPE_CHECKING:
    from collections.abc import Iterator

    from harddaysengine.app.audit import AuditRig, AuditShot
    from harddaysengine.app.game import GameSession


def wizard_seed(session: GameSession, kind: GiftKind) -> int:
    """Choose a fixture seed; the campaign still performs the natural reward roll."""
    limits = session._turn_rules().gifts.limits
    for n in range(1, 1025):
        seed = n * 0x1020305 & 0xFFFFFFFF
        roll = Rng(seed).percent()
        result = (GiftKind.ITEM, GiftKind.STAT, GiftKind.GOLD, GiftKind.CURSE)[
            sum(roll > v for v in limits)
        ]
        if result == kind:
            return seed
    raise ValueError("wizard audit could not find the requested reward seed")


def wizard_visit(rig: AuditRig, kind: GiftKind) -> GameSession:
    """Explicit fixture setup belongs to the audit, with ordinary input thereafter."""
    session = rig.new_session()
    actor = session.actors[KnightId.RED]
    actor.kind = int(kind) - 1
    actor.strength = actor.constitution = actor.endurance = 2
    actor.hp, actor.hp_max, actor.recency, actor.armour = 20, 30, 0, 27
    recalc_endurance(actor)
    session.inventories[1].counts[:] = [0] * 12
    session.overworld.gifts = GiftState()
    session.overworld.prng.state = wizard_seed(session, kind)
    session._dispatch_arrival(Arrival(1, 30))
    return session


def wizard_until(session: GameSession, phase: WizardPhase, *, fire: bool = False) -> None:
    frame = InputFrame(players={KnightId.RED: PlayerInput(fire=fire)})
    for _ in range(2000):
        screen = session.wizard_screen
        arrived = screen is not None and screen.state.phase == phase
        if arrived and not session.palette_fx.is_fading:
            return
        session.step_tick(InputFrame() if arrived else frame)
    raise ValueError("wizard audit continuation did not finish")


def _frames(session: GameSession, count: int) -> None:
    for _ in range(count):
        session.step_tick(InputFrame())


def _finish(rig: AuditRig, session: GameSession) -> Iterator[AuditShot]:
    wizard_until(session, WizardPhase.TEMPLE, fire=True)
    yield rig.capture(session, "wizard_temple")
    sheet = session.sheet_screen
    if sheet is None:
        raise ValueError("wizard audit has no temple")
    done = next(r for r in sheet.state.regions if r.id == 7)
    sheet.state.pointer_x, sheet.state.pointer_y = done.x, done.y
    session.step_tick(InputFrame(players={KnightId.RED: PlayerInput(fire=True)}))
    for _ in range(200):
        if session.scene == GameScene.OVERWORLD:
            break
        _frames(session, 1)
    if session.scene != GameScene.OVERWORLD:
        raise ValueError("wizard audit did not spend the turn")
    _frames(session, 15)
    yield rig.capture(session, "wizard_spent_exit")


def wizard_shots(rig: AuditRig) -> Iterator[AuditShot]:
    for kind in GiftKind:
        session = wizard_visit(rig, kind)
        if kind == GiftKind.ITEM:
            wizard_until(session, WizardPhase.LOADING)
            yield rig.capture(session, "wizard_loading")
        wizard_until(session, WizardPhase.ARRIVAL)
        yield rig.capture(session, f"wizard_arrival_class{int(kind) - 1}")
        _frames(session, 40)
        yield rig.capture(session, f"wizard_tower_animation_{kind.name.lower()}")
        wizard_until(session, WizardPhase.GIFT, fire=True)
        yield rig.capture(session, f"wizard_gift_{kind.name.lower()}")
        _frames(session, 35)
        yield rig.capture(session, f"wizard_gift_animation_{kind.name.lower()}")
        wizard_until(session, WizardPhase.FAREWELL, fire=True)
        yield rig.capture(session, f"wizard_farewell_{kind.name.lower()}")
        if kind == GiftKind.CURSE:
            yield from _finish(rig, session)
