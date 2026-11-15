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
    DRAW_FLAG_BG,
    DRAW_FLAG_FLIP_X,
    DRAW_FLAG_NO_GORE,
    DRAW_FLAG_STAMP_BG,
    MAX_COMBAT_JOBS,
    OP_END_FRAME,
    OP_ENGINE_CALL,
    OP_FRAME_SET,
    OP_GORE_JUMP,
    OP_IF_DEAD,
    OP_IF_NONZERO,
    OP_IF_SAME_FACING,
    OP_IF_ZERO,
    OP_JUMP,
    OP_JUMP_RET,
    OP_KILL_JOB,
    OP_LOOP2_BEGIN,
    OP_LOOP2_END,
    OP_LOOP_BEGIN,
    OP_MOTION_BLOCK,
    OP_MOVE,
    OP_POKE_OWNER,
    OP_RESET_WORK,
    OP_SECOND_SCRIPT,
    OP_SET_FACING,
    OP_SOUND,
    OP_SPAWN_JOB,
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
    return struct.pack(">BBbBh", (cel_slot & 0x07) << 2, frame, dy, flags, dx)


def test_normal_and_fast_vbl_divisors() -> None:
    script = (
        _draw(0, 1, -2, DRAW_FLAG_HURTBOX, 4)
        + bytes([OP_END_FRAME, 0x00])
        + _draw(0, 2, 0, DRAW_FLAG_ATTACK, 6)
        + bytes([OP_END_FRAME, 0xFF])
    )

    normal = CombatJob(job_id=0, frame_divisor=VBL_DIVISOR_NORMAL)
    assert normal.vbl_countdown == 1
    fired_ticks_normal = [t for t in range(1, 15) if step_vbl(normal, script)]
    assert fired_ticks_normal == [1, 7]
    assert not normal.running

    fast = CombatJob(job_id=1)
    fast.set_speed_mode(fast=True)
    assert fast.frame_divisor == VBL_DIVISOR_FAST
    fired_ticks_fast = [t for t in range(1, 13) if step_vbl(fast, script)]
    assert fired_ticks_fast == [1, 6]
    assert not fast.running


def test_paused_and_inactive_jobs_skip_ticks() -> None:
    script = _draw(1, 0, 0, 0, 0) + bytes([OP_END_FRAME, 0xFF])
    paused_job = CombatJob(paused=1)
    assert step_vbl(paused_job, script) == ()
    assert paused_job.pc == 0
    assert paused_job.vbl_countdown == 1

    inactive_job = CombatJob(active=False)
    assert step_frame(inactive_job, script) == ()


def test_draw_opcode_slot_decoding_flags_and_mirroring() -> None:
    all_flags = (
        DRAW_FLAG_HURTBOX
        | DRAW_FLAG_ATTACK
        | DRAW_FLAG_FLIP_X
        | DRAW_FLAG_BG
        | DRAW_FLAG_STAMP_BG
        | DRAW_FLAG_NO_BODY_BOX
        | DRAW_FLAG_NO_GORE
    )
    # Opcode 0x0C -> cel_slot = (0x0C & 0x1F) >> 2 = 3.
    script = struct.pack(">BBbBh", 0x0C, 7, -5, all_flags, 12) + bytes([OP_END_FRAME, 0x00])

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
    assert draw_l.flipped is False
    assert effective_flip_x(Direction.LEFT, 0) is True
    assert mirrored_dx(Direction.LEFT, -8) == 8
    assert left_job.pos == Pos25D(100, 80, 10)

    # Gore-off mode skips draws with 0x80 flag.
    gore_off_job = CombatJob(job_id=4, gore_off=True)
    events_g = step_frame(gore_off_job, script)
    assert [type(e) for e in events_g] == [FrameCompleteEvent]


def test_loop1_and_loop2_markers() -> None:
    # Loop1 repeats 3 times at the $FF $00 marker, then falls through to $FF $FF.
    loop1_script = (
        bytes([OP_LOOP_BEGIN, 3])
        + _draw(0, 1, 0, 0, 2)
        + bytes([OP_END_FRAME, 0xFF])
    )
    job = CombatJob()
    frames = [step_frame(job, loop1_script) for _ in range(3)]
    assert sum(1 for evs in frames for e in evs if isinstance(e, DrawFrameEvent)) == 3
    assert isinstance(frames[0][-1], FrameCompleteEvent)
    assert isinstance(frames[1][-1], FrameCompleteEvent)
    assert isinstance(frames[2][-1], ScriptEndedEvent)
    assert not job.running

    # Loop2 repeats twice using $FF $FE, then exits to $FF $FF.
    loop2_script = (
        bytes([OP_LOOP2_BEGIN, 2])
        + _draw(1, 2, 0, 0, 4)
        + bytes([OP_END_FRAME, 0xFE, OP_END_FRAME, 0xFF])
    )
    job2 = CombatJob()
    f0 = step_frame(job2, loop2_script)
    f1 = step_frame(job2, loop2_script)
    f2 = step_frame(job2, loop2_script)
    assert isinstance(f0[0], DrawFrameEvent) and isinstance(f0[1], FrameCompleteEvent)
    assert isinstance(f1[0], DrawFrameEvent) and isinstance(f1[1], FrameCompleteEvent)
    assert isinstance(f2[0], ScriptEndedEvent)
    assert not job2.running

    # Random loop count (0 -> 1..31 from SimPrng) is deterministic.
    prng_a = SimPrng(0xCAFE_BABE)
    prng_b = SimPrng(0xCAFE_BABE)
    rand_script = bytes([OP_LOOP_BEGIN, 0, OP_SOUND, 5, OP_END_FRAME, 0xFF])
    job_a = CombatJob()
    job_b = CombatJob()
    ev_a = step_frame(job_a, rand_script, prng=prng_a)
    ev_b = step_frame(job_b, rand_script, prng=prng_b)
    assert ev_a == ev_b
    assert 1 <= job_a.loop_counter <= 31


def test_jump_immediate_and_deferred_call_and_conditional_branches() -> None:
    # 0..5:   0x84 mode=1 (deferred arm -> offset 14)
    # 6..7:   0xFF 0x00 (yields, takes armed call -> 14)
    # 8..13:  0xA4 99 + 0xFF 0xFF (skipped)
    # 14..19: 0x84 mode=3 (immediate jump -> offset 20)
    # 20..25: 0x98 gore_jump -> offset 32 (skipped when gore_off=False)
    # 26..31: 0xC4 if_same_facing -> offset 32 (taken when same_facing_as_knight=True)
    # 32..33: 0xFF 0xFF (end)
    script = (
        struct.pack(">BBI", OP_JUMP, 1, 14)
        + bytes([OP_END_FRAME, 0x00])
        + bytes([OP_SOUND, 99, OP_END_FRAME, 0xFF, 0x00, 0x00])
        + struct.pack(">BBI", OP_JUMP, 3, 20)
        + struct.pack(">BBI", OP_GORE_JUMP, 0x07, 32)
        + struct.pack(">BBI", OP_IF_SAME_FACING, 0x00, 32)
        + bytes([OP_END_FRAME, 0xFF])
    )
    job = CombatJob(gore_off=False, same_facing_as_knight=True)
    ev0 = step_frame(job, script)
    assert isinstance(ev0[0], BranchEvent) and ev0[0].taken is False
    assert isinstance(ev0[1], FrameCompleteEvent) and ev0[1].next_pc == 14

    ev1 = step_frame(job, script)
    assert [type(e) for e in ev1] == [BranchEvent, BranchEvent, BranchEvent, ScriptEndedEvent]
    assert not job.running


def test_motion_block_and_move_with_arena_clamping() -> None:
    bounds = ArenaBounds(min_x=10, max_x=120, min_y=30, max_y=150, min_z=0, max_z=50)
    # Motion block: param=0, count=2, flags=0x11 (rise + move facing), vy=6, vy_lim=2, vx=8, vx_lim=2.
    script = (
        bytes([OP_MOTION_BLOCK, 0, 2, 0x11, 6, 2, 8, 2, OP_END_FRAME, 0x00])
        + struct.pack(">BBHHH", OP_MOVE, 0x01, 10, 0, 20)
        + bytes([OP_END_FRAME, 0x00, OP_KILL_JOB, 0x00])
    )
    job = CombatJob(
        job_id=4,
        x=80,
        y=40,
        z=10,
        direction=Direction.LEFT,
        owner_id=EntityId(7),
    )

    # Frame 0: arms motion block (return_pc = 8) and executes motion step 1 at $FF $00.
    # Facing left: x -= 8 -> 72; rise z += 6 -> 16.
    ev0 = step_frame(job, script, bounds=bounds)
    assert any(isinstance(e, MoveEvent) and (e.x, e.z) == (72, 16) for e in ev0)
    assert isinstance(ev0[-1], FrameCompleteEvent) and ev0[-1].next_pc == 8

    # Frame 1: $FF $00 triggers motion step 2 (halved speeds: vx=4, vy=3 -> x=68, z=19), still at pc=8.
    ev1 = step_frame(job, script, bounds=bounds)
    moves1 = [e for e in ev1 if isinstance(e, MoveEvent)]
    assert len(moves1) == 1
    assert (moves1[0].x, moves1[0].z) == (68, 19)

    # Frame 2: motion count exhausted -> advances past $FF $00 to pc=10 (OP_MOVE).
    ev2 = step_frame(job, script, bounds=bounds)
    assert isinstance(ev2[-1], FrameCompleteEvent) and ev2[-1].next_pc == 10

    # Frame 3: OP_MOVE (left, flags=1 -> x -= 10 -> 58; depth y += 20 -> 60), then $FF $00 -> pc=20.
    ev3 = step_frame(job, script, bounds=bounds)
    moves3 = [e for e in ev3 if isinstance(e, MoveEvent)]
    assert len(moves3) == 1
    assert (moves3[0].x, moves3[0].y) == (58, 60)

    # Frame 4: OP_KILL_JOB frees the job.
    ev4 = step_frame(job, script, bounds=bounds)
    assert any(isinstance(e, JobFreedEvent) and e.owner_id == EntityId(7) for e in ev4)
    assert not job.active
    assert not job.running


def test_sound_poke_if_field_ifdead_spawn_facing_and_reset() -> None:
    # Test 0xA4, 0xA8, 0xCC, 0xC8, 0xAC, 0xB0, 0xB8, 0xC0, 0x80, 0xD0, 0xB4.
    script = (
        bytes([OP_SOUND, 14])
        + struct.pack(">BBhI", OP_POKE_OWNER, 0x01, 104, 1)
        + struct.pack(">BBhI", OP_IF_NONZERO, 0x01, 104, 20)
        + bytes([OP_SOUND, 99])
        + struct.pack(">BBI", OP_SECOND_SCRIPT, 1, 500)
        + struct.pack(">BBI", OP_ENGINE_CALL, 0, 5)
        + struct.pack(">BBI", OP_SPAWN_JOB, 0, 77)
        + bytes([OP_FRAME_SET, 2, OP_SET_FACING, 0xFF, OP_RESET_WORK, 0x00])
        + struct.pack(">BBI", OP_IF_DEAD, 0, 50)
        + bytes([OP_END_FRAME, 0xFF])
    )
    assert len(script) == 52
    job = CombatJob(
        job_id=0,
        x=50,
        y=90,
        z=0,
        direction=Direction.RIGHT,
        owner_id=EntityId(1),
        owner_hp=0,
    )
    events = step_frame(job, script)

    assert events[0] == SoundEvent(job_id=0, sound_id=14)
    assert events[1] == CombatHookEvent(job_id=0, opcode=OP_POKE_OWNER, arg=104)
    assert isinstance(events[2], BranchEvent) and events[2].opcode == OP_IF_NONZERO and events[2].taken
    assert events[3] == CombatHookEvent(job_id=0, opcode=OP_ENGINE_CALL, arg=5)
    assert events[4] == SpawnEvent(
        job_id=0,
        spawn_kind="projectile",
        type_id=77,
        x=50,
        y=90,
        z=0,
        direction=Direction.RIGHT,
        owner_id=EntityId(1),
    )
    assert job.frame_set == 2
    assert job.facing_left is True
    assert job.work.spawn_armed is True
    assert job.work.spawn_script_id == 500
    assert isinstance(events[5], BranchEvent) and events[5].opcode == OP_IF_DEAD and events[5].taken
    assert isinstance(events[6], ScriptEndedEvent)

    # Verify 0xFD and 0xFE 1-byte jumps and 0xC8 if_zero.
    # 0..1:   OP_LOOP2_BEGIN 2 (loop2_pc=2)
    # 2..9:   OP_MOTION_BLOCK (return_pc=10)
    # 10..17: OP_IF_ZERO (target=20)
    # 18..19: OP_END_FRAME 0xFF
    # 20..21: OP_END_FRAME 0x00, OP_JUMP_RET (not reached here, tested via 0xC8 -> 20)
    jmp_script = (
        bytes([OP_LOOP2_BEGIN, 2, OP_MOTION_BLOCK, 0, 1, 0x40, 0, 0, 0, 0])
        + struct.pack(">BBhI", OP_IF_ZERO, 0x02, 10, 20)
        + bytes([OP_END_FRAME, 0xFF, OP_END_FRAME, 0x00, OP_JUMP_RET, OP_LOOP2_END])
    )
    j2 = CombatJob()
    ev_j0 = step_frame(j2, jmp_script)
    assert isinstance(ev_j0[-1], FrameCompleteEvent) and ev_j0[-1].next_pc == 20
    ev_j1 = step_frame(j2, jmp_script)
    assert isinstance(ev_j1[-1], FrameCompleteEvent) and ev_j1[-1].next_pc == 22
    assert OP_LOOP2_END == 0xFE


def test_pool_stepping_and_error_cases() -> None:
    s0 = bytes([OP_SOUND, 1, OP_END_FRAME, 0x00])
    s1 = bytes([OP_SOUND, 2, OP_END_FRAME, 0x00])
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
        step_frame(CombatJob(), struct.pack(">BBI", OP_JUMP, 3, 999))

    with pytest.raises(ScriptError, match="unknown combat opcode"):
        step_frame(CombatJob(), bytes([0xE0]))

    infinite_loop = struct.pack(">BBI", OP_JUMP, 3, 0)
    with pytest.raises(ScriptError, match="exceeded"):
        step_frame(CombatJob(), infinite_loop, max_ops=8)
