"""Invented caster values and scripts for §10.23 tests."""

from dataclasses import fields

from pymoonstone.sim.fight import Fight
from pymoonstone.sim.fight_ai.caster import (
    CASTER_TYPE,
    CasterRules,
    CasterScripts,
    install_caster,
)
from pymoonstone.sim.trajectory import FlightRules
from tests.unit.brawler_setup import make_trogg_fight
from tests.unit.knight_setup import RULES

FLIGHT = FlightRules(3, 6, 3, 4, 5, 6, 12)
SCRIPTS = CasterScripts(**{f.name: 200 + i for i, f in enumerate(fields(CasterScripts))})
CASTER = CasterRules(
    scripts=SCRIPTS,
    flight=FLIGHT,
    strike8_range=35,
    strike4_range=55,
    initial_wait=20,
    initial_height=0xFFF1,
    initial_depth=4,
    initial_steps=12,
    initial_arc=1,
    minimum_steps=7,
    quick_range=35,
    quick_arc=1,
    airborne_height=0xFFF4,
    hold_range=65,
    throw_wait=19,
    throw_height=0xFFB0,
    throw_x=130,
    throw_steps=19,
    throw_arc=100,
    displayed_height=0xFFC0,
    throw_damage=7,
    squeeze_damage=2,
    dead_hp=0xFFFE,
    shared_wait=11,
    endurance_bonus=9,
    object_x=170,
    object_height=180,
    object_facing=3,
)


def make_caster(x: int = 150, y: int = 100) -> tuple[Fight, int]:
    fight, handle = make_trogg_fight(x, y, kind=CASTER_TYPE)
    install_caster(fight, CASTER, RULES)
    first = fight.actor(1)
    fight.start_job(
        first.idle_script, 1, 1, (first.x, first.height, first.y), first.facing, first.type
    )
    fight.second = fight.spawn_actor(10, 1, (170, -30, 100), 1, 0x28)
    actor = fight.actor(handle)
    fight.start_job(actor.idle_script, handle, 1, (x, 0, y), 3, CASTER_TYPE)
    return fight, handle
