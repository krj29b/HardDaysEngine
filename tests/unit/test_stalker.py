"""Synthetic Troll state priorities, phase order and spark plumbing (§10.27)."""

import pytest

from harddaysengine.sim.fight import END, Fight, Restart, idle_object
from harddaysengine.sim.fight_ai.effects import HitSparkRules
from harddaysengine.sim.fight_ai.stalker import STALKER_TYPE, StalkerRules, stalker_handler
from harddaysengine.sim.m68k import u16
from tests.unit.brawler_setup import make_trogg_fight
from tests.unit.knight_setup import RULES

STALKER = StalkerRules(
    steps=(2, 1, 8, 3, 5, 4, 9, 6, 11, 0xFFFF, 13, 2, 17, 4, 19, 6),
    up=u16(-7),
    down=7,
    strike_near=45,
    strike_far=55,
    strike_script=180,
    heavy_script=190,
    hurt_script=200,
    hop_table=210,
    spark=HitSparkRules(220, 1, 0x28),
)


def _fight(x: int = 150, y: int = 100) -> tuple[Fight, int]:
    f, handle = make_trogg_fight(x, y, kind=STALKER_TYPE)
    f.tables[f.actor(handle).hurt_scripts] = [-1] * 9
    return f, handle


def _handle(fight: Fight, handle: int) -> int:
    state = fight.rng.state
    result = stalker_handler(STALKER, RULES, fight, handle)
    assert isinstance(result, Restart)
    assert fight.rng.state == state
    return result.script


def test_hit_takes_priority_over_hurt_even_when_both_links_are_set() -> None:
    f, handle = _fight()
    me = f.actor(handle)
    me.hit_target, me.attacker, me.input, me.action = 1, 2, 31, 32
    assert _handle(f, handle) == me.after_hit_script
    assert (me.hp, me.input, me.action) == (20, 0, 32)
    assert not any(j.active for j in f.engine.jobs)


def test_hurt_uses_contact_damage_fixed_script_and_an_ordinary_spark_job() -> None:
    f, handle = _fight()
    me = f.actor(handle)
    me.attacker, me.hit_x, me.hit_y, me.hp = 1, 77, 123, 1
    f.actor(1).action = 4
    f.handlers[0x28] = lambda _f, _h: END
    assert _handle(f, handle) == STALKER.hurt_script
    assert me.hp == u16(-2)
    spark = next(j for j in f.engine.jobs if j.active)
    assert (spark.script, spark.x, spark.height, spark.depth, spark.facing) == (220, 77, 0, 123, 1)
    assert (spark.frame_list, spark.type) == (1, 0x28)
    assert f.handlers[0x28] is idle_object
    assert f.actor(spark.owner).type == 0x28


@pytest.mark.parametrize("hp", [0, 0xFFFF])
def test_first_fighter_down_leaves_troll_idle_and_preserves_action(hp: int) -> None:
    f, handle = _fight()
    f.actor(1).hp = hp
    me = f.actor(handle)
    me.action = 32
    assert _handle(f, handle) == me.idle_script
    assert me.action == 32


@pytest.mark.parametrize(
    ("x", "previous", "script", "action"),
    [
        (144, 0, 180, 8),
        (145, 0, 190, 32),
        (154, 8, 190, 32),
        (154, 32, 180, 8),
        (155, 0, 180, 8),
        (160, 0, 180, 8),
    ],
)
def test_attack_thresholds_and_heavy_alternation(
    x: int, previous: int, script: int, action: int
) -> None:
    f, handle = _fight(x)
    me = f.actor(handle)
    me.action = previous
    f.hop_table, f.hop_facing = 999, 1
    assert _handle(f, handle) == script
    assert me.action == action
    assert (f.hop_table, f.hop_facing) == ((210, 3) if action == 8 else (999, 1))


@pytest.mark.parametrize(
    ("phase", "expected_x", "script"), [(0, 218, 71), (1, 212, 72), (6, 203, 70)]
)
def test_walk_reads_old_phase_then_selects_new_phase_script(
    phase: int, expected_x: int, script: int
) -> None:
    f, handle = _fight(220)
    me = f.actor(handle)
    me.anim_phase = phase
    assert _handle(f, handle) == script
    assert (me.x, me.y) == (expected_x, 100)


@pytest.mark.parametrize(("y", "expected"), [(120, 113), (80, 87)])
def test_diagonal_depth_overrides_step_table_y(y: int, expected: int) -> None:
    f, handle = _fight(220, y)
    assert _handle(f, handle) == 71
    assert (f.actor(handle).x, f.actor(handle).y) == (218, expected)


def test_pure_depth_step_runs_phase_backwards_without_side_input() -> None:
    f, handle = _fight(150, 120)
    assert _handle(f, handle) == 72
    assert (f.actor(handle).x, f.actor(handle).y, f.actor(handle).anim_phase) == (150, 113, 2)


def test_too_close_backs_away_and_steps_phase_backwards() -> None:
    f, handle = _fight(120)
    assert _handle(f, handle) == 72
    assert (f.actor(handle).x, f.actor(handle).anim_phase) == (122, 2)
