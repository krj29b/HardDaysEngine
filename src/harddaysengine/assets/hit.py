"""Weapon hitbox (`B/collide.hit`) and cel frame hurtbox metadata parser (ARCHITECTURE.md §4.2)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from harddaysengine.assets.cel import CelFrame


class HitFormatError(ValueError):
    """Raised when a collide.hit file violates the section/frame grammar."""


@dataclass(slots=True, frozen=True)
class HitPoint:
    """Single weapon attack point in cel-local coordinates."""

    x: int
    y: int


@dataclass(slots=True, frozen=True)
class HitFrame:
    """Per-frame attack points and coarse bounding box (`max_x`, `max_y`) (ARCHITECTURE.md §4.2)."""

    frame_index: int
    hit_type: int
    max_x: int
    max_y: int
    points: tuple[HitPoint, ...]

    @property
    def count(self) -> int:
        """Number of attack points on this frame."""
        return len(self.points)


@dataclass(slots=True, frozen=True)
class HitSet:
    """Complete weapon attack-point table for a named cel file (e.g. `kn4`, `Troll1`)."""

    name: str
    norm_name: str
    frames: tuple[HitFrame, ...]


@dataclass(slots=True, frozen=True)
class HurtBox:
    """Cel frame hurtbox rectangle (`0, 0, cel_w, cel_h`) and tight non-zero pixel bounds."""

    cel_width: int
    cel_height: int
    tight_x: int
    tight_y: int
    tight_width: int
    tight_height: int
    opaque_pixels: int


def compute_cel_hurtbox(frame: CelFrame) -> HurtBox:
    """Compute the full cel hurt surface and tight opaque pixel bounds (ARCHITECTURE.md §4.2)."""
    w = frame.meta.width
    h = frame.meta.height
    if w == 0 or h == 0:
        return HurtBox(0, 0, 0, 0, 0, 0, 0)
    ys, xs = np.nonzero(frame.mask)
    opaque = int(xs.size)
    if opaque == 0:
        return HurtBox(w, h, 0, 0, 0, 0, 0)
    min_x = int(xs.min())
    max_x = int(xs.max())
    min_y = int(ys.min())
    max_y = int(ys.max())
    return HurtBox(
        cel_width=w,
        cel_height=h,
        tight_x=min_x,
        tight_y=min_y,
        tight_width=max_x - min_x + 1,
        tight_height=max_y - min_y + 1,
        opaque_pixels=opaque,
    )


class _DigitStream:
    """Character stream over a collide.hit section body matching `hitParse` (`loaders.cpp`)."""

    def __init__(self, text: str, start: int = 0) -> None:
        self._text = text
        self.pos = start
        self.length = len(text)

    def _skip_ws(self) -> None:
        while self.pos < self.length and self._text[self.pos] in " \t\r\n":
            self.pos += 1

    def read_line(self) -> str | None:
        self._skip_ws()
        if self.pos >= self.length:
            return None
        end = self._text.find("\n", self.pos)
        if end < 0:
            end = self.length
        line = self._text[self.pos : end].strip()
        self.pos = end + 1
        return line

    def read_digits(self, n: int) -> int:
        self._skip_ws()
        if self.pos + n > self.length:
            msg = f"Truncated collide.hit digit field of width {n} at offset {self.pos}"
            raise HitFormatError(msg)
        chunk = self._text[self.pos : self.pos + n]
        if not chunk.isdigit():
            msg = f"Expected {n} digits in collide.hit at offset {self.pos}, got {chunk!r}"
            raise HitFormatError(msg)
        self.pos += n
        return int(chunk)


def _parse_populated_frame(stream: _DigitStream, frame_idx: int, count: int) -> HitFrame:
    hit_type = stream.read_digits(2)
    points: list[HitPoint] = []
    max_x = 0
    max_y = 0
    for _ in range(count):
        px = stream.read_digits(3)
        py = stream.read_digits(3)
        points.append(HitPoint(x=px, y=py))
        max_x = max(max_x, px)
        max_y = max(max_y, py)
    return HitFrame(
        frame_index=frame_idx,
        hit_type=hit_type,
        max_x=max_x,
        max_y=max_y,
        points=tuple(points),
    )


def parse_collide_hit(data: bytes | str) -> dict[str, HitSet]:
    """Parse `B/collide.hit` into a dict keyed by normalized cel stem (ARCHITECTURE.md §4.2)."""
    text = data.decode("latin-1") if isinstance(data, bytes) else data
    stream = _DigitStream(text)
    sets: dict[str, HitSet] = {}

    while True:
        header = stream.read_line()
        if not header or header == "99":
            break
        frames: list[HitFrame] = []
        frame_idx = 0
        while True:
            count = stream.read_digits(2)
            if count == 99:
                break
            if count == 0:
                frames.append(
                    HitFrame(frame_index=frame_idx, hit_type=0, max_x=0, max_y=0, points=())
                )
            else:
                frames.append(_parse_populated_frame(stream, frame_idx, count))
            frame_idx += 1
        norm = Path(header.strip()).stem.lower()
        sets[norm] = HitSet(name=header, norm_name=norm, frames=tuple(frames))

    return sets


def encode_hit_frame_binary(frame: HitFrame) -> bytes:
    """Encode a HitFrame into the exact in-memory binary record produced by `hitParse`."""
    if frame.count == 0:
        return b"\x00"
    out = bytearray(
        (frame.count & 0xFF, frame.hit_type & 0xFF, frame.max_x & 0xFF, frame.max_y & 0xFF)
    )
    for pt in frame.points:
        out.append(pt.x & 0xFF)
        out.append(pt.y & 0xFF)
    return bytes(out)


def encode_hit_set_binary(hit_set: HitSet) -> bytes:
    """Encode an entire HitSet into the concatenated binary representation used by `contactTest`."""
    return b"".join(encode_hit_frame_binary(f) for f in hit_set.frames)
