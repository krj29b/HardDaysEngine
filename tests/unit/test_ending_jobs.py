"""Program job pool, script interpreter and palette (ARCHITECTURE.md §12.39.9-12.39.10)."""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

import pytest

from harddaysengine.sim.ending_jobs import Job, JobPool, ScriptRunner
from harddaysengine.sim.ending_palette import ProgramPalette
from harddaysengine.sim.ending_types import ScriptHunk

if TYPE_CHECKING:
    from collections.abc import Iterator


class _Host:
    def __init__(self) -> None:
        self.draws: list[tuple[int, int, int, int, bool, bool]] = []
        self.calls: list[tuple[int, int]] = []

    def draw(self, slot: int, frame: int, x: int, y: int, mirrored: bool, stamp: bool) -> None:
        self.draws.append((slot, frame, x, y, mirrored, stamp))

    def call(self, target: tuple[int, int]) -> Iterator[None]:
        self.calls.append(target)
        yield

    def random(self) -> int:
        return 0x23


def _runner(data: bytes, relocs: tuple[tuple[int, int], ...] = ()) -> ScriptRunner:
    return ScriptRunner(ScriptHunk(data, relocs), {0: (8, 8, 8), 1: (4,)})


def _frames(runner: ScriptRunner, job: Job, host: _Host, count: int) -> None:
    for _ in range(count):
        for _ in runner.run(job, host):
            pass


def _job(flags: int = 1) -> Job:
    return Job(active=1, has_script=1, x=100, y=0, z=50, flags=flags)


def test_loop_repeats_frames_then_the_script_ends() -> None:
    data = bytes([0x88, 3, 0x04, 0, 0, 0, 0, 0, 0xFF, 0, 0xFF, 0xFF])
    runner, host, job = _runner(data), _Host(), _job()
    _frames(runner, job, host, 4)
    assert [draw[0] for draw in host.draws] == [1, 1, 1]
    assert job.has_script == 0 and job.active == 1


def test_zero_loop_count_draws_its_count_from_the_tick_counter() -> None:
    runner, host, job = _runner(bytes([0x88, 0, 0xFF, 0xFF])), _Host(), _job()
    _frames(runner, job, host, 1)
    assert job.state.loop_count == 2 and job.state.loop_active == 1


def test_jumps_now_and_at_the_end_of_the_frame() -> None:
    data = bytearray(32)
    data[0:6] = bytes([0x84, 1]) + struct.pack(">I", 20)
    data[6:8] = bytes([0xFF, 0])
    data[20:26] = bytes([0x84, 3]) + struct.pack(">I", 28)
    data[28:30] = bytes([0xFF, 0xFF])
    runner, host, job = _runner(bytes(data), ((2, 2), (22, 2))), _Host(), _job()
    _frames(runner, job, host, 1)
    assert job.script == 20 and job.has_script == 1
    _frames(runner, job, host, 1)
    assert job.has_script == 0


def test_moves_invert_x_for_mirrored_jobs_and_set_absolute_positions() -> None:
    data = bytes([0xA0, 0x01, 0, 5, 0, 2, 0, 1, 0xA0, 0x40, 0, 7, 0, 8, 0, 9, 0xFF, 0xFF])
    runner, host = _runner(data), _Host()
    right = _job(flags=3)
    for _ in runner.run(right, host):
        pass
    assert (right.x, right.y, right.z) == (7, 8, 9)
    left = _job()
    left.script = 0
    runner = _runner(data[:8] + bytes([0xFF, 0xFF]))
    for _ in runner.run(left, host):
        pass
    assert (left.x, left.y, left.z) == (105, 2, 51)
    mirrored = _job(flags=3)
    for _ in runner.run(mirrored, host):
        pass
    assert mirrored.x == 95


def test_second_loop_restarts_at_fe_markers() -> None:
    data = bytes([0x94, 2, 0x00, 0, 0, 0, 0, 0, 0xFF, 0xFE, 0xFF, 0xFF])
    runner, host, job = _runner(data), _Host(), _job()
    _frames(runner, job, host, 3)
    assert len(host.draws) == 2 and job.has_script == 0


def test_mirrored_draws_subtract_dx_and_width_and_stamp_bit_passes_through() -> None:
    data = bytes([0x00, 2, 0xFE, 0x10, 0, 3, 0xFF, 0xFF])
    runner, host, job = _runner(data), _Host(), _job(flags=3)
    _frames(runner, job, host, 1)
    assert host.draws == [(0, 2, 100 - 3 - 8, 48, True, True)]


def test_calls_resolve_relocated_targets_and_unknown_opcodes_raise() -> None:
    data = bytes([0xB4, 0, 0, 0, 0x12, 0x34, 0xFF, 0xFF])
    runner, host, job = _runner(data, ((2, 31),)), _Host(), _job()
    _frames(runner, job, host, 1)
    assert host.calls == [(31, 0x1234)]
    with pytest.raises(ValueError, match="not relocated"):
        _frames(_runner(data), _job(), host, 1)
    with pytest.raises(ValueError, match="not supported"):
        _frames(_runner(bytes([0xCC, 0, 0, 0, 0, 0, 0, 0])), _job(), host, 1)
    with pytest.raises(ValueError, match="does not exist"):
        _frames(_runner(bytes([0x04, 3, 0, 0, 0, 0])), _job(), host, 1)


def test_pool_spawns_into_free_slots_sorts_by_depth_and_finishes() -> None:
    pool = JobPool.sized(3)
    assert (
        pool.spawn(10, 1, 0, 30, 1) and pool.spawn(20, 2, 0, 10, 1) and pool.spawn(30, 3, 0, 20, 3)
    )
    assert not pool.spawn(40, 4, 0, 0, 1)
    pool.sort()
    assert [job.script for job in pool.jobs] == [20, 30, 10]
    assert not pool.finish()
    pool.jobs[1].has_script = 0
    assert pool.finish() and [job.active for job in pool.jobs] == [1, 0, 1]
    pool.reset()
    assert not any(job.active for job in pool.jobs)


def test_palette_fade_steps_every_period_and_writes_registers_only_on_change() -> None:
    palette = ProgramPalette()
    palette.fade([0x0F0] + [0] * 31, 2)
    palette.tick()
    assert palette.live[0] == 0 and palette.target is not None
    palette.tick()
    assert palette.live[0] == 0x010 and palette.regs[0] == 0x010
    palette.write([0xFFF] * 32)
    palette.tick()
    assert palette.regs[0] == 0xFFF
    palette.tick()
    assert palette.regs[0] == 0x020
    for _ in range(40):
        palette.tick()
    assert palette.live[0] == 0x0F0 and palette.target is None


def test_ramps_swing_back_and_free_after_their_repeats() -> None:
    palette = ProgramPalette()
    palette.copy([0x001] + [0] * 31)
    slot = palette.ramp(0, 0x003, 1, 2)
    assert slot == 0
    seen = []
    for _ in range(4):
        palette.tick()
        seen.append(palette.live[0])
    assert seen == [0x002, 0x003, 0x002, 0x001]
    assert palette.ramps[0] is None
    endless = palette.ramp(0, 0x002, 1, 0)
    assert endless == 0
    for _ in range(10):
        palette.tick()
    assert palette.ramps[0] is not None
    palette.free(0)
    assert palette.ramps[0] is None
