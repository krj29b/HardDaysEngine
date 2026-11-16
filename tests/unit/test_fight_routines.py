"""Dagger throw, dagger handler and the hop (ARCHITECTURE.md §10.19)."""

from __future__ import annotations

import pytest

from pymoonstone.sim.fight import END, POOL_FIRST, FightError, HandlerResult, Restart
from pymoonstone.sim.fight_routines import (
    DAGGER_TYPE,
    dagger_handler,
    dagger_throw,
    hop,
    hop_step,
)
from pymoonstone.sim.fight_script import RoutineCall
from tests.unit.knight_setup import HOP_WORDS, RULES, make_fight


def _call(name: str, owner: int = 1, x: int = 100, facing: int = 1) -> RoutineCall:
    return RoutineCall(name, owner, 1, x, 0, 100, facing)


def test_a_thrown_dagger_is_a_new_record_with_the_dagger_table() -> None:
    fight, _ = make_fight()
    table = fight.add_table(RULES.dagger.damage)
    fight.actor(1).daggers = 0
    dagger_throw(RULES, table, fight, _call("dagger_throw", x=120, facing=3))
    assert fight.actor(1).daggers == 255
    dagger = fight.actor(POOL_FIRST)
    assert (dagger.type, dagger.action, dagger.damage_table) == (DAGGER_TYPE, 12, table)
    assert (dagger.x, dagger.y, dagger.facing, dagger.frame_list) == (120, 100, 3, 1)
    job = fight.find_job(POOL_FIRST)
    assert job is not None
    assert (job.script, job.type, job.running) == (RULES.dagger.script, DAGGER_TYPE, True)


def test_the_engine_runs_the_throw_through_the_installed_routine() -> None:
    fight, _ = make_fight()
    fight.routines["dagger_throw"](fight, _call("dagger_throw"))
    dagger = fight.actor(POOL_FIRST)
    assert dagger.type == DAGGER_TYPE
    assert fight.table_long(dagger.damage_table, dagger.action) == RULES.dagger.damage[3]


def _dagger(x: int, facing: int, *, hit: int = 0) -> HandlerResult:
    fight, _ = make_fight()
    handle = fight.spawn_actor(RULES.dagger.script, 1, (x, 0, 100), facing, DAGGER_TYPE)
    fight.actor(handle).hit_target = hit
    return dagger_handler(RULES, fight, handle)


def _flying(result: HandlerResult) -> bool:
    if result is END:
        return False
    assert isinstance(result, Restart)
    assert result.script == RULES.dagger.flight_script
    return True


@pytest.mark.parametrize(
    ("x", "facing", "flying"),
    [
        (299, 1, True),
        (300, 1, False),
        (1, 3, True),
        (0, 3, False),
        (-8, 3, False),
        (-9, 3, True),
    ],
)
def test_a_dagger_flies_until_the_edge(x: int, facing: int, flying: bool) -> None:
    assert _flying(_dagger(x, facing)) is flying


def test_a_dagger_that_hit_is_gone() -> None:
    assert _dagger(100, 1, hit=1) is END


def test_hop_steps_move_the_job_against_the_hop_facing() -> None:
    fight, _ = make_fight()
    job = fight.start_job(10, 1, 1, (100, 0, 100), 3, 0x0C)
    assert job is not None
    fight.hop_facing, fight.hop_table = 1, RULES.hop_table
    hop(fight, _call("hop"))
    assert fight.hop_counter == 0xFFFF
    hop_step(RULES, fight, _call("hop_step"))
    hop_step(RULES, fight, _call("hop_step"))
    assert fight.hop_counter == 1
    assert job.x == 100 - HOP_WORDS[0] - HOP_WORDS[1]
    knight = fight.actor(1)
    assert (knight.facing, knight.input) == (1, 0x02)
    fight.hop_facing = 3
    hop_step(RULES, fight, _call("hop_step"))
    assert job.x == 100 - HOP_WORDS[0] - HOP_WORDS[1] + HOP_WORDS[2]


def test_the_arena_clip_stops_a_hop() -> None:
    fight, _ = make_fight()
    job = fight.start_job(10, 1, 1, (100, 0, 100), 1, 0x0C)
    assert job is not None
    fight.actor(1).x = 0xFFEC
    fight.hop_facing, fight.hop_table, fight.hop_counter = 1, RULES.hop_table, 0xFFFF
    hop_step(RULES, fight, _call("hop_step"))
    assert (job.x, fight.actor(1).input) == (100, 0)


def test_a_hop_without_a_job_or_past_the_hunk() -> None:
    fight, _ = make_fight()
    fight.hop_facing, fight.hop_table = 1, RULES.hop_table
    hop_step(RULES, fight, _call("hop_step"))
    fight.start_job(10, 1, 1, (100, 0, 100), 1, 0x0C)
    fight.hop_table = len(fight.image.data)
    with pytest.raises(FightError, match="hop table"):
        hop_step(RULES, fight, _call("hop_step"))
