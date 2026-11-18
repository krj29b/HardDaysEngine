"""Original program music constants read from its executable (ARCHITECTURE.md §6.3.1)."""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from harddaysengine.assets.fight_palette import MogVersionError

if TYPE_CHECKING:
    from harddaysengine.assets.hunk import DecodedHunkExecutable


@dataclass(slots=True, frozen=True)
class MusicData:
    """The original note periods, vibrato waveform and startup speed (§6.3.1)."""

    periods: tuple[int, ...]
    vibrato: bytes
    speed: int


def parse_music_data(executable: DecodedHunkExecutable) -> MusicData:
    """Refuse unsupported player code, then read its tables (§6.3.1)."""
    data = executable.sections[1].data
    if zlib.crc32(data) != 0x9FAA6BB6:
        raise MogVersionError("program: unsupported module player")
    periods = 0x1D4 + struct.unpack_from(">h", data, 0x1D4)[0]
    vibrato = 0x48E + struct.unpack_from(">h", data, 0x48E)[0]
    return MusicData(
        struct.unpack_from(">37H", data, periods), data[vibrato : vibrato + 32], data[0xDD]
    )
