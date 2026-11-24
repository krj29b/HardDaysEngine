"""Big-endian 132-byte VM field adapter for ``Actor`` (ARCHITECTURE.md §9.2, §10.17).

Used exclusively by the fight script VM opcodes (``$A8``, ``$C8``, ``$CC``) and disk-loaded
initial record defaults. Domain logic accesses :class:`~harddaysengine.sim.actor.Actor`
through typed attributes and helpers instead of raw byte offsets.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harddaysengine.sim.m68k import u32

if TYPE_CHECKING:
    from harddaysengine.sim.actor import Actor

ACTOR_SIZE = 132
_BLOCK_LATCH_OFFSET = 104
_BLOCK_LATCH_BIT = 0x80

# (offset, width, field name or None for unused padding) for every field of §9.2.
_LAYOUT: tuple[tuple[int, int, str | None], ...] = (
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
    (87, 1, None),
    (88, 4, "sword"),
    (92, 4, "armour"),
    (96, 4, "inventory"),
    (100, 4, "engaged_with"),
    (104, 1, None),
    (105, 1, None),
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
    (131, 1, None),
)

# For each byte of the record: (field name or None, field start, field width).
_BYTE_OWNER: tuple[tuple[str | None, int, int], ...] = tuple(
    (name, start, width) for start, width, name in _LAYOUT for _ in range(width)
)


class RecordAccessError(IndexError):
    """Raised when a script addresses bytes outside the 132-byte record."""


def _check_range(offset: int, size: int) -> range:
    if offset < 0 or offset + size > ACTOR_SIZE:
        raise RecordAccessError(f"record access at offset {offset}, size {size}")
    return range(offset, offset + size)


def _read_byte(actor: Actor, byte: int) -> int:
    if byte == _BLOCK_LATCH_OFFSET:
        return _BLOCK_LATCH_BIT if actor.block_latched else 0
    name, start, width = _BYTE_OWNER[byte]
    if name is None:
        return 0
    shift = 8 * (start + width - 1 - byte)
    value: int = getattr(actor, name)
    return (value >> shift) & 0xFF


def _write_byte(actor: Actor, byte: int, value: int) -> None:
    if byte == _BLOCK_LATCH_OFFSET:
        actor.block_latched = bool(value & _BLOCK_LATCH_BIT)
        return
    name, start, width = _BYTE_OWNER[byte]
    if name is None:
        return
    shift = 8 * (start + width - 1 - byte)
    old: int = getattr(actor, name)
    setattr(actor, name, u32((old & ~(0xFF << shift)) | (value << shift)))


class ScriptFieldAdapter:
    """Stateless big-endian byte-addressed view of ``Actor`` for VM opcodes and disk defaults."""

    @staticmethod
    def read(actor: Actor, offset: int, size: int) -> int:
        """Big-endian unsigned read of ``size`` bytes at ``offset`` (§10.17, ``$C8`` / ``$CC``)."""
        value = 0
        for byte in _check_range(offset, size):
            value = (value << 8) | _read_byte(actor, byte)
        return value

    @staticmethod
    def write(actor: Actor, offset: int, size: int, value: int) -> None:
        """Big-endian write of the low ``size`` bytes of ``value`` at ``offset`` (``$A8``)."""
        positions = _check_range(offset, size)
        for index, byte in enumerate(positions):
            shift = 8 * (size - 1 - index)
            _write_byte(actor, byte, (value >> shift) & 0xFF)
