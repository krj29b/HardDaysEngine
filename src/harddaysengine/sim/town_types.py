"""Immutable runtime town screen records (ARCHITECTURE.md §12.27)."""

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class TownButton:
    """One registered snapshot of the original town scratch record (§12.27)."""

    id: int
    x: int
    y: int
    width: int
    height: int
    type: int
    field: int


@dataclass(slots=True, frozen=True)
class TownData:
    """Original identity, pointer start, key selection and five buttons (§12.27)."""

    name: str
    picture: str
    pointer: str
    pointer_x: int
    pointer_y: int
    space_sheet: int
    buttons: tuple[TownButton, ...]
