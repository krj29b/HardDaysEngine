"""The ``program`` job pool and its animation script interpreter (ARCHITECTURE.md §12.39.9).

Jobs are sorted by depth before every script pass. A script runs until its next ``$FF`` marker;
draws go to the host, and ``$B4`` calls run decoded callbacks, which may wait (so a script pass is
a generator that yields once per VBL the callback waits).
"""

from __future__ import annotations

import struct
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Protocol

from harddaysengine.sim.m68k import s8, s16, u16

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

    from harddaysengine.sim.ending_types import ScriptHunk

SCRIPT_HUNK = 2


class ScriptHost(Protocol):
    def draw(self, slot: int, frame: int, x: int, y: int, mirrored: bool, stamp: bool) -> None: ...

    def call(self, target: tuple[int, int]) -> Iterator[None]: ...

    def random(self) -> int: ...


@dataclass(slots=True)
class JobState:
    loop_count: int = 0
    loop_active: int = 0
    p_loop: int = 0
    loop2_count: int = 0
    loop2_active: int = 0
    p_loop2: int = 0
    p_deferred: int = 0
    deferred: int = 0


@dataclass(slots=True)
class Job:
    active: int = 0
    has_script: int = 0
    script: int = 0
    x: int = 0
    y: int = 0
    z: int = 0
    flags: int = 0
    frame: int = 0
    state: JobState = field(default_factory=JobState)


@dataclass(slots=True)
class JobPool:
    """Fixed pool; a slot is taken by the first inactive job, its state block cleared (§12.39.9)."""

    jobs: list[Job]

    @classmethod
    def sized(cls, count: int) -> JobPool:
        return cls([Job() for _ in range(count)])

    def reset(self) -> None:
        for index in range(len(self.jobs)):
            self.jobs[index] = Job()

    def spawn(self, script: int, x: int, y: int, z: int, flags: int) -> bool:
        for job in self.jobs:
            if not job.active:
                job.state = JobState()
                job.script, job.x, job.y, job.z, job.flags = script, x, y, z, flags
                job.active = job.has_script = 1
                return True
        return False

    def finish(self) -> bool:
        """Handler 0 for every job whose script ended: free it; True when any was freed."""
        finished = False
        for job in self.jobs:
            if job.active and not job.has_script:
                job.active = job.has_script = 0
                finished = True
        return finished

    def sort(self) -> None:
        """Bubble passes swapping neighbours while the next one is shallower."""
        swapped = True
        while swapped:
            swapped = False
            for index in range(len(self.jobs) - 1):
                if self.jobs[index + 1].z < self.jobs[index].z:
                    self.jobs[index], self.jobs[index + 1] = self.jobs[index + 1], self.jobs[index]
                    swapped = True

    def snapshot(self) -> tuple[dict[str, object], ...]:
        return tuple(asdict(job) for job in self.jobs)


@dataclass(slots=True)
class ScriptRunner:
    """Interpreter of the script hunk (§12.39.9); ``widths[slot][frame]`` sizes mirrored draws."""

    scripts: ScriptHunk
    widths: Mapping[int, Sequence[int]]
    relocs: dict[int, int] = field(init=False)

    def __post_init__(self) -> None:
        self.relocs = dict(self.scripts.relocs)

    def _long(self, pos: int) -> int:
        value: int = struct.unpack_from(">I", self.scripts.data, pos)[0]
        return value

    def _target(self, pos: int) -> tuple[int, int]:
        hunk = self.relocs.get(pos)
        if hunk is None:
            raise ValueError(f"ending script: long at {pos:#x} is not relocated")
        return hunk, self._long(pos)

    def _jump(self, pos: int) -> int:
        hunk, offset = self._target(pos)
        if hunk != SCRIPT_HUNK:
            raise ValueError(f"ending script: jump at {pos:#x} leaves the script hunk")
        return offset

    def run(self, job: Job, host: ScriptHost) -> Iterator[None]:
        """Run ``job`` up to and including its next end-of-frame marker."""
        data = self.scripts.data
        while True:
            pos = job.script
            op = data[pos]
            if op == 0xFF:
                self._end_frame(job, data[pos + 1])
                return
            if op == 0xFE:
                job.script = job.state.p_loop2
            elif op == 0xB4:
                yield from self._call(job, host)
            elif op & 0x80:
                self._control(job, op, host)
            else:
                self._draw(job, op, host)

    def _call(self, job: Job, host: ScriptHost) -> Iterator[None]:
        pos = job.script
        if self.scripts.data[pos + 1]:
            raise ValueError(f"ending script: table call at {pos:#x} is not used by the ending")
        yield from host.call(self._target(pos + 2))
        job.script = pos + 6

    def _control(self, job: Job, op: int, host: ScriptHost) -> None:
        data, pos = self.scripts.data, job.script
        sizes = {0x8C: 8, 0xA4: 4, 0xB8: 6, 0xBC: 6, 0xC8: 6}
        if op in sizes:
            job.script = pos + sizes[op]
        elif op == 0x80:
            job.flags = job.flags ^ 2 if data[pos + 1] == 0xFF else data[pos + 1]
            job.script = pos + 2
        elif op == 0x84:
            self._jump_op(job, data[pos + 1] == 3)
        elif op in (0x88, 0x94):
            self._loop(job, op, data[pos + 1], host)
        elif op == 0xA0:
            self._move(job)
        elif op == 0xC0:
            job.active = job.has_script = 0
            job.script = pos + 2
        elif op == 0xD4:
            job.state = JobState()
            job.script = pos + 2
        else:
            raise ValueError(f"ending script: opcode ${op:02X} at {pos:#x} is not supported")

    def _jump_op(self, job: Job, now: bool) -> None:
        target = self._jump(job.script + 2)
        if now:
            job.script = target
            return
        job.state.p_deferred, job.state.deferred = target, 1
        job.script += 6

    def _loop(self, job: Job, op: int, count: int, host: ScriptHost) -> None:
        state = job.state
        job.script += 2
        if op == 0x94:
            state.loop2_count, state.loop2_active, state.p_loop2 = count, 1, job.script
            return
        if count == 0:
            count = host.random() & 0x1F or 1
        state.loop_count, state.loop_active, state.p_loop = count, 1, job.script

    def _move(self, job: Job) -> None:
        pos = job.script
        mode = self.scripts.data[pos + 1]
        dx, dy, dz = struct.unpack_from(">3H", self.scripts.data, pos + 2)
        if mode & 0x40:
            job.x, job.y, job.z = dx, dy, dz
        else:
            add = not mode & 1 if job.flags == 3 else bool(mode & 1)
            job.x = u16(job.x + dx if add else job.x - dx)
            job.y = u16(job.y - dy if mode & 8 else job.y + dy)
            job.z = u16(job.z - dz if mode & 0x20 else job.z + dz)
        job.script = pos + 8

    def _draw(self, job: Job, op: int, host: ScriptHost) -> None:
        data, pos = self.scripts.data, job.script
        if op & 3:
            raise ValueError(f"ending script: draw ${op:02X} at {pos:#x} is not slot aligned")
        slot, frame = (op & 0x1F) >> 2, data[pos + 1]
        widths = self.widths.get(slot, ())
        if frame >= len(widths):
            raise ValueError(f"ending script: frame {frame} of slot {slot} does not exist")
        dx = struct.unpack_from(">H", data, pos + 4)[0]
        mirrored = bool(job.flags & 2)
        x = job.x - dx - widths[frame] if mirrored else job.x + dx
        y = s8(data[pos + 2]) + job.y + job.z
        job.frame = frame
        host.draw(slot, frame, s16(x), s16(y), mirrored, bool(data[pos + 3] & 0x10))
        job.script = pos + 6

    def _end_frame(self, job: Job, follow: int) -> None:
        state = job.state
        if state.loop_active:
            state.loop_count = (state.loop_count - 1) & 0xFF
            if state.loop_count:
                job.script = state.p_loop
                return
        state.loop_active = 0
        if state.deferred:
            state.deferred = 0
            job.script = state.p_deferred
            return
        if follow != 0xFF:
            if follow == 0xFE and self._repeat_loop2(job):
                return
            job.script += 2
            return
        if not self._repeat_loop2(job):
            job.has_script = 0

    def _repeat_loop2(self, job: Job) -> bool:
        state = job.state
        if state.loop2_active:
            state.loop2_count = (state.loop2_count - 1) & 0xFF
            if state.loop2_count:
                job.script = state.p_loop2
                return True
        state.loop2_active = 0
        return False
