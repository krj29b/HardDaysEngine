"""Real Mudmen seeded outcomes, original death branch and replay (§10.25)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from pymoonstone.app.fight_audit import build_snatcher_audit
from pymoonstone.app.fight_runtime import FightLoader, FightRuntime
from pymoonstone.app.scene_assets import GameAssetBundle
from pymoonstone.assets.adf import DiskSet
from pymoonstone.assets.fight_palette import MogVersionError
from pymoonstone.assets.hunk import parse_hunk_executable
from pymoonstone.assets.snatcher_rules import parse_snatcher_rules
from pymoonstone.sim.fight import FIGHTER_DIED, Fight, fighter_died
from pymoonstone.sim.fight_ai.snatcher import GONE
from pymoonstone.sim.m68k import s16

if TYPE_CHECKING:
    from pathlib import Path

    from pymoonstone.sim.fight_script import RoutineCall

pytestmark = pytest.mark.adf


@pytest.fixture(scope="module")
def bundle(adf_dir: Path) -> GameAssetBundle:
    return GameAssetBundle(DiskSet.from_directory(adf_dir))


def _play(bundle: GameAssetBundle, *, defend: bool) -> tuple[Fight, list[str], list[int]]:
    audit = build_snatcher_audit(bundle)
    f = audit.scene.fight
    hashes, deaths = [], []

    def died(fight: Fight, call: RoutineCall | None) -> None:
        assert call is not None and call.owner == audit.opponent
        deaths.append(s16(fight.actor(call.owner).hp))
        fighter_died(fight, call)

    f.routines[FIGHTER_DIED] = died
    runtime = FightRuntime(audit.scene, 1)
    for frame in range(1000):
        f.ports[0] = audit.input_word() if defend else 0
        audit.scene.advance(frame * 6)
        hashes.append(
            hashlib.sha256(json.dumps(runtime.snapshot(), sort_keys=True).encode()).hexdigest()
        )
        if not f.fight_active:
            break
    assert not f.fight_active
    return f, hashes, deaths


def test_seeded_win_follows_hurt_script_death_and_replays(bundle: GameAssetBundle) -> None:
    fight, hashes, deaths = _play(bundle, defend=True)
    assert s16(fight.actor(1).hp) > 0 and fight.total == fight.alive_now == 0
    assert len(deaths) == 1 and deaths[0] <= 0
    assert hashes == _play(bundle, defend=True)[1]


def test_idle_knight_is_dragged_under(bundle: GameAssetBundle) -> None:
    fight, _, deaths = _play(bundle, defend=False)
    assert s16(fight.actor(1).hp) <= 0 and fight.snatch_flags & GONE
    assert deaths == []


def test_decoded_scripts_and_new_encounter_flag_reset(bundle: GameAssetBundle) -> None:
    loader = FightLoader(bundle)
    arena = next(a for a in loader.defs.arenas if a.name == "mudmen")
    assert loader.arena_for(arena.handler_offsets[0]) == arena
    first, second = build_snatcher_audit(bundle), build_snatcher_audit(bundle)
    first.scene.fight.snatch_flags = GONE
    assert second.scene.fight.snatch_flags == 0
    assert "snatch_flags" in FightRuntime(second.scene, 1).snapshot()


def test_wrong_handler_hunk_instruction_is_rejected(bundle: GameAssetBundle) -> None:
    executable = parse_hunk_executable("mog", bundle.disk_set.read_file("mog"))
    sections = list(executable.sections)
    section = sections[40]
    sections[40] = replace(section, data=b"\x00\x00" + section.data[2:])
    with pytest.raises(MogVersionError, match="entry instruction"):
        parse_snatcher_rules(replace(executable, sections=tuple(sections)))
