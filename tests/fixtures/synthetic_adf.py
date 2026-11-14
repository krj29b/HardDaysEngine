"""Synthetic OFS disk image and Moonstone binary asset builders (zero copyrighted data)."""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

import numpy as np

from pymoonstone.assets.adf import (
    ADF_BLOCK_SIZE,
    ADF_IMAGE_SIZE,
    ADF_ROOT_BLOCK,
    OFS_DATA_PAYLOAD_SIZE,
    OFS_HASH_BUCKETS,
    ST_FILE,
    ST_ROOT,
    T_DATA,
    T_HEADER,
    T_LIST,
    amiga_hash,
)
from pymoonstone.assets.hunk import HUNK_CODE, HUNK_DATA, HUNK_END, HUNK_HEADER, HUNK_RELOC32
from pymoonstone.assets.lzss import compress_lzss_literals
from pymoonstone.assets.picture import PICTURE_HEIGHT, PICTURE_WIDTH, indexed_to_planar
from pymoonstone.assets.rnc import RNC1_MAGIC

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


def _write_bcpl(buf: bytearray, offset: int, text: str) -> None:
    raw = text.encode("latin-1")[:30]
    buf[offset] = len(raw)
    buf[offset + 1 : offset + 1 + len(raw)] = raw


def _write_file_into_ofs(
    img: bytearray,
    next_free_block: int,
    name: str,
    payload: bytes,
    bucket_heads: dict[int, int],
) -> int:
    hdr_block = next_free_block
    next_free_block += 1

    data_blocks: list[int] = []
    pos = 0
    seq = 1
    while pos < len(payload):
        chunk = payload[pos : pos + OFS_DATA_PAYLOAD_SIZE]
        pos += len(chunk)
        blk = next_free_block
        next_free_block += 1
        data_blocks.append(blk)
        next_data = next_free_block if pos < len(payload) else 0
        b_ofs = blk * ADF_BLOCK_SIZE
        struct.pack_into(">IIIIII", img, b_ofs, T_DATA, hdr_block, seq, len(chunk), next_data, 0)
        img[b_ofs + 0x018 : b_ofs + 0x018 + len(chunk)] = chunk
        seq += 1

    # Split data_blocks across header + optional extension blocks (72 pointers max per block)
    groups = [
        data_blocks[i : i + OFS_HASH_BUCKETS]
        for i in range(0, max(1, len(data_blocks)), OFS_HASH_BUCKETS)
    ]
    ext_blocks = [hdr_block]
    for _ in range(len(groups) - 1):
        ext_blocks.append(next_free_block)
        next_free_block += 1

    for g_idx, grp in enumerate(groups):
        cur_blk = ext_blocks[g_idx]
        nxt_ext = ext_blocks[g_idx + 1] if g_idx + 1 < len(ext_blocks) else 0
        b_ofs = cur_blk * ADF_BLOCK_SIZE
        primary = T_HEADER if g_idx == 0 else T_LIST
        first_data = grp[0] if grp else 0
        struct.pack_into(">IIIII", img, b_ofs, primary, cur_blk, len(grp), 0, first_data)
        for slot_idx, d_blk in enumerate(grp):
            struct.pack_into(">I", img, b_ofs + 0x134 - slot_idx * 4, d_blk)
        if g_idx == 0:
            struct.pack_into(">I", img, b_ofs + 0x144, len(payload))
            _write_bcpl(img, b_ofs + 0x1B0, name)
            bucket = amiga_hash(name)
            prev_head = bucket_heads.get(bucket, 0)
            struct.pack_into(">I", img, b_ofs + 0x1F0, prev_head)
            bucket_heads[bucket] = hdr_block
        struct.pack_into(">III", img, b_ofs + 0x1F4, ADF_ROOT_BLOCK, nxt_ext, ST_FILE)

    return next_free_block


def build_synthetic_ofs_image(
    files: Mapping[str, bytes],
    *,
    volume_name: str = " ",
) -> bytes:
    """Build a valid 901,120-byte Amiga OFS (`DOS\\0`) disk image containing `files`."""
    img = bytearray(ADF_IMAGE_SIZE)
    img[0:4] = b"DOS\x00"

    root_ofs = ADF_ROOT_BLOCK * ADF_BLOCK_SIZE
    struct.pack_into(">I", img, root_ofs + 0x000, T_HEADER)
    _write_bcpl(img, root_ofs + 0x1B0, volume_name)
    struct.pack_into(">I", img, root_ofs + 0x1FC, ST_ROOT)

    bucket_heads: dict[int, int] = {}
    next_block = 2
    for name, payload in files.items():
        if next_block >= ADF_ROOT_BLOCK - 5:
            next_block = ADF_ROOT_BLOCK + 2
        next_block = _write_file_into_ofs(img, next_block, name, payload, bucket_heads)

    for bucket, head_blk in bucket_heads.items():
        struct.pack_into(">I", img, root_ofs + 0x018 + bucket * 4, head_blk)

    return bytes(img)


def build_synthetic_picture(
    planes: int = 5,
    fill_index: int = 7,
    raw_palette: Sequence[int] | None = None,
) -> bytes:
    """Build a valid LZSS-compressed 320x200 Amiga picture binary."""
    pal_count = 16 if planes == 4 else 32
    if raw_palette is None:
        pal_words = tuple(0x8000 | ((i * 0x111) & 0x0FFF) for i in range(pal_count))
    else:
        pal_words = tuple(raw_palette)
    pixels = np.full((PICTURE_HEIGHT, PICTURE_WIDTH), fill_index, dtype=np.uint8)
    pixels[0, :16] = np.arange(16, dtype=np.uint8)
    planar = indexed_to_planar(pixels, planes=planes)
    packed = compress_lzss_literals(planar)
    header = struct.pack(f">HI{pal_count}H", planes, len(packed), *pal_words)
    return header + packed


def build_synthetic_cel(width: int = 16, height: int = 8, plane_bits: int = 0x1F) -> bytes:
    """Build a valid 2-frame LZSS-compressed cel container."""
    px0 = np.zeros((height, width), dtype=np.uint8)
    px0[1 : height - 1, 2 : width - 2] = 5
    f0_bytes = indexed_to_planar(px0, planes=5, plane_bits=plane_bits)

    px1 = np.zeros((height, width), dtype=np.uint8)
    px1[:, :] = 15
    f1_bytes = indexed_to_planar(px1, planes=5, plane_bits=plane_bits)

    raw_planes = f0_bytes + f1_bytes
    packed = compress_lzss_literals(raw_planes)
    unpacked_bits = len(raw_planes) * 8

    header = struct.pack(">HII", 2, len(packed), unpacked_bits)
    entry0 = struct.pack(">IHHBB", 0, width, height, 0x21, plane_bits)
    entry1 = struct.pack(">IHHBB", len(f0_bytes), width, height, 0x01, plane_bits)
    return header + entry0 + entry1 + packed


def build_synthetic_arena() -> bytes:
    """Build a valid LZSS-compressed `.t` arena layout blob."""
    obs = struct.pack(">HHHHH", 1, 20, 120, 80, 0)
    script = bytearray(2400)
    struct.pack_into(">HHH", script, 0, 0x0001, 32, 40)
    struct.pack_into(">HHH", script, 6, 0xFE00, 0, 0)
    struct.pack_into(">HHH", script, 12, 0x0302, 64, 65)
    struct.pack_into(">HHH", script, 18, 0xFF00, 0, 0)
    unpacked = obs + bytes(script)
    packed = compress_lzss_literals(unpacked)
    return struct.pack(">I", len(packed)) + packed


def build_synthetic_audio_bank(samples: bytes = b"\x00\x40\x7f\x80\xc0\xff") -> bytes:
    """Build a valid LZSS-compressed `.a` signed 8-bit PCM sample bank."""
    packed = compress_lzss_literals(samples)
    return struct.pack(">I", len(packed)) + packed


def build_synthetic_mod_bytes(title: str = "MOONSTONE_SYNTH") -> bytes:
    """Build a valid 1,084-byte header + 1-pattern ProTracker (`M.K.`) module."""
    buf = bytearray(1084 + 1024 + 64)
    raw_title = title.encode("latin-1")[:20]
    buf[0 : len(raw_title)] = raw_title
    # Sample 1: 32 words (64 bytes), finetune 0, volume 64, repeat 0, replen 1
    sample1_ofs = 20
    buf[sample1_ofs : sample1_ofs + 4] = b"lead"
    struct.pack_into(">HBBHH", buf, sample1_ofs + 22, 32, 0, 64, 0, 1)
    buf[950] = 1  # song_length = 1
    buf[952] = 0  # order[0] = pattern 0
    buf[1080:1084] = b"M.K."
    return bytes(buf)


def build_synthetic_rnc1_mod() -> bytes:
    """Build a valid RNC1-packed 1,084-byte ProTracker module with 'M.K.' at +1080..+1083."""
    stream = b"M.K.\xe8"
    return struct.pack(">III", RNC1_MAGIC, 1084, len(stream)) + stream


def build_synthetic_hunk_executable(*, with_mog_s45: bool = False) -> bytes:
    """Build a valid AmigaDOS Hunk executable (optionally with 46 sections for `mog` S_45)."""
    num_secs = 46 if with_mog_s45 else 2
    out = bytearray()
    out.extend(struct.pack(">IIIII", HUNK_HEADER, 0, num_secs, 0, num_secs - 1))
    for idx in range(num_secs):
        longs = 501 if (with_mog_s45 and idx == 45) else 2
        out.extend(struct.pack(">I", longs))

    for idx in range(num_secs):
        if idx == 0:
            out.extend(struct.pack(">III", HUNK_CODE, 2, 0x4E714E75))
            out.extend(struct.pack(">I", 0x00000000))
            out.extend(struct.pack(">IIII", HUNK_RELOC32, 1, 1, 4))
            out.extend(struct.pack(">I", 0))
            out.extend(struct.pack(">I", HUNK_END))
        elif with_mog_s45 and idx == 45:
            s45_bytes = bytearray(2004)
            for inst in range(131):
                struct.pack_into(
                    ">hHHII",
                    s45_bytes,
                    168 + inst * 14,
                    -1,
                    0,
                    64,
                    inst * 128,
                    0x100,
                )
            out.extend(struct.pack(">II", HUNK_DATA, len(s45_bytes) // 4))
            out.extend(s45_bytes)
            out.extend(struct.pack(">I", HUNK_END))
        else:
            out.extend(struct.pack(">III", HUNK_DATA, 1, 0x12345678))
            out.extend(struct.pack(">I", HUNK_END))

    return bytes(out)
