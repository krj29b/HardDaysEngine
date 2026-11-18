"""AmigaDOS Hunk executable parser for runtime tables (`program`, `mog`) (ARCHITECTURE.md §5)."""

from __future__ import annotations

import struct
from dataclasses import dataclass

HUNK_HEADER = 0x000003F3
HUNK_CODE = 0x000003E9
HUNK_DATA = 0x000003EA
HUNK_BSS = 0x000003EB
HUNK_RELOC32 = 0x000003EC
HUNK_SYMBOL = 0x000003F0
HUNK_DEBUG = 0x000003F1
HUNK_END = 0x000003F2


class HunkFormatError(ValueError):
    """Raised when an AmigaDOS Hunk executable is malformed."""


@dataclass(slots=True, frozen=True)
class SynthInstrumentRecord:
    """14-byte synth instrument entry in `mog` Hunk `S_45` at offset +168 (ARCHITECTURE.md §6.1)."""

    index: int
    loop_flag: int
    loop_offset: int
    length_words: int
    sample_offset: int
    pitch_offset: int


@dataclass(slots=True, frozen=True)
class HunkSection:
    """Single CODE, DATA, or BSS hunk section parsed from an Amiga executable."""

    index: int
    hunk_type: int
    alloc_size: int
    data: bytes
    relocs: dict[int, tuple[int, ...]]

    @property
    def type_name(self) -> str:
        """Human-readable hunk type ('CODE', 'DATA', or 'BSS')."""
        if self.hunk_type == HUNK_CODE:
            return "CODE"
        if self.hunk_type == HUNK_DATA:
            return "DATA"
        return "BSS"


@dataclass(slots=True, frozen=True)
class DecodedHunkExecutable:
    """Parsed AmigaDOS Hunk executable (`program`, `mog`, `nb`, `Crystal`)."""

    name: str
    sections: tuple[HunkSection, ...]


def _read_u32(view: memoryview, pos: int) -> tuple[int, int]:
    if pos + 4 > len(view):
        msg = f"Unexpected end of hunk stream at offset {pos}"
        raise HunkFormatError(msg)
    (val,) = struct.unpack_from(">I", view, pos)
    return val, pos + 4


def _skip_string_list(view: memoryview, pos: int) -> int:
    while True:
        num_longs, pos = _read_u32(view, pos)
        if num_longs == 0:
            return pos
        pos += num_longs * 4


def _parse_reloc32(view: memoryview, pos: int, relocs: dict[int, list[int]]) -> int:
    while True:
        count, pos = _read_u32(view, pos)
        if count == 0:
            return pos
        target_hunk, pos = _read_u32(view, pos)
        offsets_end = pos + count * 4
        if offsets_end > len(view):
            msg = "Truncated HUNK_RELOC32 table"
            raise HunkFormatError(msg)
        offsets = struct.unpack_from(f">{count}I", view, pos)
        relocs.setdefault(target_hunk, []).extend(offsets)
        pos = offsets_end


def _parse_single_section(
    view: memoryview,
    pos: int,
    sec_idx: int,
    alloc_size: int,
) -> tuple[HunkSection, int]:
    hunk_type = HUNK_BSS
    sec_data = b""
    relocs: dict[int, list[int]] = {}

    while pos < len(view):
        block_id, pos = _read_u32(view, pos)
        block_type = block_id & 0x3FFFFFFF
        if block_type in (HUNK_CODE, HUNK_DATA):
            hunk_type = block_type
            data_longs, pos = _read_u32(view, pos)
            byte_len = data_longs * 4
            sec_data = bytes(view[pos : pos + byte_len])
            pos += byte_len
        elif block_type == HUNK_BSS:
            hunk_type = HUNK_BSS
            _, pos = _read_u32(view, pos)
            sec_data = b"\x00" * alloc_size
        elif block_type == HUNK_RELOC32:
            pos = _parse_reloc32(view, pos, relocs)
        elif block_type == HUNK_DEBUG:
            debug_longs, pos = _read_u32(view, pos)
            pos += debug_longs * 4
        elif block_type == HUNK_SYMBOL:
            pos = _skip_string_list(view, pos)
        elif block_type == HUNK_END:
            break
        else:
            msg = f"Unsupported hunk block type {block_type:#x} at offset {pos - 4}"
            raise HunkFormatError(msg)

    frozen_relocs = {k: tuple(v) for k, v in sorted(relocs.items())}
    section = HunkSection(
        index=sec_idx,
        hunk_type=hunk_type,
        alloc_size=alloc_size,
        data=sec_data,
        relocs=frozen_relocs,
    )
    return section, pos


def parse_hunk_executable(name: str, data: bytes | bytearray | memoryview) -> DecodedHunkExecutable:
    """Parse an AmigaDOS Hunk executable into its constituent sections (ARCHITECTURE.md §5.1)."""
    view = memoryview(data)
    magic, pos = _read_u32(view, 0)
    if magic != HUNK_HEADER:
        msg = f"File {name!r} is not an Amiga Hunk executable (magic {magic:#010x})"
        raise HunkFormatError(msg)

    pos = _skip_string_list(view, pos)
    table_size, pos = _read_u32(view, pos)
    first_hunk, pos = _read_u32(view, pos)
    last_hunk, pos = _read_u32(view, pos)
    hunk_count = last_hunk - first_hunk + 1
    if hunk_count <= 0 or table_size < hunk_count:
        msg = (
            f"Invalid hunk header counts in {name!r}: "
            f"table={table_size}, range={first_hunk}..{last_hunk}"
        )
        raise HunkFormatError(msg)

    alloc_sizes: list[int] = []
    for _ in range(hunk_count):
        size_word, pos = _read_u32(view, pos)
        alloc_sizes.append((size_word & 0x3FFFFFFF) * 4)

    sections: list[HunkSection] = []
    for sec_idx in range(hunk_count):
        sec, pos = _parse_single_section(view, pos, sec_idx, alloc_sizes[sec_idx])
        sections.append(sec)

    return DecodedHunkExecutable(name=name, sections=tuple(sections))


def parse_mog_synth_instruments(
    mog_exec: DecodedHunkExecutable,
) -> tuple[SynthInstrumentRecord, ...]:
    """Extract the 131 synth instrument records from `mog` `S_45` at +168 (ARCHITECTURE.md §6.1)."""
    if len(mog_exec.sections) <= 45:
        msg = f"Executable {mog_exec.name!r} has {len(mog_exec.sections)} sections (need S_45)"
        raise HunkFormatError(msg)
    s45 = memoryview(mog_exec.sections[45].data)
    base_offset = 168
    records: list[SynthInstrumentRecord] = []
    for idx in range(131):
        ofs = base_offset + idx * 14
        if ofs + 14 > len(s45):
            break
        loop_flag, loop_ofs, len_words, sample_ofs, pitch_ofs = struct.unpack_from(
            ">hHHII", s45, ofs
        )
        records.append(
            SynthInstrumentRecord(
                index=idx,
                loop_flag=loop_flag,
                loop_offset=loop_ofs,
                length_words=len_words,
                sample_offset=sample_ofs,
                pitch_offset=pitch_ofs,
            )
        )
    return tuple(records)
