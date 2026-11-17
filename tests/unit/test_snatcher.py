"""Synthetic Mudmen state transitions, independent tunables and shared flags (§10.25)."""

from dataclasses import fields

import pytest

from pymoonstone.sim.fight import Fight, Restart
from pymoonstone.sim.fight_ai.snatcher import (
    CARRY,
    FREE,
    GONE,
    READY,
    RELEASE,
    SHOWN,
    SNATCHER_TYPE,
    SnatcherRules,
    SnatcherScripts,
    snatcher_handler,
)
from pymoonstone.sim.fight_moves import DOWN, FIRE
from tests.unit.brawler_setup import make_trogg_fight
from tests.unit.knight_setup import RULES

SCRIPTS = SnatcherScripts(**{f.name: 300 + i for i, f in enumerate(fields(SnatcherScripts))})
SNATCHER = SnatcherRules(
    SCRIPTS,
    (3, 7, 11, 13, 17, 19, 23, 29),
    0xFFFC,
    4,
    40,
    65,
    90,
    60,
    0xFFC4,
    150,
    3,
    1,
    15,
    70,
    3,
    8,
    6,
)


def _fight(x: int = 160) -> tuple[Fight, int]:
    fight, handle = make_trogg_fight(x, kind=SNATCHER_TYPE)
    for owner in (1, handle):
        a = fight.actor(owner)
        fight.start_job(a.idle_script, owner, 1, (a.x, 0, a.y), a.facing, a.type)
    fight.actor(handle).behaviour_flags = SHOWN
    return fight, handle


def _handle(fight: Fight, handle: int) -> int:
    state = fight.rng.state
    result = snatcher_handler(SNATCHER, RULES, fight, handle)
    assert fight.rng.state == state
    assert isinstance(result, Restart)
    return result.script


@pytest.mark.parametrize(("x", "expected", "facing"), [(100, 160, 3), (170, 110, 1)])
def test_appearance_uses_target_position_and_decoded_side(
    x: int, expected: int, facing: int
) -> None:
    fight, handle = _fight()
    me = fight.actor(handle)
    fight.actor(1).x = x
    me.behaviour_flags, me.anim_timer = 0, 0
    assert _handle(fight, handle) == SCRIPTS.appear
    assert (me.x, me.y, me.facing, me.anim_timer) == (expected, 100, facing, 255)
    assert me.behaviour_flags == SHOWN | READY


def test_appearance_timer_zero_returns_idle_before_repositioning() -> None:
    fight, handle = _fight()
    me = fight.actor(handle)
    me.behaviour_flags, me.anim_timer = 0, 1
    assert _handle(fight, handle) == me.idle_script
    assert (me.x, me.behaviour_flags) == (160, 0)


@pytest.mark.parametrize("distance", [15, 70])
def test_grab_window_inclusive_pauses_and_marks_first_dead(distance: int) -> None:
    fight, handle = _fight(100 + distance)
    fight.actor(handle).behaviour_flags |= READY
    assert _handle(fight, handle) == SCRIPTS.sink
    assert fight.actor(1).hp == fight.constants.dead_hp
    assert fight.snatch_flags == GONE
    job = fight.find_job(1)
    assert job is not None and job.paused == 1


@pytest.mark.parametrize(("x", "y"), [(114, 100), (171, 100), (150, 105)])
def test_failed_grab_clears_ready_and_emerges(x: int, y: int) -> None:
    fight, handle = _fight(x)
    me = fight.actor(handle)
    me.y, me.behaviour_flags = y, SHOWN | READY
    assert _handle(fight, handle) == SCRIPTS.emerge
    assert me.behaviour_flags == SHOWN and fight.snatch_flags == 0


def test_hit_carries_then_only_port_one_breaks_hold_and_restart_is_ineffective() -> None:
    fight, handle = _fight()
    me = fight.actor(handle)
    me.hit_target = 1
    assert _handle(fight, handle) == SCRIPTS.carry
    assert fight.snatch_flags == CARRY and me.anim_timer == SNATCHER.hold_timer
    me.hit_target = 0
    fight.ports[0] = FIRE | DOWN
    assert _handle(fight, handle) == SCRIPTS.carry
    fight.ports[1] = FIRE | DOWN
    assert _handle(fight, handle) == SCRIPTS.release
    assert fight.snatch_flags == RELEASE
    job = fight.find_job(1)
    assert job is not None
    old_script = job.script
    assert _handle(fight, handle) == SCRIPTS.hurt
    assert job.paused == 0 and job.script == old_script
    assert me.hp == 20 - SNATCHER.release_damage and fight.snatch_flags == 0


def test_hold_expiry_kills_first_job_and_clears_freed_but_keeps_local_carry() -> None:
    fight, handle = _fight()
    me = fight.actor(handle)
    fight.snatch_flags, me.behaviour_flags, me.anim_timer = CARRY, CARRY, 1
    fight.ports[1] = FIRE | DOWN
    assert _handle(fight, handle) == SCRIPTS.swallow
    job = fight.find_job(1)
    assert job is not None and not job.active
    assert fight.actor(1).hp == fight.constants.dead_hp
    assert fight.snatch_flags == 0 and me.behaviour_flags == CARRY


@pytest.mark.parametrize("flags", [GONE | CARRY | FREE | RELEASE, CARRY, FREE, RELEASE])
def test_shared_state_does_not_operate_on_another_mudman(flags: int) -> None:
    fight, handle = _fight()
    fight.snatch_flags = flags
    assert _handle(fight, handle) == fight.actor(handle).idle_script


def test_hurt_precedes_hit_and_shared_flags_and_uses_attacker_damage() -> None:
    fight, handle = _fight()
    me = fight.actor(handle)
    fight.snatch_flags = GONE
    me.attacker, me.hit_target, me.input = 1, 1, 31
    fight.actor(1).action = 4
    assert _handle(fight, handle) == SCRIPTS.hurt
    assert (me.hp, me.anim_timer, me.input) == (17, SNATCHER.hurt_timer, 0)


def test_approach_thresholds_and_phase_first_step() -> None:
    fight, handle = _fight(160)
    me = fight.actor(handle)
    assert _handle(fight, handle) == SCRIPTS.strike
    assert me.behaviour_flags == SHOWN | READY
    me.behaviour_flags, me.x = SHOWN, 180
    assert _handle(fight, handle) == SCRIPTS.reach
    me.x = 200
    assert _handle(fight, handle) == 71
    assert (me.x, me.anim_phase) == (193, 1)
