"""The ending player on synthetic plans: frame loops, callbacks, pairs, wipe, tweaks (§12.39.10)."""

from __future__ import annotations

from harddaysengine.sim.ending import SCREEN, WORK
from harddaysengine.sim.ending_types import (
    Call,
    Caption,
    Cell,
    Flash,
    Invoke,
    Load,
    Pairs,
    Spawn,
    SpawnKind,
    StageTable,
    Store,
    Ticks,
)
from harddaysengine.sim.turn_rules import TextLine
from tests.unit.ending_setup import finish, plan, run

_TWO_FRAMES = bytes([0, 1, 2, 0, 0, 5, 0xFF, 0, 0, 2, 0xFE, 0x10, 0, 3, 0xFF, 0xFF])
_STAGED = (Load(), StageTable(3))


def test_frame_loop_runs_until_the_finished_job_sets_stop() -> None:
    steps = (*_STAGED, Store(Cell.SPEED, 3), Spawn(SpawnKind.LEFT, 0), Invoke(Call.FRAMES))
    ending, canvas = run(plan(steps, _TWO_FRAMES))
    assert finish(ending) == 6
    assert canvas.named("draw") == [
        ("draw", WORK, 0, 1, 165, 102, False),
        ("draw", 0, 0, 2, 163, 98, False),
        ("draw", WORK, 0, 2, 163, 98, False),
    ]
    assert canvas.calls[1:4] == [("flip",), ("copy", 0, WORK), ("draw", 0, 0, 2, 163, 98, False)]
    assert ending.cells[Cell.STOP] == 1 and not any(job.active for job in ending.pool.jobs)
    assert ending.palette.regs == list(range(32))


def test_right_spawns_are_mirrored_by_the_frame_width() -> None:
    steps = (*_STAGED, Spawn(SpawnKind.RIGHT, 0), Invoke(Call.FRAMES))
    ending, canvas = run(plan(steps, _TWO_FRAMES))
    finish(ending)
    assert canvas.named("draw")[0] == ("draw", WORK, 0, 1, 120 - 5 - 20, 102, True)


def test_flash_callback_waits_inside_the_job_pass() -> None:
    script = bytes([0xB4, 0, 0, 0, 0x0E, 0xC4, 0xFF, 0xFF])
    white = (0xFFF,) * 32
    steps = (*_STAGED, Invoke(Call.STAGE), Spawn(SpawnKind.LEFT, 0), Invoke(Call.FRAMES))
    ending, _ = run(
        plan(steps, script, relocs=((2, 0),), callbacks=(((0, 0xEC4), Flash(white, 3)),))
    )
    seen = []
    while not ending.advance():
        seen.append(tuple(ending.palette.regs))
    assert seen[:3] == [white] * 3
    assert ending.palette.regs == list(range(32)) and ending.tick == 3


def test_pairs_alternate_sides_and_run_the_counted_frames() -> None:
    script = bytes([0, 1, 0, 0, 0, 0, 0xFF, 0xFF])
    steps = (*_STAGED, Store(Cell.SPEED, 1), Pairs((0, 0)))
    ending, canvas = run(plan(steps, script))
    assert finish(ending) == 4
    draws = canvas.named("draw")
    assert [(x, mirrored) for _, _, _, _, x, _, mirrored in draws] == [(160, False), (100, True)]
    assert ending.toggle == 0 and len(canvas.named("flip")) == 4


def test_bare_ticks_flip_and_pace_without_a_stop_test() -> None:
    steps = (*_STAGED, Store(Cell.SPEED, 2), Store(Cell.STOP, 1), Ticks(3))
    ending, canvas = run(plan(steps))
    assert finish(ending) == 6 and len(canvas.named("flip")) == 3


def test_reverse_wipe_runs_down_to_its_floor_and_keeps_the_last_screen() -> None:
    steps = (*_STAGED, Invoke(Call.LOADING_A), Invoke(Call.LOADING_B))
    ending, canvas = run(plan(steps))
    assert finish(ending) == 89 * 2
    progress = [call[2] for call in canvas.named("wipe")]
    assert progress[:3] == [1000, 991, 982] and progress[-1] == 199 and len(progress) == 90
    assert canvas.calls[-2:] == [("copy", SCREEN, WORK), ("copy", SCREEN, 0)]


def test_load_applies_the_first_matching_moon_and_class_rows() -> None:
    ending, _ = run(plan((Load(),)), flags=0x80 | 0x20 | 0x10 | 0x02 | 0x01)
    finish(ending)
    table = ending.tables[3]
    assert table[12] == 0x0F80 and table[8:12] == (1, 2, 3, 4)
    assert ending.ramp_colours == [0x0C50, 0x0920, 0x0700]
    assert table[13] == 13 and table[0] == 0


def test_caption_uses_the_message_cell_with_the_recoloured_entries() -> None:
    lines = (TextLine("x", 0, 90, 1),)
    ending, canvas = run(plan((Caption(lines),)))
    assert finish(ending) == 32
    assert canvas.calls == [("clear", SCREEN), ("message", SCREEN), ("text", SCREEN, 1)]
    expected = [index << 4 for index in range(16)] + [0] * 16
    expected[1:3] = [0x800, 0x600]
    assert ending.palette.live == expected and ending.target == SCREEN
