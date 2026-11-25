"""Unit tests for F12 and crash runtime debug state snapshots (ARCHITECTURE.md §13.14, ADR-073)."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pygame
import pytest

from harddaysengine.app.audio import AudioEngine
from harddaysengine.app.campaign_menu import CampaignMenu
from harddaysengine.app.config import ConfigError, resolve_debug_settings
from harddaysengine.app.debug_dump import (
    DEBUG_DUMP_SCHEMA_VERSION,
    RECENT_INPUT_LIMIT,
    TOAST_DURATION_NS,
    DebugDumpContext,
    DebugDumpController,
    DebugDumpSettings,
    build_debug_dump,
    write_debug_dump,
)
from harddaysengine.app.game import (
    GameScene,
    GameSession,
    _pump_game_events,
    run_game_window,
)
from harddaysengine.app.hd_host import HdHostPresenter
from harddaysengine.app.input import ControlMode, InputFrame, InputRouter, PlayerInput
from harddaysengine.app.loop import RefreshPacer
from harddaysengine.app.window import WindowController
from harddaysengine.cli.main import build_parser
from harddaysengine.render.presentation import DisplaySettings, FullscreenMode
from harddaysengine.sim.dice_house import DicePhase, DiceState
from harddaysengine.sim.meeting import MeetingPhase, MeetingState
from harddaysengine.sim.overworld import Lair
from harddaysengine.sim.service_screen import Phase as ServicePhase, ServiceState
from harddaysengine.sim.service_screen_types import ServiceKind
from harddaysengine.sim.services import Donation
from harddaysengine.sim.sheet_types import SheetState
from harddaysengine.sim.stonehenge import StonePhase, StoneState
from harddaysengine.sim.towns import TownAction, TownEntry, TownState
from harddaysengine.sim.turn_rules import TextLine
from harddaysengine.sim.types import KnightId
from harddaysengine.sim.valley import ValleyPhase, ValleyState
from harddaysengine.sim.wizard import WizardPhase, WizardState

if TYPE_CHECKING:
    from pathlib import Path

    import moderngl


class _StubBackend:
    def __init__(self) -> None:
        self.size = (960, 768)

    def set_windowed(self, size: tuple[int, int]) -> None:
        self.size = size

    def set_desktop_fullscreen(self) -> None:
        pass

    def set_exclusive_fullscreen(self) -> None:
        pass

    def get_window_size(self) -> tuple[int, int]:
        return self.size

    def get_refresh_rate(self) -> int:
        return 60


class _StubJoystick:
    def __init__(
        self,
        *,
        name: str = "Test Pad",
        guid: str = "030000005e040000ea02000000000000",
        fail_guid: bool = False,
    ) -> None:
        self._name = name
        self._guid = guid
        self._fail_guid = fail_guid

    def get_name(self) -> str:
        return self._name

    def get_guid(self) -> str:
        if self._fail_guid:
            raise pygame.error("guid unavailable")
        return self._guid

    def get_numaxes(self) -> int:
        return 6

    def get_numbuttons(self) -> int:
        return 12

    def get_numhats(self) -> int:
        return 1


def _fixed_now() -> datetime:
    return datetime(2026, 11, 25, 1, 23, 45, tzinfo=UTC)


def test_build_debug_dump_partial_startup_context() -> None:
    payload = build_debug_dump(DebugDumpContext(), trigger="manual", now_utc=_fixed_now())
    assert payload["schema_version"] == DEBUG_DUMP_SCHEMA_VERSION
    assert payload["contains_adf_data"] is False
    assert payload["trigger"] == "manual"
    assert payload["captured_at_utc"] == "2026-11-25T01:23:45Z"
    assert payload["session"] is None
    assert "crash" not in payload
    host = payload["host"]
    assert isinstance(host, dict)
    assert host["disks"] == {"loaded": False, "adf_image_count": 0, "program_mog_sha256": None}


def test_build_debug_dump_full_context_and_strict_no_adf_allowlist() -> None:
    session = GameSession(seed=42, players=(KnightId.RED, KnightId.BLUE))
    session.scene = GameScene.OVERWORLD
    # Plant sentinel ADF-derived strings/filenames/palettes that MUST NEVER appear in dumps.
    session.overworld.names = [
        "SECRET_KNIGHT_0",
        "SECRET_KNIGHT_1",
        "SECRET_KNIGHT_2",
        "SECRET_KNIGHT_3",
    ]
    session.overworld.lairs = [
        Lair(handler=0x18, creatures_left=3, map_x=100, map_y=120, region=0, arena="secret_gl1.t")
    ]
    session.palette_fx.set_palette([0xABC] * 32)
    audio = AudioEngine(headless=True)
    audio._current_music_title = "SECRET_MODULE_TITLE"
    window = WindowController((960, 768), FullscreenMode.OFF, backend=_StubBackend())
    router = InputRouter(control_mode=ControlMode.AUTO)
    router._joysticks[7] = (0, cast("pygame.joystick.JoystickType", _StubJoystick()))
    pacer = RefreshPacer(refresh_hz=100, vsync=True)
    fake_gl = cast(
        "moderngl.Context",
        SimpleNamespace(
            info={
                "GL_VENDOR": "TestVendor",
                "GL_RENDERER": "TestGPU",
                "GL_VERSION": "3.3.0 Core",
            }
        ),
    )

    ctx = DebugDumpContext(
        session=session,
        display=DisplaySettings(),
        window=window,
        router=router,
        pacer=pacer,
        audio=audio,
        hd_host=HdHostPresenter(None),
        campaign_menu=CampaignMenu(None),
        gl_ctx=fake_gl,
        adf_image_count=3,
        recent_inputs=[InputFrame(key=69).to_dict()],
    )
    before_hash = session.state_hash()
    payload = build_debug_dump(ctx, trigger="hotkey_f12", now_utc=_fixed_now())
    assert session.state_hash() == before_hash

    serialized = json.dumps(payload, sort_keys=True)
    for forbidden in (
        "SECRET_KNIGHT_0",
        "SECRET_KNIGHT_1",
        "SECRET_MODULE_TITLE",
        "secret_gl1.t",
        str(0xABC),
    ):
        assert forbidden not in serialized

    env = payload["environment"]
    assert isinstance(env, dict)
    assert env["gl"] == {
        "vendor": "TestVendor",
        "renderer": "TestGPU",
        "version": "3.3.0 Core",
    }
    host = payload["host"]
    assert isinstance(host, dict)
    assert host["audio"] == {
        "headless": True,
        "mixer_initialized": False,
        "sample_rate": audio.paula.rate,
        "is_music_playing": False,
        "synth_attached": False,
        "banks_loaded": False,
    }
    inp = host["input"]
    assert isinstance(inp, dict)
    assert inp["active_control_mode"] == "shared-pad"
    assert inp["connected_gamepads"] == [
        {
            "slot": 0,
            "instance_id": 7,
            "name": "Test Pad",
            "guid": "030000005e040000ea02000000000000",
            "axes": 6,
            "buttons": 12,
            "hats": 1,
        }
    ]
    sess = payload["session"]
    assert isinstance(sess, dict)
    assert sess["state_hash"] == before_hash
    assert sess["scene"] == {"id": int(GameScene.OVERWORLD), "name": "OVERWORLD"}
    world = sess["overworld"]
    assert isinstance(world, dict)
    assert "names" not in world
    assert world["lairs"][0]["handler"] == 0x18
    assert "arena" not in world["lairs"][0]


def _subscreen(session: GameSession) -> dict[str, object]:
    snap = build_debug_dump(DebugDumpContext(session=session), trigger="manual")
    sess = snap["session"]
    assert isinstance(sess, dict)
    sub = sess["active_subscreen"]
    assert isinstance(sub, dict)
    return sub


def test_subscreen_summaries_strip_decoded_text_lines_and_hover() -> None:
    session = GameSession(seed=7)
    secret_lines = (TextLine("SECRET_DIALOGUE_TEXT", 10, 20, 0),)

    # Sheet screen with hover text
    session.sheet_screen = cast(
        "object", SimpleNamespace(state=SheetState(number=9, knight=1, hover="SECRET_HOVER_LABEL"))
    )
    snap = build_debug_dump(DebugDumpContext(session=session), trigger="manual")
    assert "SECRET_HOVER_LABEL" not in json.dumps(snap)
    assert _subscreen(session)["kind"] == "sheet"
    session.sheet_screen = None

    # Service screen with greeting lines
    session.service_screen = cast(
        "object",
        SimpleNamespace(
            state=ServiceState(
                kind=ServiceKind.HEALER,
                knight=1,
                pointer_x=50,
                pointer_y=60,
                lines=secret_lines,
                phase=ServicePhase.DONATION,
                donation=Donation(purse=100, amount=15),
            )
        ),
    )
    snap = build_debug_dump(DebugDumpContext(session=session), trigger="manual")
    assert "SECRET_DIALOGUE_TEXT" not in json.dumps(snap)
    assert _subscreen(session)["donation"] == 15
    session.service_screen = None

    # Dice screen with lines
    session.dice_screen = cast(
        "object",
        SimpleNamespace(
            run=SimpleNamespace(
                state=DiceState(
                    knight=1,
                    pointer_x=10,
                    pointer_y=20,
                    phase=DicePhase.ROUND,
                    bet=25,
                    dice=(3, 5),
                    lines=secret_lines,
                )
            )
        ),
    )
    snap = build_debug_dump(DebugDumpContext(session=session), trigger="manual")
    assert "SECRET_DIALOGUE_TEXT" not in json.dumps(snap)
    assert _subscreen(session)["bet"] == 25
    session.dice_screen = None

    # Town, Stonehenge, Wizard, Valley, Meeting, Ending numeric summaries
    session.town_screen = cast(
        "object",
        SimpleNamespace(state=TownState(True, 1, 12, 34, TownAction.SMITH, TownEntry.READY)),
    )
    assert _subscreen(session)["kind"] == "town"
    session.town_screen = None

    session.stone_screen = cast(
        "object",
        SimpleNamespace(
            state=StoneState(knight=1, matched=True, ending=0x11, phase=StonePhase.RITUAL)
        ),
    )
    assert _subscreen(session)["kind"] == "stonehenge"
    session.stone_screen = None

    session.wizard_screen = cast(
        "object",
        SimpleNamespace(
            state=WizardState(knight=1, kind=0, loading_index=2, phase=WizardPhase.GIFT)
        ),
    )
    assert _subscreen(session)["kind"] == "wizard"
    session.wizard_screen = None

    session.valley_screen = cast(
        "object",
        SimpleNamespace(
            state=ValleyState(knight=1, matched=True, loading_index=1, phase=ValleyPhase.VICTORY)
        ),
    )
    assert _subscreen(session)["kind"] == "valley"
    session.valley_screen = None

    session.meeting_screen = cast(
        "object",
        SimpleNamespace(
            state=MeetingState(first=1, second=2, phase=MeetingPhase.PROTECT),
            lines=secret_lines,
        ),
    )
    snap = build_debug_dump(DebugDumpContext(session=session), trigger="manual")
    assert "SECRET_DIALOGUE_TEXT" not in json.dumps(snap)
    assert _subscreen(session)["kind"] == "meeting"
    session.meeting_screen = None

    session.ending_screen = cast(
        "object", SimpleNamespace(run=SimpleNamespace(step_index=4, tick=99, stage=2, done=False))
    )
    assert _subscreen(session) == {
        "kind": "ending",
        "step": 4,
        "tick": 99,
        "stage": 2,
        "done": False,
    }


def test_f12_pre_router_consumption_caption_toast_and_hash_invariance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now_ns = 1_000_000_000
    log_buf = io.StringIO()
    dumper = DebugDumpController(
        DebugDumpSettings(dump_dir=tmp_path / "dumps"),
        stream=log_buf,
        clock_ns=lambda: now_ns,
    )
    session = GameSession(seed=99)
    session.last_status = "ORIGINAL STATUS"
    before_hash = session.state_hash()
    window = WindowController((960, 768), FullscreenMode.OFF, backend=_StubBackend())
    router = InputRouter()
    pacer = RefreshPacer(refresh_hz=60, vsync=True)
    campaign_menu = CampaignMenu(tmp_path / "campaign-save.json")

    # F12 press, repeat, and release are consumed; normal key passes through.
    assert dumper.consume_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F12, repeat=0))
    assert dumper.pending_manual_dump is True
    assert dumper.consume_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F12, repeat=1))
    assert dumper.consume_event(pygame.event.Event(pygame.KEYUP, key=pygame.K_F12))
    assert not dumper.consume_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE))

    # Ring buffer stays bounded at RECENT_INPUT_LIMIT.
    for idx in range(RECENT_INPUT_LIMIT + 15):
        dumper.record_input(InputFrame(key=idx))
    recent = dumper.recent_inputs()
    assert len(recent) == RECENT_INPUT_LIMIT
    assert recent[0]["key"] == 15
    assert recent[-1]["key"] == RECENT_INPUT_LIMIT + 14

    written = dumper.flush_manual_dump(
        DebugDumpContext(session=session, window=window, router=router)
    )
    assert written is not None
    assert written.is_file()
    assert written.name.startswith("debug-dump-")
    assert not dumper.pending_manual_dump
    assert session.state_hash() == before_hash
    assert session.last_status == "ORIGINAL STATUS"
    assert dumper.caption_notice() == f"DUMP SAVED: {written.name}"
    assert "Debug snapshot saved:" in log_buf.getvalue()

    # Notice expires cleanly after TOAST_DURATION_NS.
    now_ns += TOAST_DURATION_NS
    assert dumper.caption_notice() is None

    # Pump F12 through _pump_game_events even while campaign menu is open.
    campaign_menu.consume_event(
        pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F8, repeat=0),
        available=True,
        can_save=True,
    )
    assert campaign_menu.is_open

    def _fake_events(*_args: object, **_kwargs: object) -> list[pygame.event.Event]:
        return [
            pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F12, repeat=0),
            pygame.event.Event(pygame.KEYUP, key=pygame.K_F12),
        ]

    monkeypatch.setattr(pygame.event, "get", _fake_events)
    _pump_game_events(window, router, pacer, campaign_menu, session, debug_dumper=dumper)
    assert dumper.pending_manual_dump is True
    assert router.sample_frame() == InputFrame(
        players={kid: PlayerInput() for kid in KnightId}, quit_requested=False, key=0
    )


def test_write_debug_dump_atomic_lf_and_collision_suffixes(tmp_path: Path) -> None:
    dump_dir = tmp_path / "nested" / "debug-dumps"
    payload = build_debug_dump(DebugDumpContext(), trigger="hotkey_f12", now_utc=_fixed_now())
    p1 = write_debug_dump(dump_dir, payload, trigger="hotkey_f12", tick=12, now_utc=_fixed_now())
    p2 = write_debug_dump(dump_dir, payload, trigger="hotkey_f12", tick=12, now_utc=_fixed_now())
    p3 = write_debug_dump(dump_dir, payload, trigger="hotkey_f12", tick=12, now_utc=_fixed_now())

    assert p1.name == "debug-dump-20261125T012345Z-tick12.json"
    assert p2.name == "debug-dump-20261125T012345Z-tick12-2.json"
    assert p3.name == "debug-dump-20261125T012345Z-tick12-3.json"
    raw = p1.read_bytes()
    assert b"\r\n" not in raw
    assert json.loads(raw.decode("utf-8"))["schema_version"] == 1
    assert list(dump_dir.glob("*.tmp")) == []


def test_crash_dump_writes_traceback_and_never_masks_exceptions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dump_dir = tmp_path / "crashes"
    log_buf = io.StringIO()
    dumper = DebugDumpController(DebugDumpSettings(dump_dir=dump_dir), stream=log_buf)
    try:
        raise ValueError("synthetic failure")
    except ValueError as err:
        written = dumper.write_crash_dump(DebugDumpContext(session=GameSession()), err)

    assert written is not None
    assert written.name.startswith("crash-dump-")
    data = json.loads(written.read_text(encoding="utf-8"))
    assert data["trigger"] == "crash"
    assert data["contains_adf_data"] is False
    assert data["crash"]["exception_type"] == "ValueError"
    assert data["crash"]["exception_message"] == "synthetic failure"
    assert any("synthetic failure" in line for line in data["crash"]["traceback"])

    # Disabled crash dumps write nothing.
    disabled_dir = tmp_path / "disabled"
    disabled = DebugDumpController(DebugDumpSettings(dump_dir=disabled_dir, crash_dumps=False))
    assert disabled.write_crash_dump(DebugDumpContext(), RuntimeError("ignored")) is None
    assert not disabled_dir.exists()

    # Unwritable path (regular file used as dir) never raises from crash dumper or manual dump.
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("file", encoding="utf-8")
    bad = DebugDumpController(DebugDumpSettings(dump_dir=blocker), stream=io.StringIO())
    assert bad.write_crash_dump(DebugDumpContext(), RuntimeError("orig")) is None
    bad.consume_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F12, repeat=0))
    assert bad.flush_manual_dump(DebugDumpContext()) is None
    assert bad.caption_notice() == "DUMP FAILED"

    # End-to-end run_game_window crash capture re-raises original error.
    def _boom(_adf_dir: Path) -> None:
        raise RuntimeError("startup boom")

    monkeypatch.setattr("harddaysengine.app.game._open_optional_disk_set", _boom)
    e2e_dir = tmp_path / "e2e_crash"
    with pytest.raises(RuntimeError, match="startup boom"):
        run_game_window(
            tmp_path,
            debug_settings=DebugDumpSettings(dump_dir=e2e_dir, crash_dumps=True),
        )
    dumps = list(e2e_dir.glob("crash-dump-*.json"))
    assert len(dumps) == 1
    e2e_data = json.loads(dumps[0].read_text(encoding="utf-8"))
    assert e2e_data["crash"]["exception_type"] == "RuntimeError"
    assert e2e_data["crash"]["exception_message"] == "startup boom"


def test_resolve_debug_settings_precedence_validation_and_cli(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    # Default without config file
    default_s = resolve_debug_settings(cfg, environ={})
    assert default_s == DebugDumpSettings(dump_dir=tmp_path / "debug-dumps", crash_dumps=True)

    # Config file relative dump_dir + crash_dumps = false
    cfg.write_text('[debug]\ndump_dir = "custom-dumps"\ncrash_dumps = false\n', encoding="utf-8")
    from_cfg = resolve_debug_settings(cfg, environ={})
    assert from_cfg == DebugDumpSettings(dump_dir=tmp_path / "custom-dumps", crash_dumps=False)

    # Env overrides config; CLI overrides env
    from_env = resolve_debug_settings(
        cfg,
        environ={"HARDDAYSENGINE_DEBUG_DUMP_DIR": str(tmp_path / "env-dumps")},
    )
    assert from_env.dump_dir == tmp_path / "env-dumps"
    from_cli = resolve_debug_settings(
        cfg,
        cli_dump_dir=str(tmp_path / "cli-dumps"),
        crash_dumps=True,
        environ={"HARDDAYSENGINE_DEBUG_DUMP_DIR": str(tmp_path / "env-dumps")},
    )
    assert from_cli == DebugDumpSettings(dump_dir=tmp_path / "cli-dumps", crash_dumps=True)

    # Strict validation errors
    cfg.write_text("[debug]\nunknown = 1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown"):
        resolve_debug_settings(cfg, environ={})

    cfg.write_text('debug = "bad"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="table"):
        resolve_debug_settings(cfg, environ={})

    cfg.write_text("[debug]\ndump_dir = 123\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="dump_dir"):
        resolve_debug_settings(cfg, environ={})

    # CLI flags parse cleanly
    parsed = build_parser().parse_args(["--debug-dump-dir", "out/dumps", "--no-crash-dumps"])
    assert parsed.debug_dump_dir == "out/dumps"
    assert parsed.crash_dumps is False


def test_connected_gamepads_info_handles_unavailable_sdl_fields() -> None:
    router = InputRouter()
    router._joysticks[5] = (
        1,
        cast("pygame.joystick.JoystickType", _StubJoystick(name="Pad B", fail_guid=True)),
    )
    router._joysticks[2] = (0, cast("pygame.joystick.JoystickType", _StubJoystick(name="Pad A")))
    info = router.connected_gamepads_info()
    assert [entry["slot"] for entry in info] == [0, 1]
    assert "guid" in info[0]
    assert "guid" not in info[1]
