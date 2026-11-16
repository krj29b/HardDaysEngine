"""The fight engine on the real knight scripts (T11.8 acceptance, ARCHITECTURE.md §10.17).

A knight job plays its idle script, each walk script and each of the nine action scripts in
:class:`pymoonstone.sim.fight.Fight`. The draws of every tick are compared with the frames a
second walker computes. That walker is written here from §7.2 / §10.17 independently of
``sim/fight_script.py``: it expands a script into its sequence of frames, supports only the
opcodes the knight's scripts contain, and fails on any other.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from pymoonstone.app.scene_assets import GameAssetBundle
from pymoonstone.assets.adf import DiskSet
from pymoonstone.assets.fight_defs import FightDefs, parse_fight_defs
from pymoonstone.assets.fight_program import (
    KNIGHT_LIST,
    cel_files,
    cel_shapes,
    fight_constants,
    frame_lists,
    script_image,
)
from pymoonstone.assets.hunk import parse_hunk_executable
from pymoonstone.assets.mog_code import MogCode
from pymoonstone.sim.actor import Actor
from pymoonstone.sim.fight import Fight, FightConstants

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from pymoonstone.sim.fight_script import CelShape, ScriptImage

pytestmark = pytest.mark.adf

KNIGHT_TYPE = 0x0C
MAX_TICKS = 80
DAGGERS_OFFSET = 76

Frame = list[tuple[str, int, int, int, bool]]


@dataclass(frozen=True)
class Setup:
    defs: FightDefs
    image: ScriptImage
    constants: FightConstants
    lists: dict[int, tuple[str | None, ...]]
    shapes: dict[str, CelShape]

    @property
    def data(self) -> bytes:
        return self.image.data


@pytest.fixture(scope="module")
def setup(adf_dir: Path) -> Setup:
    bundle = GameAssetBundle(DiskSet.from_directory(adf_dir))
    mog = MogCode(parse_hunk_executable("mog", bundle.disk_set.read_file("mog")))
    defs = parse_fight_defs(mog.executable)
    arena = {a.name: a for a in defs.arenas}["trogg_axe"].cels
    files = cel_files(defs.knight_cels, arena)
    cels = {}
    for name in files:
        cel = bundle.get_cel(name)
        assert cel is not None, name
        cels[name] = cel
    hit_sets = bundle.get_hit_sets()
    assert hit_sets is not None
    shapes = cel_shapes(files, cels, hit_sets)
    lists = frame_lists(defs.knight_cels, arena)
    return Setup(defs, script_image(mog), fight_constants(mog), lists, shapes)


# ---- the independent walker ----------------------------------------------------------------


class Walker:
    """Expands a knight script into frames (draws per tick) for one job standing still."""

    def __init__(self, setup: Setup, owner: Actor, facing: int) -> None:
        self.data = setup.data
        self.lists = setup.lists
        self.shapes = setup.shapes
        self.owner = owner
        self.facing = facing

    def frames(self, start: int, x: int, depth: int) -> Iterator[Frame]:
        pc, frame = start, []
        loop: tuple[int, int] | None = None  # (count left, position)
        deferred: int | None = None
        while True:
            op = self.data[pc]
            if op < 0x80:
                frame.append(self._draw(pc, x, depth))
                pc += 6
            elif op == 0xFF:
                yield frame
                frame = []
                if loop is not None and loop[0] > 1:
                    loop, pc = (loop[0] - 1, loop[1]), loop[1]
                    continue
                loop = None
                if deferred is not None:
                    pc, deferred = deferred, None
                    continue
                if self.data[pc + 1] == 0xFF:
                    return
                pc += 2
            elif op == 0x88:
                assert self.data[pc + 1] != 0, "random loop counts are not walked"
                loop = (self.data[pc + 1], pc + 2)
                pc += 2
            else:
                pc, deferred, loop = self._control(op, pc, deferred, loop)

    def _control(
        self, op: int, pc: int, deferred: int | None, loop: tuple[int, int] | None
    ) -> tuple[int, int | None, tuple[int, int] | None]:
        target = struct.unpack_from(">I", self.data, pc + 2)[0]
        if op == 0x84:
            if self.data[pc + 1] == 3:
                return target, deferred, loop
            return pc + 6, target, loop
        if op in (0xA4, 0xD0, 0x80):
            if op == 0x80:
                self.facing = self.facing ^ 2 if self.data[pc + 1] == 0xFF else self.data[pc + 1]
            return pc + 2, (None if op == 0xD0 else deferred), (None if op == 0xD0 else loop)
        if op == 0xB0:
            return pc + 6, deferred, loop
        if op == 0xB4:
            return (target if self.owner.hp == 0 else pc + 6), deferred, loop
        if op == 0xC8:
            offset = struct.unpack_from(">h", self.data, pc + 2)[0]
            assert self.data[pc + 1] & 1, "only byte tests are walked"
            branch = self.owner.read(offset, 1) == 0
            return (
                (struct.unpack_from(">I", self.data, pc + 4)[0] if branch else pc + 8),
                deferred,
                loop,
            )
        raise AssertionError(f"opcode ${op:02X} at {pc} is not walked")

    def _draw(self, pc: int, x: int, depth: int) -> tuple[str, int, int, int, bool]:
        op, frame, dy, _flags, dx = struct.unpack_from(">BBbBh", self.data, pc)
        name = self.lists[KNIGHT_LIST][op // 4 & 7]
        assert name is not None
        width = self.shapes[name].frames[frame].width
        left = bool(self.facing & 2)
        screen_x = (x - dx - width) if left else (x + dx)
        return name, frame, screen_x & 0xFFFF, (depth + dy) & 0xFFFF, self.facing != 1


# ---- the engine run ------------------------------------------------------------------------


def _engine_trace(setup: Setup, script: int, facing: int) -> tuple[list[Frame], list[int]]:
    knights = [Actor(hp=20, active=1, daggers=10) for _ in range(4)]
    fight = Fight(setup.image, setup.shapes, setup.lists, knights, Actor(), setup.constants)
    fight.first = 1
    job = fight.start_job(script, 1, KNIGHT_LIST, (250, 0, 100), facing, KNIGHT_TYPE)
    assert job is not None
    frames: list[Frame] = []
    positions: list[int] = []
    for tick in range(MAX_TICKS):
        fight.engine.vbl = tick * 6
        drawn = fight.engine.tick().draws
        frames.append([(d.cel, d.frame, d.x, d.y, d.mirrored) for d in drawn])
        positions.append(job.script)
        if not job.running:
            return frames, positions
    raise AssertionError(f"script {script} did not end within {MAX_TICKS} ticks")


def _knight_scripts(defs: FightDefs) -> list[tuple[str, int]]:
    knight = defs.knight
    scripts = [("idle", knight.idle_script)]
    scripts += [(f"walk {i}", s) for i, s in enumerate(knight.walk_scripts) if s]
    scripts += [(f"action {i * 4}", s) for i, s in enumerate(knight.action_scripts)]
    return scripts


@pytest.mark.parametrize("facing", [1, 3])
def test_knight_scripts_play_the_walkers_frames(setup: Setup, facing: int) -> None:
    for name, script in _knight_scripts(setup.defs):
        owner = Actor(hp=20, daggers=10)
        expected = list(Walker(setup, owner, facing).frames(script, 250, 100))
        frames, positions = _engine_trace(setup, script, facing)
        assert frames == expected, f"{name} (script {script}) facing {facing}"
        if len(frames) > 1:
            assert positions[0] != script, f"{name} restarted after its first tick"


def test_attack_scripts_last_several_ticks(setup: Setup) -> None:
    lengths = {
        name: len(_engine_trace(setup, script, 3)[0])
        for name, script in _knight_scripts(setup.defs)
        if name.startswith("action")
    }
    multi = [name for name, length in lengths.items() if length > 1]
    assert len(multi) >= 5, lengths
