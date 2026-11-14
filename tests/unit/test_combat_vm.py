"""Unit tests for the pure-integer 50 Hz combat animation & bytecode VM."""

from __future__ import annotations

import struct

import pytest

from pymoonstone.sim.collision import (
    DRAW_FLAG_ATTACK,
    DRAW_FLAG_HURTBOX,
    DRAW_FLAG_NO_BODY_BOX,
)
from pymoonstone.sim.combat_vm import (
    DRAW_FLAG_FG_MASK,
    DRAW_FLAG_FLIP_X,
    DRAW_FLAG_STAMP_BG,
    MAX_COMBAT_JOBS,
    OP_BRANCH_RAND,
    OP_BRANCH_REL,
    OP_CALL,
    OP_END_FRAME,
    OP_END_SCRIPT,
    OP_FREE_JOB,
    OP_JUMP,
    OP_LOOP_BEGIN,
    OP_LOOP_END,
    OP_MOVE,
    OP_RETURN,
    OP_SCREEN_FLASH,
    OP_SCREEN_SHAKE,
    OP_SET_FACING,
    OP_SET_VELOCITY,
    OP_SOUND,
    OP_SPAWN_EFFECT,
    OP_SPAWN_PROJECTILE,
    VBL_DIVISOR_FAST,
    VBL_DIVISOR_NORMAL,
    ArenaBounds,
    BranchEvent,
    CombatHookEvent,
    CombatJob,
    DrawFrameEvent,
    FrameCompleteEvent,
    JobFreedEvent,
    MoveEvent,
    ScreenEffectEvent,
    ScriptEndedEvent,
    ScriptError,
    SoundEvent,
    SpawnEvent,
    effective_flip_x,
    mirrored_dx,
    step_frame,
    step_pool_vbl,
    step_vbl,
)
from pymoonstone.sim.prng import SimPrng
from pymoonstone.sim.types import Direction, EntityId, Pos25D

pytestmark = pytest.mark.sim


def _draw(cel_slot: int, frame: int, dy: int, flags: int, dx: int) -> bytes:
    return struct.pack(">BBbBh", cel_slot, frame, dy, flags, dx)


def test_normal_and_fast_vbl_divisors() -> None:
    script = (
        _draw(0, 1, -2, DRAW_FLAG_HURTBOX, 4)
        + bytes([OP_END_FRAME])
        + _draw(0, 2, 0, DRAW_FLAG_ATTACK, 6)
        + bytes([OP_END_FRAME, OP_END_SCRIPT])
    )

    normal = CombatJob(job_id=0, frame_divisor=VBL_DIVISOR_NORMAL)
    assert normal.vbl_countdown == 1
    # Tick 1 fires frame 0 immediately, then waits 5 idle ticks before tick 7 fires frame 1.
    fired_ticks_normal = [t for t in range(1, 15) if step_vbl(normal, script)]
    assert fired_ticks_normal == [1, 7, 13]
    assert not normal.running

    fast = CombatJob(job_id=1)
    fast.set_speed_mode(fast=True)
    assert fast.frame_divisor == VBL_DIVISOR_FAST
    fired_ticks_fast = [t for t in range(1, 13) if step_vbl(fast, script)]
    assert fired_ticks_fast == [1, 6, 11]
    assert not fast.running


def test_paused_and_inactive_jobs_skip_ticks() -> None:
    script = _draw(1, 0, 0, 0, 0) + bytes([OP_END_FRAME])
    paused_job = CombatJob(paused=1)
    assert step_vbl(paused_job, script) == ()
    assert paused_job.pc == 0
    assert paused_job.vbl_countdown == 1

    inactive_job = CombatJob(active=False)
    assert step_frame(inactive_job, script) == ()


def test_draw_opcode_flags_and_mirrored_displacements() -> None:
    all_flags = (
        DRAW_FLAG_HURTBOX
        | DRAW_FLAG_ATTACK
        | DRAW_FLAG_FLIP_X
        | DRAW_FLAG_STAMP_BG
        | DRAW_FLAG_NO_BODY_BOX
        | DRAW_FLAG_FG_MASK
    )
    script = _draw(3, 7, -5, all_flags, 12) + bytes([OP_END_FRAME])

    right_job = CombatJob(job_id=2, x=100, y=80, z=10, direction=Direction.RIGHT)
    events_r = step_frame(right_job, script)
    assert len(events_r) == 2
    draw_r = events_r[0]
    assert isinstance(draw_r, DrawFrameEvent)
    assert draw_r.cel_slot == 3
    assert draw_r.frame_index == 7
    assert draw_r.dx == 12
    assert draw_r.dy == -5
    assert draw_r.screen_x == 112
    assert draw_r.screen_y == 65
    assert draw_r.flipped is True
    assert draw_r.hurtbox is True
    assert draw_r.attack is True
    assert draw_r.exclude_body_box is True
    assert draw_r.stamp_bg is True
    assert draw_r.fg_masked is True
    assert isinstance(events_r[1], FrameCompleteEvent)

    left_job = CombatJob(job_id=3, x=100, y=80, z=10, direction=Direction.LEFT)
    events_l = step_frame(left_job, script)
    draw_l = events_l[0]
    assert isinstance(draw_l, DrawFrameEvent)
    assert draw_l.dx == -12
    assert draw_l.screen_x == 88
    # Facing left (True) XOR DRAW_FLAG_FLIP_X (True) -> False.
    assert draw_l.flipped is False
    assert effective_flip_x(Direction.LEFT, 0) is True
    assert mirrored_dx(Direction.LEFT, -8) == 8
    assert left_job.pos == Pos25D(100, 80, 10)


def test_loop_and_branch_opcodes() -> None:
    # Loop 3 times drawing frame 0.. and then end script.
    loop_script = (
        bytes([OP_LOOP_BEGIN, 3])
        + _draw(0, 1, 0, 0, 2)
        + bytes([OP_END_FRAME, OP_LOOP_END, OP_END_SCRIPT])
    )
    job = CombatJob()
    frames = [step_frame(job, loop_script) for _ in range(4)]
    assert [e for evs in frames[:3] for e in evs if isinstance(e, DrawFrameEvent)].__len__() == 3
    assert any(isinstance(e, BranchEvent) and e.taken for e in frames[1])
    assert any(isinstance(e, ScriptEndedEvent) for e in frames[3])
    assert not job.running

    # Random loop count (0 -> 1..31 from SimPrng) and deterministic random branch.
    prng_a = SimPrng(0xCAFE_BABE)
    prng_b = SimPrng(0xCAFE_BABE)
    rand_script = (
        bytes([OP_LOOP_BEGIN, 0])
        + struct.pack(">BH", OP_BRANCH_RAND, 128)
        + struct.pack(">h", 2)
        + bytes([OP_SOUND, 0x00, 0x05, OP_END_FRAME, OP_END_SCRIPT])
    )
    job_a = CombatJob()
    job_b = CombatJob()
    ev_a = step_frame(job_a, rand_script, prng=prng_a)
    ev_b = step_frame(job_b, rand_script, prng=prng_b)
    assert ev_a == ev_b
    assert job_a.loop_counter == job_b.loop_counter
    assert 1 <= job_a.loop_counter <= 31


def test_jump_relative_branch_and_subroutine_call_return() -> None:
    # Layout:
    # 0..4:   CALL 12
    # 5:      END_FRAME
    # 6..8:   BRANCH_REL +7 -> offset 16 (END_SCRIPT)
    # 9..11:  SOUND 99 (skipped)
    # 12..15: SUBROUTINE: SOUND 42 (3B at 12..14), RETURN (1B at 15)
    # 16:     END_SCRIPT
    sub_offset = 12
    sub_body = struct.pack(">BH", OP_SOUND, 42) + bytes([OP_RETURN])
    main = (
        struct.pack(">BI", OP_CALL, sub_offset)
        + bytes([OP_END_FRAME])
        + struct.pack(">Bh", OP_BRANCH_REL, 7)
        + struct.pack(">BH", OP_SOUND, 99)
    )
    assert len(main) == sub_offset
    script = main + sub_body + bytes([OP_END_SCRIPT])

    job = CombatJob()
    ev0 = step_frame(job, script)
    assert [type(e) for e in ev0] == [BranchEvent, SoundEvent, BranchEvent, FrameCompleteEvent]
    assert isinstance(ev0[1], SoundEvent) and ev0[1].sound_id == 42

    ev1 = step_frame(job, script)
    assert isinstance(ev1[0], BranchEvent) and ev1[0].opcode == OP_BRANCH_REL
    assert isinstance(ev1[1], ScriptEndedEvent)
    assert not job.running

    # Unconditional jump loop.
    jump_target = 0
    jump_script = (
        _draw(0, 0, 0, 0, 1) + bytes([OP_END_FRAME]) + struct.pack(">BI", OP_JUMP, jump_target)
    )
    jjob = CombatJob()
    step_frame(jjob, jump_script)
    ev_jump = step_frame(jjob, jump_script)
    assert any(
        isinstance(e, BranchEvent) and e.opcode == OP_JUMP and e.target_pc == 0 for e in ev_jump
    )


def test_velocity_integration_move_and_arena_clamping() -> None:
    bounds = ArenaBounds(min_x=10, max_x=100, min_y=30, max_y=150, min_z=0, max_z=50)
    script = (
        struct.pack(">BBhhh", OP_SET_VELOCITY, 0x01, 15, -5, 4)
        + bytes([OP_END_FRAME])
        + struct.pack(">Bhh", OP_MOVE, 10, 20)
        + bytes([OP_END_FRAME, OP_FREE_JOB])
    )
    job = CombatJob(
        job_id=4,
        x=85,
        y=33,
        z=40,
        direction=Direction.LEFT,
        owner_id=EntityId(7),
    )

    ev0 = step_frame(job, script, bounds=bounds)
    assert ev0[0] == FrameCompleteEvent(job_id=4, next_pc=9)
    # Facing left with flag bit 0 set negates vx (+15 -> -15).
    assert (job.vx, job.vy, job.vz) == (-15, -5, 4)

    # Frame 1 integrates velocity (-15, -5, +4) -> (70, 28->30, 44),
    # then OP_MOVE (-10, +20) -> (60, 30, 64->50).
    ev1 = step_frame(job, script, bounds=bounds)
    assert ev1[0] == MoveEvent(job_id=4, dx=-15, dy=-3, dz=4, x=70, y=30, z=44)
    assert ev1[1] == MoveEvent(job_id=4, dx=-10, dy=0, dz=6, x=60, y=30, z=50)
    assert ev1[2] == FrameCompleteEvent(job_id=4, next_pc=15)

    ev2 = step_frame(job, script, bounds=bounds)
    assert any(isinstance(e, JobFreedEvent) and e.owner_id == EntityId(7) for e in ev2)
    assert not job.active
    assert not job.running


def test_sound_spawn_screen_effects_facing_and_hooks() -> None:
    script = (
        struct.pack(">BH", OP_SOUND, 14)
        + struct.pack(">BHhhh", OP_SPAWN_PROJECTILE, 2, 10, 1, 5)
        + bytes([OP_SET_FACING, 0])
        + struct.pack(">BHhh", OP_SPAWN_EFFECT, 9, 4, -2)
        + bytes([OP_SCREEN_SHAKE, 6, 8])
        + struct.pack(">BHB", OP_SCREEN_FLASH, 0xA00, 3)
        + bytes([0xA5, OP_END_FRAME])
    )
    job = CombatJob(
        job_id=0,
        x=50,
        y=90,
        z=0,
        direction=Direction.RIGHT,
        owner_id=EntityId(1),
    )
    events = step_frame(job, script)

    assert events[0] == SoundEvent(job_id=0, sound_id=14)
    assert events[1] == SpawnEvent(
        job_id=0,
        spawn_kind="projectile",
        type_id=2,
        x=60,
        y=91,
        z=5,
        direction=Direction.RIGHT,
        owner_id=EntityId(1),
    )
    # OP_SET_FACING flipped RIGHT -> LEFT before OP_SPAWN_EFFECT.
    assert job.facing_left is True
    assert events[2] == SpawnEvent(
        job_id=0,
        spawn_kind="effect",
        type_id=9,
        x=46,
        y=88,
        z=0,
        direction=Direction.LEFT,
        owner_id=EntityId(1),
    )
    assert events[3] == ScreenEffectEvent(job_id=0, effect="shake", value=6, duration_vbls=8)
    assert events[4] == ScreenEffectEvent(job_id=0, effect="flash", value=0xA00, duration_vbls=3)
    assert events[5] == CombatHookEvent(job_id=0, opcode=0xA5)
    assert events[6] == FrameCompleteEvent(job_id=0, next_pc=len(script))


def test_pool_stepping_and_error_cases() -> None:
    s0 = struct.pack(">BH", OP_SOUND, 1) + bytes([OP_END_FRAME])
    s1 = struct.pack(">BH", OP_SOUND, 2) + bytes([OP_END_FRAME])
    jobs = [
        CombatJob(job_id=0, script_id=0),
        CombatJob(job_id=1, script_id=1, paused=1),
        CombatJob(job_id=2, script_id=1),
    ]
    events = step_pool_vbl(jobs, [s0, s1])
    assert events == (
        SoundEvent(job_id=0, sound_id=1),
        FrameCompleteEvent(job_id=0, next_pc=4),
        SoundEvent(job_id=2, sound_id=2),
        FrameCompleteEvent(job_id=2, next_pc=4),
    )

    with pytest.raises(ValueError, match="at most 10"):
        step_pool_vbl([CombatJob(job_id=i) for i in range(MAX_COMBAT_JOBS + 1)], [s0])

    with pytest.raises(ValueError, match="frame_divisor"):
        CombatJob(frame_divisor=0)

    with pytest.raises(ScriptError, match="truncated draw"):
        step_frame(CombatJob(), bytes([0x00, 0x01]))

    with pytest.raises(ScriptError, match="out of range"):
        step_frame(CombatJob(), struct.pack(">BI", OP_JUMP, 999))

    with pytest.raises(ScriptError, match="empty call stack"):
        step_frame(CombatJob(), bytes([OP_RETURN]))

    with pytest.raises(ScriptError, match="unknown combat opcode"):
        step_frame(CombatJob(), bytes([0xE0]))

    infinite_loop = struct.pack(">BI", OP_JUMP, 0)
    with pytest.raises(ScriptError, match="exceeded"):
        step_frame(CombatJob(), infinite_loop, max_ops=8)
