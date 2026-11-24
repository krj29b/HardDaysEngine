"""Guardian priorities, byte semantics, body jobs and grab transitions (§10.29)."""

import pytest

from harddaysengine.sim.fight import KEEP, GuardianStage, Restart
from harddaysengine.sim.fight_ai.guardian import (
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
    assert me.action == action and me.cooldown_byte() == wait
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
    ("stage", "following", "script"),
    [
        (GuardianStage.STAGE1, GuardianStage.STAGE7, SCRIPTS.stage1),
        (GuardianStage.STAGE3, GuardianStage.IDLE, SCRIPTS.stage3),
        (GuardianStage.STAGE9, GuardianStage.IDLE, 11),
    ],
)
def test_stage_transitions(stage: GuardianStage, following: GuardianStage, script: int) -> None:
    f, owner = make_guardian()
    f.guardian.stage = stage
    result = guardian_handler(GUARDIAN, RULES, f, owner)
    assert isinstance(result, Restart) and result.script == script
    assert f.guardian.stage is following


@pytest.mark.parametrize(
    ("stage", "distance", "grabs"),
    [
        (GuardianStage.STAGE5, 64, False),
        (GuardianStage.STAGE5, 65, True),
        (GuardianStage.STAGE5, 75, True),
        (GuardianStage.STAGE5, 76, False),
        (GuardianStage.STAGE7, 79, False),
        (GuardianStage.STAGE7, 80, True),
        (GuardianStage.STAGE7, 95, True),
        (GuardianStage.STAGE7, 96, False),
    ],
)
def test_inclusive_grab_windows(stage: GuardianStage, distance: int, grabs: bool) -> None:
    f, owner = make_guardian(100 + distance)
    f.guardian.stage = stage
    guardian_handler(GUARDIAN, RULES, f, owner)
    assert f.guardian.holding is grabs
    job = f.find_job(1)
    assert job is not None and job.paused == int(grabs)


@pytest.mark.parametrize("stage", [GuardianStage.STAGE1, GuardianStage.STAGE3])
def test_stage_release_does_not_write_misdirected_script_timer(stage: GuardianStage) -> None:
    f, owner = make_guardian()
    me = f.actor(owner)
    me.cooldown = 0x1234
    f.guardian.stage, f.guardian.holding = stage, True
    f.toggle_pause(1)
    guardian_handler(GUARDIAN, RULES, f, owner)
    job = f.find_job(1)
    assert job is not None and job.script == SCRIPTS.release and job.paused == 0
    assert me.cooldown == 0x1234 and not f.guardian.holding
    assert f.hop_facing == me.facing


def test_hurt_precedes_hit_and_stages_and_reads_previous_target() -> None:
    f, owner = make_guardian(120)
    me = f.actor(owner)
    me.attacker, me.hit_target, me.action = 1, 1, 4
    f.guardian.stage, f.guardian.holding = GuardianStage.STAGE5, True
    f.guardian.target = 2
    f.actor(2).x = 110
    guardian_handler(GUARDIAN, RULES, f, owner)
    assert me.x == 145 and me.hp < 20 and f.guardian.target == 2
    assert f.guardian.stage is GuardianStage.IDLE and not f.guardian.holding and me.action == 4


def test_hit_keeps_script_and_resets_stage_and_hold() -> None:
    f, owner = make_guardian()
    me = f.actor(owner)
    me.hit_target, me.cooldown = 1, 0x0055
    f.guardian.stage, f.guardian.holding = GuardianStage.STAGE7, True
    assert guardian_handler(GUARDIAN, RULES, f, owner) is KEEP
    assert me.cooldown == 0x0855
    assert f.guardian.stage is GuardianStage.IDLE and not f.guardian.holding


def test_body_create_uses_job_depth_and_job_facing() -> None:
    f, owner = make_guardian()
    main = f.find_job(owner)
    assert main is not None
    main.x, main.height, main.depth, main.facing = 123, 0xFFFE, 88, 1
    body_create(GUARDIAN, f, CALL)
    body = f.find_job(f.guardian.body)
    assert body is not None
    assert (body.x, body.height, body.depth, body.frame_list) == (123, 0xFFFE, 88, 2)
    assert f.actor(f.guardian.body).y == 88
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


def test_full_grab_stage_chain_runs_deterministically_without_random_draws() -> None:
    f, owner = make_guardian(170)
    before = f.rng.state
    f.guardian.stage = GuardianStage.STAGE5
    assert isinstance(guardian_handler(GUARDIAN, RULES, f, owner), Restart)
    assert f.guardian.stage is GuardianStage.STAGE1 and f.guardian.holding
    assert isinstance(guardian_handler(GUARDIAN, RULES, f, owner), Restart)
    assert f.guardian.stage is GuardianStage.STAGE7 and not f.guardian.holding
    assert f.rng.state == before


def test_missing_jobs_are_safe() -> None:
    f, owner = make_guardian()
    f.kill_job(owner)
    job = f.find_job(owner)
    assert job is not None
    job.owner = 0
    body_create(GUARDIAN, f, CALL)
    assert f.find_job(f.guardian.body) is None
    body_facing(f, CALL)
    body_remove(f, CALL)
    assert f.sounds == []
