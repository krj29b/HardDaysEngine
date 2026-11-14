"""Rob Northen RNC Method 1 backward stream decompressor (ARCHITECTURE.md §2.2)."""

from __future__ import annotations

import struct
from dataclasses import dataclass

RNC1_MAGIC = 0x524E4301  # b"RNC\x01"
RNC_HEADER_SIZE = 12

# Indexed by step counter 3..0 (ARCHITECTURE.md §2.2)
_LIT_WIDTHS = (10, 3, 2, 2)
_LIT_BASES = (14, 7, 4, 1)

# Indexed by step + 1 (0..4)
_LEN_BITS = (10, 2, 1, 0, 0)
_LEN_BASES = (10, 6, 4, 3, 2)

# Indexed by step + 1 (0..2)
_OFS_BITS = (12, 5, 8)
_OFS_BASES = (0x0120, 0x0000, 0x0020)


class RNCFormatError(ValueError):
    """Raised when an RNC1 header or compressed stream is invalid."""


@dataclass(slots=True, frozen=True)
class RNCHeader:
    """12-byte RNC Method 1 container header."""

    unpacked_size: int
    packed_size: int


def parse_rnc_header(data: bytes | bytearray | memoryview) -> RNCHeader:
    """Parse and validate the 12-byte RNC1 header (ARCHITECTURE.md §2.2)."""
    view = memoryview(data)
    if len(view) < RNC_HEADER_SIZE:
        msg = f"RNC buffer too short ({len(view)} bytes < 12)"
        raise RNCFormatError(msg)
    magic, unpacked_size, packed_size = struct.unpack_from(">III", view, 0)
    if magic != RNC1_MAGIC:
        msg = f"Invalid RNC1 magic {magic:#010x} (expected {RNC1_MAGIC:#010x})"
        raise RNCFormatError(msg)
    if len(view) < RNC_HEADER_SIZE + packed_size:
        msg = f"RNC stream truncated: need {RNC_HEADER_SIZE + packed_size} bytes, got {len(view)}"
        raise RNCFormatError(msg)
    return RNCHeader(unpacked_size=unpacked_size, packed_size=packed_size)


class _BackwardBitReader:
    """MSB-first sentinel-bit shifting reader moving backwards from the end of the stream."""

    def __init__(self, view: memoryview, end_offset: int) -> None:
        self._view = view
        self.read_pos = end_offset - 1
        self._bits = int(view[self.read_pos]) if self.read_pos >= 0 else 0

    def read_byte(self) -> int:
        self.read_pos -= 1
        return int(self._view[self.read_pos]) if self.read_pos >= 0 else 0

    def get_bit(self) -> int:
        is_set = (self._bits & 0x80) != 0
        self._bits = (self._bits << 1) & 0xFF
        if self._bits != 0:
            return 1 if is_set else 0
        next_byte = self.read_byte()
        self._bits = ((next_byte << 1) | (1 if is_set else 0)) & 0xFF
        return 1 if (next_byte & 0x80) != 0 else 0

    def get_bits(self, count: int) -> int:
        result = 0
        for _ in range(count):
            result = (result << 1) | self.get_bit()
        return result


def _read_literal_count(bits: _BackwardBitReader) -> int:
    if bits.get_bit() == 0:
        return 0
    count = 0
    if bits.get_bit() == 1:
        step = 3
        while True:
            width = _LIT_WIDTHS[step]
            count = bits.get_bits(width)
            all_ones = (1 << width) - 1
            if step == 0 or count != all_ones:
                break
            step -= 1
        count += _LIT_BASES[step]
    return count + 1


def _read_match_length(bits: _BackwardBitReader) -> int:
    step = 3
    while bits.get_bit() == 1:
        step -= 1
        if step < 0:
            break
    idx = step + 1
    return bits.get_bits(_LEN_BITS[idx]) + _LEN_BASES[idx]


def _read_match_offset(bits: _BackwardBitReader, length: int) -> int:
    if length == 2:
        if bits.get_bit() == 1:
            return bits.get_bits(9) + 64
        return bits.get_bits(6)
    step = 1
    while bits.get_bit() == 1:
        step -= 1
        if step < 0:
            break
    idx = step + 1
    return bits.get_bits(_OFS_BITS[idx]) + _OFS_BASES[idx]


def _copy_backward_match(out: bytearray, write_pos: int, length: int, offset: int) -> int:
    last = length - 1
    copy_pos = (write_pos + offset + last) if offset != 0 else (write_pos + 1)
    for _ in range(last + 1):
        write_pos -= 1
        copy_pos -= 1
        if 0 <= write_pos < len(out):
            out[write_pos] = out[copy_pos] if 0 <= copy_pos < len(out) else 0
    return write_pos


def decompress_rnc1(data: bytes | bytearray | memoryview) -> bytes:
    """Decompress a backward-stream RNC1 container into raw bytes (ARCHITECTURE.md §2.2)."""
    hdr = parse_rnc_header(data)
    if hdr.unpacked_size == 0 or hdr.packed_size == 0:
        return b"\x00" * hdr.unpacked_size

    view = memoryview(data)
    bits = _BackwardBitReader(view, RNC_HEADER_SIZE + hdr.packed_size)
    out = bytearray(hdr.unpacked_size)
    write_pos = hdr.unpacked_size

    while True:
        lit_count = _read_literal_count(bits)
        for _ in range(lit_count):
            b = bits.read_byte()
            write_pos -= 1
            if 0 <= write_pos < len(out):
                out[write_pos] = b
        if bits.read_pos <= RNC_HEADER_SIZE:
            break
        length = _read_match_length(bits)
        offset = _read_match_offset(bits, length)
        write_pos = _copy_backward_match(out, write_pos, length, offset)

    return bytes(out)
