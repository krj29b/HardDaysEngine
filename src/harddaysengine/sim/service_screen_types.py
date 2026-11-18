"""Decoded records of the healer and mystic screens (ARCHITECTURE.md §12.31)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harddaysengine.sim.turn_rules import TextLine


class ServiceKind(IntEnum):
    """The two donation screens; the value is the portrait frame each passes."""

    MYSTIC = 0
    HEALER = 1


LEAVE = 2
ACCEPT = 3
LESS = 4
MORE = 5


@dataclass(slots=True, frozen=True)
class ScreenRegion:
    """One registered button: right and bottom edges excluded (§12.31)."""

    x: int
    y: int
    width: int
    height: int
    code: int

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height


@dataclass(slots=True, frozen=True)
class CelDraw:
    """A cel frame at (x, y); ``frame`` is ``None`` for the screen's portrait."""

    frame: int | None
    x: int
    y: int


@dataclass(slots=True, frozen=True)
class LabelDraw:
    """A single string at (x, y) in ``style``."""

    text: str
    x: int
    y: int
    style: int


@dataclass(slots=True, frozen=True)
class NumberDraw:
    """The purse (or the donation) formatted to three characters at (x, y) in ``style``."""

    purse: bool
    x: int
    y: int
    style: int


type DonationDraw = CelDraw | LabelDraw | NumberDraw


@dataclass(slots=True, frozen=True)
class DonationLayout:
    """The shared donation screen: cel file, regions, redraw list and repeat wait."""

    cel_file: str
    regions: tuple[ScreenRegion, ...]
    draws: tuple[DonationDraw, ...]
    repeat_wait: int


@dataclass(slots=True, frozen=True)
class ServiceOpening:
    """The front of one screen: picture, pointer start, greeting, synth starts, portrait."""

    picture: str
    pointer_x: int
    pointer_y: int
    greeting: tuple[TextLine, ...]
    sounds: tuple[tuple[int, int], ...]
    portrait: int


@dataclass(slots=True, frozen=True)
class ServiceScreens:
    """Both screens, the shared donation layout and every reply list (§12.31)."""

    donation: DonationLayout
    mystic: ServiceOpening
    healer: ServiceOpening
    refused: tuple[TextLine, ...]
    nothing_given: tuple[TextLine, ...]
    close_wait: int
    healer_small: tuple[TextLine, ...]
    healer_plain: tuple[TextLine, ...]
    healer_healed: tuple[TextLine, ...]
    mystic_nothing: tuple[TextLine, ...]
    mystic_unchanged: tuple[TextLine, ...]
    mystic_raise: tuple[tuple[int, tuple[TextLine, ...]], ...]
    mystic_lower: tuple[tuple[int, tuple[TextLine, ...]], ...]

    def opening(self, kind: ServiceKind) -> ServiceOpening:
        return self.mystic if kind == ServiceKind.MYSTIC else self.healer
