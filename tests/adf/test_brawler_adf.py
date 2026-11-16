"""Trogg fights on the real disks (T11.10a acceptance, ARCHITECTURE.md §10.20).

The test plays the knight with a simple joystick bot and the Troggs with the brawler handler, using
everything read from the user's ``mog``. The arena set-up (§10.3) belongs to T11.11, so the test
spawns the Troggs itself from the arena's spawn lists and creature initialiser. It checks that:

* a seeded fight reaches a decided outcome, won and lost, and replays identically;
* each Trogg dies through its hurt script's death branch (the "a fighter died" routine runs for
  it, with its hit points gone);
* the creature draws idle, walk, attack, hurt and death frames, all from its loaded cels.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from pymoonstone.app.scene_assets import GameAssetBundle
from pymoonstone.assets.adf import DiskSet
from pymoonstone.assets.creature_rules import parse_brawler_rules
from pymoonstone.assets.fight_defs import ArenaDef, FightDefs, SpawnEntry, parse_fight_defs
from pymoonstone.assets.fight_program import (
    CREATURE_LIST,
    KNIGHT_LIST,
    cel_files,
    cel_shapes,
    fight_constants,
    frame_lists,
    script_image,
    script_tables,
)
from pymoonstone.assets.fighter_rules import parse_knight_rules
from pymoonstone.assets.hunk import parse_hunk_executable
from pymoonstone.assets.mog_code import MogCode
from pymoonstone.sim.actor import Actor
from pymoonstone.sim.fight import (
    FIGHTER_DIED,
    Fight,
    Handler,
    HandlerResult,
    Restart,
    TableKeys,
    fighter_died,
)
from pymoonstone.sim.fight_ai.brawler import BRAWLER_TYPES, BrawlerRules, install_brawler
from pymoonstone.sim.fighters import KNIGHT_TYPE, install_knight_control
from pymoonstone.sim.inventory import Inventory
from pymoonstone.sim.m68k import s16
from pymoonstone.sim.rng import Rng

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from pymoonstone.sim.fight_script import RoutineCall
    from pymoonstone.sim.fighter_rules import KnightRules

pytestmark = pytest.mark.adf

ARENAS = ("trogg_axe", "trogg_axe_2", "trogg_spear")
FIRE, RIGHT, LEFT, DOWN, UP = 0x10, 0x01, 0x02, 0x04, 0x08
MAX_FRAMES = 6000
STRIKE, DAGGER, DAGGER_TYPE = 0x08, 0x0C, 0x34
DEPTH_SLACK = 3
STRIKE_RANGE = 50
DAGGER_RANGE = 150
CENTRE = (110, 210)
SEED = 0x2468ACE1


@dataclass(frozen=True)
class Setup:
    defs: FightDefs
    knight_rules: KnightRules
    brawler: BrawlerRules
    mog: MogCode
    bundle: GameAssetBundle


@pytest.fixture(scope="module")
def setup(adf_dir: Path) -> Setup:
    bundle = GameAssetBundle(DiskSet.from_directory(adf_dir))
    executable = parse_hunk_executable("mog", bundle.disk_set.read_file("mog"))
    return Setup(
        defs=parse_fight_defs(executable),
        knight_rules=parse_knight_rules(executable),
        brawler=parse_brawler_rules(executable),
        mog=MogCode(executable),
        bundle=bundle,
    )


@dataclass
class Trace:
    """What happened in one fight."""

    died: list[tuple[int, int]] = field(default_factory=list)
    creature_draws: set[tuple[str, int]] = field(default_factory=set)
    scripts: set[int] = field(default_factory=set)
    frames: int = 0
    hashes: list[int] = field(default_factory=list)


def _arena(setup: Setup, name: str) -> ArenaDef:
    return next(arena for arena in setup.defs.arenas if arena.name == name)


def _spawner(fight: Fight, arena: ArenaDef, keys: TableKeys) -> Callable[[SpawnEntry], int]:
    init = arena.creature

    def spawn(entry: SpawnEntry) -> int:
        position = (entry.x, entry.height, entry.y)
        handle = fight.alloc_record()
        actor = fight.actor(handle)
        keys.assign(actor)
        actor.x, actor.height, actor.y = (value & 0xFFFF for value in position)
        actor.facing, actor.frame_list = entry.facing, CREATURE_LIST
        actor.hit_target = actor.attacker = 0
        assert init.actor_type is not None and init.hp is not None
        actor.type, actor.input_port = init.actor_type, init.input_port or 0
        actor.hp, actor.hp_max = init.hp, init.hp_max or 0
        actor.reach_x, actor.too_close_x = init.reach_x or 0, init.too_close_x or 0
        actor.depth_reach = init.depth_reach or 0
        fight.start_job(
            init.idle_script, handle, CREATURE_LIST, position, entry.facing, init.actor_type
        )
        fight.alive_now += 1
        return handle

    return spawn


def _fight(setup: Setup, arena: ArenaDef, *, hp: int) -> tuple[Fight, TableKeys]:
    files = cel_files(setup.defs.knight_cels, arena.cels)
    cels = {}
    for name in files:
        cel = setup.bundle.get_cel(name)
        assert cel is not None, name
        cels[name] = cel
    hit_sets = setup.bundle.get_hit_sets()
    assert hit_sets is not None
    fight = Fight(
        script_image(setup.mog),
        cel_shapes(files, cels, hit_sets),
        frame_lists(setup.defs.knight_cels, arena.cels),
        [Actor() for _ in range(4)],
        Actor(),
        fight_constants(setup.mog),
    )
    fight.rng = Rng(SEED)
    install_knight_control(fight, setup.knight_rules)
    install_brawler(fight, setup.brawler, setup.knight_rules)
    knight_keys = fight.register_tables(script_tables(setup.defs.knight))
    for poke in arena.knight_pokes:
        key = knight_keys.action_scripts if poke.table == "action" else knight_keys.hurt_scripts
        fight.tables[key][poke.offset // 4] = poke.script
    knight = fight.actor(1)
    knight_keys.assign(knight)
    knight.x, knight.y, knight.facing, knight.input_port = 250, 100, 3, 1
    knight.hp = knight.hp_max = hp
    knight.strength, knight.sword, knight.daggers = 3, 0x19, 99
    knight.type, knight.active, knight.inventory = KNIGHT_TYPE, 1, 1
    fight.inventories[1] = Inventory()
    fight.start_job(knight.idle_script, 1, KNIGHT_LIST, (250, 0, 100), 3, KNIGHT_TYPE)
    fight.first, fight.fight_active = 1, True
    return fight, fight.register_tables(script_tables(arena.creature))


def _nearest(fight: Fight, knight: Actor) -> Actor | None:
    troggs = [
        fight.actor(job.owner)
        for job in fight.engine.jobs
        if job.active and job.owner != 1 and fight.actor(job.owner).type in (0x18, 0x1C, 0x20)
    ]
    alive = [t for t in troggs if s16(t.hp) > 0]
    return min(alive, key=lambda t: abs(s16(t.x) - s16(knight.x)), default=None)


def _daggers_in_flight(fight: Fight) -> int:
    return sum(
        1 for job in fight.engine.jobs if job.active and fight.actor(job.owner).type == DAGGER_TYPE
    )


def _bot(fight: Fight, rules: KnightRules, knight: Actor, trogg: Actor | None) -> int:
    """Line up on the Trogg's depth, face it, strike when close, else throw a dagger.

    A Trogg keeps its distance and backs away from a knight that comes too close, so the dagger
    is the knight's way to reach it. A dagger flying left past the margin keeps flying (§10.19)
    and holds its record, so the bot throws only at a Trogg on its depth line within dagger range,
    one dagger at a time, and the dagger hits.
    """
    if trogg is None:
        return 0
    dy = s16(trogg.y) - s16(knight.y)
    if abs(dy) > DEPTH_SLACK:
        return DOWN if dy > 0 else UP
    dx = s16(trogg.x) - s16(knight.x)
    toward = RIGHT if dx > 0 else LEFT
    if (knight.facing == 1) != (dx > 0):
        return toward
    table = rules.actions_right if knight.facing == 1 else rules.actions_left
    if abs(dx) <= STRIKE_RANGE:
        return FIRE | table.index(STRIKE)
    if abs(dx) <= DAGGER_RANGE and _daggers_in_flight(fight) == 0:
        return FIRE | table.index(DAGGER)
    if s16(knight.x) < CENTRE[0]:
        return RIGHT
    if s16(knight.x) > CENTRE[1]:
        return LEFT
    return 0


def _play(setup: Setup, name: str, *, fights_back: bool, hp: int = 200) -> tuple[Fight, Trace]:
    arena = _arena(setup, name)
    fight, keys = _fight(setup, arena, hp=hp)
    spawn = _spawner(fight, arena, keys)
    toggle = [0]

    def next_creature(f: Fight) -> None:
        toggle[0] ^= 1
        spawn(arena.next_creature[toggle[0]])

    trace = Trace()

    def died(f: Fight, call: RoutineCall | None) -> None:
        assert call is not None
        trace.died.append((call.owner, s16(f.actor(call.owner).hp)))
        fighter_died(f, call)

    def logged(handler: Handler) -> Handler:
        def run(f: Fight, handle: int) -> HandlerResult:
            result = handler(f, handle)
            if isinstance(result, Restart):
                trace.scripts.add(result.script)
            return result

        return run

    for kind in BRAWLER_TYPES:
        fight.handlers[kind] = logged(fight.handlers[kind])
    fight.next_creature = next_creature
    fight.routines[FIGHTER_DIED] = died
    fight.max_alive, fight.total = arena.max_alive, arena.total
    for entry in arena.first_wave[: arena.max_alive]:
        spawn(entry)
    knight = fight.actor(1)
    for frame in range(MAX_FRAMES):
        if not fight.fight_active:
            break
        fight.ports[0] = (
            _bot(fight, setup.knight_rules, knight, _nearest(fight, knight)) if fights_back else 0
        )
        output = fight.step(frame * 6)
        names = {name for name in fight.frame_lists[CREATURE_LIST] if name is not None}
        trace.creature_draws |= {(d.cel, d.frame) for d in output.tick.draws if d.cel in names}
        trace.hashes.append(hash(tuple((a.x, a.y, a.hp, a.action) for a in fight.actors)))
        trace.frames = frame
    return fight, trace


@pytest.mark.parametrize("name", ARENAS)
def test_the_knight_wins_and_every_trogg_dies_through_its_death_branch(
    setup: Setup, name: str
) -> None:
    fight, trace = _play(setup, name, fights_back=True)
    arena = _arena(setup, name)
    assert not fight.fight_active, f"{name}: undecided after {trace.frames} frames"
    assert s16(fight.actor(1).hp) > 0, f"{name}: the knight lost"
    assert s16(fight.total) <= 0
    troggs = [(owner, hp) for owner, hp in trace.died if owner != 1]
    assert len(troggs) == arena.total
    assert all(hp <= 0 for _, hp in troggs)


@pytest.mark.parametrize("name", ARENAS)
def test_a_knight_that_does_nothing_loses(setup: Setup, name: str) -> None:
    fight, trace = _play(setup, name, fights_back=False, hp=20)
    assert not fight.fight_active, f"{name}: undecided after {trace.frames} frames"
    assert s16(fight.actor(1).hp) <= 0


@pytest.mark.parametrize("name", ARENAS)
def test_a_seeded_fight_replays_identically(setup: Setup, name: str) -> None:
    first = _play(setup, name, fights_back=True)[1]
    second = _play(setup, name, fights_back=True)[1]
    assert first.hashes == second.hashes


@pytest.mark.parametrize("name", ARENAS)
def test_the_trogg_plays_idle_walk_attack_hurt_and_death(setup: Setup, name: str) -> None:
    _, trace = _play(setup, name, fights_back=True)
    init = _arena(setup, name).creature
    walks = {s for s in init.walk_scripts if s}
    attacks = {s for s in init.action_scripts if s} or {setup.brawler.thrust_script}
    hurts = {s for s in init.hurt_scripts if s}
    assert init.idle_script in trace.scripts | {init.idle_script} and trace.scripts
    assert trace.scripts & walks, "no walk script"
    assert trace.scripts & attacks, "no attack script"
    assert trace.scripts & hurts, "no hurt script"
    assert trace.creature_draws, "the Trogg drew nothing"
