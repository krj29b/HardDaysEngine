"""Invented Balok rule values for tests independent of the disks (§10.28)."""

from dataclasses import fields
from functools import partial

from pymoonstone.sim.fight import Fight
from pymoonstone.sim.fight_ai.drake import DRAKE_TYPE, DrakeRules, DrakeScripts, install_drake
from pymoonstone.sim.fight_ai.effects import HitSparkRules
from pymoonstone.sim.screen_fx import SCREEN_SHAKE, ShakeRules, screen_shake
from pymoonstone.sim.trajectory import FlightRules
from tests.unit.brawler_setup import make_trogg_fight
from tests.unit.knight_setup import RULES

SCRIPTS = DrakeScripts(**{f.name: 300 + i for i, f in enumerate(fields(DrakeScripts))})
DRAKE = DrakeRules(
    scripts=SCRIPTS,
    flight=FlightRules(3, 6, 3, 4, 5, 6, 12),
    spark=HitSparkRules(400, 1, 0x28),
    close=40,
    hop_range=60,
    heavy_range=90,
    wait_range=130,
    aim_reach=55,
    minimum_steps=2,
    maximum_steps=12,
    landing_height=0xFFE2,
    landing_range=8,
    landing_wait=3,
    hard_arc=6,
    release_offset=35,
    toggle_xor=1,
    hop_table=420,
    sound_soft=61,
    sound_hard_first=62,
    sound_hard_second=63,
    sound_grab_first=64,
    sound_grab_second=65,
    counter_step=1,
    counter_reload=7,
)


def make_drake(x: int = 180, y: int = 100) -> tuple[Fight, int]:
    fight, handle = make_trogg_fight(x, y, kind=DRAKE_TYPE)
    install_drake(fight, DRAKE, RULES)
    fight.shake_rules = ShakeRules(2, (3, 0, -3))
    fight.routines[SCREEN_SHAKE] = partial(screen_shake, fight.shake_rules)
    for owner in (1, handle):
        actor = fight.actor(owner)
        fight.start_job(
            actor.idle_script, owner, 1, (actor.x, 0, actor.y), actor.facing, actor.type
        )
    return fight, handle
