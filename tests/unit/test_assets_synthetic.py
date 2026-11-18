"""Headless unit tests for Phase 3 asset decoders and synthetic ADF extraction (T3.1-T3.7)."""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

import numpy as np
import pytest

from harddaysengine.assets import (
    RNC1_MAGIC,
    ADFFormatError,
    ArenaFormatError,
    AudioFormatError,
    CelFormatError,
    DiskSet,
    HitFormatError,
    HunkFormatError,
    LZSSFormatError,
    LZSSMatch,
    OFSVolume,
    PictureFormatError,
    RNCFormatError,
    amiga_hash,
    cel_frame_to_rgba,
    cell_word_to_rgb,
    composite_arena_background,
    compress_lzss_literals,
    compute_cel_hurtbox,
    decode_arena,
    decode_cel,
    decode_iff_8svx,
    decode_palette,
    decode_palette_word,
    decode_picture,
    decode_picture_pack,
    decode_sample_bank,
    decode_stile,
    decompress_lzss,
    decompress_rnc1,
    encode_hit_frame_binary,
    encode_hit_set_binary,
    encode_lzss_tokens,
    extract_all_assets,
    flip_cel_frame,
    identify_disk_role,
    parse_collide_hit,
    parse_hunk_executable,
    parse_mog_synth_instruments,
    parse_protracker_mod,
    paula_period_to_hz,
    pcm8_to_wav_bytes,
    picture_to_rgba,
    planar_to_indexed,
)
from harddaysengine.cli.extract import main as extract_cli_main
from harddaysengine.cli.main import main as main_cli
from tests.fixtures.synthetic_adf import (
    build_synthetic_arena,
    build_synthetic_audio_bank,
    build_synthetic_cel,
    build_synthetic_hunk_executable,
    build_synthetic_mod_bytes,
    build_synthetic_ofs_image,
    build_synthetic_picture,
    build_synthetic_rnc1_mod,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_amiga_hash_case_insensitive() -> None:
    assert amiga_hash("HE1.ob") == amiga_hash("he1.OB")
    assert 0 <= amiga_hash("program") < 72
    assert 0 <= amiga_hash("collide.hit") < 72


def test_ofs_volume_and_large_file_extension_blocks() -> None:
    # 40,000 bytes requires 82 data blocks (> 72), exercising T_LIST extension block traversal
    large_payload = bytes(i % 251 for i in range(40_000))
    img = build_synthetic_ofs_image(
        {
            "program": b"prog_bytes",
            "mog": b"mog_bytes",
            "big.bin": large_payload,
            "empty.txt": b"",
            " drag.cel": b"leading_space",
        }
    )
    vol = OFSVolume(img, disk_id="A")
    assert identify_disk_role(vol) == "A"
    assert vol.has_file("PROGRAM")
    assert vol.has_file("drag.cel")
    assert vol.read_file("big.bin") == large_payload
    assert vol.read_file("empty.txt") == b""
    assert vol.read_file("drag.cel") == b"leading_space"
    with pytest.raises(FileNotFoundError):
        vol.read_file("missing.file")


def test_ofs_volume_rejects_invalid_headers() -> None:
    with pytest.raises(ADFFormatError):
        OFSVolume(b"short")
    bad_sig = bytearray(901_120)
    bad_sig[0:4] = b"BAD!"
    with pytest.raises(ADFFormatError):
        OFSVolume(bytes(bad_sig))


def test_disk_set_marker_identification_and_duplicate_resolution() -> None:
    vol_a = OFSVolume(build_synthetic_ofs_image({"program": b"p", "mog": b"m", "kn1.ob": b"hello"}))
    vol_b = OFSVolume(
        build_synthetic_ofs_image({"HE1.ob": b"h", "KN1.ob": b"full_knight_data_20k"})
    )
    vol_c = OFSVolume(build_synthetic_ofs_image({"be1.c": b"beast"}))

    ds = DiskSet([vol_a, vol_b, vol_c])
    assert set(ds.disks.keys()) == {"A", "B", "C"}
    assert ds.get_entry("kn1.ob").disk_id == "B"
    assert ds.read_file("kn1.ob") == b"full_knight_data_20k"
    with pytest.raises(FileNotFoundError):
        ds.read_file("nonexistent")


def test_lzss_literal_and_backref_edge_cases() -> None:
    raw = b"Moonstone: A Hard Days Knight (1991)"
    compressed = compress_lzss_literals(raw)
    assert decompress_lzss(compressed) == raw

    # Backref with overlapping repeat (distance=1, count=6) and zero distance (distance=0, count=4)
    stream = encode_lzss_tokens(
        [
            ord("A"),
            LZSSMatch(count=5, distance=1),
            ord("B"),
            LZSSMatch(count=4, distance=0),
        ]
    )
    assert decompress_lzss(stream) == b"AAAAAABBBBB"
    assert decompress_lzss(stream, max_output=4) == b"AAAA"

    with pytest.raises(LZSSFormatError):
        decompress_lzss(encode_lzss_tokens([LZSSMatch(count=3, distance=5)]))
    with pytest.raises(LZSSFormatError):
        decompress_lzss(b"\x80\x00")


def test_rnc1_header_and_backward_decompression() -> None:
    mod_rnc = build_synthetic_rnc1_mod()
    unpacked = decompress_rnc1(mod_rnc)
    assert len(unpacked) == 1084
    assert unpacked[1080:1084] == b"M.K."

    # Empty / zero packed_size container
    zero_rnc = struct.pack(">III", RNC1_MAGIC, 16, 0)
    assert decompress_rnc1(zero_rnc) == b"\x00" * 16

    # Match length=2 and offset=1 backward copy:
    # Bit stream (MSB-first from last byte):
    # 1. copy_literals: `10` -> 1 literal byte ('Z')
    # 2. read_length: `0` -> length = 2
    # 3. read_offset (len==2): `0` + `000001` -> offset = 1
    # Bits: `10` + `0` + `0` + `000001` = `1000_0000` (0x80), `0100_0000` (0x40)
    # Wait: first byte read is at end-1 (0x80), then literal 'Z' is read at end-2,
    # then when bit reader refills after 8 bits it reads end-3 (0x40), then read_pos == 12 stops!
    match_stream = bytes([0x00, ord("Z"), 0x81])
    rnc_match = struct.pack(">III", RNC1_MAGIC, 3, len(match_stream)) + match_stream
    assert decompress_rnc1(rnc_match) == b"ZZZ"

    with pytest.raises(RNCFormatError):
        decompress_rnc1(b"short")
    with pytest.raises(RNCFormatError):
        decompress_rnc1(struct.pack(">III", 0x12345678, 10, 0))


def test_palette_word_decoding_and_combat_assembly() -> None:
    # Bit 15 set -> mask $0FFF directly
    assert decode_palette_word(0x8ABC) == 0x0ABC
    assert cell_word_to_rgb(0x0F80) == (255, 136, 0)
    # Bit 15 clear -> shifted left by 1
    assert decode_palette_word(0x0421) == 0x0842

    pal = decode_palette([0x8000, 0x8F00, 0x0777])
    assert pal[0] == (0, 0, 0)
    assert pal[1] == (255, 0, 0)
    assert pal[2] == (238, 238, 238)


def test_planar_picture_and_pack_round_trip() -> None:
    pic_bytes_5 = build_synthetic_picture(planes=5, fill_index=12)
    pic_bytes_4 = build_synthetic_picture(planes=4, fill_index=3)
    pic5 = decode_picture(pic_bytes_5)
    assert pic5.width == 320
    assert pic5.height == 200
    assert pic5.planes == 5
    assert int(pic5.pixels[0, 5]) == 5
    assert int(pic5.pixels[10, 10]) == 12

    rgba = picture_to_rgba(pic5)
    assert rgba.shape == (200, 320, 4)

    pack = decode_picture_pack(pic_bytes_5 + pic_bytes_4)
    assert len(pack) == 2
    assert pack[0].planes == 5
    assert pack[1].planes == 4


def test_cel_decoding_and_horizontal_mirroring() -> None:
    cel_bytes = build_synthetic_cel(width=12, height=8, plane_bits=0x1F)
    cel = decode_cel(cel_bytes)
    assert cel.frame_count == 2
    f0 = cel.frames[0]
    assert f0.meta.width == 12
    assert f0.meta.height == 8
    assert f0.meta.hot_x == 2
    assert f0.meta.unflipped is True
    assert bool(f0.mask[2, 3]) is True
    assert bool(f0.mask[0, 0]) is False

    flipped = flip_cel_frame(f0)
    assert flipped.meta.unflipped is False
    assert flipped.meta.hot_x == (16 - 12)
    assert np.array_equal(flipped.pixels, f0.pixels[:, ::-1])

    unflipped_again = flip_cel_frame(flipped)
    assert unflipped_again.meta.unflipped is True

    rgba = cel_frame_to_rgba(f0)
    assert rgba.shape == (8, 12, 4)
    assert int(rgba[0, 0, 3]) == 0
    assert int(rgba[2, 3, 3]) == 255


def test_collide_hit_parser_and_hurtbox() -> None:
    sample_hit = "kn4\n00\n02 01 010 020 035 042\n99\nTroll1\n01 01 050 060\n99\n"
    sets = parse_collide_hit(sample_hit)
    assert set(sets.keys()) == {"kn4", "troll1"}
    kn4 = sets["kn4"]
    assert len(kn4.frames) == 2
    assert kn4.frames[0].count == 0
    assert encode_hit_frame_binary(kn4.frames[0]) == b"\x00"
    assert kn4.frames[1].count == 2
    assert kn4.frames[1].max_x == 35
    assert kn4.frames[1].max_y == 42
    assert encode_hit_frame_binary(kn4.frames[1]) == bytes([2, 1, 35, 42, 10, 20, 35, 42])
    assert len(encode_hit_set_binary(kn4)) == 9

    cel = decode_cel(build_synthetic_cel(width=16, height=8))
    hb = compute_cel_hurtbox(cel.frames[0])
    assert hb.cel_width == 16
    assert hb.cel_height == 8
    assert hb.tight_x == 2
    assert hb.tight_y == 1
    assert hb.tight_width == 12
    assert hb.tight_height == 6
    assert hb.opaque_pixels == 72


def test_arena_and_stile_decoding_and_compositing() -> None:
    arena_bytes = build_synthetic_arena()
    arena = decode_arena("fol1.t", arena_bytes)
    assert arena.region == 0
    assert arena.is_lair is True
    assert len(arena.obstacles) == 1
    assert arena.obstacles[0].x1 == 20
    assert arena.obstacles[0].x2 == 120
    assert len(arena.tiles) == 2

    test_pics = [
        decode_picture(build_synthetic_picture(planes=5, fill_index=i + 1)) for i in range(9)
    ]
    comp = composite_arena_background(arena, test_pics)
    assert comp.width == 320
    assert comp.height == 200
    # Forest (region 0) base backdrop is test_pics[4] (fill_index=5),
    # non-0x0300 tile at (32, 40) comes from test_pics[1] (fill_index=2),
    # 0x0300 tile at (64, 65) comes from test_pics[0] (fill_index=1).
    assert int(comp.pixels[10, 10]) == 5
    assert int(comp.pixels[40, 32]) == 2
    assert int(comp.pixels[65, 64]) == 1

    stile_bytes = struct.pack(">480H", *range(480))
    words = decode_stile(stile_bytes)
    assert len(words) == 480
    assert words[479] == 479


def test_arena_region_sheet_dispatch_and_signed_coords() -> None:
    from harddaysengine.assets.lzss import compress_lzss_literals

    test_pics = [
        decode_picture(build_synthetic_picture(planes=5, fill_index=i + 1)) for i in range(9)
    ]

    # Build a script with 0x0400 tile_index=1 at (-10, -5), 0x0300 tile_index=2 at (64, 65)
    obs = struct.pack(">H", 0)
    script = bytearray(2400)
    struct.pack_into(">Hhh", script, 0, 0x0401, -10, -5)
    struct.pack_into(">Hhh", script, 6, 0x0302, 64, 65)
    struct.pack_into(">Hhh", script, 12, 0xFF00, 0, 0)
    packed = compress_lzss_literals(obs + bytes(script))
    blob = struct.pack(">I", len(packed)) + packed

    expected_sheets = (
        # (filename, expected_region, expected_base_fill, expected_fg_fill)
        ("fo1.t", 0, 5, 1),  # test[4] backdrop, test[0] fg
        ("gll1.t", 1, 8, 1),  # test[7] backdrop, test[0] fg (no +40 offset)
        ("sw1.t", 2, 7, 3),  # test[6] backdrop, test[2] fg
        ("wal1.t", 3, 6, 4),  # test[5] backdrop, test[3] fg
    )
    for name, exp_region, exp_base, exp_fg in expected_sheets:
        arena = decode_arena(name, blob)
        assert arena.region == exp_region
        assert arena.tiles[0].x == -10
        assert arena.tiles[0].y == -5
        comp = composite_arena_background(arena, test_pics)
        # Uncovered floor pixel comes from region base backdrop
        assert int(comp.pixels[150, 150]) == exp_base
        # Clipped negative-coord 0x0401 tile blits from shared test_pics[1] (fill_index=2)
        assert int(comp.pixels[0, 0]) == 2
        # 0x0302 tile at (64, 65) blits from region fg sheet
        assert int(comp.pixels[65, 64]) == exp_fg

    with pytest.raises(ArenaFormatError):
        composite_arena_background(decode_arena("fo1.t", blob), test_pics[:7])


def test_audio_bank_8svx_wav_and_mod_decoders() -> None:
    bank_bytes = build_synthetic_audio_bank(b"\x00\x7f\x80\xff")
    bank = decode_sample_bank("kn.a", bank_bytes)
    assert bank.sample_count == 4
    wav = pcm8_to_wav_bytes(bank.samples_pcm8, sample_rate=8287)
    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"
    assert wav[-4:] == b"\x80\xff\x00\x7f"
    assert paula_period_to_hz(428) == 8287

    # Standard IFF 8SVX container
    vhdr = struct.pack(">IIIHBBBBH", 4, 0, 0, 11025, 1, 0, 0, 0, 64)
    body = b"\x10\x20\x30\x40"
    iff = (
        b"FORM"
        + struct.pack(">I", 4 + 8 + len(vhdr) + 8 + len(body))
        + b"8SVX"
        + b"VHDR"
        + struct.pack(">I", len(vhdr))
        + vhdr
        + b"BODY"
        + struct.pack(">I", len(body))
        + body
    )
    pcm, rate = decode_iff_8svx(iff)
    assert pcm == body
    assert rate == 11025

    mod = parse_protracker_mod("music.mod", build_synthetic_mod_bytes("TEST_MOD"))
    assert mod.title == "TEST_MOD"
    assert mod.signature == "M.K."
    assert mod.song_length == 1
    assert mod.samples[0].name == "lead"
    assert mod.samples[0].length_bytes == 64


def test_hunk_executable_parser() -> None:
    mog_bytes = build_synthetic_hunk_executable(with_mog_s45=True)
    exe = parse_hunk_executable("mog", mog_bytes)
    assert len(exe.sections) == 46
    assert exe.sections[0].type_name == "CODE"
    assert exe.sections[0].relocs == {1: (4,)}
    insts = parse_mog_synth_instruments(exe)
    assert len(insts) == 131
    assert insts[10].sample_offset == 10 * 128


def test_deterministic_extraction_and_cli(tmp_path: Path) -> None:
    disks_dir = tmp_path / "disks"
    disks_dir.mkdir()
    test_pack = b"".join(build_synthetic_picture(planes=5, fill_index=i + 1) for i in range(9))

    (disks_dir / "Disk1.adf").write_bytes(
        build_synthetic_ofs_image(
            {
                "program": build_synthetic_hunk_executable(with_mog_s45=False),
                "mog": build_synthetic_hunk_executable(with_mog_s45=True),
                "bg0.piv": build_synthetic_picture(planes=5, fill_index=4),
                "music.cmp": build_synthetic_rnc1_mod(),
                "kn1.ob": b"hello",
            }
        )
    )
    (disks_dir / "Disk2.adf").write_bytes(
        build_synthetic_ofs_image(
            {
                "HE1.ob": build_synthetic_cel(),
                "KN1.ob": build_synthetic_cel(width=16, height=16),
                "kn4.ob": build_synthetic_cel(width=16, height=16),
                "collide.hit": b"kn4\n00\n01 01 008 008\n99\n",
                "test": test_pack,
                "fol1.t": build_synthetic_arena(),
                "kn.a": build_synthetic_audio_bank(),
            }
        )
    )
    (disks_dir / "Disk3.adf").write_bytes(
        build_synthetic_ofs_image(
            {
                "be1.c": build_synthetic_cel(),
            }
        )
    )

    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    m1 = extract_all_assets(disks_dir, out1)
    m2 = extract_all_assets(disks_dir, out2)
    assert m1 == m2

    # Bit-for-bit identical deterministic manifest.json
    json1 = (out1 / "manifest.json").read_bytes()
    json2 = (out2 / "manifest.json").read_bytes()
    assert json1 == json2
    assert len(m1.disks) == 3
    assert len(m1.pictures) == 10  # 9 from test + bg0.piv
    assert len(m1.cels) == 4  # be1.c, he1.ob, kn1.ob (from B!), kn4.ob
    assert len(m1.arenas) == 1
    assert len(m1.audio_banks) == 1
    assert len(m1.modules) == 1
    assert len(m1.executables) == 2

    # Verify CLI entry points
    out3 = tmp_path / "out3"
    rc = extract_cli_main(["--adf-dir", str(disks_dir), "--output-dir", str(out3)])
    assert rc == 0
    assert (out3 / "manifest.json").read_bytes() == json1

    out4 = tmp_path / "out4"
    rc_main = main_cli(["--adf-dir", str(disks_dir), "--extract-assets", "--output-dir", str(out4)])
    assert rc_main == 0
    assert (out4 / "manifest.json").read_bytes() == json1


def test_decoder_error_branches() -> None:
    with pytest.raises(PictureFormatError):
        planar_to_indexed(b"\x00", 320, 200, planes=5)
    with pytest.raises(PictureFormatError):
        decode_picture(b"\x00\x03\x00\x00\x00\x00")
    with pytest.raises(CelFormatError):
        decode_cel(b"short")
    with pytest.raises(HitFormatError):
        parse_collide_hit("kn4\n02 01 010\n99\n")
    with pytest.raises(ArenaFormatError):
        decode_arena("bad.t", b"\x00")
    with pytest.raises(ArenaFormatError):
        decode_stile(b"short")
    with pytest.raises(AudioFormatError):
        decode_sample_bank("bad.a", b"\x00")
    with pytest.raises(AudioFormatError):
        decode_iff_8svx(b"NOT_IFF")
    with pytest.raises(AudioFormatError):
        parse_protracker_mod("short.mod", b"short")
    with pytest.raises(HunkFormatError):
        parse_hunk_executable("bad", b"not_a_hunk")
