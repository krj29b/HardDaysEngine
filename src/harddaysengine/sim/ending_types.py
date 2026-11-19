"""Decoded ``program`` ending plan records (ARCHITECTURE.md §12.39).

The ending is the second pass of the ``program`` executable. Its scene bodies are straight-line
calls and stores; :mod:`harddaysengine.assets.ending_data` turns them into the step records below
without guessing, and the ending player (T11.22b) executes them in order.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harddaysengine.sim.turn_rules import TextLine


class Buffer(IntEnum):
    """Picture buffers and screens of ``program`` (§12.39.2)."""

    SHOWN = 0  # the picture the scenes draw from
    P1 = 1
    P2 = 2
    P3 = 3
    P4 = 4
    P5 = 5
    P6 = 6
    P7 = 7
    P8 = 8
    P9 = 9
    SCREEN = 10  # the displayed screen at scene start
    WORK = 11  # the screen being drawn


class Cell(IntEnum):
    """Scene cells the ending stores to (§12.39.4)."""

    CREDITS = 0  # word: non-zero draws the overlay cel every frame
    OVERLAY_PHASE = 1  # word: 2 skips the overlay's last two cels
    STAGE_MODE = 2  # word: first-frame palette mode (2 fade, 3 fade out, 4 set, 5 black)
    STAGED = 3  # word: non-zero once the first-frame palette ran
    STOP = 4  # word: non-zero ends the frame loop
    SPEED = 5  # long: ticks per scene frame
    GAP = 6  # long: extra ticks added to every scene frame
    LEFT_DEPTH = 7  # word: added to the depth of left spawns
    RIGHT_DEPTH = 8  # word: added to the depth of right spawns


class Call(IntEnum):
    """Argument-free scene primitives (§12.39.4)."""

    JOBS_RESET = 0
    BLACKOUT = 1  # all 32 colours black at once
    FADE_OUT = 2  # fade to black, then the fixed wait
    FLIP = 3
    PREPARE = 4  # shown picture to both screens
    MUSIC = 5
    FRAMES = 6  # frame loop until STOP
    STAGE = 7  # set the stage palette at once
    LOADING_A = 8  # reverse wipe, set-up half
    LOADING_B = 9  # reverse wipe, running half


class SpawnKind(IntEnum):
    """The two spawn helpers: left (x 160, flags 1) and right (x 120, flags 3) (§12.39.5)."""

    LEFT = 0
    RIGHT = 1


@dataclass(slots=True, frozen=True)
class Invoke:
    call: Call


@dataclass(slots=True, frozen=True)
class Store:
    cell: Cell
    value: int


@dataclass(slots=True, frozen=True)
class StageTable:
    """The stage palette becomes picture palette ``palette`` (0..7)."""

    palette: int


@dataclass(slots=True, frozen=True)
class Show:
    """Copy ``source`` into the shown picture buffer."""

    source: Buffer


@dataclass(slots=True, frozen=True)
class Select:
    """The shown buffer pointer is replaced by ``source`` (no pixels move)."""

    source: Buffer


@dataclass(slots=True, frozen=True)
class Spawn:
    kind: SpawnKind
    script: int  # byte offset in the script hunk


@dataclass(slots=True, frozen=True)
class Pairs:
    """Spawn ``scripts`` in turn, alternating sides, sixteen frames each (§12.39.5)."""

    scripts: tuple[int, ...]


@dataclass(slots=True, frozen=True)
class Series:
    """Spawn ``scripts`` in turn on the left, counted frames from ``first_frame`` (§12.42.5)."""

    scripts: tuple[int, ...]
    first_frame: int


@dataclass(slots=True, frozen=True)
class Ticks:
    """``passes`` bare job passes: no stop test, overlay or stage palette."""

    passes: int


@dataclass(slots=True, frozen=True)
class RampRelease:
    slot: int  # 0..2, the ramps the script callback created


@dataclass(slots=True, frozen=True)
class Wait:
    ticks: int


@dataclass(slots=True, frozen=True)
class Clear:
    buffer: Buffer


@dataclass(slots=True, frozen=True)
class Target:
    buffer: Buffer


@dataclass(slots=True, frozen=True)
class PaletteTo:
    """Fade to picture palette ``palette`` at the shared period, then wait 16 ticks per step."""

    palette: int


@dataclass(slots=True, frozen=True)
class FadeBlack:
    """Fade to black with ``period`` ticks per step; no wait of its own."""

    period: int


@dataclass(slots=True, frozen=True)
class Text:
    lines: tuple[TextLine, ...]


@dataclass(slots=True, frozen=True)
class Caption:
    """The message-picture caption screen with its recoloured entries."""

    lines: tuple[TextLine, ...]


@dataclass(slots=True, frozen=True)
class Load:
    """The ending loader (``EndingLoad``) runs here."""


Step = (
    Invoke
    | Store
    | StageTable
    | Show
    | Select
    | Spawn
    | Pairs
    | Series
    | Ticks
    | RampRelease
    | Wait
    | Clear
    | Target
    | PaletteTo
    | FadeBlack
    | Text
    | Caption
    | Load
)


@dataclass(slots=True, frozen=True)
class PictureLoad:
    name: str
    buffer: Buffer
    palette: int
    ending_tweak: bool
    class_tweak: bool


@dataclass(slots=True, frozen=True)
class EndingLoad:
    """Files of the ending loader in their original order (§12.39.3)."""

    pictures: tuple[PictureLoad, ...]
    cleared: Buffer  # cleared after the tweaked pictures, before the plain ones
    cels: tuple[tuple[int, str], ...]  # (frame-table slot, file) in load order
    overlay: str
    stile: str
    stile_bytes: int
    music: str
    music_bytes: int


@dataclass(slots=True, frozen=True)
class EndingTweak:
    """Moon tweak row: three stage-palette entries and the three ramp colours (§12.39.3)."""

    bit: int
    entries: tuple[tuple[int, int], ...]
    ramp_colours: tuple[int, int, int]


@dataclass(slots=True, frozen=True)
class ClassTweak:
    """Knight-class tweak row: four colours from ``first`` on (§12.39.3)."""

    bit: int
    colours: tuple[int, int, int, int]


@dataclass(slots=True, frozen=True)
class RampSpec:
    """One colour ramp of the script callback: entry, colour cell 0..2 and rate (§12.39.6)."""

    entry: int
    colour: int
    rate: int


@dataclass(slots=True, frozen=True)
class SpawnSide:
    """Start record of one spawn helper; ``depth`` is the cell added to ``z`` (§12.39.5)."""

    x: int
    y: int
    z: int
    flags: int
    depth: Cell


@dataclass(slots=True, frozen=True)
class OverlayDraw:
    frame: int
    x: int
    y: int


@dataclass(slots=True, frozen=True)
class Overlay:
    """Credit overlay cels; the tail after ``kept`` is skipped while the phase is ``skip_phase``."""

    draws: tuple[OverlayDraw, ...]
    kept: int
    skip_phase: int


@dataclass(slots=True, frozen=True)
class RampSetup:
    """Script callback: create the three colour ramps of ``EndingPlan.ramps`` (§12.39.6)."""


@dataclass(slots=True, frozen=True)
class Flash:
    """Script callback: show ``colours`` now, wait, then show the live palette again."""

    colours: tuple[int, ...]
    ticks: int


@dataclass(slots=True, frozen=True)
class FlashSeries:
    """Script callback: before each flash wait ``waits[i]`` ticks, then wait ``tail``."""

    waits: tuple[int, ...]
    flash: Flash
    tail: int


@dataclass(slots=True, frozen=True)
class WipeSlow:
    """Script callback: the reverse wipe's step word drops by ``amount``."""

    amount: int


@dataclass(slots=True, frozen=True)
class WipeStop:
    """Script callback: the running half of the reverse wipe ends."""


Callback = RampSetup | Flash | FlashSeries | WipeSlow | WipeStop


@dataclass(slots=True, frozen=True)
class ScriptHunk:
    """The script hunk's bytes and the hunk each relocated long points into."""

    data: bytes
    relocs: tuple[tuple[int, int], ...]


@dataclass(slots=True, frozen=True)
class TextFont:
    """Text font file and routine operands: charmap offset in the script hunk, bias, width."""

    file: str
    charmap: int
    bias: int
    width: int


@dataclass(slots=True, frozen=True)
class EndingPlan:
    """The whole ending pass of ``program`` (§12.39)."""

    flag_bit: int
    steps: tuple[Step, ...]
    load: EndingLoad
    ending_tweaks: tuple[EndingTweak, ...]
    class_tweaks: tuple[ClassTweak, ...]
    class_first: int
    ramp_defaults: tuple[int, int, int]
    ramps: tuple[RampSpec, ...]
    spawns: tuple[SpawnSide, SpawnSide]
    pair_frames: int
    caption_entries: tuple[tuple[int, int], ...]
    fade_period: int
    fade_wait: int
    palette_period: int
    next_program: str
    overlay: Overlay
    callbacks: tuple[tuple[tuple[int, int], Callback], ...]
    scripts: ScriptHunk
    font: TextFont
    job_slots: int
    wipe: WipeSpec
    stage_period: int
    message: str
    palette_cell: tuple[int, ...]


@dataclass(slots=True, frozen=True)
class WipeSpec:
    """Reverse-wipe operands: start progress, step, pacing cells and the floor (§12.39.8)."""

    start: int
    step: int
    speed: int
    gap: int
    floor: int
