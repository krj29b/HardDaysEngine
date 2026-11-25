"""On-demand (F12) and crash runtime state snapshots with strict no-ADF-data allowlist (§13.14).

Implements ADR-073: captures read-only engine environment, host display/pacing/audio/input
telemetry, a bounded rolling window of recent ``InputFrame``s, non-reversible ``program``/``mog``
SHA-256 disk fingerprint, and strictly numeric/enum simulation state. Never embeds raw ``.adf``
bytes, disk asset filenames, decoded executable strings/tables, disk palettes, pixels, or PCM.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import time
import traceback
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING

import pygame

from harddaysengine import __version__
from harddaysengine.app.campaign_save import disk_signature
from harddaysengine.app.input_diag_events import SDL_ENV_KEYS, SDL_ENV_PREFIXES

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from typing import TextIO

    import moderngl

    from harddaysengine.app.audio import AudioEngine
    from harddaysengine.app.campaign_menu import CampaignMenu
    from harddaysengine.app.fight_runtime import FightRuntime
    from harddaysengine.app.game import ActiveEncounter, GameSession
    from harddaysengine.app.hd_host import HdHostPresenter
    from harddaysengine.app.input import InputFrame, InputRouter
    from harddaysengine.app.loop import RefreshPacer
    from harddaysengine.app.window import WindowController
    from harddaysengine.render.presentation import DisplaySettings
    from harddaysengine.sim.actor import Actor
    from harddaysengine.sim.fight_script import Job
    from harddaysengine.sim.overworld import Lair, OverworldState

DEBUG_DUMP_SCHEMA_VERSION = 1
DEFAULT_DUMP_DIR_NAME = "debug-dumps"
RECENT_INPUT_LIMIT = 60
TOAST_DURATION_NS = 3_000_000_000


@dataclass(slots=True, frozen=True)
class DebugDumpSettings:
    """Resolved runtime debug dump configuration (§13.14.2)."""

    dump_dir: Path
    crash_dumps: bool = True


@dataclass(slots=True)
class DebugDumpContext:
    """Read-only references to live host components for snapshot capture (§13.14)."""

    session: GameSession | None = None
    display: DisplaySettings | None = None
    window: WindowController | None = None
    router: InputRouter | None = None
    pacer: RefreshPacer | None = None
    audio: AudioEngine | None = None
    hd_host: HdHostPresenter | None = None
    campaign_menu: CampaignMenu | None = None
    gl_ctx: moderngl.Context | None = None
    adf_image_count: int = 0
    replay_active: bool = False
    recording_active: bool = False
    recorded_frame_count: int = 0
    recent_inputs: Sequence[dict[str, object]] = ()


def _safe_str(fn: Callable[[], str]) -> str | None:
    try:
        value = fn()
    except (pygame.error, AttributeError, NotImplementedError, RuntimeError):
        return None
    return value if value else None


def _collect_sdl_env() -> dict[str, str]:
    return {
        key: os.environ[key]
        for key in sorted(os.environ)
        if key in SDL_ENV_KEYS or key.startswith(SDL_ENV_PREFIXES)
    }


def _gl_info(gl_ctx: moderngl.Context | None) -> dict[str, str] | None:
    if gl_ctx is None:
        return None
    try:
        info = gl_ctx.info
    except Exception:
        return None
    out: dict[str, str] = {}
    for dst, src in (
        ("vendor", "GL_VENDOR"),
        ("renderer", "GL_RENDERER"),
        ("version", "GL_VERSION"),
    ):
        raw = info.get(src)
        if isinstance(raw, str) and raw:
            out[dst] = raw
    return out or None


def _environment_snapshot(gl_ctx: moderngl.Context | None) -> dict[str, object]:
    env: dict[str, object] = {
        "engine_version": __version__,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "sys_platform": sys.platform,
        "pygame_version": pygame.version.ver,
        "sdl_version": list(pygame.get_sdl_version()),
        "sdl_env": _collect_sdl_env(),
    }
    if pygame.display.get_init():
        driver = _safe_str(pygame.display.get_driver)
        if driver is not None:
            env["video_driver"] = driver
    gl = _gl_info(gl_ctx)
    if gl is not None:
        env["gl"] = gl
    return env


def _current_window_size(window: WindowController) -> list[int]:
    w, h = window.windowed_size
    if pygame.display.get_init():
        try:
            cur_w, cur_h = pygame.display.get_window_size()
            if cur_w > 0 and cur_h > 0:
                w, h = cur_w, cur_h
        except pygame.error:
            pass
    return [w, h]


def _display_snapshot(ctx: DebugDumpContext) -> dict[str, object] | None:
    if ctx.display is None:
        return None
    window = ctx.window
    effective = window.apply_display_settings(ctx.display) if window is not None else ctx.display
    window_size = _current_window_size(window) if window is not None else None
    refresh_hz = window.get_refresh_rate() if window is not None else 0
    preview_open = window is not None and window.display_preview is not None
    return {
        "scale": effective.scale,
        "filter": effective.filter.value,
        "scale_mode": effective.scale_mode.value,
        "aspect": effective.aspect.value,
        "crop": effective.crop.value,
        "fullscreen": effective.fullscreen.value,
        "vsync": effective.vsync,
        "surround": effective.surround.mode.value,
        "hd_mode": effective.hd_mode.value,
        "hd_pack_configured": effective.hd_pack_dir is not None,
        "hd_active": (
            effective.hd_enabled and ctx.hd_host is not None and ctx.hd_host.pack is not None
        ),
        "window_size": window_size,
        "refresh_hz": refresh_hz,
        "display_preview_open": preview_open,
        "campaign_menu_open": ctx.campaign_menu is not None and ctx.campaign_menu.is_open,
    }


def _pacing_snapshot(pacer: RefreshPacer | None) -> dict[str, object] | None:
    if pacer is None:
        return None
    return {
        "refresh_hz": pacer.refresh_hz,
        "vsync": pacer.vsync,
        "lockstep_refreshes": pacer.lockstep_refreshes,
        "total_ticks": pacer.accumulator.total_ticks,
        "alpha_permille": pacer.accumulator.alpha_permille,
    }


def _audio_snapshot(audio: AudioEngine | None) -> dict[str, object] | None:
    if audio is None:
        return None
    return {
        "headless": audio.headless,
        "mixer_initialized": audio.mixer_initialized,
        "sample_rate": audio.paula.rate,
        "is_music_playing": audio.is_music_playing,
        "synth_attached": audio.synth is not None,
        "banks_loaded": audio.banks is not None,
    }


def _input_snapshot(ctx: DebugDumpContext) -> dict[str, object]:
    out: dict[str, object] = {
        "replay_active": ctx.replay_active,
        "recording_active": ctx.recording_active,
        "recorded_frame_count": ctx.recorded_frame_count,
        "recent_inputs": list(ctx.recent_inputs),
    }
    if ctx.router is not None:
        out["configured_control_mode"] = ctx.router.configured_control_mode.value
        out["active_control_mode"] = ctx.router.active_control_mode.value
        out["connected_gamepads"] = ctx.router.connected_gamepads_info()
    if ctx.session is not None:
        targets = ctx.session.control_targets()
        out["control_targets"] = {
            "primary": int(targets.primary),
            "duel_opponent": (
                None if targets.duel_opponent is None else int(targets.duel_opponent)
            ),
        }
    return out


def _disks_snapshot(ctx: DebugDumpContext) -> dict[str, object]:
    disk_set = ctx.session.disk_set if ctx.session is not None else None
    signature: str | None = None
    if disk_set is not None:
        try:
            signature = disk_signature(disk_set)
        except Exception:
            signature = None
    return {
        "loaded": disk_set is not None,
        "adf_image_count": ctx.adf_image_count,
        "program_mog_sha256": signature,
    }


def _host_snapshot(ctx: DebugDumpContext) -> dict[str, object]:
    return {
        "display": _display_snapshot(ctx),
        "pacing": _pacing_snapshot(ctx.pacer),
        "audio": _audio_snapshot(ctx.audio),
        "input": _input_snapshot(ctx),
        "disks": _disks_snapshot(ctx),
    }


def _actor_summary(index: int, actor: Actor) -> dict[str, int | bool]:
    """Allowlisted numeric-only actor state (no script offsets or decoded names)."""
    return {
        "index": index,
        "active": actor.active,
        "kind": actor.kind,
        "type": actor.type,
        "x": actor.x,
        "y": actor.y,
        "height": actor.height,
        "facing": actor.facing,
        "map_x": actor.map_x,
        "map_y": actor.map_y,
        "tile_x": actor.tile_x,
        "tile_y": actor.tile_y,
        "hp": actor.hp,
        "hp_max": actor.hp_max,
        "lives": actor.lives,
        "gold": actor.gold,
        "strength": actor.strength,
        "constitution": actor.constitution,
        "endurance": actor.endurance,
        "derived_endurance": actor.derived_endurance,
        "armour": actor.armour,
        "sword": actor.sword,
        "daggers": actor.daggers,
        "progress": actor.progress,
        "frog_days": actor.frog_days,
        "recency": actor.recency,
        "action": actor.action,
        "anim_phase": actor.anim_phase,
        "anim_timer": actor.anim_timer,
        "input_port": actor.input_port,
        "input": actor.input,
        "hit_target": actor.hit_target,
        "attacker": actor.attacker,
        "engaged_with": actor.engaged_with,
        "block_latched": actor.block_latched,
        "cooldown": actor.cooldown,
        "life_loss": actor.life_loss,
    }


def _job_summary(slot: int, job: Job) -> dict[str, int | bool]:
    """Allowlisted numeric-only job state (no cel filenames or script pointers)."""
    return {
        "slot": slot,
        "active": job.active,
        "running": job.running,
        "paused": job.paused,
        "owner": job.owner,
        "type": job.type,
        "x": job.x,
        "depth": job.depth,
        "height": job.height,
        "screen_x": job.screen_x,
        "screen_y": job.screen_y,
        "width": job.width,
        "frame_height": job.frame_height,
        "facing": job.facing,
        "attack_count": len(job.attack),
        "hurt_count": len(job.hurt),
        "motion_active": job.work.motion_active,
        "loop_active": job.work.loop_active,
    }


def _lair_summary(index: int, lair: Lair) -> dict[str, object]:
    """Allowlisted numeric-only lair record (excludes ``arena`` disk filename)."""
    return {
        "index": index,
        "handler": lair.handler,
        "creatures_left": lair.creatures_left,
        "map_x": lair.map_x,
        "map_y": lair.map_y,
        "region": lair.region,
        "gold": lair.gold,
        "loot_counts": list(lair.loot.counts),
    }


def _overworld_summary(world: OverworldState) -> dict[str, object]:
    """Allowlisted numeric overworld state (excludes ``names`` and ``arrivals`` labels)."""
    return {
        "turn_index": world.turn_index,
        "turn_order": [int(k) for k in world.turn_order],
        "day": world.day,
        "moon_index": world.moon_index,
        "moon_frame": world.moon_frame,
        "moon_subcount": world.moon_subcount,
        "total_turns": world.total_turns,
        "claimed_moonstones_mask": world.claimed_moonstones_mask,
        "budget": world.budget,
        "spent": world.spent,
        "move": world.move,
        "terrain_counter": world.terrain_counter,
        "blocked": world.blocked,
        "travel": world.travel,
        "ambush": world.ambush,
        "saved_x": world.saved_x,
        "saved_y": world.saved_y,
        "region_code": world.region_code,
        "phase": world.phase,
        "menu": world.menu,
        "turn_over": world.turn_over,
        "dragon_active": world.dragon_active,
        "dragon_encounter": world.dragon_encounter,
        "game_over": world.game_over,
        "arrival_count": sum(1 for arr in world.arrivals if arr.kind != 0),
        "highlight_count": len(world.highlights),
        "lairs": [_lair_summary(idx, lair) for idx, lair in enumerate(world.lairs)],
    }


def _combat_summary(encounter: ActiveEncounter | None) -> dict[str, object] | None:
    """Allowlisted numeric combat state (excludes ``tables``, ``mirrored``, and filenames)."""
    if encounter is None:
        return None
    rt: FightRuntime = encounter.runtime
    fight = rt.fight
    engine = fight.engine
    trig = encounter.trigger
    active_actors = [
        _actor_summary(idx, actor) for idx, actor in enumerate(fight.actors) if actor.active
    ]
    active_jobs = [_job_summary(idx, job) for idx, job in enumerate(engine.jobs) if job.active]
    return {
        "trigger": {
            "kind": int(trig.kind),
            "kind_name": trig.kind.name,
            "knight_id": int(trig.knight_id),
            "node_id": trig.node_id,
            "day": trig.day,
            "moon_phase": int(trig.moon_phase),
            "opponent_knight_id": (
                None if trig.opponent_knight_id is None else int(trig.opponent_knight_id)
            ),
            "lair_index": trig.lair_index,
            "creature_type": trig.creature_type,
            "encounter_kind": trig.encounter_kind,
        },
        "knight_ids": [int(k) for k in encounter.knight_ids],
        "first": rt.first,
        "opponent": rt.opponent,
        "phase": rt.phase,
        "finished": rt.finished,
        "vbl": engine.vbl,
        "gore_off": engine.gore_off,
        "ai_day": fight.ai_day,
        "ai_last_action": fight.ai_last_action,
        "ports": list(fight.ports),
        "rng_state": fight.rng.state,
        "active_actors": active_actors,
        "active_jobs": active_jobs,
    }


def _subscreen_town_sheet_service(session: GameSession) -> dict[str, object] | None:
    if session.town_screen is not None:
        st = session.town_screen.state
        return {
            "kind": "town",
            "waterdeep": st.waterdeep,
            "knight": st.knight,
            "pointer_x": st.pointer_x,
            "pointer_y": st.pointer_y,
            "pending": int(st.pending),
            "entry": int(st.entry),
        }
    if session.sheet_screen is not None:
        st = session.sheet_screen.state
        return {
            "kind": "sheet",
            "number": st.number,
            "knight": st.knight,
            "other": st.other,
            "lair": st.lair,
            "previous": st.previous,
            "pointer_x": st.pointer_x,
            "pointer_y": st.pointer_y,
            "changes": st.changes,
            "last_item": st.last_item,
            "done": st.done,
            "hand_over": st.hand_over,
            "bad_luck": st.bad_luck,
            "read_only": st.read_only,
            "wait": st.wait,
            "icon_count": len(st.icons),
            "region_count": len(st.regions),
        }
    if session.service_screen is not None:
        st = session.service_screen.state
        return {
            "kind": "service",
            "service_kind": int(st.kind),
            "knight": st.knight,
            "pointer_x": st.pointer_x,
            "pointer_y": st.pointer_y,
            "phase": int(st.phase),
            "fire_held": st.fire_held,
            "wait": st.wait,
            "donation": st.donation.amount,
            "outcome": st.outcome,
        }
    return None


def _subscreen_places(session: GameSession) -> dict[str, object] | None:
    if session.dice_screen is not None:
        st = session.dice_screen.run.state
        return {
            "kind": "dice",
            "knight": st.knight,
            "pointer_x": st.pointer_x,
            "pointer_y": st.pointer_y,
            "phase": int(st.phase),
            "throw": st.throw,
            "frame_wait": st.frame_wait,
            "countdown": st.countdown,
            "bet": st.bet,
            "done": st.done,
            "dice": list(st.dice),
        }
    if session.stone_screen is not None:
        st = session.stone_screen.state
        return {
            "kind": "stonehenge",
            "knight": st.knight,
            "matched": st.matched,
            "ending": st.ending,
            "phase": int(st.phase),
            "wait": st.wait,
            "fire_held": st.fire_held,
            "has_request": st.request is not None,
        }
    if session.wizard_screen is not None:
        st = session.wizard_screen.state
        return {
            "kind": "wizard",
            "knight": st.knight,
            "actor_kind": st.kind,
            "loading_index": st.loading_index,
            "phase": int(st.phase),
            "wait": st.wait,
            "has_result": st.result is not None,
        }
    return None


def _subscreen_events(session: GameSession) -> dict[str, object] | None:
    if session.village_visit is not None:
        knight, phase, wait = session.village_visit.snapshot()
        return {"kind": "village", "knight": knight, "phase": phase, "wait": wait}
    if session.valley_screen is not None:
        st = session.valley_screen.state
        return {
            "kind": "valley",
            "knight": st.knight,
            "matched": st.matched,
            "loading_index": st.loading_index,
            "phase": int(st.phase),
            "wait": st.wait,
            "fire_held": st.fire_held,
            "won": st.won,
        }
    if session.meeting_screen is not None:
        st = session.meeting_screen.state
        return {
            "kind": "meeting",
            "first": st.first,
            "second": st.second,
            "phase": int(st.phase),
            "wait": st.wait,
            "fire_held": st.fire_held,
            "defeat": st.defeat,
            "confused": st.confused,
            "hoard": st.hoard,
        }
    if session.ending_screen is not None:
        run = session.ending_screen.run
        return {
            "kind": "ending",
            "step": run.step_index,
            "tick": run.tick,
            "stage": run.stage,
            "done": run.done,
        }
    return None


def _active_subscreen_summary(session: GameSession) -> dict[str, object] | None:
    return (
        _subscreen_town_sheet_service(session)
        or _subscreen_places(session)
        or _subscreen_events(session)
    )


def _palette_status(session: GameSession) -> dict[str, object]:
    """Operational palette-FX counters only (no disk ``$0RGB`` colour words)."""
    snap = session.palette_fx.snapshot()
    raw_cycles = snap.get("cycles")
    cycle_count = len(raw_cycles) if isinstance(raw_cycles, list) else 0
    return {
        "is_fading": session.palette_fx.is_fading,
        "is_flashing": session.palette_fx.is_flashing,
        "divisor": snap["divisor"],
        "tick": snap["tick"],
        "vbl": session.palette_fx.vbl_count,
        "cycle_count": cycle_count,
    }


def _session_snapshot(session: GameSession | None) -> dict[str, object] | None:
    if session is None:
        return None
    try:
        digest = session.state_hash()
    except Exception:
        digest = None
    actors = [_actor_summary(idx, actor) for idx, actor in enumerate(session.actors)]
    actors.append(_actor_summary(4, session.dragon_actor))
    return {
        "tick": session.tick_count,
        "scene": {"id": int(session.scene), "name": session.scene.name},
        "state_hash": digest,
        "seed": session.seed,
        "paused": session.paused,
        "players": [int(k) for k in session.players],
        "selected_player_count": session.selected_player_count,
        "menu_cursor": session.menu_cursor,
        "gore_enabled": session.gore_enabled,
        "practice_mode": session.practice_mode,
        "knight_pick": {
            "active": session.knight_pick_active,
            "slot": session.knight_pick_slot,
            "taken_mask": session.knight_taken_mask,
            "picked": [int(k) for k in session.picked_knights],
        },
        "rng_state": session.overworld.prng.state,
        "loading_cycle": session.loading_cycle,
        "last_sheet_item": session.last_sheet_item,
        "spawn_lanes_counter": session.spawn_lanes.counter,
        "bad_luck": session.bad_luck,
        "last_defeat_mask": session.last_defeat_mask,
        "palette_fx": _palette_status(session),
        "actors": actors,
        "inventories": {str(k): list(v.counts) for k, v in sorted(session.inventories.items())},
        "overworld": _overworld_summary(session.overworld),
        "combat": _combat_summary(session.active_encounter),
        "active_subscreen": _active_subscreen_summary(session),
    }


def _crash_snapshot(exc: BaseException) -> dict[str, object]:
    tb_lines = [
        line.rstrip("\r\n")
        for line in traceback.format_exception(type(exc), exc, exc.__traceback__)
    ]
    return {
        "exception_type": type(exc).__qualname__,
        "exception_message": str(exc),
        "traceback": tb_lines,
    }


def build_debug_dump(
    ctx: DebugDumpContext,
    *,
    trigger: str,
    exc: BaseException | None = None,
    now_utc: datetime | None = None,
) -> dict[str, object]:
    """Assemble a JSON-serializable, strictly no-ADF-data runtime debug snapshot (§13.14)."""
    stamp = (now_utc if now_utc is not None else datetime.now(UTC)).astimezone(UTC)
    payload: dict[str, object] = {
        "schema_version": DEBUG_DUMP_SCHEMA_VERSION,
        "contains_adf_data": False,
        "trigger": trigger,
        "captured_at_utc": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "environment": _environment_snapshot(ctx.gl_ctx),
        "host": _host_snapshot(ctx),
        "session": _session_snapshot(ctx.session),
    }
    if exc is not None:
        payload["crash"] = _crash_snapshot(exc)
    return payload


def _choose_dump_path(dump_dir: Path, prefix: str, stamp: datetime, tick: int) -> Path:
    base = f"{prefix}-{stamp.strftime('%Y%m%dT%H%M%SZ')}-tick{max(0, tick)}"
    candidate = dump_dir / f"{base}.json"
    suffix = 2
    while candidate.exists():
        candidate = dump_dir / f"{base}-{suffix}.json"
        suffix += 1
    return candidate


def write_debug_dump(
    dump_dir: Path,
    payload: Mapping[str, object],
    *,
    trigger: str,
    tick: int,
    now_utc: datetime | None = None,
) -> Path:
    """Atomically write a UTF-8 LF JSON debug snapshot into ``dump_dir`` (§13.14.1)."""
    stamp = (now_utc if now_utc is not None else datetime.now(UTC)).astimezone(UTC)
    dump_dir.mkdir(parents=True, exist_ok=True)
    prefix = "crash-dump" if trigger == "crash" else "debug-dump"
    target = _choose_dump_path(dump_dir, prefix, stamp, tick)
    encoded = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    temp_path: Path | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=dump_dir,
            prefix=f".{target.stem}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(encoded)
            handle.flush()
        temp_path.replace(target)
    except BaseException:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise
    return target


class DebugDumpController:
    """Host-only pre-router ``F12`` hotkey handler, input ring buffer, and crash dumper."""

    def __init__(
        self,
        settings: DebugDumpSettings,
        *,
        stream: TextIO | None = None,
        clock_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        self.settings = settings
        self._stream = stream
        self._clock_ns = clock_ns
        self._suppressed_keyups: set[int] = set()
        self._pending_manual = False
        self._recent_inputs: deque[dict[str, object]] = deque(maxlen=RECENT_INPUT_LIMIT)
        self._last_dump_path: Path | None = None
        self._notice_text: str | None = None
        self._notice_until_ns: int = 0

    @property
    def pending_manual_dump(self) -> bool:
        """Whether an ``F12`` capture request is waiting to be written."""
        return self._pending_manual

    @property
    def last_dump_path(self) -> Path | None:
        """Path of the most recently written snapshot, if any."""
        return self._last_dump_path

    def record_input(self, frame: InputFrame) -> None:
        """Append one sampled ``InputFrame`` to the bounded host ring buffer."""
        self._recent_inputs.append(frame.to_dict())

    def recent_inputs(self) -> list[dict[str, object]]:
        """Return a copy of the bounded recent ``InputFrame`` history."""
        return list(self._recent_inputs)

    def consume_event(self, event: pygame.event.Event) -> bool:
        """Consume ``F12`` press/repeat/release before overlay panels and ``InputRouter``."""
        etype = event.type
        if etype == pygame.KEYDOWN and int(event.key) == pygame.K_F12:
            self._suppressed_keyups.add(pygame.K_F12)
            if not bool(getattr(event, "repeat", False)):
                self._pending_manual = True
            return True
        if etype == pygame.KEYUP and int(event.key) in self._suppressed_keyups:
            self._suppressed_keyups.discard(int(event.key))
            return True
        return False

    def caption_notice(self) -> str | None:
        """Return a short-lived host-only caption notice without mutating ``GameSession``."""
        if self._notice_text is None:
            return None
        if self._clock_ns() >= self._notice_until_ns:
            self._notice_text = None
            return None
        return self._notice_text

    def _log(self, message: str) -> None:
        out = self._stream if self._stream is not None else sys.stderr
        try:
            out.write(message + "\n")
            out.flush()
        except OSError:
            pass

    def flush_manual_dump(self, ctx: DebugDumpContext) -> Path | None:
        """Write any pending ``F12`` runtime snapshot without mutating simulation state."""
        if not self._pending_manual:
            return None
        self._pending_manual = False
        ctx.recent_inputs = self.recent_inputs()
        tick = ctx.session.tick_count if ctx.session is not None else 0
        try:
            payload = build_debug_dump(ctx, trigger="hotkey_f12")
            written = write_debug_dump(
                self.settings.dump_dir, payload, trigger="hotkey_f12", tick=tick
            )
        except OSError as exc:
            self._notice_text = "DUMP FAILED"
            self._notice_until_ns = self._clock_ns() + TOAST_DURATION_NS
            self._log(f"[HardDaysEngine] Failed to write debug dump: {exc}")
            return None
        self._last_dump_path = written
        self._notice_text = f"DUMP SAVED: {written.name}"
        self._notice_until_ns = self._clock_ns() + TOAST_DURATION_NS
        self._log(f"[HardDaysEngine] Debug snapshot saved: {written}")
        return written

    def write_crash_dump(self, ctx: DebugDumpContext, exc: BaseException) -> Path | None:
        """Write an automatic crash snapshot without ever masking ``exc`` (§13.14.1)."""
        if not self.settings.crash_dumps:
            return None
        try:
            ctx.recent_inputs = self.recent_inputs()
            tick = ctx.session.tick_count if ctx.session is not None else 0
            payload = build_debug_dump(ctx, trigger="crash", exc=exc)
            written = write_debug_dump(self.settings.dump_dir, payload, trigger="crash", tick=tick)
        except Exception as dump_exc:
            self._log(f"[HardDaysEngine] Failed to write crash dump: {dump_exc}")
            return None
        self._last_dump_path = written
        self._log(f"[HardDaysEngine] Crash snapshot saved: {written}")
        return written
