"""Original Flyer win/loss, death script, decoded rules and persistent state (§10.26)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from harddaysengine.app.fight_audit import build_flyer_audit
from harddaysengine.app.fight_runtime import FightLoader, FightRuntime
from harddaysengine.app.game import GameSession
from harddaysengine.app.scene_assets import GameAssetBundle
from harddaysengine.assets.adf import DiskSet
from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.flyer_rules import parse_flyer_rules
from harddaysengine.assets.hunk import parse_hunk_executable
from harddaysengine.sim.actor import Actor
from harddaysengine.sim.fight import FIGHTER_DIED, Fight, fighter_died
from harddaysengine.sim.inventory import Inventory
from harddaysengine.sim.m68k import s16
from harddaysengine.sim.rng import Rng

if TYPE_CHECKING:
    from pathlib import Path

    from harddaysengine.sim.fight_script import RoutineCall

pytestmark = pytest.mark.adf


@pytest.fixture(scope="module")
def bundle(adf_dir: Path) -> GameAssetBundle:
    return GameAssetBundle(DiskSet.from_directory(adf_dir))


def _play(bundle: GameAssetBundle, *, defend: bool) -> tuple[Fight, list[str], list[int], set[str]]:
    audit = build_flyer_audit(bundle)
    f = audit.scene.fight
    hashes, deaths, draws = [], [], set()
    if not defend:
        f.actor(1).hp = 10

    def died(fight: Fight, call: RoutineCall | None) -> None:
        assert call is not None and call.owner == audit.opponent
        deaths.append(s16(fight.actor(call.owner).hp))
        fighter_died(fight, call)

    f.routines[FIGHTER_DIED] = died
    runtime = FightRuntime(audit.scene, 1)
    for frame in range(1000):
        f.ports[0] = audit.input_word() if defend else 0
        output = audit.scene.advance(frame * 6)
        draws |= {d.cel for d in output.tick.draws}
        hashes.append(
            hashlib.sha256(json.dumps(runtime.snapshot(), sort_keys=True).encode()).hexdigest()
        )
        if not f.fight_active:
            break
    assert not f.fight_active
    return f, hashes, deaths, draws


def test_win_follows_hurt_script_death_and_full_state_replays(bundle: GameAssetBundle) -> None:
    f, hashes, deaths, draws = _play(bundle, defend=True)
    assert f.total == f.alive_now == 0 and s16(f.actor(1).hp) > 0
    assert len(deaths) == 1 and deaths[0] <= 0
    assert {"be1.c", "be2.c"} <= draws
    assert hashes == _play(bundle, defend=True)[1]


def test_idle_knight_loses_to_the_original_flyer(bundle: GameAssetBundle) -> None:
    f, _, deaths, _ = _play(bundle, defend=False)
    assert s16(f.actor(1).hp) <= 0 and deaths == []


def test_loader_keeps_executable_alternation_word_between_fights(bundle: GameAssetBundle) -> None:
    loader = FightLoader(bundle)
    arena = next(a for a in loader.defs.arenas if a.name == "flyer")
    assert loader.arena_for(arena.handler_offsets[0]) == arena
    records = [Actor(hp=20, hp_max=20, strength=3) for _ in range(4)]
    first = loader.creature(arena, "fo1.t", records, Actor(), {1: Inventory()}, Rng(7), 1)
    first.fight.flyer.toggle ^= loader.flyer_rules.toggle_xor
    second = loader.creature(arena, "fo1.t", records, Actor(), {1: Inventory()}, Rng(8), 1)
    assert first.fight.flyer is second.fight.flyer is loader.flyer
    assert loader.flyer.toggle != loader.flyer_rules.initial_toggle
    assert second.snapshot()["flyer"] == {"toggle": loader.flyer.toggle}
    second.scene.advance(65543)
    assert second.snapshot()["beam_word"] == 7


def test_unidentified_executable_is_rejected(bundle: GameAssetBundle) -> None:
    executable = parse_hunk_executable("mog", bundle.disk_set.read_file("mog"))
    section = executable.sections[0]
    changed = replace(section, data=b"\x00\x00" + section.data[2:])
    with pytest.raises(MogVersionError):
        parse_flyer_rules(replace(executable, sections=(changed, *executable.sections[1:])))


def test_host_hash_keeps_alternation_state_when_no_fight_is_active(bundle: GameAssetBundle) -> None:
    session = GameSession()
    session.fight_loader = FightLoader(bundle)
    before = session.state_hash()
    session.fight_loader.flyer.toggle ^= session.fight_loader.flyer_rules.toggle_xor
    assert session.active_encounter is None and session.state_hash() != before
