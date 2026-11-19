"""Decoded ``program`` intro plan and ``mog`` menu records (ARCHITECTURE.md §12.42)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from harddaysengine.sim.ending_types import Step

if TYPE_CHECKING:
    from harddaysengine.sim.ending_types import (
        Buffer,
        Callback,
        ClassTweak,
        Overlay,
        PictureLoad,
        ScriptHunk,
        SpawnSide,
        TextFont,
    )
    from harddaysengine.sim.turn_rules import TextLine


@dataclass(slots=True, frozen=True)
class PictureCopy:
    """One picture and palette table copy of the mid-intro setup (§12.42.5)."""

    src_buffer: Buffer
    dst_buffer: Buffer
    src_palette: int
    dst_palette: int


@dataclass(slots=True, frozen=True)
class PictureSetup:
    """Copy the three story pictures and palette tables into P2..P4 / tables 0..2 (§12.42.5)."""

    copies: tuple[PictureCopy, ...]


@dataclass(slots=True, frozen=True)
class ForwardWipe:
    """Scroll down the ``intro.stile`` panorama from 0 to ``limit`` and start music (§12.42.4)."""

    entries: tuple[tuple[int, int], ...]
    thresholds: tuple[int, ...]
    steps: tuple[int, ...]
    music_step: int
    limit: int


@dataclass(slots=True, frozen=True)
class ProgressSpec:
    """Operands of the four loader progress routines in hunk 31 (§12.42.3)."""

    gold_entries: tuple[tuple[int, int], ...]
    white_word: int
    black_word: int
    tail_period: int
    flash_period: int
    logo_frame: int
    logo_x: int
    logo_y: int
    logo_wait: int
    flash_wait: int
    credits: tuple[tuple[TextLine, ...], ...]


@dataclass(slots=True, frozen=True)
class IntroLoad:
    """Files and progress steps of the ``program`` intro loader in hunk 8 (§12.42.2)."""

    mindscape: str
    mindscape_scratch: Buffer
    pictures: tuple[PictureLoad, ...]
    cels: tuple[tuple[int, str], ...]
    cels_with_progress: int
    overlay: str
    stile: str
    stile_bytes: int
    music: str
    music_bytes: int
    progress: ProgressSpec


IntroStep = Step | PictureSetup | ForwardWipe


@dataclass(slots=True, frozen=True)
class IntroPlan:
    """The whole intro pass of ``program`` (§12.42)."""

    flag_bit: int
    steps: tuple[IntroStep, ...]
    load: IntroLoad
    class_tweaks: tuple[ClassTweak, ...]
    class_first: int
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
    stage_period: int
    message: str
    palette_cell: tuple[int, ...]


@dataclass(slots=True, frozen=True)
class SpriteDraw:
    """Single cel frame draw at ``(x, y)`` (§12.42.6)."""

    frame: int
    x: int
    y: int


@dataclass(slots=True, frozen=True)
class TitleMenuLayout:
    """Decoded ``mog`` title menu layout on ``ch.piv`` (§12.42.6)."""

    banner: SpriteDraw
    cursor_frame: int
    cursor_x: int
    cursor_y: tuple[int, int, int, int]
    lines: tuple[TextLine, ...]
    players_line_index: int
    gore_line_index: int
    gore_on_text: str
    gore_off_text: str
    span: int


@dataclass(slots=True, frozen=True)
class KnightSelectLayout:
    """Decoded ``mog`` Select a Knight screen layout (§12.42.6)."""

    lines: tuple[TextLine, ...]
    portrait_x: tuple[int, int, int, int]
    portrait_y: int
    portrait_first_frame: int
    highlight_frame: int
    name_x: int
    name_y: int
    name_style: int
    knight_names: tuple[str, str, str, str]
    palette: tuple[int, ...]
    span: int


@dataclass(slots=True, frozen=True)
class MogMenuData:
    """Combined ``mog`` title-menu and knight-select screen layouts (§12.42.6)."""

    title: TitleMenuLayout
    knight_select: KnightSelectLayout
    char_map: bytes
