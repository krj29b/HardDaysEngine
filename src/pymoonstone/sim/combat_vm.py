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
from pymoonstone.sim.m68k import s8, s16, u8, u16, u32
from pymoonstone.sim.types import Direction, EntityId, Pos25D

if TYPE_CHECKING:
    from pymoonstone.sim.prng import SimPrng

VBL_DIVISOR_NORMAL: int = 6
VBL_DIVISOR_FAST: int = 5
MAX_COMBAT_JOBS: int = 10
DEFAULT_MAX_OPS_PER_FRAME: int = 256

DRAW_OPCODE_MAX: int = 0x7F
DRAW_INSTRUCTION_SIZE: int = 6

DRAW_FLAG_FLIP_X: int = 0x04
DRAW_FLAG_BG: int = 0x10
DRAW_FLAG_STAMP_BG: int = 0x20
DRAW_FLAG_FG_MASK: int = 0x80
DRAW_FLAG_NO_GORE: int = 0x80

OP_SET_FACING: int = 0x80
OP_JUMP: int = 0x84
OP_LOOP_BEGIN: int = 0x88
OP_MOTION_BLOCK: int = 0x8C
OP_LOOP2_BEGIN: int = 0x94
OP_GORE_JUMP: int = 0x98
OP_MOVE: int = 0xA0
OP_SOUND: int = 0xA4
OP_POKE_OWNER: int = 0xA8
OP_SECOND_SCRIPT: int = 0xAC
OP_ENGINE_CALL: int = 0xB0
OP_IF_DEAD: int = 0xB4
OP_SPAWN_JOB: int = 0xB8
OP_KILL_JOB: int = 0xBC
OP_FRAME_SET: int = 0xC0
OP_IF_SAME_FACING: int = 0xC4
OP_IF_ZERO: int = 0xC8
OP_IF_NONZERO: int = 0xCC
OP_RESET_WORK: int = 0xD0
OP_JUMP_RET: int = 0xFD
OP_LOOP_END: int = 0xFD
OP_LOOP2_END: int = 0xFE
OP_END_FRAME: int = 0xFF
OP_END_SCRIPT: int = 0xFF
OP_FREE_JOB: int = OP_KILL_JOB
OP_SPAWN_PROJECTILE: int = OP_SPAWN_JOB


class ScriptError(ValueError):
    """Raised when combat bytecode is truncated, out of bounds, or loops without yielding."""


@dataclass(slots=True, frozen=True)
class ArenaBounds:
    """Optional integer arena bounds for ``0xA0`` movement and motion stepping."""

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
    """Structured cel draw request emitted by ``0x00..0x7F`` (ARCHITECTURE.md §7.2)."""

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
    """Synth sound effect trigger emitted by ``0xA4`` (ARCHITECTURE.md §7.2)."""

    job_id: int
    sound_id: int


@dataclass(slots=True, frozen=True)
class SpawnEvent:
    """Object or second-script spawn request emitted by ``0xB8``."""

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
    """Position update emitted by ``0xA0``, ``0x8C`` motion, or velocity integration."""

    job_id: int
    dx: int
    dy: int
    dz: int
    x: int
    y: int
    z: int


@dataclass(slots=True, frozen=True)
class BranchEvent:
    """Control-flow jump/branch emitted by ``0x84``, ``0x98``, ``0xB4``, ``0xC4..0xCC``."""

    job_id: int
    opcode: int
    from_pc: int
    target_pc: int
    taken: bool


@dataclass(slots=True, frozen=True)
class ScreenEffectEvent:
    """Screen shake or palette flash request."""

    job_id: int
    effect: str
    value: int
    duration_vbls: int


@dataclass(slots=True, frozen=True)
class CombatHookEvent:
    """Engine callback / owner-poke notification for ``0xA8`` / ``0xB0``."""

    job_id: int
    opcode: int
    arg: int = 0


@dataclass(slots=True, frozen=True)
class FrameCompleteEvent:
    """End-of-frame marker ``0xFF`` reached (ARCHITECTURE.md §7.2)."""

    job_id: int
    next_pc: int


@dataclass(slots=True, frozen=True)
class ScriptEndedEvent:
    """Script terminated by ``0xFF 0xFF`` or end of buffer (ARCHITECTURE.md §7.2)."""

    job_id: int
    pc: int


@dataclass(slots=True, frozen=True)
class JobFreedEvent:
    """Job and owner freed by ``0xBC`` (ARCHITECTURE.md §7.2)."""

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
    motion_param: int = 0
    motion_flags: int = 0
    motion_active: bool = False
    motion_count: int = 0
    motion_vy: int = 0
    motion_vy_limit: int = 0
    motion_vx: int = 0
    motion_vx_limit: int = 0
    return_pc: int = 0
    call_stack: list[int] = field(default_factory=list)

    def reset_preserving_spawn(self) -> None:
        """Clear work state while keeping ``spawn_armed`` and ``spawn_script_id``."""
        armed = self.spawn_armed
        spawn_id = self.spawn_script_id
        self.loop_count = 0
        self.loop_active = False
        self.loop_pc = 0
        self.loop2_count = 0
        self.loop2_active = False
        self.loop2_pc = 0
        self.call_pc = 0
        self.call_armed = False
        self.motion_param = 0
        self.motion_flags = 0
        self.motion_active = False
        self.motion_count = 0
        self.motion_vy = 0
        self.motion_vy_limit = 0
        self.motion_vx = 0
        self.motion_vx_limit = 0
        self.return_pc = 0
        self.call_stack.clear()
        self.spawn_armed = armed
        self.spawn_script_id = spawn_id


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
    frame_set: int = 1
    draw_flags: int = 0
    screen_x: int = 0
    screen_y: int = 0
    job_type: int = 0
    frame_divisor: int = VBL_DIVISOR_NORMAL
    vbl_countdown: int = 1
    loop_counter: int = 0
    owner_id: EntityId | None = None
    link_id: EntityId | None = None
    owner_hp: int = 100
    gore_off: bool = False
    same_facing_as_knight: bool = True
    owner_fields: dict[int, int] = field(default_factory=dict)
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

    cel_slot = (script[pc] & 0x1F) >> 2
    frame_index = script[pc + 1]
    dy = _read_s8(script, pc + 2)
    draw_flags = script[pc + 3]
    dx = _read_s16(script, pc + 4)
    job.pc = pc + DRAW_INSTRUCTION_SIZE

    if job.gore_off and (draw_flags & DRAW_FLAG_NO_GORE):
        return

    eff_dx = mirrored_dx(job.direction, dx)
    flipped = effective_flip_x(job.direction, draw_flags)
    draw_x = job.x + eff_dx
    draw_y = job.pos.screen_y + dy

    job.cel_slot = cel_slot
    job.frame_index = frame_index
    job.draw_flags = draw_flags
    job.screen_x = draw_x
    job.screen_y = draw_y

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
            stamp_bg=bool(draw_flags & (DRAW_FLAG_BG | DRAW_FLAG_STAMP_BG)),
            fg_masked=bool(draw_flags & DRAW_FLAG_FG_MASK),
        )
    )


def _step_motion_y(job: CombatJob, work: CombatWork) -> bool:
    flags = work.motion_flags
    if flags & 0x02:
        job.z -= work.motion_vy
        if job.z <= 0:
            job.z = 0
            work.motion_flags &= ~0x02
        elif not (flags & 0x20) and s8(work.motion_vy) < s8(work.motion_vy_limit):
            work.motion_vy = u8(work.motion_vy << 1)
        return True
    if flags & 0x01:
        job.z += work.motion_vy
        if not (flags & 0x20) and s8(work.motion_vy) > s8(work.motion_vy_limit):
            work.motion_vy = u8(work.motion_vy >> 1)
        return True
    return False


def _step_motion_x(job: CombatJob, work: CombatWork) -> bool:
    flags = work.motion_flags
    moved = False
    if flags & 0x10:
        moved = True
        job.x += mirrored_dx(job.direction, work.motion_vx)
    if flags & 0x04:
        moved = True
        job.x -= mirrored_dx(job.direction, work.motion_vx)
    if moved and not (flags & 0x80) and s8(work.motion_vx) > s8(work.motion_vx_limit):
        work.motion_vx = u8(work.motion_vx >> 1)
    return moved


def _step_motion(ctx: _StepContext) -> None:
    job = ctx.job
    work = job.work
    old_x, old_z = job.x, job.z
    moved_y = _step_motion_y(job, work)
    moved_x = _step_motion_x(job, work)
    moved = moved_y or moved_x

    if ctx.bounds is not None:
        job.x, job.y, job.z = ctx.bounds.clamp(job.x, job.y, job.z)

    if not (work.motion_flags & 0x40):
        job.pc = _validate_target(ctx.script, work.return_pc)
    if not moved:
        work.motion_active = False
    else:
        ctx.events.append(
            MoveEvent(
                job_id=job.job_id,
                dx=job.x - old_x,
                dy=0,
                dz=job.z - old_z,
                x=job.x,
                y=job.y,
                z=job.z,
            )
        )


def _step_loop2_on_marker(ctx: _StepContext) -> bool:
    work = ctx.job.work
    if work.loop2_active:
        work.loop2_count = u8(work.loop2_count - 1)
        if work.loop2_count != 0:
            ctx.job.pc = _validate_target(ctx.script, work.loop2_pc)
            return True
    work.loop2_active = False
    return False


def _handle_end_frame(ctx: _StepContext) -> None:
    job = ctx.job
    work = job.work
    pc = job.pc

    if work.loop_active:
        work.loop_count = u8(work.loop_count - 1)
        if work.loop_count != 0:
            job.loop_counter = work.loop_count
            job.pc = _validate_target(ctx.script, work.loop_pc)
            ctx.yielded = True
            ctx.events.append(FrameCompleteEvent(job_id=job.job_id, next_pc=job.pc))
            return
    work.loop_active = False
    job.loop_counter = 0

    if work.motion_active:
        work.motion_count = u8(work.motion_count - 1)
        if s8(work.motion_count) >= 0:
            _step_motion(ctx)
            ctx.yielded = True
            ctx.events.append(FrameCompleteEvent(job_id=job.job_id, next_pc=job.pc))
            return
    work.motion_active = False

    if work.call_armed:
        work.call_armed = False
        job.pc = _validate_target(ctx.script, work.call_pc)
        ctx.yielded = True
        ctx.events.append(FrameCompleteEvent(job_id=job.job_id, next_pc=job.pc))
        return

    next_byte = ctx.script[pc + 1] if pc + 1 < len(ctx.script) else 0x00
    if next_byte == 0xFF:
        if _step_loop2_on_marker(ctx):
            ctx.yielded = True
            ctx.events.append(FrameCompleteEvent(job_id=job.job_id, next_pc=job.pc))
            return
        job.running = False
        job.pc = min(pc + 2, len(ctx.script))
        ctx.yielded = True
        ctx.events.append(ScriptEndedEvent(job_id=job.job_id, pc=job.pc))
        return

    if next_byte == 0xFE and _step_loop2_on_marker(ctx):
        ctx.yielded = True
        ctx.events.append(FrameCompleteEvent(job_id=job.job_id, next_pc=job.pc))
        return

    job.pc = min(pc + 2, len(ctx.script))
    ctx.yielded = True
    ctx.events.append(FrameCompleteEvent(job_id=job.job_id, next_pc=job.pc))


def _handle_set_facing(ctx: _StepContext) -> None:
    pc = ctx.job.pc
    arg = _read_u8(ctx.script, pc + 1)
    if arg == 0xFF:
        ctx.job.direction = ctx.job.direction.flipped()
    elif arg == Direction.LEFT:
        ctx.job.direction = Direction.LEFT
    else:
        ctx.job.direction = Direction.RIGHT
    ctx.job.pc = pc + 2


def _handle_jump(ctx: _StepContext) -> None:
    from_pc = ctx.job.pc
    mode = _read_u8(ctx.script, from_pc + 1)
    target = _validate_target(ctx.script, _read_u32(ctx.script, from_pc + 2))
    if mode == 3:
        ctx.job.pc = target
        taken = True
    else:
        ctx.job.work.call_pc = target
        ctx.job.work.call_armed = True
        ctx.job.pc = from_pc + 6
        taken = False
    ctx.events.append(
        BranchEvent(
            job_id=ctx.job.job_id,
            opcode=OP_JUMP,
            from_pc=from_pc,
            target_pc=target,
            taken=taken,
        )
    )


def _handle_loop_begin(ctx: _StepContext) -> None:
    count = _read_u8(ctx.script, ctx.job.pc + 1)
    if count == 0:
        count = ctx.prng.rand_range(1, 32) if ctx.prng is not None else 1
    ctx.job.pc += 2
    ctx.job.loop_counter = count
    ctx.job.work.loop_count = count
    ctx.job.work.loop_active = True
    ctx.job.work.loop_pc = ctx.job.pc


def _handle_motion_block(ctx: _StepContext) -> None:
    pc = ctx.job.pc
    work = ctx.job.work
    work.motion_active = True
    work.motion_param = _read_u8(ctx.script, pc + 1)
    work.motion_count = _read_u8(ctx.script, pc + 2)
    work.motion_flags = _read_u8(ctx.script, pc + 3)
    work.motion_vy = _read_u8(ctx.script, pc + 4)
    work.motion_vy_limit = _read_u8(ctx.script, pc + 5)
    work.motion_vx = _read_u8(ctx.script, pc + 6)
    work.motion_vx_limit = _read_u8(ctx.script, pc + 7)
    ctx.job.pc = pc + 8
    work.return_pc = ctx.job.pc


def _handle_loop2_begin(ctx: _StepContext) -> None:
    count = _read_u8(ctx.script, ctx.job.pc + 1)
    ctx.job.pc += 2
    ctx.job.work.loop2_count = count
    ctx.job.work.loop2_active = True
    ctx.job.work.loop2_pc = ctx.job.pc


def _handle_gore_jump(ctx: _StepContext) -> None:
    from_pc = ctx.job.pc
    target = _validate_target(ctx.script, _read_u32(ctx.script, from_pc + 2))
    taken = ctx.job.gore_off
    ctx.job.pc = target if taken else from_pc + 6
    ctx.events.append(
        BranchEvent(
            job_id=ctx.job.job_id,
            opcode=OP_GORE_JUMP,
            from_pc=from_pc,
            target_pc=ctx.job.pc,
            taken=taken,
        )
    )


def _handle_move(ctx: _StepContext) -> None:
    pc = ctx.job.pc
    flags = _read_u8(ctx.script, pc + 1)
    raw_dx = _read_u16(ctx.script, pc + 2)
    raw_dy = _read_u16(ctx.script, pc + 4)
    raw_dz = _read_u16(ctx.script, pc + 6)

    if flags & 0x40:
        new_x = s16(raw_dx)
        new_z = s16(raw_dy)
        new_y = s16(raw_dz)
    else:
        x_add = not (flags & 0x01) if ctx.job.direction.is_left else bool(flags & 0x01)
        new_x = ctx.job.x + raw_dx if x_add else ctx.job.x - raw_dx
        new_z = ctx.job.z - raw_dy if (flags & 0x08) else ctx.job.z + raw_dy
        new_y = ctx.job.y - raw_dz if (flags & 0x20) else ctx.job.y + raw_dz

    if ctx.bounds is not None:
        new_x, new_y, new_z = ctx.bounds.clamp(new_x, new_y, new_z)
    actual_dx = new_x - ctx.job.x
    actual_dy = new_y - ctx.job.y
    actual_dz = new_z - ctx.job.z
    ctx.job.x = new_x
    ctx.job.y = new_y
    ctx.job.z = new_z
    ctx.job.pc = pc + 8
    ctx.events.append(
        MoveEvent(
            job_id=ctx.job.job_id,
            dx=actual_dx,
            dy=actual_dy,
            dz=actual_dz,
            x=new_x,
            y=new_y,
            z=new_z,
        )
    )


def _handle_sound(ctx: _StepContext) -> None:
    pc = ctx.job.pc
    sound_id = _read_u8(ctx.script, pc + 1)
    ctx.job.pc = pc + 2
    ctx.events.append(SoundEvent(job_id=ctx.job.job_id, sound_id=sound_id))


def _handle_poke_owner(ctx: _StepContext) -> None:
    pc = ctx.job.pc
    size_flags = _read_u8(ctx.script, pc + 1)
    offset = _read_s16(ctx.script, pc + 2)
    value = _read_u32(ctx.script, pc + 4)
    if size_flags & 1:
        ctx.job.owner_fields[offset] = value & 0xFF
    elif size_flags & 2:
        ctx.job.owner_fields[offset] = value & 0xFFFF
    else:
        ctx.job.owner_fields[offset] = value
    ctx.job.pc = pc + 8
    ctx.events.append(CombatHookEvent(job_id=ctx.job.job_id, opcode=OP_POKE_OWNER, arg=offset))


def _handle_second_script(ctx: _StepContext) -> None:
    pc = ctx.job.pc
    armed = _read_u8(ctx.script, pc + 1)
    target = _read_u32(ctx.script, pc + 2)
    ctx.job.work.spawn_armed = armed != 0
    ctx.job.work.spawn_script_id = target
    ctx.job.pc = pc + 6


def _handle_engine_call(ctx: _StepContext) -> None:
    pc = ctx.job.pc
    routine_id = _read_u32(ctx.script, pc + 2)
    ctx.job.pc = pc + 6
    ctx.events.append(CombatHookEvent(job_id=ctx.job.job_id, opcode=OP_ENGINE_CALL, arg=routine_id))


def _handle_if_dead(ctx: _StepContext) -> None:
    from_pc = ctx.job.pc
    target = _validate_target(ctx.script, _read_u32(ctx.script, from_pc + 2))
    taken = ctx.job.owner_hp <= 0
    if taken:
        ctx.job.pc = target
        ctx.job.work.reset_preserving_spawn()
    else:
        ctx.job.pc = from_pc + 6
    ctx.events.append(
        BranchEvent(
            job_id=ctx.job.job_id,
            opcode=OP_IF_DEAD,
            from_pc=from_pc,
            target_pc=ctx.job.pc,
            taken=taken,
        )
    )


def _handle_spawn_job(ctx: _StepContext) -> None:
    pc = ctx.job.pc
    target = _read_u32(ctx.script, pc + 2)
    ctx.job.pc = pc + 6
    ctx.events.append(
        SpawnEvent(
            job_id=ctx.job.job_id,
            spawn_kind="projectile",
            type_id=target,
            x=ctx.job.x,
            y=ctx.job.y,
            z=ctx.job.z,
            direction=ctx.job.direction,
            owner_id=ctx.job.owner_id,
        )
    )


def _handle_kill_job(ctx: _StepContext) -> None:
    ctx.job.pc += 2
    ctx.job.active = False
    ctx.job.running = False
    ctx.yielded = True
    ctx.events.append(JobFreedEvent(job_id=ctx.job.job_id, owner_id=ctx.job.owner_id))


def _handle_frame_set(ctx: _StepContext) -> None:
    pc = ctx.job.pc
    ctx.job.frame_set = _read_u8(ctx.script, pc + 1)
    ctx.job.pc = pc + 2


def _handle_if_same_facing(ctx: _StepContext) -> None:
    from_pc = ctx.job.pc
    target = _validate_target(ctx.script, _read_u32(ctx.script, from_pc + 2))
    taken = ctx.job.same_facing_as_knight
    ctx.job.pc = target if taken else from_pc + 6
    ctx.events.append(
        BranchEvent(
            job_id=ctx.job.job_id,
            opcode=OP_IF_SAME_FACING,
            from_pc=from_pc,
            target_pc=ctx.job.pc,
            taken=taken,
        )
    )


def _handle_if_field(ctx: _StepContext, *, expect_nonzero: bool, opcode: int) -> None:
    from_pc = ctx.job.pc
    size_flags = _read_u8(ctx.script, from_pc + 1)
    offset = _read_s16(ctx.script, from_pc + 2)
    target = _validate_target(ctx.script, _read_u32(ctx.script, from_pc + 4))
    raw_val = ctx.job.owner_fields.get(offset, 0)
    if size_flags & 1:
        val = raw_val & 0xFF
    elif size_flags & 2:
        val = raw_val & 0xFFFF
    else:
        val = u32(raw_val)
    taken = (val != 0) if expect_nonzero else (val == 0)
    ctx.job.pc = target if taken else from_pc + 8
    ctx.events.append(
        BranchEvent(
            job_id=ctx.job.job_id,
            opcode=opcode,
            from_pc=from_pc,
            target_pc=ctx.job.pc,
            taken=taken,
        )
    )


def _handle_if_zero(ctx: _StepContext) -> None:
    _handle_if_field(ctx, expect_nonzero=False, opcode=OP_IF_ZERO)


def _handle_if_nonzero(ctx: _StepContext) -> None:
    _handle_if_field(ctx, expect_nonzero=True, opcode=OP_IF_NONZERO)


def _handle_reset_work(ctx: _StepContext) -> None:
    ctx.job.work.reset_preserving_spawn()
    ctx.job.loop_counter = 0
    ctx.job.pc += 2


def _handle_jump_ret(ctx: _StepContext) -> None:
    from_pc = ctx.job.pc
    target = _validate_target(ctx.script, ctx.job.work.return_pc)
    ctx.job.pc = target
    ctx.events.append(
        BranchEvent(
            job_id=ctx.job.job_id,
            opcode=OP_JUMP_RET,
            from_pc=from_pc,
            target_pc=target,
            taken=True,
        )
    )


def _handle_jump_loop2(ctx: _StepContext) -> None:
    from_pc = ctx.job.pc
    target = _validate_target(ctx.script, ctx.job.work.loop2_pc)
    ctx.job.pc = target
    ctx.events.append(
        BranchEvent(
            job_id=ctx.job.job_id,
            opcode=OP_LOOP2_END,
            from_pc=from_pc,
            target_pc=target,
            taken=True,
        )
    )


_CONTROL_HANDLERS: dict[int, _OpcodeHandler] = {
    OP_SET_FACING: _handle_set_facing,
    OP_JUMP: _handle_jump,
    OP_LOOP_BEGIN: _handle_loop_begin,
    OP_MOTION_BLOCK: _handle_motion_block,
    OP_LOOP2_BEGIN: _handle_loop2_begin,
    OP_GORE_JUMP: _handle_gore_jump,
    OP_MOVE: _handle_move,
    OP_SOUND: _handle_sound,
    OP_POKE_OWNER: _handle_poke_owner,
    OP_SECOND_SCRIPT: _handle_second_script,
    OP_ENGINE_CALL: _handle_engine_call,
    OP_IF_DEAD: _handle_if_dead,
    OP_SPAWN_JOB: _handle_spawn_job,
    OP_KILL_JOB: _handle_kill_job,
    OP_FRAME_SET: _handle_frame_set,
    OP_IF_SAME_FACING: _handle_if_same_facing,
    OP_IF_ZERO: _handle_if_zero,
    OP_IF_NONZERO: _handle_if_nonzero,
    OP_RESET_WORK: _handle_reset_work,
    OP_JUMP_RET: _handle_jump_ret,
    OP_LOOP2_END: _handle_jump_loop2,
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
