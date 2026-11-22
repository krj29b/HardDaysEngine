"""Unit, virtual-controller, and CLI smoke tests for SDL input diagnostics (T16.2)."""

from __future__ import annotations

import ast
import ctypes
import io
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pygame
import pytest

import harddaysengine
from harddaysengine import __version__
from harddaysengine.app.input_diag_events import (
    DeviceIdentity,
    controller_axis_band,
    format_terminal_record,
    normalize_pygame_event,
    raw_axis_band,
    sanitize_json_value,
)
from harddaysengine.app.input_diagnostics import (
    DiagnosticsOptions,
    InputDiagnosticsSession,
    run_input_diagnostics,
)
from harddaysengine.cli.input_diagnostics import main

SRC_ROOT = Path(harddaysengine.__file__).parent


class _VirtualSdl2:
    """Thin ctypes helper over SDL2's virtual joystick API for deterministic hotplug tests."""

    def __init__(self, lib: ctypes.CDLL) -> None:
        self._lib = lib
        self._lib.SDL_JoystickAttachVirtual.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        self._lib.SDL_JoystickAttachVirtual.restype = ctypes.c_int
        self._lib.SDL_JoystickDetachVirtual.argtypes = [ctypes.c_int]
        self._lib.SDL_JoystickDetachVirtual.restype = ctypes.c_int
        self._lib.SDL_JoystickFromInstanceID.argtypes = [ctypes.c_int]
        self._lib.SDL_JoystickFromInstanceID.restype = ctypes.c_void_p
        self._lib.SDL_JoystickSetVirtualAxis.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int16,
        ]
        self._lib.SDL_JoystickSetVirtualButton.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_uint8,
        ]
        self._lib.SDL_JoystickSetVirtualHat.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_uint8,
        ]
        self._attached: list[int] = []

    def attach(
        self,
        *,
        joy_type: int = 1,
        naxes: int = 6,
        nbuttons: int = 11,
        nhats: int = 1,
    ) -> int:
        idx = int(self._lib.SDL_JoystickAttachVirtual(joy_type, naxes, nbuttons, nhats))
        if idx < 0:
            raise RuntimeError(f"SDL_JoystickAttachVirtual failed: {idx}")
        self._attached.append(idx)
        return idx

    def set_axis(self, instance_id: int, axis: int, value: int) -> None:
        ptr = self._lib.SDL_JoystickFromInstanceID(instance_id)
        if ptr:
            self._lib.SDL_JoystickSetVirtualAxis(ptr, axis, value)

    def set_button(self, instance_id: int, button: int, value: int) -> None:
        ptr = self._lib.SDL_JoystickFromInstanceID(instance_id)
        if ptr:
            self._lib.SDL_JoystickSetVirtualButton(ptr, button, value)

    def set_hat(self, instance_id: int, hat: int, value: int) -> None:
        ptr = self._lib.SDL_JoystickFromInstanceID(instance_id)
        if ptr:
            self._lib.SDL_JoystickSetVirtualHat(ptr, hat, value)

    def detach(self, device_index: int) -> None:
        self._lib.SDL_JoystickDetachVirtual(device_index)
        if device_index in self._attached:
            self._attached.remove(device_index)

    def detach_all(self) -> None:
        for idx in sorted(self._attached, reverse=True):
            self._lib.SDL_JoystickDetachVirtual(idx)
        self._attached.clear()


def _load_virtual_sdl2() -> _VirtualSdl2 | None:
    pdir = Path(pygame.__file__).parent
    candidates: list[Path] = []
    for pattern in ("*SDL2*.dll", "*SDL2*.so*", "*SDL2*.dylib"):
        candidates.extend(sorted(pdir.rglob(pattern)))
    for candidate in candidates:
        try:
            lib = ctypes.CDLL(str(candidate))
            if hasattr(lib, "SDL_JoystickAttachVirtual"):
                return _VirtualSdl2(lib)
        except OSError:
            continue
    return None


@pytest.fixture
def dummy_sdl_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")


def test_diagnostics_modules_do_not_import_game_or_disk_layers() -> None:
    forbidden = (
        "harddaysengine.sim",
        "harddaysengine.render",
        "harddaysengine.assets",
        "harddaysengine.app.game",
        "harddaysengine.app.input",
        "harddaysengine.app.audio",
        "harddaysengine.app.window",
        "moderngl",
        "amitools",
        "pygame.mixer",
    )
    files = (
        SRC_ROOT / "app" / "input_diag_events.py",
        SRC_ROOT / "app" / "input_diagnostics.py",
        SRC_ROOT / "cli" / "input_diagnostics.py",
    )
    offenders: list[str] = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                if any(name == bad or name.startswith(bad + ".") for bad in forbidden):
                    offenders.append(f"{path.name}: {name}")
    assert offenders == []


def test_axis_bands_and_json_sanitization() -> None:
    assert raw_axis_band(-0.5) == "neg"
    assert raw_axis_band(-0.49) == "center"
    assert raw_axis_band(0.49) == "center"
    assert raw_axis_band(0.5) == "pos"
    assert controller_axis_band(-16384) == "neg"
    assert controller_axis_band(0) == "center"
    assert controller_axis_band(16384) == "pos"

    class _Custom:
        def __repr__(self) -> str:
            return "<custom>"

    assert sanitize_json_value({"a": (1, 2.1234567, _Custom())}) == {"a": [1, 2.123457, "<custom>"]}


def test_normalize_keyboard_mouse_joystick_controller_other_and_ignored() -> None:
    key_ev = pygame.event.Event(
        pygame.KEYDOWN,
        {"key": pygame.K_SPACE, "mod": pygame.KMOD_LSHIFT | pygame.KMOD_LCTRL, "unicode": " "},
    )
    norm_key = normalize_pygame_event(key_ev)
    assert norm_key is not None
    assert norm_key.category == "keyboard"
    assert norm_key.event == "key_down"
    assert norm_key.fields["key_name"] == "space"
    assert norm_key.fields["mod_names"] == ["lctrl", "lshift"]
    assert norm_key.fields["unicode"] == " "

    mouse_btn = pygame.event.Event(
        pygame.MOUSEBUTTONDOWN, {"button": 1, "pos": (12, 34), "clicks": 1, "which": 0}
    )
    norm_mouse = normalize_pygame_event(mouse_btn)
    assert norm_mouse is not None
    assert norm_mouse.category == "mouse"
    assert norm_mouse.event == "mouse_button_down"
    assert norm_mouse.fields["button_name"] == "left"
    assert norm_mouse.device == {"kind": "mouse", "which": 0}

    wheel = pygame.event.Event(pygame.MOUSEWHEEL, {"x": 0, "y": -1, "flipped": False})
    norm_wheel = normalize_pygame_event(wheel)
    assert norm_wheel is not None
    assert norm_wheel.event == "mouse_wheel"
    assert norm_wheel.fields["y"] == -1

    ident = DeviceIdentity(
        kind="gamepad", instance_id=7, name="Test Pad", guid="0300abcd", is_controller=True
    )
    hat = pygame.event.Event(pygame.JOYHATMOTION, {"instance_id": 7, "hat": 0, "value": (-1, 1)})
    norm_hat = normalize_pygame_event(hat, device=ident)
    assert norm_hat is not None
    assert norm_hat.category == "joystick"
    assert norm_hat.event == "joy_hat_motion"
    assert norm_hat.fields["direction"] == "up_left"
    assert norm_hat.device["guid"] == "0300abcd"

    ctrl_axis = pygame.event.Event(
        pygame.CONTROLLERAXISMOTION, {"instance_id": 7, "axis": 0, "value": 20000}
    )
    norm_caxis = normalize_pygame_event(ctrl_axis, device=ident)
    assert norm_caxis is not None
    assert norm_caxis.category == "controller"
    assert norm_caxis.event == "controller_axis_motion"
    assert norm_caxis.fields["axis_name"] == "leftx"
    assert norm_caxis.fields["band"] == "pos"
    assert norm_caxis.fields["normalized"] == round(20000 / 32767.0, 6)

    ctrl_btn = pygame.event.Event(pygame.CONTROLLERBUTTONDOWN, {"instance_id": 7, "button": 15})
    norm_cbtn = normalize_pygame_event(ctrl_btn, device=ident)
    assert norm_cbtn is not None
    assert norm_cbtn.fields["button_name"] == "misc1"

    unknown_type = pygame.USEREVENT + 17
    unknown = pygame.event.Event(unknown_type, {"detail": "probe"})
    norm_unk = normalize_pygame_event(unknown)
    assert norm_unk is not None
    assert norm_unk.category == "other_input"
    assert norm_unk.event == f"sdl_event_{unknown_type}"
    assert norm_unk.fields["detail"] == "probe"

    if hasattr(pygame, "AUDIODEVICEADDED"):
        assert normalize_pygame_event(pygame.event.Event(pygame.AUDIODEVICEADDED, {})) is None

    quit_norm = normalize_pygame_event(pygame.event.Event(pygame.QUIT, {}))
    assert quit_norm is not None
    assert quit_norm.category == "lifecycle"
    assert quit_norm.event == "quit"


def test_terminal_formatting_for_all_record_types() -> None:
    start_lines = format_terminal_record(
        {
            "record_type": "session_start",
            "engine_version": "0.4.1",
            "pygame_version": "2.5.8",
            "sdl_version": [2, 32, 10],
            "sys_platform": "win32",
            "capture_window": False,
            "capture_window_note": "headless",
        }
    )
    assert len(start_lines) == 2
    assert "window=off" in start_lines[0]
    assert "headless" in start_lines[1]

    disc_lines = format_terminal_record(
        {
            "record_type": "device_discovered",
            "elapsed_ms": 5,
            "origin": "startup",
            "device_kind": "gamepad",
            "instance_id": 0,
            "name": "Virtual Controller",
            "guid": "ff00",
            "is_controller": True,
            "capabilities": {"axes": 6, "buttons": 11, "hats": 1},
            "controller_mapping": {"a": "b0", "leftx": "a0"},
        }
    )
    assert len(disc_lines) == 2
    assert "DEVICE+ (startup) id=0" in disc_lines[0]
    assert "mapping: a=b0 leftx=a0" in disc_lines[1]

    rem_lines = format_terminal_record(
        {
            "record_type": "device_removed",
            "elapsed_ms": 12,
            "instance_id": 0,
            "name": "Virtual Controller",
            "guid": "ff00",
        }
    )
    assert 'DEVICE- id=0 "Virtual Controller"' in rem_lines[0]


class _BrokenMetaJoystick:
    """Stub joystick whose optional metadata queries fail with ``pygame.error``."""

    def get_instance_id(self) -> int:
        return 42

    def get_name(self) -> str:
        return "Minimal Pad"

    def get_guid(self) -> str:
        raise pygame.error("no guid")

    def get_power_level(self) -> str:
        raise pygame.error("no power")

    def get_numaxes(self) -> int:
        return 2

    def get_numbuttons(self) -> int:
        return 4

    def get_numhats(self) -> int:
        raise pygame.error("no hats")

    def get_numballs(self) -> int:
        return 0

    def get_axis(self, _idx: int) -> float:
        raise pygame.error("no axis read")

    def get_button(self, _idx: int) -> bool:
        return False

    def quit(self) -> None:
        return None


def test_unavailable_joystick_metadata_is_omitted_not_fatal(
    dummy_sdl_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pygame.joystick, "get_count", lambda: 1)
    monkeypatch.setattr(pygame.joystick, "Joystick", lambda _idx: _BrokenMetaJoystick())
    session = InputDiagnosticsSession(DiagnosticsOptions(no_window=True, duration_s=0.0))
    records = session.start()
    try:
        discovered = [r for r in records if r["record_type"] == "device_discovered"]
        assert len(discovered) == 1
        rec = discovered[0]
        assert rec["instance_id"] == 42
        assert rec["name"] == "Minimal Pad"
        assert "guid" not in rec
        assert "power_level" not in rec
        assert rec["capabilities"] == {"axes": 2, "buttons": 4, "balls": 0}
    finally:
        session.finish("quit")


def test_cli_version_and_invalid_duration(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"HardDaysEngine {__version__}"

    with pytest.raises(SystemExit) as bad:
        main(["--duration", "-1"])
    assert bad.value.code == 2


def test_smoke_no_devices_and_clean_shutdown(dummy_sdl_env: None) -> None:
    buf = io.StringIO()
    summary = run_input_diagnostics(
        DiagnosticsOptions(no_window=True, duration_s=0.02, poll_sleep_s=0.002),
        stream=buf,
    )
    assert summary.reason == "duration_elapsed"
    assert summary.records[0]["record_type"] == "session_start"
    assert summary.records[-1]["record_type"] == "session_end"
    assert pygame.mixer.get_init() is None
    text = buf.getvalue()
    assert "HardDaysEngine" in text
    assert "SESSION END reason=duration_elapsed" in text


def test_smoke_keyboard_interrupt_shutdown(dummy_sdl_env: None) -> None:
    buf = io.StringIO()

    def _interrupt(_iteration: int) -> None:
        raise KeyboardInterrupt

    summary = run_input_diagnostics(
        DiagnosticsOptions(no_window=True, poll_sleep_s=0.0),
        stream=buf,
        on_poll=_interrupt,
    )
    assert summary.reason == "interrupt"
    assert summary.records[-1]["reason"] == "interrupt"


def test_console_script_subprocess_smoke() -> None:
    env = dict(os.environ)
    env["SDL_VIDEODRIVER"] = "dummy"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "harddaysengine.cli.input_diagnostics",
            "--no-window",
            "--duration",
            "0.02",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "input diagnostics" in proc.stdout
    assert "SESSION END reason=duration_elapsed" in proc.stdout


def _drive_startup_inputs(sdl: _VirtualSdl2, inst: int, iteration: int) -> None:
    if iteration == 1:
        sdl.set_axis(inst, 0, 22000)
        sdl.set_button(inst, 0, 1)
        sdl.set_hat(inst, 0, 1)
        pygame.event.post(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_a, "mod": 0}))
        pygame.event.post(
            pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"button": 1, "pos": (10, 20)})
        )
    elif iteration == 2:
        pygame.event.post(pygame.event.Event(pygame.QUIT, {}))


def _find_virtual_instance_id() -> int | None:
    for idx in range(pygame.joystick.get_count()):
        joy = pygame.joystick.Joystick(idx)
        if "Virtual" in joy.get_name():
            return joy.get_instance_id()
    return None


def _events_for_device(
    records: tuple[dict[str, object], ...], names: tuple[str, ...], inst_id: object
) -> list[dict[str, object]]:
    return [
        r
        for r in records
        if r["record_type"] == "event"
        and r["event"] in names
        and isinstance(r["device"], dict)
        and r["device"].get("instance_id") == inst_id
    ]


def test_virtual_controller_startup_and_input_order(dummy_sdl_env: None) -> None:
    sdl = _load_virtual_sdl2()
    if sdl is None:
        pytest.skip("SDL2 virtual joystick API unavailable on this platform")

    pygame.display.init()
    pygame.joystick.init()
    try:
        dev_idx = sdl.attach()
        session = InputDiagnosticsSession(DiagnosticsOptions(no_window=True, poll_sleep_s=0.0))
        start_recs = session.start()
        discovered = [r for r in start_recs if r["record_type"] == "device_discovered"]
        virt = next(r for r in discovered if r.get("device_index") == dev_idx)
        raw_inst = virt["instance_id"]
        assert isinstance(raw_inst, int)
        assert virt["origin"] == "startup"
        assert virt["is_controller"] is True
        assert virt["capabilities"] == {"axes": 6, "buttons": 11, "hats": 1, "balls": 0}
        assert "guid" in virt
        assert "controller_mapping" in virt

        for it in range(3):
            _drive_startup_inputs(sdl, raw_inst, it)
            session.poll_once()
            if session.stop_reason is not None:
                break
        end_rec = session.finish(session.stop_reason or "quit")
        assert end_rec["reason"] == "quit"

        names = [str(r["event"]) for r in session.records if r["record_type"] == "event"]
        for expected in (
            "controller_axis_motion",
            "joy_axis_motion",
            "controller_button_down",
            "joy_button_down",
            "joy_hat_motion",
            "key_down",
            "mouse_button_down",
        ):
            assert expected in names
        assert names.index("key_down") < names.index("mouse_button_down")
        assert [r["seq"] for r in session.records] == list(range(1, len(session.records) + 1))
    finally:
        sdl.detach_all()


def test_virtual_controller_hotplug_while_running(dummy_sdl_env: None) -> None:
    sdl = _load_virtual_sdl2()
    if sdl is None:
        pytest.skip("SDL2 virtual joystick API unavailable on this platform")

    state: dict[str, Any] = {"dev_idx": None}
    buf = io.StringIO()

    def _on_poll(iteration: int) -> None:
        if iteration == 1:
            state["dev_idx"] = sdl.attach()
        elif iteration == 3 and state["dev_idx"] is not None:
            inst = _find_virtual_instance_id()
            if inst is not None:
                sdl.set_button(inst, 0, 1)
        elif iteration == 5 and state["dev_idx"] is not None:
            sdl.detach(int(state["dev_idx"]))
            state["dev_idx"] = None
        elif iteration >= 7:
            pygame.event.post(pygame.event.Event(pygame.QUIT, {}))

    try:
        summary = run_input_diagnostics(
            DiagnosticsOptions(no_window=True, poll_sleep_s=0.0),
            stream=buf,
            on_poll=_on_poll,
        )
    finally:
        sdl.detach_all()

    assert summary.reason == "quit"
    discovered = [
        r
        for r in summary.records
        if r["record_type"] == "device_discovered" and r.get("origin") == "hotplug"
    ]
    assert len(discovered) == 1
    inst_id = discovered[0]["instance_id"]
    name = discovered[0]["name"]
    guid = discovered[0].get("guid")

    add_events = _events_for_device(
        summary.records, ("controller_device_added", "joy_device_added"), inst_id
    )
    assert len(add_events) == 2
    assert [e["fields"]["already_open"] for e in add_events if isinstance(e["fields"], dict)] == [
        False,
        True,
    ]
    btn_events = _events_for_device(
        summary.records, ("controller_button_down", "joy_button_down"), inst_id
    )
    assert len(btn_events) >= 2

    rem_events = _events_for_device(
        summary.records, ("controller_device_removed", "joy_device_removed"), inst_id
    )
    assert len(rem_events) == 2
    for ev in rem_events:
        dev = ev["device"]
        assert isinstance(dev, dict)
        assert dev["name"] == name
        assert dev.get("guid") == guid

    removed_recs = [r for r in summary.records if r["record_type"] == "device_removed"]
    assert len(removed_recs) == 1
    assert removed_recs[0]["instance_id"] == inst_id
    assert removed_recs[0]["name"] == name
    assert "DEVICE+ (hotplug)" in buf.getvalue()
    assert "DEVICE-" in buf.getvalue()
