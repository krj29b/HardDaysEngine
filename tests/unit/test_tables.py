"""Unit tests for runtime executable table decoders (`src/pymoonstone/assets/tables.py`)."""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from pymoonstone.assets.adf import DiskSet
from pymoonstone.assets.hunk import (
    HUNK_CODE,
    HUNK_DATA,
    HUNK_END,
    HUNK_HEADER,
    DecodedHunkExecutable,
    HunkFormatError,
    parse_hunk_executable,
)
from pymoonstone.assets.tables import (
    BUILTIN_FONT_BYTES,
    BUILTIN_FONT_GLYPHS,
    MOG_ACTION_COUNT,
    MOG_ACTION_LEFT_OFFSET,
    MOG_ACTION_RIGHT_OFFSET,
    MOG_BOLD_FONT_SECTION,
    MOG_CREATURE_NODE_AUX_OFFSET,
    MOG_CREATURE_NODE_COUNT,
    MOG_CREATURE_NODE_POS_OFFSET,
    MOG_CREATURE_NODE_SCRIPT_OFFSET,
    MOG_CREATURE_TYPE_DEF_OFFSET,
    MOG_ENCOUNTER_BAND_COUNT,
    MOG_ENCOUNTER_BANDS_OFFSET,
    MOG_KNIGHT_COUNT,
    MOG_KNIGHT_NAME_STRIDE,
    MOG_KNIGHT_NAMES_OFFSET,
    MOG_MAP_SITES_OFFSET,
    MOG_MAP_SITES_SECTION,
    MOG_SYNTH_BLOB_BASE,
    MOG_SYNTH_ENV_BASE,
    MOG_SYNTH_ENV_COUNT,
    MOG_SYNTH_ENV_STRIDE,
    MOG_SYNTH_RELOC_BEG,
    MOG_SYNTH_RELOC_END,
    MOG_SYNTH_SEQ_COUNT,
    MOG_SYNTH_SEQ_TABLE_OFFSET,
    MOG_SYNTH_TABLES_SECTION,
    MOG_SYNTH_VIB_BASE,
    MOG_SYNTH_VIB_COUNT,
    MOG_SYNTH_VIB_STRIDE,
    MOG_SYNTH_WAVE_BYTES,
    MOG_SYNTH_WAVE_SECTION,
    MOG_TABLES_SECTION,
    PROGRAM_BOLD_FONT_SECTION,
    load_mog_tables,
    load_program_tables,
    parse_combat_action_tables,
    parse_knight_script_tables,
    parse_monster_script_tables,
    parse_creature_nodes,
    parse_encounter_bands,
    parse_knight_names,
    parse_map_sites,
    parse_mog_synth_sequences,
)


def _build_synthetic_hunk_binary(sections: list[tuple[int, bytes]]) -> bytes:
    """Build an AmigaDOS Hunk executable from `(hunk_type, payload)` pairs."""
    count = len(sections)
    out = bytearray()
    out.extend(struct.pack(">5I", HUNK_HEADER, 0, count, 0, count - 1))
    for _, payload in sections:
        padded_len = (len(payload) + 3) & ~3
        out.extend(struct.pack(">I", padded_len // 4))
    for hunk_type, payload in sections:
        padded = payload.ljust((len(payload) + 3) & ~3, b"\x00")
        out.extend(struct.pack(">2I", hunk_type, len(padded) // 4))
        out.extend(padded)
        out.extend(struct.pack(">I", HUNK_END))
    return bytes(out)


def _build_synthetic_s2() -> bytes:
    s2 = bytearray(160)
    for idx in range(9):
        struct.pack_into(">HHH", s2, MOG_MAP_SITES_OFFSET + idx * 6, 0x15 + idx, 10 + idx * 5, 20)
    struct.pack_into(">HHH", s2, MOG_MAP_SITES_OFFSET + 9 * 6, 0xFFFF, 0xFFFF, 0xFFFF)
    return bytes(s2)


def _build_synthetic_s4() -> bytes:
    s4 = bytearray(29000)
    for idx in range(MOG_KNIGHT_COUNT):
        label = f"KNIGHT_{idx}".ljust(MOG_KNIGHT_NAME_STRIDE - 1, " ").encode("ascii") + b"\x00"
        start = MOG_KNIGHT_NAMES_OFFSET + idx * MOG_KNIGHT_NAME_STRIDE
        s4[start : start + MOG_KNIGHT_NAME_STRIDE] = label

    str_cursor = 4200
    for idx in range(MOG_CREATURE_NODE_COUNT):
        struct.pack_into(">HH", s4, MOG_CREATURE_TYPE_DEF_OFFSET + idx * 4, 24 + idx, 10 + idx)
        struct.pack_into(">HH", s4, MOG_CREATURE_NODE_POS_OFFSET + idx * 4, 50 + idx, 100 + idx)
        struct.pack_into(">H", s4, MOG_CREATURE_NODE_AUX_OFFSET + idx * 2, (idx // 6) * 4)
        script_name = f"lair{idx:02d}.t\x00".encode("ascii")
        s4[str_cursor : str_cursor + len(script_name)] = script_name
        struct.pack_into(">I", s4, MOG_CREATURE_NODE_SCRIPT_OFFSET + idx * 4, str_cursor)
        str_cursor += len(script_name)

    for idx in range(MOG_ACTION_COUNT):
        struct.pack_into(">H", s4, MOG_ACTION_RIGHT_OFFSET + idx * 2, idx * 4)
        struct.pack_into(">H", s4, MOG_ACTION_LEFT_OFFSET + idx * 2, idx * 4 + 2)

    for idx in range(MOG_ENCOUNTER_BAND_COUNT):
        struct.pack_into(">HH", s4, MOG_ENCOUNTER_BANDS_OFFSET + idx * 4, (idx + 1) * 25, idx + 1)
    return bytes(s4)


def _build_synthetic_s44() -> bytes:
    s44 = bytearray(MOG_SYNTH_SEQ_TABLE_OFFSET + MOG_SYNTH_SEQ_COUNT * 4)
    s44[MOG_SYNTH_BLOB_BASE : MOG_SYNTH_BLOB_BASE + 2] = b"\xaa\xbb"
    s44[MOG_SYNTH_RELOC_BEG:MOG_SYNTH_RELOC_END] = b"\xff" * (
        MOG_SYNTH_RELOC_END - MOG_SYNTH_RELOC_BEG
    )
    for i in range(MOG_SYNTH_VIB_COUNT):
        start = MOG_SYNTH_VIB_BASE + i * MOG_SYNTH_VIB_STRIDE
        s44[start : start + MOG_SYNTH_VIB_STRIDE] = bytes([0x10 + i] * MOG_SYNTH_VIB_STRIDE)
    for i in range(MOG_SYNTH_ENV_COUNT):
        start = MOG_SYNTH_ENV_BASE + i * MOG_SYNTH_ENV_STRIDE
        s44[start : start + MOG_SYNTH_ENV_STRIDE] = bytes([0x20 + i] * MOG_SYNTH_ENV_STRIDE)

    seq1_ofs = 5200
    seq2_ofs = 5216
    s44[seq1_ofs : seq1_ofs + 4] = b"\x3c\x08\x80\x00"
    s44[seq2_ofs : seq2_ofs + 4] = b"\x40\x10\xac\x00"
    struct.pack_into(">I", s44, MOG_SYNTH_SEQ_TABLE_OFFSET + 1 * 4, seq1_ofs)
    struct.pack_into(">I", s44, MOG_SYNTH_SEQ_TABLE_OFFSET + 2 * 4, seq2_ofs)
    return bytes(s44)


def _build_synthetic_s45() -> bytes:
    s45 = bytearray(2016)
    s45[:MOG_SYNTH_WAVE_BYTES] = bytes((i % 127) for i in range(MOG_SYNTH_WAVE_BYTES))
    struct.pack_into(">hHHII", s45, 168, -1, 4, 32, 0x00000010, MOG_SYNTH_BLOB_BASE + 4)
    return bytes(s45)


def _build_synthetic_mog() -> DecodedHunkExecutable:
    """Create a synthetic `mog` hunk executable containing all runtime tables."""
    section_map: dict[int, tuple[int, bytes]] = {
        MOG_MAP_SITES_SECTION: (HUNK_DATA, _build_synthetic_s2()),
        MOG_TABLES_SECTION: (HUNK_DATA, _build_synthetic_s4()),
        MOG_BOLD_FONT_SECTION: (HUNK_DATA, bytes((i & 0xFF) for i in range(BUILTIN_FONT_BYTES))),
        MOG_SYNTH_TABLES_SECTION: (HUNK_CODE, _build_synthetic_s44()),
        MOG_SYNTH_WAVE_SECTION: (HUNK_DATA, _build_synthetic_s45()),
    }
    sections = [section_map.get(sec_idx, (HUNK_DATA, b"\x00\x00\x00\x00")) for sec_idx in range(46)]
    return parse_hunk_executable("mog", _build_synthetic_hunk_binary(sections))


def _build_synthetic_program() -> DecodedHunkExecutable:
    """Create a synthetic `program` hunk executable with its built-in font."""
    sections: list[tuple[int, bytes]] = []
    for sec_idx in range(34):
        if sec_idx == PROGRAM_BOLD_FONT_SECTION:
            sections.append(
                (HUNK_DATA, bytes((i ^ 0x55) & 0xFF for i in range(BUILTIN_FONT_BYTES)))
            )
        else:
            sections.append((HUNK_DATA, b"\x00\x00\x00\x00"))
    return parse_hunk_executable("program", _build_synthetic_hunk_binary(sections))


def test_parse_combat_action_tables_synthetic() -> None:
    mog = _build_synthetic_mog()
    actions = parse_combat_action_tables(mog)
    assert actions.right_offsets == tuple(idx * 4 for idx in range(MOG_ACTION_COUNT))
    assert actions.left_offsets == tuple(idx * 4 + 2 for idx in range(MOG_ACTION_COUNT))


def test_parse_knight_names_and_lair_metadata_synthetic() -> None:
    mog = _build_synthetic_mog()
    names = parse_knight_names(mog)
    assert names == ("KNIGHT_0", "KNIGHT_1", "KNIGHT_2", "KNIGHT_3")

    sites = parse_map_sites(mog)
    assert len(sites) == 9
    assert sites[0].site_id == 0x15
    assert sites[-1].site_id == 0x1D

    nodes = parse_creature_nodes(mog)
    assert len(nodes) == MOG_CREATURE_NODE_COUNT
    assert nodes[0].creature_type == 24
    assert nodes[0].defence == 10
    assert nodes[0].arena_script == "lair00.t"
    assert nodes[23].arena_script == "lair23.t"

    bands = parse_encounter_bands(mog)
    assert [(b.limit, b.kind) for b in bands] == [(25, 1), (50, 2), (75, 3), (100, 4)]


def test_parse_mog_synth_and_bundle_synthetic() -> None:
    mog = _build_synthetic_mog()
    tables = load_mog_tables(mog)

    assert len(tables.builtin_font.glyphs) == BUILTIN_FONT_GLYPHS
    assert tables.synth.blob_base == MOG_SYNTH_BLOB_BASE
    assert tables.synth.blob[:2] == b"\x00\x00"
    rel_off = MOG_SYNTH_RELOC_BEG - MOG_SYNTH_BLOB_BASE
    rel_end = MOG_SYNTH_RELOC_END - MOG_SYNTH_BLOB_BASE
    assert tables.synth.blob[rel_off:rel_end] == bytes(rel_end - rel_off)
    assert len(tables.synth.vibrato_rows) == MOG_SYNTH_VIB_COUNT
    assert len(tables.synth.envelope_rows) == MOG_SYNTH_ENV_COUNT
    assert len(tables.synth.sequences) == MOG_SYNTH_SEQ_COUNT
    assert tables.synth.sequences[0].s44_offset == 0
    assert tables.synth.sequences[0].stream == b""
    assert tables.synth.sequences[1].s44_offset == 5200
    assert tables.synth.sequences[1].stream[:4] == b"\x3c\x08\x80\x00"
    assert len(tables.synth.sequences[1].stream) == 16
    assert tables.synth.sequences[2].s44_offset == 5216
    assert tables.synth.sequences[2].stream[:4] == b"\x40\x10\xac\x00"
    assert len(tables.synth.waveforms) == MOG_SYNTH_WAVE_BYTES
    assert tables.synth.instruments[0].loop_flag == -1
    assert tables.synth.instruments[0].length_words == 32


def test_load_program_tables_synthetic() -> None:
    program = _build_synthetic_program()
    tables = load_program_tables(program)
    assert len(tables.builtin_font.glyphs) == BUILTIN_FONT_GLYPHS
    assert all(len(g) == 8 for g in tables.builtin_font.glyphs)


def test_table_decoders_reject_truncated_or_invalid_hunks() -> None:
    tiny = parse_hunk_executable(
        "tiny", _build_synthetic_hunk_binary([(HUNK_DATA, b"\x00\x00\x00\x00")])
    )
    with pytest.raises(HunkFormatError):
        parse_combat_action_tables(tiny)
    with pytest.raises(HunkFormatError):
        parse_map_sites(tiny)
    with pytest.raises(HunkFormatError):
        parse_mog_synth_sequences(tiny)

    mog = _build_synthetic_mog()
    bad_s44 = bytearray(mog.sections[MOG_SYNTH_TABLES_SECTION].data)
    struct.pack_into(">I", bad_s44, MOG_SYNTH_SEQ_TABLE_OFFSET + 4, 100)
    bad_sections = [
        (s.hunk_type, bytes(bad_s44) if s.index == MOG_SYNTH_TABLES_SECTION else s.data)
        for s in mog.sections
    ]
    bad_mog = parse_hunk_executable("bad_mog", _build_synthetic_hunk_binary(bad_sections))
    with pytest.raises(HunkFormatError):
        parse_mog_synth_sequences(bad_mog)


@pytest.mark.adf
def test_runtime_tables_from_real_disks(adf_dir: Path) -> None:
    disks = DiskSet.from_directory(adf_dir)
    program = parse_hunk_executable("program", disks.read_file("program"))
    mog = parse_hunk_executable("mog", disks.read_file("mog"))

    prog_tables = load_program_tables(program)
    mog_tables = load_mog_tables(mog)

    assert prog_tables.builtin_font.glyphs == mog_tables.builtin_font.glyphs
    assert mog_tables.knight_names == (
        "SIR_RICHARD",
        "SIR_GODBER",
        "SIR_JEFFREY",
        "SIR_EDWARD",
    )
    assert len(mog_tables.combat_actions.right_offsets) == MOG_ACTION_COUNT
    assert len(mog_tables.combat_actions.left_offsets) == MOG_ACTION_COUNT
    assert len(mog_tables.lair_region.map_sites) == 9
    assert len(mog_tables.lair_region.creature_nodes) == MOG_CREATURE_NODE_COUNT
    assert mog_tables.lair_region.creature_nodes[0].arena_script == "fol1.t"
    assert mog_tables.lair_region.creature_nodes[23].arena_script == "gll6.t"
    assert [(b.limit, b.kind) for b in mog_tables.lair_region.encounter_bands] == [
        (50, 1),
        (70, 2),
        (90, 3),
        (100, 4),
    ]
    assert len(mog_tables.synth.sequences) == MOG_SYNTH_SEQ_COUNT
    assert mog_tables.synth.sequences[0].s44_offset == 0
    assert all(seq.s44_offset > 0 and len(seq.stream) > 0 for seq in mog_tables.synth.sequences[1:])
    assert len(mog_tables.synth.instruments) == 131
    assert mog_tables.knight_scripts is not None
    assert len(mog_tables.knight_scripts.action_scripts) == 9
    assert len(mog_tables.knight_scripts.hurt_scripts) == 9
    assert len(mog_tables.knight_scripts.walk_scripts) == 3
    assert len(mog_tables.monster_scripts) == 11


def test_parse_knight_and_monster_script_tables_synthetic() -> None:
    from pymoonstone.assets.hunk import HUNK_BSS, HunkSection

    # Build a synthetic S_0 with LEA S_1+100, A0; MOVE.L #S_4+400, 4(A0); RTS
    # followed by Knight init (22(A1)=S_4+400, 34(A1)=S_1+100) + RTS
    # and Monster init (type 0x20, 22(A1)=S_4+420, 30(A1)=S_1+200) + RTS.
    s0_buf = bytearray()
    r1_offs: list[int] = []
    r4_offs: list[int] = []

    # LEA S_1+100, A0
    s0_buf += struct.pack(">H", 0x41F9)
    r1_offs.append(len(s0_buf))
    s0_buf += struct.pack(">I", 100)
    # MOVE.L #S_4+400, 4(A0)
    s0_buf += struct.pack(">H", 0x217C)
    r4_offs.append(len(s0_buf))
    s0_buf += struct.pack(">Ih", 400, 4)
    # LEA S_1+200, A0
    s0_buf += struct.pack(">H", 0x41F9)
    r1_offs.append(len(s0_buf))
    s0_buf += struct.pack(">I", 200)
    # MOVE.L #S_4+420, (A0)+
    s0_buf += struct.pack(">H", 0x20FC)
    r4_offs.append(len(s0_buf))
    s0_buf += struct.pack(">I", 420)
    s0_buf += struct.pack(">H", 0x4E75)  # RTS

    # Knight init block
    s0_buf += struct.pack(">H", 0x237C)
    r4_offs.append(len(s0_buf))
    s0_buf += struct.pack(">Ih", 400, 22)
    s0_buf += struct.pack(">H", 0x237C)
    r1_offs.append(len(s0_buf))
    s0_buf += struct.pack(">Ih", 100, 34)
    s0_buf += struct.pack(">H", 0x4E75)

    # Monster init block (type 0x20)
    s0_buf += struct.pack(">HHh", 0x137C, 0x20, 77)
    s0_buf += struct.pack(">H", 0x237C)
    r4_offs.append(len(s0_buf))
    s0_buf += struct.pack(">Ih", 420, 22)
    s0_buf += struct.pack(">H", 0x237C)
    r1_offs.append(len(s0_buf))
    s0_buf += struct.pack(">Ih", 200, 30)
    s0_buf += struct.pack(">H", 0x4E75)

    syn_exec = DecodedHunkExecutable(
        name="syn_mog",
        sections=(
            HunkSection(0, HUNK_CODE, len(s0_buf), bytes(s0_buf), {1: tuple(r1_offs), 4: tuple(r4_offs)}),
            HunkSection(1, HUNK_BSS, 512, b"\x00" * 512, {}),
            HunkSection(2, HUNK_DATA, 4, b"\x00" * 4, {}),
            HunkSection(3, HUNK_DATA, 4, b"\x00" * 4, {}),
            HunkSection(4, HUNK_DATA, 512, b"\x00" * 512, {}),
        ),
    )
    ks = parse_knight_script_tables(syn_exec)
    assert ks.idle_script == 400
    assert ks.action_scripts[1] == 400

    ms = parse_monster_script_tables(syn_exec)
    assert len(ms) == 1
    assert ms[0].creature_type == 0x20
    assert ms[0].idle_script == 420
    assert ms[0].hurt_scripts[0] == 420


@pytest.mark.adf
def test_real_knight_scripts_emit_multipart_draws_in_vm(adf_dir: Path) -> None:
    from pymoonstone.sim.combat_vm import CombatJob, DrawFrameEvent, step_frame

    disks = DiskSet.from_directory(adf_dir)
    mog = parse_hunk_executable("mog", disks.read_file("mog"))
    s4_bytes = mog.sections[MOG_TABLES_SECTION].data
    ks = parse_knight_script_tables(mog)

    cases = {
        "IDLE": ks.idle_script,
        "WALK": ks.walk_scripts[0][0],
        "THRUST": ks.action_scripts[1],
        "LOW_SWEEP": ks.action_scripts[2],
        "OVERHEAD_SLASH": ks.action_scripts[8],
    }
    for name, pc in cases.items():
        job = CombatJob(pc=pc)
        events = step_frame(job, s4_bytes)
        draws = [e for e in events if isinstance(e, DrawFrameEvent)]
        assert len(draws) >= 2, f"{name} at pc={pc} emitted {len(draws)} draws, expected >= 2"
