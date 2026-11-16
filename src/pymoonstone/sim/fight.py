"""One fight: actor pools, job plumbing, the job pass and the fight-level routines.

Implements ARCHITECTURE.md §10.18 on top of the script engine of §10.17
(:mod:`pymoonstone.sim.fight_script`) and the contact pass (:mod:`pymoonstone.sim.fight_contact`).
Handlers (§10.16) are registered per type byte by the tasks that write them; engine routines
(``$B0``) per name. A routine without an implementation is reported in
:attr:`FrameOutput.skipped_calls` and otherwise ignored, as §10.18 states.

Actor handles: 1..4 are the knights, 5 the dragon, 6..25 the fight pool and 26 the spare record
the link clear touches. 0 is "none".
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from pymoonstone.sim.actor import Actor
from pymoonstone.sim.fight_contact import contact_scan
from pymoonstone.sim.fight_script import (
    CelShape,
    Job,
    RoutineCall,
    ScriptEngine,
    ScriptImage,
    TickOutput,
)
from pymoonstone.sim.m68k import s16, u16

KNIGHT_COUNT = 4
DRAGON = KNIGHT_COUNT + 1
POOL_FIRST = DRAGON + 1
POOL_SIZE = 20
POOL_SPARE = POOL_FIRST + POOL_SIZE
IDLE_OBJECT_TYPE = 0x28

FIGHTER_DIED = "fighter_died"
END_FIGHT = "end_fight"
MARK_FIRST_DEAD = "mark_first_dead"
PAUSE_ALL = "pause_all"


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


@dataclass(slots=True)
class FrameOutput:
    """What one :meth:`Fight.step` produced: the engine tick and the skipped routine calls."""

    tick: TickOutput
    skipped_calls: list[RoutineCall]


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
    kept: int = 0
    fight_active: bool = False
    end_timer: int = 0
    alive_now: int = 0
    max_alive: int = 0
    total: int = 0
    next_creature: Callable[[Fight], None] | None = None
    skipped: list[RoutineCall] = field(default_factory=list)

    def __post_init__(self) -> None:
        if len(self.knights) != KNIGHT_COUNT:
            raise ValueError(f"a fight needs {KNIGHT_COUNT} knight records")
        pool = [Actor() for _ in range(POOL_SIZE + 1)]
        self.actors = [*self.knights, self.dragon, *pool]
        self.engine = ScriptEngine(
            image=self.image,
            actors=self.actors,
            cels=self.cels,
            frame_lists=self.frame_lists,
            call=self._call,
            spawn=self._spawn_object,
        )
        self.routines = {
            FIGHTER_DIED: fighter_died,
            END_FIGHT: end_fight,
            MARK_FIRST_DEAD: mark_first_dead,
            PAUSE_ALL: pause_all,
        }

    # ---- records and jobs (§10.18) ---------------------------------------------------------

    def actor(self, handle: int) -> Actor:
        """The record behind a handle."""
        if not 1 <= handle <= len(self.actors):
            raise FightError(f"no actor with handle {handle}")
        return self.actors[handle - 1]

    def alloc_record(self) -> int:
        """Take the first free pool record and mark it in use."""
        for handle in range(POOL_FIRST, POOL_SPARE):
            actor = self.actor(handle)
            if actor.active == 0:
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
        """Free the owner's job and its record."""
        job = self.find_job(owner)
        if job is not None:
            job.active = job.running = False
            self.actor(owner).active = 0

    def restart_job(self, owner: int, script: int) -> None:
        """Give the owner's job a cleared work block and a new script."""
        job = self.find_job(owner)
        if job is not None:
            job.work.clear()
            job.script, job.running = script, True

    def clear_links(self) -> None:
        """Clear both links of the dragon, 21 pool records and the four knights (§10.18)."""
        for handle in (DRAGON, *range(POOL_FIRST, POOL_SPARE + 1), *range(1, KNIGHT_COUNT + 1)):
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
        self.job_pass()
        self.engine.vbl = vbl
        self.engine.current_knight = self.first
        tick = self.engine.tick()
        contact_scan(self.engine, self.clear_links)
        return FrameOutput(tick, self.skipped)

    def _apply(self, job: Job, owner: Actor, result: HandlerResult) -> None:
        if isinstance(result, Keep):
            return
        if isinstance(result, End):
            job.active = job.running = False
            owner.active = 0
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


def pause_all(fight: Fight, _call: RoutineCall | None = None) -> None:
    """Toggle the pause of every pool record's job except the kept one, then the dragon's."""
    for handle in range(POOL_FIRST, POOL_SPARE):
        if handle != fight.kept:
            fight.toggle_pause(handle)
    fight.toggle_pause(DRAGON)
