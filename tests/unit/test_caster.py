"""Ratmen state phases, damage rules and shared-state ownership (§10.23)."""

from dataclasses import replace

import pytest

from harddaysengine.sim.fight import END, KEEP, CasterPhase, Fight, Restart
from harddaysengine.sim.fight_ai.caster import caster_handler
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
    assert actor.cooldown == CASTER.initial_wait
    assert fight.caster.phases[handle] is CasterPhase.INITIAL_FLIGHT
    assert fight.caster.initial_active
    assert (request.target_x, request.target_depth, request.target_height) == (170, 104, u16(-30))


@pytest.mark.parametrize("phase", [CasterPhase.OUT_FLIGHT, CasterPhase.QUICK_FLIGHT])
def test_flight_with_absent_slot_lands_to_idle(phase: CasterPhase) -> None:
    fight, handle = make_caster()
    me = fight.actor(handle)
    fight.caster.phases[handle] = phase
    me.height = u16(-50)
    assert _script(_handle(fight, handle)) == me.idle_script
    assert (fight.caster.phases.get(handle, CasterPhase.IDLE), me.height) == (CasterPhase.IDLE, 0)


def test_initial_landing_enters_hold_and_countdown_aims_without_resetting_cooldown() -> None:
    fight, handle = make_caster(130)
    me = fight.actor(handle)
    fight.caster.phases[handle] = CasterPhase.INITIAL_FLIGHT
    me.cooldown = 2
    fight.caster.initial_active = True
    assert _script(_handle(fight, handle)) == SCRIPTS.hold_near
    assert (fight.caster.phases[handle], me.cooldown) == (CasterPhase.HOLD, 1)
    _handle(fight, handle)
    assert fight.caster.phases[handle] is CasterPhase.QUICK_FLIGHT
    assert not fight.caster.initial_active
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
    fight.caster.phases[handle] = CasterPhase.OUT_FLIGHT if airborne else CasterPhase.HOLD
    attacker.action = action
    assert _script(_handle(fight, handle)) == script
    assert (me.hp, me.height, fight.caster.phases.get(handle, CasterPhase.IDLE)) == (
        CASTER.dead_hp,
        0,
        CasterPhase.IDLE,
    )
    assert fight.alive_now == fight.total == 0


def test_other_attacker_enters_think_and_clears_self_action() -> None:
    fight, handle = make_caster()
    me, other = fight.actor(handle), fight.actor(fight.second)
    other.action, me.action, me.attacker = 20, 12, fight.second
    fight.caster.wait = 2
    assert _script(_handle(fight, handle)) == me.idle_script
    assert (other.action, me.action, fight.caster.wait) == (20, 0, 1)


def test_catch_sign_extends_endurance_byte_and_pauses_victim() -> None:
    fight, handle = make_caster()
    me, target = fight.actor(handle), fight.actor(1)
    target.endurance = 125
    me.hit_target = 1
    fight.caster.phases[handle] = CasterPhase.OUT_FLIGHT
    assert _script(_handle(fight, handle)) == SCRIPTS.grab
    assert me.cooldown == u16(-122)
    assert fight.caster.victim_grabbed
    assert fight.caster.phases[handle] is CasterPhase.GRABBED
    job = fight.find_job(1)
    assert job is not None and job.paused


def test_escape_uses_second_port_then_release_toggles_victim_and_ends() -> None:
    fight, handle = make_caster()
    me = fight.actor(handle)
    fight.caster.phases[handle] = CasterPhase.GRABBED
    me.cooldown = 5
    fight.ports[:] = [FIRE | DOWN, 0]
    assert _script(_handle(fight, handle)) == SCRIPTS.grab
    fight.ports[:] = [0, FIRE | DOWN]
    assert _script(_handle(fight, handle)) == SCRIPTS.escape
    assert fight.caster.phases[handle] is CasterPhase.RELEASE
    fight.caster.victim_grabbed = True
    fight.toggle_pause(1)
    assert _handle(fight, handle) == END
    assert not fight.caster.victim_grabbed
    job = fight.find_job(1)
    assert job is not None and not job.paused and job.script == fight.actor(1).after_hit_script


def test_throw_damage_uses_true_signed_subtraction_and_only_changes_job_facing() -> None:
    fight, handle = make_caster()
    me, target = fight.actor(handle), fight.actor(1)
    fight.caster.phases[handle] = CasterPhase.THROW
    me.facing = 3
    target.hp, target.facing = 0x8000, 1
    assert _script(_handle(fight, handle)) == SCRIPTS.thrown
    job = fight.find_job(1)
    assert job is not None and job.script == SCRIPTS.victim_fall and job.facing == 3
    assert target.facing == 1 and target.hp == u16(-32768 - CASTER.throw_damage)
    assert fight.caster.phases[handle] is CasterPhase.OUT_FLIGHT


def test_slam_input_damage_or_squeeze_and_hold_hit_pause() -> None:
    fight, handle = make_caster()
    me = fight.actor(handle)
    fight.caster.phases[handle] = CasterPhase.HOLD
    me.hit_target = 1
    assert _script(_handle(fight, handle)) == SCRIPTS.slam_start
    assert fight.caster.phases[handle] is CasterPhase.SLAM
    assert fight.caster.victim_held
    me.hit_target = 0
    hp = fight.actor(1).hp
    assert _script(_handle(fight, handle)) == SCRIPTS.squeeze
    assert fight.actor(1).hp == hp - CASTER.squeeze_damage
    fight.ports[1] = FIRE | DOWN
    me.hp = 1
    assert _script(_handle(fight, handle)) == SCRIPTS.drop
    assert not fight.caster.victim_held


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


def test_damage_and_hurt_script_come_from_attacker() -> None:
    fight, handle = make_caster()
    me, first, attacker = fight.actor(handle), fight.actor(1), fight.actor(2)
    first.action, first.strength, attacker.action, attacker.strength = 4, 3, 8, 5
    me.attacker = 2
    expected = fight.table_long(me.hurt_scripts, 8)
    assert _script(_handle(fight, handle)) == expected
    assert me.hp == 12
    fight.caster.phases[handle] = CasterPhase.OUT_FLIGHT
    assert _script(_handle(fight, handle)) == SCRIPTS.flight_hurt


def test_shared_wait_only_counts_down_when_depth_is_near() -> None:
    fight, handle = make_caster(200, 150)
    fight.caster.wait = 7
    _handle(fight, handle)
    assert fight.caster.wait == 7


def test_unrecognized_attacker_writes_aim_map_depth_only_to_self() -> None:
    fight, handle = make_caster(200)
    me, other = fight.actor(handle), fight.actor(fight.second)
    me.attacker, me.map_y, other.map_y = fight.second, 77, 55
    fight.caster.initial_active = True
    assert _script(_handle(fight, handle)) == SCRIPTS.flight
    assert (me.map_y, other.map_y) == (fight.actor(1).y, 55)


def test_slam_overflow_comparison_uses_flags_before_word_wrap() -> None:
    fight, handle = make_caster()
    fight.caster.phases[handle] = CasterPhase.SLAM
    fight.actor(1).hp = 0x8000
    rules = replace(CASTER, squeeze_damage=1)
    result = caster_handler(rules, RULES, fight, handle)
    assert _script(result) == SCRIPTS.finish
    assert fight.actor(1).hp == 0x7FFF
