"""Dragon decision boundaries, flights and linked objects (§10.30)."""

import pytest

from harddaysengine.sim.fight import DRAGON, KEEP, DragonPhase, FightError, Restart
from harddaysengine.sim.fight_ai.dragon import (
    dragon_handler,
    dragon_step,
    linked_jobs,
    part_handler,
)
from harddaysengine.sim.fight_script import RoutineCall
from harddaysengine.sim.m68k import u16
from tests.unit.dragon_setup import DRAGON_RULES as R, SCRIPTS, make_dragon
from tests.unit.knight_setup import RULES

CALL = RoutineCall("test", 5, 2, 0, 0, 0, 1)


def test_near_first_phase_starts_flight_with_exact_request() -> None:
    f = make_dragon()
    d = f.actor(DRAGON)
    result = dragon_handler(R, RULES, f, DRAGON)
    assert isinstance(result, Restart) and result.script == 608
    assert f.dragon_cells.phase is DragonPhase.FLYING_TO_NEAR and d.cooldown == R.near.steps
    req = f.flights.request
    assert (req.owner, req.target_x, req.target_height, req.steps, req.arc) == (
        5,
        R.near.x,
        R.near.height,
        6,
        30,
    )
    assert f.actor(f.dragon_cells.link_a).y == d.y + 8
    assert f.actor(f.dragon_cells.link_b).y == d.y - 17


@pytest.mark.parametrize(
    ("distance", "phase", "script", "expected"),
    [
        (94, DragonPhase.FAR_GROUND, 608, DragonPhase.FLYING_TO_NEAR),
        (95, DragonPhase.FAR_GROUND, SCRIPTS.strike8, DragonPhase.FAR_GROUND),
        (94, DragonPhase.NEAR_GROUND, SCRIPTS.breath, DragonPhase.NEAR_GROUND),
        (95, DragonPhase.NEAR_GROUND, 616, DragonPhase.FLYING_TO_FAR),
    ],
)
def test_exact_phase_boundary(
    distance: int, phase: DragonPhase, script: int, expected: DragonPhase
) -> None:
    f = make_dragon()
    d = f.actor(DRAGON)
    f.actor(1).x = d.x + distance
    d.reach_x = 200
    f.dragon_cells.phase = phase
    result = dragon_handler(R, RULES, f, DRAGON)
    assert isinstance(result, Restart) and result.script == script
    assert f.dragon_cells.phase is expected


@pytest.mark.parametrize(
    ("distance", "hit", "script", "action"),
    [
        (45, False, SCRIPTS.bite, 4),
        (46, False, SCRIPTS.breath, 32),
        (45, True, SCRIPTS.breath, 32),
    ],
)
def test_second_phase_attack_pick_and_breath_object(
    distance: int, hit: bool, script: int, action: int
) -> None:
    f = make_dragon()
    d = f.actor(DRAGON)
    f.actor(1).x = d.x + distance
    f.dragon_cells.phase = DragonPhase.NEAR_GROUND
    f.dragon_cells.was_hit = hit
    result = dragon_handler(R, RULES, f, DRAGON)
    assert isinstance(result, Restart) and result.script == script and d.action == action
    assert not f.dragon_cells.was_hit
    if action == 32:
        fire = next(j for j in f.engine.jobs if j.active and j.type == 40)
        assert (fire.x, fire.depth, fire.frame_list, fire.script) == (
            d.x + R.fire_x,
            d.y + R.fire_y,
            2,
            SCRIPTS.fire,
        )
        assert f.dragon_cells.reduced_reach


def test_depth_mismatch_or_dead_target_keeps_idle_and_clears_reduced() -> None:
    f = make_dragon()
    d = f.actor(DRAGON)
    f.dragon_cells.phase, f.dragon_cells.reduced_reach = DragonPhase.NEAR_GROUND, True
    f.actor(1).hp = 0
    result = dragon_handler(R, RULES, f, DRAGON)
    assert isinstance(result, Restart) and result.script == d.idle_script
    assert not f.dragon_cells.reduced_reach


def test_reduced_step_restores_actor_tunables_without_touching_other_jobs() -> None:
    f = make_dragon()
    d = f.actor(DRAGON)
    f.dragon_cells.reduced_reach = True
    later = f.engine.jobs[2]
    later.width, later.frame_height = 11, 22
    dragon_step(R, f)
    assert (d.reach_x, d.too_close_x) == (60, 10)
    assert (f.dragon_cells.saved_reach, f.dragon_cells.saved_close) == (60, 10)
    assert (later.width, later.frame_height) == (11, 22)
    job = f.find_job(DRAGON)
    assert job is not None and (job.x, job.depth) == (d.x, d.y)


@pytest.mark.parametrize(
    ("x", "target", "expected"), [(72, 300, 72), (71, 300, 74), (18, 0, 18), (19, 0, 16)]
)
def test_horizontal_step_does_not_reach_either_bound(x: int, target: int, expected: int) -> None:
    f = make_dragon()
    d = f.actor(DRAGON)
    d.x = x
    d.reach_x, d.too_close_x = 1, 0
    f.actor(1).x = target
    dragon_step(R, f)
    assert d.x == expected and d.facing == 1


def test_flight_tracks_depth_at_equality_and_discards_trajectory_depth() -> None:
    f = make_dragon()
    d = f.actor(DRAGON)
    dragon_handler(R, RULES, f, DRAGON)
    f.actor(1).y = d.y
    old = d.y
    result = dragon_handler(R, RULES, f, DRAGON)
    assert isinstance(result, Restart) and d.y == old - R.step and d.cooldown == 5
    assert result.script == 609 and d.anim_phase == 1
    for _ in range(5):
        dragon_handler(R, RULES, f, DRAGON)
    assert f.dragon_cells.phase is DragonPhase.NEAR_GROUND


def test_ignored_hit_keeps_running_script_and_advances_normal_flight_when_ended() -> None:
    f = make_dragon()
    d = f.actor(DRAGON)
    dragon_handler(R, RULES, f, DRAGON)
    d.attacker = f.dragon_cells.link_a
    assert dragon_handler(R, RULES, f, DRAGON) is KEEP
    job = f.find_job(DRAGON)
    assert job is not None
    job.running = False
    d.cooldown = 1
    f.engine.jobs[2].x = 77
    result = dragon_handler(R, RULES, f, DRAGON)
    assert isinstance(result, Restart) and result.script == 609
    assert d.cooldown == 0 and f.engine.jobs[2].x == 77
    assert f.dragon_cells.phase is DragonPhase.NEAR_GROUND


@pytest.mark.parametrize("kind", [0x0C, 0x34])
def test_hurt_precedes_hit_and_does_not_cancel_flight(kind: int) -> None:
    f = make_dragon()
    d = f.actor(DRAGON)
    att = f.actor(1)
    att.type, att.action = kind, 8
    d.attacker = d.hit_target = 1
    f.dragon_cells.phase = DragonPhase.FLYING_TO_FAR
    before = d.hp
    result = dragon_handler(R, RULES, f, DRAGON)
    assert isinstance(result, Restart) and result.script == (402 if kind == 0x0C else 403)
    assert d.hp < before
    assert f.dragon_cells.phase is DragonPhase.FLYING_TO_FAR and f.dragon_cells.was_hit
    if kind == 0x34:
        assert d.hp == before - R.dagger_damage
    assert any(j.active and j.script == R.spark.script for j in f.engine.jobs)


def test_bite_frees_target_job_but_leaves_classic_hp_for_finishing_script() -> None:
    f = make_dragon()
    d = f.actor(DRAGON)
    d.hit_target = 1
    d.action = 4
    hp = f.actor(1).hp
    result = dragon_handler(R, RULES, f, DRAGON)
    assert isinstance(result, Restart) and result.script == SCRIPTS.finish
    assert f.actor(1).hp == hp and not f.actor(1).active
    d.action = 8
    assert dragon_handler(R, RULES, f, DRAGON) is KEEP


@pytest.mark.parametrize(
    ("x", "near", "attacks"), [(85, True, True), (86, True, False), (85, False, False)]
)
def test_part_attack_boundary_and_action14(x: int, near: bool, attacks: bool) -> None:
    f = make_dragon()
    owner = f.dragon_cells.link_a
    p = f.actor(owner)
    f.actor(1).x = x
    f.actor(1).y = p.y if near else p.y + 20
    result = part_handler(R, f, owner)
    assert isinstance(result, Restart)
    assert result.script == (SCRIPTS.part_attack if attacks else p.idle_script)
    if attacks:
        assert p.action == 0x14


def test_part_hurt_hit_death_priorities_and_hop() -> None:
    f = make_dragon()
    owner = f.dragon_cells.link_a
    p = f.actor(owner)
    p.attacker = p.hit_target = 1
    f.actor(DRAGON).hp = 0
    result = part_handler(R, f, owner)
    assert isinstance(result, Restart) and result.script == p.idle_script and f.hop_table == 0
    p.attacker = 0
    part_handler(R, f, owner)
    assert (f.hop_facing, f.hop_table) == (1, R.hop_table)
    p.hit_target = 0
    result = part_handler(R, f, owner)
    assert isinstance(result, Restart) and result.script == SCRIPTS.part_death


def test_linked_job_callback_offsets_both_links_by_offset_and_gap() -> None:
    f = make_dragon()
    a, b = f.dragon_cells.link_a, f.dragon_cells.link_b
    linked_jobs(R, f, CALL)
    assert f.actor(a).y == 108 and f.actor(b).y == 83
    job_a, job_b = f.find_job(a), f.find_job(b)
    assert job_a is not None and job_a.depth == 108
    assert job_b is not None and job_b.depth == 83


def test_flight_negative_phase_raises_instead_of_reading_unrelated_memory() -> None:
    f = make_dragon()
    d = f.actor(DRAGON)
    dragon_handler(R, RULES, f, DRAGON)
    d.anim_phase = 127
    with pytest.raises(FightError, match="outside the table"):
        dragon_handler(R, RULES, f, DRAGON)
    assert d.anim_phase == 128 and d.cooldown == u16(R.near.steps - 1)
