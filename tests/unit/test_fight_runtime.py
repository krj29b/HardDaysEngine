"""Persistent host clock and snapshots on synthetic scripts (ARCHITECTURE.md §10.22)."""

from __future__ import annotations

import json

from pymoonstone.app.fight_runtime import FightRuntime
from pymoonstone.sim.fight import end_fight
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
