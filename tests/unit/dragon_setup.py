"""Invented Dragon rules and records, independent of original disks (§10.30)."""

from dataclasses import fields

from pymoonstone.sim.fight import DRAGON, Fight, ScriptTables
from pymoonstone.sim.fight_ai.dragon import (
    DRAGON_TYPE,
    PART_TYPE,
    DragonFlight,
    DragonRules,
    DragonScripts,
    install_dragon,
)
from pymoonstone.sim.fight_ai.effects import HitSparkRules
from pymoonstone.sim.trajectory import FlightRules
from tests.unit.knight_setup import RULES, make_fight

SCRIPTS = DragonScripts(**{f.name: 300 + i for i, f in enumerate(fields(DragonScripts))})
DRAGON_RULES = DragonRules(
    scripts=SCRIPTS,
    near=DragonFlight(32, 6, 70, 0xFFE2, 30),
    far=DragonFlight(64, 5, 70, 0xFFF0, 40),
    flight=FlightRules(6, 4, 2, 3, 4, 5, 8),
    spark=HitSparkRules(500, 4, 40),
    phase_range=95,
    breath_range=45,
    step=3,
    minimum_x=15,
    maximum_x=75,
    reduced_reach=4,
    reduced_close=2,
    dagger_damage=7,
    link_offset=8,
    link_gap=25,
    fire_x=35,
    fire_y=4,
    fire_height=0,
    fire_facing=1,
    fire_kind=40,
    fire_list=2,
    hop_table=520,
    part_limit=85,
    part_action=0x14,
    sounds=(51, 52),
)
TABLES = ScriptTables(
    11,
    13,
    tuple(400 + i for i in range(9)),
    (),
    tuple(600 + i for i in range(24)),
    tuple(range(9)),
    (),
)


def make_dragon() -> Fight:
    f, _ = make_fight()
    install_dragon(f, DRAGON_RULES, RULES)
    main = f.actor(DRAGON)
    keys = f.register_tables(TABLES)
    keys.assign(main)
    main.x, main.y, main.height, main.facing = 40, 100, 0, 1
    main.hp, main.hp_max, main.active, main.type = 100, 100, 1, DRAGON_TYPE
    main.reach_x, main.too_close_x, main.depth_reach = 60, 10, 4
    f.start_job(main.idle_script, DRAGON, 2, (40, 0, 100), 1, DRAGON_TYPE)
    f.start_job(f.actor(1).idle_script, 1, 1, (100, 0, 100), 3, 0x0C)
    handles = []
    for y in (80, 120):
        handle = f.alloc_record()
        actor = f.actor(handle)
        keys.assign(actor)
        actor.type, actor.x, actor.y, actor.depth_reach = PART_TYPE, 5, y, 8
        actor.hp = 100
        f.start_job(actor.idle_script, handle, 2, (5, 0, y), 1, PART_TYPE)
        handles.append(handle)
    f.dragon_cells.link_a, f.dragon_cells.link_b = handles
    f.first, f.fight_active = 1, True
    return f
