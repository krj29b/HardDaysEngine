"""Guardian priorities, byte semantics, body jobs and grab transitions (§10.29)."""

import pytest

from harddaysengine.sim.fight import KEEP, Restart
from harddaysengine.sim.fight_ai.guardian import (
    HOLD,
    STAGE1,
    STAGE3,
    STAGE5,
    STAGE7,
    STAGE9,
    body_create,
    body_facing,
    body_remove,
    grab_damage,
    guardian_handler,
    victim_release,
)
from harddaysengine.sim.fight_script import RoutineCall
from harddaysengine.sim.m68k import u16
from tests.unit.guardian_setup import GUARDIAN, SCRIPTS, make_guardian
from tests.unit.knight_setup import RULES

CALL = RoutineCall("test", 6, 2, 0, 0, 0, 1)


@pytest.mark.parametrize(
    ("distance", "script", "action", "wait"),
    [
        (50, SCRIPTS.lunge, 32, 4),
        (51, SCRIPTS.strike8, 8, 7),
        (70, SCRIPTS.strike8, 8, 7),
        (71, SCRIPTS.strike4, 4, 9),
        (90, SCRIPTS.strike4, 4, 9),
    ],
)
def test_decision_boundaries(distance: int, script: int, action: int, wait: int) -> None:
    f, owner = make_guardian(100 + distance)
    me = f.actor(owner)
    result = guardian_handler(GUARDIAN, RULES, f, owner)
    assert isinstance(result, Restart) and result.script == script
    assert me.action == action and me.read(106, 1) == wait
    assert me.cooldown == wait << 8


def test_cooldown_decrements_high_byte_and_movement_has_no_clip() -> None:
    f, owner = make_guardian(200, 110)
    me = f.actor(owner)
    me.cooldown = 0x025A
    guardian_handler(GUARDIAN, RULES, f, owner)
    assert me.cooldown == 0x025A and (me.x, me.y) == (197, 107)
    me.y = 100
    guardian_handler(GUARDIAN, RULES, f, owner)
    assert me.cooldown == 0x015A and me.x == 194


@pytest.mark.parametrize(
    ("flag", "following", "script"),
    [
        (STAGE1, STAGE7, SCRIPTS.stage1),
        (STAGE3, 0, SCRIPTS.stage3),
        (STAGE9, 0, 11),
    ],
)
def test_stage_priority(flag: int, following: int, script: int) -> None:
    f, owner = make_guardian()
    f.guardian.flags = flag
    result = guardian_handler(GUARDIAN, RULES, f, owner)
    assert isinstance(result, Restart) and result.script == script
    assert f.guardian.flags == following


@pytest.mark.parametrize(
    ("stage", "distance", "grabs"),
    [
        (STAGE5, 64, False),
        (STAGE5, 65, True),
        (STAGE5, 75, True),
        (STAGE5, 76, False),
        (STAGE7, 79, False),
        (STAGE7, 80, True),
        (STAGE7, 95, True),
        (STAGE7, 96, False),
    ],
)
def test_inclusive_grab_windows(stage: int, distance: int, grabs: bool) -> None:
    f, owner = make_guardian(100 + distance)
    f.guardian.flags = stage
    guardian_handler(GUARDIAN, RULES, f, owner)
    assert bool(f.guardian.flags & HOLD) is grabs
    job = f.find_job(1)
    assert job is not None and job.paused == int(grabs)


@pytest.mark.parametrize("stage", [STAGE1, STAGE3])
def test_stage_release_does_not_write_misdirected_script_timer(stage: int) -> None:
    f, owner = make_guardian()
    me = f.actor(owner)
    me.cooldown = 0x1234
    f.guardian.flags = stage | HOLD
    f.toggle_pause(1)
    guardian_handler(GUARDIAN, RULES, f, owner)
    job = f.find_job(1)
    assert job is not None and job.script == SCRIPTS.release and job.paused == 0
    assert me.cooldown == 0x1234 and not f.guardian.flags & HOLD
    assert f.hop_facing == me.facing


def test_hurt_precedes_hit_and_flags_and_reads_previous_target() -> None:
    f, owner = make_guardian(120)
    me = f.actor(owner)
    me.attacker, me.hit_target, me.action = 1, 1, 4
    f.guardian.flags = STAGE5 | HOLD
    f.guardian.target = 2
    f.actor(2).x = 110
    guardian_handler(GUARDIAN, RULES, f, owner)
    assert me.x == 145 and me.hp < 20 and f.guardian.target == 2
    assert f.guardian.flags == 0 and me.action == 4


def test_hit_keeps_script_and_resets_shared_flags() -> None:
    f, owner = make_guardian()
    me = f.actor(owner)
    me.hit_target, me.cooldown = 1, 0x0055
    f.guardian.flags, f.guardian.spare = 0xFF, 0xFF
    assert guardian_handler(GUARDIAN, RULES, f, owner) is KEEP
    assert me.cooldown == 0x0855 and f.guardian.flags == f.guardian.spare == 0


def test_body_create_preserves_depth_typo_and_uses_job_facing() -> None:
    f, owner = make_guardian()
    main = f.find_job(owner)
    assert main is not None
    main.x, main.height, main.depth, main.facing = 123, 0xFFFE, 88, 1
    body_create(GUARDIAN, f, CALL)
    body = f.find_job(f.guardian.body)
    assert body is not None
    assert (body.x, body.height, body.depth, body.frame_list) == (123, 0xFFFE, 123, 2)
    main.facing = 3
    body_facing(f, CALL)
    assert body.facing == 3
    guardian_handler(GUARDIAN, RULES, f, owner)
    me = f.actor(owner)
    assert (body.x, body.depth) == (me.x, me.y)
    body_remove(f, CALL)
    assert not body.active and f.actor(f.guardian.body).active == 0


@pytest.mark.parametrize(("x", "facing", "expected"), [(10, 3, 2), (270, 1, 279), (100, 1, 140)])
def test_grab_damage_and_job_release_clamping(x: int, facing: int, expected: int) -> None:
    f, owner = make_guardian(x)
    f.actor(owner).facing = facing
    hp = f.actor(1).hp
    grab_damage(GUARDIAN, f, CALL)
    assert f.actor(1).hp == u16(hp - GUARDIAN.grab_damage)
    victim_release(GUARDIAN, f, CALL)
    job = f.find_job(1)
    assert job is not None and job.x == expected and job.paused == 0
    assert job.facing == facing ^ 2 and job.depth == f.actor(owner).y
    assert job.script == f.actor(1).idle_script


def test_dead_victim_requests_fade_and_fixed_death_script() -> None:
    f, _ = make_guardian()
    f.actor(1).hp = 0
    victim_release(GUARDIAN, f, CALL)
    job = f.find_job(1)
    assert f.guardian.fade and job is not None and job.script == SCRIPTS.victim_death


def test_simultaneous_stage_flags_follow_original_order_without_random_draws() -> None:
    f, owner = make_guardian()
    f.guardian.flags = STAGE1 | STAGE3 | STAGE9 | STAGE7 | STAGE5
    before = f.rng.state
    result = guardian_handler(GUARDIAN, RULES, f, owner)
    assert isinstance(result, Restart) and result.script == SCRIPTS.stage1
    assert not f.guardian.flags & STAGE1 and f.guardian.flags & STAGE3
    assert f.rng.state == before


def test_missing_jobs_are_safe_and_fixed_sounds_preserve_order() -> None:
    f, owner = make_guardian()
    f.kill_job(owner)
    job = f.find_job(owner)
    assert job is not None
    job.owner = 0
    body_create(GUARDIAN, f, CALL)
    assert f.find_job(f.guardian.body) is None
    body_facing(f, CALL)
    body_remove(f, CALL)
    f.routines["guardian_fixed_sounds"](f, CALL)
    assert f.sounds == list(GUARDIAN.sounds)
