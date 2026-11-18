"""The dice house: rounds, the thrower job and the roll, one PAL tick at a time (§12.32).

The thrower is an ordinary fight-engine job (type ``$44``) in its own :class:`Fight`, as the
original reuses its fight engine; its handler reads the pointer and fire cells kept here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import IntEnum
from functools import partial
from typing import TYPE_CHECKING

from harddaysengine.sim.actor import Actor
from harddaysengine.sim.fight import END, Fight, HandlerResult, restart_from
from harddaysengine.sim.m68k import s16
from harddaysengine.sim.services import dice_bet, dice_roll
from harddaysengine.sim.sound import SoundKind, SoundRequest, install_sounds
from harddaysengine.sim.turn_rules import TextLine

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harddaysengine.sim.fight import FightConstants
    from harddaysengine.sim.fight_script import CelShape, Draw, ScriptImage
    from harddaysengine.sim.rng import Rng
    from harddaysengine.sim.service_types import DiceRules
    from harddaysengine.sim.sound import SoundRules

POINTER_MAX_X = 314
POINTER_MAX_Y = 195
POINTER_STEP = 2
_NUMBER_MODULO = 1000
FRAME_LIST = 1
_KNIGHTS = 4


@dataclass(slots=True, frozen=True)
class DiceRegion:
    """A registered dice-house button: ``kind`` 1 bets ``code`` gold, 2 leaves (§12.32)."""

    x: int
    y: int
    width: int
    height: int
    kind: int
    code: int

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height


@dataclass(slots=True, frozen=True)
class DiceLine:
    """A roll text line; ``field`` names the number written after ``text`` ("" for none)."""

    text: str
    x: int
    y: int
    style: int
    field: str = ""


@dataclass(slots=True, frozen=True)
class Thrower:
    """The spawn and the handler's scripts and frame waits (§12.32)."""

    x: int
    height: int
    depth: int
    facing: int
    kind: int
    idle_script: int
    throw_script: int
    spawn_wait: int
    idle_wait: int
    throw_wait: int
    bet_kind: int
    leave_kind: int


@dataclass(slots=True, frozen=True)
class DiceHouse:
    """Everything the dice house reads from ``mog`` (§12.32)."""

    tavern: str
    table: str
    cel_file: str
    regions: tuple[DiceRegion, ...]
    ramp_entry: int
    ramps: tuple[tuple[int, tuple[int, ...]], ...]
    sounds: tuple[tuple[int, int], ...]
    pointer_x: int
    thrower: Thrower
    gold_at: tuple[int, int, int]
    dice_at: tuple[tuple[int, int], ...]
    lost: tuple[DiceLine, ...]
    won: tuple[DiceLine, ...]
    suffix: str
    roll_wait: int

    def ramp(self, kind: int) -> tuple[int, ...]:
        """The class shades of ``kind``; an unlisted kind writes nothing."""
        return next((words for key, words in self.ramps if key == kind), ())


@dataclass(slots=True, frozen=True)
class DiceEngine:
    """The fight-engine inputs of the thrower: hunk-42 scripts, the dice cels, routines."""

    image: ScriptImage
    cels: Mapping[str, CelShape]
    constants: FightConstants
    sounds: SoundRules


class DicePhase(IntEnum):
    """Where the original routine is (§12.32)."""

    ROUND = 0
    PLAY = 1
    ROLL_WAIT = 2
    ROLL_FIRE = 3
    DONE = 4


@dataclass(slots=True)
class DiceState:
    """Every mutable cell of a visit, except the engine's own records."""

    knight: int
    pointer_x: int
    pointer_y: int
    phase: DicePhase = DicePhase.ROUND
    throw: int = 0
    frame_wait: int = 0
    countdown: int = 0
    bet: int = 0
    done: bool = False
    fire: bool = False
    fire_held: bool = False
    dice: tuple[int, ...] = ()
    lines: tuple[TextLine, ...] = ()
    sounds: list[SoundRequest] = field(default_factory=list)


@dataclass(slots=True)
class DiceWorld:
    """The player and the rules the visit changes and applies."""

    player: Actor
    rng: Rng
    rules: DiceRules


@dataclass(slots=True)
class DiceRun:
    """One visit: the state, the house data and the thrower's fight engine."""

    house: DiceHouse
    engine: DiceEngine
    state: DiceState
    fight: Fight | None = None
    draws: list[Draw] = field(default_factory=list)
    vbl: int = 0

    def snapshot(self) -> dict[str, object]:
        """Serializable visit state, including the thrower's job and pool records."""
        payload: dict[str, object] = {"state": asdict(self.state), "vbl": self.vbl}
        if self.fight is not None:
            payload["jobs"] = [asdict(job) for job in self.fight.engine.jobs]
            payload["actors"] = [asdict(actor) for actor in self.fight.actors]
        return payload


def open_dice(house: DiceHouse, engine: DiceEngine, knight: int, pointer_y: int) -> DiceRun:
    """Entry after the gold test: regions set, three fixed-channel sounds (§12.32)."""
    state = DiceState(knight, house.pointer_x, pointer_y)
    state.sounds = [SoundRequest(SoundKind.FIXED, seq, ch) for seq, ch in house.sounds]
    return DiceRun(house, engine, state)


def can_enter(player: Actor) -> bool:
    """The dice house is not entered with gold not above 0 (signed)."""
    return s16(player.gold) > 0


def _idle(run: DiceRun, me: Actor) -> HandlerResult:
    run.state.frame_wait = run.house.thrower.idle_wait
    return restart_from(me, run.house.thrower.idle_script)


def thrower_handler(run: DiceRun, world: DiceWorld, fight: Fight, handle: int) -> HandlerResult:
    """Handler of type ``$44`` (§12.32)."""
    state, thrower, me = run.state, run.house.thrower, fight.actor(handle)
    if state.throw:
        state.throw = 2
        return END
    if not state.fire:
        state.throw = 0
        return _idle(run, me)
    region = next(
        (r for r in run.house.regions if r.contains(state.pointer_x, state.pointer_y)), None
    )
    if region is not None and region.kind == thrower.bet_kind:
        state.bet = region.code
        if dice_bet(world.player, state.bet):
            state.throw, state.frame_wait = 1, thrower.throw_wait
            return restart_from(me, thrower.throw_script)
    elif region is not None and region.kind == thrower.leave_kind:
        state.done = True
    return _idle(run, me)


def _new_fight(run: DiceRun, world: DiceWorld) -> Fight:
    engine = run.engine
    fight = Fight(
        engine.image,
        engine.cels,
        {FRAME_LIST: (run.house.cel_file, None, None, None, None)},
        [Actor() for _ in range(_KNIGHTS)],
        Actor(),
        engine.constants,
        rng=world.rng,
    )
    install_sounds(fight, engine.sounds)
    fight.handlers[run.house.thrower.kind] = partial(thrower_handler, run, world)
    return fight


def _pass(run: DiceRun, world: DiceWorld) -> None:
    """Job pass, tick and draws; then the roll, the end, or the frame wait (§12.32)."""
    if run.fight is None:
        raise ValueError("the dice house has no thrower engine")
    output = run.fight.step(run.vbl)
    run.draws = list(output.tick.draws)
    run.state.sounds.extend(output.sounds)
    if output.skipped_calls:
        names = ", ".join(call.name for call in output.skipped_calls)
        raise ValueError(f"dice scripts called unimplemented routines: {names}")
    if run.state.throw == 2:
        _roll(run, world)
    elif run.state.done:
        run.state.phase = DicePhase.DONE
    else:
        run.state.countdown = run.state.frame_wait


def _round(run: DiceRun, world: DiceWorld) -> None:
    state = run.state
    if not can_enter(world.player):
        state.phase = DicePhase.DONE
        return
    thrower = run.house.thrower
    run.fight = _new_fight(run, world)
    run.fight.spawn_actor(
        thrower.idle_script,
        FRAME_LIST,
        (thrower.x, thrower.height, thrower.depth),
        thrower.facing,
        thrower.kind,
    )
    state.throw, state.frame_wait, state.phase = 0, thrower.spawn_wait, DicePhase.PLAY
    _pass(run, world)


def _digits(value: int) -> str:
    return str(value % _NUMBER_MODULO)


def roll_lines(
    house: DiceHouse, lines: tuple[DiceLine, ...], values: dict[str, int]
) -> tuple[TextLine, ...]:
    """The roll list with each field's digits and the suffix after its prefix (§12.32)."""
    out = []
    for line in lines:
        text = line.text
        if line.field:
            text = f"{line.text}{_digits(values[line.field])}{house.suffix}"
        out.append(TextLine(text, line.x, line.y, line.style))
    return tuple(out)


def _roll(run: DiceRun, world: DiceWorld) -> None:
    state, house = run.state, run.house
    result = dice_roll(world.player, state.bet, world.rng, world.rules)
    state.dice = result.dice
    gold = world.player.gold
    if result.multiplier:
        values = {"won": result.winnings, "gold": gold}
        state.lines = roll_lines(house, house.won, values)
    else:
        state.lines = roll_lines(house, house.lost, {"bet": state.bet, "gold": gold})
    state.phase, state.countdown = DicePhase.ROLL_WAIT, house.roll_wait
    run.fight, run.draws = None, []


def _fire_released(state: DiceState, fire: bool) -> bool:
    if fire:
        state.fire_held = True
        return False
    released, state.fire_held = state.fire_held, False
    return released


def _roll_fire(run: DiceRun, world: DiceWorld, fire: bool) -> None:
    if _fire_released(run.state, fire):
        if s16(world.player.gold) < 0:
            run.state.done = True
        run.state.phase = DicePhase.ROUND


def step(
    run: DiceRun,
    world: DiceWorld,
    inputs: tuple[int, int, bool],
    *,
    fading: bool = False,
) -> None:
    """One VBL: the pointer always moves; the routine advances unless a fade runs (§12.32)."""
    dx, dy, fire = inputs
    state = run.state
    state.sounds.clear()
    state.pointer_x = max(0, min(POINTER_MAX_X, state.pointer_x + dx * POINTER_STEP))
    state.pointer_y = max(0, min(POINTER_MAX_Y, state.pointer_y + dy * POINTER_STEP))
    state.fire = fire
    run.vbl += 1
    if fading or state.phase == DicePhase.DONE:
        return
    if state.phase == DicePhase.ROUND:
        _round(run, world)
    elif state.phase == DicePhase.PLAY:
        state.countdown -= 1
        if state.countdown <= 0:
            _pass(run, world)
    elif state.phase == DicePhase.ROLL_WAIT:
        state.countdown -= 1
        if state.countdown <= 0:
            state.phase = DicePhase.ROLL_FIRE
    else:
        _roll_fire(run, world, fire)
