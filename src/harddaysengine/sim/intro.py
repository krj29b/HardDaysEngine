"""Deterministic VBL generator player of the ``program`` intro pass (ARCHITECTURE.md §12.42).

:class:`IntroRun` executes an :class:`IntroPlan` one PAL VBL per :meth:`IntroRun.advance`, sharing
the 40-slot :class:`JobPool`, :class:`ScriptRunner`, and :class:`ProgramPalette` with the ending
player (:mod:`harddaysengine.sim.ending`). Pixels never enter the simulation; every screen change
is an :class:`IntroCanvas` call in the original order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from harddaysengine.sim.ending import SCREEN, WORK, EndingCanvas, EndingInputs, load_palette
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
    PictureLoad,
    Select,
    Series,
    Show,
    Spawn,
    SpawnKind,
    StageTable,
    Store,
    Target,
    Text,
    Ticks,
    Wait,
)
from harddaysengine.sim.intro_types import (
    ForwardWipe,
    IntroPlan,
    IntroStep,
    PictureCopy,
    PictureSetup,
    ProgressSpec,
)
from harddaysengine.sim.m68k import s16, u16

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from harddaysengine.sim.turn_rules import TextLine


class IntroCanvas(EndingCanvas, Protocol):
    """Pixel side of the intro: adds the Mindscape picture copy and font-cel banner draw."""

    def mindscape(self, target: int) -> None: ...

    def font_cel(self, target: int, frame: int, x: int, y: int) -> None: ...


class IntroRun:
    """One intro pass of ``program`` (§12.42)."""

    def __init__(
        self,
        plan: IntroPlan,
        inputs: EndingInputs,
        canvas: IntroCanvas,
        flags: int = 0,
    ) -> None:
        self.plan, self.flags, self.inputs, self.canvas = plan, flags, inputs, canvas
        self.palette = ProgramPalette()
        self.pool = JobPool.sized(plan.job_slots)
        self.runner = ScriptRunner(plan.scripts, inputs.widths)
        self.cells = dict.fromkeys(Cell, 0)
        self.tables: dict[int, tuple[int, ...]] = {}
        self.cell = list(plan.palette_cell)
        self.shown, self.target, self.stage = 0, WORK, 0
        self.tick = self.stamp = self.toggle = self.step_index = self.page = 0
        self.progress = self.wipe_step = self.wipe_base = 0
        self.palette_period = plan.palette_period
        self.music = self.done = False
        self._callbacks = dict(plan.callbacks)
        self._flow = self._main()

    def advance(self) -> bool:
        """Run until one VBL has passed; True once the intro has handed over to ``Mog``."""
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
            "page": self.page,
            "cells": {cell.name: value for cell, value in self.cells.items()},
            "shown": self.shown,
            "target": self.target,
            "stage": self.stage,
            "toggle": self.toggle,
            "wipe": (self.progress, self.wipe_step, self.wipe_base),
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

    def _execute(self, step: IntroStep) -> Iterator[None]:
        match step:
            case Invoke(call=call):
                yield from self._invoke(call)
            case Load():
                yield from self._intro_load()
            case ForwardWipe() as wipe:
                yield from self._forward_wipe(wipe)
            case Pairs(scripts=scripts):
                yield from self._pairs(scripts)
            case Series(scripts=scripts, first_frame=first_frame):
                yield from self._series(scripts, first_frame)
            case Ticks(passes=passes):
                for _ in range(passes):
                    yield from self._pass(frame_body=False)
            case Wait(ticks=ticks):
                yield from self._wait(ticks)
            case PaletteTo(palette=palette):
                yield from self._palette_to(self.tables[palette], self.palette_period)
            case _:
                yield from self._execute_more(step)

    def _execute_more(self, step: IntroStep) -> Iterator[None]:
        if isinstance(step, Caption):
            yield from self._caption(step.lines)
            return
        self._instant(step)

    def _instant(self, step: IntroStep) -> None:
        match step:
            case PictureSetup(copies=copies):
                self._picture_setup(copies)
            case Store(cell=cell, value=value):
                self.cells[cell] = value
            case StageTable(palette=palette):
                self.stage = palette
            case Select(source=source):
                self.shown = int(source)
            case Target(buffer=buffer):
                self.target = self._resolve(buffer)
            case Show(source=source):
                self.canvas.copy(int(source), self.shown)
            case Spawn(kind=kind, script=script):
                self._spawn(kind, script)
            case _:
                self._canvas_instant(step)

    def _canvas_instant(self, step: IntroStep) -> None:
        match step:
            case Clear(buffer=buffer):
                self.canvas.clear(self._resolve(buffer))
            case FadeBlack(period=period):
                self.palette.fade(BLACK, period)
            case Text(lines=lines):
                self.canvas.text(self.target, lines)
            case _:
                raise ValueError(f"intro: step {step!r} is not understood")

    def _resolve(self, buffer: Buffer) -> int:
        return self.shown if buffer == Buffer.SHOWN else int(buffer)

    def _invoke(self, call: Call) -> Iterator[None]:
        if call == Call.FADE_OUT:
            yield from self._fade_out()
            return
        if call == Call.FRAMES:
            yield from self._frames()
            return
        instant: dict[Call, Callable[[], None]] = {
            Call.JOBS_RESET: self.pool.reset,
            Call.BLACKOUT: self.palette.blackout,
            Call.FLIP: self._flip,
            Call.PREPARE: self._prepare,
            Call.MUSIC: self._music,
            Call.STAGE: self._stage_now,
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

    def _palette_to(self, colours: Sequence[int], period: int) -> Iterator[None]:
        self.palette_period = period
        self.palette.fade(colours, period)
        yield from self._wait(period << 4)

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
        yield from self._palette_to(colours, self.palette_period)

    # -- intro loader & progress screens (§12.42.2-12.42.3) --------------------------------

    def _load_picture_palette(self, picture: PictureLoad) -> None:
        load_palette(self.cell, self.inputs.palettes[picture.name])
        if picture.palette < 0:
            return
        colours = list(self.cell)
        if picture.class_tweak:
            self._class_tweak(colours)
        self.tables[picture.palette] = tuple(colours)

    def _class_tweak(self, colours: list[int]) -> None:
        first = self.plan.class_first
        for tweak in self.plan.class_tweaks:
            if self.flags >> tweak.bit & 1:
                colours[first : first + len(tweak.colours)] = tweak.colours
                return

    def _patch_table_0(self, entries: Sequence[tuple[int, int]]) -> None:
        colours = list(self.tables[0])
        for entry, word in entries:
            colours[entry] = word
        self.tables[0] = tuple(colours)

    def _patch_table_0_uniform(self, progress: ProgressSpec, word: int) -> None:
        self._patch_table_0(tuple((entry, word) for entry, _ in progress.gold_entries))

    def _step_tail(self, progress: ProgressSpec, *, flip: bool, settle: bool) -> Iterator[None]:
        if flip:
            self._flip()
        self.palette_period = progress.tail_period
        self._patch_table_0(progress.gold_entries)
        self.palette.fade(self.tables[0], progress.tail_period)
        self.canvas.copy(self.shown, WORK)
        if settle:
            yield from self._wait(progress.tail_period << 4)

    def _load_step_0(self, progress: ProgressSpec) -> Iterator[None]:
        yield from self._fade_out()
        self._flip()
        self.canvas.clear(SCREEN)
        self.canvas.clear(WORK)
        self.canvas.clear(self.shown)
        self.wipe_base = self.shown
        self.progress = 0
        self.canvas.wipe(self.shown, 0)
        self._prepare()
        yield from self._step_tail(progress, flip=True, settle=True)

    def _load_step_1(self, progress: ProgressSpec) -> Iterator[None]:
        self._patch_table_0_uniform(progress, progress.black_word)
        self.palette.fade(self.tables[0], 0)
        self.target = WORK
        self.canvas.font_cel(WORK, progress.logo_frame, progress.logo_x, progress.logo_y)
        self._flip()
        yield from self._wait(progress.logo_wait)
        yield from self._step_tail(progress, flip=False, settle=True)

    def _load_step_2(self, progress: ProgressSpec) -> Iterator[None]:
        self._patch_table_0_uniform(progress, progress.white_word)
        yield from self._palette_to(self.tables[0], progress.flash_period)
        yield from self._wait(progress.flash_wait)
        self._patch_table_0_uniform(progress, progress.black_word)
        self.palette.fade(self.tables[0], 0)
        self._flip()
        self._prepare()
        yield from self._load_step_3(progress)

    def _load_step_3(self, progress: ProgressSpec) -> Iterator[None]:
        self._patch_table_0_uniform(progress, progress.white_word)
        yield from self._palette_to(self.tables[0], progress.flash_period)
        self.target = WORK
        self.canvas.copy(self.shown, WORK)
        if self.page < len(progress.credits):
            self.canvas.text(WORK, progress.credits[self.page])
            self.page += 1
            self._patch_table_0_uniform(progress, progress.black_word)
            self.palette.fade(self.tables[0], 0)
            yield from self._step_tail(progress, flip=True, settle=True)
        else:
            yield from self._step_tail(progress, flip=True, settle=False)

    def _intro_load(self) -> Iterator[None]:
        load = self.plan.load
        progress = load.progress
        self.palette.blackout()
        self.target = SCREEN
        self.canvas.mindscape(SCREEN)
        load_palette(self.cell, self.inputs.palettes[load.mindscape])
        yield from self._palette_to(tuple(self.cell), self.palette_period)

        self._load_picture_palette(load.pictures[0])
        yield from self._load_step_0(progress)
        self._load_picture_palette(load.pictures[1])
        yield from self._load_step_1(progress)
        self._load_picture_palette(load.pictures[2])
        yield from self._load_step_2(progress)
        for picture in load.pictures[3:]:
            self._load_picture_palette(picture)
            yield from self._load_step_3(progress)
        for _ in range(load.cels_with_progress):
            yield from self._load_step_3(progress)

    # -- forward wipe & picture setup (§12.42.4-12.42.5) -----------------------------------

    def _forward_wipe(self, wipe: ForwardWipe) -> Iterator[None]:
        self._patch_table_0(wipe.entries)
        self.palette.write(self.tables[0])
        self.palette.copy(self.tables[0])
        last_row = len(wipe.thresholds) - 1
        while s16(self.progress) < wipe.limit:
            row = 0
            while row < last_row and s16(wipe.thresholds[row]) < s16(self.progress):
                row += 1
            self.wipe_step = wipe.steps[row]
            self.stamp = self.tick
            self.progress = min(wipe.limit, u16(self.progress + self.wipe_step))
            self.canvas.wipe(SCREEN, self.progress)
            self._flip()
            if self.wipe_step == wipe.music_step:
                self._music()
            yield from self._pace()
        self.canvas.copy(SCREEN, WORK)
        self.canvas.copy(SCREEN, self.shown)

    def _picture_setup(self, copies: tuple[PictureCopy, ...]) -> None:
        for copy in copies:
            self.canvas.copy(int(copy.src_buffer), int(copy.dst_buffer))
            self.tables[copy.dst_palette] = self.tables[copy.src_palette]

    # -- frame loops, series, pairs & callbacks --------------------------------------------

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

    def _series(self, scripts: tuple[int, ...], first_frame: int) -> Iterator[None]:
        passes = max(0, self.plan.pair_frames - first_frame)
        for script in scripts:
            self._spawn(SpawnKind.LEFT, script)
            for _ in range(passes):
                yield from self._pass(frame_body=True)

    def _body(self) -> None:
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

    def draw(self, slot: int, frame: int, x: int, y: int, mirrored: bool, stamp: bool) -> None:
        if stamp:
            self.canvas.draw(self.shown, slot, frame, x, y, mirrored)
        self.canvas.draw(self.target, slot, frame, x, y, mirrored)

    def random(self) -> int:
        return self.tick

    def call(self, target: tuple[int, int]) -> Iterator[None]:
        callback = self._callbacks.get(target)
        match callback:
            case Flash():
                yield from self._flash(callback)
            case FlashSeries(waits=waits, flash=flash, tail=tail):
                for ticks in waits:
                    yield from self._wait(ticks)
                    yield from self._flash(flash)
                yield from self._wait(tail)
            case _:
                raise ValueError(f"intro script calls an unknown routine {target}")

    def _flash(self, flash: Flash) -> Iterator[None]:
        self.palette.write(flash.colours)
        yield from self._wait(flash.ticks)
        self.palette.write(self.palette.live)
