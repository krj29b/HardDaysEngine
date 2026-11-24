"""One fight: actor pools, job plumbing, the job pass and the fight-level routines.

Implements ARCHITECTURE.md §10.18 (and the fight cells of §10.19) on
top of the script engine of §10.17 (:mod:`harddaysengine.sim.fight_script`) and the contact
pass (:mod:`harddaysengine.sim.fight_contact`).
Handlers (§10.16) are registered per type byte by the tasks that write them; engine routines
(``$B0``) per name. A routine without an implementation is reported in
:attr:`FrameOutput.skipped_calls` and otherwise ignored, as §10.18 states.

Actor handles: 1..4 are the knights, 5 the dragon and 6..25 the fight pool. 0 is "none".
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from harddaysengine.sim.actor import Actor
from harddaysengine.sim.fight_contact import contact_scan
from harddaysengine.sim.fight_script import (
    CelShape,
    Job,
    RoutineCall,
    ScriptEngine,
    ScriptImage,
    TickOutput,
)
from harddaysengine.sim.m68k import s16, u16, u32
from harddaysengine.sim.rng import Rng
from harddaysengine.sim.screen_fx import ShakeRules, ShakeState
from harddaysengine.sim.sound import SoundCells, SoundRequest, play
from harddaysengine.sim.trajectory import FlightState, release_flight

if TYPE_CHECKING:
    from harddaysengine.sim.inventory import Inventory

KNIGHT_COUNT = 4
DRAGON = KNIGHT_COUNT + 1
POOL_FIRST = DRAGON + 1
POOL_SIZE = 20
IDLE_OBJECT_TYPE = 0x28

FIGHTER_DIED = "fighter_died"
END_FIGHT = "end_fight"
MARK_FIRST_DEAD = "mark_first_dead"
PAUSE_ALL = "pause_all"
SET_GLOAT = "set_gloat_latch"


class FightError(RuntimeError):
    """A state the original cannot leave (a full pool, a spawn loop that never ends)."""


@dataclass(slots=True, frozen=True)
class Keep:
    """Handler result: leave the job alone."""


@dataclass(slots=True, frozen=True)
class End:
    """Handler result: free the job and its owner."""


@dataclass(slots=True, frozen=True)
class Restart:
    """Handler result: restart the job on ``script`` at this position and facing."""

    script: int
    x: int
    height: int
    depth: int
    facing: int


KEEP: Final = Keep()
END: Final = End()

HandlerResult = Keep | End | Restart
Handler = Callable[["Fight", int], HandlerResult]
Routine = Callable[["Fight", RoutineCall], None]


@dataclass(slots=True, frozen=True)
class FightConstants:
    """Numbers the fight-level routines take from ``mog`` code (§10.12, §10.15)."""

    end_delay: int
    dead_hp: int


@dataclass(slots=True, frozen=True)
class Wall:
    """One wall segment of the arena's obstacle table (§4.3): x range and depth line."""

    x1: int
    x2: int
    y: int


@dataclass(slots=True, frozen=True)
class ScriptTables:
    """The scripts and tables an initialiser gives a record (§10.4), as values."""

    idle_script: int
    after_hit_script: int
    hurt_scripts: tuple[int, ...]
    action_scripts: tuple[int, ...]
    walk_scripts: tuple[int, ...]
    damage_table: tuple[int, ...]
    defence_table: tuple[int, ...]


@dataclass(slots=True, frozen=True)
class TableKeys:
    """:class:`ScriptTables` stored in a fight: the scripts and the keys of the five tables."""

    idle_script: int
    after_hit_script: int
    hurt_scripts: int
    action_scripts: int
    walk_scripts: int
    damage_table: int
    defence_table: int

    def assign(self, actor: Actor) -> None:
        """Point the record's script fields and table fields at these (§10.4)."""
        actor.idle_script, actor.after_hit_script = self.idle_script, self.after_hit_script
        actor.hurt_scripts, actor.action_scripts = self.hurt_scripts, self.action_scripts
        actor.walk_scripts, actor.damage_table = self.walk_scripts, self.damage_table
        actor.defence_table = self.defence_table


@dataclass(slots=True)
class ApproachCells:
    """The scratch words the approach and step helpers pass between each other (§10.20)."""

    dx: int = 0
    dy: int = 0
    dy_override: int = 0
    near: int = 0
    block: int = 0
    vertical: int = 0


@dataclass(slots=True)
class CasterCells:
    """Shared Ratmen flags, wait and aim results (ARCHITECTURE.md §10.23)."""

    flags: int = 0
    wait: int = 0
    aim_distance: int = 0
    aim_steps: int = 0
    aim_arc: int = 0


@dataclass(slots=True)
class DrakeCells:
    """Balok's shared executable cells (ARCHITECTURE.md §10.28)."""

    flags: int = 0
    remaining: int = 0
    target_x: int = 0
    target_y: int = 0
    toggle: int = 0


@dataclass(slots=True)
class DragonCells:
    """Encounter-owned Dragon phases, linked records and saved reach words (§10.30)."""

    flags: int = 0
    spare: int = 0
    distance: int = 0
    link_a: int = 0
    link_b: int = 0
    saved_reach: int = 0
    saved_close: int = 0


@dataclass(slots=True)
class GuardianCells:
    """Encounter-owned Guardian body handles, stages and previous target (§10.29)."""

    main: int = 0
    body: int = 0
    target: int = 0
    flags: int = 0
    spare: int = 0
    fade: bool = False


@dataclass(slots=True)
class FlyerCells:
    """The persistent executable alternation word (§10.26)."""

    toggle: int = 0


@dataclass(slots=True)
class FrameOutput:
    """What one :meth:`Fight.step` produced.

    The engine tick, the skipped routine calls and every sound request of the frame in execution
    order: handlers, script opcodes and routines (§10.32). ``tick.sounds`` stays empty.
    """

    tick: TickOutput
    skipped_calls: list[RoutineCall]
    sounds: list[SoundRequest]


def restart_from(actor: Actor, script: int) -> Restart:
    """The usual handler exit: ``script`` at the owner's position and facing (§10.18)."""
    return Restart(script, actor.x, actor.height, actor.y, actor.facing)


def idle_object(fight: Fight, handle: int) -> HandlerResult:
    """Handler of type ``$28``: clear both links and keep the script (§10.18)."""
    actor = fight.actor(handle)
    actor.hit_target = actor.attacker = 0
    return KEEP


@dataclass(slots=True)
class Fight:
    """The fight state of §10.12 / §10.18 around one :class:`ScriptEngine`.

    ``knights`` (four) and ``dragon`` are the persistent records; the fight writes into them.
    ``first`` is the active-fight block's first fighter, ``kept`` the record whose job the pause
    routine skips. ``next_creature`` is the arena's spawn routine.

    The cells of §10.19: ``tables`` holds every script and number table by key (record fields
    such as ``damage_table`` hold the key); ``inventories`` the inventories by the handle in
    ``Actor.inventory``; ``ports`` the joystick words of port 0 and 1; ``walls`` the arena's
    obstacle table; ``demo``, ``confused`` / ``confused_on`` and ``moon_frame`` as named there;
    ``hop_facing``, ``hop_table`` and ``hop_counter`` the hop cells.

    The cells of §10.20: ``rng`` is the generator of §11.2, which the map hands to the fight (the
    default seed only serves fights built on their own); ``gloated`` the brawler's gloat latch;
    ``approach`` the scratch words of the approach helpers.
    """

    image: ScriptImage
    cels: Mapping[str, CelShape]
    frame_lists: Mapping[int, tuple[str | None, ...]]
    knights: Sequence[Actor]
    dragon: Actor
    constants: FightConstants
    actors: list[Actor] = field(init=False)
    engine: ScriptEngine = field(init=False)
    handlers: dict[int, Handler] = field(default_factory=lambda: {IDLE_OBJECT_TYPE: idle_object})
    routines: dict[str, Routine] = field(init=False)
    first: int = 0
    second: int = 0
    kept: int = 0
    fight_active: bool = False
    end_timer: int = 0
    alive_now: int = 0
    max_alive: int = 0
    total: int = 0
    next_creature: Callable[[Fight], None] | None = None
    skipped: list[RoutineCall] = field(default_factory=list)
    tables: dict[int, list[int]] = field(default_factory=dict)
    inventories: dict[int, Inventory] = field(default_factory=dict)
    ports: list[int] = field(default_factory=lambda: [0, 0])
    walls: tuple[Wall, ...] = ()
    demo: bool = False
    confused: int = 0
    confused_on: bool = False
    moon_frame: int = 0
    sounds: list[SoundRequest] = field(default_factory=list)
    sound_cells: SoundCells = field(default_factory=SoundCells)
    hop_facing: int = 0
    hop_table: int = 0
    hop_counter: int = 0
    rng: Rng = field(default_factory=lambda: Rng(1))
    ai_day: int = 0
    ai_last_action: int = 0
    gloated: bool = False
    approach: ApproachCells = field(default_factory=ApproachCells)
    caster: CasterCells = field(default_factory=CasterCells)
    snatch_flags: int = 0
    flyer: FlyerCells = field(default_factory=FlyerCells)
    drake: DrakeCells = field(default_factory=DrakeCells)
    dragon_cells: DragonCells = field(default_factory=DragonCells)
    guardian: GuardianCells = field(default_factory=GuardianCells)
    beam_word: int = 0
    shake: ShakeState = field(default_factory=ShakeState)
    shake_rules: ShakeRules | None = None
    effect_vbl: int | None = None
    flights: FlightState = field(default_factory=FlightState)

    def __post_init__(self) -> None:
        if len(self.knights) != KNIGHT_COUNT:
            raise ValueError(f"a fight needs {KNIGHT_COUNT} knight records")
        pool = [Actor() for _ in range(POOL_SIZE)]
        self.actors = [*self.knights, self.dragon, *pool]
        self.engine = ScriptEngine(
            image=self.image,
            actors=self.actors,
            cels=self.cels,
            frame_lists=self.frame_lists,
            call=self._call,
            spawn=self._spawn_object,
            sound=self.play_sound,
            on_kill=self._release_flight,
        )
        self.routines = {
            FIGHTER_DIED: fighter_died,
            END_FIGHT: end_fight,
            MARK_FIRST_DEAD: mark_first_dead,
            PAUSE_ALL: pause_all,
            SET_GLOAT: set_gloat_latch,
        }

    def _release_flight(self, owner: int) -> None:
        release_flight(self.flights, owner)

    def play_sound(self, sound: int) -> None:
        """A play request on the next free channel, in execution order (§10.32)."""
        self.sounds.append(play(sound))

    # ---- records and jobs (§10.18) ---------------------------------------------------------

    def actor(self, handle: int) -> Actor:
        """The record behind a handle."""
        if not 1 <= handle <= len(self.actors):
            raise FightError(f"no actor with handle {handle}")
        return self.actors[handle - 1]

    def alloc_record(self) -> int:
        """Take the first free pool record, clear it and mark it in use."""
        for handle in range(POOL_FIRST, POOL_FIRST + POOL_SIZE):
            actor = self.actor(handle)
            if actor.active == 0:
                actor.clear()
                actor.active = 1
                return handle
        raise FightError("all 20 fight records are in use")

    def start_job(
        self,
        script: int,
        owner: int,
        frame_list: int,
        position: tuple[int, int, int],
        facing: int,
        kind: int,
    ) -> Job | None:
        """Start a job in the first free record (§10.18); ``None`` when all ten are taken."""
        job = next((j for j in self.engine.jobs if not j.active), None)
        if job is None:
            return None
        job.work.clear()
        job.script, job.owner, job.frame_list = script, owner, frame_list
        job.x, job.height, job.depth = (u16(v) for v in position)
        job.facing, job.type = facing, kind
        job.active = job.running = True
        return job

    def spawn_actor(
        self, script: int, frame_list: int, position: tuple[int, int, int], facing: int, kind: int
    ) -> int:
        """Allocate a record, place it and start its job (§10.18). Returns its handle."""
        handle = self.alloc_record()
        actor = self.actor(handle)
        actor.x, actor.height, actor.y = (u16(v) for v in position)
        actor.facing, actor.frame_list = facing, frame_list
        actor.hit_target = actor.attacker = 0
        actor.active, actor.type = 1, kind
        self.start_job(script, handle, frame_list, position, facing, kind)
        return handle

    def find_job(self, owner: int) -> Job | None:
        """The first record whose owner is ``owner``, active or not (§10.18)."""
        return next((job for job in self.engine.jobs if job.owner == owner), None)

    def toggle_pause(self, owner: int) -> None:
        """Flip the pause word of the owner's job."""
        job = self.find_job(owner)
        if job is not None:
            job.paused ^= 1

    def kill_job(self, owner: int) -> None:
        """Free the owner's job, its record and any active trajectory slot."""
        job = self.find_job(owner)
        if job is not None:
            job.active = job.running = False
            self.actor(owner).active = 0
        release_flight(self.flights, owner)

    def restart_job(self, owner: int, script: int) -> None:
        """Give the owner's job a cleared work block and a new script."""
        job = self.find_job(owner)
        if job is not None:
            job.work.clear()
            job.script, job.running = script, True

    def add_table(self, entries: Sequence[int]) -> int:
        """Store a table of longs and return its key (never 0, which means "no table")."""
        key = len(self.tables) + 1
        self.tables[key] = [u32(entry) for entry in entries]
        return key

    def register_tables(self, tables: ScriptTables) -> TableKeys:
        """Store the five tables of ``tables``; records sharing the keys share the tables."""
        return TableKeys(
            idle_script=tables.idle_script,
            after_hit_script=tables.after_hit_script,
            hurt_scripts=self.add_table(tables.hurt_scripts),
            action_scripts=self.add_table(tables.action_scripts),
            walk_scripts=self.add_table(tables.walk_scripts),
            damage_table=self.add_table(tables.damage_table),
            defence_table=self.add_table(tables.defence_table),
        )

    def table_long(self, key: int, offset: int) -> int:
        """The long at byte offset ``offset`` (a signed word) of table ``key`` (§10.19)."""
        table = self.tables.get(key)
        if table is None:
            raise FightError(f"no table with key {key}")
        index, rest = divmod(s16(offset), 4)
        if rest or not 0 <= index < len(table):
            raise FightError(f"table {key} read at byte offset {s16(offset)}: outside the table")
        return table[index]

    def inventory_byte(self, actor: Actor, slot: int) -> int:
        """The actor's inventory count at ``slot``; 0 for a record without an inventory."""
        if actor.inventory == 0:
            return 0
        inventory = self.inventories.get(actor.inventory)
        if inventory is None:
            raise FightError(f"no inventory with handle {actor.inventory}")
        return inventory.byte(slot)

    def clear_links(self) -> None:
        """Clear both links of the dragon, 20 pool records and the four knights (§10.18)."""
        for handle in (
            DRAGON,
            *range(POOL_FIRST, POOL_FIRST + POOL_SIZE),
            *range(1, KNIGHT_COUNT + 1),
        ):
            actor = self.actor(handle)
            actor.hit_target = actor.attacker = 0

    # ---- one frame -------------------------------------------------------------------------

    def job_pass(self) -> None:
        """Call the handler of every job that was hit, hit something or ended (§10.18)."""
        for job in self.engine.jobs:
            if not job.active:
                continue
            owner = self.actor(job.owner)
            if owner.hit_target == 0 and owner.attacker == 0 and job.running:
                continue
            handler = self.handlers.get(job.type)
            if handler is None:
                raise FightError(f"no handler for type ${job.type:02X}")
            self._apply(job, owner, handler(self, job.owner))

    def step(self, vbl: int) -> FrameOutput:
        """Job pass, engine tick and contact pass of one fight frame (§10.7, items 1, 2, 4)."""
        self.skipped = []
        self.sounds = []
        self.advance_vbl(vbl)
        self.beam_word = u16(vbl)
        self.job_pass()
        self.engine.vbl = vbl
        self.engine.current_knight = self.first
        tick = self.engine.tick()
        contact_scan(self.engine, self.clear_links)
        return FrameOutput(tick, self.skipped, self.sounds)

    def advance_vbl(self, vbl: int, *, ticks: int | None = None) -> None:
        """Advance presentation effects by elapsed PAL ticks, once per timestamp (§10.27)."""
        if ticks is None:
            ticks = 0 if self.effect_vbl is None else max(0, vbl - self.effect_vbl)
        if self.effect_vbl == vbl:
            ticks = 0
        if self.shake_rules is not None:
            self.shake.advance(ticks, self.shake_rules)
        self.effect_vbl = vbl

    def _apply(self, job: Job, owner: Actor, result: HandlerResult) -> None:
        if isinstance(result, Keep):
            return
        if isinstance(result, End):
            job.active = job.running = False
            owner.active = 0
            release_flight(self.flights, job.owner)
            return
        job.script = result.script
        job.x, job.height, job.depth = u16(result.x), u16(result.height), u16(result.depth)
        job.facing = result.facing
        job.running = True

    def _call(self, call: RoutineCall) -> None:
        routine = self.routines.get(call.name)
        if routine is None:
            self.skipped.append(call)
            return
        routine(self, call)

    def _spawn_object(self, script: int, job: Job) -> None:
        """``$B8``: an idle object running ``script`` where the job stands (§10.18)."""
        position = (job.x, job.height, job.depth)
        self.spawn_actor(script, job.frame_list, position, job.facing, IDLE_OBJECT_TYPE)


# ---- fight-level routines (§10.12) ----------------------------------------------------------


def end_fight(fight: Fight, _call: RoutineCall | None = None) -> None:
    """If the fight is still active: start the end delay and clear the active flag."""
    if fight.fight_active:
        fight.end_timer = fight.constants.end_delay
        fight.fight_active = False


def fighter_died(fight: Fight, _call: RoutineCall | None = None) -> None:
    """One creature fewer alive and to come; refill the wave or end the fight (§10.12)."""
    if s16(fight.actor(fight.first).hp) <= 0:
        end_fight(fight)
        return
    fight.alive_now = u16(fight.alive_now - 1)
    fight.total = u16(fight.total - 1)
    if s16(fight.total) <= 0 and fight.alive_now == 0:
        end_fight(fight)
        return
    while fight.max_alive != fight.alive_now and s16(fight.total) > 0:
        before = fight.alive_now
        if fight.next_creature is not None:
            fight.next_creature(fight)
        if fight.alive_now == before:
            raise FightError("the next-creature routine spawned nothing: the original hangs here")


def mark_first_dead(fight: Fight, _call: RoutineCall | None = None) -> None:
    """The first fighter's hit points become the dead marker."""
    fight.actor(fight.first).hp = fight.constants.dead_hp


def set_gloat_latch(fight: Fight, _call: RoutineCall | None = None) -> None:
    """Set the brawler's gloat latch (§10.20); the knight's "down hard" script calls it."""
    fight.gloated = True


def pause_all(fight: Fight, _call: RoutineCall | None = None) -> None:
    """Toggle the pause of every pool record's job except the kept one, then the dragon's."""
    for handle in range(POOL_FIRST, POOL_FIRST + POOL_SIZE):
        if handle != fight.kept:
            fight.toggle_pause(handle)
    fight.toggle_pause(DRAGON)
