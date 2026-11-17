"""Balok priorities, flight arithmetic, recovery quirks and counter draws (§10.28)."""

import pytest

from pymoonstone.sim.fight import Fight, Restart
from pymoonstone.sim.fight_ai.drake import (
    FINISHED,
    FLYING,
    GRABBED,
    HEAVY,
    RECOVER,
    STRIKE,
    counter,
    drake_handler,
)
from pymoonstone.sim.fight_script import RoutineCall
from pymoonstone.sim.m68k import s8, u16
from pymoonstone.sim.rng import Rng
from tests.unit.drake_setup import DRAKE, SCRIPTS, make_drake
from tests.unit.knight_setup import RULES


def _handle(fight: Fight, handle: int) -> int:
    state = fight.rng.state
    result = drake_handler(DRAKE, RULES, fight, handle)
    assert fight.rng.state == state
    assert isinstance(result, Restart)
    return result.script


@pytest.mark.parametrize(
    ("old", "timer"), [(0, 7), (1, 0), (127, 126), (128, 7), (129, 7), (255, 7)]
)
def test_counter_signed_subtraction_wrap_and_discarded_draw(old: int, timer: int) -> None:
    f, handle = make_drake()
    me = f.actor(handle)
    me.anim_timer = old
    expected = Rng(f.rng.state)
    if s8(old) - DRAKE.counter_step < 0:
        expected.draw()
    counter(DRAKE, f, RoutineCall("counter", handle, 1, 0, 0, 0, 1))
    assert me.anim_timer == timer and f.rng.state == expected.state


def test_flying_has_priority_over_both_hit_links_and_other_flags() -> None:
    f, handle = make_drake()
    me = f.actor(handle)
    me.attacker = me.hit_target = 1
    me.behaviour_flags2 = 9
    f.drake.flags, f.drake.remaining, f.caster.aim_steps = FLYING | GRABBED | RECOVER, 2, 20
    assert _handle(f, handle) == SCRIPTS.flight
    assert (me.hp, me.behaviour_flags2, f.drake.remaining) == (20, 0, 1)


def test_hurt_precedes_hit_spawns_spark_and_uses_fixed_script() -> None:
    f, handle = make_drake()
    me = f.actor(handle)
    me.attacker = me.hit_target = 1
    f.actor(1).action = 4
    assert _handle(f, handle) == SCRIPTS.hurt
    assert me.hp == 17
    assert any(j.active and j.type == 0x28 for j in f.engine.jobs)


@pytest.mark.parametrize(
    ("action", "script"), [(HEAVY, SCRIPTS.grab), (STRIKE, SCRIPTS.after_strike), (4, 13)]
)
def test_hit_reply_by_action_and_heavy_grab_pauses_victim(action: int, script: int) -> None:
    f, handle = make_drake()
    me = f.actor(handle)
    me.hit_target, me.action = 1, action
    assert _handle(f, handle) == script
    job = f.find_job(1)
    assert job is not None and job.paused == int(action == HEAVY)
    assert bool(f.drake.flags & GRABBED) == (action == HEAVY)


def test_grab_becomes_carry_then_living_first_fighter_is_released_at_job_offset() -> None:
    f, handle = make_drake()
    me, target = f.actor(handle), f.actor(1)
    f.drake.flags = GRABBED
    assert _handle(f, handle) == SCRIPTS.carry and f.drake.flags == RECOVER
    target.hit_target = target.attacker = handle
    f.toggle_pause(1)
    original_x = target.x
    assert _handle(f, handle) == me.after_hit_script
    job = f.find_job(1)
    assert job is not None and job.x == me.x - DRAKE.release_offset
    assert job.script == target.idle_script and job.paused == 0
    assert target.x == original_x and target.hit_target == target.attacker == f.drake.flags == 0


def test_recovery_alternates_finishers_for_a_dead_first_fighter() -> None:
    f, handle = make_drake()
    f.actor(1).hp = 0xFFFF
    f.drake.flags = RECOVER
    assert _handle(f, handle) == SCRIPTS.finish_first
    assert f.drake.flags == FINISHED and f.drake.toggle == 1
    f.drake.flags = RECOVER
    assert _handle(f, handle) == SCRIPTS.finish_second
    assert f.drake.flags == FINISHED and f.drake.toggle == 0


@pytest.mark.parametrize(
    ("x", "last", "script"),
    [
        (141, 0, SCRIPTS.strike),
        (160, 0, SCRIPTS.strike),
        (160, STRIKE, SCRIPTS.heavy),
        (161, 0, SCRIPTS.heavy),
        (190, 0, SCRIPTS.heavy),
    ],
)
def test_melee_thresholds_and_action_alternation(x: int, last: int, script: int) -> None:
    f, handle = make_drake(x)
    me = f.actor(handle)
    me.depth_reach, me.action, me.input = 10, last, 17
    assert _handle(f, handle) == script
    assert me.input == 17
    if script == SCRIPTS.strike:
        assert me.action == STRIKE and (f.hop_facing, f.hop_table) == (3, 420)


def test_no_daggers_wait_band_and_dead_target_leave_idle() -> None:
    f, handle = make_drake(225)
    me = f.actor(handle)
    me.action = 4
    f.actor(1).daggers = 0
    assert _handle(f, handle) == me.idle_script and me.action == 4
    f.actor(1).hp = 0
    me.attacker = me.hit_target = 0
    f.drake.flags = 0
    assert _handle(f, handle) == me.idle_script


def test_aim_override_does_not_mutate_reach_and_cap_does_not_replace_aim_steps() -> None:
    f, handle = make_drake(400, 130)
    me = f.actor(handle)
    old_reach = me.reach_x
    assert _handle(f, handle) == SCRIPTS.landing
    assert me.reach_x == old_reach
    assert f.flights.request.target_x == 100 + DRAKE.aim_reach
    assert f.caster.aim_steps > f.drake.remaining == DRAKE.maximum_steps
    assert f.drake.flags == FLYING and me.action == 0
    assert any(s.owner == handle for s in f.flights.slots)


def test_jump_cooldown_is_byte_counted_and_close_jump_clears_it() -> None:
    f, handle = make_drake(400, 130)
    me = f.actor(handle)
    me.behaviour_flags2 = 2
    assert _handle(f, handle) == me.idle_script and me.behaviour_flags2 == 1
    assert _handle(f, handle) == SCRIPTS.landing
    f, handle = make_drake(130)
    f.actor(handle).behaviour_flags2 = 9
    assert _handle(f, handle) == SCRIPTS.landing
    assert f.actor(handle).behaviour_flags2 == 0


@pytest.mark.parametrize(("arc", "sounds", "shake"), [(5, [61], False), (6, [62, 63], True)])
def test_flight_countdown_landing_uses_uncapped_arc_for_sound_and_shake(
    arc: int, sounds: list[int], shake: bool
) -> None:
    f, handle = make_drake()
    f.drake.flags, f.drake.remaining, f.caster.aim_arc = FLYING, 1, arc
    f.actor(handle).height = u16(-90)
    assert _handle(f, handle) == SCRIPTS.landing
    me = f.actor(handle)
    assert (me.height, me.behaviour_flags2, f.drake.flags) == (0, 3, 0)
    assert f.sounds == sounds and f.shake.active == shake


def test_pounce_contact_marks_first_dead_and_restarts_its_fixed_script() -> None:
    f, handle = make_drake(105)
    me = f.actor(handle)
    me.y, me.height = 103, u16(-20)
    f.drake.flags, f.drake.remaining = FLYING, 5
    f.caster.aim_steps, f.caster.aim_arc = 10, 4
    assert _handle(f, handle) == SCRIPTS.landing
    assert (me.x, me.y) == (100, 100)
    assert f.actor(1).hp == f.constants.dead_hp
    job = f.find_job(1)
    assert job is not None and job.script == SCRIPTS.victim_landing


def test_sound_pair_engine_calls_keep_decoded_order_without_random_draws() -> None:
    f, handle = make_drake()
    state = f.rng.state
    call = RoutineCall("drake_grab_sounds", handle, 1, 0, 0, 0, 1)
    f.routines[call.name](f, call)
    assert f.sounds == [64, 65] and f.rng.state == state
