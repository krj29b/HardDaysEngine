"""The numbers, scripts and rule tables of knight control, as read from ``mog`` (§10.19).

Nothing here holds a game value: :mod:`harddaysengine.assets.fighter_rules` fills these records from
the user's ``mog``. Script values are positions in the script hunk; ``hop_table`` is a position
in the script hunk too; ``DaggerRules.damage`` is the dagger's damage table as longs.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


class HurtRule(Enum):
    """The knight's reaction to a blow, chosen by the attacker's type (§10.19)."""

    CONTACT = auto()
    KNIGHT = auto()
    AXE = auto()
    SPEAR = auto()
    RATMEN = auto()
    FLYER = auto()
    GUARDIAN = auto()
    STALKER = auto()
    DRAKE = auto()
    DRAGON = auto()
    DRAGON_FLIGHT = auto()
    DRAGON_OBJECT = auto()


class HitRule(Enum):
    """The knight's reaction to its own blow landing, chosen by the target's type (§10.19)."""

    CARRY_ON = auto()
    HURT_BY_FLIGHT = auto()


@dataclass(slots=True, frozen=True)
class WalkSteps:
    """The three 4-entry step tables indexed by the walk phase (§10.9)."""

    side: tuple[int, ...]
    up: tuple[int, ...]
    down: tuple[int, ...]


@dataclass(slots=True, frozen=True)
class ArenaClip:
    """The arena clip numbers (§10.19): facing reach, side limits, foot offset, depth limits."""

    reach: int
    left: int
    right: int
    foot: int
    bottom: int
    top: int


@dataclass(slots=True, frozen=True)
class Probes:
    """Movement and obstacle probe numbers (§10.19)."""

    vertical_reach: int
    obstacle_reach: int
    wall_top: int


@dataclass(slots=True, frozen=True)
class DamageRules:
    """Sword bonuses ``(code, bonus)`` and moonstone pairs ``(bit, moon frame)`` (§10.19)."""

    sword_bonus: tuple[tuple[int, int], ...]
    moonstones: tuple[tuple[int, int], ...]


@dataclass(slots=True, frozen=True)
class HurtAmounts:
    """Flat hit-point losses of the hurt rules (§10.19)."""

    spear: int
    flyer: int
    guardian_heavy: int
    guardian_strike: int
    guardian_other: int
    stalker: int
    drake: int
    dragon_depth: int
    dragon_strike: int
    dragon_flight: int
    object_offset: int


@dataclass(slots=True, frozen=True)
class HurtScripts:
    """Fixed scripts the hurt rules play (§10.19)."""

    contact_down: int
    contact_down_hard: int
    axe_down: int
    axe_down_hard: int
    spear_parry: int
    spear_kill: int
    ratmen_curse: int
    ratmen_strike: int
    flyer_kill_same: int
    flyer_kill_facing: int
    flyer_hurt_same: int
    flyer_hurt_facing: int
    stalker_flung: int
    object_hop: int


@dataclass(slots=True, frozen=True)
class DaggerRules:
    """The dagger throw and the dagger handler (§10.19)."""

    script: int
    flight_script: int
    damage: tuple[int, ...]
    action: int
    right_edge: int
    left_margin: int


@dataclass(slots=True, frozen=True)
class KnightRules:
    """Everything §10.19 reads from ``mog``."""

    steps: WalkSteps
    actions_right: tuple[int, ...]
    actions_left: tuple[int, ...]
    clip: ArenaClip
    probes: Probes
    clang_sound: int
    damage: DamageRules
    amounts: HurtAmounts
    scripts: HurtScripts
    dagger: DaggerRules
    hop_table: int
    hurt_rules: Mapping[int, HurtRule]
    hit_rules: Mapping[int, HitRule]
