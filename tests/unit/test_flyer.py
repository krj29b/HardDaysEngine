"""Synthetic Flyer edge, damage, finishing and draw-order contracts (§10.26)."""

from dataclasses import replace

import pytest

from harddaysengine.sim.fight import Fight, Restart
from harddaysengine.sim.fight_ai.flyer import FLYER_TYPE, FlyerRules, flyer_handler
from harddaysengine.sim.m68k import u16
from harddaysengine.sim.rng import Rng
from tests.unit.brawler_setup import make_trogg_fight
from tests.unit.knight_setup import RULES

FLYER = FlyerRules(
    steps=(9, 7, 5, 11),
    finish_same=300,
    finish_opposite=310,
    initial_toggle=0,
    right_edge=280,
    enter_right=330,
    enter_left=u16(-36),
    facing_left=3,
    facing_right=1,
    toggle_xor=1,
    beam_mask=3,
    beam_shift=1,
    pause_mask=7,
    pause_or=3,
    pause_step=1,
    phase_step=1,
    phase_count=4,
    phase_reset=0,
)


def _fight(x: int = 150, facing: int = 1) -> tuple[Fight, int]:
    fight, handle = make_trogg_fight(x, kind=FLYER_TYPE)
    me = fight.actor(handle)
    me.facing = facing
    for owner in (1, 2, handle):
        a = fight.actor(owner)
        fight.start_job(a.idle_script, owner, 1, (a.x, 0, a.y), a.facing, a.type)
    fight.fight_active = True
    return fight, handle


def _handle(fight: Fight, handle: int, rules: FlyerRules = FLYER) -> int:
    result = flyer_handler(rules, RULES, fight, handle)
    assert isinstance(result, Restart)
    return result.script


@pytest.mark.parametrize(("x", "facing", "expected"), [(150, 1, 157), (150, 3, 143), (0, 3, 65529)])
def test_motion_advances_phase_before_reading_step_and_does_not_draw(
    x: int, facing: int, expected: int
) -> None:
    f, handle = _fight(x, facing)
    me = f.actor(handle)
    me.anim_timer, me.input, me.action = 5, 31, 0
    state = f.rng.state
    assert _handle(f, handle) == 71
    assert (me.x, me.anim_phase, me.anim_timer, me.input, me.action) == (expected, 1, 5, 0, 32)
    assert f.rng.state == state


@pytest.mark.parametrize(
    ("x", "facing", "enter", "next_facing"),
    [(280, 1, 330, 3), (u16(-1), 3, u16(-36), 1), (280, 0, 330, 3)],
)
def test_turn_uses_decoded_boundary_and_exactly_one_draw(
    x: int, facing: int, enter: int, next_facing: int
) -> None:
    f, handle = _fight(x, facing)
    me = f.actor(handle)
    f.beam_word = 3
    expected = Rng(f.rng.state)
    timer = ((expected.draw() & FLYER.pause_mask) | FLYER.pause_or) - FLYER.pause_step
    assert _handle(f, handle) == me.idle_script
    assert (me.x, me.facing, me.y, me.anim_phase) == (enter, next_facing, 100, 0)
    assert f.rng.state == expected.state and me.anim_timer == timer
    assert f.flyer.toggle == 1
    # The pause is not counted down on the next ordinary motion call.
    _handle(f, handle)
    assert me.anim_timer == timer and f.rng.state == expected.state


def test_alternate_turn_uses_beam_bits_and_wraps_depth_without_an_extra_draw() -> None:
    f, handle = _fight(280)
    me = f.actor(handle)
    f.actor(1).y, f.beam_word, f.flyer.toggle = 65534, 7, 1
    expected = Rng(f.rng.state)
    expected.draw()
    _handle(f, handle)
    assert (me.y, f.flyer.toggle) == (4, 0)
    assert f.rng.state == expected.state


def test_last_valid_phase_wraps_but_garbage_phase_raises() -> None:
    f, handle = _fight()
    me = f.actor(handle)
    me.anim_phase = 3
    assert _handle(f, handle) == 70
    assert me.x == 159 and me.anim_phase == 0
    me.anim_phase = 127
    with pytest.raises(ValueError, match="phase -128"):
        _handle(f, handle)


def test_zero_pause_operand_continues_motion_in_the_turn_call() -> None:
    f, handle = _fight(280)
    rules = replace(FLYER, pause_mask=0, pause_or=0, pause_step=0)
    assert _handle(f, handle, rules) == 71
    assert (f.actor(handle).x, f.actor(handle).anim_phase) == (323, 1)


def test_hurt_has_priority_and_uses_attacker_action_contact_damage_and_word_wrap() -> None:
    f, handle = _fight(280)
    me = f.actor(handle)
    me.attacker, me.hit_target, me.hp = 1, 2, 1
    f.actor(1).action = 4
    state = f.rng.state
    assert _handle(f, handle) == 21
    assert me.hp == u16(-2) and f.rng.state == state
    assert f.flyer.toggle == 0


def test_other_flyer_attacker_falls_through_to_hit_and_flyer_victim_to_motion() -> None:
    f, handle = _fight()
    me = f.actor(handle)
    me.attacker, me.hit_target = 2, 1
    f.actor(2).type = FLYER_TYPE
    assert _handle(f, handle) == me.after_hit_script
    assert me.hp == 20 and me.x == 150
    me.hit_target = 2
    assert _handle(f, handle) == 71
    assert me.hp == 20 and me.x == 157


@pytest.mark.parametrize(("hp", "hitting", "demo"), [(1, 0, False), (0, 2, False), (0, 0, True)])
def test_nonfinishing_hit_uses_alternate_script(hp: int, hitting: int, demo: bool) -> None:
    f, handle = _fight()
    me, victim = f.actor(handle), f.actor(1)
    me.hit_target, victim.hp, victim.hit_target, f.demo = 1, hp, hitting, demo
    assert _handle(f, handle) == me.after_hit_script
    assert f.fight_active and victim.active


@pytest.mark.parametrize(("facing", "script"), [(1, 300), (3, 310)])
def test_finisher_kills_first_job_and_chooses_script_from_hit_victim_facing(
    facing: int, script: int
) -> None:
    f, handle = _fight()
    me, victim = f.actor(handle), f.actor(2)
    me.hit_target, victim.hp, victim.facing = 2, 0, facing
    state = f.rng.state
    assert _handle(f, handle) == script
    job = f.find_job(1)
    assert job is not None and not job.active
    assert f.actor(1).hp == f.constants.dead_hp
    assert victim.active and not f.fight_active and f.end_timer == f.constants.end_delay
    assert f.rng.state == state
