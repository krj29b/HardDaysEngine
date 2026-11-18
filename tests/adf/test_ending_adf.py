"""The program ending pass decoded from the user's own disks (ARCHITECTURE.md §12.39)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harddaysengine.assets.adf import DiskSet
from harddaysengine.assets.ending_data import parse_ending_plan
from harddaysengine.assets.hunk import parse_hunk_executable
from harddaysengine.sim.ending_types import (
    Buffer,
    Call,
    Caption,
    Cell,
    EndingPlan,
    Invoke,
    Load,
    Pairs,
    Select,
    Show,
    Spawn,
    Text,
    Ticks,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.adf


def _plan(adf_dir: Path) -> tuple[EndingPlan, DiskSet]:
    disks = DiskSet.from_directory(adf_dir)
    return parse_ending_plan(parse_hunk_executable("program", disks.read_file("program"))), disks


def test_sequence_has_captions_loader_scenes_and_hand_back(adf_dir: Path) -> None:
    plan, _ = _plan(adf_dir)
    assert plan.flag_bit == 0x80
    assert isinstance(plan.steps[0], Caption) and isinstance(plan.steps[1], Load)
    assert isinstance(plan.steps[-3], Caption) and len(plan.steps[-3].lines) == 1
    assert plan.steps[-1] == Invoke(Call.FADE_OUT)
    assert plan.steps.count(Invoke(Call.FRAMES)) == 7
    assert plan.steps.count(Invoke(Call.MUSIC)) == 1
    assert [type(s) for s in plan.steps].count(Load) == 1
    pairs = [s for s in plan.steps if isinstance(s, Pairs)]
    assert len(pairs) == 1 and len(set(pairs[0].scripts)) == 1 and len(pairs[0].scripts) == 5
    assert [s for s in plan.steps if isinstance(s, Ticks)] == [Ticks(41)]
    texts = [s for s in plan.steps if isinstance(s, Text)]
    assert len(texts) == 1 and len(texts[0].lines) == 5
    assert plan.next_program.lower() == "mog"


def test_loading_wipe_and_frames_come_in_original_order(adf_dir: Path) -> None:
    plan, _ = _plan(adf_dir)
    calls = [s.call for s in plan.steps if isinstance(s, Invoke)]
    a, b = calls.index(Call.LOADING_A), calls.index(Call.LOADING_B)
    assert a < b and calls[b + 1] == Call.FRAMES
    assert calls.index(Call.MUSIC) < calls.index(Call.FRAMES)
    shows = [s.source for s in plan.steps if isinstance(s, Show)]
    assert shows[0] == Buffer.P9
    assert {s.source for s in plan.steps if isinstance(s, Select)} == {Buffer.P1, Buffer.P3}
    assert all(s.script < 12288 for s in plan.steps if isinstance(s, Spawn))


def test_loader_files_exist_and_fill_every_frame_slot(adf_dir: Path) -> None:
    plan, disks = _plan(adf_dir)
    load = plan.load
    names = [p.name for p in load.pictures] + [n for _, n in load.cels]
    names += [load.overlay, load.stile, load.music]
    assert all(disks.has_file(name) for name in names)
    assert sorted(slot for slot, _ in load.cels) == list(range(6))
    assert [p.ending_tweak for p in load.pictures].count(True) == 1
    assert len({p.buffer for p in load.pictures}) == len(load.pictures)
    assert load.cleared in {p.buffer for p in load.pictures if not p.class_tweak}
    assert load.stile_bytes == 1000


def test_palette_tweaks_ramps_and_spawn_sides_are_bounded(adf_dir: Path) -> None:
    plan, _ = _plan(adf_dir)
    assert [t.bit for t in plan.ending_tweaks] == [1, 0, 2]
    assert [t.bit for t in plan.class_tweaks] == [4, 5, 3, 6]
    colours = [c for t in plan.class_tweaks for c in t.colours]
    colours += [c for t in plan.ending_tweaks for _, c in t.entries]
    colours += [c for t in plan.ending_tweaks for c in t.ramp_colours]
    assert all(0 <= c < 4096 for c in colours)
    assert plan.class_first == 8
    assert [r.colour for r in plan.ramps] == [0, 1, 2]
    assert [r.entry for r in plan.ramps] == [e for e, _ in plan.ending_tweaks[0].entries]
    left, right = plan.spawns
    assert (left.flags, right.flags) == (1, 3)
    assert (left.depth, right.depth) == (Cell.LEFT_DEPTH, Cell.RIGHT_DEPTH)
    assert plan.pair_frames == 16
    assert [e for e, _ in plan.caption_entries] == [1, 2, 3, 4, 5]
    assert (plan.fade_period, plan.fade_wait, plan.palette_period) == (2, 36, 2)
