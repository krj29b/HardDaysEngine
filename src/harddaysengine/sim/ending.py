"""Deterministic player of the decoded ``program`` ending (ARCHITECTURE.md §12.39.10).

:class:`EndingRun` executes an :class:`EndingPlan` one PAL VBL per :meth:`EndingRun.advance`.
Control flow is a generator: every wait, pacing gap and callback wait yields once per VBL after
the palette tick. Pixels never enter the simulation; every screen change is an
:class:`EndingCanvas` call, made in the original order.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from harddaysengine.sim.ending_jobs import JobPool, ScriptRunner
from harddaysengine.sim.ending_palette import BLACK, ProgramPalette
from harddaysengine.sim.ending_types import (
    Buffer,
    Call,
    Caption,
    Cell,
    Clear,
    FadeBlack,
    Flash,
    FlashSeries,
    Invoke,
    Load,
    Pairs,
    PaletteTo,
    RampRelease,
    RampSetup,
    Select,
    Show,
    Spawn,
    SpawnKind,
    StageTable,
    Store,
    Target,
    Text,
    Ticks,
    Wait,
    WipeSlow,
    WipeStop,
)
from harddaysengine.sim.m68k import s16, u16

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping, Sequence

    from harddaysengine.sim.ending_types import EndingPlan, Step
    from harddaysengine.sim.turn_rules import TextLine

SCREEN, WORK = int(Buffer.SCREEN), int(Buffer.WORK)


class EndingCanvas(Protocol):
    """Pixel side of the ending. Buffers 0..9 are pictures; 10 and 11 the shown and work screen."""

    def flip(self) -> None: ...

    def copy(self, source: int, target: int) -> None: ...

    def clear(self, target: int) -> None: ...

    def draw(self, target: int, slot: int, frame: int, x: int, y: int, mirrored: bool) -> None: ...

    def overlay(self, frame: int, x: int, y: int) -> None: ...

    def text(self, target: int, lines: tuple[TextLine, ...]) -> None: ...

    def message(self, target: int) -> None: ...

    def wipe(self, target: int, progress: int) -> None: ...


@dataclass(slots=True, frozen=True)
class EndingInputs:
    """Values read from files: raw picture palette words by file name, message words, widths."""

    palettes: Mapping[str, tuple[int, ...]]
    message: tuple[int, ...]
    widths: Mapping[int, Sequence[int]]


def load_palette(cell: list[int], words: Sequence[int]) -> None:
    """Load picture palette words into the shared cell (bit 15 rule, §12.39.10)."""
    for index, word in enumerate(words):
        cell[index] = word & 0x7FFF if word & 0x8000 else u16(word << 1)


class EndingRun:
    """One ending pass for the boot ``flags`` (§12.39.10)."""

    def __init__(
        self, plan: EndingPlan, flags: int, inputs: EndingInputs, canvas: EndingCanvas
    ) -> None:
        self.plan, self.flags, self.inputs, self.canvas = plan, flags, inputs, canvas
        self.palette = ProgramPalette()
        self.pool = JobPool.sized(plan.job_slots)
        self.runner = ScriptRunner(plan.scripts, inputs.widths)
        self.cells = dict.fromkeys(Cell, 0)
        self.tables: dict[int, tuple[int, ...]] = {}
        self.cell = list(plan.palette_cell)
        self.ramp_colours = list(plan.ramp_defaults)
        self.ramp_slots: list[int | None] = [None] * len(plan.ramps)
        self.shown, self.target, self.stage = 0, WORK, 0
        self.tick = self.stamp = self.toggle = self.step_index = 0
        self.progress = self.wipe_step = self.wipe_stopped = self.wipe_base = 0
        self.music = self.done = False
        self._callbacks = dict(plan.callbacks)
        self._flow = self._main()

    # -- driving ---------------------------------------------------------------------------

    def advance(self) -> bool:
        """Run until one VBL has passed; True once the ending has handed back."""
        if not self.done:
            try:
                next(self._flow)
            except StopIteration:
                self.done = True
        return self.done

    def snapshot(self) -> dict[str, object]:
        return {
            "flags": self.flags,
            "step": self.step_index,
            "tick": self.tick,
            "stamp": self.stamp,
            "cells": {cell.name: value for cell, value in self.cells.items()},
            "shown": self.shown,
            "target": self.target,
            "stage": self.stage,
            "toggle": self.toggle,
            "wipe": (self.progress, self.wipe_step, self.wipe_stopped, self.wipe_base),
            "ramps": (tuple(self.ramp_colours), tuple(self.ramp_slots)),
            "palette": self.palette.snapshot(),
            "cell": tuple(self.cell),
            "jobs": self.pool.snapshot(),
            "music": self.music,
            "done": self.done,
        }

    def _main(self) -> Iterator[None]:
        for index, step in enumerate(self.plan.steps):
            self.step_index = index
            yield from self._execute(step)

    def _vbl(self) -> Iterator[None]:
        self.palette.tick()
        self.tick += 1
        yield

    def _wait(self, ticks: int) -> Iterator[None]:
        for _ in range(ticks):
            yield from self._vbl()

    # -- steps -----------------------------------------------------------------------------

    def _execute(self, step: Step) -> Iterator[None]:
        match step:
            case Invoke(call=call):
                yield from self._invoke(call)
            case Pairs(scripts=scripts):
                yield from self._pairs(scripts)
            case Ticks(passes=passes):
                for _ in range(passes):
                    yield from self._pass(frame_body=False)
            case Wait(ticks=ticks):
                yield from self._wait(ticks)
            case PaletteTo(palette=palette):
                yield from self._palette_to(self.tables[palette])
            case Caption(lines=lines):
                yield from self._caption(lines)
            case _:
                self._instant(step)

    def _instant(self, step: Step) -> None:
        match step:
            case Store(cell=cell, value=value):
                self.cells[cell] = value
            case StageTable(palette=palette):
                self.stage = palette
            case Select(source=source):
                self.shown = int(source)
            case Target(buffer=buffer):
                self.target = self._resolve(buffer)
            case RampRelease(slot=slot):
                self._release(slot)
            case Load():
                self._load()
            case _:
                self._screen(step)

    def _screen(self, step: Step) -> None:
        match step:
            case Show(source=source):
                self.canvas.copy(int(source), self.shown)
            case Spawn(kind=kind, script=script):
                self._spawn(kind, script)
            case Clear(buffer=buffer):
                self.canvas.clear(self._resolve(buffer))
            case FadeBlack(period=period):
                self.palette.fade(BLACK, period)
            case Text(lines=lines):
                self.canvas.text(self.target, lines)
            case _:
                raise ValueError(f"ending: step {step!r} is not understood")

    def _resolve(self, buffer: Buffer) -> int:
        return self.shown if buffer == Buffer.SHOWN else int(buffer)

    def _invoke(self, call: Call) -> Iterator[None]:
        blocking: dict[Call, Callable[[], Iterator[None]]] = {
            Call.FADE_OUT: self._fade_out,
            Call.FRAMES: self._frames,
            Call.LOADING_B: self._wipe_run,
        }
        if call in blocking:
            yield from blocking[call]()
            return
        instant: dict[Call, Callable[[], None]] = {
            Call.JOBS_RESET: self.pool.reset,
            Call.BLACKOUT: self.palette.blackout,
            Call.FLIP: self._flip,
            Call.PREPARE: self._prepare,
            Call.MUSIC: self._music,
            Call.STAGE: self._stage_now,
            Call.LOADING_A: self._wipe_setup,
        }
        instant[call]()

    def _flip(self) -> None:
        self.canvas.flip()
        self.target = WORK

    def _prepare(self) -> None:
        self.canvas.copy(self.shown, SCREEN)
        self.canvas.copy(self.shown, WORK)

    def _music(self) -> None:
        self.music = True

    def _stage_now(self) -> None:
        table = self.tables[self.stage]
        self.palette.write(table)
        self.palette.copy(table)

    def _fade_out(self) -> Iterator[None]:
        self.palette.fade(BLACK, self.plan.fade_period)
        yield from self._wait(self.plan.fade_wait)

    def _palette_to(self, colours: Sequence[int]) -> Iterator[None]:
        self.palette.fade(colours, self.plan.palette_period)
        yield from self._wait(self.plan.palette_period << 4)

    def _caption(self, lines: tuple[TextLine, ...]) -> Iterator[None]:
        self.palette.blackout()
        self.canvas.clear(SCREEN)
        self.target = SCREEN
        self.canvas.message(SCREEN)
        self.canvas.text(SCREEN, lines)
        load_palette(self.cell, self.inputs.message)
        colours = list(self.cell)
        for entry, colour in self.plan.caption_entries:
            colours[entry] = colour
        yield from self._palette_to(colours)

    # -- loader ----------------------------------------------------------------------------

    def _load(self) -> None:
        """Palette tables with the moon and class tweaks of the boot flags (§12.39.3)."""
        for picture in self.plan.load.pictures:
            load_palette(self.cell, self.inputs.palettes[picture.name])
            colours = list(self.cell)
            if picture.ending_tweak:
                self._moon_tweak(colours)
            if picture.class_tweak:
                self._class_tweak(colours)
            self.tables[picture.palette] = tuple(colours)

    def _moon_tweak(self, colours: list[int]) -> None:
        for tweak in self.plan.ending_tweaks:
            if self.flags >> tweak.bit & 1:
                for entry, colour in tweak.entries:
                    colours[entry] = colour
                self.ramp_colours = list(tweak.ramp_colours)
                return

    def _class_tweak(self, colours: list[int]) -> None:
        first = self.plan.class_first
        for tweak in self.plan.class_tweaks:
            if self.flags >> tweak.bit & 1:
                colours[first : first + len(tweak.colours)] = tweak.colours
                return

    # -- frame loops -----------------------------------------------------------------------

    def _spawn(self, kind: SpawnKind, script: int) -> None:
        side = self.plan.spawns[kind]
        z = u16(side.z + self.cells[side.depth])
        self.pool.spawn(script, side.x, side.y, z, side.flags)

    def _finish(self) -> None:
        if self.pool.finish():
            self.cells[Cell.STOP] = 1

    def _jobs(self) -> Iterator[None]:
        self.pool.sort()
        for index in range(len(self.pool.jobs)):
            job = self.pool.jobs[index]
            if job.active:
                yield from self.runner.run(job, self)

    def _frames(self) -> Iterator[None]:
        while True:
            self.stamp = self.tick
            self._finish()
            if self.cells[Cell.STOP]:
                return
            yield from self._jobs()
            self._body()
            yield from self._pace()

    def _pass(self, *, frame_body: bool) -> Iterator[None]:
        """One counted (pairs) or bare (ticks) pass: no stop test."""
        self.stamp = self.tick
        self._finish()
        yield from self._jobs()
        if frame_body:
            self._body()
        else:
            self._flip()
            self.canvas.copy(self.shown, WORK)
        yield from self._pace()

    def _pairs(self, scripts: tuple[int, ...]) -> Iterator[None]:
        for script in scripts:
            self.toggle ^= 1
            self._spawn(SpawnKind.LEFT if self.toggle else SpawnKind.RIGHT, script)
            for _ in range(self.plan.pair_frames):
                yield from self._pass(frame_body=True)

    def _body(self) -> None:
        """Overlay, flip, dirty-rectangle restore and the first-frame stage palette."""
        if self.cells[Cell.CREDITS]:
            overlay = self.plan.overlay
            skip = self.cells[Cell.OVERLAY_PHASE] == overlay.skip_phase
            for draw in overlay.draws[: overlay.kept] if skip else overlay.draws:
                self.canvas.overlay(draw.frame, draw.x, draw.y)
        self.canvas.flip()
        self.target = WORK
        self.canvas.copy(self.shown, WORK)
        if not self.cells[Cell.STAGED]:
            self.cells[Cell.STAGED] = 1
            self._stage_palette()

    def _stage_palette(self) -> None:
        mode = self.cells[Cell.STAGE_MODE]
        if mode == 2:
            self.palette.fade(self.tables[self.stage], self.plan.stage_period)
        elif mode == 3:
            self.palette.fade(BLACK, self.plan.stage_period)
        elif mode == 5:
            self.palette.blackout()
        else:
            self._stage_now()

    def _pace(self) -> Iterator[None]:
        budget = self.cells[Cell.SPEED] + self.cells[Cell.GAP]
        yield from self._wait(max(0, budget - (self.tick - self.stamp)))

    # -- reverse wipe ----------------------------------------------------------------------

    def _wipe_setup(self) -> None:
        wipe = self.plan.wipe
        self._flip()
        self.canvas.clear(SCREEN)
        self.wipe_base = self.shown
        self.cells[Cell.SPEED], self.cells[Cell.GAP] = wipe.speed, wipe.gap
        self.progress, self.wipe_step, self.wipe_stopped = wipe.start, wipe.step, 0
        self.canvas.wipe(self.wipe_base, self.progress)
        self.canvas.copy(self.wipe_base, WORK)
        self._flip()

    def _wipe_run(self) -> Iterator[None]:
        while not self.wipe_stopped:
            self.stamp = self.tick
            self.progress = u16(self.progress - self.wipe_step)
            if s16(self.progress) < 0:
                self.progress = 0
            self.canvas.wipe(self.wipe_base, self.progress)
            self.canvas.copy(self.wipe_base, WORK)
            self._finish()
            yield from self._jobs()
            self._flip()
            yield from self._pace()
            if s16(self.progress) < self.plan.wipe.floor:
                break
        self.canvas.copy(SCREEN, WORK)
        self.canvas.copy(SCREEN, self.shown)

    # -- script host -----------------------------------------------------------------------

    def draw(self, slot: int, frame: int, x: int, y: int, mirrored: bool, stamp: bool) -> None:
        if stamp:
            self.canvas.draw(self.shown, slot, frame, x, y, mirrored)
        self.canvas.draw(self.target, slot, frame, x, y, mirrored)

    def random(self) -> int:
        return self.tick

    def call(self, target: tuple[int, int]) -> Iterator[None]:
        callback = self._callbacks.get(target)
        match callback:
            case RampSetup():
                self._ramp_setup()
            case Flash():
                yield from self._flash(callback)
            case FlashSeries(waits=waits, flash=flash, tail=tail):
                for ticks in waits:
                    yield from self._wait(ticks)
                    yield from self._flash(flash)
                yield from self._wait(tail)
            case WipeSlow(amount=amount):
                self.wipe_step = u16(self.wipe_step - amount)
            case WipeStop():
                self.wipe_stopped = 1
            case _:
                raise ValueError(f"ending script calls an unknown routine {target}")

    def _flash(self, flash: Flash) -> Iterator[None]:
        self.palette.write(flash.colours)
        yield from self._wait(flash.ticks)
        self.palette.write(self.palette.live)

    def _ramp_setup(self) -> None:
        for index, spec in enumerate(self.plan.ramps):
            colour = self.ramp_colours[spec.colour]
            self.ramp_slots[index] = self.palette.ramp(spec.entry, colour, spec.rate, 0)

    def _release(self, slot: int) -> None:
        handle = self.ramp_slots[slot]
        if handle is not None:
            self.palette.free(handle)
