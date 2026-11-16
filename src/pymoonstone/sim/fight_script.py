"""The fight script engine: jobs, work blocks, the interpreter and one engine tick.

Implements ARCHITECTURE.md §10.17. A :class:`Job` keeps its script position, work block and
lists across ticks; nothing restarts a script except the job pass (§10.18) and the script's own
jumps. The engine never decides what a fighter does next: it runs scripts and leaves the links,
lists and bounding boxes for the handlers.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from pymoonstone.sim.m68k import s8, s16, u8, u16

if TYPE_CHECKING:
    from pymoonstone.sim.actor import Actor

JOB_COUNT = 10
LIST_ENTRIES_PER_JOB = 8
FRAME_LIST_SLOTS = 5
FACING_RIGHT = 1
FACING_LEFT = 3

# The original hangs on a script that never reaches a marker; the engine gives up after this.
MAX_INSTRUCTIONS_PER_RUN = 4096

_DRAW_HURT = 0x01
_DRAW_ATTACK = 0x02
_DRAW_STAMP = 0x10
_DRAW_TEXT = 0x20
_DRAW_NO_BOX = 0x40
_DRAW_GORE = 0x80

_MOTION_RISE = 0x01
_MOTION_FALL = 0x02
_MOTION_BACK = 0x04
_MOTION_FORWARD = 0x10
_MOTION_LOCK_VY = 0x20
_MOTION_KEEP = 0x40
_MOTION_LOCK_VX = 0x80

_MARKER = 0xFF
_LOOP2_JUMP = 0xFE
_RETURN_JUMP = 0xFD
_JUMP_NOW = 3


class ScriptError(RuntimeError):
    """A script did something the original cannot survive (bad slot, runaway loop, overflow)."""


@dataclass(slots=True)
class Work:
    """The 36-byte work block of a job (§10.17)."""

    loop_count: int = 0
    loop_active: bool = False
    loop_pos: int = 0
    loop2_count: int = 0
    loop2_active: bool = False
    loop2_pos: int = 0
    call_target: int = 0
    call_armed: bool = False
    second_armed: bool = False
    second_script: int = 0
    motion_param: int = 0
    motion_flags: int = 0
    motion_active: bool = False
    motion_count: int = 0
    vy: int = 0
    vy_limit: int = 0
    vx: int = 0
    vx_limit: int = 0
    return_pos: int = 0

    def reset(self) -> None:
        """Clear the block except the second script and its armed flag (``$D0``, ``$B4``)."""
        armed, script = self.second_armed, self.second_script
        self.clear()
        self.second_armed, self.second_script = armed, script

    def clear(self) -> None:
        """Clear the whole block (starting a job)."""
        fresh = Work()
        for name in Work.__slots__:
            setattr(self, name, getattr(fresh, name))


@dataclass(slots=True)
class Job:
    """One job record (§9.6, §10.17). ``list_base`` is its first entry in the list buffers."""

    list_base: int
    active: bool = False
    running: bool = False
    paused: int = 0
    script: int = 0
    x: int = 0
    height: int = 0
    depth: int = 0
    screen_x: int = 0
    screen_y: int = 0
    width: int = 0
    frame_height: int = 0
    frame: int = 0
    facing: int = 0
    owner: int = 0
    frame_list: int = 0
    type: int = 0
    work: Work = field(default_factory=Work)


@dataclass(slots=True, frozen=True)
class FrameShape:
    """Size of one cel frame and its hurt mask in stored orientation (§3.2, §10.17).

    ``mask_rows[row]`` holds the OR of the frame's stored planes across the 16-pixel-aligned
    row, most significant bit first: column ``c`` is bit ``aligned_width - 1 - c``.
    """

    width: int
    height: int
    mask_rows: tuple[int, ...]

    @property
    def aligned_width(self) -> int:
        """Row width in pixels rounded up to 16 (the mirror span of §3.2)."""
        return ((self.width + 15) >> 4) << 4

    def opaque(self, xw: int, yw: int, *, mirrored: bool) -> bool:
        """The contact test's pixel read at offset ``(xw, yw)`` from the cel's corner (§10.17).

        The word is addressed as the original does: byte offset ``(xw >> 4) * 2 + yw *
        row_bytes`` as a signed word, so a column past the row reads on into the next row. An
        address outside the frame's plane reads nothing.
        """
        row_bytes = self.aligned_width >> 3
        offset = s16(u16(((xw >> 4) << 1) + u16(yw * row_bytes)))
        if row_bytes == 0 or not 0 <= offset < row_bytes * self.height:
            return False
        row, byte_in_row = divmod(offset, row_bytes)
        column = byte_in_row * 8 + (xw & 15)
        bit = column if mirrored else self.aligned_width - 1 - column
        return bool((self.mask_rows[row] >> bit) & 1)


@dataclass(slots=True, frozen=True)
class HitShape:
    """One hit record of a hit set (§4.2): the coarse box and the contact points."""

    max_dx: int
    max_dy: int
    points: tuple[tuple[int, int], ...]


@dataclass(slots=True, frozen=True)
class CelShape:
    """What the engine needs of one cel file: frame sizes, masks and its hit set, if any."""

    name: str
    frames: tuple[FrameShape, ...]
    hits: tuple[HitShape | None, ...] | None


@dataclass(slots=True, frozen=True)
class ScriptImage:
    """The script hunk of ``mog`` and the engine routines its ``$B0`` operands name.

    ``routines`` maps the position of a ``$B0`` operand to the routine's name (§10.18).
    """

    data: bytes
    routines: Mapping[int, str]


@dataclass(slots=True, frozen=True)
class ListEntry:
    """One attack or hurt list entry: the cel, its frame and the screen position (§10.17)."""

    cel: str
    frame: int
    x: int
    y: int


@dataclass(slots=True, frozen=True)
class Draw:
    """One cel drawn this tick, in drawing order (§10.17).

    ``mirrored`` is the frame's state when drawn; ``stamp`` means it was also stamped into the
    background picture; ``text`` is the draw's text flag. Positions are 16-bit screen words.
    """

    cel: str
    frame: int
    x: int
    y: int
    mirrored: bool
    stamp: bool
    text: bool


@dataclass(slots=True, frozen=True)
class RoutineCall:
    """A ``$B0`` call: the routine, the owner and the job's frame list, position and facing."""

    name: str
    owner: int
    frame_list: int
    x: int
    height: int
    depth: int
    facing: int


@dataclass(slots=True)
class BoxCells:
    """The engine's bounding-box cells (§10.17); written to the owner at every write-back."""

    armed: bool = False
    min_x: int = 0
    max_x: int = 0
    min_y: int = 0
    max_y: int = 0

    def clear(self) -> None:
        """Disarm and zero the four edges (start of a main-pass job)."""
        self.armed = False
        self.min_x = self.max_x = self.min_y = self.max_y = 0

    def grow(self, x: int, width: int, y: int, height: int) -> None:
        """Grow by one draw with the original's signed compares."""
        if not self.armed:
            self.min_x = self.max_x = x
            self.min_y = self.max_y = y
            self.armed = True
        if not s16(x) > s16(self.min_x):
            self.min_x = x
        right = u16(x + width)
        if not s16(right) < s16(self.max_x):
            self.max_x = right
        if not s16(y) > s16(self.min_y):
            self.min_y = y
        bottom = u16(y + height)
        if not s16(bottom) < s16(self.max_y):
            self.max_y = bottom


class ListBuffer:
    """One of the two 80-entry list buffers, eight entries per job (§10.17)."""

    def __init__(self) -> None:
        self.entries: list[ListEntry | None] = [None] * (JOB_COUNT * LIST_ENTRIES_PER_JOB)
        self.cursor: int | None = None

    def append(self, entry: ListEntry) -> None:
        """Write at the cursor and advance it."""
        if self.cursor is None or self.cursor >= len(self.entries):
            raise ScriptError(f"list entry written outside the buffer at {self.cursor}")
        self.entries[self.cursor] = entry
        self.cursor += 1

    def read(self, base: int) -> list[ListEntry]:
        """Entries from ``base`` up to the first empty one (it may run into the next job's)."""
        found: list[ListEntry] = []
        for entry in self.entries[base:]:
            if entry is None:
                break
            found.append(entry)
        return found

    def clear(self) -> None:
        """Empty every entry (end of the contact pass); the cursor stays."""
        self.entries = [None] * len(self.entries)


@dataclass(slots=True)
class TickOutput:
    """What one engine tick produced, in order."""

    draws: list[Draw] = field(default_factory=list)
    sounds: list[int] = field(default_factory=list)
    calls: list[RoutineCall] = field(default_factory=list)


@dataclass(slots=True)
class ScriptEngine:
    """All engine cells of §10.17: the job table, lists, box, scratch block and the cel state.

    ``frame_lists`` holds the four frame-set lists (§10.15) by number 1..4. ``call`` runs a
    ``$B0`` routine and ``spawn`` a ``$B8`` object; both are supplied by the fight (§10.18).
    """

    image: ScriptImage
    actors: Sequence[Actor]
    cels: Mapping[str, CelShape]
    frame_lists: Mapping[int, tuple[str | None, ...]]
    call: Callable[[RoutineCall], None]
    spawn: Callable[[int, Job], None]
    jobs: list[Job] = field(
        default_factory=lambda: [Job(list_base=i * LIST_ENTRIES_PER_JOB) for i in range(JOB_COUNT)]
    )
    attack: ListBuffer = field(default_factory=ListBuffer)
    hurt: ListBuffer = field(default_factory=ListBuffer)
    box: BoxCells = field(default_factory=BoxCells)
    scratch_work: Work = field(default_factory=Work)
    mirrored: set[tuple[str, int]] = field(default_factory=set)
    gore_off: bool = False
    vbl: int = 0
    current_knight: int = 0
    output: TickOutput = field(default_factory=TickOutput)

    def owner(self, job: Job) -> Actor:
        """The actor a job runs for (handle 1 is ``actors[0]``)."""
        return self.actors[job.owner - 1]

    def tick(self) -> TickOutput:
        """One engine tick: sort, second-script pass, main pass (§10.17)."""
        self.output = TickOutput()
        sort_jobs(self.jobs)
        for job in self.jobs:
            if job.active and not job.paused and job.work.second_armed:
                scratch = replace(job, height=0, script=job.work.second_script)
                scratch.work = self.scratch_work
                run_job(self, scratch)
        for job in self.jobs:
            if job.active and not job.paused:
                self.attack.cursor = job.list_base
                self.hurt.cursor = job.list_base
                self.box.clear()
                run_job(self, job)
        return self.output


def sort_jobs(jobs: list[Job]) -> None:
    """Bubble the records by unsigned depth until a sweep swaps nothing (§10.17)."""
    swapped = True
    while swapped:
        swapped = False
        for index in range(len(jobs) - 1):
            if u16(jobs[index + 1].depth) < u16(jobs[index].depth):
                jobs[index], jobs[index + 1] = jobs[index + 1], jobs[index]
                swapped = True


def run_job(engine: ScriptEngine, job: Job) -> None:
    """Run ``job`` up to and including its next frame marker (§10.17)."""
    data = engine.image.data
    for _ in range(MAX_INSTRUCTIONS_PER_RUN):
        if job.script == 0:
            return
        op = _byte(data, job.script)
        if op < 0x80:
            _draw(engine, job, job.script)
            job.script += 6
            continue
        handler = _HANDLERS.get(op)
        if handler is None or handler(engine, job, job.script):
            return
    raise ScriptError(f"script ran {MAX_INSTRUCTIONS_PER_RUN} instructions without a marker")


# ---- reading -------------------------------------------------------------------------------


def _byte(data: bytes, pos: int) -> int:
    if not 0 <= pos < len(data):
        raise ScriptError(f"script position {pos} outside the script hunk")
    return data[pos]


def _word(data: bytes, pos: int) -> int:
    return (_byte(data, pos) << 8) | _byte(data, pos + 1)


def _long(data: bytes, pos: int) -> int:
    return (_word(data, pos) << 16) | _word(data, pos + 2)


# ---- draw ----------------------------------------------------------------------------------


def _draw(engine: ScriptEngine, job: Job, pos: int) -> None:
    data = engine.image.data
    op, frame, dy, flags = (_byte(data, pos + i) for i in range(4))
    cel = _cel_for(engine, job, (op & 0x1F) >> 2)
    if frame >= len(cel.frames):
        raise ScriptError(f"draw at {pos}: frame {frame} outside {cel.name}")
    shape = cel.frames[frame]
    job.frame = frame
    key = (cel.name, frame)
    wanted = FACING_LEFT if key in engine.mirrored else FACING_RIGHT
    if wanted != job.facing:
        engine.mirrored ^= {key}
    dx = _word(data, pos + 4)
    x = u16(job.x - dx - shape.width) if job.facing & 2 else u16(dx + job.x)
    y = u16(s8(dy) + job.height + job.depth)
    job.screen_x, job.screen_y = x, y
    job.width, job.frame_height = shape.width, shape.height
    if not flags & _DRAW_NO_BOX:
        engine.box.grow(x, shape.width, y, shape.height)
    if engine.gore_off and flags & _DRAW_GORE:
        return
    _emit(engine, Draw(cel.name, frame, x, y, key in engine.mirrored, False, False), flags)


def _emit(engine: ScriptEngine, draw: Draw, flags: int) -> None:
    """Lists, stamp, text flag and the draw itself, for a draw that is not skipped (§10.17)."""
    if flags & _DRAW_ATTACK:
        engine.attack.append(ListEntry(draw.cel, draw.frame, draw.x, draw.y))
    if flags & _DRAW_HURT:
        engine.hurt.append(ListEntry(draw.cel, draw.frame, draw.x, draw.y))
    stamp, text = bool(flags & _DRAW_STAMP), bool(flags & _DRAW_TEXT)
    engine.output.draws.append(replace(draw, stamp=stamp, text=text))


def _cel_for(engine: ScriptEngine, job: Job, slot: int) -> CelShape:
    entries = engine.frame_lists.get(job.frame_list)
    name = entries[slot] if entries is not None and slot < len(entries) else None
    if name is None:
        raise ScriptError(f"draw names slot {slot} of frame list {job.frame_list}: not loaded")
    return engine.cels[name]


# ---- frame marker and motion ---------------------------------------------------------------


def _write_back(engine: ScriptEngine, job: Job) -> None:
    owner = engine.owner(job)
    owner.x, owner.height, owner.y = job.x, job.height, job.depth
    owner.facing = job.facing
    box = engine.box
    owner.box_min_x, owner.box_max_x = box.min_x, box.max_x
    owner.box_min_y, owner.box_max_y = box.min_y, box.max_y


def _loop2_repeats(work: Work) -> bool:
    if work.loop2_active:
        work.loop2_count = u8(work.loop2_count - 1)
        if work.loop2_count != 0:
            return True
    work.loop2_active = False
    return False


def _marker_target(job: Job, next_byte: int) -> None:
    """Steps 1-6 of the frame marker (§10.17): move the script position or end the script."""
    work = job.work
    if work.loop_active:
        work.loop_count = u8(work.loop_count - 1)
        if work.loop_count != 0:
            job.script = work.loop_pos
            return
    work.loop_active = False
    if work.motion_active:
        work.motion_count = u8(work.motion_count - 1)
        if s8(work.motion_count) >= 0:
            motion_step(job)
            return
    if work.call_armed:
        work.call_armed = False
        job.script = work.call_target
        return
    if next_byte == _MARKER:
        if _loop2_repeats(work):
            job.script = work.loop2_pos
        else:
            job.running = False
        return
    if next_byte == _LOOP2_JUMP and _loop2_repeats(work):
        job.script = work.loop2_pos
        return
    job.script += 2


def _op_marker(engine: ScriptEngine, job: Job, pos: int) -> bool:
    _marker_target(job, _byte(engine.image.data, pos + 1))
    _write_back(engine, job)
    return True


def _vertical(job: Job, work: Work, v: int) -> tuple[int, bool]:
    if work.motion_flags & _MOTION_FALL:
        job.height = u16(job.height + v)
        if s16(job.height) >= 0:
            job.height = 0
            work.motion_flags &= ~_MOTION_FALL & 0xFF
            return v, True
        v = job.height
        if not work.motion_flags & _MOTION_LOCK_VY and s8(v) < s8(work.vy_limit):
            v = (v & 0xFF00) | u8(v << 1)
            work.vy = v & 0xFF
        return v, True
    if work.motion_flags & _MOTION_RISE:
        job.height = u16(job.height - v)
        if not work.motion_flags & _MOTION_LOCK_VY and s8(v) > s8(work.vy_limit):
            v = (v & 0xFF00) | ((v & 0xFF) >> 1)
            work.vy = v & 0xFF
        return v, True
    return v, False


def _horizontal(job: Job, work: Work, v: int, *, subtract: bool) -> int:
    job.x = u16(job.x - v) if subtract else u16(job.x + v)
    if not work.motion_flags & _MOTION_LOCK_VX and s8(v) > s8(work.vx_limit):
        v = (v & 0xFF00) | ((v & 0xFF) >> 1)
        work.vx = v & 0xFF
    return v


def motion_step(job: Job) -> None:
    """One step of the ``$8C`` motion block, with the original's register quirks (§10.17)."""
    work = job.work
    v, moved = _vertical(job, work, work.vy)
    v = (v & 0xFF00) | work.vx
    left = bool(job.facing & 2)
    if work.motion_flags & _MOTION_FORWARD:
        v = _horizontal(job, work, v, subtract=left)
        moved = True
    if work.motion_flags & _MOTION_BACK:
        v = _horizontal(job, work, v, subtract=not left)
        moved = True
    if not work.motion_flags & _MOTION_KEEP:
        job.script = work.return_pos
    if not moved:
        work.motion_active = False


# ---- control opcodes -----------------------------------------------------------------------


def _op_jump_saved(engine: ScriptEngine, job: Job, pos: int) -> bool:
    op = _byte(engine.image.data, pos)
    job.script = job.work.return_pos if op == _RETURN_JUMP else job.work.loop2_pos
    return job.script == 0


def _op_facing(engine: ScriptEngine, job: Job, pos: int) -> bool:
    value = _byte(engine.image.data, pos + 1)
    job.facing = job.facing ^ 2 if value == 0xFF else value
    engine.owner(job).facing = job.facing
    job.script += 2
    return False


def _op_jump(engine: ScriptEngine, job: Job, pos: int) -> bool:
    data = engine.image.data
    target = _long(data, pos + 2)
    if _byte(data, pos + 1) == _JUMP_NOW:
        job.script = target
    else:
        job.work.call_target, job.work.call_armed = target, True
        job.script += 6
    return False


def _op_loop(engine: ScriptEngine, job: Job, pos: int) -> bool:
    count = _byte(engine.image.data, pos + 1)
    if count == 0:
        count = (engine.vbl & 0x1F) or 1
    job.work.loop_count, job.work.loop_active = count, True
    job.script += 2
    job.work.loop_pos = job.script
    return False


def _op_motion(engine: ScriptEngine, job: Job, pos: int) -> bool:
    work = job.work
    param, count, flags, vy, vy_limit, vx, vx_limit = engine.image.data[pos + 1 : pos + 8]
    work.motion_active = True
    work.motion_param, work.motion_count, work.motion_flags = param, count, flags
    work.vy, work.vy_limit, work.vx, work.vx_limit = vy, vy_limit, vx, vx_limit
    job.script += 8
    work.return_pos = job.script
    return False


def _op_loop2(engine: ScriptEngine, job: Job, pos: int) -> bool:
    job.work.loop2_count = _byte(engine.image.data, pos + 1)
    job.work.loop2_active = True
    job.script += 2
    job.work.loop2_pos = job.script
    return False


def _op_gore_jump(engine: ScriptEngine, job: Job, pos: int) -> bool:
    job.script = _long(engine.image.data, pos + 2) if engine.gore_off else job.script + 6
    return False


def _op_move(engine: ScriptEngine, job: Job, pos: int) -> bool:
    data = engine.image.data
    flags = _byte(data, pos + 1)
    dx, dh, dd = _word(data, pos + 2), _word(data, pos + 4), _word(data, pos + 6)
    if flags & 0x40:
        job.x, job.height, job.depth = dx, dh, dd
    else:
        add_x = not flags & 1 if job.facing == FACING_LEFT else bool(flags & 1)
        job.x = u16(job.x + dx) if add_x else u16(job.x - dx)
        job.height = u16(job.height - dh) if flags & 0x08 else u16(job.height + dh)
        job.depth = u16(job.depth - dd) if flags & 0x20 else u16(job.depth + dd)
    job.script += 8
    return False


def _op_sound(engine: ScriptEngine, job: Job, pos: int) -> bool:
    engine.output.sounds.append(_byte(engine.image.data, pos + 1))
    job.script += 2
    return False


def _owner_size(size_flags: int) -> int:
    if size_flags & 1:
        return 1
    return 2 if size_flags & 2 else 4


def _op_poke(engine: ScriptEngine, job: Job, pos: int) -> bool:
    data = engine.image.data
    size = _owner_size(_byte(data, pos + 1))
    engine.owner(job).write(s16(_word(data, pos + 2)), size, _long(data, pos + 4))
    job.script += 8
    return False


def _op_second(engine: ScriptEngine, job: Job, pos: int) -> bool:
    data = engine.image.data
    job.work.second_script = _long(data, pos + 2)
    job.work.second_armed = _byte(data, pos + 1) != 0
    job.script += 6
    return False


def _op_call(engine: ScriptEngine, job: Job, pos: int) -> bool:
    name = engine.image.routines.get(pos + 2)
    if name is None:
        raise ScriptError(f"$B0 at {pos} names no known engine routine")
    engine.call(
        RoutineCall(name, job.owner, job.frame_list, job.x, job.height, job.depth, job.facing)
    )
    job.script += 6
    return False


def _op_if_dead(engine: ScriptEngine, job: Job, pos: int) -> bool:
    if s16(engine.owner(job).hp) > 0:
        job.script += 6
    else:
        job.script = _long(engine.image.data, pos + 2)
        job.work.reset()
    return False


def _op_spawn(engine: ScriptEngine, job: Job, pos: int) -> bool:
    engine.spawn(_long(engine.image.data, pos + 2), job)
    job.script += 6
    return False


def _op_kill(engine: ScriptEngine, job: Job, _pos: int) -> bool:
    engine.owner(job).active = 0
    job.active = job.running = False
    job.script += 2
    return False


def _op_frame_set(engine: ScriptEngine, job: Job, pos: int) -> bool:
    number = _byte(engine.image.data, pos + 1)
    if number not in engine.frame_lists:
        raise ScriptError(f"$C0 at {pos} selects frame list {number}, which does not exist")
    job.frame_list = number
    engine.owner(job).frame_list = number
    job.script += 2
    return False


def _op_if_same_facing(engine: ScriptEngine, job: Job, pos: int) -> bool:
    other = next((j for j in engine.jobs if j.owner == engine.current_knight), None)
    if other is not None and other.facing == job.facing:
        job.script = _long(engine.image.data, pos + 2)
    else:
        job.script += 6
    return False


def _owner_value(engine: ScriptEngine, job: Job, pos: int) -> int:
    data = engine.image.data
    size = _owner_size(_byte(data, pos + 1))
    return engine.owner(job).read(s16(_word(data, pos + 2)), size)


def _op_if_zero(engine: ScriptEngine, job: Job, pos: int) -> bool:
    if _owner_value(engine, job, pos) == 0:
        job.script = _long(engine.image.data, pos + 4)
    else:
        job.script += 8
    return False


def _op_if_nonzero(engine: ScriptEngine, job: Job, pos: int) -> bool:
    if _owner_value(engine, job, pos) != 0:
        job.script = _long(engine.image.data, pos + 4)
    else:
        job.script += 8
    return False


def _op_reset(_engine: ScriptEngine, job: Job, _pos: int) -> bool:
    job.work.reset()
    job.script += 2
    return False


_Handler = Callable[[ScriptEngine, Job, int], bool]

_HANDLERS: dict[int, _Handler] = {
    0x80: _op_facing,
    0x84: _op_jump,
    0x88: _op_loop,
    0x8C: _op_motion,
    0x94: _op_loop2,
    0x98: _op_gore_jump,
    0xA0: _op_move,
    0xA4: _op_sound,
    0xA8: _op_poke,
    0xAC: _op_second,
    0xB0: _op_call,
    0xB4: _op_if_dead,
    0xB8: _op_spawn,
    0xBC: _op_kill,
    0xC0: _op_frame_set,
    0xC4: _op_if_same_facing,
    0xC8: _op_if_zero,
    0xCC: _op_if_nonzero,
    0xD0: _op_reset,
    _RETURN_JUMP: _op_jump_saved,
    _LOOP2_JUMP: _op_jump_saved,
    _MARKER: _op_marker,
}
