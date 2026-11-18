"""Temporary location-screen record during the ADR-009 migration.

This record belongs only to the legacy shops and sheets awaiting T11.15 to T11.21. It has
no combat controller, animation state or damage rules. Fights use the Actor of §9.2.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from harddaysengine.sim.types import KnightId


class SecondaryWeapon(IntEnum):
    """Legacy shop selection, retained until the inventory/sheet migration."""

    THROWING_KNIFE = 0
    FIREBALL_SCROLL = 1


@dataclass(slots=True)
class KnightState:
    """Legacy location values; never stepped or used to resolve a fight."""

    knight_id: KnightId = KnightId.RED
    hp: int = 100
    max_hp: int = 100
    stamina: int = 100
    max_stamina: int = 100
    armour: int = 0
    shield: int = 0
    sword_tier: int = 1
    secondary_weapon: SecondaryWeapon = SecondaryWeapon.THROWING_KNIFE
    daggers: int = 5
    strength: int = 1
    constitution: int = 1
    endurance: int = 1
    lives: int = 3
    gold: int = 0
    xp: int = 0
    keys: int = 0
    moonstones: int = 0
