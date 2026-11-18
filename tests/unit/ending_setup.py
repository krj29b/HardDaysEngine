"""Synthetic ending plans and a recording canvas for the player tests (§12.39.10)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harddaysengine.sim.ending import EndingInputs, EndingRun
from harddaysengine.sim.ending_types import (
    Buffer,
    Cell,
    ClassTweak,
    EndingLoad,
    EndingPlan,
    EndingTweak,
    Overlay,
    OverlayDraw,
    PictureLoad,
    RampSpec,
    ScriptHunk,
    SpawnSide,
    TextFont,
    WipeSpec,
)

if TYPE_CHECKING:
    from harddaysengine.sim.ending_types import Callback, Step
    from harddaysengine.sim.turn_rules import TextLine

PALETTE = tuple(0x8000 | index for index in range(32))
MESSAGE = tuple(0x8000 | (index << 4) for index in range(16))


class Recorder:
    """Every canvas call in order."""

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def flip(self) -> None:
        self.calls.append(("flip",))

    def copy(self, source: int, target: int) -> None:
        self.calls.append(("copy", source, target))

    def clear(self, target: int) -> None:
        self.calls.append(("clear", target))

    def draw(self, target: int, slot: int, frame: int, x: int, y: int, mirrored: bool) -> None:
        self.calls.append(("draw", target, slot, frame, x, y, mirrored))

    def overlay(self, frame: int, x: int, y: int) -> None:
        self.calls.append(("overlay", frame, x, y))

    def text(self, target: int, lines: tuple[TextLine, ...]) -> None:
        self.calls.append(("text", target, len(lines)))

    def message(self, target: int) -> None:
        self.calls.append(("message", target))

    def wipe(self, target: int, progress: int) -> None:
        self.calls.append(("wipe", target, progress))

    def named(self, name: str) -> list[tuple[object, ...]]:
        return [call for call in self.calls if call[0] == name]


def plan(
    steps: tuple[Step, ...],
    scripts: bytes = b"\xff\xff",
    *,
    relocs: tuple[tuple[int, int], ...] = (),
    callbacks: tuple[tuple[tuple[int, int], Callback], ...] = (),
) -> EndingPlan:
    load = EndingLoad(
        pictures=(PictureLoad("pic", Buffer.P5, 3, ending_tweak=True, class_tweak=True),),
        cleared=Buffer.P2,
        cels=((0, "cel"),),
        overlay="ov",
        stile="st",
        stile_bytes=1000,
        music="mu",
        music_bytes=1,
    )
    return EndingPlan(
        flag_bit=0x80,
        steps=steps,
        load=load,
        ending_tweaks=(
            EndingTweak(1, ((12, 0x0F80),), (0x0C50, 0x0920, 0x0700)),
            EndingTweak(0, ((12, 0x0111),), (0x0111, 0x0333, 0x0555)),
        ),
        class_tweaks=(ClassTweak(4, (1, 2, 3, 4)), ClassTweak(5, (5, 6, 7, 8))),
        class_first=8,
        ramp_defaults=(0, 0, 0),
        ramps=(RampSpec(12, 0, 5), RampSpec(15, 1, 5), RampSpec(23, 2, 5)),
        spawns=(
            SpawnSide(160, 0, 100, 1, Cell.LEFT_DEPTH),
            SpawnSide(120, 0, 100, 3, Cell.RIGHT_DEPTH),
        ),
        pair_frames=2,
        caption_entries=((1, 0x800), (2, 0x600)),
        fade_period=2,
        fade_wait=36,
        palette_period=2,
        next_program="Mog",
        overlay=Overlay((OverlayDraw(0, 0, 56), OverlayDraw(1, 157, 104)), 1, 2),
        callbacks=callbacks,
        scripts=ScriptHunk(scripts, relocs),
        font=TextFont("font", 0, 2, 320),
        job_slots=4,
        wipe=WipeSpec(1000, 9, 2, 0, 200),
        stage_period=2,
        message="msg",
        palette_cell=(0,) * 32,
    )


def run(ending: EndingPlan, flags: int = 0x80) -> tuple[EndingRun, Recorder]:
    canvas = Recorder()
    inputs = EndingInputs({"pic": PALETTE}, MESSAGE, {0: (10, 20, 30)})
    return EndingRun(ending, flags, inputs, canvas), canvas


def finish(ending: EndingRun, limit: int = 10000) -> int:
    """Advance until done; return the VBLs that passed."""
    for _ in range(limit):
        if ending.advance():
            return ending.tick
    raise AssertionError("ending did not finish")
