"""Persistent host clock and snapshots on synthetic scripts (ARCHITECTURE.md §10.22)."""

from __future__ import annotations

import json

from harddaysengine.app.fight_runtime import FightRuntime
from harddaysengine.sim.fight import end_fight
from harddaysengine.sim.fight_setup import FightWarnings
from tests.unit.fight_setup_data import SETUP
from tests.unit.test_fight_scene import _scene


def test_clock_preserves_job_until_six_vbls_and_samples_the_latest_ports() -> None:
    runtime = FightRuntime(_scene(), 1)
    job = runtime.fight.find_job(1)
    assert job is not None
    for vbl in range(1, 6):
        assert runtime.step_vbl(vbl, [1, 0]) is None
        assert job.script == 2
    output = runtime.step_vbl(6, [2, 16])
    assert output is not None and len(output.tick.draws) == 1
    assert job.script == 10
    assert runtime.fight.ports == [2, 16]


def test_end_delay_is_counted_in_fight_frames_and_stops_at_zero() -> None:
    runtime = FightRuntime(_scene(), 1)
    runtime.fight.fight_active = True
    end_fight(runtime.fight)
    assert runtime.fight.end_timer == 10
    for vbl in range(1, 60):
        runtime.step_vbl(vbl, [0, 0])
        assert not runtime.finished
    runtime.step_vbl(60, [0, 0])
    assert runtime.finished
    final = json.dumps(runtime.snapshot(), sort_keys=True)
    assert runtime.step_vbl(61, [1, 1]) is None
    assert json.dumps(runtime.snapshot(), sort_keys=True) == final


def test_snapshot_includes_loop_work_script_position_and_rng() -> None:
    runtime = FightRuntime(_scene(), 1)
    job = runtime.fight.find_job(1)
    assert job is not None
    first = json.dumps(runtime.snapshot(), sort_keys=True)
    job.work.loop_count = 9
    loop = json.dumps(runtime.snapshot(), sort_keys=True)
    assert loop != first
    job.script += 2
    script = json.dumps(runtime.snapshot(), sort_keys=True)
    assert script != loop
    runtime.fight.rng.draw()
    assert json.dumps(runtime.snapshot(), sort_keys=True) != script


def _configured() -> FightRuntime:
    runtime = FightRuntime(_scene(), 1, rules=SETUP, warnings=FightWarnings([0] * 32))
    runtime.fight.first = 1
    runtime.fight.fight_active = True
    runtime.fight.actor(1).hp = 10
    return runtime


def test_negative_hp_starts_separate_delay_and_decrements_on_that_same_frame() -> None:
    runtime = _configured()
    runtime.fight.actor(1).hp = 0xFFFF
    for vbl in range(1, 4):
        assert runtime.step_vbl(vbl, [0, 0]) is None
        assert runtime.fight.fight_active
    assert runtime.step_vbl(4, [0, 0]) is not None
    assert not runtime.fight.fight_active and runtime.fight.end_timer == 6
    for vbl in range(5, 28):
        runtime.step_vbl(vbl, [0, 0])
        assert not runtime.finished
    runtime.step_vbl(28, [0, 0])
    assert runtime.finished


def test_zero_hp_alone_does_not_end_the_fight() -> None:
    runtime = _configured()
    runtime.fight.actor(1).hp = 0
    for vbl in range(1, 13):
        runtime.step_vbl(vbl, [0, 0])
    assert runtime.fight.fight_active and not runtime.finished


def test_zero_inactive_timer_wraps_through_256_frames() -> None:
    runtime = _configured()
    runtime.fight.fight_active = False
    runtime.fight.end_timer = 0
    for vbl in range(1, 5):
        runtime.step_vbl(vbl, [0, 0])
    assert runtime.fight.end_timer == 255 and not runtime.finished
    for vbl in range(5, 1024):
        runtime.step_vbl(vbl, [0, 0])
    assert not runtime.finished
    runtime.step_vbl(1024, [0, 0])
    assert runtime.finished


def test_warning_registration_follows_frame_and_ramps_on_vbls_without_render_calls() -> None:
    runtime = _configured()
    runtime.fight.actor(1).hp = 9
    for vbl in range(1, 5):
        runtime.step_vbl(vbl, [0, 0])
    assert runtime.warnings is not None and len(runtime.warnings.ramps) == 3
    assert runtime.warnings.colors[6] == 0
    runtime.step_vbl(5, [0, 0])
    runtime.step_vbl(6, [0, 0])
    assert runtime.warnings.colors[6] == 0x111
    assert all(c == 0 for i, c in enumerate(runtime.warnings.colors) if i not in (6, 7, 8))
    snapshot = json.dumps(runtime.snapshot(), sort_keys=True)
    runtime.warnings.step(SETUP)
    assert json.dumps(runtime.snapshot(), sort_keys=True) != snapshot


def test_release_frees_jobs_and_ramps_but_retains_final_pixels() -> None:
    runtime = _configured()
    runtime.fight.actor(1).hp = 9
    for vbl in range(1, 5):
        runtime.step_vbl(vbl, [0, 0])
    pixels = runtime.scene.canvas.pixels()
    runtime.release()
    assert all(not job.active for job in runtime.fight.engine.jobs)
    assert runtime.warnings is not None and not runtime.warnings.ramps
    assert (runtime.scene.canvas.pixels() == pixels).all()


def test_guardian_death_fade_advances_on_pal_ticks_and_is_in_snapshot() -> None:
    runtime = _configured()
    assert runtime.warnings is not None
    runtime.warnings.colors[:] = [0x333] * 32
    runtime.fight.guardian.fade = True
    runtime.step_vbl(1, [0, 0])
    assert runtime.warnings.colors[2:8] == [0x222] * 6
    assert runtime.warnings.colors[:2] == [0x333] * 2
    assert runtime.warnings.colors[8:] == [0x333] * 24
    assert runtime.snapshot()["guardian"] == {
        "main": 0,
        "body": 0,
        "target": 0,
        "stage": 0,
        "holding": False,
        "fade": True,
    }
