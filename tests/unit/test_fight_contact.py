"""The contact test and the contact pass (ARCHITECTURE.md §10.10, §10.17)."""

from __future__ import annotations

from harddaysengine.sim.actor import Actor
from harddaysengine.sim.fight_contact import (
    Contact,
    contact_scan,
    contact_test,
    depth_close,
    overlap,
)
from harddaysengine.sim.fight_script import (
    CelShape,
    FrameShape,
    HitShape,
    ListEntry,
    ScriptEngine,
    ScriptImage,
)


def _rows(width: int, height: int, columns: dict[int, set[int]]) -> FrameShape:
    """A frame whose set pixels are ``columns[row]`` (stored orientation)."""
    aligned = ((width + 15) >> 4) << 4
    rows = tuple(
        sum(1 << (aligned - 1 - c) for c in columns.get(row, set())) for row in range(height)
    )
    return FrameShape(width, height, rows)


FULL = _rows(16, 16, {row: set(range(16)) for row in range(16)})
WEAPON = CelShape("w", (FrameShape(16, 8, (0,) * 8),), (HitShape(4, 4, ((2, 2),)),))
LONG_REACH = CelShape("r", (FrameShape(16, 8, (0,) * 8),), (HitShape(12, 4, ((12, 0),)),))
BARE = CelShape("n", (FrameShape(16, 8, (0,) * 8),), None)
EMPTY_HIT = CelShape("e", (FrameShape(16, 8, (0,) * 8),), (None,))


def _engine(*cels: CelShape) -> ScriptEngine:
    def ignore(*_args: object) -> None:
        return None

    return ScriptEngine(
        image=ScriptImage(bytes(2), {}),
        actors=[Actor() for _ in range(4)],
        cels={cel.name: cel for cel in cels},
        frame_lists={},
        call=ignore,
        spawn=ignore,
    )


def test_interval_test_counts_b_ending_on_a0_but_not_b_starting_on_a1() -> None:
    assert overlap(10, 20, 5, 10)
    assert not overlap(10, 20, 20, 30)
    assert overlap(10, 20, 12, 15)
    assert not overlap(10, 20, 0, 9)
    assert not overlap(10, 20, 21, 30)


def test_depth_close_uses_a_16_bit_difference() -> None:
    assert depth_close(50, 60)
    assert not depth_close(50, 61)
    assert depth_close(0x8000, 0)


def test_a_point_on_an_opaque_pixel_hits() -> None:
    engine = _engine(WEAPON, CelShape("d", (FULL,), None))
    hit = contact_test(engine, ListEntry("w", 0, 100, 100), ListEntry("d", 0, 100, 100))
    assert hit == Contact(102, 102)


def test_a_point_on_a_clear_pixel_misses() -> None:
    defender = CelShape("d", (_rows(16, 16, {2: {9}}),), None)
    engine = _engine(WEAPON, defender)
    assert contact_test(engine, ListEntry("w", 0, 100, 100), ListEntry("d", 0, 100, 100)) is None


def test_no_hit_set_or_an_empty_record_never_hits() -> None:
    engine = _engine(BARE, EMPTY_HIT, CelShape("d", (FULL,), None))
    assert contact_test(engine, ListEntry("n", 0, 100, 100), ListEntry("d", 0, 100, 100)) is None
    assert contact_test(engine, ListEntry("e", 0, 100, 100), ListEntry("d", 0, 100, 100)) is None


def test_a_mirrored_attacker_tests_and_reports_the_mirrored_point() -> None:
    # Weapon is 16px wide with hit point at px=2; mirrored gate_x is 100 + 16 - 2 = 114 (col 14).
    defender_raw_only = CelShape("d", (_rows(16, 16, {2: {2}}),), None)
    engine = _engine(WEAPON, defender_raw_only)
    engine.mirrored.add(("w", 0))
    assert contact_test(engine, ListEntry("w", 0, 100, 100), ListEntry("d", 0, 100, 100)) is None

    defender_mirrored_col = CelShape("d", (_rows(16, 16, {2: {14}}),), None)
    engine = _engine(WEAPON, defender_mirrored_col)
    engine.mirrored.add(("w", 0))
    hit = contact_test(engine, ListEntry("w", 0, 100, 100), ListEntry("d", 0, 100, 100))
    assert hit == Contact(114, 102)


def test_a_mirrored_defender_is_read_across_its_aligned_row() -> None:
    defender = CelShape("d", (_rows(10, 16, {2: {9}}),), None)
    engine = _engine(WEAPON, defender)
    hurt = ListEntry("d", 0, 104 - 6 + 2, 100)  # point column 6 of the hurt cel
    attack = ListEntry("w", 0, hurt.x + 4, 100)
    assert contact_test(engine, attack, hurt) is None
    engine.mirrored.add(("d", 0))
    assert contact_test(engine, attack, hurt) == Contact(hurt.x + 6, 102)


def test_opaque_enforces_strict_frame_bounds_without_scanline_wraparound() -> None:
    frame = _rows(16, 4, {1: {0}})
    assert frame.opaque(0, 1, mirrored=False)
    assert not frame.opaque(16, 0, mirrored=False)
    assert not frame.opaque(0xFFFF, 1, mirrored=False)
    assert not frame.opaque(0, 4, mirrored=False)
    assert not frame.opaque(0, 0xFFFF, mirrored=False)
    # Mirrored LONG_REACH at x=104 (width=16, px=12) tests gate_x = 104 + 16 - 12 = 108 (col 8),
    # which misses col 0 on row 1 and never wraps past width into row 1.
    engine = _engine(LONG_REACH, CelShape("d", (frame,), None))
    engine.mirrored.add(("r", 0))
    assert contact_test(engine, ListEntry("r", 0, 104, 100), ListEntry("d", 0, 100, 100)) is None


def test_the_pass_links_the_first_hit_and_empties_per_job_lists() -> None:
    engine = _engine(WEAPON, CelShape("d", (FULL,), None))
    attacker, near, far = engine.jobs[0], engine.jobs[1], engine.jobs[2]
    for job, owner, depth in ((attacker, 1, 50), (near, 2, 60), (far, 3, 61)):
        job.active, job.owner, job.depth = True, owner, depth
    attacker.attack.append(ListEntry("w", 0, 100, 100))
    for job in (far, near):
        job.hurt.append(ListEntry("d", 0, 100, 100))
    cleared: list[bool] = []
    contact_scan(engine, lambda: cleared.append(True))
    actors = engine.actors
    assert cleared == [True]
    assert (actors[0].hit_target, actors[1].attacker, actors[2].attacker) == (2, 1, 0)
    assert (actors[1].hit_x, actors[1].hit_y) == (102, 102)
    assert attacker.attack == []
    assert near.hurt == [] and far.hurt == []


def test_a_job_never_hits_itself() -> None:
    engine = _engine(WEAPON, CelShape("d", (FULL,), None))
    job = engine.jobs[0]
    job.active, job.owner = True, 1
    job.attack.append(ListEntry("w", 0, 100, 100))
    job.hurt.append(ListEntry("d", 0, 100, 100))
    contact_scan(engine, lambda: None)
    assert engine.actors[0].hit_target == 0
