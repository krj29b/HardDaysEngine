"""Fight palette tables read from ``mog`` and their assembly (ARCHITECTURE.md §10.13, §10.15).

The colours are not stored here: class and scene colours are immediate operands of
``MOVE.W #imm,(A0)+`` runs in ``mog`` hunk 0 and the region tables are data in hunk 4. This
module holds only the *positions* of those operands for the known ``mog`` version and checks the
instruction word in front of every operand it reads.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pymoonstone.assets.hunk import DecodedHunkExecutable, HunkSection

PALETTE_SIZE = 32
MOG_CODE_SECTION = 0
MOG_DATA_SECTION = 4
# CRC-32 of the code hunk (hunk 0) of the supported `mog` (172,260-byte file).
MOG_CODE_CRC32 = 0xFA43E5B2

SCENE_FLYER = 0x00
SCENE_MUDMEN = 0x04
SCENE_GUARDIAN = 0x08
SCENE_DUEL = 0x0C
SCENE_DRAGON = 0x14
SCENE_TROGG_AXE = 0x18
SCENE_TROGG_SPEAR = 0x20
SCENE_RATMEN = 0x24
SCENE_BALOK = 0x30
SCENE_TROLL = 0x40

REGION_GLEN = 0x0
REGION_FOREST = 0x4
REGION_SWAMP = 0x8
REGION_WASTELAND = 0xC

_OP_MOVE_IMM_POSTINC = 0x30FC  # MOVE.W #imm,(A0)+
_OP_MOVE_IMM_INDIRECT = 0x30BC  # MOVE.W #imm,(A0)
_OP_MOVE_IMM_DISP = 0x317C  # MOVE.W #imm,d16(A0)
_OP_LEA_ABS_A1 = 0x43F9  # LEA abs.l,A1

_FIRST_CLASS_INDEX = 6
_SCENE_INDEX = 9
_BLOOD_INDEX = 15
_REGION_INDEX = 16
_DRAGON_TAIL_INDEX = 29
_LOW_TWEAK_INDEX = 1
_CLASS_OTHER = 4

# Hunk-0 positions of the first instruction of each immediate run: (position, colour count).
_CLASS_RUNS: tuple[tuple[int, int], ...] = (
    (36342, 3),  # kind 0
    (36366, 3),  # kind 1
    (36414, 3),  # kind 2
    (36390, 3),  # kind 3
    (36436, 3),  # every other kind
)
_SCENE_RUNS: tuple[tuple[int, int, int], ...] = (
    (SCENE_FLYER, 35884, 7),
    (SCENE_RATMEN, 35934, 7),
    (SCENE_BALOK, 36088, 7),
    (SCENE_TROLL, 36120, 6),
    (SCENE_MUDMEN, 36164, 7),
    (SCENE_DRAGON, 36194, 7),
)
# Trogg scenes pick a row by region: wasteland, glen, every other region.
_TROGG_RUNS: tuple[tuple[int, int], ...] = ((35980, 7), (36024, 7), (36056, 7))
_DRAGON_TAIL_RUN = (36234, 3)
_LOW_TWEAK_RUN = (36550, 4)
_BLOOD_RED_POS = 36318
# Hunk-0 positions of the `LEA table,A1` instructions that name the hunk-4 colour tables.
_REGION_LEAS: tuple[tuple[int, int], ...] = (
    (REGION_GLEN, 36522),
    (REGION_FOREST, 36530),
    (REGION_SWAMP, 36566),
    (REGION_WASTELAND, 36602),
)
_REGION_TABLE_WORDS = 13
_GUARDIAN_LEA = 36148
_GUARDIAN_TABLE_WORDS = 23


class MogVersionError(ValueError):
    """Raised when ``mog`` is not the supported version or an expected instruction is missing."""


@dataclass(slots=True, frozen=True)
class FightPaletteTables:
    """12-bit ``$0RGB`` colour rows used to assemble a fight palette (§10.13)."""

    class_rows: tuple[tuple[int, ...], ...]
    scene_rows: Mapping[int, tuple[int, ...]]
    trogg_rows: tuple[tuple[int, ...], ...]
    dragon_tail: tuple[int, ...]
    low_tweak: tuple[int, ...]
    region_rows: Mapping[int, tuple[int, ...]]
    guardian_row: tuple[int, ...]
    blood_red: int


def _word(data: bytes, pos: int) -> int:
    value: int = struct.unpack_from(">H", data, pos)[0]
    return value


def _read_immediate_run(code: bytes, pos: int, count: int, what: str) -> tuple[int, ...]:
    """Read ``count`` operands of consecutive ``MOVE.W #imm,(A0)[+]`` instructions."""
    words: list[int] = []
    for index in range(count):
        at = pos + index * 4
        opcode = _word(code, at)
        if opcode not in (_OP_MOVE_IMM_POSTINC, _OP_MOVE_IMM_INDIRECT):
            msg = f"mog: {what}: expected MOVE.W #imm at code offset {at}, found ${opcode:04X}"
            raise MogVersionError(msg)
        words.append(_word(code, at + 2))
    return tuple(words)


def _read_table_via_lea(
    code_section: HunkSection, data: bytes, pos: int, count: int, what: str
) -> tuple[int, ...]:
    """Follow a relocated ``LEA table,A1`` at ``pos`` into hunk 4 and read ``count`` words."""
    opcode = _word(code_section.data, pos)
    relocated = pos + 2 in code_section.relocs.get(MOG_DATA_SECTION, ())
    if opcode != _OP_LEA_ABS_A1 or not relocated:
        msg = f"mog: {what}: expected a relocated LEA into hunk 4 at code offset {pos}"
        raise MogVersionError(msg)
    offset: int = struct.unpack_from(">I", code_section.data, pos + 2)[0]
    if offset + count * 2 > len(data):
        msg = f"mog: {what}: table at hunk-4 offset {offset} runs past the hunk"
        raise MogVersionError(msg)
    return tuple(struct.unpack_from(f">{count}H", data, offset))


def _read_blood_red(code: bytes) -> int:
    opcode = _word(code, _BLOOD_RED_POS)
    displacement = _word(code, _BLOOD_RED_POS + 4)
    if opcode != _OP_MOVE_IMM_DISP or displacement != _BLOOD_INDEX * 2:
        msg = f"mog: blood colour: expected MOVE.W #imm,30(A0) at code offset {_BLOOD_RED_POS}"
        raise MogVersionError(msg)
    return _word(code, _BLOOD_RED_POS + 2)


def parse_fight_palette_tables(mog_exec: DecodedHunkExecutable) -> FightPaletteTables:
    """Read the fight colour rows from the user's ``mog`` (§10.15)."""
    if len(mog_exec.sections) <= MOG_DATA_SECTION:
        raise MogVersionError("mog: fewer hunks than the fight palette tables need")
    code_section = mog_exec.sections[MOG_CODE_SECTION]
    code = code_section.data
    data = mog_exec.sections[MOG_DATA_SECTION].data
    crc = zlib.crc32(code)
    if crc != MOG_CODE_CRC32:
        msg = (
            f"mog: unsupported version (code hunk CRC-32 ${crc:08X}); "
            f"supported: ${MOG_CODE_CRC32:08X}"
        )
        raise MogVersionError(msg)
    return FightPaletteTables(
        class_rows=tuple(
            _read_immediate_run(code, pos, count, f"class colours {kind}")
            for kind, (pos, count) in enumerate(_CLASS_RUNS)
        ),
        scene_rows={
            scene: _read_immediate_run(code, pos, count, f"scene ${scene:02X} colours")
            for scene, pos, count in _SCENE_RUNS
        },
        trogg_rows=tuple(
            _read_immediate_run(code, pos, count, "Trogg scene colours")
            for pos, count in _TROGG_RUNS
        ),
        dragon_tail=_read_immediate_run(code, *_DRAGON_TAIL_RUN, "dragon scene tail"),
        low_tweak=_read_immediate_run(code, *_LOW_TWEAK_RUN, "region low colours"),
        region_rows={
            region: _read_table_via_lea(
                code_section, data, pos, _REGION_TABLE_WORDS, f"region ${region:X} colours"
            )
            for region, pos in _REGION_LEAS
        },
        guardian_row=_read_table_via_lea(
            code_section, data, _GUARDIAN_LEA, _GUARDIAN_TABLE_WORDS, "guardian scene colours"
        ),
        blood_red=_read_blood_red(code),
    )


def _class_row(tables: FightPaletteTables, kind: int) -> tuple[int, ...]:
    return tables.class_rows[kind if 0 <= kind < _CLASS_OTHER else _CLASS_OTHER]


def _trogg_row(tables: FightPaletteTables, region: int) -> tuple[int, ...]:
    if region == REGION_WASTELAND:
        return tables.trogg_rows[0]
    if region == REGION_GLEN:
        return tables.trogg_rows[1]
    return tables.trogg_rows[2]


def _scene_row(
    tables: FightPaletteTables, scene: int, region: int, second_kind: int
) -> tuple[int, ...]:
    if scene == SCENE_DUEL:
        return _class_row(tables, second_kind)
    if scene in (SCENE_TROGG_AXE, SCENE_TROGG_SPEAR):
        return _trogg_row(tables, region)
    if scene == SCENE_GUARDIAN:
        return tables.guardian_row
    return tables.scene_rows.get(scene, ())


def _put(palette: list[int], index: int, row: Sequence[int]) -> None:
    palette[index : index + len(row)] = row


def assemble_fight_palette(
    picture_words: Sequence[int],
    tables: FightPaletteTables,
    *,
    scene: int,
    region: int,
    first_kind: int,
    second_kind: int = 0,
) -> tuple[int, ...]:
    """Build the 32-word fight palette from the arena picture's palette (§10.13).

    ``picture_words`` are the 12-bit words of the arena's object sheet. ``scene`` is the arena's
    scene code, ``region`` the region code (0, 4, 8, ``$C``), ``first_kind`` / ``second_kind``
    the ``kind`` of the first and second fighter (§9.2).
    """
    palette = [word & 0x0FFF for word in picture_words[:PALETTE_SIZE]]
    palette.extend([0] * (PALETTE_SIZE - len(palette)))

    _put(palette, _SCENE_INDEX, _scene_row(tables, scene, region, second_kind))
    if scene == SCENE_DRAGON:
        _put(palette, _DRAGON_TAIL_INDEX, tables.dragon_tail)

    _put(palette, _FIRST_CLASS_INDEX, _class_row(tables, first_kind))
    if scene != SCENE_GUARDIAN:
        if region in (REGION_SWAMP, REGION_WASTELAND):
            _put(palette, _LOW_TWEAK_INDEX, tables.low_tweak)
        _put(palette, _REGION_INDEX, tables.region_rows.get(region, ()))

    palette[0] = 0
    if scene != SCENE_DRAGON:
        palette[_BLOOD_INDEX] = tables.blood_red
    return tuple(palette)
