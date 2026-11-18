"""Invented Guardian values for disk-independent tests (§10.29)."""

from dataclasses import fields

from harddaysengine.sim.fight import Fight
from harddaysengine.sim.fight_ai.guardian import (
    GUARDIAN_TYPE,
    GuardianRules,
    GuardianScripts,
    install_guardian,
)
from tests.unit.brawler_setup import make_trogg_fight
from tests.unit.knight_setup import RULES

SCRIPTS = GuardianScripts(**{f.name: 300 + i for i, f in enumerate(fields(GuardianScripts))})
GUARDIAN = GuardianRules(
    scripts=SCRIPTS,
    lunge_range=50,
    strike8_range=70,
    strike4_range=90,
    step=3,
    lunge_wait=4,
    strike8_wait=7,
    strike4_wait=9,
    grab5_min=65,
    grab5_max=75,
    grab5_wait=3,
    grab7_min=80,
    grab7_max=95,
    grab7_wait=2,
    snap_range=25,
    snap_offset=35,
    hit_wait=8,
    grab_damage=7,
    release_offset=40,
    release_limit=280,
    release_max=279,
    release_min=2,
    hop_table=410,
    hop2_table=420,
    sounds=(61, 62, 63, 64),
)


def make_guardian(x: int = 180, y: int = 100) -> tuple[Fight, int]:
    fight, handle = make_trogg_fight(x, y, kind=GUARDIAN_TYPE)
    install_guardian(fight, GUARDIAN, RULES)
    fight.guardian.main, fight.guardian.body, fight.guardian.target = (
        handle,
        fight.alloc_record(),
        1,
    )
    for owner in (1, handle):
        actor = fight.actor(owner)
        fight.start_job(
            actor.idle_script, owner, 1, (actor.x, 0, actor.y), actor.facing, actor.type
        )
    return fight, handle
