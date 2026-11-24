"""Fight plumbing: pools, jobs, job pass, fight-level routines (ARCHITECTURE.md §10.18)."""

from __future__ import annotations

import struct

import pytest

from harddaysengine.sim.actor import Actor
from harddaysengine.sim.fight import (
    DRAGON,
    END,
    IDLE_OBJECT_TYPE,
    KEEP,
    POOL_FIRST,
    POOL_SIZE,
    Fight,
    FightConstants,
    FightError,
    HandlerResult,
    end_fight,
    fighter_died,
    idle_object,
    mark_first_dead,
    pause_all,
    restart_from,
)
from harddaysengine.sim.fight_script import CelShape, FrameShape, HitShape, ScriptImage
from harddaysengine.sim.trajectory import FlightSlot

KNIGHT_TYPE = 0x0C
BASE = 2


def _draw(slot: int, frame: int, flags: int = 0) -> bytes:
    return struct.pack(">BBbBh", slot * 4, frame, 0, flags, 0)


SOLID = FrameShape(16, 16, (0xFFFF,) * 16)
CELS = {
    "body": CelShape("body", (SOLID,), None),
    "blade": CelShape("blade", (SOLID,), (HitShape(4, 4, ((1, 1),)),)),
}
LISTS = {1: ("body", "blade", None, None, None)}


def _fight(code: bytes = b"\xff\xff", routines: dict[int, str] | None = None) -> Fight:
    knights = [Actor(hp=20, active=1) for _ in range(4)]
    return Fight(
        image=ScriptImage(bytes(BASE) + code, routines or {}),
        cels=CELS,
        frame_lists=LISTS,
        knights=knights,
        dragon=Actor(),
        constants=FightConstants(end_delay=35, dead_hp=0xFFFF),
    )


def test_a_fight_needs_four_knights() -> None:
    with pytest.raises(ValueError, match="4 knight records"):
        Fight(ScriptImage(b"", {}), {}, {}, [Actor()], Actor(), FightConstants(35, 0xFFFF))


def test_records_are_taken_in_order_cleared_on_alloc_and_bounded_to_twenty() -> None:
    fight = _fight()
    dirty = fight.actor(POOL_FIRST)
    dirty.behaviour_flags, dirty.cooldown, dirty.hp = 0x5A, 17, 99
    handles = [fight.alloc_record() for _ in range(POOL_SIZE)]
    assert handles == list(range(POOL_FIRST, POOL_FIRST + POOL_SIZE))
    assert (dirty.active, dirty.behaviour_flags, dirty.cooldown, dirty.hp) == (1, 0, 0, 0)
    with pytest.raises(FightError, match="20 fight records"):
        fight.alloc_record()
    with pytest.raises(FightError, match="no actor"):
        fight.actor(0)
    with pytest.raises(FightError, match="no actor"):
        fight.actor(POOL_FIRST + POOL_SIZE)


def test_spawn_places_the_record_and_starts_a_job() -> None:
    fight = _fight()
    fight.engine.jobs[0].work.vx = 9
    handle = fight.spawn_actor(BASE, 1, (-60, 0, 100), 1, 0x30)
    actor = fight.actor(handle)
    assert (actor.x, actor.y, actor.facing, actor.type, actor.frame_list) == (
        0xFFC4,
        100,
        1,
        0x30,
        1,
    )
    job = fight.find_job(handle)
    assert job is not None
    assert (job.active, job.running, job.x, job.depth, job.type, job.work.vx) == (
        True, True, 0xFFC4, 100, 0x30, 0,
    )  # fmt: skip


def test_start_job_gives_up_when_all_ten_are_taken() -> None:
    fight = _fight()
    for _ in range(10):
        assert fight.start_job(BASE, 1, 1, (0, 0, 0), 1, KNIGHT_TYPE) is not None
    assert fight.start_job(BASE, 1, 1, (0, 0, 0), 1, KNIGHT_TYPE) is None


def test_owner_lookup_matches_inactive_jobs_and_drives_pause_kill_restart() -> None:
    fight = _fight()
    job = fight.start_job(BASE, 1, 1, (0, 0, 0), 1, KNIGHT_TYPE)
    assert job is not None
    job.active = False
    assert fight.find_job(1) is job
    fight.toggle_pause(1)
    assert job.paused == 1
    job.active = True
    job.work.loop_active = True
    fight.restart_job(1, 40)
    assert (job.script, job.running, job.work.loop_active) == (40, True, False)
    fight.kill_job(1)
    assert (job.active, job.running, fight.actor(1).active) == (False, False, 0)
    fight.toggle_pause(7)  # no job: nothing happens


def test_clear_links_covers_knights_dragon_and_twenty_pool_records() -> None:
    fight = _fight()
    for handle in (1, 4, DRAGON, POOL_FIRST, POOL_FIRST + POOL_SIZE - 1):
        fight.actor(handle).hit_target = fight.actor(handle).attacker = 3
    fight.clear_links()
    assert len(fight.actors) == POOL_FIRST + POOL_SIZE - 1
    assert all(
        fight.actor(h).hit_target == fight.actor(h).attacker == 0
        for h in range(1, POOL_FIRST + POOL_SIZE)
    )


def test_killing_or_ending_a_job_releases_its_trajectory_slots() -> None:
    # 1) kill_job
    fight = _fight()
    fight.flights.slots = [
        FlightSlot(owner=POOL_FIRST, remaining=5),
        FlightSlot(owner=2, remaining=4),
    ]
    h = fight.spawn_actor(BASE, 1, (10, 0, 50), 1, KNIGHT_TYPE)
    assert h == POOL_FIRST
    fight.kill_job(h)
    assert (fight.flights.slots[0].owner, fight.flights.slots[0].remaining) == (0, 0)
    assert fight.flights.slots[1].owner == 2

    # 2) Handler returning END
    fight = _fight()
    fight.flights.slots = [FlightSlot(owner=POOL_FIRST, remaining=6)]
    fight.handlers[KNIGHT_TYPE] = lambda _f, _h: END
    h = fight.spawn_actor(BASE, 1, (10, 0, 50), 1, KNIGHT_TYPE)
    job = fight.find_job(h)
    assert job is not None
    job.running = False
    fight.job_pass()
    assert (fight.flights.slots[0].owner, fight.flights.slots[0].remaining) == (0, 0)

    # 3) Script opcode $BC (_op_kill)
    fight = _fight(bytes((0xBC, 0)) + b"\xff\xff")
    fight.flights.slots = [FlightSlot(owner=POOL_FIRST, remaining=7)]
    h = fight.spawn_actor(BASE, 1, (10, 0, 50), 1, KNIGHT_TYPE)
    fight.engine.tick()
    assert (fight.flights.slots[0].owner, fight.flights.slots[0].remaining) == (0, 0)


def test_job_pass_calls_handlers_only_on_links_or_an_ended_script() -> None:
    fight = _fight()
    calls: list[int] = []

    def handler(_fight: Fight, handle: int) -> HandlerResult:
        calls.append(handle)
        return KEEP

    fight.handlers[KNIGHT_TYPE] = handler
    job = fight.start_job(BASE, 1, 1, (0, 0, 0), 1, KNIGHT_TYPE)
    assert job is not None
    fight.job_pass()
    fight.actor(1).attacker = 6
    fight.job_pass()
    fight.actor(1).attacker = 0
    job.running = False
    fight.job_pass()
    assert calls == [1, 1]


def test_job_pass_end_and_restart() -> None:
    fight = _fight()
    fight.handlers[KNIGHT_TYPE] = lambda f, h: END
    job = fight.start_job(BASE, 1, 1, (0, 0, 0), 1, KNIGHT_TYPE)
    assert job is not None
    job.running = False
    fight.job_pass()
    assert (job.active, fight.actor(1).active) == (False, 0)

    fight = _fight()
    owner = fight.actor(1)
    owner.x, owner.height, owner.y, owner.facing = 30, 0, 75, 3
    fight.handlers[KNIGHT_TYPE] = lambda f, h: restart_from(f.actor(h), 99)
    job = fight.start_job(BASE, 1, 1, (0, 0, 0), 1, KNIGHT_TYPE)
    assert job is not None
    job.running = False
    job.work.loop_active = True
    fight.job_pass()
    assert (job.script, job.x, job.depth, job.facing, job.running) == (99, 30, 75, 3, True)
    assert job.work.loop_active  # a restart keeps the work block


def test_a_type_without_handler_is_an_error() -> None:
    fight = _fight()
    job = fight.start_job(BASE, 1, 1, (0, 0, 0), 1, 0x18)
    assert job is not None
    job.running = False
    with pytest.raises(FightError, match=r"type \$18"):
        fight.job_pass()


def test_idle_object_clears_links_and_keeps() -> None:
    fight = _fight()
    actor = fight.actor(1)
    actor.hit_target, actor.attacker = 2, 3
    assert idle_object(fight, 1) is KEEP
    assert (actor.hit_target, actor.attacker) == (0, 0)


def test_end_fight_only_acts_while_the_fight_is_active() -> None:
    fight = _fight()
    end_fight(fight)
    assert fight.end_timer == 0
    fight.fight_active = True
    end_fight(fight)
    assert (fight.fight_active, fight.end_timer) == (False, 35)


def test_fighter_died_ends_the_fight_when_the_first_fighter_is_down() -> None:
    fight = _fight()
    fight.first, fight.fight_active = 1, True
    fight.actor(1).hp = 0
    fight.alive_now = fight.total = 2
    fighter_died(fight)
    assert (fight.fight_active, fight.alive_now) == (False, 2)


def test_fighter_died_counts_down_and_ends_on_the_last_creature() -> None:
    fight = _fight()
    fight.first, fight.fight_active = 1, True
    fight.alive_now, fight.max_alive, fight.total = 1, 1, 1
    fighter_died(fight)
    assert (fight.alive_now, fight.total, fight.fight_active) == (0, 0, False)


def test_fighter_died_refills_the_wave() -> None:
    fight = _fight()
    fight.first, fight.fight_active = 1, True
    fight.alive_now, fight.max_alive, fight.total = 2, 2, 5

    def spawn(f: Fight) -> None:
        f.alive_now += 1

    fight.next_creature = spawn
    fighter_died(fight)
    assert (fight.alive_now, fight.total, fight.fight_active) == (2, 4, True)
    fight.next_creature = None
    with pytest.raises(FightError, match="hangs"):
        fighter_died(fight)


def test_mark_first_dead_and_pause_all() -> None:
    fight = _fight()
    fight.first = 2
    mark_first_dead(fight)
    assert fight.actor(2).hp == 0xFFFF
    kept = fight.spawn_actor(BASE, 1, (0, 0, 0), 1, 0x24)
    other = fight.spawn_actor(BASE, 1, (0, 0, 0), 1, 0x24)
    fight.start_job(BASE, DRAGON, 1, (0, 0, 0), 1, 0x14)
    fight.kept = kept
    pause_all(fight)
    assert [_paused(fight, h) for h in (kept, other, DRAGON)] == [0, 1, 1]


def _paused(fight: Fight, handle: int) -> int:
    job = fight.find_job(handle)
    assert job is not None
    return job.paused


def test_step_reports_skipped_routines_and_spawns_idle_objects() -> None:
    code = struct.pack(">BBI", 0xB0, 0, 0) + struct.pack(">BBI", 0xB8, 0, BASE + 14)
    code += b"\xff\xff" + _draw(0, 0) + b"\xff\xff"
    fight = _fight(code, routines={BASE + 2: "sound_pick_1"})
    fight.handlers[KNIGHT_TYPE] = lambda f, h: KEEP
    fight.first = 1
    fight.start_job(BASE, 1, 1, (40, 0, 90), 3, KNIGHT_TYPE)
    out = fight.step(vbl=0)
    assert [c.name for c in out.skipped_calls] == ["sound_pick_1"]
    spawned = fight.actor(POOL_FIRST)
    assert (spawned.type, spawned.x, spawned.y, spawned.facing) == (IDLE_OBJECT_TYPE, 40, 90, 3)
    # The sort moved the knight's job behind the inactive records, so the object took slot 0,
    # which this pass had already visited: it first runs in the next tick.
    assert out.tick.draws == []
    assert fight.find_job(1) is fight.engine.jobs[-1]
    out = fight.step(vbl=6)
    assert ([d.cel for d in out.tick.draws], out.skipped_calls) == (["body"], [])


def test_step_runs_the_contact_pass_after_the_tick() -> None:
    code = _draw(1, 0, flags=0x02) + b"\xff\x00" + b"\xff\xff"
    code += _draw(0, 0, flags=0x01) + b"\xff\x00" + b"\xff\xff"
    fight = _fight(code)
    fight.handlers[KNIGHT_TYPE] = lambda f, h: KEEP
    fight.start_job(BASE, 1, 1, (100, 0, 50), 1, KNIGHT_TYPE)
    fight.start_job(BASE + 10, 2, 1, (100, 0, 50), 1, KNIGHT_TYPE)
    fight.step(vbl=0)
    assert (fight.actor(1).hit_target, fight.actor(2).attacker) == (2, 1)
    assert (fight.actor(2).hit_x, fight.actor(2).hit_y) == (101, 51)
