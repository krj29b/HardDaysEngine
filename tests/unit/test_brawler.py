"""The brawler handler of the Troggs (ARCHITECTURE.md §10.20)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harddaysengine.sim.fight import END, KEEP, Fight, Restart
from harddaysengine.sim.fight_ai.brawler import (
    AXE_TYPE,
    AXE_TYPE_2,
    BRAWLER_TYPES,
    SPEAR_TYPE,
    brawler_handler,
    cooldown,
    set_cooldown,
)
from harddaysengine.sim.fight_moves import LEFT, RIGHT, UP
from harddaysengine.sim.rng import Rng
from tests.unit.brawler_setup import (
    BRAWLER,
    SIDE_SCRIPTS,
    TROGG_TABLES,
    UP_SCRIPTS,
    make_trogg_fight,
)
from tests.unit.knight_setup import RULES

if TYPE_CHECKING:
    from collections.abc import Callable

STRIKE = TROGG_TABLES.action_scripts[BRAWLER.strike_action // 4]
HEAVY = TROGG_TABLES.action_scripts[BRAWLER.heavy_action // 4]
IDLE = TROGG_TABLES.idle_script
AFTER_HIT = TROGG_TABLES.after_hit_script


def _seed(accept: Callable[[int], bool]) -> int:
    """The first of a spread of states whose percent draw ``accept`` takes."""
    seeds = (i * 0x9E3779B1 & 0xFFFFFFFF for i in range(1, 1000))
    return next(seed for seed in seeds if accept(Rng(seed).percent()))


def _handle(fight: Fight, handle: int) -> object:
    return brawler_handler(BRAWLER, RULES, fight, handle)


def _script(result: object) -> int:
    assert isinstance(result, Restart)
    return result.script


def test_install_registers_the_three_types() -> None:
    fight, _ = make_trogg_fight(150)
    assert set(BRAWLER_TYPES) <= set(fight.handlers)


def test_the_cooldown_is_the_high_byte_of_its_word() -> None:
    fight, handle = make_trogg_fight(150)
    trogg = fight.actor(handle)
    trogg.cooldown = 0x0305
    set_cooldown(trogg, 9)
    assert (trogg.cooldown, cooldown(trogg)) == (0x0905, 9)


# ---- decide ----------------------------------------------------------------------------------


def test_a_high_roll_strikes() -> None:
    fight, handle = make_trogg_fight(150)
    fight.rng = Rng(_seed(lambda p: p > BRAWLER.gloat_odds))
    result = _handle(fight, handle)
    trogg = fight.actor(handle)
    assert result == Restart(STRIKE, 150, 0, 100, 3)
    assert (trogg.action, cooldown(trogg)) == (BRAWLER.strike_action, BRAWLER.strike_cooldown)


@pytest.mark.parametrize(("defending", "script"), [(False, STRIKE), (True, HEAVY)])
def test_a_low_roll_strikes_unless_the_first_fighter_defends(defending: bool, script: int) -> None:
    fight, handle = make_trogg_fight(150)
    fight.rng = Rng(_seed(lambda p: p <= BRAWLER.gloat_odds))
    fight.actor(1).action = BRAWLER.defend_action if defending else 0
    assert _script(_handle(fight, handle)) == script


def test_beyond_close_range_the_heavy_blow_needs_no_draw() -> None:
    fight, handle = make_trogg_fight(190)
    state = fight.rng.state
    assert _script(_handle(fight, handle)) == HEAVY
    trogg = fight.actor(handle)
    assert (trogg.action, cooldown(trogg)) == (BRAWLER.heavy_action, BRAWLER.heavy_cooldown)
    assert fight.rng.state == state


def test_beyond_heavy_range_it_walks() -> None:
    fight, handle = make_trogg_fight(215)
    result = _handle(fight, handle)
    assert result == Restart(SIDE_SCRIPTS[1], 213, 0, 100, 3)


def test_too_close_it_backs_away() -> None:
    fight, handle = make_trogg_fight(120)
    result = _handle(fight, handle)
    trogg = fight.actor(handle)
    assert (trogg.facing, trogg.input) == (3, RIGHT)
    assert result == Restart(SIDE_SCRIPTS[2], 122, 0, 100, 3)


def test_out_of_depth_it_walks_along_the_depth_axis() -> None:
    fight, handle = make_trogg_fight(150, 120)
    result = _handle(fight, handle)
    assert result == Restart(UP_SCRIPTS[1], 150, 0, 109, 3)


def test_the_cooldown_counts_down_on_the_idle_script() -> None:
    fight, handle = make_trogg_fight(150)
    trogg = fight.actor(handle)
    trogg.cooldown = 0x0305
    state = fight.rng.state
    assert _handle(fight, handle) == Restart(IDLE, 150, 0, 100, 3)
    assert trogg.cooldown == 0x0205
    assert fight.rng.state == state


def test_the_spear_thrusts_within_its_reach_and_walks_beyond() -> None:
    fight, handle = make_trogg_fight(150, kind=SPEAR_TYPE)
    assert _script(_handle(fight, handle)) == BRAWLER.thrust_script
    trogg = fight.actor(handle)
    assert (trogg.action, cooldown(trogg)) == (BRAWLER.thrust_action, BRAWLER.thrust_cooldown)
    fight, handle = make_trogg_fight(170, kind=SPEAR_TYPE)
    assert _script(_handle(fight, handle)) == SIDE_SCRIPTS[1]


def test_over_a_downed_knight_it_gloats_once_then_stands() -> None:
    fight, handle = make_trogg_fight(150)
    fight.actor(1).hp = 0
    state = fight.rng.state
    assert _script(_handle(fight, handle)) == STRIKE
    assert fight.gloated
    assert _handle(fight, handle) == Restart(IDLE, 150, 0, 100, 3)
    assert fight.rng.state == state


def test_over_a_downed_knight_it_waits_out_the_cooldown_first() -> None:
    fight, handle = make_trogg_fight(150)
    fight.actor(1).hp = 0
    set_cooldown(fight.actor(handle), 2)
    assert _script(_handle(fight, handle)) == IDLE
    assert (cooldown(fight.actor(handle)), fight.gloated) == (1, False)


def test_over_a_downed_knight_it_walks_up_or_stands_in_the_demo() -> None:
    fight, handle = make_trogg_fight(195)
    fight.actor(1).hp = 0
    assert _script(_handle(fight, handle)) == SIDE_SCRIPTS[1]
    fight, handle = make_trogg_fight(150)
    fight.actor(1).hp, fight.demo = 0, True
    assert _script(_handle(fight, handle)) == IDLE
    assert not fight.gloated


# ---- hurt ------------------------------------------------------------------------------------


def test_a_knight_blow_takes_contact_damage_and_plays_the_hurt_script() -> None:
    fight, handle = make_trogg_fight(150)
    trogg, knight = fight.actor(handle), fight.actor(1)
    trogg.attacker, knight.action = 1, 8
    set_cooldown(trogg, 5)
    result = _handle(fight, handle)
    assert trogg.hp == 20 - (3 + knight.strength)
    assert result == Restart(TROGG_TABLES.hurt_scripts[2], 150, 0, 100, 3)
    assert cooldown(trogg) == 0


def test_a_dagger_takes_the_fixed_amount() -> None:
    fight, handle = make_trogg_fight(150)
    dagger = fight.actor(fight.alloc_record())
    dagger.type, dagger.action = 0x34, 0x0C
    trogg = fight.actor(handle)
    trogg.attacker = fight.actors.index(dagger) + 1
    assert _script(_handle(fight, handle)) == TROGG_TABLES.hurt_scripts[3]
    assert trogg.hp == 20 - BRAWLER.dagger_damage


def test_a_spear_without_a_damage_table_takes_nothing_but_hurts() -> None:
    fight, handle = make_trogg_fight(150)
    spear_handle = fight.alloc_record()
    spear = fight.actor(spear_handle)
    spear.type, spear.action = SPEAR_TYPE, 4
    trogg = fight.actor(handle)
    trogg.attacker = spear_handle
    assert _script(_handle(fight, handle)) == TROGG_TABLES.hurt_scripts[1]
    assert trogg.hp == 20


@pytest.mark.parametrize("kind", [0x10, AXE_TYPE, AXE_TYPE_2, 0x24])
def test_other_attackers_leave_it_alone(kind: int) -> None:
    fight, handle = make_trogg_fight(150)
    other_handle = fight.alloc_record()
    fight.actor(other_handle).type = kind
    trogg = fight.actor(handle)
    trogg.attacker = other_handle
    set_cooldown(trogg, 3)
    assert _handle(fight, handle) == Restart(IDLE, 150, 0, 100, 3)
    assert (trogg.hp, cooldown(trogg)) == (20, 0)


def test_a_zero_hurt_entry_ends_the_job() -> None:
    fight, handle = make_trogg_fight(150)
    fight.actor(handle).attacker = 1
    fight.actor(1).action = 0
    assert _handle(fight, handle) is END


# ---- hit -------------------------------------------------------------------------------------


@pytest.mark.parametrize(("kind", "script"), [(AXE_TYPE, AFTER_HIT), (SPEAR_TYPE, IDLE)])
def test_a_blow_on_a_standing_knight(kind: int, script: int) -> None:
    fight, handle = make_trogg_fight(150, kind=kind)
    fight.actor(handle).hit_target = 1
    assert _handle(fight, handle) == Restart(script, 150, 0, 100, 3)
    assert cooldown(fight.actor(handle)) == BRAWLER.after_hit_cooldown


def test_the_axe_keeps_its_job_over_a_downed_knight() -> None:
    fight, handle = make_trogg_fight(150)
    fight.actor(handle).hit_target = 1
    fight.actor(1).hp = 0
    assert _handle(fight, handle) is KEEP


def test_a_downed_knight_that_is_hitting_counts_as_standing() -> None:
    fight, handle = make_trogg_fight(150)
    fight.actor(handle).hit_target = 1
    fight.actor(1).hp, fight.actor(1).hit_target = 0, handle
    assert _script(_handle(fight, handle)) == AFTER_HIT


def test_the_spear_finishes_a_downed_knight() -> None:
    fight, handle = make_trogg_fight(150, kind=SPEAR_TYPE)
    job = fight.start_job(10, 1, 1, (100, 0, 100), 1, 0x0C)
    assert job is not None
    fight.actor(handle).hit_target = 1
    fight.actor(1).hp = 0
    assert _script(_handle(fight, handle)) == BRAWLER.finish_script
    assert not job.active
    assert fight.actor(1).active == 0


def test_in_the_demo_the_spear_goes_back_to_idle() -> None:
    fight, handle = make_trogg_fight(150, kind=SPEAR_TYPE)
    fight.actor(handle).hit_target = 1
    fight.actor(1).hp, fight.demo = 0, True
    assert _script(_handle(fight, handle)) == IDLE


def test_a_blow_on_another_record_clears_only_self_not_victim() -> None:
    fight, handle = make_trogg_fight(150)
    other_handle = fight.alloc_record()
    other, trogg = fight.actor(other_handle), fight.actor(handle)
    other.type, other.input, other.action = AXE_TYPE, LEFT, 8
    trogg.hit_target, trogg.input, trogg.action = other_handle, UP, 8
    result = _handle(fight, handle)
    assert (other.input, other.action) == (LEFT, 8)
    assert (trogg.input, trogg.action) == (0, 0)
    assert cooldown(trogg) == BRAWLER.after_hit_cooldown - 1
    assert result == Restart(AFTER_HIT, 150, 0, 100, 3)
