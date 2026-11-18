"""Original Stonehenge rules and persistent ritual jobs (ARCHITECTURE.md §12.16/34)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import IntEnum
from functools import partial
from typing import TYPE_CHECKING

from harddaysengine.sim.actor import Actor
from harddaysengine.sim.fight import END, Fight, HandlerResult
from harddaysengine.sim.m68k import u8, u16
from harddaysengine.sim.rewards import recalc_hp
from harddaysengine.sim.sound import SoundKind, SoundRequest

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harddaysengine.sim.fight import FightConstants
    from harddaysengine.sim.fight_script import CelShape, Draw, RoutineCall, ScriptImage
    from harddaysengine.sim.inventory import Inventory
    from harddaysengine.sim.rng import Rng
    from harddaysengine.sim.turn_rules import TextLine


@dataclass(slots=True, frozen=True)
class RitualSpawn:
    script: int
    position: tuple[int, int, int]
    facing: int
    kind: int


@dataclass(slots=True, frozen=True)
class RitualRules:
    picture: str
    cel: str
    bank: str
    spawns: tuple[RitualSpawn, ...]
    ramps: tuple[tuple[int, tuple[int, ...]], ...]
    palette_entry: int
    palette_period: int
    frame_wait: int
    sounds: tuple[SoundRequest, ...]
    sound_limit: int
    sound_id: int


@dataclass(slots=True, frozen=True)
class StoneRules:
    matches: tuple[tuple[int, int], ...]
    moon_bits: tuple[tuple[int, int], ...]
    class_bits: tuple[tuple[int, int], ...]
    ending_flag: int
    life_cap: int
    life_gain: int
    quest_wait: int
    fade_period: int
    fade_wait: int
    message: str
    font: str
    recolor: tuple[int, ...]
    offer: tuple[TextLine, ...]
    quest: tuple[TextLine, ...]
    ritual_text: tuple[TextLine, ...]
    program: str
    ritual: RitualRules


@dataclass(slots=True, frozen=True)
class EndingRequest:
    """The original executable and boot flags handed to T11.22 (§12.34)."""

    program: str
    flags: int


class StonePhase(IntEnum):
    MAP_OUT = 0
    OFFER = 1
    OFFER_OUT = 2
    OFFER_SHEET = 3
    OFFER_CLOSING = 4
    RITUAL_MESSAGE = 5
    RITUAL_MESSAGE_OUT = 6
    RITUAL = 7
    RITUAL_OUT = 8
    TEMPLE = 9
    TEMPLE_CLOSING = 10
    QUEST = 11
    HAND_OVER = 12


@dataclass(slots=True)
class StoneState:
    knight: int
    matched: bool
    ending: int
    phase: StonePhase = StonePhase.MAP_OUT
    wait: int = 0
    fire_held: bool = False
    request: EndingRequest | None = None

    def released(self, fire: bool) -> bool:
        """Original press-then-release wait; earlier held input is not buffered."""
        if fire:
            self.fire_held = True
            return False
        released, self.fire_held = self.fire_held, False
        return released


def matches(rules: StoneRules, moon: int, stones: int) -> bool:
    return any(moon == frame and stones & (1 << bit) for bit, frame in rules.matches)


def ending_code(rules: StoneRules, moon: int, kind: int) -> int:
    """Read-decoded bit rows retain the original class and phase pairing (§12.16)."""
    flags = rules.ending_flag
    for key, bit in rules.moon_bits:
        if key == moon:
            flags |= 1 << bit
    for key, bit in rules.class_bits:
        if key == kind:
            flags |= 1 << bit
    return flags


def bless(actor: Actor, inventory: Inventory, rules: StoneRules) -> None:
    """The equality-only life cap, healed HP and cleared life-loss byte (§12.16)."""
    if actor.lives != rules.life_cap:
        actor.lives = u8(actor.lives + rules.life_gain)
    recalc_hp(actor, inventory)
    actor.hp, actor.life_loss = actor.hp_max, 0


@dataclass(slots=True, frozen=True)
class RitualEngine:
    image: ScriptImage
    cels: Mapping[str, CelShape]
    constants: FightConstants


@dataclass(slots=True)
class RitualRun:
    """Ritual-local fight jobs using the campaign generator, without a second VM."""

    rules: RitualRules
    fight: Fight
    done: bool = False
    started: bool = False
    countdown: int = 0
    batches: list[list[Draw]] = field(default_factory=list)
    sounds: list[SoundRequest] = field(default_factory=list)

    def snapshot(self) -> dict[str, object]:
        return {
            "done": self.done,
            "started": self.started,
            "countdown": self.countdown,
            "jobs": [asdict(job) for job in self.fight.engine.jobs],
            "actors": [asdict(actor) for actor in self.fight.actors],
            "vbl": self.fight.engine.vbl,
            "scratch": asdict(self.fight.engine.scratch_work),
            "box": asdict(self.fight.engine.box),
            "mirrored": sorted(self.fight.engine.mirrored),
            "attack": [
                None if value is None else asdict(value)
                for value in self.fight.engine.attack.entries
            ],
            "hurt": [
                None if value is None else asdict(value) for value in self.fight.engine.hurt.entries
            ],
            "cursors": [self.fight.engine.attack.cursor, self.fight.engine.hurt.cursor],
            "rng": self.fight.rng.state,
        }

    def advance(self, vbl: int) -> None:
        """Initial pass, immediate loop pass, then one pass per frame wait (§12.34)."""
        self.batches, self.sounds = [], []
        if self.done:
            return
        if not self.started:
            self.started = True
            self._pass(vbl)
            self.sounds.extend(self.rules.sounds)
        elif self.countdown > 1:
            self.countdown -= 1
            return
        if not self.done:
            self._pass(vbl)
        self.countdown = self.rules.frame_wait

    def _pass(self, vbl: int) -> None:
        fight = self.fight
        fight.skipped, fight.sounds = [], []
        fight.advance_vbl(vbl)
        fight.beam_word = u16(vbl)
        fight.job_pass()
        if self.done:
            return
        fight.engine.vbl, fight.engine.current_knight = vbl, fight.first
        output = fight.engine.tick()
        if fight.skipped:
            raise ValueError("ritual called an unimplemented engine routine")
        self.batches.append(list(output.draws))
        self.sounds.extend(fight.sounds)


def _done(run: RitualRun, _fight: Fight, _handle: int) -> HandlerResult:
    run.done = True
    return END


def _sound(rules: RitualRules, fight: Fight, _call: RoutineCall) -> None:
    if fight.rng.percent() <= rules.sound_limit:
        fight.sounds.append(SoundRequest(SoundKind.PLAY, rules.sound_id))


def open_ritual(rules: RitualRules, engine: RitualEngine, rng: Rng) -> RitualRun:
    fight = Fight(
        engine.image,
        engine.cels,
        {1: (rules.cel, None, None, None, None)},
        [Actor() for _ in range(4)],
        Actor(),
        engine.constants,
        rng=rng,
    )
    run = RitualRun(rules, fight)
    fight.routines["ritual_sound"] = partial(_sound, rules)
    for spawn in rules.spawns:
        fight.handlers[spawn.kind] = partial(_done, run)
        fight.spawn_actor(spawn.script, 1, spawn.position, spawn.facing, spawn.kind)
    return run
