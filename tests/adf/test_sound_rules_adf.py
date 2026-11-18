"""Sound routines decoded from the user's ``mog`` (ARCHITECTURE.md §10.32)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harddaysengine.assets.adf import DiskSet
from harddaysengine.assets.fight_program import _ROUTINES
from harddaysengine.assets.hunk import parse_hunk_executable
from harddaysengine.assets.sound_rules import parse_sound_rules
from harddaysengine.sim.sound import (
    CounterStep,
    CycleStep,
    DrawPick,
    Emit,
    ListStep,
    SoundKind,
    SoundRules,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.adf

SEQUENCES = 168  # sfxPlay ids run 1..168 (§6.2)


@pytest.fixture(scope="module")
def rules(adf_dir: Path) -> SoundRules:
    disks = DiskSet.from_directory(adf_dir)
    return parse_sound_rules(parse_hunk_executable("mog", disks.read_file("mog")))


def _ids(rules: SoundRules) -> list[int]:
    ids: list[int] = []
    for _name, steps in rules.routines:
        for step in steps:
            if isinstance(step, DrawPick):
                ids.extend(sound for choice in step.choices for sound in choice)
            elif isinstance(step, Emit):
                ids.append(step.request.sound)
            elif isinstance(step, CycleStep):
                ids.extend(step.base + v for v in range(step.limit))
            elif isinstance(step, ListStep):
                ids.extend(w for w in step.words if w != step.terminator)
            elif isinstance(step, CounterStep):
                ids.append(step.sound)
    return ids


def test_every_sound_routine_the_scripts_name_is_decoded(rules: SoundRules) -> None:
    # The Balok grab pair is two play requests and stays with its handler rules (§10.28).
    names = {name for *_, name in _ROUTINES if "sound" in name} - {"drake_grab_sounds"}
    assert names == {name for name, _ in rules.routines}


def test_every_id_is_a_synth_sequence(rules: SoundRules) -> None:
    ids = _ids(rules)
    assert ids and all(1 <= sound <= SEQUENCES for sound in ids)


def test_draws_cover_their_masks_and_folds_repeat_the_first_id(rules: SoundRules) -> None:
    folded = 0
    for _name, steps in rules.routines:
        for step in steps:
            if isinstance(step, DrawPick):
                assert len(step.choices) == step.mask + 1
                folded += step.mask > 1 and step.choices[0] == step.choices[1]
    assert folded >= 3


def test_requests_name_valid_channels_and_cells_start_in_range(rules: SoundRules) -> None:
    emits = [s.request for _n, steps in rules.routines for s in steps if isinstance(s, Emit)]
    assert {r.kind for r in emits} == {SoundKind.FIXED, SoundKind.RELEASE}
    assert all(0 <= r.channel < 4 for r in emits)
    releases = [r for r in emits if r.kind == SoundKind.RELEASE]
    assert [r.channel for r in releases] == [0, 1]
    assert all(r.mask & (1 << r.channel) == 0 and r.mask & 0xF0 == 0 for r in releases)
    (window,) = {s.words for _n, steps in rules.routines for s in steps if isinstance(s, ListStep)}
    assert window[-1] == 0xFFFF and rules.cells.list_offset % 2 == 0
