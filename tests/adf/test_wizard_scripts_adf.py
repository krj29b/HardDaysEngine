"""Independent wizard script expansion, without using the runtime interpreter (§10.17/12.36)."""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

import pytest

from harddaysengine.app.scene_assets import GameAssetBundle
from harddaysengine.app.wizard_screen import WizardScreen
from harddaysengine.assets.adf import DiskSet
from harddaysengine.sim.actor import Actor
from harddaysengine.sim.fight import Fight
from harddaysengine.sim.rng import Rng

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.adf
Frame = list[tuple[int, int, int]]


class SpeechScript:
    """Expand only draws, second loops and jumps; reject any other shipped instruction."""

    def __init__(self, data: bytes, start: int, position: tuple[int, int, int]) -> None:
        self.data, self.pc, self.position = data, start, position
        self.loop_start, self.loop_left = 0, 0
        self.deferred: int | None = None
        self.ended = False

    def _marker(self, following: int) -> None:
        if self.deferred is not None:
            self.pc, self.deferred = self.deferred, None
        elif following in (254, 255):
            if self.loop_left:
                self.loop_left = (self.loop_left - 1) & 255
            if self.loop_left:
                self.pc = self.loop_start
            elif following == 255:
                self.ended = True

    def next_frame(self) -> Frame:
        if self.ended:
            return []
        result: Frame = []
        for _ in range(200):
            pos = self.pc
            op = self.data[pos]
            if op < 128:
                _, frame, dy, _, dx = struct.unpack_from(">BBbBh", self.data, pos)
                x, height, depth = self.position
                result.append((frame, (x + dx) & 65535, (height + depth + dy) & 65535))
                self.pc += 6
            elif op == 255:
                self.pc += 2
                self._marker(self.data[pos + 1])
                return result
            elif op == 148:
                self.loop_start, self.loop_left, self.pc = pos + 2, self.data[pos + 1], pos + 2
            elif op == 254:
                self.pc = self.loop_start
            elif op == 132:
                target = int.from_bytes(self.data[pos + 2 : pos + 6], "big")
                if self.data[pos + 1] == 3:
                    self.pc = target
                else:
                    self.deferred, self.pc = target, pos + 6
            else:
                raise AssertionError(f"unwalked wizard opcode {op:02x}")
        raise AssertionError("wizard script did not reach a frame marker")


def test_each_original_wizard_script_matches_independent_frame_expansion(adf_dir: Path) -> None:
    screen = WizardScreen(GameAssetBundle(DiskSet.from_directory(adf_dir)), 1, 3, 0)
    engine, rules = screen.engine, screen.rules
    for spawn in rules.spawns:
        fight = Fight(
            engine.image,
            engine.cels,
            {1: (rules.cel,) * 5},
            [Actor() for _ in range(4)],
            Actor(),
            engine.constants,
            rng=Rng(1),
        )
        fight.spawn_actor(spawn.script, 1, spawn.position, spawn.facing, spawn.kind)
        oracle = SpeechScript(engine.image.data, spawn.script, spawn.position)
        for frame in range(120):
            expected = oracle.next_frame()
            output = fight.step(frame * 5)
            assert not output.skipped_calls
            assert [(d.frame, d.x, d.y) for d in output.tick.draws] == expected
            assert all(d.cel == rules.cel and not d.mirrored for d in output.tick.draws)
