"""Amiga audio decoders: `.a` PCM banks, IFF 8SVX, and `.cmp` modules (ARCHITECTURE.md §6)."""

from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

from harddaysengine.assets.hunk import HUNK_HEADER, parse_hunk_executable
from harddaysengine.assets.lzss import decompress_lzss
from harddaysengine.assets.rnc import decompress_rnc1

PAL_PAULA_CLOCK = 3_546_895
DEFAULT_PAULA_PERIOD = 428  # ~8,287 Hz standard Amiga SFX rate
DEFAULT_SAMPLE_RATE = PAL_PAULA_CLOCK // DEFAULT_PAULA_PERIOD  # 8,287 Hz
MOD_HEADER_SIZE = 1084
MOD_SAMPLE_COUNT = 31


class AudioFormatError(ValueError):
    """Raised when an audio bank, 8SVX chunk, or ProTracker module is malformed."""


@dataclass(slots=True, frozen=True)
class DecodedSampleBank:
    """Decoded `.a` LZSS sound bank with signed 8-bit Paula PCM samples (ARCHITECTURE.md §6.1)."""

    name: str
    packed_size: int
    samples_pcm8: bytes

    @property
    def sample_count(self) -> int:
        """Total number of 8-bit PCM sample frames in this bank."""
        return len(self.samples_pcm8)


@dataclass(slots=True, frozen=True)
class ModSampleHeader:
    """30-byte ProTracker sample slot descriptor (ARCHITECTURE.md §6.3)."""

    index: int
    name: str
    length_bytes: int
    finetune: int
    volume: int
    repeat_start_bytes: int
    repeat_length_bytes: int


@dataclass(slots=True, frozen=True)
class DecodedModule:
    """Decompressed four-channel MOD with its on-disk signature (ARCHITECTURE.md §6.3)."""

    name: str
    title: str
    signature: str
    song_length: int
    pattern_count: int
    order_table: tuple[int, ...]
    samples: tuple[ModSampleHeader, ...]
    raw_mod: bytes


def paula_period_to_hz(period: int) -> int:
    """Convert an Amiga PAL Paula period word to integer Hz (`3_546_895 // period`)."""
    if period <= 0:
        msg = f"Paula period must be positive, got {period}"
        raise ValueError(msg)
    return PAL_PAULA_CLOCK // period


def decode_sample_bank(name: str, data: bytes | bytearray | memoryview) -> DecodedSampleBank:
    """Decode a `.a` sound sample bank (Hunk-wrapped, IFF 8SVX, or LZSS) (ARCHITECTURE.md §6.1)."""
    view = memoryview(data)
    if len(view) < 4:
        msg = f"Audio bank {name!r} too short ({len(view)} bytes < 4)"
        raise AudioFormatError(msg)
    (first_word,) = struct.unpack_from(">I", view, 0)
    if first_word == HUNK_HEADER:
        exe = parse_hunk_executable(name, view)
        pcm = b"".join(sec.data for sec in exe.sections)
        return DecodedSampleBank(name=name, packed_size=len(view), samples_pcm8=pcm)
    if bytes(view[0:4]) == b"FORM":
        pcm, _ = decode_iff_8svx(view)
        return DecodedSampleBank(name=name, packed_size=len(view), samples_pcm8=pcm)
    stream_end = min(len(view), 4 + first_word)
    pcm = decompress_lzss(view[4:stream_end])
    return DecodedSampleBank(name=name, packed_size=first_word, samples_pcm8=pcm)


def decode_iff_8svx(data: bytes | bytearray | memoryview) -> tuple[bytes, int]:
    """Decode a standard IFF `FORM....8SVX` container into `(signed_pcm8_bytes, sample_rate_hz)`."""
    view = memoryview(data)
    if len(view) < 12 or bytes(view[0:4]) != b"FORM" or bytes(view[8:12]) != b"8SVX":
        msg = "Invalid IFF 8SVX header (expected FORM....8SVX)"
        raise AudioFormatError(msg)
    pos = 12
    sample_rate = DEFAULT_SAMPLE_RATE
    body: bytes | None = None
    while pos + 8 <= len(view):
        chunk_id = bytes(view[pos : pos + 4])
        (chunk_size,) = struct.unpack_from(">I", view, pos + 4)
        pos += 8
        chunk_end = min(len(view), pos + chunk_size)
        if chunk_id == b"VHDR" and chunk_size >= 16:
            (rate_word,) = struct.unpack_from(">H", view, pos + 12)
            if rate_word > 0:
                sample_rate = rate_word
        elif chunk_id == b"BODY":
            body = bytes(view[pos:chunk_end])
        pos = chunk_end + (chunk_size & 1)
    if body is None:
        msg = "IFF 8SVX container missing BODY chunk"
        raise AudioFormatError(msg)
    return body, sample_rate


def pcm8_to_wav_bytes(pcm_signed: bytes, sample_rate: int = DEFAULT_SAMPLE_RATE) -> bytes:
    """Convert signed 8-bit Amiga PCM (-128..127) to a standard 8-bit unsigned RIFF WAVE file."""
    unsigned_arr = np.frombuffer(pcm_signed, dtype=np.uint8) ^ 0x80
    payload = unsigned_arr.tobytes()
    data_size = len(payload)
    riff_size = 36 + data_size
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        riff_size,
        b"WAVE",
        b"fmt ",
        16,  # PCM fmt chunk size
        1,  # format = 1 (PCM)
        1,  # channels = 1 (mono)
        sample_rate,
        sample_rate,  # byte rate (1 byte/sample * 1 channel)
        1,  # block align
        8,  # bits per sample
        b"data",
        data_size,
    )
    return header + payload


def _parse_mod_sample(view: memoryview, index: int) -> ModSampleHeader:
    offset = 20 + index * 30
    raw_name = bytes(view[offset : offset + 22]).split(b"\x00", 1)[0].decode("latin-1").strip()
    len_words, finetune, volume, rep_words, replen_words = struct.unpack_from(
        ">HBBHH", view, offset + 22
    )
    return ModSampleHeader(
        index=index + 1,
        name=raw_name,
        length_bytes=len_words * 2,
        finetune=finetune & 0x0F,
        volume=min(volume, 64),
        repeat_start_bytes=rep_words * 2,
        repeat_length_bytes=replen_words * 2,
    )


def parse_protracker_mod(name: str, mod_bytes: bytes) -> DecodedModule:
    """Read the 31-sample MOD header and its signature (ARCHITECTURE.md §6.3)."""
    if len(mod_bytes) < MOD_HEADER_SIZE:
        msg = f"ProTracker module {name!r} too short ({len(mod_bytes)} bytes < {MOD_HEADER_SIZE})"
        raise AudioFormatError(msg)
    view = memoryview(mod_bytes)
    title = bytes(view[0:20]).split(b"\x00", 1)[0].decode("latin-1").strip()
    samples = tuple(_parse_mod_sample(view, i) for i in range(MOD_SAMPLE_COUNT))
    song_length = int(view[950])
    order_table = tuple(int(b) for b in view[952:1080])
    signature = bytes(view[1080:1084]).decode("latin-1")
    pattern_count = (max(order_table) + 1) if order_table else 0
    return DecodedModule(
        name=name,
        title=title,
        signature=signature,
        song_length=song_length,
        pattern_count=pattern_count,
        order_table=order_table,
        samples=samples,
        raw_mod=mod_bytes,
    )


def decode_cmp_module(name: str, data: bytes | bytearray | memoryview) -> DecodedModule:
    """Decompress an RNC1 `.cmp` module (`music.cmp`, `vmusic.cmp`) and parse its header."""
    raw_mod = decompress_rnc1(data)
    return parse_protracker_mod(name, raw_mod)
