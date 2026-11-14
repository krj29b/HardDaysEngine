"""Pure-integer 50 Hz combat animation & bytecode VM (ARCHITECTURE.md §7.1-§7.3)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pymoonstone.sim.collision import (
    DRAW_FLAG_ATTACK,
    DRAW_FLAG_HURTBOX,
    DRAW_FLAG_NO_BODY_BOX,
)
from pymoonstone.sim.m68k import s8, s16, u16, u32
from pymoonstone.sim.types import Direction, EntityId, Pos25D

if TYPE_CHECKING:
    from pymoonstone.sim.prng import SimPrng

VBL_DIVISOR_NORMAL: int = 6
VBL_DIVISOR_FAST: int = 5
MAX_COMBAT_JOBS: int = 10
DEFAULT_MAX_OPS_PER_FRAME: int = 256

DRAW_OPCODE_MAX: int = 0x1F
DRAW_INSTRUCTION_SIZE: int = 6

DRAW_FLAG_FLIP_X: int = 0x04
DRAW_FLAG_STAMP_BG: int = 0x20
DRAW_FLAG_FG_MASK: int = 0x80

OP_END_SCRIPT: int = 0x80
OP_JUMP: int = 0x81
OP_FREE_JOB: int = 0x82
OP_LOOP_BEGIN: int = 0x83
OP_LOOP_END: int = 0x84
OP_BRANCH_REL: int = 0x85
OP_BRANCH_RAND: int = 0x86
OP_CALL: int = 0x87
OP_RETURN: int = 0x88
OP_SET_VELOCITY: int = 0x89
OP_MOVE: int = 0x8A
OP_SOUND: int = 0x8E
OP_SPAWN_PROJECTILE: int = 0x90
OP_SPAWN_EFFECT: int = 0x91
OP_SCREEN_SHAKE: int = 0x92
OP_SCREEN_FLASH: int = 0x93
OP_SET_FACING: int = 0x94
OP_HOOK_MAX: int = 0xD4
OP_END_FRAME: int = 0xFF


class ScriptError(ValueError):
    """Raised when combat bytecode is truncated, out of bounds, or loops without yielding."""


@dataclass(slots=True, frozen=True)
class ArenaBounds:
    """Optional integer arena bounds for ``0x8A`` movement and velocity clipping."""

    min_x: int
    max_x: int
    min_y: int
    max_y: int
    min_z: int = 0
    max_z: int = 200

    def clamp(self, x: int, y: int, z: int) -> tuple[int, int, int]:
        """Clamp ``(x, y, z)`` to inclusive arena bounds."""
        cx = max(self.min_x, min(self.max_x, x))
        cy = max(self.min_y, min(self.max_y, y))
        cz = max(self.min_z, min(self.max_z, z))
        return cx, cy, cz


@dataclass(slots=True, frozen=True)
class DrawFrameEvent:
    """Structured cel draw request emitted by ``0x00..0x1F`` (ARCHITECTURE.md §7.2)."""

    job_id: int
    cel_slot: int
    frame_index: int
    screen_x: int
    screen_y: int
    dx: int
    dy: int
    draw_flags: int
    flipped: bool
    hurtbox: bool
    attack: bool
    exclude_body_box: bool
    stamp_bg: bool
    fg_masked: bool


@dataclass(slots=True, frozen=True)
class SoundEvent:
    """Synth sound effect trigger emitted by ``0x8E`` (ARCHITECTURE.md §7.2)."""

    job_id: int
    sound_id: int


@dataclass(slots=True, frozen=True)
class SpawnEvent:
    """Projectile or visual-effect spawn request emitted by ``0x90`` / ``0x91``."""

    job_id: int
    spawn_kind: str
    type_id: int
    x: int
    y: int
    z: int
    direction: Direction
    owner_id: EntityId | None


@dataclass(slots=True, frozen=True)
class MoveEvent:
    """Position update emitted by ``0x8A`` or velocity integration."""

    job_id: int
    dx: int
    dy: int
    dz: int
    x: int
    y: int
    z: int


@dataclass(slots=True, frozen=True)
class BranchEvent:
    """Control-flow jump/branch/call/return emitted by ``0x81``, ``0x84..0x88``."""

    job_id: int
    opcode: int
    from_pc: int
    target_pc: int
    taken: bool


@dataclass(slots=True, frozen=True)
class ScreenEffectEvent:
    """Screen shake or palette flash request emitted by ``0x92`` / ``0x93``."""

    job_id: int
    effect: str
    value: int
    duration_vbls: int


@dataclass(slots=True, frozen=True)
class CombatHookEvent:
    """Generic combat hook notification for ``0x95..0xD4`` opcodes."""

    job_id: int
    opcode: int


@dataclass(slots=True, frozen=True)
class FrameCompleteEvent:
    """End-of-frame marker ``0xFF`` reached (ARCHITECTURE.md §7.2)."""

    job_id: int
    next_pc: int


@dataclass(slots=True, frozen=True)
class ScriptEndedEvent:
    """Script terminated by ``0x80`` or end of buffer (ARCHITECTURE.md §7.2)."""

    job_id: int
    pc: int


@dataclass(slots=True, frozen=True)
class JobFreedEvent:
    """Job and owner freed by ``0x82`` (ARCHITECTURE.md §7.2)."""

    job_id: int
    owner_id: EntityId | None


CombatEvent = (
    DrawFrameEvent
    | SoundEvent
    | SpawnEvent
    | MoveEvent
    | BranchEvent
    | ScreenEffectEvent
    | CombatHookEvent
    | FrameCompleteEvent
    | ScriptEndedEvent
    | JobFreedEvent
)


@dataclass(slots=True)
class CombatWork:
    """36-byte ``CombatWork`` scratch block per job (ARCHITECTURE.md §7.2-§7.3)."""

    loop_count: int = 0
    loop_active: bool = False
    loop_pc: int = 0
    loop2_count: int = 0
    loop2_active: bool = False
    loop2_pc: int = 0
    call_pc: int = 0
    call_armed: bool = False
    spawn_armed: bool = False
    spawn_script_id: int = 0
    motion_flags: int = 0
    motion_active: bool = False
    motion_count: int = 0
    return_pc: int = 0
    call_stack: list[int] = field(default_factory=list)


@dataclass(slots=True)
class CombatJob:
    """50-byte ``CombatJob`` slot state (ARCHITECTURE.md §7.1-§7.3).

    Coordinates follow :class:`pymoonstone.sim.types.Pos25D`: ``x`` is horizontal,
    ``y`` is ground depth, and ``z`` is altitude above ground (``screen_y = y - z``).
    """

    job_id: int = 0
    active: bool = True
    running: bool = True
    paused: int = 0
    script_id: int = 0
    pc: int = 0
    x: int = 0
    y: int = 0
    z: int = 0
    vx: int = 0
    vy: int = 0
    vz: int = 0
    direction: Direction = Direction.RIGHT
    cel_slot: int = 0
    frame_index: int = 0
    draw_flags: int = 0
    screen_x: int = 0
    screen_y: int = 0
    job_type: int = 0
    frame_divisor: int = VBL_DIVISOR_NORMAL
    vbl_countdown: int = 1
    loop_counter: int = 0
    owner_id: EntityId | None = None
    link_id: EntityId | None = None
    work: CombatWork = field(default_factory=CombatWork)

    def __post_init__(self) -> None:
        if self.frame_divisor <= 0:
            raise ValueError(f"frame_divisor must be positive, got {self.frame_divisor}")

    @property
    def pos(self) -> Pos25D:
        """Return current 2.5D integer world position."""
        return Pos25D(self.x, self.y, self.z)

    @property
    def facing_left(self) -> bool:
        """True when facing left (bit 1 of ``ubFlags`` set)."""
        return self.direction.is_left

    def set_speed_mode(self, *, fast: bool) -> None:
        """Switch between 6-VBL normal and 5-VBL fast combat timing (ARCHITECTURE.md §7.1)."""
        self.frame_divisor = VBL_DIVISOR_FAST if fast else VBL_DIVISOR_NORMAL


def effective_flip_x(direction: Direction, draw_flags: int) -> bool:
    """XOR horizontal flip bit ``0x04`` with job facing (ARCHITECTURE.md §7.2)."""
    return direction.is_left ^ bool(draw_flags & DRAW_FLAG_FLIP_X)


def mirrored_dx(direction: Direction, dx: int) -> int:
    """Negate horizontal displacement ``dx`` when the job faces left (ARCHITECTURE.md §7.2)."""
    return -dx if direction.is_left else dx


def _read_u8(script: bytes, pc: int) -> int:
    if pc < 0 or pc >= len(script):
        raise ScriptError(f"unexpected end of script reading u8 at offset {pc}")
    return script[pc]


def _read_s8(script: bytes, pc: int) -> int:
    return s8(_read_u8(script, pc))


def _read_u16(script: bytes, pc: int) -> int:
    if pc < 0 or pc + 2 > len(script):
        raise ScriptError(f"unexpected end of script reading BE16 at offset {pc}")
    return u16((script[pc] << 8) | script[pc + 1])


def _read_s16(script: bytes, pc: int) -> int:
    return s16(_read_u16(script, pc))


def _read_u32(script: bytes, pc: int) -> int:
    if pc < 0 or pc + 4 > len(script):
        raise ScriptError(f"unexpected end of script reading BE32 at offset {pc}")
    return u32((script[pc] << 24) | (script[pc + 1] << 16) | (script[pc + 2] << 8) | script[pc + 3])


def _validate_target(script: bytes, target: int) -> int:
    if target < 0 or target > len(script):
        raise ScriptError(f"jump target {target} out of range 0..{len(script)}")
    return target


@dataclass(slots=True)
class _StepContext:
    job: CombatJob
    script: bytes
    prng: SimPrng | None
    bounds: ArenaBounds | None
    events: list[CombatEvent] = field(default_factory=list)
    yielded: bool = False


_OpcodeHandler = Callable[[_StepContext], None]


def _handle_draw(ctx: _StepContext) -> None:
    job = ctx.job
    script = ctx.script
    pc = job.pc
    if pc + DRAW_INSTRUCTION_SIZE > len(script):
        raise ScriptError(f"truncated draw instruction at offset {pc}")

    cel_slot = script[pc]
    frame_index = script[pc + 1]
    dy = _read_s8(script, pc + 2)
    draw_flags = script[pc + 3]
    dx = _read_s16(script, pc + 4)

    eff_dx = mirrored_dx(job.direction, dx)
    flipped = effective_flip_x(job.direction, draw_flags)
    draw_x = job.x + eff_dx
    draw_y = job.pos.screen_y + dy

    job.cel_slot = cel_slot
    job.frame_index = frame_index
    job.draw_flags = draw_flags
    job.screen_x = draw_x
    job.screen_y = draw_y
    job.pc = pc + DRAW_INSTRUCTION_SIZE

    ctx.events.append(
        DrawFrameEvent(
            job_id=job.job_id,
            cel_slot=cel_slot,
            frame_index=frame_index,
            screen_x=draw_x,
            screen_y=draw_y,
            dx=eff_dx,
            dy=dy,
            draw_flags=draw_flags,
            flipped=flipped,
            hurtbox=bool(draw_flags & DRAW_FLAG_HURTBOX),
            attack=bool(draw_flags & DRAW_FLAG_ATTACK),
            exclude_body_box=bool(draw_flags & DRAW_FLAG_NO_BODY_BOX),
            stamp_bg=bool(draw_flags & DRAW_FLAG_STAMP_BG),
            fg_masked=bool(draw_flags & DRAW_FLAG_FG_MASK),
        )
    )


def _handle_end_frame(ctx: _StepContext) -> None:
    ctx.job.pc += 1
    ctx.yielded = True
    ctx.events.append(FrameCompleteEvent(job_id=ctx.job.job_id, next_pc=ctx.job.pc))


def _handle_end_script(ctx: _StepContext) -> None:
    ctx.job.pc += 1
    ctx.job.running = False
    ctx.yielded = True
    ctx.events.append(ScriptEndedEvent(job_id=ctx.job.job_id, pc=ctx.job.pc))


def _handle_jump(ctx: _StepContext) -> None:
    from_pc = ctx.job.pc
    target = _validate_target(ctx.script, _read_u32(ctx.script, from_pc + 1))
    ctx.job.pc = target
    ctx.events.append(
        BranchEvent(
            job_id=ctx.job.job_id,
            opcode=OP_JUMP,
            from_pc=from_pc,
            target_pc=target,
            taken=True,
        )
    )


def _handle_free_job(ctx: _StepContext) -> None:
    ctx.job.pc += 1
    ctx.job.active = False
    ctx.job.running = False
    ctx.yielded = True
    ctx.events.append(JobFreedEvent(job_id=ctx.job.job_id, owner_id=ctx.job.owner_id))


def _handle_loop_begin(ctx: _StepContext) -> None:
    count = _read_u8(ctx.script, ctx.job.pc + 1)
    if count == 0:
        count = ctx.prng.rand_range(1, 32) if ctx.prng is not None else 1
    ctx.job.pc += 2
    ctx.job.loop_counter = count
    ctx.job.work.loop_count = count
    ctx.job.work.loop_active = True
    ctx.job.work.loop_pc = ctx.job.pc


def _handle_loop_end(ctx: _StepContext) -> None:
    from_pc = ctx.job.pc
    work = ctx.job.work
    if work.loop_active and work.loop_count > 1:
        work.loop_count -= 1
        ctx.job.loop_counter = work.loop_count
        target = _validate_target(ctx.script, work.loop_pc)
        ctx.job.pc = target
        ctx.events.append(
            BranchEvent(
                job_id=ctx.job.job_id,
                opcode=OP_LOOP_END,
                from_pc=from_pc,
                target_pc=target,
                taken=True,
            )
        )
        return
    work.loop_active = False
    work.loop_count = 0
    ctx.job.loop_counter = 0
    ctx.job.pc = from_pc + 1
    ctx.events.append(
        BranchEvent(
            job_id=ctx.job.job_id,
            opcode=OP_LOOP_END,
            from_pc=from_pc,
            target_pc=ctx.job.pc,
            taken=False,
        )
    )


def _handle_branch_rel(ctx: _StepContext) -> None:
    from_pc = ctx.job.pc
    offset = _read_s16(ctx.script, from_pc + 1)
    target = _validate_target(ctx.script, from_pc + 3 + offset)
    ctx.job.pc = target
    ctx.events.append(
        BranchEvent(
            job_id=ctx.job.job_id,
            opcode=OP_BRANCH_REL,
            from_pc=from_pc,
            target_pc=target,
            taken=True,
        )
    )


def _handle_branch_rand(ctx: _StepContext) -> None:
    from_pc = ctx.job.pc
    threshold = _read_u8(ctx.script, from_pc + 1)
    offset = _read_s16(ctx.script, from_pc + 2)
    next_pc = from_pc + 4
    roll = ctx.prng.rand_range(256) if ctx.prng is not None else 0
    taken = roll < threshold
    target = _validate_target(ctx.script, next_pc + offset) if taken else next_pc
    ctx.job.pc = target
    ctx.events.append(
        BranchEvent(
            job_id=ctx.job.job_id,
            opcode=OP_BRANCH_RAND,
            from_pc=from_pc,
            target_pc=target,
            taken=taken,
        )
    )


def _handle_call(ctx: _StepContext) -> None:
    from_pc = ctx.job.pc
    target = _validate_target(ctx.script, _read_u32(ctx.script, from_pc + 1))
    return_pc = from_pc + 5
    ctx.job.work.call_stack.append(return_pc)
    ctx.job.work.return_pc = return_pc
    ctx.job.pc = target
    ctx.events.append(
        BranchEvent(
            job_id=ctx.job.job_id,
            opcode=OP_CALL,
            from_pc=from_pc,
            target_pc=target,
            taken=True,
        )
    )


def _handle_return(ctx: _StepContext) -> None:
    from_pc = ctx.job.pc
    work = ctx.job.work
    if not work.call_stack:
        raise ScriptError(f"return with empty call stack at offset {from_pc}")
    target = _validate_target(ctx.script, work.call_stack.pop())
    ctx.job.pc = target
    ctx.events.append(
        BranchEvent(
            job_id=ctx.job.job_id,
            opcode=OP_RETURN,
            from_pc=from_pc,
            target_pc=target,
            taken=True,
        )
    )


def _handle_set_velocity(ctx: _StepContext) -> None:
    pc = ctx.job.pc
    flags = _read_u8(ctx.script, pc + 1)
    vx = _read_s16(ctx.script, pc + 2)
    vy = _read_s16(ctx.script, pc + 4)
    vz = _read_s16(ctx.script, pc + 6)
    ctx.job.vx = mirrored_dx(ctx.job.direction, vx) if (flags & 0x01) else vx
    ctx.job.vy = vy
    ctx.job.vz = vz
    ctx.job.work.motion_flags = flags
    ctx.job.work.motion_active = True
    ctx.job.pc = pc + 8


def _handle_move(ctx: _StepContext) -> None:
    pc = ctx.job.pc
    raw_dx = _read_s16(ctx.script, pc + 1)
    dz = _read_s16(ctx.script, pc + 3)
    eff_dx = mirrored_dx(ctx.job.direction, raw_dx)
    new_x = ctx.job.x + eff_dx
    new_y = ctx.job.y
    new_z = ctx.job.z + dz
    if ctx.bounds is not None:
        new_x, new_y, new_z = ctx.bounds.clamp(new_x, new_y, new_z)
    actual_dx = new_x - ctx.job.x
    actual_dz = new_z - ctx.job.z
    ctx.job.x = new_x
    ctx.job.y = new_y
    ctx.job.z = new_z
    ctx.job.pc = pc + 5
    ctx.events.append(
        MoveEvent(
            job_id=ctx.job.job_id,
            dx=actual_dx,
            dy=0,
            dz=actual_dz,
            x=new_x,
            y=new_y,
            z=new_z,
        )
    )


def _handle_sound(ctx: _StepContext) -> None:
    pc = ctx.job.pc
    sound_id = _read_u16(ctx.script, pc + 1)
    ctx.job.pc = pc + 3
    ctx.events.append(SoundEvent(job_id=ctx.job.job_id, sound_id=sound_id))


def _handle_spawn_projectile(ctx: _StepContext) -> None:
    pc = ctx.job.pc
    type_id = _read_u16(ctx.script, pc + 1)
    dx = _read_s16(ctx.script, pc + 3)
    dy = _read_s16(ctx.script, pc + 5)
    dz = _read_s16(ctx.script, pc + 7)
    ctx.job.pc = pc + 9
    ctx.events.append(
        SpawnEvent(
            job_id=ctx.job.job_id,
            spawn_kind="projectile",
            type_id=type_id,
            x=ctx.job.x + mirrored_dx(ctx.job.direction, dx),
            y=ctx.job.y + dy,
            z=ctx.job.z + dz,
            direction=ctx.job.direction,
            owner_id=ctx.job.owner_id,
        )
    )


def _handle_spawn_effect(ctx: _StepContext) -> None:
    pc = ctx.job.pc
    type_id = _read_u16(ctx.script, pc + 1)
    dx = _read_s16(ctx.script, pc + 3)
    dy = _read_s16(ctx.script, pc + 5)
    ctx.job.pc = pc + 7
    ctx.events.append(
        SpawnEvent(
            job_id=ctx.job.job_id,
            spawn_kind="effect",
            type_id=type_id,
            x=ctx.job.x + mirrored_dx(ctx.job.direction, dx),
            y=ctx.job.y + dy,
            z=ctx.job.z,
            direction=ctx.job.direction,
            owner_id=ctx.job.owner_id,
        )
    )


def _handle_screen_shake(ctx: _StepContext) -> None:
    pc = ctx.job.pc
    intensity = _read_u8(ctx.script, pc + 1)
    duration = _read_u8(ctx.script, pc + 2)
    ctx.job.pc = pc + 3
    ctx.events.append(
        ScreenEffectEvent(
            job_id=ctx.job.job_id,
            effect="shake",
            value=intensity,
            duration_vbls=duration,
        )
    )


def _handle_screen_flash(ctx: _StepContext) -> None:
    pc = ctx.job.pc
    color = _read_u16(ctx.script, pc + 1)
    duration = _read_u8(ctx.script, pc + 3)
    ctx.job.pc = pc + 4
    ctx.events.append(
        ScreenEffectEvent(
            job_id=ctx.job.job_id,
            effect="flash",
            value=color,
            duration_vbls=duration,
        )
    )


def _handle_set_facing(ctx: _StepContext) -> None:
    pc = ctx.job.pc
    arg = _read_u8(ctx.script, pc + 1)
    if arg == Direction.LEFT:
        ctx.job.direction = Direction.LEFT
    elif arg == Direction.RIGHT:
        ctx.job.direction = Direction.RIGHT
    else:
        ctx.job.direction = ctx.job.direction.flipped()
    ctx.job.pc = pc + 2


_CONTROL_HANDLERS: dict[int, _OpcodeHandler] = {
    OP_END_SCRIPT: _handle_end_script,
    OP_JUMP: _handle_jump,
    OP_FREE_JOB: _handle_free_job,
    OP_LOOP_BEGIN: _handle_loop_begin,
    OP_LOOP_END: _handle_loop_end,
    OP_BRANCH_REL: _handle_branch_rel,
    OP_BRANCH_RAND: _handle_branch_rand,
    OP_CALL: _handle_call,
    OP_RETURN: _handle_return,
    OP_SET_VELOCITY: _handle_set_velocity,
    OP_MOVE: _handle_move,
    OP_SOUND: _handle_sound,
    OP_SPAWN_PROJECTILE: _handle_spawn_projectile,
    OP_SPAWN_EFFECT: _handle_spawn_effect,
    OP_SCREEN_SHAKE: _handle_screen_shake,
    OP_SCREEN_FLASH: _handle_screen_flash,
    OP_SET_FACING: _handle_set_facing,
    OP_END_FRAME: _handle_end_frame,
}


def _integrate_velocity(job: CombatJob, bounds: ArenaBounds | None) -> MoveEvent | None:
    if job.vx == 0 and job.vy == 0 and job.vz == 0:
        return None
    new_x = job.x + job.vx
    new_y = job.y + job.vy
    new_z = job.z + job.vz
    if bounds is not None:
        new_x, new_y, new_z = bounds.clamp(new_x, new_y, new_z)
    dx = new_x - job.x
    dy = new_y - job.y
    dz = new_z - job.z
    job.x = new_x
    job.y = new_y
    job.z = new_z
    return MoveEvent(job_id=job.job_id, dx=dx, dy=dy, dz=dz, x=new_x, y=new_y, z=new_z)


def _dispatch_opcode(ctx: _StepContext) -> None:
    opcode = ctx.script[ctx.job.pc]
    if opcode <= DRAW_OPCODE_MAX:
        _handle_draw(ctx)
        return
    handler = _CONTROL_HANDLERS.get(opcode)
    if handler is not None:
        handler(ctx)
        return
    if OP_END_SCRIPT <= opcode <= OP_HOOK_MAX:
        ctx.job.pc += 1
        ctx.events.append(CombatHookEvent(job_id=ctx.job.job_id, opcode=opcode))
        return
    raise ScriptError(f"unknown combat opcode 0x{opcode:02X} at offset {ctx.job.pc}")


def step_frame(
    job: CombatJob,
    script: bytes,
    *,
    prng: SimPrng | None = None,
    bounds: ArenaBounds | None = None,
    max_ops: int = DEFAULT_MAX_OPS_PER_FRAME,
) -> tuple[CombatEvent, ...]:
    """Execute one combat script frame on ``job`` until ``0xFF`` or termination."""
    if not job.active or not job.running or job.paused != 0:
        return ()

    ctx = _StepContext(job=job, script=script, prng=prng, bounds=bounds)
    move_event = _integrate_velocity(job, bounds)
    if move_event is not None:
        ctx.events.append(move_event)

    ops = 0
    while not ctx.yielded:
        if job.pc >= len(script):
            job.running = False
            ctx.events.append(ScriptEndedEvent(job_id=job.job_id, pc=job.pc))
            break
        if ops >= max_ops:
            raise ScriptError(f"frame exceeded {max_ops} opcodes at offset {job.pc}")
        _dispatch_opcode(ctx)
        ops += 1

    return tuple(ctx.events)


def step_vbl(
    job: CombatJob,
    script: bytes,
    *,
    prng: SimPrng | None = None,
    bounds: ArenaBounds | None = None,
    max_ops: int = DEFAULT_MAX_OPS_PER_FRAME,
) -> tuple[CombatEvent, ...]:
    """Advance ``job`` by one 50 Hz PAL VBL tick (ARCHITECTURE.md §7.1)."""
    if not job.active or not job.running or job.paused != 0:
        return ()
    job.vbl_countdown -= 1
    if job.vbl_countdown > 0:
        return ()
    job.vbl_countdown = job.frame_divisor
    return step_frame(job, script, prng=prng, bounds=bounds, max_ops=max_ops)


def step_pool_vbl(
    jobs: Sequence[CombatJob],
    scripts: Sequence[bytes] | dict[int, bytes],
    *,
    prng: SimPrng | None = None,
    bounds: ArenaBounds | None = None,
    max_ops: int = DEFAULT_MAX_OPS_PER_FRAME,
) -> tuple[CombatEvent, ...]:
    """Step up to ``MAX_COMBAT_JOBS`` active jobs by one 50 Hz VBL tick in slot order."""
    if len(jobs) > MAX_COMBAT_JOBS:
        raise ValueError(f"expected at most {MAX_COMBAT_JOBS} combat jobs, got {len(jobs)}")
    events: list[CombatEvent] = []
    for job in jobs:
        if not job.active or not job.running or job.paused != 0:
            continue
        script = scripts[job.script_id]
        events.extend(step_vbl(job, script, prng=prng, bounds=bounds, max_ops=max_ops))
    return tuple(events)
