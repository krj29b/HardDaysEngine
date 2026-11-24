"""Fight sound requests and the sound pickers (ARCHITECTURE.md §10.32).

Every effect of the original goes through the hunk-16 sound front end: *play* on the next free
channel, *fixed* on a named channel, or *release* a channel. The fight reports each call as one
:class:`SoundRequest`, in execution order. Choosing channels and playing sequences is
presentation; nothing here reads it back.

The sound pickers are engine routines (§10.18) built from a few step kinds. Their draws come from
the shared generator, so they are part of the PRNG order. Their counters are executable cells
that persist for the session (:class:`SoundCells`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from functools import partial
from typing import TYPE_CHECKING

from harddaysengine.sim.m68k import s16, u16

if TYPE_CHECKING:
    from collections.abc import Callable

    from harddaysengine.sim.fight import Fight
    from harddaysengine.sim.fight_script import RoutineCall
    from harddaysengine.sim.rng import Rng


class SoundError(ValueError):
    """A picker read outside the decoded executable data (unreachable with original data)."""


class SoundKind(IntEnum):
    """The three front-end entries (§10.32) and a direct synth start bypassing them (§12.31)."""

    PLAY = 0
    FIXED = 1
    RELEASE = 2
    START = 3


@dataclass(slots=True, frozen=True)
class SoundRequest:
    """One front-end call.

    ``sound`` is the sequence id (for a release, the silence sequence it then plays). ``channel``
    names the channel of a fixed, release or start request and is 0 for play. ``mask`` is the
    busy-byte mask of a release and 0 otherwise.
    """

    kind: SoundKind
    sound: int
    channel: int = 0
    mask: int = 0


def play(sound: int) -> SoundRequest:
    """A play request: the next free channel (§10.32)."""
    return SoundRequest(SoundKind.PLAY, u16(sound))


@dataclass(slots=True)
class SoundCells:
    """The persistent picker cells: plain cycle, per-list offsets and counter."""

    plain: int = 0
    list_offsets: list[int] = field(default_factory=lambda: [0, 0])
    counter: int = 0


@dataclass(slots=True, frozen=True)
class Emit:
    """A constant request (``MOVE.W #id,D0`` and a call, or a release entry)."""

    request: SoundRequest


@dataclass(slots=True, frozen=True)
class DrawPick:
    """One draw masked with ``mask``; ``choices[value]`` are the play ids, in order."""

    mask: int
    choices: tuple[tuple[int, ...], ...]


@dataclass(slots=True, frozen=True)
class CycleStep:
    """The plain cycle: step the word; at or above ``limit`` (signed) store ``reset``."""

    step: int
    limit: int
    reset: int
    base: int


@dataclass(slots=True, frozen=True)
class ListStep:
    """A list cycle over decoded words with its own independent offset slot."""

    start: int
    words: tuple[int, ...]
    step: int
    terminator: int
    reset: int
    slot: int = 0


@dataclass(slots=True, frozen=True)
class CounterStep:
    """Step the counter word, AND it with ``mask``; play ``sound`` when the result is zero."""

    step: int
    mask: int
    sound: int


type SoundStep = Emit | DrawPick | CycleStep | ListStep | CounterStep


@dataclass(slots=True, frozen=True)
class SoundRules:
    """Every sound-only engine routine by name, and the cells' values in the executable."""

    routines: tuple[tuple[str, tuple[SoundStep, ...]], ...]
    cells: SoundCells

    def initial_cells(self) -> SoundCells:
        """A fresh copy of the executable's cell values, for one session."""
        return SoundCells(self.cells.plain, list(self.cells.list_offsets), self.cells.counter)


def _list_word(step: ListStep, offset: int) -> int:
    position = step.start + s16(offset)
    if position < 0 or position % 2 or position // 2 >= len(step.words):
        raise SoundError(f"sound list read at byte {position} outside the decoded words")
    return step.words[position // 2]


def _list_pick(step: ListStep, cells: SoundCells) -> int:
    if step.slot >= len(cells.list_offsets):
        cells.list_offsets.extend([0] * (step.slot + 1 - len(cells.list_offsets)))
    offset = u16(cells.list_offsets[step.slot] + step.step)
    word = _list_word(step, offset)
    if word == step.terminator:
        offset = step.reset
        word = _list_word(step, offset)
    cells.list_offsets[step.slot] = offset
    return word


def _cycle_pick(step: CycleStep, cells: SoundCells) -> int:
    cells.plain = u16(cells.plain + step.step)
    if s16(cells.plain) >= s16(step.limit):
        cells.plain = step.reset
    return u16(cells.plain + step.base)


def _counter_pick(step: CounterStep, cells: SoundCells) -> int | None:
    cells.counter = u16(cells.counter + step.step) & step.mask
    return step.sound if cells.counter == 0 else None


def run_steps(
    steps: tuple[SoundStep, ...],
    rng: Rng,
    cells: SoundCells,
    emit: Callable[[SoundRequest], None],
) -> None:
    """Run one routine's steps in order (§10.32)."""
    for step in steps:
        match step:
            case Emit(request):
                emit(request)
            case DrawPick(mask, choices):
                for sound in choices[rng.draw() & mask]:
                    emit(play(sound))
            case CycleStep():
                emit(play(_cycle_pick(step, cells)))
            case ListStep():
                emit(play(_list_pick(step, cells)))
            case CounterStep():
                picked = _counter_pick(step, cells)
                if picked is not None:
                    emit(play(picked))


def _routine(steps: tuple[SoundStep, ...], fight: Fight, _call: RoutineCall) -> None:
    run_steps(steps, fight.rng, fight.sound_cells, fight.sounds.append)


def install_sounds(fight: Fight, rules: SoundRules) -> None:
    """Register every sound-only routine of ``rules`` on ``fight`` (§10.32)."""
    for name, steps in rules.routines:
        fight.routines[name] = partial(_routine, steps)
