"""Full-screen Amiga planar picture decoder (.piv, .p, mindscape, B/test) (ARCHITECTURE.md §3.1)."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import numpy.typing as npt

from harddaysengine.assets.lzss import decompress_lzss
from harddaysengine.assets.palette import RGBColor, decode_palette, palette_to_rgba_array

if TYPE_CHECKING:
    from collections.abc import Sequence

PICTURE_WIDTH = 320
PICTURE_HEIGHT = 200
PICTURE_ROW_BYTES = 40
PICTURE_PLANE_BYTES = PICTURE_ROW_BYTES * PICTURE_HEIGHT  # 8,000 bytes


class PictureFormatError(ValueError):
    """Raised when a picture header or plane payload is malformed."""


@dataclass(slots=True, frozen=True)
class DecodedPicture:
    """Decoded 320x200 Amiga planar picture with palette and indexed pixel array."""

    width: int
    height: int
    planes: int
    packed_size: int
    raw_palette: tuple[int, ...]
    palette: tuple[RGBColor, ...]
    pixels: npt.NDArray[np.uint8]
    raw_planes: bytes


def planar_to_indexed(
    plane_bytes: bytes | bytearray | memoryview,
    width: int,
    height: int,
    planes: int = 5,
    plane_bits: int | None = None,
) -> npt.NDArray[np.uint8]:
    """Convert non-interleaved Amiga bitplanes into a (height, width) uint8 palette-index array."""
    if width <= 0 or height <= 0:
        return np.zeros((max(height, 0), max(width, 0)), dtype=np.uint8)
    row_bytes = ((width + 15) >> 4) * 2
    plane_size = row_bytes * height
    mask = ((1 << planes) - 1) if plane_bits is None else plane_bits

    view = memoryview(plane_bytes)
    out = np.zeros((height, width), dtype=np.uint8)
    cursor = 0
    for plane_idx in range(planes):
        if not ((mask >> plane_idx) & 1):
            continue
        if cursor + plane_size > len(view):
            msg = (
                f"Plane buffer too short for plane {plane_idx}: "
                f"need {cursor + plane_size}, got {len(view)}"
            )
            raise PictureFormatError(msg)
        raw_plane = np.frombuffer(view[cursor : cursor + plane_size], dtype=np.uint8).reshape(
            height, row_bytes
        )
        bits = np.unpackbits(raw_plane, axis=1)[:, :width]
        out |= bits << plane_idx
        cursor += plane_size
    return out


def indexed_to_planar(
    pixels: npt.NDArray[np.uint8],
    planes: int = 5,
    plane_bits: int | None = None,
) -> bytes:
    """Pack a (height, width) uint8 palette-index array into non-interleaved Amiga bitplanes."""
    height, width = pixels.shape
    row_bytes = ((width + 15) >> 4) * 2
    padded_width = row_bytes * 8
    mask = ((1 << planes) - 1) if plane_bits is None else plane_bits
    chunks: list[bytes] = []
    for plane_idx in range(planes):
        if not ((mask >> plane_idx) & 1):
            continue
        bit_grid = np.zeros((height, padded_width), dtype=np.uint8)
        bit_grid[:, :width] = (pixels >> plane_idx) & 1
        packed = np.packbits(bit_grid, axis=1)
        chunks.append(packed.tobytes())
    return b"".join(chunks)


def decode_picture_from_offset(
    data: bytes | bytearray | memoryview,
    offset: int = 0,
) -> tuple[DecodedPicture, int]:
    """Decode one picture starting at `offset` and return `(picture, next_offset)`."""
    view = memoryview(data)
    if offset + 6 > len(view):
        msg = f"Picture header truncated at offset {offset}"
        raise PictureFormatError(msg)
    planes, packed_size = struct.unpack_from(">HI", view, offset)
    if planes not in (4, 5):
        msg = f"Unsupported picture plane count {planes} at offset {offset} (expected 4 or 5)"
        raise PictureFormatError(msg)
    pal_count = 16 if planes == 4 else 32
    pal_bytes = pal_count * 2
    stream_start = offset + 6 + pal_bytes
    stream_end = stream_start + packed_size
    if stream_end > len(view):
        msg = f"Picture LZSS stream truncated: ends at {stream_end}, buffer size {len(view)}"
        raise PictureFormatError(msg)

    raw_palette: tuple[int, ...] = struct.unpack_from(f">{pal_count}H", view, offset + 6)
    palette = decode_palette(raw_palette)
    expected_bytes = planes * PICTURE_PLANE_BYTES
    raw_planes = decompress_lzss(view[stream_start:stream_end], max_output=expected_bytes)
    if len(raw_planes) < expected_bytes:
        msg = f"Decoded picture plane data short: got {len(raw_planes)}, expected {expected_bytes}"
        raise PictureFormatError(msg)

    pixels = planar_to_indexed(raw_planes, PICTURE_WIDTH, PICTURE_HEIGHT, planes=planes)
    pic = DecodedPicture(
        width=PICTURE_WIDTH,
        height=PICTURE_HEIGHT,
        planes=planes,
        packed_size=packed_size,
        raw_palette=raw_palette,
        palette=palette,
        pixels=pixels,
        raw_planes=raw_planes[:expected_bytes],
    )
    return pic, stream_end


def decode_picture(data: bytes | bytearray | memoryview) -> DecodedPicture:
    """Decode a single full-screen Amiga picture (.piv, .p, mindscape) (ARCHITECTURE.md §3.1)."""
    pic, _ = decode_picture_from_offset(data, 0)
    return pic


def decode_picture_pack(data: bytes | bytearray | memoryview) -> list[DecodedPicture]:
    """Decode a concatenated multi-picture pack such as `B/test` (ARCHITECTURE.md §3.1)."""
    view = memoryview(data)
    offset = 0
    pictures: list[DecodedPicture] = []
    while offset < len(view):
        pic, offset = decode_picture_from_offset(view, offset)
        pictures.append(pic)
    return pictures


def picture_to_rgba(
    pic: DecodedPicture,
    palette_override: Sequence[RGBColor] | None = None,
) -> npt.NDArray[np.uint8]:
    """Convert a DecodedPicture into a (200, 320, 4) uint8 RGBA image array."""
    pal = palette_override if palette_override is not None else pic.palette
    lut = palette_to_rgba_array(pal, size=32, zero_transparent=False)
    res: npt.NDArray[np.uint8] = lut[pic.pixels]
    return res
