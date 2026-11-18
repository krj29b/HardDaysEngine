"""Dragon decision boundaries, original job aliases, flights and linked objects (§10.30)."""

import pytest

from harddaysengine.sim.fight import DRAGON, KEEP, FightError, Restart
from harddaysengine.sim.fight_ai.dragon import (
    FLIGHT,
    HIT,
    PHASE2,
    REDUCED,
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
    assert f.dragon_cells.flags == FLIGHT | PHASE2 and d.cooldown == R.near.steps
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
    ("distance", "flags", "script", "flight"),
    [
        (94, 0, 608, True),
        (95, 0, SCRIPTS.strike8, False),
        (94, PHASE2, SCRIPTS.breath, False),
        (95, PHASE2, 616, True),
    ],
)
def test_exact_phase_boundary(distance: int, flags: int, script: int, flight: bool) -> None:
    f = make_dragon()
    d = f.actor(DRAGON)
    f.actor(1).x = d.x + distance
    d.reach_x = 200
    f.dragon_cells.flags = flags
    result = dragon_handler(R, RULES, f, DRAGON)
    assert isinstance(result, Restart) and result.script == script
    assert bool(f.dragon_cells.flags & FLIGHT) == flight


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
    f.dragon_cells.flags = PHASE2 | (HIT if hit else 0)
    result = dragon_handler(R, RULES, f, DRAGON)
    assert isinstance(result, Restart) and result.script == script and d.action == action
    assert not f.dragon_cells.flags & HIT
    if action == 32:
        fire = next(j for j in f.engine.jobs if j.active and j.type == 40)
        assert (fire.x, fire.depth, fire.frame_list, fire.script) == (
            d.x + R.fire_x,
            d.y + R.fire_y,
            2,
            SCRIPTS.fire,
        )
        assert f.dragon_cells.flags & REDUCED


def test_depth_mismatch_or_dead_target_keeps_idle_and_clears_reduced() -> None:
    f = make_dragon()
    d = f.actor(DRAGON)
    f.dragon_cells.flags = PHASE2 | REDUCED
    f.actor(1).hp = 0
    result = dragon_handler(R, RULES, f, DRAGON)
    assert isinstance(result, Restart) and result.script == d.idle_script
    assert not f.dragon_cells.flags & REDUCED


def test_reduced_step_keeps_actor_tunables_and_restores_into_later_job() -> None:
    f = make_dragon()
    d = f.actor(DRAGON)
    f.dragon_cells.flags = REDUCED
    dragon_step(R, f)
    assert (d.reach_x, d.too_close_x) == (R.reduced_reach, R.reduced_close)
    assert (f.dragon_cells.saved_reach, f.dragon_cells.saved_close) == (60, 10)
    later = f.engine.jobs[2]
    assert (later.width, later.frame_height) == (60, 10)
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
    assert not f.dragon_cells.flags & FLIGHT


def test_ignored_hit_keeps_running_script_and_aliases_ended_job_during_flight() -> None:
    f = make_dragon()
    d = f.actor(DRAGON)
    dragon_handler(R, RULES, f, DRAGON)
    d.attacker = f.dragon_cells.link_a
    assert dragon_handler(R, RULES, f, DRAGON) is KEEP
    job = f.find_job(DRAGON)
    assert job is not None
    job.running = False
    job.height = 90
    f.engine.jobs[2].x = 1
    before = (d.x, d.height, d.cooldown)
    dragon_handler(R, RULES, f, DRAGON)
    assert (d.x, d.height, d.cooldown) == before
    assert f.engine.jobs[2].x == 0 and job.height == 93
    assert not f.dragon_cells.flags & FLIGHT


def test_alias_flight_outside_job_table_raises() -> None:
    f = make_dragon()
    d = f.actor(DRAGON)
    d.attacker = f.dragon_cells.link_a
    job = f.find_job(DRAGON)
    assert job is not None
    f.engine.jobs.remove(job)
    f.engine.jobs.append(job)
    job.running = False
    f.dragon_cells.flags = FLIGHT
    with pytest.raises(FightError, match="beyond the job table"):
        dragon_handler(R, RULES, f, DRAGON)


@pytest.mark.parametrize("kind", [0x0C, 0x34])
def test_hurt_precedes_hit_and_does_not_cancel_flight(kind: int) -> None:
    f = make_dragon()
    d = f.actor(DRAGON)
    att = f.actor(1)
    att.type, att.action = kind, 8
    d.attacker = d.hit_target = 1
    f.dragon_cells.flags = FLIGHT
    before = d.hp
    result = dragon_handler(R, RULES, f, DRAGON)
    assert isinstance(result, Restart) and result.script == (402 if kind == 0x0C else 403)
    assert d.hp < before and f.dragon_cells.flags == FLIGHT | HIT
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


def test_linked_job_callback_preserves_both_depths_at_plus_offset() -> None:
    f = make_dragon()
    a, b = f.dragon_cells.link_a, f.dragon_cells.link_b
    linked_jobs(R, f, CALL)
    assert f.actor(a).y == f.actor(b).y == 108
    assert f.find_job(a) is not None and f.find_job(a).depth == 108
    assert f.find_job(b) is not None and f.find_job(b).depth == 108


def test_flight_negative_phase_raises_instead_of_reading_unrelated_memory() -> None:
    f = make_dragon()
    d = f.actor(DRAGON)
    dragon_handler(R, RULES, f, DRAGON)
    d.anim_phase = 127
    with pytest.raises(FightError, match="outside the table"):
        dragon_handler(R, RULES, f, DRAGON)
    assert d.anim_phase == 128 and d.cooldown == u16(R.near.steps - 1)
