"""Synthetic wizard jobs: persistent scripts, aliased slots and input cadence (§12.36)."""

import struct
from dataclasses import replace

import pytest

from harddaysengine.sim.fight import FightConstants
from harddaysengine.sim.fight_script import CelShape, FrameShape, ListEntry, ScriptImage
from harddaysengine.sim.rng import Rng
from harddaysengine.sim.sound import SoundKind, SoundRequest
from harddaysengine.sim.wizard import WizardEngine, open_animation
from harddaysengine.sim.wizard_types import WizardLoading, WizardRules, WizardSpawn

RULES = WizardRules(
    "tower",
    "gift",
    "cel",
    "bank",
    "font",
    (
        WizardSpawn(2, (70, 0, 90), 1, 40),
        WizardSpawn(2, (40, 0, 70), 1, 40),
        WizardSpawn(2, (80, 0, 80), 1, 40),
    ),
    (),
    6,
    2,
    (3, 5, 2),
    ((10, 20, 0),) * 3,
    2,
    7,
    (SoundRequest(SoundKind.START, 9, 2),),
    (),
    " bits",
    WizardLoading("m", "f", (), ()),
)


def engine(*, unknown: bool = False) -> WizardEngine:
    # Slot four has the same allocation as slot zero. Frame changes on each marker.
    data = bytes(2) + struct.pack(">BBbBh", 16, 0, 0, 0, 1) + b"\xff\x00"
    data += struct.pack(">BBbBh", 0, 1, 0, 0, 2) + b"\xff\x00"
    data += struct.pack(">BBI", 0x84, 3, 2)
    if unknown:
        data = bytes(2) + struct.pack(">BBI", 0xB0, 0, 0) + data[2:]
    shape = CelShape("cel", (FrameShape(4, 4, (0,) * 4),) * 2, None)
    return WizardEngine(
        ScriptImage(data, {4: "unimplemented"} if unknown else {}),
        {"cel": shape},
        FightConstants(1, 65535),
    )


def test_arrival_has_two_initial_passes_and_does_not_buffer_early_fire() -> None:
    rng = Rng(123)
    run = open_animation(RULES, engine(), 0, rng, 50)
    assert [[d.frame for d in batch] for batch in run.batches] == [[0], [1]]
    assert run.fight.engine.frame_lists[1] == ("cel",) * 5
    assert run.sounds == list(RULES.sounds) and rng.state == 123
    script = run.fight.engine.jobs[0].script
    assert not run.advance(51, True) and not run.batches and not run.sounds
    assert not run.advance(52, False) and run.fight.engine.jobs[0].script == script
    assert not run.advance(53, False)
    assert [[d.frame for d in batch] for batch in run.batches] == [[0]]
    before = run.snapshot()
    assert run.advance(54, True) is False
    assert run.advance(55, True) is False
    assert run.advance(56, True) is True
    assert not run.batches and run.fight.engine.vbl == before["vbl"]


@pytest.mark.parametrize("stage,count,wait", [(1, 2, 5), (2, 1, 2)])
def test_gift_and_farewell_begin_with_one_pass_and_fresh_pools(
    stage: int, count: int, wait: int
) -> None:
    run = open_animation(RULES, engine(), stage, Rng(1), 10)
    assert len(run.batches) == 1 and len(run.batches[0]) == count
    assert all(d.frame == 0 for d in run.batches[0])
    assert not run.sounds
    for vbl in range(11, 10 + wait):
        assert not run.advance(vbl, True)
    assert run.advance(10 + wait, True)


def test_snapshots_include_work_lists_current_output_and_random_state() -> None:
    run = open_animation(RULES, engine(), 1, Rng(1), 10)
    before = run.snapshot()
    run.fight.engine.jobs[0].work.loop_count = 17
    assert run.snapshot() != before
    before = run.snapshot()
    run.fight.engine.jobs[0].attack.append(ListEntry("cel", 0, 10, 20))
    assert run.snapshot() != before
    before = run.snapshot()
    run.fight.rng.draw()
    assert run.snapshot() != before


def test_unknown_callbacks_and_invalid_stages_fail_explicitly() -> None:
    with pytest.raises(ValueError, match="unimplemented"):
        open_animation(RULES, engine(unknown=True), 0, Rng(1), 0)
    with pytest.raises(ValueError, match="stage outside"):
        open_animation(RULES, engine(), 3, Rng(1), 0)
    moved = replace(RULES, spawns=(replace(RULES.spawns[0], position=(90, 0, 20)),))
    assert open_animation(moved, engine(), 0, Rng(1), 0).batches[0][0].x == 91
