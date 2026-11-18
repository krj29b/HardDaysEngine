"""The fight script engine on synthetic scripts (ARCHITECTURE.md §10.17)."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

import pytest

from harddaysengine.sim.actor import Actor
from harddaysengine.sim.fight_script import (
    CelShape,
    FrameShape,
    Job,
    RoutineCall,
    ScriptEngine,
    ScriptError,
    ScriptImage,
    motion_step,
    sort_jobs,
)

BASE = 2  # script position 0 means "nothing", so code starts at 2


def draw(slot: int, frame: int, dy: int = 0, flags: int = 0, dx: int = 0) -> bytes:
    return struct.pack(">BBbBh", slot * 4, frame, dy, flags, dx)


def mark(next_byte: int = 0) -> bytes:
    return bytes((0xFF, next_byte))


def op2(op: int, arg: int) -> bytes:
    return bytes((op, arg))


def op6(op: int, arg: int, target: int) -> bytes:
    return struct.pack(">BBI", op, arg, target)


def op8(op: int, size: int, offset: int, value: int) -> bytes:
    return struct.pack(">BBhI", op, size, offset, value)


def _full(width: int, height: int) -> FrameShape:
    aligned = ((width + 15) >> 4) << 4
    row = ((1 << width) - 1) << (aligned - width)
    return FrameShape(width, height, (row,) * height)


CEL = CelShape("a.cel", (_full(10, 4), _full(20, 6), _full(16, 2)), None)
OTHER = CelShape("b.cel", (_full(8, 8),), None)


@dataclass
class Rig:
    engine: ScriptEngine
    calls: list[RoutineCall] = field(default_factory=list)
    spawns: list[tuple[int, int]] = field(default_factory=list)

    def job(self, script: int, *, owner: int = 1, x: int = 100, depth: int = 50) -> Job:
        job = next(j for j in self.engine.jobs if not j.active)
        job.active = job.running = True
        job.script, job.owner, job.frame_list = script, owner, 1
        job.x, job.height, job.depth, job.facing = x, 0, depth, 1
        return job

    def actor(self, handle: int = 1) -> Actor:
        return self.engine.actors[handle - 1]

    def frames(self, ticks: int) -> list[list[tuple[str, int, int, int]]]:
        return [
            [(d.cel, d.frame, d.x, d.y) for d in self.engine.tick().draws] for _ in range(ticks)
        ]


def rig(code: bytes, routines: dict[int, str] | None = None) -> Rig:
    holder: list[Rig] = []

    def call(c: RoutineCall) -> None:
        holder[0].calls.append(c)

    def spawn(script: int, job: Job) -> None:
        holder[0].spawns.append((script, job.owner))

    engine = ScriptEngine(
        image=ScriptImage(bytes(BASE) + code, routines or {}),
        actors=[Actor() for _ in range(6)],
        cels={CEL.name: CEL, OTHER.name: OTHER},
        frame_lists={1: (CEL.name, OTHER.name, None, None, None), 2: (OTHER.name,) * 5},
        call=call,
        spawn=spawn,
    )
    holder.append(Rig(engine))
    return holder[0]


# ---- draws ---------------------------------------------------------------------------------


def test_a_draw_facing_right_lands_at_x_plus_dx_and_writes_back() -> None:
    r = rig(draw(0, 0, dy=-3, dx=5) + mark(0xFF))
    r.job(BASE)
    assert r.frames(1) == [[("a.cel", 0, 105, 47)]]
    owner = r.actor()
    assert (owner.x, owner.height, owner.y, owner.facing) == (100, 0, 50, 1)
    assert (owner.box_min_x, owner.box_max_x, owner.box_min_y, owner.box_max_y) == (
        105,
        115,
        47,
        51,
    )


def test_a_draw_facing_left_mirrors_x_and_the_frame() -> None:
    r = rig(draw(0, 0, dx=5) + mark(0xFF))
    r.job(BASE).facing = 3
    out = r.engine.tick()
    assert (out.draws[0].x, out.draws[0].mirrored) == (100 - 5 - 10, True)


def test_the_mirror_state_is_shared_between_jobs() -> None:
    r = rig(draw(0, 0) + mark(0xFF))
    r.job(BASE, depth=10).facing = 3
    r.job(BASE, owner=2, depth=20)
    out = r.engine.tick()
    assert [d.mirrored for d in out.draws] == [True, False]
    assert ("a.cel", 0) not in r.engine.mirrored


def test_a_facing_other_than_1_or_3_toggles_the_frame_at_every_draw() -> None:
    r = rig(draw(0, 0) + draw(0, 0) + mark(0xFF))
    r.job(BASE).facing = 0
    assert [d.mirrored for d in r.engine.tick().draws] == [True, False]


def test_gore_off_skips_the_draw_and_its_lists_but_not_the_box() -> None:
    r = rig(draw(0, 0, flags=0x83) + mark(0xFF))
    r.engine.gore_off = True
    job = r.job(BASE)
    assert r.engine.tick().draws == []
    assert r.engine.attack.read(job.list_base) == []
    assert r.actor().box_max_x == 110


def test_flags_stamp_text_and_no_box() -> None:
    r = rig(draw(0, 0, flags=0x70) + mark(0xFF))
    r.job(BASE)
    out = r.engine.tick()
    assert (out.draws[0].stamp, out.draws[0].text) == (True, True)
    assert r.actor().box_min_x == 0


def test_box_takes_the_union_of_the_draws() -> None:
    r = rig(draw(0, 0, dy=0, dx=0) + draw(0, 1, dy=-10, dx=-4) + mark(0xFF))
    r.job(BASE)
    r.engine.tick()
    owner = r.actor()
    assert (owner.box_min_x, owner.box_max_x, owner.box_min_y, owner.box_max_y) == (96, 116, 40, 54)


def test_list_entries_go_to_the_jobs_own_slots() -> None:
    r = rig(draw(1, 0, flags=0x03) + mark(0xFF))
    r.job(BASE, depth=10)
    second = r.job(BASE, owner=2, depth=20)
    r.engine.tick()
    assert [e.cel for e in r.engine.attack.read(second.list_base)] == ["b.cel"]
    assert r.engine.hurt.read(second.list_base)[0].x == 100


def test_a_bad_slot_or_frame_is_an_error() -> None:
    r = rig(draw(2, 0) + mark(0xFF))
    r.job(BASE)
    with pytest.raises(ScriptError, match="slot 2"):
        r.engine.tick()
    r = rig(draw(0, 9) + mark(0xFF))
    r.job(BASE)
    with pytest.raises(ScriptError, match="frame 9"):
        r.engine.tick()


# ---- markers -------------------------------------------------------------------------------


def test_script_position_survives_across_ticks() -> None:
    r = rig(draw(0, 0) + mark() + draw(0, 1) + mark() + draw(0, 2) + mark(0xFF))
    job = r.job(BASE)
    assert [f[0][1] for f in r.frames(3)] == [0, 1, 2]
    assert not job.running


def test_an_ended_script_stays_on_its_marker_and_keeps_writing_back() -> None:
    r = rig(draw(0, 0) + mark(0xFF))
    job = r.job(BASE)
    r.engine.tick()
    end = job.script
    job.x = 7
    assert r.frames(1) == [[]]
    assert (job.script, r.actor().x, r.actor().box_min_x) == (end, 7, 0)


def test_loop1_repeats_the_frame() -> None:
    r = rig(op2(0x88, 3) + draw(0, 0) + mark() + draw(0, 1) + mark(0xFF))
    r.job(BASE)
    assert [f[0][1] for f in r.frames(4)] == [0, 0, 0, 1]


def test_loop1_count_zero_takes_the_vbl_counter() -> None:
    r = rig(op2(0x88, 0) + draw(0, 0) + mark(0xFF))
    job = r.job(BASE)
    r.engine.vbl = 0x45
    r.engine.tick()
    assert job.work.loop_count == 4
    r = rig(op2(0x88, 0) + draw(0, 0) + mark(0xFF))
    job = r.job(BASE)
    r.engine.vbl = 0x40
    r.engine.tick()
    assert (job.running, job.work.loop_active) == (False, False)


def test_loop2_with_fe_and_with_ff() -> None:
    code = op2(0x94, 2) + draw(0, 0) + mark() + draw(0, 1) + mark(0xFE) + draw(0, 2) + mark(0xFF)
    r = rig(code)
    r.job(BASE)
    assert [f[0][1] for f in r.frames(5)] == [0, 1, 0, 1, 2]
    r = rig(op2(0x94, 2) + draw(0, 0) + mark(0xFF))
    job = r.job(BASE)
    r.frames(2)
    assert not job.running


def test_jumps_now_and_deferred() -> None:
    target = BASE + 6 + 6 + 2
    code = op6(0x84, 0, target) + draw(0, 0) + mark() + draw(0, 1) + mark(0xFF)
    r = rig(code)
    r.job(BASE)
    assert [f[0][1] for f in r.frames(2)] == [0, 1]
    code = op6(0x84, 3, BASE + 6 + 8) + draw(0, 0) + mark(0xFF) + draw(0, 2) + mark(0xFF)
    r = rig(code)
    r.job(BASE)
    assert r.frames(1)[0][0][1] == 2


def test_return_and_loop2_jumps_and_zero_targets() -> None:
    r = rig(bytes((0xFD,)) + mark(0xFF))
    job = r.job(BASE)
    job.x = 33
    r.engine.tick()
    assert (job.script, r.actor().x) == (0, 0)
    code = op2(0x94, 1) + draw(0, 0) + mark() + bytes((0xFE,))
    r = rig(code)
    job = r.job(BASE)
    assert [f[0][1] for f in r.frames(2)] == [0, 0]


def test_an_unknown_opcode_stops_without_write_back() -> None:
    r = rig(bytes((0x90, 0)))
    job = r.job(BASE)
    r.engine.tick()
    assert (job.script, r.actor().x) == (BASE, 0)


def test_a_runaway_script_is_an_error() -> None:
    r = rig(op6(0x84, 3, BASE))
    r.job(BASE)
    with pytest.raises(ScriptError, match="without a marker"):
        r.engine.tick()


# ---- motion --------------------------------------------------------------------------------


def motion(count: int, flags: int, vy: int, vy_limit: int, vx: int = 0, vx_limit: int = 0) -> bytes:
    return bytes((0x8C, 0, count, flags, vy, vy_limit, vx, vx_limit))


def test_rise_halves_the_speed_and_runs_out_without_clearing_active() -> None:
    r = rig(motion(2, 0x01, 8, 2) + draw(0, 0) + mark() + draw(0, 1) + mark(0xFF))
    job = r.job(BASE)
    frames = r.frames(4)
    heights = [f[0][3] - 50 for f in frames]
    assert heights == [0, -8, -12, -12]
    assert [f[0][1] for f in frames] == [0, 0, 0, 1]
    assert job.work.motion_active


def test_a_fall_that_does_not_land_adds_ff_xx_to_x() -> None:
    job = Job(list_base=0, x=100, height=0xFFEC, facing=1)
    work = job.work
    work.motion_flags, work.vy, work.vy_limit, work.vx, work.vx_limit = 0x12, 4, 16, 3, 0
    work.motion_active, work.return_pos = True, 40
    motion_step(job)
    assert job.height == 0xFFF0
    assert work.vy == 0xE0
    assert job.x == (100 + 0xFF03) & 0xFFFF
    assert work.vx == 1
    assert job.script == 40


def test_landing_clears_the_fall_and_keeps_the_plain_speed() -> None:
    job = Job(list_base=0, x=100, height=0xFFFE, facing=3)
    work = job.work
    work.motion_flags, work.vy, work.vx, work.vx_limit = 0x52, 4, 3, 5
    work.motion_active = True
    job.script = 9
    motion_step(job)
    assert (job.height, work.motion_flags, job.x, job.script) == (0, 0x50, 97, 9)


def test_a_step_that_moves_nothing_ends_the_motion() -> None:
    job = Job(list_base=0)
    job.work.motion_active = True
    motion_step(job)
    assert not job.work.motion_active


# ---- control opcodes -----------------------------------------------------------------------


def test_facing_set_and_toggle_reach_the_owner_at_once() -> None:
    r = rig(op2(0x80, 3) + op2(0x80, 0xFF) + op2(0x80, 0xFF) + bytes((0x90,)))
    job = r.job(BASE)
    r.engine.tick()
    assert (job.facing, r.actor().facing) == (3, 3)


def test_move_relative_and_absolute() -> None:
    code = struct.pack(">BBHHH", 0xA0, 0x28, 5, 3, 2) + mark(0xFF)
    r = rig(code)
    job = r.job(BASE)
    job.facing = 3
    r.engine.tick()
    assert (job.x, job.height, job.depth) == (105, 0xFFFD, 48)
    r = rig(struct.pack(">BBHHH", 0xA0, 0x01, 5, 0, 0) + mark(0xFF))
    job = r.job(BASE)
    r.engine.tick()
    assert job.x == 105
    r = rig(struct.pack(">BBHHH", 0xA0, 0x40, 7, 8, 9) + mark(0xFF))
    job = r.job(BASE)
    r.engine.tick()
    assert (job.x, job.height, job.depth) == (7, 8, 9)


def test_poke_and_owner_tests() -> None:
    skip = BASE + 8 + 8 + 8 + 2
    code = op8(0xA8, 4, 22, 0x1234) + op8(0xA8, 1, 76, 0) + op8(0xC8, 1, 76, skip)
    code += mark(0xFF) + op8(0xCC, 0, 22, skip + 10) + mark(0xFF) + draw(0, 2) + mark(0xFF)
    r = rig(code)
    r.actor().daggers = 5
    r.job(BASE)
    assert r.frames(1)[0][0][1] == 2
    assert (r.actor().idle_script, r.actor().daggers) == (0x1234, 0)


def test_sound_and_routine_calls() -> None:
    code = op2(0xA4, 52) + op6(0xB0, 0, 0) + mark(0xFF)
    r = rig(code, routines={BASE + 4: "end_fight"})
    job = r.job(BASE)
    out = r.engine.tick()
    assert out.sounds == [52]
    assert r.calls == [RoutineCall("end_fight", job.owner, 1, 100, 0, 50, 1)]
    r = rig(op6(0xB0, 0, 0) + mark(0xFF))
    r.job(BASE)
    with pytest.raises(ScriptError, match="no known engine routine"):
        r.engine.tick()


def test_the_script_advances_after_the_routine_returns() -> None:
    code = op6(0xB0, 0, 0) + mark(0xFF) + draw(0, 0) + mark(0xFF) + draw(0, 1) + mark(0xFF)
    r = rig(code, routines={BASE + 2: "restart"})
    job = r.job(BASE)

    def restart(_call: RoutineCall) -> None:
        job.script = BASE + 8 - 6 + 8  # the routine moves the script; +6 still follows

    r.engine.call = restart
    assert r.frames(1)[0][0][1] == 1


def test_spawn_kill_and_frame_set() -> None:
    code = op6(0xB8, 0, 77) + op2(0xC0, 2) + draw(0, 0) + bytes((0xBC, 0)) + mark(0xFF)
    r = rig(code)
    r.actor().active = 1
    job = r.job(BASE)
    out = r.engine.tick()
    assert r.spawns == [(77, 1)]
    assert out.draws[0].cel == "b.cel"
    assert (job.active, job.running, r.actor().active, r.actor().frame_list) == (False, False, 0, 2)
    assert r.actor().x == 100
    r = rig(op2(0xC0, 7))
    r.job(BASE)
    with pytest.raises(ScriptError, match="frame list 7"):
        r.engine.tick()


def test_jump_if_dead_resets_the_work_block_but_keeps_the_second_script() -> None:
    target = BASE + 6 + 2
    r = rig(op6(0xB4, 0, target) + mark(0xFF) + draw(0, 1) + mark(0xFF))
    job = r.job(BASE)
    job.work.loop_active, job.work.second_armed, job.work.second_script = True, True, BASE + 6
    r.actor().hp = 0
    assert r.frames(1)[0] == [("a.cel", 1, 100, 50)]
    work = job.work
    assert (work.loop_active, work.second_armed, work.second_script) == (False, True, BASE + 6)
    r = rig(op6(0xB4, 0, target) + mark(0xFF) + draw(0, 1) + mark(0xFF))
    job = r.job(BASE)
    r.actor().hp = 1
    assert r.frames(1) == [[]]


def test_reset_keeps_the_second_script() -> None:
    r = rig(op6(0xAC, 1, 0) + op2(0xD0, 0) + op2(0x88, 4) + bytes((0x90,)))
    job = r.job(BASE)
    job.work.vx = 3
    r.engine.tick()
    assert (job.work.vx, job.work.second_armed, job.work.loop_count) == (0, True, 4)


def test_same_facing_test_looks_up_the_acting_knights_job() -> None:
    code = op6(0xC4, 0, BASE + 6 + 2) + mark(0xFF) + draw(0, 1) + mark(0xFF)
    r = rig(code)
    r.engine.current_knight = 2
    r.job(BASE)
    other = r.job(BASE + 6 + 2 + 6, owner=2, depth=60)
    other.active = False
    assert r.frames(1)[0] == [("a.cel", 1, 100, 50)]
    r = rig(code)
    r.engine.current_knight = 5
    r.job(BASE)
    assert r.frames(1)[0] == []


# ---- the second-script pass and sorting ----------------------------------------------------


def test_second_script_runs_on_a_copy_with_height_zero_and_a_shared_work_block() -> None:
    second = BASE + 6 + 6 + 2 + 6 + 2
    code = op6(0xAC, 1, second) + draw(0, 0, flags=0x01) + mark() + draw(0, 1) + mark(0xFF)
    code += op2(0x88, 3) + draw(1, 0, flags=0x02) + mark() + mark(0xFF)
    r = rig(code)
    job = r.job(BASE)
    job.height = 0xFFF6
    r.engine.tick()
    out = r.engine.tick()
    assert [(d.cel, d.y) for d in out.draws] == [("b.cel", 50), ("a.cel", 40)]
    assert job.script == BASE + 20
    assert r.engine.scratch_work.loop_count == 2
    assert job.work.loop_count == 0


def test_second_script_entries_append_at_the_cursor_of_the_last_main_pass_job() -> None:
    second = BASE + 6 + 6 + 2
    code = op6(0xAC, 1, second) + draw(0, 0, flags=0x02) + mark(0xFF)
    code += draw(1, 0, flags=0x02) + mark(0xFF)
    r = rig(code)
    job = r.job(BASE)
    r.engine.tick()
    r.engine.attack.clear()
    r.engine.tick()
    entries = r.engine.attack.entries
    assert entries[job.list_base] is None
    second_entry = entries[job.list_base + 1]
    assert second_entry is not None
    assert second_entry.cel == "b.cel"
    assert r.engine.attack.read(job.list_base) == []


def test_sort_is_an_unsigned_bubble_on_depth() -> None:
    jobs = [Job(list_base=i * 8, depth=d) for i, d in enumerate((50, 0xFFF0, 10, 30))]
    sort_jobs(jobs)
    assert [j.depth for j in jobs] == [10, 30, 50, 0xFFF0]
    assert [j.list_base for j in jobs] == [16, 24, 0, 8]


def test_paused_and_inactive_jobs_do_not_run() -> None:
    r = rig(draw(0, 0) + mark(0xFF))
    r.job(BASE).paused = 1
    r.job(BASE, owner=2).active = False
    assert r.frames(1) == [[]]


def test_list_overflow_is_an_error() -> None:
    r = rig(draw(0, 0, flags=0x02) * 81 + mark(0xFF))
    r.job(BASE)
    with pytest.raises(ScriptError, match="outside the buffer"):
        r.engine.tick()
