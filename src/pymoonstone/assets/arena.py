"""Arena layout (`.t`) and screen-wipe tile map (`.stile`) decoders (ARCHITECTURE.md §3.3, §4.3)."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import numpy.typing as npt

from pymoonstone.assets.lzss import decompress_lzss
from pymoonstone.assets.picture import (
    PICTURE_HEIGHT,
    PICTURE_WIDTH,
    DecodedPicture,
    indexed_to_planar,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

ARENA_RECORD_BYTES = 8
ARENA_SCRIPT_BYTES = 2400
STILE_WORD_COUNT = 480
STILE_BYTE_SIZE = STILE_WORD_COUNT * 2  # 960 bytes

REGION_PREFIXES: dict[str, int] = {
    "fo": 0,
    "gl": 1,
    "sw": 2,
    "wa": 3,
}

# B/test pack picture indices per region (0=fo, 1=gl, 2=sw, 3=wa) (ARCHITECTURE.md §3.1, §4.3).
_REGION_BACKDROP_INDEX: tuple[int, int, int, int] = (4, 7, 6, 5)
_REGION_FG_SHEET_INDEX: tuple[int, int, int, int] = (0, 0, 2, 3)
_MAIN_SHEET_INDEX = 1


class ArenaFormatError(ValueError):
    """Raised when an arena `.t` or `.stile` file is malformed."""


@dataclass(slots=True, frozen=True)
class ObstacleSegment:
    """8-byte obstacle wall segment (`x1`, `x2`, `y`, `unused`) (ARCHITECTURE.md §4.3)."""

    x1: int
    x2: int
    y: int
    unused: int


@dataclass(slots=True, frozen=True)
class ArenaTileOp:
    """6-byte tile-compositor command (`kind`, `tile_index`, `x`, `y`) (ARCHITECTURE.md §4.3)."""

    kind: int
    tile_index: int
    x: int
    y: int


@dataclass(slots=True, frozen=True)
class DecodedArena:
    """Decoded `.t` arena layout with obstacle segments and tile-compositor script."""

    name: str
    region: int
    is_lair: bool
    obstacles: tuple[ObstacleSegment, ...]
    tiles: tuple[ArenaTileOp, ...]


def infer_arena_region(name: str) -> tuple[int, bool]:
    """Infer region index (0..3 for fo/gl/sw/wa) and whether the arena is a Lair (`*l1..6.t`)."""
    stem = name.strip().lower().removesuffix(".t")
    prefix = stem[:2]
    region = REGION_PREFIXES.get(prefix, 0)
    rest = stem[2:]
    is_lair = rest.startswith("l")
    return region, is_lair


def _parse_tile_script(script_view: memoryview) -> list[ArenaTileOp]:
    tiles: list[ArenaTileOp] = []
    max_entries = len(script_view) // 6
    for idx in range(max_entries):
        kind_tile, dx, dy = struct.unpack_from(">Hhh", script_view, idx * 6)
        kind = kind_tile & 0xFF00
        tile_idx = kind_tile & 0x00FF
        if kind == 0xFF00:
            break
        if kind == 0xFE00:
            continue
        tiles.append(ArenaTileOp(kind=kind, tile_index=tile_idx, x=dx, y=dy))
    return tiles


def decode_arena(name: str, data: bytes | bytearray | memoryview) -> DecodedArena:
    """Decode an LZSS-compressed `.t` arena file (ARCHITECTURE.md §4.3)."""
    view = memoryview(data)
    if len(view) < 4:
        msg = f"Arena {name!r} too short ({len(view)} bytes < 4)"
        raise ArenaFormatError(msg)
    (packed_size,) = struct.unpack_from(">I", view, 0)
    stream_end = min(len(view), 4 + packed_size)
    unpacked = decompress_lzss(view[4:stream_end])
    uview = memoryview(unpacked)
    if len(uview) < 2:
        msg = f"Unpacked arena {name!r} too short ({len(uview)} bytes)"
        raise ArenaFormatError(msg)

    (seg_count,) = struct.unpack_from(">H", uview, 0)
    table_end = 2 + seg_count * ARENA_RECORD_BYTES
    if table_end > len(uview):
        msg = f"Arena {name!r} obstacle table truncated: need {table_end}, got {len(uview)}"
        raise ArenaFormatError(msg)

    obstacles: list[ObstacleSegment] = []
    for i in range(seg_count):
        x1, x2, y, unused = struct.unpack_from(">HHHH", uview, 2 + i * ARENA_RECORD_BYTES)
        obstacles.append(ObstacleSegment(x1=x1, x2=x2, y=y, unused=unused))

    script_slice = uview[table_end : min(len(uview), table_end + ARENA_SCRIPT_BYTES)]
    tiles = _parse_tile_script(script_slice)
    region, is_lair = infer_arena_region(name)
    return DecodedArena(
        name=name,
        region=region,
        is_lair=is_lair,
        obstacles=tuple(obstacles),
        tiles=tuple(tiles),
    )


def _blit_tile_cookie_cut(
    canvas: npt.NDArray[np.uint8],
    sheet: npt.NDArray[np.uint8],
    tile_index: int,
    dest_x: int,
    dest_y: int,
) -> None:
    if not (0 <= tile_index < 80):
        return
    src_x = (tile_index % 10) * 32
    src_y = (tile_index // 10) * 25
    tile = sheet[src_y : src_y + 25, src_x : src_x + 32]

    if dest_x >= PICTURE_WIDTH or dest_y >= PICTURE_HEIGHT or dest_x + 32 <= 0 or dest_y + 25 <= 0:
        return
    x0 = max(0, dest_x)
    y0 = max(0, dest_y)
    x1 = min(PICTURE_WIDTH, dest_x + 32)
    y1 = min(PICTURE_HEIGHT, dest_y + 25)
    sub_tile = tile[y0 - dest_y : y1 - dest_y, x0 - dest_x : x1 - dest_x]
    target = canvas[y0:y1, x0:x1]
    np.copyto(target, sub_tile, where=(sub_tile != 0))


def composite_arena_background(
    arena: DecodedArena,
    test_pictures: Sequence[DecodedPicture],
) -> DecodedPicture:
    """Composite a 320x200 arena background from `B/test` tile sheets (ARCHITECTURE.md §4.3).

    The returned palette is the region's object sheet palette, the base of the fight palette
    (ARCHITECTURE.md §10.13).
    """
    if len(test_pictures) < 8:
        msg = f"Expected at least 8 pictures from B/test, got {len(test_pictures)}"
        raise ArenaFormatError(msg)

    region = arena.region & 3
    base_pic = test_pictures[_REGION_BACKDROP_INDEX[region]]
    main_sheet = test_pictures[_MAIN_SHEET_INDEX]
    fg_sheet = test_pictures[_REGION_FG_SHEET_INDEX[region]]

    canvas = base_pic.pixels.copy()
    for op in arena.tiles:
        sheet = fg_sheet if op.kind == 0x0300 else main_sheet
        _blit_tile_cookie_cut(canvas, sheet.pixels, op.tile_index, op.x, op.y)

    raw_planes = indexed_to_planar(canvas, planes=5)
    return DecodedPicture(
        width=PICTURE_WIDTH,
        height=PICTURE_HEIGHT,
        planes=5,
        packed_size=0,
        raw_palette=fg_sheet.raw_palette,
        palette=fg_sheet.palette,
        pixels=canvas,
        raw_planes=raw_planes,
    )


def decode_stile(data: bytes | bytearray | memoryview) -> tuple[int, ...]:
    """Decode a 960-byte `.stile` wipe tile map into 480 BE16 words (ARCHITECTURE.md §3.3)."""
    view = memoryview(data)
    if len(view) != STILE_BYTE_SIZE:
        msg = f"Expected {STILE_BYTE_SIZE}-byte .stile file, got {len(view)} bytes"
        raise ArenaFormatError(msg)
    words: tuple[int, ...] = struct.unpack_from(f">{STILE_WORD_COUNT}H", view, 0)
    return words
