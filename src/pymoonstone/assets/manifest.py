"""Deterministic Pydantic v2 manifest schema for extracted Moonstone assets (T3.6)."""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict


class DiskManifestEntry(BaseModel):
    """Source ADF floppy image summary."""

    model_config = ConfigDict(frozen=True)

    disk_id: str
    filename: str
    size_bytes: int
    sha256: str
    file_count: int


class PictureManifestEntry(BaseModel):
    """Extracted full-screen picture or `test` sub-picture."""

    model_config = ConfigDict(frozen=True)

    name: str
    source_disk: str
    source_sha256: str
    width: int
    height: int
    planes: int
    palette_hex: list[str]
    output_png: str
    output_sha256: str


class HitPointEntry(BaseModel):
    """Single weapon attack point `(x, y)` in cel-local coordinates."""

    model_config = ConfigDict(frozen=True)

    x: int
    y: int


class CelFrameManifestEntry(BaseModel):
    """Metadata, hurtbox bounds, and optional weapon hit points for a single cel frame."""

    model_config = ConfigDict(frozen=True)

    index: int
    width: int
    height: int
    hot_x: int
    unflipped: bool
    plane_bits: int
    opaque_pixels: int
    hurt_tight_box: tuple[int, int, int, int]
    hit_type: int
    hit_max_box: tuple[int, int]
    hit_points: list[HitPointEntry]
    output_png: str | None = None
    output_sha256: str | None = None


class CelManifestEntry(BaseModel):
    """Extracted cel container (`.cel`, `.ob`, `.c`, `.f`, `.font`)."""

    model_config = ConfigDict(frozen=True)

    name: str
    norm_name: str
    source_disk: str
    source_sha256: str
    frame_count: int
    unpacked_bytes: int
    has_hit_set: bool
    frames: list[CelFrameManifestEntry]


class ArenaManifestEntry(BaseModel):
    """Extracted `.t` arena layout and composited background."""

    model_config = ConfigDict(frozen=True)

    name: str
    region: int
    is_lair: bool
    source_disk: str
    source_sha256: str
    obstacle_count: int
    tile_op_count: int
    output_png: str | None = None
    output_sha256: str | None = None


class AudioBankManifestEntry(BaseModel):
    """Extracted `.a` 8-bit PCM sound bank exported as WAV."""

    model_config = ConfigDict(frozen=True)

    name: str
    source_disk: str
    source_sha256: str
    sample_bytes: int
    sample_rate_hz: int
    output_wav: str
    output_sha256: str


class ModuleManifestEntry(BaseModel):
    """Extracted `.cmp` RNC1 ProTracker music module."""

    model_config = ConfigDict(frozen=True)

    name: str
    source_disk: str
    source_sha256: str
    title: str
    signature: str
    song_length: int
    pattern_count: int
    unpacked_bytes: int
    output_mod: str
    output_sha256: str


class ExecutableManifestEntry(BaseModel):
    """Parsed AmigaDOS Hunk executable summary (`program`, `mog`, `nb`, `Crystal`)."""

    model_config = ConfigDict(frozen=True)

    name: str
    source_disk: str
    source_sha256: str
    size_bytes: int
    hunk_count: int
    synth_instrument_count: int


class AssetManifest(BaseModel):
    """Root deterministic asset extraction manifest."""

    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    disks: list[DiskManifestEntry]
    pictures: list[PictureManifestEntry]
    cels: list[CelManifestEntry]
    arenas: list[ArenaManifestEntry]
    audio_banks: list[AudioBankManifestEntry]
    modules: list[ModuleManifestEntry]
    executables: list[ExecutableManifestEntry]

    def to_deterministic_json(self) -> str:
        """Serialize the manifest with sorted keys and 2-space indentation."""
        payload = self.model_dump(mode="json")
        return json.dumps(payload, indent=2, sort_keys=True) + "\n"
