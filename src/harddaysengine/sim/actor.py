"""The actor record shared by knights, the dragon and fight objects (ARCHITECTURE.md §9.2).

Every field holds its raw unsigned value at its width; signed reads go through
:func:`harddaysengine.sim.m68k.s16`. References (``hit_target``, ``attacker``, ``engaged_with``) are
actor handles, script fields hold script positions and ``frame_list`` a frame-set number; 0 is
"none" everywhere. Script opcodes that address fields by byte offset go through
:class:`harddaysengine.sim.script_adapter.ScriptFieldAdapter`.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import IntEnum

from harddaysengine.sim.m68k import u8

_COOLDOWN_SHIFT = 8
_BYTE_MASK = 0xFF


class StatKind(IntEnum):
    """Primary knight attributes keyed by their decoded table codes (ARCHITECTURE.md §9.2)."""

    STRENGTH = 70
    CONSTITUTION = 71
    ENDURANCE = 72


ALL_STATS: tuple[StatKind, ...] = (
    StatKind.STRENGTH,
    StatKind.CONSTITUTION,
    StatKind.ENDURANCE,
)


@dataclass(slots=True)
class Actor:
    """One actor record (§9.2); field names follow the table there."""

    active: int = 0
    x: int = 0
    height: int = 0
    y: int = 0
    facing: int = 0
    input_port: int = 0
    anim_phase: int = 0
    anim_timer: int = 0
    hit_target: int = 0
    attacker: int = 0
    idle_script: int = 0
    after_hit_script: int = 0
    hurt_scripts: int = 0
    action_scripts: int = 0
    frame_list: int = 0
    damage_table: int = 0
    walk_scripts: int = 0
    defence_table: int = 0
    kind: int = 0
    box_min_x: int = 0
    box_max_x: int = 0
    input: int = 0
    action: int = 0
    tile_x: int = 0
    tile_y: int = 0
    strength: int = 0
    constitution: int = 0
    endurance: int = 0
    lives: int = 0
    gold: int = 0
    daggers: int = 0
    type: int = 0
    progress: int = 0
    hp: int = 0
    frog_days: int = 0
    recency: int = 0
    hp_max: int = 0
    derived_endurance: int = 0
    sword: int = 0
    armour: int = 0
    inventory: int = 0
    engaged_with: int = 0
    block_latched: bool = False
    cooldown: int = 0
    name: int = 0
    box_min_y: int = 0
    box_max_y: int = 0
    reach_x: int = 0
    too_close_x: int = 0
    depth_reach: int = 0
    hit_x: int = 0
    hit_y: int = 0
    map_x: int = 0
    map_y: int = 0
    life_loss: int = 0

    def get_stat(self, stat: StatKind) -> int:
        """Read one primary stat byte by :class:`StatKind`."""
        if stat == StatKind.STRENGTH:
            return self.strength
        if stat == StatKind.CONSTITUTION:
            return self.constitution
        if stat == StatKind.ENDURANCE:
            return self.endurance
        raise ValueError(f"unknown stat kind {stat}")

    def set_stat(self, stat: StatKind, value: int) -> None:
        """Write one primary stat byte by :class:`StatKind`."""
        wrapped = u8(value)
        if stat == StatKind.STRENGTH:
            self.strength = wrapped
        elif stat == StatKind.CONSTITUTION:
            self.constitution = wrapped
        elif stat == StatKind.ENDURANCE:
            self.endurance = wrapped
        else:
            raise ValueError(f"unknown stat kind {stat}")

    def add_stat(self, stat: StatKind, delta: int) -> int:
        """Add ``delta`` (wrapped to unsigned byte) to one primary stat and return the new value."""
        updated = u8(self.get_stat(stat) + delta)
        self.set_stat(stat, updated)
        return updated

    def cooldown_byte(self) -> int:
        """Read the high byte of the 16-bit ``cooldown`` counter."""
        return (self.cooldown >> _COOLDOWN_SHIFT) & _BYTE_MASK

    def set_cooldown_byte(self, value: int) -> None:
        """Write the high byte of ``cooldown`` while preserving its low byte."""
        self.cooldown = ((value & _BYTE_MASK) << _COOLDOWN_SHIFT) | (self.cooldown & _BYTE_MASK)

    def clear(self) -> None:
        """Reset every field to its default value (a freed pool record, §10.18)."""
        for item in fields(self):
            setattr(self, item.name, item.default)
