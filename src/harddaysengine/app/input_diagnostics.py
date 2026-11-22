"""Standalone SDL input diagnostic session (ARCHITECTURE.md §13.12, ADR-065)."""

from __future__ import annotations

import contextlib
import os
import platform
import sys
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import pygame

from harddaysengine import __version__
from harddaysengine.app.input_diag_events import (
    DIAGNOSTICS_SCHEMA_VERSION,
    SDL_ENV_KEYS,
    SDL_ENV_PREFIXES,
    DeviceIdentity,
    format_terminal_record,
    normalize_pygame_event,
)
from harddaysengine.app.input_diag_report import (
    DEFAULT_AXIS_EPSILON,
    DEFAULT_COALESCE_MS,
    DEFAULT_MAX_RECORDS,
    CoalesceConfig,
    JsonlReportWriter,
    MotionCoalescer,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from typing import TextIO

CAPTURE_WINDOW_SIZE = (320, 120)
CAPTURE_WINDOW_TITLE = "HardDaysEngine Input Diagnostics"
_HEADLESS_DRIVERS = frozenset({"dummy", "offscreen"})


class _ControllerHandle(Protocol):
    @property
    def name(self) -> str: ...
    def get_mapping(self) -> dict[str, str]: ...
    def quit(self) -> None: ...


class _ControllerModule(Protocol):
    def init(self) -> None: ...
    def quit(self) -> None: ...
    def get_init(self) -> bool: ...
    def is_controller(self, device_index: int) -> bool: ...
    def from_joystick(self, joystick: pygame.joystick.JoystickType) -> _ControllerHandle: ...


class _TouchModule(Protocol):
    def get_num_devices(self) -> int: ...
    def get_device(self, index: int, /) -> int: ...


@dataclass(slots=True, frozen=True)
class DiagnosticsOptions:
    """Configuration for a standalone SDL input diagnostic run (§13.12.1, §13.12.5-7)."""

    no_window: bool = False
    duration_s: float | None = None
    poll_sleep_s: float = 0.005
    output_path: Path | None = None
    max_records: int = DEFAULT_MAX_RECORDS
    coalesce_ms: int = DEFAULT_COALESCE_MS
    axis_epsilon: float = DEFAULT_AXIS_EPSILON
    raw: bool = False


@dataclass(slots=True, frozen=True)
class DiagnosticsSummary:
    """Final counts and emitted records from a diagnostic session."""

    reason: str
    total_raw_events: int
    total_emitted_records: int
    coalesced_raw_events: int
    truncated: bool
    elapsed_ms: int
    records: tuple[dict[str, object], ...]


@dataclass(slots=True)
class _OpenGamepad:
    """Retained SDL handles for one connected joystick/controller keyed by instance_id."""

    instance_id: int
    joystick: pygame.joystick.JoystickType | None
    controller: _ControllerHandle | None
    identity: DeviceIdentity


def _load_controller_module() -> _ControllerModule | None:
    try:
        from pygame._sdl2 import controller as sdl2_ctrl
    except (ImportError, pygame.error):
        return None
    return _ControllerAdapter(
        init_fn=sdl2_ctrl.init,
        quit_fn=sdl2_ctrl.quit,
        get_init_fn=sdl2_ctrl.get_init,
        is_controller_fn=sdl2_ctrl.is_controller,
        from_joystick_fn=sdl2_ctrl.Controller.from_joystick,
    )


@dataclass(slots=True, frozen=True)
class _ControllerAdapter:
    """Typed wrapper over optional ``pygame._sdl2.controller``."""

    init_fn: Callable[[], None]
    quit_fn: Callable[[], None]
    get_init_fn: Callable[[], bool]
    is_controller_fn: Callable[[int], bool]
    from_joystick_fn: Callable[[pygame.joystick.JoystickType], _ControllerHandle]

    def init(self) -> None:
        self.init_fn()

    def quit(self) -> None:
        self.quit_fn()

    def get_init(self) -> bool:
        return self.get_init_fn()

    def is_controller(self, device_index: int) -> bool:
        return self.is_controller_fn(device_index)

    def from_joystick(self, joystick: pygame.joystick.JoystickType) -> _ControllerHandle:
        return self.from_joystick_fn(joystick)


def _load_touch_module() -> _TouchModule | None:
    try:
        from pygame._sdl2 import touch as sdl2_touch
    except (ImportError, pygame.error):
        return None
    return sdl2_touch


def _query_capabilities(joy: pygame.joystick.JoystickType) -> dict[str, int]:
    caps: dict[str, int] = {}
    probes = (
        ("axes", joy.get_numaxes),
        ("buttons", joy.get_numbuttons),
        ("hats", joy.get_numhats),
        ("balls", joy.get_numballs),
    )
    for key, fn in probes:
        try:
            caps[key] = fn()
        except (pygame.error, AttributeError, NotImplementedError):
            continue
    return caps


def _query_initial_state(
    joy: pygame.joystick.JoystickType, caps: dict[str, int]
) -> dict[str, object]:
    state: dict[str, object] = {}
    axes = _probe_axes(joy, caps.get("axes", 0))
    if axes is not None:
        state["axes"] = axes
    hats = _probe_hats(joy, caps.get("hats", 0))
    if hats is not None:
        state["hats"] = hats
    buttons = _probe_buttons(joy, caps.get("buttons", 0))
    if buttons is not None:
        state["buttons"] = buttons
    return state


def _probe_axes(joy: pygame.joystick.JoystickType, count: int) -> list[float] | None:
    try:
        return [round(joy.get_axis(idx), 6) for idx in range(count)]
    except (pygame.error, AttributeError, NotImplementedError):
        return None


def _probe_hats(joy: pygame.joystick.JoystickType, count: int) -> list[list[int]] | None:
    try:
        return [[int(h[0]), int(h[1])] for h in (joy.get_hat(idx) for idx in range(count))]
    except (pygame.error, AttributeError, NotImplementedError):
        return None


def _probe_buttons(joy: pygame.joystick.JoystickType, count: int) -> list[int] | None:
    try:
        return [idx for idx in range(count) if joy.get_button(idx)]
    except (pygame.error, AttributeError, NotImplementedError):
        return None


def _safe_str_query(fn: Callable[[], str]) -> str | None:
    try:
        value = fn()
    except (pygame.error, AttributeError, NotImplementedError):
        return None
    return value if value else None


def _safe_mapping_query(ctrl: _ControllerHandle) -> dict[str, str] | None:
    try:
        raw = ctrl.get_mapping()
    except (pygame.error, AttributeError, NotImplementedError):
        return None
    return dict(sorted(raw.items()))


def _is_relevant_sdl_env(key: str) -> bool:
    return key in SDL_ENV_KEYS or key.startswith(SDL_ENV_PREFIXES)


def _collect_sdl_env() -> dict[str, str]:
    return {key: os.environ[key] for key in sorted(os.environ) if _is_relevant_sdl_env(key)}


def _event_instance_id(event: pygame.event.Event) -> int | None:
    raw = event.dict.get("instance_id")
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    return None


def _dispatch_records(
    records: list[dict[str, object]] | tuple[dict[str, object], ...],
    callback: Callable[[dict[str, object]], None] | None,
) -> None:
    if callback is None:
        return
    for rec in records:
        callback(rec)


def _sdl_const(name: str) -> int:
    val = getattr(pygame, name, -1)
    return val if isinstance(val, int) else -1


_JOY_ADDED = _sdl_const("JOYDEVICEADDED")
_JOY_REMOVED = _sdl_const("JOYDEVICEREMOVED")
_CTRL_ADDED = _sdl_const("CONTROLLERDEVICEADDED")
_CTRL_REMOVED = _sdl_const("CONTROLLERDEVICEREMOVED")
_CTRL_REMAPPED = _sdl_const("CONTROLLERDEVICEREMAPPED")
_WINDOW_CLOSE = _sdl_const("WINDOWCLOSE")


class InputDiagnosticsSession:
    """Manages SDL input initialization, device discovery, hotplug, and FIFO event capture."""

    def __init__(self, options: DiagnosticsOptions | None = None) -> None:
        self._options = options if options is not None else DiagnosticsOptions()
        if self._options.max_records < 1:
            raise ValueError("max_records must be a positive integer")
        self._coalesce_cfg = CoalesceConfig(
            coalesce_ms=self._options.coalesce_ms,
            axis_epsilon=self._options.axis_epsilon,
            raw=self._options.raw,
        )
        self._coalescer = MotionCoalescer(self._coalesce_cfg)
        self._ctrl_mod: _ControllerModule | None = None
        self._touch_mod: _TouchModule | None = None
        self._controller_api_available = False
        self._touch_api_available = False
        self._capture_window = False
        self._capture_window_note: str | None = None
        self._video_driver: str | None = None
        self._started = False
        self._closed = False
        self._start_ns = 0
        self._seq = 0
        self._raw_events = 0
        self._truncated = False
        self._stop_reason: str | None = None
        self._active: dict[int, _OpenGamepad] = {}
        self._known: dict[int, DeviceIdentity] = {}
        self._records: list[dict[str, object]] = []

    @property
    def stop_reason(self) -> str | None:
        """Non-``None`` once a stop trigger (such as ``quit``) has been observed."""
        return self._stop_reason

    @property
    def records(self) -> tuple[dict[str, object], ...]:
        """All diagnostic records emitted so far."""
        return tuple(self._records)

    def elapsed_ms(self) -> int:
        """Monotonic elapsed milliseconds since session start."""
        if self._start_ns == 0:
            return 0
        return max(0, (time.monotonic_ns() - self._start_ns) // 1_000_000)

    def _emit_raw(self, payload: dict[str, object]) -> dict[str, object]:
        self._seq += 1
        record: dict[str, object] = {"seq": self._seq, "elapsed_ms": self.elapsed_ms(), **payload}
        self._records.append(record)
        return record

    def _emit(self, payload: dict[str, object]) -> dict[str, object] | None:
        if self._seq >= self._options.max_records - 1:
            self._truncated = True
            self._stop_reason = "max_records_reached"
            return None
        record = self._emit_raw(payload)
        if self._seq >= self._options.max_records - 1:
            self._truncated = True
            self._stop_reason = "max_records_reached"
        return record

    def _emit_many(self, payloads: list[dict[str, object]]) -> list[dict[str, object]]:
        emitted: list[dict[str, object]] = []
        for payload in payloads:
            rec = self._emit(payload)
            if rec is None:
                break
            emitted.append(rec)
        return emitted

    def start(self) -> list[dict[str, object]]:
        """Initialize SDL input subsystems, emit ``session_start``, and discover devices."""
        if self._started:
            return []
        self._start_ns = time.monotonic_ns()
        self._init_sdl()
        self._started = True
        emitted: list[dict[str, object]] = []
        first = self._emit(self._build_session_start())
        if first is None:
            return emitted
        emitted.append(first)
        if self._stop_reason is None:
            emitted.extend(self._discover_startup_gamepads())
        if self._stop_reason is None:
            emitted.extend(self._discover_startup_touch())
        return emitted

    def _init_sdl(self) -> None:
        os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
        self._init_display()
        if not pygame.joystick.get_init():
            pygame.joystick.init()
        self._ctrl_mod = _load_controller_module()
        if self._ctrl_mod is not None:
            try:
                if not self._ctrl_mod.get_init():
                    self._ctrl_mod.init()
                self._controller_api_available = True
            except pygame.error:
                self._controller_api_available = False
        self._touch_mod = _load_touch_module()
        self._touch_api_available = self._touch_mod is not None

    def _init_display(self) -> None:
        try:
            if not pygame.display.get_init():
                pygame.display.init()
        except pygame.error as exc:
            self._capture_window_note = str(exc)
            os.environ["SDL_VIDEODRIVER"] = "dummy"
            pygame.display.init()
        self._video_driver = _safe_str_query(pygame.display.get_driver)
        if self._options.no_window or (self._video_driver in _HEADLESS_DRIVERS):
            self._capture_window = False
            return
        try:
            pygame.display.set_mode(CAPTURE_WINDOW_SIZE)
            pygame.display.set_caption(CAPTURE_WINDOW_TITLE)
            self._capture_window = True
        except pygame.error as exc:
            self._capture_window = False
            self._capture_window_note = str(exc)

    def _build_session_start(self) -> dict[str, object]:
        config = self._coalesce_cfg.to_dict()
        config["max_records"] = self._options.max_records
        payload: dict[str, object] = {
            "record_type": "session_start",
            "schema_version": DIAGNOSTICS_SCHEMA_VERSION,
            "engine_version": __version__,
            "pygame_version": pygame.version.ver,
            "sdl_version": list(pygame.get_sdl_version()),
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "sys_platform": sys.platform,
            "capture_window": self._capture_window,
            "controller_api_available": self._controller_api_available,
            "touch_api_available": self._touch_api_available,
            "sdl_env": _collect_sdl_env(),
            "config": config,
        }
        if self._video_driver is not None:
            payload["video_driver"] = self._video_driver
        if self._capture_window_note is not None:
            payload["capture_window_note"] = self._capture_window_note
        return payload

    def _discover_startup_gamepads(self) -> list[dict[str, object]]:
        emitted: list[dict[str, object]] = []
        try:
            count = pygame.joystick.get_count()
        except pygame.error:
            return emitted
        for idx in range(count):
            rec, _ = self._open_gamepad(idx, origin="startup", fallback_guid=None)
            if rec is not None:
                emitted.append(rec)
        return emitted

    def _discover_startup_touch(self) -> list[dict[str, object]]:
        if self._touch_mod is None:
            return []
        emitted: list[dict[str, object]] = []
        try:
            count = self._touch_mod.get_num_devices()
        except (pygame.error, AttributeError, NotImplementedError):
            return emitted
        for idx in range(count):
            try:
                touch_id = self._touch_mod.get_device(idx)
            except (pygame.error, AttributeError, NotImplementedError):
                continue
            rec = self._emit(
                {
                    "record_type": "device_discovered",
                    "device_kind": "touch",
                    "origin": "startup",
                    "touch_id": touch_id,
                }
            )
            if rec is None:
                break
            emitted.append(rec)
        return emitted

    def _open_gamepad(
        self,
        device_index: int,
        *,
        origin: str,
        fallback_guid: str | None,
    ) -> tuple[dict[str, object] | None, DeviceIdentity | None]:
        try:
            joy = pygame.joystick.Joystick(device_index)
            inst_id = joy.get_instance_id()
        except pygame.error:
            return None, None
        existing = self._active.get(inst_id)
        if existing is not None:
            return None, existing.identity
        ctrl, is_ctrl = self._try_open_controller(device_index, joy)
        name = _safe_str_query(joy.get_name)
        guid = _safe_str_query(joy.get_guid) or fallback_guid
        identity = DeviceIdentity(
            kind="gamepad",
            instance_id=inst_id,
            name=name,
            guid=guid,
            is_controller=is_ctrl,
        )
        self._active[inst_id] = _OpenGamepad(inst_id, joy, ctrl, identity)
        self._known[inst_id] = identity
        record = self._build_gamepad_discovery(
            joy, ctrl, identity, device_index=device_index, origin=origin
        )
        return self._emit(record), identity

    def _try_open_controller(
        self, device_index: int, joy: pygame.joystick.JoystickType
    ) -> tuple[_ControllerHandle | None, bool]:
        if not self._controller_api_available or self._ctrl_mod is None:
            return None, False
        try:
            is_ctrl = self._ctrl_mod.is_controller(device_index)
        except (pygame.error, AttributeError, NotImplementedError):
            return None, False
        if not is_ctrl:
            return None, False
        try:
            return self._ctrl_mod.from_joystick(joy), True
        except (pygame.error, AttributeError, NotImplementedError):
            return None, True

    def _build_gamepad_discovery(
        self,
        joy: pygame.joystick.JoystickType,
        ctrl: _ControllerHandle | None,
        identity: DeviceIdentity,
        *,
        device_index: int,
        origin: str,
    ) -> dict[str, object]:
        caps = _query_capabilities(joy)
        record: dict[str, object] = {
            "record_type": "device_discovered",
            "device_kind": "gamepad",
            "origin": origin,
            "instance_id": identity.instance_id,
            "device_index": device_index,
            "is_controller": bool(identity.is_controller),
            "capabilities": caps,
        }
        if identity.name:
            record["name"] = identity.name
        if identity.guid:
            record["guid"] = identity.guid
        power = _safe_str_query(joy.get_power_level)
        if power is not None:
            record["power_level"] = power
        initial = _query_initial_state(joy, caps)
        if initial:
            record["initial_state"] = initial
        if ctrl is not None:
            ctrl_name = _safe_str_query(lambda: ctrl.name)
            if ctrl_name is not None:
                record["controller_name"] = ctrl_name
            mapping = _safe_mapping_query(ctrl)
            if mapping is not None:
                record["controller_mapping"] = mapping
        return record

    def poll_once(self) -> list[dict[str, object]]:
        """Drain all queued SDL events in FIFO order and flush expired motion bursts."""
        emitted: list[dict[str, object]] = []
        for event in pygame.event.get():
            if self._stop_reason is not None:
                break
            emitted.extend(self.process_event(event))
        if self._stop_reason is None:
            emitted.extend(self._emit_many(self._coalescer.flush_expired(self.elapsed_ms())))
        return emitted

    def process_event(self, event: pygame.event.Event) -> list[dict[str, object]]:
        """Process one SDL event, updating hotplug state and emitting ordered records."""
        if self._stop_reason is not None:
            return []
        etype = event.type
        if etype in (_JOY_ADDED, _CTRL_ADDED):
            return self._handle_device_added(event)
        if etype in (_JOY_REMOVED, _CTRL_REMOVED):
            return self._handle_device_removed(event)
        if etype == _CTRL_REMAPPED:
            return self._handle_controller_remapped(event)
        if etype in (pygame.QUIT, _WINDOW_CLOSE):
            self._stop_reason = "quit"
        device = self._identity_for_event(event)
        return self._emit_normalized(event, device=device)

    def _identity_for_event(self, event: pygame.event.Event) -> DeviceIdentity | None:
        raw_inst = event.dict.get("instance_id")
        if isinstance(raw_inst, int) and not isinstance(raw_inst, bool):
            active = self._active.get(raw_inst)
            if active is not None:
                return active.identity
            return self._known.get(raw_inst)
        return None

    def _handle_device_added(self, event: pygame.event.Event) -> list[dict[str, object]]:
        emitted = self._emit_many(self._coalescer.flush_all())
        dev_idx = event.dict.get("device_index")
        raw_guid = event.dict.get("guid")
        fallback_guid = raw_guid if isinstance(raw_guid, str) and raw_guid else None
        identity: DeviceIdentity | None = None
        already_open: bool | None = None
        if isinstance(dev_idx, int) and not isinstance(dev_idx, bool):
            disc, identity = self._open_gamepad(
                dev_idx, origin="hotplug", fallback_guid=fallback_guid
            )
            if disc is not None:
                emitted.append(disc)
                already_open = False
            elif identity is not None:
                already_open = True
        emitted.extend(self._emit_normalized(event, device=identity, already_open=already_open))
        return emitted

    def _handle_device_removed(self, event: pygame.event.Event) -> list[dict[str, object]]:
        inst_id = _event_instance_id(event)
        identity = self._identity_for_event(event)
        emitted = self._emit_normalized(event, device=identity)
        if inst_id is not None:
            removed = self._release_device_handle(inst_id, event.type)
            if removed is not None:
                emitted.append(removed)
        return emitted

    def _release_device_handle(self, inst_id: int, event_type: int) -> dict[str, object] | None:
        entry = self._active.get(inst_id)
        if entry is None:
            return None
        if event_type == _CTRL_REMOVED:
            self._close_controller(entry)
        elif event_type == _JOY_REMOVED:
            self._close_joystick(entry)
        if entry.controller is None and entry.joystick is None:
            self._active.pop(inst_id, None)
            return self._emit_device_removed(entry.identity)
        return None

    def _handle_controller_remapped(self, event: pygame.event.Event) -> list[dict[str, object]]:
        raw_inst = event.dict.get("instance_id")
        inst_id = raw_inst if isinstance(raw_inst, int) and not isinstance(raw_inst, bool) else None
        identity = self._identity_for_event(event)
        mapping: dict[str, str] | None = None
        if inst_id is not None:
            entry = self._active.get(inst_id)
            if entry is not None and entry.controller is not None:
                mapping = _safe_mapping_query(entry.controller)
        return self._emit_normalized(event, device=identity, mapping=mapping)

    def _emit_device_removed(self, identity: DeviceIdentity) -> dict[str, object] | None:
        payload: dict[str, object] = {
            "record_type": "device_removed",
            "device_kind": identity.kind,
        }
        if identity.instance_id is not None:
            payload["instance_id"] = identity.instance_id
        if identity.name:
            payload["name"] = identity.name
        if identity.guid:
            payload["guid"] = identity.guid
        return self._emit(payload)

    def _emit_normalized(
        self,
        event: pygame.event.Event,
        *,
        device: DeviceIdentity | None = None,
        already_open: bool | None = None,
        mapping: dict[str, str] | None = None,
    ) -> list[dict[str, object]]:
        norm = normalize_pygame_event(
            event, device=device, already_open=already_open, mapping=mapping
        )
        if norm is None:
            return []
        self._raw_events += 1
        payloads = self._coalescer.push(norm, self.elapsed_ms())
        return self._emit_many(payloads)

    def _close_controller(self, entry: _OpenGamepad) -> None:
        if entry.controller is not None:
            with contextlib.suppress(pygame.error):
                entry.controller.quit()
            entry.controller = None

    def _close_joystick(self, entry: _OpenGamepad) -> None:
        if entry.joystick is not None:
            with contextlib.suppress(pygame.error):
                entry.joystick.quit()
            entry.joystick = None

    def flush_pending(self) -> list[dict[str, object]]:
        """Flush any buffered motion/axis bursts before shutdown."""
        if self._stop_reason == "max_records_reached":
            return []
        return self._emit_many(self._coalescer.flush_all())

    def finish(self, reason: str) -> dict[str, object]:
        """Emit ``session_end`` and release all open SDL device handles and subsystems."""
        if self._closed:
            return self._records[-1]
        final_reason = "max_records_reached" if self._truncated else reason
        end_rec = self._emit_raw(
            {
                "record_type": "session_end",
                "reason": final_reason,
                "total_raw_events": self._raw_events,
                "total_emitted_records": self._seq + 1,
                "coalesced_raw_events": self._coalescer.coalesced_raw_events,
                "truncated": self._truncated,
            }
        )
        self._cleanup_sdl()
        self._closed = True
        return end_rec

    def _cleanup_sdl(self) -> None:
        for entry in list(self._active.values()):
            self._close_controller(entry)
            self._close_joystick(entry)
        self._active.clear()
        if self._controller_api_available and self._ctrl_mod is not None:
            with contextlib.suppress(pygame.error):
                if self._ctrl_mod.get_init():
                    self._ctrl_mod.quit()
        if pygame.joystick.get_init():
            pygame.joystick.quit()
        if pygame.display.get_init():
            pygame.display.quit()

    def run(
        self,
        *,
        on_record: Callable[[dict[str, object]], None] | None = None,
        on_poll: Callable[[int], None] | None = None,
    ) -> DiagnosticsSummary:
        """Run the diagnostic loop until ``QUIT``, ``KeyboardInterrupt``, cap, or duration."""
        _dispatch_records(self.start(), on_record)
        reason = self._loop_until_stop(on_record=on_record, on_poll=on_poll)
        _dispatch_records(self.flush_pending(), on_record)
        end_rec = self.finish(reason)
        _dispatch_records((end_rec,), on_record)
        return DiagnosticsSummary(
            reason=str(end_rec["reason"]),
            total_raw_events=self._raw_events,
            total_emitted_records=self._seq,
            coalesced_raw_events=self._coalescer.coalesced_raw_events,
            truncated=self._truncated,
            elapsed_ms=self.elapsed_ms(),
            records=self.records,
        )

    def _loop_until_stop(
        self,
        *,
        on_record: Callable[[dict[str, object]], None] | None,
        on_poll: Callable[[int], None] | None,
    ) -> str:
        deadline_ns = self._deadline_ns()
        iteration = 0
        try:
            while self._stop_reason is None:
                if on_poll is not None:
                    on_poll(iteration)
                iteration += 1
                batch = self.poll_once()
                _dispatch_records(batch, on_record)
                if self._stop_reason is not None:
                    return self._stop_reason
                if deadline_ns is not None and time.monotonic_ns() >= deadline_ns:
                    return "duration_elapsed"
                self._sleep_if_idle(batch)
        except KeyboardInterrupt:
            return "interrupt"
        return self._stop_reason

    def _deadline_ns(self) -> int | None:
        if self._options.duration_s is None:
            return None
        return self._start_ns + int(self._options.duration_s * 1_000_000_000)

    def _sleep_if_idle(self, batch: list[dict[str, object]]) -> None:
        if not batch and self._options.poll_sleep_s > 0.0:
            time.sleep(self._options.poll_sleep_s)


def run_input_diagnostics(
    options: DiagnosticsOptions | None = None,
    *,
    stream: TextIO | None = None,
    on_poll: Callable[[int], None] | None = None,
) -> DiagnosticsSummary:
    """Run the diagnostic session, streaming to ``stream`` and optional flushed JSONL."""
    opts = options if options is not None else DiagnosticsOptions()
    out = stream if stream is not None else sys.stdout
    writer = JsonlReportWriter(opts.output_path) if opts.output_path is not None else None

    def _write_record(record: dict[str, object]) -> None:
        if writer is not None:
            writer.write_record(record)
        for line in format_terminal_record(record):
            out.write(line + "\n")
        out.flush()

    session = InputDiagnosticsSession(opts)
    try:
        return session.run(on_record=_write_record, on_poll=on_poll)
    finally:
        if writer is not None:
            writer.close()
