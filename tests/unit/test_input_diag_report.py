"""Tests for T16.3: JSON Lines reports, coalescing, bounded output, and partial recovery."""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING

import pygame
import pytest

from harddaysengine.app.input_diag_events import (
    DeviceIdentity,
    format_terminal_record,
    normalize_pygame_event,
)
from harddaysengine.app.input_diag_report import CoalesceConfig, MotionCoalescer
from harddaysengine.app.input_diagnostics import DiagnosticsOptions, run_input_diagnostics
from harddaysengine.cli.input_diagnostics import main

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def dummy_sdl_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")


def test_raw_axis_coalescing_preserves_thresholds_and_extrema() -> None:
    coalescer = MotionCoalescer(CoalesceConfig(coalesce_ms=50, axis_epsilon=0.02))
    ident = DeviceIdentity(kind="gamepad", instance_id=0, name="Pad", is_controller=True)
    emitted: list[dict[str, object]] = []

    # 100 micro-jitter samples in center band
    for idx in range(100):
        val = 0.005 if idx % 2 == 0 else -0.008
        if idx == 0:
            val = 0.01
        elif idx == 99:
            val = -0.004
        ev = normalize_pygame_event(
            pygame.event.Event(pygame.JOYAXISMOTION, {"instance_id": 0, "axis": 0, "value": val}),
            device=ident,
        )
        assert ev is not None
        emitted.extend(coalescer.push(ev, elapsed_ms=10 + idx))

    # Still buffered because jitter stayed within axis_epsilon (< 0.02) and < 1000 ms
    assert emitted == []

    # Threshold crossing into pos (>= 0.5) must flush prior center burst first
    cross_pos = normalize_pygame_event(
        pygame.event.Event(pygame.JOYAXISMOTION, {"instance_id": 0, "axis": 0, "value": 0.55}),
        device=ident,
    )
    assert cross_pos is not None
    emitted.extend(coalescer.push(cross_pos, elapsed_ms=120))
    assert len(emitted) == 2

    burst = emitted[0]
    assert burst["coalesced"] is True
    assert burst["sample_count"] == 100
    bf = burst["fields"]
    assert isinstance(bf, dict)
    assert bf["first_value"] == 0.01
    assert bf["last_value"] == -0.004
    assert bf["min_value"] == -0.008
    assert bf["max_value"] == 0.01

    crossing = emitted[1]
    assert "coalesced" not in crossing
    cf = crossing["fields"]
    assert isinstance(cf, dict)
    assert cf["value"] == 0.55
    assert cf["band"] == "pos"
    assert cf["threshold_crossed"] is True

    # Multiple samples inside pos, then return to center
    for idx, val in enumerate((0.62, 0.91, 0.78)):
        ev = normalize_pygame_event(
            pygame.event.Event(pygame.JOYAXISMOTION, {"instance_id": 0, "axis": 0, "value": val}),
            device=ident,
        )
        assert ev is not None
        emitted.extend(coalescer.push(ev, elapsed_ms=130 + idx * 5))

    back_center = normalize_pygame_event(
        pygame.event.Event(pygame.JOYAXISMOTION, {"instance_id": 0, "axis": 0, "value": 0.1}),
        device=ident,
    )
    assert back_center is not None
    emitted.extend(coalescer.push(back_center, elapsed_ms=150))
    assert len(emitted) == 4
    pos_burst = emitted[2]
    assert pos_burst["coalesced"] is True
    assert pos_burst["sample_count"] == 3
    pbf = pos_burst["fields"]
    assert isinstance(pbf, dict)
    assert (pbf["first_value"], pbf["last_value"], pbf["min_value"], pbf["max_value"]) == (
        0.62,
        0.78,
        0.62,
        0.91,
    )
    back_fields = emitted[3]["fields"]
    assert isinstance(back_fields, dict)
    assert back_fields["band"] == "center"
    assert back_fields["threshold_crossed"] is True
    assert coalescer.coalesced_raw_events == (100 - 1) + (3 - 1)


def test_controller_axis_threshold_and_raw_mode() -> None:
    ident = DeviceIdentity(kind="gamepad", instance_id=1, name="Xbox", is_controller=True)
    coalescer = MotionCoalescer(CoalesceConfig(coalesce_ms=50, axis_epsilon=0.02))
    out: list[dict[str, object]] = []
    for t, val in ((0, 100), (10, 200), (20, 20000)):
        ev = normalize_pygame_event(
            pygame.event.Event(
                pygame.CONTROLLERAXISMOTION, {"instance_id": 1, "axis": 0, "value": val}
            ),
            device=ident,
        )
        assert ev is not None
        out.extend(coalescer.push(ev, elapsed_ms=t))
    assert len(out) == 2
    assert out[0]["coalesced"] is True
    crossing_fields = out[1]["fields"]
    assert isinstance(crossing_fields, dict)
    assert crossing_fields["threshold_crossed"] is True


def test_controller_axis_raw_mode_emits_every_sample() -> None:
    ident = DeviceIdentity(kind="gamepad", instance_id=1, name="Xbox", is_controller=True)
    raw_coalescer = MotionCoalescer(CoalesceConfig(coalesce_ms=50, axis_epsilon=0.02, raw=True))
    emitted: list[dict[str, object]] = []
    for t, val in ((0, 100), (5, 110), (10, 120)):
        ev = normalize_pygame_event(
            pygame.event.Event(
                pygame.CONTROLLERAXISMOTION, {"instance_id": 1, "axis": 0, "value": val}
            ),
            device=ident,
        )
        assert ev is not None
        emitted.extend(raw_coalescer.push(ev, elapsed_ms=t))
    assert len(emitted) == 3
    assert all("coalesced" not in rec for rec in emitted)
    assert raw_coalescer.coalesced_raw_events == 0


def test_mouse_motion_coalescing_preserves_causal_order_before_click() -> None:
    coalescer = MotionCoalescer(CoalesceConfig(coalesce_ms=50))
    emitted: list[dict[str, object]] = []
    for step in range(40):
        ev = normalize_pygame_event(
            pygame.event.Event(
                pygame.MOUSEMOTION,
                {"pos": (10 + step, 20 + step * 2), "rel": (1, 2), "buttons": (0, 0, 0)},
            )
        )
        assert ev is not None
        emitted.extend(coalescer.push(ev, elapsed_ms=10 + step))
    assert emitted == []

    click = normalize_pygame_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"button": 1, "pos": (49, 98)})
    )
    assert click is not None
    emitted.extend(coalescer.push(click, elapsed_ms=52))
    assert len(emitted) == 2
    assert emitted[0]["event"] == "mouse_motion"
    assert emitted[0]["coalesced"] is True
    assert emitted[0]["sample_count"] == 40
    mf = emitted[0]["fields"]
    assert isinstance(mf, dict)
    assert mf["first_pos"] == [10, 20]
    assert mf["last_pos"] == [49, 98]
    assert mf["rel"] == [40, 80]
    assert emitted[1]["event"] == "mouse_button_down"


def test_terminal_rendering_for_threshold_and_coalesced_events() -> None:
    thresh_lines = format_terminal_record(
        {
            "record_type": "event",
            "seq": 3,
            "elapsed_ms": 120,
            "category": "joystick",
            "event": "joy_axis_motion",
            "device": {"kind": "gamepad", "instance_id": 0},
            "fields": {"axis": 0, "value": 0.55, "band": "pos", "threshold_crossed": True},
        }
    )
    assert len(thresh_lines) == 1
    assert "[threshold]" in thresh_lines[0]
    assert "threshold_crossed=" not in thresh_lines[0]

    coal_lines = format_terminal_record(
        {
            "record_type": "event",
            "seq": 4,
            "elapsed_ms": 180,
            "category": "mouse",
            "event": "mouse_motion",
            "device": {"kind": "mouse"},
            "coalesced": True,
            "sample_count": 25,
            "fields": {
                "pos": [50, 60],
                "first_pos": [10, 20],
                "last_pos": [50, 60],
                "rel": [40, 40],
            },
        }
    )
    assert len(coal_lines) == 1
    assert "(x25 coalesced)" in coal_lines[0]
    assert "first_pos=[10,20]" in coal_lines[0]


def test_jsonl_report_bounded_output_under_noisy_motion(
    dummy_sdl_env: None, tmp_path: Path
) -> None:
    report_path = tmp_path / "noisy.jsonl"
    buf = io.StringIO()

    def _flood(iteration: int) -> None:
        if iteration == 0:
            for step in range(80):
                pygame.event.post(
                    pygame.event.Event(
                        pygame.MOUSEMOTION,
                        {"pos": (step, step), "rel": (1, 1), "buttons": (0, 0, 0)},
                    )
                )
            pygame.event.post(
                pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_RETURN, "mod": 0})
            )
        elif iteration == 1:
            pygame.event.post(pygame.event.Event(pygame.QUIT, {}))

    summary = run_input_diagnostics(
        DiagnosticsOptions(
            no_window=True,
            poll_sleep_s=0.0,
            output_path=report_path,
            coalesce_ms=500,
        ),
        stream=buf,
        on_poll=_flood,
    )
    assert summary.reason == "quit"
    assert summary.truncated is False
    assert summary.coalesced_raw_events == 79

    raw_bytes = report_path.read_bytes()
    assert b"\r\n" not in raw_bytes
    lines = raw_bytes.decode("utf-8").splitlines()
    parsed = [json.loads(line) for line in lines]
    assert len(parsed) == summary.total_emitted_records
    assert parsed[0]["record_type"] == "session_start"
    assert parsed[0]["schema_version"] == 1
    assert parsed[0]["config"]["coalesce_ms"] == 500
    assert parsed[-1]["record_type"] == "session_end"
    assert parsed[-1]["coalesced_raw_events"] == 79
    assert [r["seq"] for r in parsed] == list(range(1, len(parsed) + 1))

    ev_names = [r["event"] for r in parsed if r["record_type"] == "event"]
    assert ev_names.index("mouse_motion") < ev_names.index("key_down")
    assert "(x80 coalesced)" in buf.getvalue()


def test_max_records_hard_bound_truncates_cleanly(dummy_sdl_env: None, tmp_path: Path) -> None:
    report_path = tmp_path / "bounded.jsonl"
    buf = io.StringIO()

    def _spam_keys(iteration: int) -> None:
        if iteration == 0:
            for _ in range(40):
                pygame.event.post(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_a, "mod": 0}))

    summary = run_input_diagnostics(
        DiagnosticsOptions(
            no_window=True,
            poll_sleep_s=0.0,
            output_path=report_path,
            max_records=6,
            raw=True,
        ),
        stream=buf,
        on_poll=_spam_keys,
    )
    assert summary.reason == "max_records_reached"
    assert summary.truncated is True
    assert summary.total_emitted_records == 6

    parsed = [json.loads(line) for line in report_path.read_text(encoding="utf-8").splitlines()]
    assert len(parsed) == 6
    assert parsed[0]["record_type"] == "session_start"
    assert parsed[-1]["record_type"] == "session_end"
    assert parsed[-1]["truncated"] is True
    assert parsed[-1]["reason"] == "max_records_reached"


def test_partial_report_is_flushed_and_recoverable_on_crash(
    dummy_sdl_env: None, tmp_path: Path
) -> None:
    report_path = tmp_path / "partial.jsonl"
    buf = io.StringIO()

    def _crash_mid_run(iteration: int) -> None:
        if iteration == 0:
            pygame.event.post(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_x, "mod": 0}))
        elif iteration == 1:
            raise RuntimeError("simulated hard interruption")

    with pytest.raises(RuntimeError, match="simulated hard interruption"):
        run_input_diagnostics(
            DiagnosticsOptions(no_window=True, poll_sleep_s=0.0, output_path=report_path),
            stream=buf,
            on_poll=_crash_mid_run,
        )

    lines = report_path.read_text(encoding="utf-8").splitlines()
    parsed = [json.loads(line) for line in lines]
    assert len(parsed) >= 2
    assert parsed[0]["record_type"] == "session_start"
    assert any(r.get("event") == "key_down" for r in parsed)


def test_cli_output_option_validation_and_file_error(
    dummy_sdl_env: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_file = tmp_path / "cli_report.jsonl"
    assert main(["--output", str(out_file), "--no-window", "--duration", "0.02"]) == 0
    lines = out_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 2
    assert json.loads(lines[0])["record_type"] == "session_start"
    assert json.loads(lines[-1])["record_type"] == "session_end"

    # Passing an existing directory as --output triggers an OSError handled with exit code 2
    assert main(["--output", str(tmp_path), "--no-window", "--duration", "0.02"]) == 2
    assert "Error writing diagnostic report" in capsys.readouterr().err

    for bad_args in (
        ["--max-records", "0"],
        ["--coalesce-ms", "-1"],
        ["--axis-epsilon", "-0.1"],
    ):
        with pytest.raises(SystemExit) as exc:
            main(bad_args)
        assert exc.value.code == 2


def test_virtual_controller_end_to_end_jsonl_report(dummy_sdl_env: None, tmp_path: Path) -> None:
    from tests.unit.test_input_diagnostics import _find_virtual_instance_id, _load_virtual_sdl2

    sdl = _load_virtual_sdl2()
    if sdl is None:
        pytest.skip("SDL2 virtual joystick API unavailable on this platform")

    report_path = tmp_path / "virtual_report.jsonl"
    state: dict[str, int | None] = {"dev_idx": None}
    buf = io.StringIO()

    def _drive(iteration: int) -> None:
        if iteration == 1:
            state["dev_idx"] = sdl.attach()
        elif iteration == 3:
            inst = _find_virtual_instance_id()
            if inst is not None:
                sdl.set_axis(inst, 0, 500)
                sdl.set_axis(inst, 0, 24000)
                sdl.set_button(inst, 0, 1)
                sdl.set_hat(inst, 0, 1)
        elif iteration == 5 and state["dev_idx"] is not None:
            sdl.detach(state["dev_idx"])
            state["dev_idx"] = None
        elif iteration >= 7:
            pygame.event.post(pygame.event.Event(pygame.QUIT, {}))

    try:
        summary = run_input_diagnostics(
            DiagnosticsOptions(
                no_window=True,
                poll_sleep_s=0.0,
                output_path=report_path,
            ),
            stream=buf,
            on_poll=_drive,
        )
    finally:
        sdl.detach_all()

    assert summary.reason == "quit"
    parsed = [json.loads(line) for line in report_path.read_text(encoding="utf-8").splitlines()]
    assert parsed[0]["record_type"] == "session_start"
    assert parsed[-1]["record_type"] == "session_end"
    assert any(r["record_type"] == "device_discovered" and r["origin"] == "hotplug" for r in parsed)
    assert any(r["record_type"] == "device_removed" for r in parsed)
    ev_names = {r["event"] for r in parsed if r["record_type"] == "event"}
    assert {
        "controller_axis_motion",
        "joy_axis_motion",
        "controller_button_down",
        "joy_hat_motion",
    } <= ev_names
    assert any(
        r["record_type"] == "event"
        and isinstance(r.get("fields"), dict)
        and r["fields"].get("threshold_crossed") is True
        for r in parsed
    )
