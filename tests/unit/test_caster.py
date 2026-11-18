"""Ratmen state priority, damage quirks and shared-state ownership (§10.23)."""

from dataclasses import replace

import pytest

from harddaysengine.sim.fight import END, KEEP, Fight, Restart
from harddaysengine.sim.fight_ai.caster import (
    GRABBED,
    HOLD,
    INITIAL,
    OUT,
    QUICK,
    RELEASE,
    SLAM,
    THROW,
    caster_handler,
)
from harddaysengine.sim.fight_moves import DOWN, FIRE
from harddaysengine.sim.m68k import u16
from tests.unit.caster_setup import CASTER, SCRIPTS, make_caster
from tests.unit.knight_setup import RULES


def _handle(fight: Fight, handle: int) -> object:
    state = fight.rng.state
    result = caster_handler(CASTER, RULES, fight, handle)
    assert fight.rng.state == state
    return result


def _script(result: object) -> int:
    assert isinstance(result, Restart)
    return result.script


@pytest.mark.parametrize(
    ("x", "action", "script"), [(125, 8, SCRIPTS.strike8), (150, 4, SCRIPTS.strike4)]
)
def test_selects_strike_and_faces_first_fighter(x: int, action: int, script: int) -> None:
    fight, handle = make_caster(x)
    assert _script(_handle(fight, handle)) == script
    assert (fight.actor(handle).action, fight.actor(handle).facing) == (action, 3)


def test_initial_flight_uses_second_job_and_whole_word_wait() -> None:
    fight, handle = make_caster(200)
    assert _script(_handle(fight, handle)) == SCRIPTS.flight
    actor, request = fight.actor(handle), fight.flights.request
    assert (actor.cooldown, actor.behaviour_flags) == (CASTER.initial_wait, INITIAL | OUT)
    assert fight.caster.flags == INITIAL
    assert (request.target_x, request.target_depth, request.target_height) == (170, 104, u16(-30))


@pytest.mark.parametrize("flag", [OUT, QUICK])
def test_flight_has_priority_over_other_states_and_absent_slot_lands(flag: int) -> None:
    fight, handle = make_caster()
    me = fight.actor(handle)
    me.behaviour_flags, me.behaviour_flags2 = flag | GRABBED | HOLD, RELEASE | SLAM
    me.height = u16(-50)
    assert _script(_handle(fight, handle)) == me.idle_script
    assert (me.behaviour_flags, me.height) == (0, 0)


def test_initial_landing_enters_hold_and_countdown_aims_without_resetting_cooldown() -> None:
    fight, handle = make_caster(130)
    me = fight.actor(handle)
    me.behaviour_flags, me.cooldown = OUT | INITIAL, 2
    fight.caster.flags = INITIAL
    assert _script(_handle(fight, handle)) == SCRIPTS.hold_near
    assert (me.behaviour_flags, me.cooldown) == (HOLD, 1)
    _handle(fight, handle)
    assert me.behaviour_flags == QUICK
    assert not fight.caster.flags & INITIAL
    assert fight.flights.request.steps == CASTER.minimum_steps
    assert me.cooldown == CASTER.flight.minimum_steps


@pytest.mark.parametrize(
    ("airborne", "action", "script"),
    [
        (False, 0x10, SCRIPTS.death),
        (False, 0x1C, SCRIPTS.death),
        (True, 0x18, SCRIPTS.flight_death),
    ],
)
def test_special_knight_actions_select_death_without_direct_accounting(
    airborne: bool, action: int, script: int
) -> None:
    fight, handle = make_caster()
    me, attacker = fight.actor(handle), fight.actor(1)
    me.attacker, me.hit_target = 1, 2
    me.behaviour_flags = OUT if airborne else HOLD
    attacker.action = action
    assert _script(_handle(fight, handle)) == script
    assert (me.hp, me.height, me.behaviour_flags) == (CASTER.dead_hp, 0, 0)
    assert fight.alive_now == fight.total == 0


def test_other_attacker_enters_think_and_clears_the_other_records_action() -> None:
    fight, handle = make_caster()
    me, other = fight.actor(handle), fight.actor(fight.second)
    other.action, me.action, me.attacker = 20, 12, fight.second
    fight.caster.wait = 2
    assert _script(_handle(fight, handle)) == me.idle_script
    assert (other.action, me.action, fight.caster.wait) == (0, 12, 1)


def test_catch_sign_extends_endurance_byte_and_pauses_victim() -> None:
    fight, handle = make_caster()
    me, target = fight.actor(handle), fight.actor(1)
    target.endurance = 125
    me.hit_target, me.behaviour_flags = 1, OUT
    assert _script(_handle(fight, handle)) == SCRIPTS.grab
    assert me.cooldown == u16(-122)
    assert fight.caster.flags & GRABBED
    job = fight.find_job(1)
    assert job is not None and job.paused


def test_escape_uses_second_port_then_release_toggles_victim_and_ends() -> None:
    fight, handle = make_caster()
    me = fight.actor(handle)
    me.behaviour_flags, me.cooldown = GRABBED, 5
    fight.ports[:] = [FIRE | DOWN, 0]
    assert _script(_handle(fight, handle)) == SCRIPTS.grab
    fight.ports[:] = [0, FIRE | DOWN]
    assert _script(_handle(fight, handle)) == SCRIPTS.escape
    assert me.behaviour_flags2 & RELEASE
    fight.caster.flags = GRABBED
    fight.toggle_pause(1)
    assert _handle(fight, handle) == END
    assert fight.caster.flags == 0
    job = fight.find_job(1)
    assert job is not None and not job.paused and job.script == fight.actor(1).after_hit_script


def test_throw_damage_uses_true_signed_subtraction_and_only_changes_job_facing() -> None:
    fight, handle = make_caster()
    me, target = fight.actor(handle), fight.actor(1)
    me.behaviour_flags, me.facing = THROW, 3
    target.hp, target.facing = 0x8000, 1
    assert _script(_handle(fight, handle)) == SCRIPTS.thrown
    job = fight.find_job(1)
    assert job is not None and job.script == SCRIPTS.victim_fall and job.facing == 3
    assert target.facing == 1 and target.hp == u16(-32768 - CASTER.throw_damage)
    assert me.behaviour_flags == OUT


def test_slam_input_damage_or_squeeze_and_hold_hit_pause() -> None:
    fight, handle = make_caster()
    me = fight.actor(handle)
    me.behaviour_flags, me.hit_target = HOLD, 1
    assert _script(_handle(fight, handle)) == SCRIPTS.slam_start
    me.hit_target = 0
    hp = fight.actor(1).hp
    assert _script(_handle(fight, handle)) == SCRIPTS.squeeze
    assert fight.actor(1).hp == hp - CASTER.squeeze_damage
    fight.ports[1] = FIRE | DOWN
    me.hp = 1
    assert _script(_handle(fight, handle)) == SCRIPTS.drop
    assert not fight.caster.flags & HOLD


def test_normal_hit_keeps_job_or_counters_and_sets_shared_wait() -> None:
    fight, handle = make_caster()
    me = fight.actor(handle)
    me.hit_target, me.facing, me.action = 1, fight.actor(1).facing, 12
    facing = fight.actor(1).facing
    assert _handle(fight, handle) == KEEP
    assert fight.actor(1).facing == facing ^ 2
    assert fight.caster.wait == CASTER.shared_wait
    me.action = 8
    assert _script(_handle(fight, handle)) == SCRIPTS.counter8


def test_damage_and_hurt_script_are_taken_from_first_not_attacker() -> None:
    fight, handle = make_caster()
    me, first, attacker = fight.actor(handle), fight.actor(1), fight.actor(2)
    first.action, first.strength, attacker.action = 4, 3, 8
    me.attacker = 2
    expected = fight.table_long(me.hurt_scripts, 4)
    assert _script(_handle(fight, handle)) == expected
    assert me.hp == 15
    me.behaviour_flags = OUT
    assert _script(_handle(fight, handle)) == SCRIPTS.flight_hurt


def test_shared_wait_only_counts_down_when_depth_is_near() -> None:
    fight, handle = make_caster(200, 150)
    fight.caster.wait = 7
    _handle(fight, handle)
    assert fight.caster.wait == 7


def test_unrecognized_attacker_preserves_the_stray_map_depth_write_when_aiming() -> None:
    fight, handle = make_caster(200)
    me, other = fight.actor(handle), fight.actor(fight.second)
    me.attacker, me.map_y = fight.second, 77
    fight.caster.flags = INITIAL
    assert _script(_handle(fight, handle)) == SCRIPTS.flight
    assert (me.map_y, other.map_y) == (77, fight.actor(1).y)


def test_slam_overflow_comparison_uses_flags_before_word_wrap() -> None:
    fight, handle = make_caster()
    me = fight.actor(handle)
    me.behaviour_flags2 = SLAM
    fight.actor(1).hp = 0x8000
    rules = replace(CASTER, squeeze_damage=1)
    result = caster_handler(rules, RULES, fight, handle)
    assert _script(result) == SCRIPTS.finish
    assert fight.actor(1).hp == 0x7FFF
