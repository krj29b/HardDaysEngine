"""Amiga OFS floppy image reader and three-disk identifier (ARCHITECTURE.md §1)."""

from __future__ import annotations

import struct
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

ADF_BLOCK_SIZE = 512
ADF_TOTAL_BLOCKS = 1760
ADF_IMAGE_SIZE = ADF_BLOCK_SIZE * ADF_TOTAL_BLOCKS  # 901,120 bytes
ADF_ROOT_BLOCK = 880
OFS_DATA_PAYLOAD_SIZE = 488
OFS_HASH_BUCKETS = 72

T_HEADER = 2
T_DATA = 8
T_LIST = 16
ST_ROOT = 1
ST_USERDIR = 2
ST_FILE = 0xFFFFFFFD  # -3 in 32-bit two's complement


class ADFFormatError(ValueError):
    """Raised when an ADF image or OFS block violates the specification."""


@dataclass(slots=True, frozen=True)
class OFSFileEntry:
    """Metadata for a single file located on an Amiga OFS volume."""

    name: str
    norm_name: str
    size: int
    header_block: int
    disk_id: str
    disk_path: Path | None = None


def amiga_hash(name: str) -> int:
    """Compute the case-insensitive AmigaDOS 72-bucket directory hash (ARCHITECTURE.md §1.1)."""
    h = len(name)
    for ch in name:
        code = ord(ch)
        if 97 <= code <= 122:
            code -= 32
        h = (h * 13 + code) & 0x7FF
    return h % OFS_HASH_BUCKETS


def normalize_amiga_name(name: str) -> str:
    """Normalize an Amiga filename (strip leading/trailing spaces and lowercase)."""
    return name.strip().lower()


def _read_u32(buf: memoryview, offset: int) -> int:
    val: int = struct.unpack_from(">I", buf, offset)[0]
    return val


def _read_bcpl_name(block: memoryview, offset: int = 0x1B0) -> str:
    length = block[offset]
    if length > 30:
        msg = f"Invalid BCPL name length {length} at offset {offset:#x}"
        raise ADFFormatError(msg)
    return bytes(block[offset + 1 : offset + 1 + length]).decode("latin-1")


class OFSVolume:
    """Reader for a standard 880 KiB Amiga OFS (DOS\\0) disk image (ARCHITECTURE.md §1.1)."""

    def __init__(
        self,
        data: bytes,
        *,
        disk_id: str = "?",
        source_path: Path | None = None,
    ) -> None:
        if len(data) != ADF_IMAGE_SIZE:
            msg = f"Expected {ADF_IMAGE_SIZE}-byte ADF image, got {len(data)} bytes"
            raise ADFFormatError(msg)
        if data[:4] != b"DOS\x00":
            msg = f"Unsupported Amiga DOS signature {data[:4]!r} (expected b'DOS\\x00')"
            raise ADFFormatError(msg)
        self._data = data
        self._view = memoryview(data)
        self.disk_id = disk_id
        self.source_path = source_path

        root = self._block_view(ADF_ROOT_BLOCK)
        if _read_u32(root, 0x000) != T_HEADER or _read_u32(root, 0x1FC) != ST_ROOT:
            msg = "Invalid OFS root block header at block 880"
            raise ADFFormatError(msg)
        self.volume_name = _read_bcpl_name(root)
        self._entries: dict[str, OFSFileEntry] = {}
        self._scan_directory(ADF_ROOT_BLOCK, prefix="")
        if self.disk_id == "?":
            detected = identify_disk_role(self)
            if detected is not None:
                self._set_disk_id(detected)

    def _set_disk_id(self, role: str) -> None:
        self.disk_id = role
        self._entries = {k: replace(v, disk_id=role) for k, v in self._entries.items()}

    @classmethod
    def from_file(cls, path: Path, *, disk_id: str = "?") -> OFSVolume:
        """Load an OFS volume from a file on disk."""
        return cls(path.read_bytes(), disk_id=disk_id, source_path=path)

    def _block_view(self, block_num: int) -> memoryview:
        if not (0 <= block_num < ADF_TOTAL_BLOCKS):
            msg = f"Block number {block_num} out of range (0..{ADF_TOTAL_BLOCKS - 1})"
            raise ADFFormatError(msg)
        start = block_num * ADF_BLOCK_SIZE
        return self._view[start : start + ADF_BLOCK_SIZE]

    def _scan_directory(self, dir_block_num: int, prefix: str) -> None:
        dir_block = self._block_view(dir_block_num)
        for bucket in range(OFS_HASH_BUCKETS):
            chain_block = _read_u32(dir_block, 0x018 + bucket * 4)
            self._walk_hash_chain(chain_block, prefix)

    def _walk_hash_chain(self, block_num: int, prefix: str) -> None:
        visited: set[int] = set()
        current = block_num
        while current != 0:
            if current in visited:
                msg = f"Cycle detected in OFS hash chain at block {current}"
                raise ADFFormatError(msg)
            visited.add(current)
            block = self._block_view(current)
            if _read_u32(block, 0x000) != T_HEADER:
                msg = f"Expected T_HEADER (2) at block {current}"
                raise ADFFormatError(msg)
            sec_type = _read_u32(block, 0x1FC)
            raw_name = _read_bcpl_name(block)
            full_name = f"{prefix}{raw_name}" if prefix else raw_name
            if sec_type == ST_FILE:
                size = _read_u32(block, 0x144)
                norm = normalize_amiga_name(full_name)
                self._entries[norm] = OFSFileEntry(
                    name=full_name,
                    norm_name=norm,
                    size=size,
                    header_block=current,
                    disk_id=self.disk_id,
                    disk_path=self.source_path,
                )
            elif sec_type == ST_USERDIR:
                self._scan_directory(current, prefix=f"{full_name}/")
            current = _read_u32(block, 0x1F0)

    def list_entries(self) -> list[OFSFileEntry]:
        """Return all file entries on this volume sorted by normalized name."""
        return [self._entries[k] for k in sorted(self._entries)]

    def has_file(self, name: str) -> bool:
        """Check whether a file exists on this volume (case- and space-insensitive)."""
        return normalize_amiga_name(name) in self._entries

    def get_entry(self, name: str) -> OFSFileEntry:
        """Look up a file entry by name."""
        norm = normalize_amiga_name(name)
        if norm not in self._entries:
            msg = f"File {name!r} not found on disk {self.disk_id}"
            raise FileNotFoundError(msg)
        return self._entries[norm]

    def read_file(self, name: str) -> bytes:
        """Read the full byte payload of a file on this volume."""
        entry = self.get_entry(name)
        return self.read_entry(entry)

    def read_entry(self, entry: OFSFileEntry) -> bytes:
        """Extract the payload bytes for a given OFSFileEntry."""
        if entry.size == 0:
            return b""
        out = bytearray()
        remaining = entry.size
        hdr_block_num = entry.header_block
        visited_headers: set[int] = set()
        while hdr_block_num != 0 and remaining > 0:
            if hdr_block_num in visited_headers:
                msg = f"Cycle in extension blocks for {entry.name!r}"
                raise ADFFormatError(msg)
            visited_headers.add(hdr_block_num)
            hdr = self._block_view(hdr_block_num)
            high_seq = min(_read_u32(hdr, 0x008), OFS_HASH_BUCKETS)
            for idx in range(high_seq):
                if remaining <= 0:
                    break
                ptr_offset = 0x134 - idx * 4
                data_block_num = _read_u32(hdr, ptr_offset)
                chunk = self._read_data_block(data_block_num, remaining)
                out.extend(chunk)
                remaining -= len(chunk)
            hdr_block_num = _read_u32(hdr, 0x1F8)
        if remaining != 0:
            msg = f"Truncated file {entry.name!r}: missing {remaining} bytes"
            raise ADFFormatError(msg)
        return bytes(out)

    def _read_data_block(self, block_num: int, remaining: int) -> memoryview:
        block = self._block_view(block_num)
        if _read_u32(block, 0x000) != T_DATA:
            msg = f"Expected T_DATA (8) at block {block_num}"
            raise ADFFormatError(msg)
        data_size = _read_u32(block, 0x00C)
        take = min(remaining, data_size, OFS_DATA_PAYLOAD_SIZE)
        return block[0x018 : 0x018 + take]


def identify_disk_role(volume: OFSVolume) -> str | None:
    """Identify whether an OFS volume is Moonstone Disk 'A', 'B', or 'C' (ARCHITECTURE.md §1.2)."""
    if volume.has_file("program") and volume.has_file("mog"):
        return "A"
    if volume.has_file("be1.c"):
        return "C"
    if volume.has_file("HE1.ob"):
        return "B"
    return None


class DiskSet:
    """Combined multi-disk catalog across Moonstone Disks A, B, and C (ARCHITECTURE.md §1.2)."""

    def __init__(self, volumes: Iterable[OFSVolume]) -> None:
        self.disks: dict[str, OFSVolume] = {}
        self._catalog: dict[str, tuple[OFSVolume, OFSFileEntry]] = {}
        for vol in volumes:
            role = identify_disk_role(vol) or vol.disk_id
            vol._set_disk_id(role)
            self.disks[role] = vol
            for entry in vol.list_entries():
                existing = self._catalog.get(entry.norm_name)
                if existing is None or entry.size > existing[1].size:
                    self._catalog[entry.norm_name] = (vol, entry)

    @classmethod
    def from_directory(cls, adf_dir: Path) -> DiskSet:
        """Scan a directory of .adf images and build a combined DiskSet."""
        adf_paths = sorted(
            p for p in adf_dir.iterdir() if p.is_file() and p.suffix.lower() == ".adf"
        )
        volumes = [OFSVolume.from_file(p) for p in adf_paths]
        return cls(volumes)

    def list_entries(self) -> list[OFSFileEntry]:
        """Return all deduplicated file entries sorted by normalized name."""
        return [self._catalog[k][1] for k in sorted(self._catalog)]

    def has_file(self, name: str) -> bool:
        """Return True if the normalized filename exists in the combined catalog."""
        return normalize_amiga_name(name) in self._catalog

    def get_entry(self, name: str) -> OFSFileEntry:
        """Return the winning OFSFileEntry for a filename."""
        norm = normalize_amiga_name(name)
        if norm not in self._catalog:
            msg = f"File {name!r} not found in DiskSet"
            raise FileNotFoundError(msg)
        return self._catalog[norm][1]

    def read_file(self, name: str) -> bytes:
        """Read the byte contents of the winning file entry across the disk set."""
        norm = normalize_amiga_name(name)
        if norm not in self._catalog:
            msg = f"File {name!r} not found in DiskSet"
            raise FileNotFoundError(msg)
        vol, entry = self._catalog[norm]
        return vol.read_entry(entry)
