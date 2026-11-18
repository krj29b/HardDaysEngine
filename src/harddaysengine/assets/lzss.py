"""Moonstone forward LZSS stream decompressor and encoder (ARCHITECTURE.md §2.1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

LZSS_MAX_COUNT = 34
LZSS_MIN_COUNT = 3
LZSS_MAX_DISTANCE = 0x07FF


class LZSSFormatError(ValueError):
    """Raised when an LZSS stream is truncated or contains an invalid back-reference."""


@dataclass(slots=True, frozen=True)
class LZSSMatch:
    """Back-reference token (count in 3..34, distance in 0..2047) for synthetic encoding."""

    count: int
    distance: int


def _copy_match(out: bytearray, count: int, distance: int, max_output: int | None) -> None:
    if distance == 0:
        fill = out[-1] if out else 0
        for _ in range(count):
            if max_output is not None and len(out) >= max_output:
                return
            out.append(fill)
        return
    if distance > len(out):
        msg = f"LZSS lookback distance {distance} exceeds current output size {len(out)}"
        raise LZSSFormatError(msg)
    for _ in range(count):
        if max_output is not None and len(out) >= max_output:
            return
        out.append(out[len(out) - distance])


def decompress_lzss(data: bytes | bytearray | memoryview, max_output: int | None = None) -> bytes:
    """Decompress a Moonstone forward LZSS byte stream (ARCHITECTURE.md §2.1)."""
    src = memoryview(data)
    src_len = len(src)
    pos = 0
    out = bytearray()

    while pos < src_len and (max_output is None or len(out) < max_output):
        flags = src[pos]
        pos += 1
        for bit in range(7, -1, -1):
            if pos >= src_len or (max_output is not None and len(out) >= max_output):
                break
            if (flags >> bit) & 1:
                if pos + 1 >= src_len:
                    msg = "Truncated 16-bit back-reference word in LZSS stream"
                    raise LZSSFormatError(msg)
                word = (src[pos] << 8) | src[pos + 1]
                pos += 2
                count = LZSS_MAX_COUNT - (word >> 11)
                distance = word & LZSS_MAX_DISTANCE
                _copy_match(out, count, distance, max_output)
            else:
                out.append(src[pos])
                pos += 1

    return bytes(out)


def encode_lzss_tokens(tokens: Sequence[int | LZSSMatch]) -> bytes:
    """Encode a sequence of literal bytes (int 0..255) and LZSSMatch tokens into a valid stream."""
    out = bytearray()
    idx = 0
    total = len(tokens)
    while idx < total:
        chunk = tokens[idx : idx + 8]
        idx += 8
        flags = 0
        payload = bytearray()
        for bit_idx, item in enumerate(chunk):
            bit_pos = 7 - bit_idx
            if isinstance(item, LZSSMatch):
                if not (LZSS_MIN_COUNT <= item.count <= LZSS_MAX_COUNT):
                    msg = f"Match count {item.count} outside 3..34"
                    raise ValueError(msg)
                if not (0 <= item.distance <= LZSS_MAX_DISTANCE):
                    msg = f"Match distance {item.distance} outside 0..2047"
                    raise ValueError(msg)
                flags |= 1 << bit_pos
                word = ((LZSS_MAX_COUNT - item.count) << 11) | item.distance
                payload.append((word >> 8) & 0xFF)
                payload.append(word & 0xFF)
            else:
                payload.append(item & 0xFF)
        out.append(flags)
        out.extend(payload)
    return bytes(out)


def compress_lzss_literals(data: bytes) -> bytes:
    """Encode raw bytes as a literal-only Moonstone LZSS stream (for synthetic fixtures)."""
    out = bytearray()
    for offset in range(0, len(data), 8):
        out.append(0x00)
        out.extend(data[offset : offset + 8])
    return bytes(out)
