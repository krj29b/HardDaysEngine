"""Sprite & cel container decoder (.cel, .ob, .c, .f, .font) (ARCHITECTURE.md §3.2)."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import numpy.typing as npt

from harddaysengine.assets.lzss import decompress_lzss
from harddaysengine.assets.palette import RGBColor, default_combat_palette, palette_to_rgba_array
from harddaysengine.assets.picture import planar_to_indexed

if TYPE_CHECKING:
    from collections.abc import Sequence

CEL_HEADER_SIZE = 10
CEL_FRAME_ENTRY_SIZE = 10


class CelFormatError(ValueError):
    """Raised when a cel header, frame directory, or plane buffer is malformed."""


@dataclass(slots=True, frozen=True)
class CelFrameMeta:
    """10-byte cel frame directory entry (ARCHITECTURE.md §3.2)."""

    index: int
    data_offset: int
    width: int
    height: int
    hot_and_flags: int
    plane_bits: int

    @property
    def hot_x(self) -> int:
        """Horizontal alignment shift subtracted from draw X (`hot_and_flags >> 4`)."""
        return (self.hot_and_flags >> 4) & 0x0F

    @property
    def unflipped(self) -> bool:
        """True when bit 0 of `hot_and_flags` is 1 (original unflipped orientation on disk)."""
        return (self.hot_and_flags & 1) == 1

    @property
    def row_bytes(self) -> int:
        """Word-aligned byte stride per scanline (`((width + 15) >> 4) * 2`)."""
        return ((self.width + 15) >> 4) * 2

    @property
    def plane_count(self) -> int:
        """Number of bitplanes stored for this frame (`popcount(plane_bits & 0x1F)`)."""
        return (self.plane_bits & 0x1F).bit_count()

    @property
    def total_plane_bytes(self) -> int:
        """Total byte count across all present bitplanes for this frame."""
        return self.row_bytes * self.height * self.plane_count


@dataclass(slots=True, frozen=True)
class CelFrame:
    """Decoded cel frame with indexed pixels (0..31) and 1-bit opacity/hurtbox mask."""

    meta: CelFrameMeta
    pixels: npt.NDArray[np.uint8]
    mask: npt.NDArray[np.bool_]


@dataclass(slots=True, frozen=True)
class DecodedCel:
    """Decoded cel container with all frames and raw decompressed plane buffer."""

    frame_count: int
    packed_size: int
    unpacked_bits: int
    frames: tuple[CelFrame, ...]
    raw_planes: bytes


def _parse_frame_directory(view: memoryview, frame_count: int) -> list[CelFrameMeta]:
    entries: list[CelFrameMeta] = []
    for idx in range(frame_count):
        entry_ofs = CEL_HEADER_SIZE + idx * CEL_FRAME_ENTRY_SIZE
        data_offset, width, height, hot_flags, plane_bits = struct.unpack_from(
            ">IHHBB", view, entry_ofs
        )
        entries.append(
            CelFrameMeta(
                index=idx,
                data_offset=data_offset,
                width=width,
                height=height,
                hot_and_flags=hot_flags,
                plane_bits=plane_bits,
            )
        )
    return entries


def _decode_single_frame(meta: CelFrameMeta, raw_view: memoryview) -> CelFrame:
    if meta.width == 0 or meta.height == 0 or (meta.plane_bits & 0x1F) == 0:
        empty_px = np.zeros((meta.height, meta.width), dtype=np.uint8)
        empty_mask = np.zeros((meta.height, meta.width), dtype=np.bool_)
        return CelFrame(meta=meta, pixels=empty_px, mask=empty_mask)

    end_offset = meta.data_offset + meta.total_plane_bytes
    if end_offset > len(raw_view):
        padded = bytearray(meta.total_plane_bytes)
        avail = max(0, len(raw_view) - meta.data_offset)
        if avail > 0:
            padded[:avail] = raw_view[meta.data_offset : meta.data_offset + avail]
        plane_slice = memoryview(padded)
    else:
        plane_slice = raw_view[meta.data_offset : end_offset]

    pixels = planar_to_indexed(
        plane_slice,
        meta.width,
        meta.height,
        planes=5,
        plane_bits=meta.plane_bits & 0x1F,
    )
    mask: npt.NDArray[np.bool_] = pixels != 0
    return CelFrame(meta=meta, pixels=pixels, mask=mask)


def decode_cel(data: bytes | bytearray | memoryview) -> DecodedCel:
    """Decode a Moonstone cel family file (.cel, .ob, .c, .f, .font) (ARCHITECTURE.md §3.2)."""
    view = memoryview(data)
    if len(view) < CEL_HEADER_SIZE:
        msg = f"Cel buffer too short ({len(view)} bytes < {CEL_HEADER_SIZE})"
        raise CelFormatError(msg)

    frame_count, packed_size, unpacked_bits = struct.unpack_from(">HII", view, 0)
    stream_start = CEL_HEADER_SIZE + frame_count * CEL_FRAME_ENTRY_SIZE
    if stream_start > len(view):
        msg = (
            f"Cel frame table truncated: need {stream_start} bytes for "
            f"{frame_count} frames, got {len(view)}"
        )
        raise CelFormatError(msg)

    metas = _parse_frame_directory(view, frame_count)
    unpacked_bytes = unpacked_bits >> 3
    stream_end = min(len(view), stream_start + packed_size)
    raw_planes = decompress_lzss(
        view[stream_start:stream_end],
        max_output=unpacked_bytes if unpacked_bytes > 0 else None,
    )
    if unpacked_bytes > len(raw_planes):
        raw_planes = raw_planes + b"\x00" * (unpacked_bytes - len(raw_planes))

    raw_view = memoryview(raw_planes)
    frames = tuple(_decode_single_frame(m, raw_view) for m in metas)
    return DecodedCel(
        frame_count=frame_count,
        packed_size=packed_size,
        unpacked_bits=unpacked_bits,
        frames=frames,
        raw_planes=raw_planes,
    )


def flip_cel_frame(frame: CelFrame) -> CelFrame:
    """Horizontally mirror a cel frame (`mirrorCelHeader`/`Planes`, ARCHITECTURE.md §3.2)."""
    meta = frame.meta
    aligned16 = (meta.width + 15) & ~15
    if meta.unflipped:
        new_hot = ((aligned16 - meta.width) & 0x0F) << 4
        new_flags = new_hot  # bit 0 cleared -> flipped
    else:
        new_flags = 0x01  # bit 0 set -> unflipped, hot_x = 0

    flipped_meta = CelFrameMeta(
        index=meta.index,
        data_offset=meta.data_offset,
        width=meta.width,
        height=meta.height,
        hot_and_flags=new_flags,
        plane_bits=meta.plane_bits,
    )
    flipped_pixels: npt.NDArray[np.uint8] = np.ascontiguousarray(frame.pixels[:, ::-1])
    flipped_mask: npt.NDArray[np.bool_] = np.ascontiguousarray(frame.mask[:, ::-1])
    return CelFrame(meta=flipped_meta, pixels=flipped_pixels, mask=flipped_mask)


def cel_frame_to_rgba(
    frame: CelFrame,
    palette: Sequence[RGBColor] | None = None,
) -> npt.NDArray[np.uint8]:
    """Convert a CelFrame into a (height, width, 4) uint8 RGBA array with colour 0 transparent."""
    pal = palette if palette is not None else default_combat_palette()
    lut = palette_to_rgba_array(pal, size=32, zero_transparent=True)
    if frame.meta.width == 0 or frame.meta.height == 0:
        return np.zeros((frame.meta.height, frame.meta.width, 4), dtype=np.uint8)
    res: npt.NDArray[np.uint8] = lut[frame.pixels]
    return res
