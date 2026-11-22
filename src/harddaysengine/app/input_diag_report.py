"""Motion/axis coalescing and flushed UTF-8 JSON Lines writer (ARCHITECTURE.md §13.12.5-6)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, TextIO

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from harddaysengine.app.input_diag_events import NormalizedEvent

DEFAULT_MAX_RECORDS = 10_000
DEFAULT_COALESCE_MS = 50
DEFAULT_AXIS_EPSILON = 0.02
_QUIET_AXIS_HEARTBEAT_MS = 1000

_COALESCABLE_EVENTS = frozenset(
    {
        "joy_axis_motion",
        "controller_axis_motion",
        "mouse_motion",
        "finger_motion",
        "controller_touchpad_motion",
        "controller_sensor_update",
    }
)


@dataclass(slots=True, frozen=True)
class CoalesceConfig:
    """Parameters governing high-frequency motion and axis coalescing (§13.12.5)."""

    coalesce_ms: int = DEFAULT_COALESCE_MS
    axis_epsilon: float = DEFAULT_AXIS_EPSILON
    raw: bool = False

    @property
    def effective_coalesce_ms(self) -> int:
        """Return active coalescing window in milliseconds (0 in raw mode)."""
        return 0 if self.raw else max(0, self.coalesce_ms)

    @property
    def effective_axis_epsilon(self) -> float:
        """Return active raw joystick axis deadband delta (0.0 in raw mode)."""
        return 0.0 if self.raw else max(0.0, self.axis_epsilon)

    @property
    def controller_axis_epsilon(self) -> int:
        """Return active mapped controller axis deadband delta in s16 units."""
        return round(self.effective_axis_epsilon * 32768)

    def to_dict(self) -> dict[str, object]:
        """Serialize active coalescing parameters for ``session_start``."""
        return {
            "coalesce_ms": self.effective_coalesce_ms,
            "axis_epsilon": round(self.effective_axis_epsilon, 6),
            "controller_axis_epsilon": self.controller_axis_epsilon,
            "raw": self.raw,
        }


@dataclass(slots=True)
class _PendingBurst:
    """Buffered continuous-motion burst waiting for window expiry or causal flush."""

    stream_key: tuple[object, ...]
    category: str
    event: str
    sdl_type: int
    sdl_name: str
    device: dict[str, object]
    first_elapsed_ms: int
    last_elapsed_ms: int
    sample_count: int
    first_fields: dict[str, object]
    last_fields: dict[str, object]
    min_value: float | int | None = None
    max_value: float | int | None = None
    sum_rel_x: int = 0
    sum_rel_y: int = 0


def _stream_key(norm: NormalizedEvent) -> tuple[object, ...] | None:
    ev = norm.event
    if ev not in _COALESCABLE_EVENTS:
        return None
    dev = norm.device
    fields = norm.fields
    if ev == "joy_axis_motion":
        return ("joy_axis", dev.get("instance_id"), fields.get("axis"))
    if ev == "controller_axis_motion":
        return ("controller_axis", dev.get("instance_id"), fields.get("axis"))
    if ev == "mouse_motion":
        return ("mouse_motion", dev.get("which"))
    if ev == "finger_motion":
        return ("finger_motion", fields.get("touch_id"), fields.get("finger_id"))
    if ev == "controller_touchpad_motion":
        return (
            "controller_touchpad",
            dev.get("instance_id"),
            fields.get("touchpad"),
            fields.get("finger"),
        )
    return ("controller_sensor", dev.get("instance_id"), fields.get("sensor"))


def _numeric_value(fields: Mapping[str, object]) -> float | int | None:
    raw = fields.get("value")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int | float):
        return raw
    return None


def _rel_pair(fields: Mapping[str, object]) -> tuple[int, int]:
    raw = fields.get("rel")
    if isinstance(raw, list) and len(raw) == 2:
        dx, dy = raw[0], raw[1]
        if isinstance(dx, int) and isinstance(dy, int):
            return dx, dy
    return 0, 0


def _single_event_payload(
    norm: NormalizedEvent, *, extra_fields: Mapping[str, object] | None = None
) -> dict[str, object]:
    fields = dict(norm.fields)
    if extra_fields:
        fields.update(extra_fields)
    return {
        "record_type": "event",
        "category": norm.category,
        "event": norm.event,
        "sdl_type": norm.sdl_type,
        "sdl_name": norm.sdl_name,
        "device": dict(norm.device),
        "fields": fields,
    }


def _burst_to_payload(burst: _PendingBurst) -> dict[str, object]:
    if burst.sample_count <= 1:
        return {
            "record_type": "event",
            "category": burst.category,
            "event": burst.event,
            "sdl_type": burst.sdl_type,
            "sdl_name": burst.sdl_name,
            "device": dict(burst.device),
            "fields": dict(burst.last_fields),
        }
    fields = _merge_burst_fields(burst)
    return {
        "record_type": "event",
        "category": burst.category,
        "event": burst.event,
        "sdl_type": burst.sdl_type,
        "sdl_name": burst.sdl_name,
        "device": dict(burst.device),
        "coalesced": True,
        "sample_count": burst.sample_count,
        "first_elapsed_ms": burst.first_elapsed_ms,
        "last_elapsed_ms": burst.last_elapsed_ms,
        "fields": fields,
    }


def _merge_burst_fields(burst: _PendingBurst) -> dict[str, object]:
    fields = dict(burst.last_fields)
    if burst.event in ("joy_axis_motion", "controller_axis_motion"):
        fields["first_value"] = burst.first_fields.get("value")
        fields["last_value"] = burst.last_fields.get("value")
        if burst.min_value is not None:
            fields["min_value"] = burst.min_value
        if burst.max_value is not None:
            fields["max_value"] = burst.max_value
    elif burst.event == "mouse_motion":
        if "pos" in burst.first_fields:
            fields["first_pos"] = burst.first_fields["pos"]
        if "pos" in burst.last_fields:
            fields["last_pos"] = burst.last_fields["pos"]
        fields["rel"] = [burst.sum_rel_x, burst.sum_rel_y]
    elif burst.event in ("finger_motion", "controller_touchpad_motion"):
        _copy_first_last(burst, fields, ("x", "y"))
    elif burst.event == "controller_sensor_update":
        _copy_first_last(burst, fields, ("data",))
    return fields


def _copy_first_last(
    burst: _PendingBurst, fields: dict[str, object], keys: tuple[str, ...]
) -> None:
    for key in keys:
        if key in burst.first_fields:
            fields[f"first_{key}"] = burst.first_fields[key]
        if key in burst.last_fields:
            fields[f"last_{key}"] = burst.last_fields[key]


class MotionCoalescer:
    """Coalesces high-rate motion/axis streams while preserving thresholds and causal order."""

    def __init__(self, config: CoalesceConfig | None = None) -> None:
        self._config = config if config is not None else CoalesceConfig()
        self._pending: dict[tuple[object, ...], _PendingBurst] = {}
        self._last_band: dict[tuple[object, ...], str] = {}
        self._coalesced_raw = 0

    @property
    def coalesced_raw_events(self) -> int:
        """Number of raw events folded into emitted coalesced bursts."""
        return self._coalesced_raw

    def push(self, norm: NormalizedEvent, elapsed_ms: int) -> list[dict[str, object]]:
        """Process one normalized event and return zero or more payloads ready for emission."""
        key = _stream_key(norm)
        if key is None:
            ready = self.flush_all()
            ready.append(_single_event_payload(norm))
            return ready
        crossed = self._check_axis_threshold(key, norm)
        if self._config.raw or self._config.effective_coalesce_ms == 0:
            extra = {"threshold_crossed": True} if crossed else None
            return [_single_event_payload(norm, extra_fields=extra)]
        if crossed:
            ready = self._flush_stream(key)
            ready.append(_single_event_payload(norm, extra_fields={"threshold_crossed": True}))
            return ready
        self._accumulate(key, norm, elapsed_ms)
        return self.flush_expired(elapsed_ms)

    def _check_axis_threshold(self, key: tuple[object, ...], norm: NormalizedEvent) -> bool:
        if norm.event not in ("joy_axis_motion", "controller_axis_motion"):
            return False
        raw_band = norm.fields.get("band")
        if not isinstance(raw_band, str):
            return False
        prev = self._last_band.get(key)
        self._last_band[key] = raw_band
        if prev is None:
            return raw_band != "center"
        return raw_band != prev

    def _accumulate(self, key: tuple[object, ...], norm: NormalizedEvent, elapsed_ms: int) -> None:
        val = _numeric_value(norm.fields)
        dx, dy = _rel_pair(norm.fields) if norm.event == "mouse_motion" else (0, 0)
        existing = self._pending.get(key)
        if existing is None:
            self._pending[key] = _PendingBurst(
                stream_key=key,
                category=norm.category,
                event=norm.event,
                sdl_type=norm.sdl_type,
                sdl_name=norm.sdl_name,
                device=dict(norm.device),
                first_elapsed_ms=elapsed_ms,
                last_elapsed_ms=elapsed_ms,
                sample_count=1,
                first_fields=dict(norm.fields),
                last_fields=dict(norm.fields),
                min_value=val,
                max_value=val,
                sum_rel_x=dx,
                sum_rel_y=dy,
            )
            return
        existing.last_elapsed_ms = elapsed_ms
        existing.sample_count += 1
        existing.last_fields = dict(norm.fields)
        existing.device = dict(norm.device)
        existing.sum_rel_x += dx
        existing.sum_rel_y += dy
        if val is not None:
            existing.min_value = val if existing.min_value is None else min(existing.min_value, val)
            existing.max_value = val if existing.max_value is None else max(existing.max_value, val)

    def _flush_stream(self, key: tuple[object, ...]) -> list[dict[str, object]]:
        burst = self._pending.pop(key, None)
        if burst is None:
            return []
        if burst.sample_count > 1:
            self._coalesced_raw += burst.sample_count - 1
        return [_burst_to_payload(burst)]

    def _should_expire(self, burst: _PendingBurst, now_ms: int) -> bool:
        age = now_ms - burst.first_elapsed_ms
        window = self._config.effective_coalesce_ms
        if age < window:
            return False
        if burst.event not in ("joy_axis_motion", "controller_axis_motion"):
            return True
        span = self._axis_span(burst)
        eps = (
            self._config.controller_axis_epsilon
            if burst.event == "controller_axis_motion"
            else self._config.effective_axis_epsilon
        )
        if span >= eps:
            return True
        return age >= max(window, _QUIET_AXIS_HEARTBEAT_MS)

    def _axis_span(self, burst: _PendingBurst) -> float:
        if burst.min_value is None or burst.max_value is None:
            return 0.0
        return float(burst.max_value - burst.min_value)

    def flush_expired(self, now_ms: int) -> list[dict[str, object]]:
        """Flush pending bursts whose coalescing window has elapsed."""
        ordered = sorted(
            self._pending.values(),
            key=lambda b: (b.first_elapsed_ms, str(b.stream_key)),
        )
        ready: list[dict[str, object]] = []
        for burst in ordered:
            if self._should_expire(burst, now_ms):
                ready.extend(self._flush_stream(burst.stream_key))
        return ready

    def flush_all(self) -> list[dict[str, object]]:
        """Flush every buffered burst in ``(first_elapsed_ms, stream_key)`` order."""
        if not self._pending:
            return []
        ordered = sorted(
            self._pending.values(),
            key=lambda b: (b.first_elapsed_ms, str(b.stream_key)),
        )
        ready: list[dict[str, object]] = []
        for burst in ordered:
            ready.extend(self._flush_stream(burst.stream_key))
        return ready


class JsonlReportWriter:
    """Line-flushed UTF-8 JSON Lines writer for recoverable diagnostic reports (§13.12.6)."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._fp: TextIO | None = path.open("w", encoding="utf-8", newline="\n")

    def write_record(self, record: Mapping[str, object]) -> None:
        """Serialize one record as a single UTF-8 JSON line and flush immediately."""
        if self._fp is None:
            return
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        self._fp.write(line + "\n")
        self._fp.flush()

    def close(self) -> None:
        """Flush and close the underlying report file."""
        if self._fp is not None:
            self._fp.flush()
            self._fp.close()
            self._fp = None
