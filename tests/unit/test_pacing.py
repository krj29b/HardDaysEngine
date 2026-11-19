"""Pure unit tests for refresh pacing schedules and 50 Hz mode selection (T13.4, §13.6)."""

from __future__ import annotations

import pytest

from harddaysengine.app.audio import AudioEngine
from harddaysengine.app.game import GameSession
from harddaysengine.app.input import InputFrame, PlayerInput
from harddaysengine.app.loop import (
    TICK_NS,
    PacingHistogram,
    RefreshPacer,
    multiple_of_50_hz,
)
from harddaysengine.app.window import select_50hz_display_mode
from harddaysengine.sim.types import KnightId


@pytest.mark.parametrize(
    ("hz", "expected"),
    [
        (50, 1),
        (100, 2),
        (150, 3),
        (200, 4),
        (0, 0),
        (-50, 0),
        (60, 0),
        (75, 0),
        (120, 0),
        (144, 0),
    ],
)
def test_multiple_of_50_hz(hz: int, expected: int) -> None:
    assert multiple_of_50_hz(hz) == expected


@pytest.mark.parametrize("refresh_hz", [50, 100, 150, 200])
def test_50hz_multiples_show_every_tick_for_equal_refreshes(refresh_hz: int) -> None:
    expected_n = refresh_hz // 50
    pacer = RefreshPacer(refresh_hz=refresh_hz, vsync=True)
    assert pacer.lockstep_refreshes == expected_n
    hist = PacingHistogram()

    period_ns = 1_000_000_000 // refresh_hz
    jitter_pattern = (-400_000, 350_000, -150_000, 500_000, -300_000)
    total_ticks = 100
    for idx in range(total_ticks * expected_n):
        delta = period_ns + jitter_pattern[idx % len(jitter_pattern)]
        step = pacer.step(delta)
        assert step.should_present
        hist.record_present(pacer.accumulator.total_ticks)

    assert pacer.accumulator.total_ticks == total_ticks
    assert hist.distinct_ticks_shown == total_ticks
    assert hist.total_presents == total_ticks * expected_n
    assert hist.refreshes_per_tick() == {expected_n: total_ticks}


def test_60hz_vsync_shows_each_tick_for_one_or_two_refreshes() -> None:
    pacer = RefreshPacer(refresh_hz=60, vsync=True)
    assert pacer.lockstep_refreshes == 0
    hist = PacingHistogram()

    # 600 ideal 60 Hz refreshes = 10 seconds = 500 ticks
    for i in range(600):
        delta = 16_666_667 if (i % 3) != 0 else 16_666_666
        step = pacer.step(delta)
        assert step.should_present
        hist.record_present(pacer.accumulator.total_ticks)

    counts = hist.refreshes_per_tick()
    assert set(counts.keys()) == {1, 2}
    assert pacer.accumulator.total_ticks == 500


def test_vsync_off_presents_once_per_tick() -> None:
    pacer = RefreshPacer(refresh_hz=60, vsync=False)
    assert pacer.lockstep_refreshes == 0
    hist = PacingHistogram()

    # Poll every 2 ms for 2.0 seconds (1000 polls -> 100 ticks)
    for _ in range(1000):
        step = pacer.step(2_000_000)
        if step.should_present:
            hist.record_present(pacer.accumulator.total_ticks)

    assert pacer.accumulator.total_ticks == 100
    # Initial frame (tick 0) + 100 completed ticks, each shown exactly once
    assert hist.refreshes_per_tick() == {1: 101}


def test_lockstep_recovers_from_stall_and_updates_on_refresh_change() -> None:
    pacer = RefreshPacer(refresh_hz=100, vsync=True)
    # 60 ms stall catches up 3 ticks
    stall = pacer.step(TICK_NS * 3)
    assert stall.ticks == 3
    assert stall.should_present

    # Switching from 100 Hz to 60 Hz disables lockstep; switching to 50 Hz re-enables it
    pacer.set_refresh_rate(60)
    assert pacer.lockstep_refreshes == 0
    pacer.set_refresh_rate(50)
    assert pacer.lockstep_refreshes == 1


def test_select_50hz_display_mode() -> None:
    modes = [
        (1920, 1080, 60),
        (1920, 1080, 50),
        (1280, 720, 50),
        (2560, 1440, 144),
    ]
    assert select_50hz_display_mode(modes, (1920, 1080)) == (1920, 1080, 50)
    assert select_50hz_display_mode(modes, (1600, 900)) == (1920, 1080, 50)
    assert select_50hz_display_mode([(1920, 1080, 60), (1280, 720, 40)], (1920, 1080)) is None


def test_replay_hash_identical_across_all_pacing_schedules() -> None:
    target_ticks = 60
    inputs = [
        InputFrame(players={KnightId.RED: PlayerInput(confirm=(i == 2))})
        for i in range(target_ticks)
    ]

    hashes: list[str] = []
    configs: list[tuple[int, bool, int]] = [
        (50, True, 20_000_000),
        (100, True, 10_000_000),
        (150, True, 6_666_667),
        (200, True, 5_000_000),
        (60, True, 16_666_667),
        (60, False, 2_000_000),
    ]
    for hz, vsync, delta_ns in configs:
        audio = AudioEngine(headless=True)
        try:
            session = GameSession(seed=0x2468ACE1, audio=audio)
            pacer = RefreshPacer(refresh_hz=hz, vsync=vsync)
            idx = 0
            while idx < target_ticks:
                step = pacer.step(delta_ns)
                for _ in range(step.ticks):
                    if idx < target_ticks:
                        session.step_tick(inputs[idx])
                        idx += 1
            hashes.append(session.state_hash())
        finally:
            audio.close()

    assert len(set(hashes)) == 1
