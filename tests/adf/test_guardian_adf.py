"""Original Guardian setup, natural win/death, loss and full-state replay (§10.29)."""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import fields, replace
from typing import TYPE_CHECKING

import pytest

from harddaysengine.app.fight_audit import build_guardian_audit
from harddaysengine.app.fight_runtime import FightLoader, FightRuntime
from harddaysengine.app.scene_assets import GameAssetBundle
from harddaysengine.assets.adf import DiskSet
from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.guardian_rules import parse_guardian_rules
from harddaysengine.sim.fight import END_FIGHT, Fight, HandlerResult, Restart, end_fight
from harddaysengine.sim.fight_ai.guardian import GUARDIAN_TYPE
from harddaysengine.sim.m68k import s16
from tests.adf.test_fight_defs_adf import Walker, _arena_sets, _check

if TYPE_CHECKING:
    from pathlib import Path

    from harddaysengine.sim.fight_script import RoutineCall

pytestmark = pytest.mark.adf


@pytest.fixture(scope="module")
def bundle(adf_dir: Path) -> GameAssetBundle:
    return GameAssetBundle(DiskSet.from_directory(adf_dir))


def _play(bundle: GameAssetBundle, *, win: bool) -> tuple[Fight, list[str], list[int], set[int]]:
    audit = build_guardian_audit(bundle)
    f = audit.scene.fight
    if not win:
        f.actor(1).hp = 20
    hashes, ended, scripts = [], [], set()
    original = f.handlers[GUARDIAN_TYPE]

    def handler(fight: Fight, owner: int) -> HandlerResult:
        result = original(fight, owner)
        if owner == audit.opponent and isinstance(result, Restart):
            scripts.add(result.script)
        return result

    def end(fight: Fight, call: RoutineCall | None) -> None:
        if call is not None and call.owner == audit.opponent:
            ended.append(s16(fight.actor(call.owner).hp))
        end_fight(fight, call)

    f.handlers[GUARDIAN_TYPE], f.routines[END_FIGHT] = handler, end
    runtime = FightRuntime(audit.scene, 1)
    for frame in range(1500):
        f.ports[1] = audit.input_word() if win else 0
        output = audit.scene.advance(frame * 6)
        assert not output.skipped_calls
        hashes.append(
            hashlib.sha256(json.dumps(runtime.snapshot(), sort_keys=True).encode()).hexdigest()
        )
        if not f.fight_active:
            break
    assert not f.fight_active
    return f, hashes, ended, scripts


def test_natural_seeded_win_follows_hurt_death_branch_and_replays(bundle: GameAssetBundle) -> None:
    f, hashes, ended, scripts = _play(bundle, win=True)
    assert s16(f.actor(1).hp) > 0 and s16(f.actor(f.guardian.main).hp) <= 0
    assert len(ended) == 1 and ended[0] <= 0
    assert FightLoader(bundle).guardian_rules.scripts.hurt in scripts
    assert hashes == _play(bundle, win=True)[1]
    assert not f.actor(f.guardian.body).active


def test_idle_knight_loses(bundle: GameAssetBundle) -> None:
    f, _, ended, _ = _play(bundle, win=False)
    assert s16(f.actor(1).hp) <= 0 and s16(f.actor(f.guardian.main).hp) > 0
    assert ended == []


def test_setup_and_operands_match_independent_executable_reads(bundle: GameAssetBundle) -> None:
    loader = FightLoader(bundle)
    arena = next(a for a in loader.defs.arenas if a.name == "guardian")
    assert loader.arena_for(8) == arena
    audit = build_guardian_audit(bundle)
    f, code = audit.scene.fight, loader.mog.hunk_data(0)

    def word(at: int) -> int:
        return int.from_bytes(code[at : at + 2], "big")

    actor = f.actor(f.guardian.main)
    assert (actor.x, actor.height, actor.y) == (word(0x4984), word(0x498A), word(0x4990))
    assert actor.hp == word(0x4972) and f.guardian.body == f.guardian.main + 1
    assert f.max_alive == f.total == f.alive_now == 1
    assert f.actor(f.guardian.body).active and f.find_job(f.guardian.body) is None
    data = loader.mog.hunk_data(40)
    assert loader.guardian_rules.grab_damage == struct.unpack_from(">H", data, 0xBC4)[0]
    before = FightRuntime(audit.scene, 1).snapshot()
    f.guardian.holding = True
    assert FightRuntime(audit.scene, 1).snapshot() != before


@pytest.mark.parametrize(("hunk", "offset"), [(0, 0), (40, 0x6AC), (40, 0x818), (40, 0xA4E)])
def test_unknown_version_or_corrupt_instructions_are_rejected(
    bundle: GameAssetBundle, hunk: int, offset: int
) -> None:
    executable = FightLoader(bundle).mog.executable
    sections = list(executable.sections)
    section = sections[hunk]
    data = section.data[:offset] + b"\x00\x00" + section.data[offset + 2 :]
    sections[hunk] = replace(section, data=data)
    with pytest.raises(MogVersionError):
        parse_guardian_rules(replace(executable, sections=tuple(sections)))


def test_all_fixed_guardian_scripts_draw_exact_loaded_frames(bundle: GameAssetBundle) -> None:
    loader = FightLoader(bundle)
    arena = next(a for a in loader.defs.arenas if a.name == "guardian")
    scripts = loader.guardian_rules.scripts
    creature = {
        getattr(scripts, field.name)
        for field in fields(scripts)
        if field.name not in ("release", "victim_death")
    }
    sets = _arena_sets(bundle, loader.defs, arena)
    assert _check(Walker(loader.mog), creature, 2, sets) == []
    assert _check(Walker(loader.mog), {scripts.release, scripts.victim_death}, 1, sets) == []
