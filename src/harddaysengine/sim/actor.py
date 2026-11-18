"""The 132-byte actor record shared by knights, the dragon and fight objects (ARCHITECTURE.md §9.2).

Every field holds its raw unsigned value at its width; signed reads go through
:func:`harddaysengine.sim.m68k.s16`. References (``hit_target``, ``attacker``, ``engaged_with``) are
actor handles, script fields hold script positions and ``frame_list`` a frame-set number; 0 is
"none" everywhere, as in the original. Scripts address the record by byte offset (§10.17,
opcodes ``$A8``, ``$C8``, ``$CC``), so :meth:`Actor.read` and :meth:`Actor.write` map any byte
range onto the named fields, big-endian.
"""

from __future__ import annotations

from dataclasses import dataclass

from harddaysengine.sim.m68k import u32

ACTOR_SIZE = 132

# (offset, width, field name) for every field of §9.2.
_LAYOUT: tuple[tuple[int, int, str], ...] = (
    (0, 4, "active"),
    (4, 2, "x"),
    (6, 2, "height"),
    (8, 2, "y"),
    (10, 1, "facing"),
    (11, 1, "input_port"),
    (12, 1, "anim_phase"),
    (13, 1, "anim_timer"),
    (14, 4, "hit_target"),
    (18, 4, "attacker"),
    (22, 4, "idle_script"),
    (26, 4, "after_hit_script"),
    (30, 4, "hurt_scripts"),
    (34, 4, "action_scripts"),
    (38, 4, "frame_list"),
    (42, 4, "damage_table"),
    (46, 4, "walk_scripts"),
    (50, 4, "defence_table"),
    (54, 4, "kind"),
    (58, 2, "box_min_x"),
    (60, 2, "box_max_x"),
    (62, 2, "input"),
    (64, 2, "action"),
    (66, 2, "tile_x"),
    (68, 2, "tile_y"),
    (70, 1, "strength"),
    (71, 1, "constitution"),
    (72, 1, "endurance"),
    (73, 1, "lives"),
    (74, 2, "gold"),
    (76, 1, "daggers"),
    (77, 1, "type"),
    (78, 2, "progress"),
    (80, 2, "hp"),
    (82, 1, "frog_days"),
    (83, 1, "recency"),
    (84, 2, "hp_max"),
    (86, 1, "derived_endurance"),
    (87, 1, "spare87"),
    (88, 4, "sword"),
    (92, 4, "armour"),
    (96, 4, "inventory"),
    (100, 4, "engaged_with"),
    (104, 1, "behaviour_flags"),
    (105, 1, "behaviour_flags2"),
    (106, 2, "cooldown"),
    (108, 4, "name"),
    (112, 2, "box_min_y"),
    (114, 2, "box_max_y"),
    (116, 2, "reach_x"),
    (118, 2, "too_close_x"),
    (120, 2, "depth_reach"),
    (122, 2, "hit_x"),
    (124, 2, "hit_y"),
    (126, 2, "map_x"),
    (128, 2, "map_y"),
    (130, 1, "life_loss"),
    (131, 1, "spare131"),
)

# For each byte of the record: (field name, field start, field width).
_BYTE_OWNER: tuple[tuple[str, int, int], ...] = tuple(
    (name, start, width) for start, width, name in _LAYOUT for _ in range(width)
)


class RecordAccessError(IndexError):
    """Raised when a script addresses bytes outside the 132-byte record."""


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
    spare87: int = 0
    sword: int = 0
    armour: int = 0
    inventory: int = 0
    engaged_with: int = 0
    behaviour_flags: int = 0
    behaviour_flags2: int = 0
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
    spare131: int = 0

    def read(self, offset: int, size: int) -> int:
        """Big-endian unsigned read of ``size`` bytes at ``offset`` (§10.17, ``$C8`` / ``$CC``)."""
        value = 0
        for byte in _check_range(offset, size):
            value = (value << 8) | self._byte(byte)
        return value

    def write(self, offset: int, size: int, value: int) -> None:
        """Big-endian write of the low ``size`` bytes of ``value`` at ``offset`` (``$A8``)."""
        positions = _check_range(offset, size)
        for index, byte in enumerate(positions):
            shift = 8 * (size - 1 - index)
            self._set_byte(byte, (value >> shift) & 0xFF)

    def clear(self) -> None:
        """Zero every field (a freed pool record, §10.18)."""
        for _, _, name in _LAYOUT:
            setattr(self, name, 0)

    def _byte(self, byte: int) -> int:
        name, start, width = _BYTE_OWNER[byte]
        shift = 8 * (start + width - 1 - byte)
        value: int = getattr(self, name)
        return (value >> shift) & 0xFF

    def _set_byte(self, byte: int, value: int) -> None:
        name, start, width = _BYTE_OWNER[byte]
        shift = 8 * (start + width - 1 - byte)
        old: int = getattr(self, name)
        setattr(self, name, u32((old & ~(0xFF << shift)) | (value << shift)))


def _check_range(offset: int, size: int) -> range:
    if offset < 0 or offset + size > ACTOR_SIZE:
        raise RecordAccessError(f"record access at offset {offset}, size {size}")
    return range(offset, offset + size)
