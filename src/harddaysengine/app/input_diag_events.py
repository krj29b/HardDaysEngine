"""Event normalization and terminal output for SDL input diagnostics (ARCHITECTURE.md §13.12)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pygame

if TYPE_CHECKING:
    from collections.abc import Callable

DIAGNOSTICS_SCHEMA_VERSION = 1
RAW_AXIS_THRESHOLD = 0.5
CONTROLLER_AXIS_THRESHOLD = 16384

SDL_ENV_KEYS: tuple[str, ...] = (
    "SDL_VIDEODRIVER",
    "SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS",
    "SDL_GAMECONTROLLERCONFIG",
    "SDL_HINT_JOYSTICK_HIDAPI",
    "SDL_HINT_JOYSTICK_RAWINPUT",
    "SDL_HINT_XINPUT_ENABLED",
    "SDL_HINT_DIRECTINPUT_ENABLED",
    "SDL_JOYSTICK_HIDAPI",
    "SDL_JOYSTICK_RAWINPUT",
)

MODIFIER_FLAGS: tuple[tuple[int, str], ...] = (
    (pygame.KMOD_LSHIFT, "lshift"),
    (pygame.KMOD_RSHIFT, "rshift"),
    (pygame.KMOD_LCTRL, "lctrl"),
    (pygame.KMOD_RCTRL, "rctrl"),
    (pygame.KMOD_LALT, "lalt"),
    (pygame.KMOD_RALT, "ralt"),
    (pygame.KMOD_LMETA, "lmeta"),
    (pygame.KMOD_RMETA, "rmeta"),
    (pygame.KMOD_NUM, "num"),
    (pygame.KMOD_CAPS, "caps"),
    (pygame.KMOD_MODE, "mode"),
)

MOUSE_BUTTON_NAMES: dict[int, str] = {
    1: "left",
    2: "middle",
    3: "right",
    4: "x1",
    5: "x2",
}

HAT_DIRECTIONS: dict[tuple[int, int], str] = {
    (0, 0): "center",
    (0, 1): "up",
    (0, -1): "down",
    (-1, 0): "left",
    (1, 0): "right",
    (-1, 1): "up_left",
    (1, 1): "up_right",
    (-1, -1): "down_left",
    (1, -1): "down_right",
}

CONTROLLER_AXIS_NAMES: dict[int, str] = {
    0: "leftx",
    1: "lefty",
    2: "rightx",
    3: "righty",
    4: "triggerleft",
    5: "triggerright",
}

CONTROLLER_BUTTON_NAMES: dict[int, str] = {
    0: "a",
    1: "b",
    2: "x",
    3: "y",
    4: "back",
    5: "guide",
    6: "start",
    7: "leftstick",
    8: "rightstick",
    9: "leftshoulder",
    10: "rightshoulder",
    11: "dpad_up",
    12: "dpad_down",
    13: "dpad_left",
    14: "dpad_right",
    15: "misc1",
    16: "paddle1",
    17: "paddle2",
    18: "paddle3",
    19: "paddle4",
    20: "touchpad",
}


def _sdl_const(name: str) -> int | None:
    value = getattr(pygame, name, None)
    return value if isinstance(value, int) else None


def _const_set(names: tuple[str, ...]) -> frozenset[int]:
    return frozenset(val for name in names if (val := _sdl_const(name)) is not None)


def _const_map(pairs: tuple[tuple[str, str], ...]) -> dict[int, str]:
    out: dict[int, str] = {}
    for name, label in pairs:
        val = _sdl_const(name)
        if val is not None:
            out[val] = label
    return out


IGNORED_SDL_TYPES: frozenset[int] = _const_set(
    (
        "AUDIODEVICEADDED",
        "AUDIODEVICEREMOVED",
        "WINDOWEXPOSED",
        "VIDEOEXPOSE",
        "WINDOWSHOWN",
        "WINDOWHIDDEN",
        "WINDOWMOVED",
        "WINDOWRESIZED",
        "WINDOWSIZECHANGED",
        "WINDOWMINIMIZED",
        "WINDOWMAXIMIZED",
        "WINDOWRESTORED",
        "WINDOWDISPLAYCHANGED",
        "WINDOWTAKEFOCUS",
        "WINDOWHITTEST",
        "WINDOWICCPROFCHANGED",
        "ACTIVEEVENT",
        "VIDEORESIZE",
    )
)

LIFECYCLE_EVENTS: dict[int, str] = _const_map(
    (
        ("QUIT", "quit"),
        ("WINDOWCLOSE", "window_close"),
        ("WINDOWFOCUSGAINED", "window_focus_gained"),
        ("WINDOWFOCUSLOST", "window_focus_lost"),
        ("WINDOWENTER", "window_enter"),
        ("WINDOWLEAVE", "window_leave"),
    )
)

KEYBOARD_EVENTS: dict[int, str] = _const_map(
    (
        ("KEYDOWN", "key_down"),
        ("KEYUP", "key_up"),
        ("TEXTINPUT", "text_input"),
        ("TEXTEDITING", "text_editing"),
        ("KEYMAPCHANGED", "keymap_changed"),
    )
)

MOUSE_EVENTS: dict[int, str] = _const_map(
    (
        ("MOUSEMOTION", "mouse_motion"),
        ("MOUSEBUTTONDOWN", "mouse_button_down"),
        ("MOUSEBUTTONUP", "mouse_button_up"),
        ("MOUSEWHEEL", "mouse_wheel"),
    )
)

JOYSTICK_EVENTS: dict[int, str] = _const_map(
    (
        ("JOYDEVICEADDED", "joy_device_added"),
        ("JOYDEVICEREMOVED", "joy_device_removed"),
        ("JOYAXISMOTION", "joy_axis_motion"),
        ("JOYHATMOTION", "joy_hat_motion"),
        ("JOYBALLMOTION", "joy_ball_motion"),
        ("JOYBUTTONDOWN", "joy_button_down"),
        ("JOYBUTTONUP", "joy_button_up"),
    )
)

CONTROLLER_EVENTS: dict[int, str] = _const_map(
    (
        ("CONTROLLERDEVICEADDED", "controller_device_added"),
        ("CONTROLLERDEVICEREMOVED", "controller_device_removed"),
        ("CONTROLLERDEVICEREMAPPED", "controller_device_remapped"),
        ("CONTROLLERAXISMOTION", "controller_axis_motion"),
        ("CONTROLLERBUTTONDOWN", "controller_button_down"),
        ("CONTROLLERBUTTONUP", "controller_button_up"),
        ("CONTROLLERTOUCHPADDOWN", "controller_touchpad_down"),
        ("CONTROLLERTOUCHPADMOTION", "controller_touchpad_motion"),
        ("CONTROLLERTOUCHPADUP", "controller_touchpad_up"),
        ("CONTROLLERSENSORUPDATE", "controller_sensor_update"),
    )
)

OTHER_KNOWN_INPUT_EVENTS: dict[int, str] = _const_map(
    (
        ("FINGERDOWN", "finger_down"),
        ("FINGERMOTION", "finger_motion"),
        ("FINGERUP", "finger_up"),
        ("MULTIGESTURE", "multi_gesture"),
        ("DOLLARGESTURE", "dollar_gesture"),
        ("DOLLARRECORD", "dollar_record"),
    )
)


@dataclass(slots=True, frozen=True)
class DeviceIdentity:
    """Stable device identity attached to discovery and input records (§13.12.2)."""

    kind: str
    instance_id: int | None = None
    name: str | None = None
    guid: str | None = None
    is_controller: bool | None = None
    touch_id: int | None = None
    which: int | None = None

    def to_dict(self) -> dict[str, object]:
        """Serialize present identity fields, omitting unavailable values."""
        out: dict[str, object] = {"kind": self.kind}
        if self.instance_id is not None:
            out["instance_id"] = self.instance_id
        if self.name:
            out["name"] = self.name
        if self.guid:
            out["guid"] = self.guid
        if self.is_controller is not None:
            out["is_controller"] = self.is_controller
        if self.touch_id is not None:
            out["touch_id"] = self.touch_id
        if self.which is not None:
            out["which"] = self.which
        return out


@dataclass(slots=True, frozen=True)
class NormalizedEvent:
    """Normalized diagnostic representation of a single SDL event (§13.12.4)."""

    category: str
    event: str
    sdl_type: int
    sdl_name: str
    device: dict[str, object]
    fields: dict[str, object]


def sanitize_json_value(value: object) -> object:
    """Convert arbitrary SDL event attribute values into JSON-safe primitives."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return [sanitize_json_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(k): sanitize_json_value(v) for k, v in value.items()}
    return repr(value)


def raw_axis_band(value: float) -> str:
    """Classify a raw joystick axis value against the §13.12.5 deadzone bands."""
    if value <= -RAW_AXIS_THRESHOLD:
        return "neg"
    if value >= RAW_AXIS_THRESHOLD:
        return "pos"
    return "center"


def controller_axis_band(value: int) -> str:
    """Classify a mapped SDL controller axis value against the §13.12.5 deadzone bands."""
    if value <= -CONTROLLER_AXIS_THRESHOLD:
        return "neg"
    if value >= CONTROLLER_AXIS_THRESHOLD:
        return "pos"
    return "center"


def modifier_names(mod_mask: int) -> list[str]:
    """Decode an SDL key-modifier bitmask into sorted stable names."""
    return sorted(label for flag, label in MODIFIER_FLAGS if mod_mask & flag)


def _int_attr(data: Mapping[str, object], key: str) -> int | None:
    raw = data.get(key)
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    return None


def _float_attr(data: Mapping[str, object], key: str) -> float | None:
    raw = data.get(key)
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int | float):
        return float(raw)
    return None


def _pair_int(data: Mapping[str, object], key: str) -> list[int] | None:
    raw = data.get(key)
    if isinstance(raw, Sequence) and not isinstance(raw, str | bytes | bytearray) and len(raw) == 2:
        a, b = raw[0], raw[1]
        if isinstance(a, int | float) and isinstance(b, int | float):
            return [int(a), int(b)]
    return None


def _optional_copy(
    src: Mapping[str, object], dst: dict[str, object], keys: tuple[str, ...]
) -> None:
    for key in keys:
        if key in src and src[key] is not None:
            dst[key] = sanitize_json_value(src[key])


def _safe_key_name(key_code: int) -> str:
    try:
        name = pygame.key.name(key_code)
    except pygame.error:
        return f"key_{key_code}"
    return name or f"key_{key_code}"


def _norm_keyboard(
    event_name: str, data: Mapping[str, object]
) -> tuple[dict[str, object], dict[str, object]]:
    fields: dict[str, object] = {}
    if event_name in ("key_down", "key_up"):
        key_code = _int_attr(data, "key")
        if key_code is not None:
            fields["key"] = key_code
            fields["key_name"] = _safe_key_name(key_code)
        mod_mask = _int_attr(data, "mod")
        if mod_mask is not None:
            fields["mod"] = mod_mask
            fields["mod_names"] = modifier_names(mod_mask)
        _optional_copy(data, fields, ("scancode", "repeat"))
        unicode_val = data.get("unicode")
        if isinstance(unicode_val, str) and unicode_val:
            fields["unicode"] = unicode_val
    elif event_name == "text_input":
        _optional_copy(data, fields, ("text",))
    elif event_name == "text_editing":
        _optional_copy(data, fields, ("text", "start", "length"))
    return DeviceIdentity(kind="keyboard").to_dict(), fields


def _norm_mouse(
    event_name: str, data: Mapping[str, object]
) -> tuple[dict[str, object], dict[str, object]]:
    which = _int_attr(data, "which")
    device = DeviceIdentity(kind="mouse", which=which).to_dict()
    fields: dict[str, object] = {}
    if event_name == "mouse_motion":
        _optional_copy(data, fields, ("pos", "rel", "buttons", "touch", "which"))
    elif event_name in ("mouse_button_down", "mouse_button_up"):
        btn = _int_attr(data, "button")
        if btn is not None:
            fields["button"] = btn
            fields["button_name"] = MOUSE_BUTTON_NAMES.get(btn, f"button_{btn}")
        _optional_copy(data, fields, ("pos", "clicks", "touch", "which"))
    elif event_name == "mouse_wheel":
        _optional_copy(data, fields, ("x", "y", "flipped", "precise_x", "precise_y", "touch"))
        if which is not None:
            fields["which"] = which
    return device, fields


def _norm_joystick(
    event_name: str,
    data: Mapping[str, object],
    *,
    device: DeviceIdentity | None,
    already_open: bool | None,
) -> tuple[dict[str, object], dict[str, object]]:
    inst = _int_attr(data, "instance_id")
    dev_obj = device if device is not None else DeviceIdentity(kind="gamepad", instance_id=inst)
    fields: dict[str, object] = {}
    if event_name == "joy_device_added":
        _optional_copy(data, fields, ("device_index", "guid"))
        if already_open is not None:
            fields["already_open"] = already_open
    elif event_name == "joy_device_removed":
        _optional_copy(data, fields, ("instance_id",))
    elif event_name == "joy_axis_motion":
        _fill_joy_axis(data, fields)
    elif event_name == "joy_hat_motion":
        _fill_joy_hat(data, fields)
    elif event_name == "joy_ball_motion":
        _optional_copy(data, fields, ("ball", "rel"))
    elif event_name in ("joy_button_down", "joy_button_up"):
        _optional_copy(data, fields, ("button",))
    return dev_obj.to_dict(), fields


def _fill_joy_axis(data: Mapping[str, object], fields: dict[str, object]) -> None:
    _optional_copy(data, fields, ("axis",))
    val = _float_attr(data, "value")
    if val is not None:
        fields["value"] = round(val, 6)
        fields["band"] = raw_axis_band(val)


def _fill_joy_hat(data: Mapping[str, object], fields: dict[str, object]) -> None:
    _optional_copy(data, fields, ("hat",))
    pair = _pair_int(data, "value")
    if pair is not None:
        fields["value"] = pair
        fields["direction"] = HAT_DIRECTIONS.get((pair[0], pair[1]), f"hat_{pair[0]}_{pair[1]}")


def _fill_controller_device_event(
    event_name: str,
    data: Mapping[str, object],
    fields: dict[str, object],
    *,
    already_open: bool | None,
    mapping: Mapping[str, str] | None,
) -> None:
    if event_name == "controller_device_added":
        _optional_copy(data, fields, ("device_index", "guid"))
        if already_open is not None:
            fields["already_open"] = already_open
        return
    _optional_copy(data, fields, ("instance_id",))
    if event_name == "controller_device_remapped" and mapping is not None:
        fields["mapping"] = dict(sorted(mapping.items()))


def _norm_controller(
    event_name: str,
    data: Mapping[str, object],
    *,
    device: DeviceIdentity | None,
    already_open: bool | None,
    mapping: Mapping[str, str] | None,
) -> tuple[dict[str, object], dict[str, object]]:
    inst = _int_attr(data, "instance_id")
    dev_obj = device if device is not None else DeviceIdentity(kind="gamepad", instance_id=inst)
    fields: dict[str, object] = {}
    if event_name.startswith("controller_device_"):
        _fill_controller_device_event(
            event_name, data, fields, already_open=already_open, mapping=mapping
        )
    elif event_name == "controller_axis_motion":
        _fill_controller_axis(data, fields)
    elif event_name in ("controller_button_down", "controller_button_up"):
        _fill_controller_button(data, fields)
    elif event_name.startswith("controller_touchpad_"):
        _optional_copy(data, fields, ("touchpad", "finger", "x", "y", "pressure"))
    elif event_name == "controller_sensor_update":
        _optional_copy(data, fields, ("sensor", "data"))
    return dev_obj.to_dict(), fields


def _fill_controller_axis(data: Mapping[str, object], fields: dict[str, object]) -> None:
    axis = _int_attr(data, "axis")
    if axis is not None:
        fields["axis"] = axis
        fields["axis_name"] = CONTROLLER_AXIS_NAMES.get(axis, f"axis_{axis}")
    val = _int_attr(data, "value")
    if val is not None:
        fields["value"] = val
        fields["normalized"] = round(max(-1.0, val / 32767.0), 6)
        fields["band"] = controller_axis_band(val)


def _fill_controller_button(data: Mapping[str, object], fields: dict[str, object]) -> None:
    btn = _int_attr(data, "button")
    if btn is not None:
        fields["button"] = btn
        fields["button_name"] = CONTROLLER_BUTTON_NAMES.get(btn, f"button_{btn}")


def _norm_other_input(
    event_type: int, data: Mapping[str, object]
) -> tuple[str, dict[str, object], dict[str, object]]:
    event_name = OTHER_KNOWN_INPUT_EVENTS.get(event_type, f"sdl_event_{event_type}")
    touch_id = _int_attr(data, "touch_id")
    device = (
        DeviceIdentity(kind="touch", touch_id=touch_id).to_dict()
        if touch_id is not None
        else DeviceIdentity(kind="unknown").to_dict()
    )
    fields = {k: sanitize_json_value(v) for k, v in sorted(data.items())}
    return event_name, device, fields


def _norm_lifecycle(
    event_name: str, data: Mapping[str, object]
) -> tuple[dict[str, object], dict[str, object]]:
    fields: dict[str, object] = {}
    _optional_copy(data, fields, ("window",))
    return DeviceIdentity(kind="lifecycle").to_dict(), fields


def _safe_event_name(event_type: int) -> str:
    try:
        name = pygame.event.event_name(event_type)
    except pygame.error:
        return f"Event{event_type}"
    return name or f"Event{event_type}"


def normalize_pygame_event(
    event: pygame.event.Event,
    *,
    device: DeviceIdentity | None = None,
    already_open: bool | None = None,
    mapping: Mapping[str, str] | None = None,
) -> NormalizedEvent | None:
    """Normalize a pygame event per §13.12.4, or return ``None`` for ignored housekeeping."""
    etype = event.type
    if etype in IGNORED_SDL_TYPES:
        return None
    data = event.dict
    sdl_name = _safe_event_name(etype)
    if etype in KEYBOARD_EVENTS:
        ev_name = KEYBOARD_EVENTS[etype]
        dev, fields = _norm_keyboard(ev_name, data)
        return NormalizedEvent("keyboard", ev_name, etype, sdl_name, dev, fields)
    if etype in MOUSE_EVENTS:
        ev_name = MOUSE_EVENTS[etype]
        dev, fields = _norm_mouse(ev_name, data)
        return NormalizedEvent("mouse", ev_name, etype, sdl_name, dev, fields)
    if etype in JOYSTICK_EVENTS:
        ev_name = JOYSTICK_EVENTS[etype]
        dev, fields = _norm_joystick(ev_name, data, device=device, already_open=already_open)
        return NormalizedEvent("joystick", ev_name, etype, sdl_name, dev, fields)
    if etype in CONTROLLER_EVENTS:
        ev_name = CONTROLLER_EVENTS[etype]
        dev, fields = _norm_controller(
            ev_name, data, device=device, already_open=already_open, mapping=mapping
        )
        return NormalizedEvent("controller", ev_name, etype, sdl_name, dev, fields)
    if etype in LIFECYCLE_EVENTS:
        ev_name = LIFECYCLE_EVENTS[etype]
        dev, fields = _norm_lifecycle(ev_name, data)
        return NormalizedEvent("lifecycle", ev_name, etype, sdl_name, dev, fields)
    ev_name, dev, fields = _norm_other_input(etype, data)
    return NormalizedEvent("other_input", ev_name, etype, sdl_name, dev, fields)


def _format_session_start(record: Mapping[str, object]) -> list[str]:
    sdl_ver = record.get("sdl_version")
    sdl_text = ".".join(str(x) for x in sdl_ver) if isinstance(sdl_ver, list) else "unknown"
    win_text = "on" if record.get("capture_window") else "off"
    line = (
        f"HardDaysEngine {record.get('engine_version', '?')} input diagnostics | "
        f"pygame-ce {record.get('pygame_version', '?')} | SDL {sdl_text} | "
        f"{record.get('sys_platform', '?')} | window={win_text}"
    )
    note = record.get("capture_window_note")
    if isinstance(note, str) and note:
        return [line, f"  note: capture window unavailable ({note})"]
    return [line]


def _format_device_discovered(record: Mapping[str, object]) -> list[str]:
    elapsed = record.get("elapsed_ms", 0)
    origin = record.get("origin", "unknown")
    kind = record.get("device_kind", "gamepad")
    if kind == "touch":
        return [f"[+{elapsed}ms] DEVICE+ ({origin}) touch_id={record.get('touch_id', 'n/a')}"]
    caps = record.get("capabilities")
    cap_map = caps if isinstance(caps, Mapping) else {}
    ctrl_flag = "yes" if record.get("is_controller") else "no"
    line = (
        f"[+{elapsed}ms] DEVICE+ ({origin}) id={record.get('instance_id', 'n/a')} "
        f'"{record.get("name", "unknown")}" guid={record.get("guid", "n/a")} '
        f"ctrl={ctrl_flag} axes={cap_map.get('axes', 'n/a')} "
        f"btns={cap_map.get('buttons', 'n/a')} hats={cap_map.get('hats', 'n/a')} "
        f"power={record.get('power_level', 'n/a')}"
    )
    lines = [line]
    mapping = record.get("controller_mapping")
    if isinstance(mapping, Mapping) and mapping:
        pairs = " ".join(f"{k}={v}" for k, v in sorted(mapping.items()))
        lines.append(f"  mapping: {pairs}")
    return lines


def _format_device_removed(record: Mapping[str, object]) -> list[str]:
    elapsed = record.get("elapsed_ms", 0)
    return [
        f"[+{elapsed}ms] DEVICE- id={record.get('instance_id', 'n/a')} "
        f'"{record.get("name", "unknown")}" guid={record.get("guid", "n/a")}'
    ]


def _format_event_tag(category: str, device: Mapping[str, object]) -> str:
    tag_map = {
        "keyboard": "KEY",
        "mouse": "MOUSE",
        "joystick": "JOY",
        "controller": "CTRL",
        "other_input": "OTHER",
        "lifecycle": "LIFE",
    }
    prefix = tag_map.get(category, category.upper())
    inst = device.get("instance_id")
    if inst is not None:
        return f"{prefix} id={inst}"
    touch_id = device.get("touch_id")
    if touch_id is not None:
        return f"{prefix} touch={touch_id}"
    return prefix


def _format_fields_summary(fields: Mapping[str, object], *, coalesced: bool = False) -> str:
    parts: list[str] = []
    for key, value in fields.items():
        if key == "threshold_crossed":
            continue
        if coalesced and key in ("value", "pos"):
            continue
        if key == "mapping" and isinstance(value, Mapping):
            parts.append(f"mapping_keys={len(value)}")
        elif isinstance(value, list):
            parts.append(f"{key}=[{','.join(str(x) for x in value)}]")
        else:
            parts.append(f"{key}={value}")
    if fields.get("threshold_crossed"):
        parts.append("[threshold]")
    return " ".join(parts)


def _format_event(record: Mapping[str, object]) -> list[str]:
    elapsed = record.get("elapsed_ms", 0)
    seq = record.get("seq", 0)
    category = str(record.get("category", "other_input"))
    raw_dev = record.get("device")
    device = raw_dev if isinstance(raw_dev, Mapping) else {}
    raw_fields = record.get("fields")
    fields = raw_fields if isinstance(raw_fields, Mapping) else {}
    coalesced = bool(record.get("coalesced"))
    tag = _format_event_tag(category, device)
    ev_name = record.get("event", "unknown")
    summary = _format_fields_summary(fields, coalesced=coalesced)
    suffix = f" (x{record.get('sample_count', 1)} coalesced)" if coalesced else ""
    tail = f" {summary}" if summary else ""
    return [f"[+{elapsed}ms] #{seq} {tag} {ev_name}{tail}{suffix}"]


def _format_session_end(record: Mapping[str, object]) -> list[str]:
    elapsed = record.get("elapsed_ms", 0)
    truncated = "yes" if record.get("truncated") else "no"
    return [
        f"[+{elapsed}ms] SESSION END reason={record.get('reason', 'unknown')} "
        f"raw={record.get('total_raw_events', 0)} "
        f"emitted={record.get('total_emitted_records', 0)} truncated={truncated}"
    ]


_RECORD_FORMATTERS: dict[str, Callable[[Mapping[str, object]], list[str]]] = {
    "session_start": _format_session_start,
    "device_discovered": _format_device_discovered,
    "device_removed": _format_device_removed,
    "event": _format_event,
    "session_end": _format_session_end,
}


def format_terminal_record(record: Mapping[str, object]) -> list[str]:
    """Render a diagnostic record into concise single-line terminal strings (§13.12.6)."""
    rtype = str(record.get("record_type", ""))
    formatter = _RECORD_FORMATTERS.get(rtype)
    if formatter is None:
        return [str(record)]
    return formatter(record)
