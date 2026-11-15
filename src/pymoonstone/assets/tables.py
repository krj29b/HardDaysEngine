"""Runtime executable table decoders for `program` and `mog` (ARCHITECTURE.md §5, §6.1-§6.2, §7)."""

from __future__ import annotations

import struct
from dataclasses import dataclass

from pymoonstone.assets.hunk import (
    DecodedHunkExecutable,
    HunkFormatError,
    HunkSection,
    SynthInstrumentRecord,
    parse_mog_synth_instruments,
)

MOG_MAP_SITES_SECTION = 2
MOG_MAP_SITES_OFFSET = 100
MOG_MAP_SITES_COUNT = 10
MAP_SITE_END_ID = 0xFFFF

MOG_TABLES_SECTION = 4
MOG_KNIGHT_NAMES_OFFSET = 96
MOG_KNIGHT_COUNT = 4
MOG_KNIGHT_NAME_STRIDE = 22

MOG_CREATURE_TYPE_DEF_OFFSET = 3626
MOG_CREATURE_NODE_POS_OFFSET = 3722
MOG_CREATURE_NODE_AUX_OFFSET = 3818
MOG_CREATURE_NODE_SCRIPT_OFFSET = 3866
MOG_CREATURE_NODE_COUNT = 24

MOG_ACTION_RIGHT_OFFSET = 4130
MOG_ACTION_LEFT_OFFSET = 4152
MOG_ACTION_COUNT = 11

MOG_ENCOUNTER_BANDS_OFFSET = 28356
MOG_ENCOUNTER_BAND_COUNT = 4

MOG_BOLD_FONT_SECTION = 33
PROGRAM_BOLD_FONT_SECTION = 28
BUILTIN_FONT_BYTES = 1024
BUILTIN_FONT_GLYPHS = 128
BUILTIN_FONT_GLYPH_BYTES = 8

MOG_SYNTH_TABLES_SECTION = 44
MOG_SYNTH_BLOB_BASE = 3570
MOG_SYNTH_RELOC_BEG = 4628
MOG_SYNTH_RELOC_END = 4980
MOG_SYNTH_VIB_BASE = 5046
MOG_SYNTH_VIB_COUNT = 4
MOG_SYNTH_VIB_STRIDE = 15
MOG_SYNTH_ENV_BASE = 5106
MOG_SYNTH_ENV_COUNT = 2
MOG_SYNTH_ENV_STRIDE = 8
MOG_SYNTH_SEQ_TABLE_OFFSET = 8788
MOG_SYNTH_SEQ_COUNT = 168

MOG_SYNTH_WAVE_SECTION = 45
MOG_SYNTH_WAVE_BYTES = 170

__all__ = [
    "BUILTIN_FONT_BYTES",
    "BUILTIN_FONT_GLYPHS",
    "BUILTIN_FONT_GLYPH_BYTES",
    "MAP_SITE_END_ID",
    "MOG_ACTION_COUNT",
    "MOG_ACTION_LEFT_OFFSET",
    "MOG_ACTION_RIGHT_OFFSET",
    "MOG_BOLD_FONT_SECTION",
    "MOG_CREATURE_NODE_AUX_OFFSET",
    "MOG_CREATURE_NODE_COUNT",
    "MOG_CREATURE_NODE_POS_OFFSET",
    "MOG_CREATURE_NODE_SCRIPT_OFFSET",
    "MOG_CREATURE_TYPE_DEF_OFFSET",
    "MOG_ENCOUNTER_BANDS_OFFSET",
    "MOG_ENCOUNTER_BAND_COUNT",
    "MOG_KNIGHT_COUNT",
    "MOG_KNIGHT_NAMES_OFFSET",
    "MOG_KNIGHT_NAME_STRIDE",
    "MOG_MAP_SITES_COUNT",
    "MOG_MAP_SITES_OFFSET",
    "MOG_MAP_SITES_SECTION",
    "MOG_SYNTH_BLOB_BASE",
    "MOG_SYNTH_ENV_BASE",
    "MOG_SYNTH_ENV_COUNT",
    "MOG_SYNTH_ENV_STRIDE",
    "MOG_SYNTH_RELOC_BEG",
    "MOG_SYNTH_RELOC_END",
    "MOG_SYNTH_SEQ_COUNT",
    "MOG_SYNTH_SEQ_TABLE_OFFSET",
    "MOG_SYNTH_TABLES_SECTION",
    "MOG_SYNTH_VIB_BASE",
    "MOG_SYNTH_VIB_COUNT",
    "MOG_SYNTH_VIB_STRIDE",
    "MOG_SYNTH_WAVE_BYTES",
    "MOG_SYNTH_WAVE_SECTION",
    "MOG_TABLES_SECTION",
    "PROGRAM_BOLD_FONT_SECTION",
    "BuiltinFontTable",
    "CombatActionTables",
    "CreatureNodeRecord",
    "EncounterBandRecord",
    "KnightScriptTables",
    "LairRegionMetadata",
    "MapSiteRecord",
    "MogRuntimeTables",
    "MonsterScriptTables",
    "ProgramRuntimeTables",
    "SynthSequenceEntry",
    "SynthSequenceTables",
    "load_mog_tables",
    "load_program_tables",
    "parse_builtin_font",
    "parse_combat_action_tables",
    "parse_creature_nodes",
    "parse_encounter_bands",
    "parse_knight_names",
    "parse_knight_script_tables",
    "parse_lair_region_metadata",
    "parse_map_sites",
    "parse_mog_synth_sequences",
    "parse_monster_script_tables",
]


@dataclass(slots=True, frozen=True)
class CombatActionTables:
    """Direction-mask to knight action-script byte-offset tables (`mog` `S_4`)."""

    right_offsets: tuple[int, ...]
    left_offsets: tuple[int, ...]


@dataclass(slots=True, frozen=True)
class CreatureNodeRecord:
    """Overworld creature/lair node parameters from `mog` `S_4` (ARCHITECTURE.md §5.2)."""

    index: int
    creature_type: int
    defence: int
    map_x: int
    map_y: int
    aux_value: int
    arena_script: str


@dataclass(slots=True, frozen=True)
class MapSiteRecord:
    """Static overworld site coordinate entry from `mog` `S_2`."""

    site_id: int
    x: int
    y: int


@dataclass(slots=True, frozen=True)
class EncounterBandRecord:
    """Random 0..99 encounter threshold and kind from `mog` `S_4`."""

    limit: int
    kind: int


@dataclass(slots=True, frozen=True)
class LairRegionMetadata:
    """Combined overworld site, creature/lair node, and encounter-band tables."""

    map_sites: tuple[MapSiteRecord, ...]
    creature_nodes: tuple[CreatureNodeRecord, ...]
    encounter_bands: tuple[EncounterBandRecord, ...]


@dataclass(slots=True, frozen=True)
class BuiltinFontTable:
    """1,024-byte 1-bpp 8x8 built-in bold font (`program` `S_28` / `mog` `S_33`)."""

    glyphs: tuple[bytes, ...]


@dataclass(slots=True, frozen=True)
class SynthSequenceEntry:
    """Single voice sequence pointer and bytecode stream from `mog` `S_44`."""

    index: int
    s44_offset: int
    stream: bytes


@dataclass(slots=True, frozen=True)
class SynthSequenceTables:
    """4-voice VBL synth tables, sequence streams, waveforms, and instruments (`S_44`/`S_45`)."""

    blob_base: int
    blob: bytes
    vibrato_rows: tuple[bytes, ...]
    envelope_rows: tuple[bytes, ...]
    sequences: tuple[SynthSequenceEntry, ...]
    waveforms: bytes
    instruments: tuple[SynthInstrumentRecord, ...]


@dataclass(slots=True, frozen=True)
class KnightScriptTables:
    """Knight fight-script pointer tables (`mog` `S_4` offsets via `S_0`/`S_1` relocs)."""

    idle_script: int
    alt_script: int
    action_scripts: tuple[int, ...]
    hurt_scripts: tuple[int, ...]
    walk_scripts: tuple[tuple[int, ...], ...]
    cel_slot_table_s1_offset: int = 0


@dataclass(slots=True, frozen=True)
class MonsterScriptTables:
    """Monster fight-script and cel-slot pointer tables (`mog` `S_4` offsets via `S_0`/`S_1`)."""

    creature_type: int
    idle_script: int
    alt_script: int
    action_scripts: tuple[int, ...]
    hurt_scripts: tuple[int, ...]
    walk_scripts: tuple[tuple[int, ...], ...]
    cel_slot_table_s1_offset: int = 0


@dataclass(slots=True, frozen=True)
class ProgramRuntimeTables:
    """Runtime tables extracted from the `program` executable."""

    builtin_font: BuiltinFontTable


@dataclass(slots=True, frozen=True)
class MogRuntimeTables:
    """Runtime tables extracted from the `mog` executable."""

    knight_names: tuple[str, ...]
    combat_actions: CombatActionTables
    lair_region: LairRegionMetadata
    builtin_font: BuiltinFontTable
    synth: SynthSequenceTables
    knight_scripts: KnightScriptTables | None = None
    monster_scripts: tuple[MonsterScriptTables, ...] = ()


def _require_section(
    executable: DecodedHunkExecutable,
    section_index: int,
    min_bytes: int,
) -> HunkSection:
    if section_index < 0 or section_index >= len(executable.sections):
        msg = (
            f"Executable {executable.name!r} has {len(executable.sections)} sections "
            f"(need S_{section_index})"
        )
        raise HunkFormatError(msg)
    section = executable.sections[section_index]
    if len(section.data) < min_bytes:
        msg = (
            f"Executable {executable.name!r} S_{section_index} is {len(section.data)} bytes "
            f"(need at least {min_bytes})"
        )
        raise HunkFormatError(msg)
    return section


def _read_cstr(data: bytes, offset: int) -> str:
    if offset < 0 or offset >= len(data):
        msg = f"String offset {offset} out of bounds for {len(data)}-byte section"
        raise HunkFormatError(msg)
    end = data.find(0, offset)
    if end < 0:
        msg = f"Unterminated C-string at offset {offset}"
        raise HunkFormatError(msg)
    return data[offset:end].decode("ascii", errors="replace")


def parse_combat_action_tables(mog_exec: DecodedHunkExecutable) -> CombatActionTables:
    """Read `kActionRight` and `kActionLeft` offset tables from `mog` `S_4`."""
    min_len = MOG_ACTION_LEFT_OFFSET + MOG_ACTION_COUNT * 2
    s4 = _require_section(mog_exec, MOG_TABLES_SECTION, min_len).data
    fmt = f">{MOG_ACTION_COUNT}H"
    right_offsets = struct.unpack_from(fmt, s4, MOG_ACTION_RIGHT_OFFSET)
    left_offsets = struct.unpack_from(fmt, s4, MOG_ACTION_LEFT_OFFSET)
    return CombatActionTables(right_offsets=right_offsets, left_offsets=left_offsets)


def parse_knight_names(mog_exec: DecodedHunkExecutable) -> tuple[str, ...]:
    """Read the 4 default knight names from `mog` `S_4`."""
    min_len = MOG_KNIGHT_NAMES_OFFSET + MOG_KNIGHT_COUNT * MOG_KNIGHT_NAME_STRIDE
    s4 = _require_section(mog_exec, MOG_TABLES_SECTION, min_len).data
    names: list[str] = []
    for idx in range(MOG_KNIGHT_COUNT):
        start = MOG_KNIGHT_NAMES_OFFSET + idx * MOG_KNIGHT_NAME_STRIDE
        raw = s4[start : start + MOG_KNIGHT_NAME_STRIDE]
        nul = raw.find(0)
        trimmed = raw[:nul] if nul >= 0 else raw
        names.append(trimmed.decode("ascii", errors="replace").rstrip(" "))
    return tuple(names)


def parse_creature_nodes(mog_exec: DecodedHunkExecutable) -> tuple[CreatureNodeRecord, ...]:
    """Read the 24 overworld creature/lair node records from `mog` `S_4`."""
    min_len = MOG_CREATURE_NODE_SCRIPT_OFFSET + MOG_CREATURE_NODE_COUNT * 4
    s4 = _require_section(mog_exec, MOG_TABLES_SECTION, min_len).data
    nodes: list[CreatureNodeRecord] = []
    for idx in range(MOG_CREATURE_NODE_COUNT):
        creature_type, defence = struct.unpack_from(
            ">HH", s4, MOG_CREATURE_TYPE_DEF_OFFSET + idx * 4
        )
        map_x, map_y = struct.unpack_from(">HH", s4, MOG_CREATURE_NODE_POS_OFFSET + idx * 4)
        (aux_value,) = struct.unpack_from(">H", s4, MOG_CREATURE_NODE_AUX_OFFSET + idx * 2)
        (script_ptr,) = struct.unpack_from(">I", s4, MOG_CREATURE_NODE_SCRIPT_OFFSET + idx * 4)
        arena_script = _read_cstr(s4, script_ptr) if script_ptr != 0 else ""
        nodes.append(
            CreatureNodeRecord(
                index=idx,
                creature_type=creature_type,
                defence=defence,
                map_x=map_x,
                map_y=map_y,
                aux_value=aux_value,
                arena_script=arena_script,
            )
        )
    return tuple(nodes)


def parse_map_sites(mog_exec: DecodedHunkExecutable) -> tuple[MapSiteRecord, ...]:
    """Read static overworld map site coordinates from `mog` `S_2`."""
    min_len = MOG_MAP_SITES_OFFSET + MOG_MAP_SITES_COUNT * 6
    s2 = _require_section(mog_exec, MOG_MAP_SITES_SECTION, min_len).data
    sites: list[MapSiteRecord] = []
    for idx in range(MOG_MAP_SITES_COUNT):
        site_id, x, y = struct.unpack_from(">HHH", s2, MOG_MAP_SITES_OFFSET + idx * 6)
        if site_id == MAP_SITE_END_ID:
            break
        sites.append(MapSiteRecord(site_id=site_id, x=x, y=y))
    return tuple(sites)


def parse_encounter_bands(mog_exec: DecodedHunkExecutable) -> tuple[EncounterBandRecord, ...]:
    """Read the 4 random-encounter threshold bands from `mog` `S_4`."""
    min_len = MOG_ENCOUNTER_BANDS_OFFSET + MOG_ENCOUNTER_BAND_COUNT * 4
    s4 = _require_section(mog_exec, MOG_TABLES_SECTION, min_len).data
    bands: list[EncounterBandRecord] = []
    for idx in range(MOG_ENCOUNTER_BAND_COUNT):
        limit, kind = struct.unpack_from(">HH", s4, MOG_ENCOUNTER_BANDS_OFFSET + idx * 4)
        bands.append(EncounterBandRecord(limit=limit, kind=kind))
    return tuple(bands)


def parse_lair_region_metadata(mog_exec: DecodedHunkExecutable) -> LairRegionMetadata:
    """Read combined overworld site, creature/lair node, and encounter-band tables."""
    return LairRegionMetadata(
        map_sites=parse_map_sites(mog_exec),
        creature_nodes=parse_creature_nodes(mog_exec),
        encounter_bands=parse_encounter_bands(mog_exec),
    )


def parse_builtin_font(
    executable: DecodedHunkExecutable,
    section_index: int,
) -> BuiltinFontTable:
    """Read the 1,024-byte 1-bpp 8x8 built-in bold font (`program` `S_28` or `mog` `S_33`)."""
    raw = _require_section(executable, section_index, BUILTIN_FONT_BYTES).data
    glyphs = tuple(
        raw[idx * BUILTIN_FONT_GLYPH_BYTES : (idx + 1) * BUILTIN_FONT_GLYPH_BYTES]
        for idx in range(BUILTIN_FONT_GLYPHS)
    )
    return BuiltinFontTable(glyphs=glyphs)


def _extract_synth_sequences(s44: bytes) -> tuple[SynthSequenceEntry, ...]:
    raw_ptrs = struct.unpack_from(f">{MOG_SYNTH_SEQ_COUNT}I", s44, MOG_SYNTH_SEQ_TABLE_OFFSET)
    sorted_starts = sorted({ptr for ptr in raw_ptrs if ptr > 0})
    next_start_map: dict[int, int] = {}
    for pos, start in enumerate(sorted_starts):
        if start < MOG_SYNTH_BLOB_BASE or start >= MOG_SYNTH_SEQ_TABLE_OFFSET:
            msg = f"Synth sequence offset {start} outside S_44 sequence region"
            raise HunkFormatError(msg)
        next_start = (
            sorted_starts[pos + 1] if pos + 1 < len(sorted_starts) else MOG_SYNTH_SEQ_TABLE_OFFSET
        )
        next_start_map[start] = next_start

    entries: list[SynthSequenceEntry] = []
    for idx, ptr in enumerate(raw_ptrs):
        if ptr == 0:
            entries.append(SynthSequenceEntry(index=idx, s44_offset=0, stream=b""))
        else:
            entries.append(
                SynthSequenceEntry(
                    index=idx,
                    s44_offset=ptr,
                    stream=s44[ptr : next_start_map[ptr]],
                )
            )
    return tuple(entries)


def parse_mog_synth_sequences(mog_exec: DecodedHunkExecutable) -> SynthSequenceTables:
    """Read 4-voice synth tables, sequence streams, waveforms, and instruments (`S_44`/`S_45`)."""
    s44_min = MOG_SYNTH_SEQ_TABLE_OFFSET + MOG_SYNTH_SEQ_COUNT * 4
    s44 = _require_section(mog_exec, MOG_SYNTH_TABLES_SECTION, s44_min).data
    s45 = _require_section(mog_exec, MOG_SYNTH_WAVE_SECTION, MOG_SYNTH_WAVE_BYTES).data

    blob_buf = bytearray(s44[MOG_SYNTH_BLOB_BASE:])
    blob_buf[0:2] = b"\x00\x00"
    reloc_start = MOG_SYNTH_RELOC_BEG - MOG_SYNTH_BLOB_BASE
    reloc_end = MOG_SYNTH_RELOC_END - MOG_SYNTH_BLOB_BASE
    blob_buf[reloc_start:reloc_end] = bytes(reloc_end - reloc_start)

    vibrato_rows = tuple(
        s44[
            MOG_SYNTH_VIB_BASE + i * MOG_SYNTH_VIB_STRIDE : MOG_SYNTH_VIB_BASE
            + (i + 1) * MOG_SYNTH_VIB_STRIDE
        ]
        for i in range(MOG_SYNTH_VIB_COUNT)
    )
    envelope_rows = tuple(
        s44[
            MOG_SYNTH_ENV_BASE + i * MOG_SYNTH_ENV_STRIDE : MOG_SYNTH_ENV_BASE
            + (i + 1) * MOG_SYNTH_ENV_STRIDE
        ]
        for i in range(MOG_SYNTH_ENV_COUNT)
    )
    sequences = _extract_synth_sequences(s44)
    instruments = parse_mog_synth_instruments(mog_exec)

    return SynthSequenceTables(
        blob_base=MOG_SYNTH_BLOB_BASE,
        blob=bytes(blob_buf),
        vibrato_rows=vibrato_rows,
        envelope_rows=envelope_rows,
        sequences=sequences,
        waveforms=s45[:MOG_SYNTH_WAVE_BYTES],
        instruments=instruments,
    )


def _step_s1_table_write(
    data: bytes,
    pos: int,
    op: int,
    r4: set[int],
    table: dict[int, int],
    seq: int,
) -> tuple[int, int]:
    if op == 0x217C and (pos + 2) in r4 and pos + 8 <= len(data):
        val, disp = struct.unpack_from(">Ih", data, pos + 2)
        table[disp] = val
        return pos + 8, seq
    if op == 0x20FC and pos + 6 <= len(data):
        if (pos + 2) in r4:
            (table[seq],) = struct.unpack_from(">I", data, pos + 2)
        return pos + 6, seq + 4
    if op == 0x4298:
        return pos + 2, seq + 4
    return pos + 2, seq


def _step_s1_pointer_op(
    data: bytes,
    pos: int,
    r1: set[int],
    r4: set[int],
    tables: dict[int, dict[int, int]],
    cur_a0: int | None,
    seq: int,
) -> tuple[int, int | None, int]:
    (op,) = struct.unpack_from(">H", data, pos)
    if op == 0x41F9 and (pos + 2) in r1 and pos + 6 <= len(data):
        (next_a0,) = struct.unpack_from(">I", data, pos + 2)
        tables.setdefault(next_a0, {})
        return pos + 6, next_a0, 0
    if cur_a0 is None:
        return pos + 2, None, seq
    next_pos, next_seq = _step_s1_table_write(data, pos, op, r4, tables[cur_a0], seq)
    return next_pos, cur_a0, next_seq


def _scan_s1_pointer_tables(s0: HunkSection) -> dict[int, dict[int, int]]:
    """Scan `mog` `S_0` `LEA S_1+base, A0` initializers for `S_4` script-pointer tables."""
    data = s0.data
    r1 = set(s0.relocs.get(1, ()))
    r4 = set(s0.relocs.get(4, ()))
    tables: dict[int, dict[int, int]] = {}
    cur_a0: int | None = None
    seq = 0
    pos = 0
    while pos + 2 <= len(data):
        pos, cur_a0, seq = _step_s1_pointer_op(data, pos, r1, r4, tables, cur_a0, seq)
    return tables


def _step_actor_init_op(
    data: bytes,
    pos: int,
    r1: set[int],
    r4: set[int],
    cur: dict[int | str, int],
    blocks: list[dict[int | str, int]],
) -> tuple[int, dict[int | str, int]]:
    (op,) = struct.unpack_from(">H", data, pos)
    if op == 0x4E75:
        if 22 in cur:
            blocks.append(cur)
        return pos + 2, {}
    if op == 0x237C and pos + 8 <= len(data) and ((pos + 2) in r4 or (pos + 2) in r1):
        val, disp = struct.unpack_from(">Ih", data, pos + 2)
        cur[disp] = val
        return pos + 8, cur
    if op == 0x137C and pos + 6 <= len(data):
        val, disp = struct.unpack_from(">Hh", data, pos + 2)
        if disp == 77:
            cur["type"] = val
        return pos + 6, cur
    return pos + 2, cur


def _scan_actor_init_blocks(s0: HunkSection) -> list[dict[int | str, int]]:
    """Scan `mog` `S_0` `RTS`-delimited actor initializers writing `22(A1)` (`ulIdleScript`)."""
    data = s0.data
    r1 = set(s0.relocs.get(1, ()))
    r4 = set(s0.relocs.get(4, ()))
    blocks: list[dict[int | str, int]] = []
    cur: dict[int | str, int] = {}
    pos = 0
    while pos + 2 <= len(data):
        pos, cur = _step_actor_init_op(data, pos, r1, r4, cur, blocks)
    return blocks


def _extract_9_entries(tables: dict[int, dict[int, int]], s1_base: int | None) -> tuple[int, ...]:
    if s1_base is None or s1_base not in tables:
        return (0,) * 9
    entries = tables[s1_base]
    return tuple(entries.get(idx * 4, 0) for idx in range(9))


def _extract_walk_blocks(
    tables: dict[int, dict[int, int]],
    s1_base: int | None,
) -> tuple[tuple[int, ...], ...]:
    if s1_base is None or s1_base not in tables:
        return ((0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0))
    entries = tables[s1_base]
    return tuple(
        tuple(entries.get(block * 32 + phase * 4, 0) for phase in range(4)) for block in range(3)
    )


def parse_knight_script_tables(mog_exec: DecodedHunkExecutable) -> KnightScriptTables:
    """Decode knight fight-script pointer tables from `mog` `S_0`/`S_1`/`S_4` (§7.2)."""
    s0 = _require_section(mog_exec, 0, 2)
    _require_section(mog_exec, MOG_TABLES_SECTION, 2)
    s1_tables = _scan_s1_pointer_tables(s0)
    for block in _scan_actor_init_blocks(s0):
        if "type" not in block and 34 in block:
            return KnightScriptTables(
                idle_script=block.get(22, 0),
                alt_script=block.get(26, 0),
                action_scripts=_extract_9_entries(s1_tables, block.get(34)),
                hurt_scripts=_extract_9_entries(s1_tables, block.get(30)),
                walk_scripts=_extract_walk_blocks(s1_tables, block.get(46)),
                cel_slot_table_s1_offset=block.get(38, 0),
            )
    msg = f"Executable {mog_exec.name!r} S_0 contains no knight script-table initializer"
    raise HunkFormatError(msg)


def parse_monster_script_tables(mog_exec: DecodedHunkExecutable) -> tuple[MonsterScriptTables, ...]:
    """Decode monster fight-script pointer tables from `mog` `S_0`/`S_1`/`S_4` (§7.2)."""
    s0 = _require_section(mog_exec, 0, 2)
    _require_section(mog_exec, MOG_TABLES_SECTION, 2)
    s1_tables = _scan_s1_pointer_tables(s0)
    results: list[MonsterScriptTables] = []
    seen_types: set[int] = set()
    for block in _scan_actor_init_blocks(s0):
        if "type" not in block:
            continue
        creature_type = block["type"]
        if creature_type in seen_types:
            continue
        seen_types.add(creature_type)
        results.append(
            MonsterScriptTables(
                creature_type=creature_type,
                idle_script=block.get(22, 0),
                alt_script=block.get(26, 0),
                action_scripts=_extract_9_entries(s1_tables, block.get(34)),
                hurt_scripts=_extract_9_entries(s1_tables, block.get(30)),
                walk_scripts=_extract_walk_blocks(s1_tables, block.get(46)),
                cel_slot_table_s1_offset=block.get(38, 0),
            )
        )
    return tuple(results)


def load_mog_tables(mog_exec: DecodedHunkExecutable) -> MogRuntimeTables:
    """Load all runtime data tables from a parsed `mog` executable."""
    s0 = mog_exec.sections[0] if mog_exec.sections else None
    has_script_relocs = s0 is not None and 1 in s0.relocs and 4 in s0.relocs
    knight_scripts = parse_knight_script_tables(mog_exec) if has_script_relocs else None
    monster_scripts = parse_monster_script_tables(mog_exec) if has_script_relocs else ()
    return MogRuntimeTables(
        knight_names=parse_knight_names(mog_exec),
        combat_actions=parse_combat_action_tables(mog_exec),
        lair_region=parse_lair_region_metadata(mog_exec),
        builtin_font=parse_builtin_font(mog_exec, MOG_BOLD_FONT_SECTION),
        synth=parse_mog_synth_sequences(mog_exec),
        knight_scripts=knight_scripts,
        monster_scripts=monster_scripts,
    )


def load_program_tables(program_exec: DecodedHunkExecutable) -> ProgramRuntimeTables:
    """Load all runtime data tables from a parsed `program` executable."""
    return ProgramRuntimeTables(
        builtin_font=parse_builtin_font(program_exec, PROGRAM_BOLD_FONT_SECTION),
    )
