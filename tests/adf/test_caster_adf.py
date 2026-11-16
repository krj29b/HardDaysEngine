"""Seeded Ratmen outcomes, executable reads and original death branches (§10.23)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

import pytest

from pymoonstone.app.fight_audit import build_caster_audit
from pymoonstone.app.fight_runtime import FightLoader, FightRuntime
from pymoonstone.app.scene_assets import GameAssetBundle
from pymoonstone.assets.adf import DiskSet
from pymoonstone.assets.caster_rules import parse_caster_rules
from pymoonstone.assets.fight_palette import MogVersionError
from pymoonstone.assets.hunk import parse_hunk_executable
from pymoonstone.sim.fight import FIGHTER_DIED, Fight, HandlerResult, Restart, fighter_died
from pymoonstone.sim.fight_ai.caster import CASTER_TYPE
from pymoonstone.sim.m68k import s16

if TYPE_CHECKING:
    from pathlib import Path

    from pymoonstone.sim.fight_script import RoutineCall

pytestmark = pytest.mark.adf


@pytest.fixture(scope="module")
def bundle(adf_dir: Path) -> GameAssetBundle:
    return GameAssetBundle(DiskSet.from_directory(adf_dir))


@dataclass(slots=True)
class Trace:
    deaths: list[tuple[int, int]] = field(default_factory=list)
    scripts: set[int] = field(default_factory=set)
    hashes: list[str] = field(default_factory=list)
    draws: set[str] = field(default_factory=set)


def _play(bundle: GameAssetBundle, *, defend: bool, hp: int = 200) -> tuple[Fight, Trace]:
    audit = build_caster_audit(bundle)
    fight, trace = audit.scene.fight, Trace()
    fight.actor(1).hp = hp
    original = fight.handlers[CASTER_TYPE]

    def logged(f: Fight, owner: int) -> HandlerResult:
        result = original(f, owner)
        if isinstance(result, Restart):
            trace.scripts.add(result.script)
        return result

    def died(f: Fight, call: RoutineCall | None) -> None:
        assert call is not None
        trace.deaths.append((call.owner, s16(f.actor(call.owner).hp)))
        fighter_died(f, call)

    # Observe results without changing handler scheduling or script execution.
    fight.routines[FIGHTER_DIED] = died
    fight.handlers[CASTER_TYPE] = logged
    runtime = FightRuntime(audit.scene, 1)
    for frame in range(2000):
        fight.ports[0] = audit.input_word() if defend else 0
        output = audit.scene.advance(frame * 6)
        job = fight.find_job(audit.opponent)
        if job is not None:
            trace.scripts.add(job.script)
        trace.draws |= {d.cel for d in output.tick.draws}
        trace.hashes.append(
            hashlib.sha256(json.dumps(runtime.snapshot(), sort_keys=True).encode()).hexdigest()
        )
        if not fight.fight_active:
            break
    return fight, trace


def test_seeded_melee_win_uses_the_hurt_scripts_death_branch(bundle: GameAssetBundle) -> None:
    fight, trace = _play(bundle, defend=True)
    assert not fight.fight_active
    assert s16(fight.actor(1).hp) > 0
    assert fight.total == fight.alive_now == 0
    assert len(trace.deaths) == 1 and trace.deaths[0][1] <= 0
    assert {"Ratmen1.cel", "Ratmen2.cel"} <= trace.draws
    loader = FightLoader(bundle)
    arena = next(a for a in loader.defs.arenas if a.name == "ratmen")
    assert trace.scripts & set(arena.creature.hurt_scripts)


def test_idle_knight_loses_and_seeded_full_snapshots_replay(bundle: GameAssetBundle) -> None:
    fight, trace = _play(bundle, defend=False, hp=20)
    assert not fight.fight_active and s16(fight.actor(1).hp) <= 0
    assert FightLoader(bundle).caster_rules.scripts.strike8 in trace.scripts
    assert _play(bundle, defend=True)[1].hashes == _play(bundle, defend=True)[1].hashes


def test_anchor_and_moon_variants_use_decoded_original_values(bundle: GameAssetBundle) -> None:
    loader = FightLoader(bundle)
    arena = next(a for a in loader.defs.arenas if a.name == "ratmen")
    assert len(arena.moon_variants) == 2
    for variant in arena.moon_variants:
        audit = build_caster_audit(bundle, variant.moon_frame)
        fight = audit.scene.fight
        me, anchor = fight.actor(audit.opponent), fight.actor(fight.second)
        edge = max(loader.knight_rules.clip.top, *(w.y for w in fight.walls))
        assert (anchor.x, s16(anchor.height), anchor.y) == (
            loader.caster_rules.object_x,
            edge - loader.caster_rules.object_height,
            edge,
        )
        assert me.hp == variant.hp
        assert tuple(fight.tables[me.damage_table]) == variant.damage_table
        snapshot = FightRuntime(audit.scene, 1).snapshot()
        assert "flights" in snapshot and "caster" in snapshot


def test_rules_reject_an_unidentified_executable(bundle: GameAssetBundle) -> None:
    executable = parse_hunk_executable("mog", bundle.disk_set.read_file("mog"))
    code = executable.sections[0]
    changed = replace(code, data=bytes([code.data[0] ^ 1]) + code.data[1:])
    with pytest.raises(MogVersionError):
        parse_caster_rules(replace(executable, sections=(changed, *executable.sections[1:])))
