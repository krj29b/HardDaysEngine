"""Synthetic brawler rules and a Trogg next to the knights of ``knight_setup`` (§10.20).

Every number here is invented for the tests and differs from the original's, so a test that
passes proves the code reads the rule record, not a constant of its own.
"""

from __future__ import annotations

from pymoonstone.sim.fight import Fight, ScriptTables
from pymoonstone.sim.fight_ai.approach import ApproachRules
from pymoonstone.sim.fight_ai.brawler import AXE_TYPE, BrawlerRules, install_brawler
from tests.unit.knight_setup import RULES, make_fight

# Eight {x, y} pairs per table; phases past the third pair are distinct too.
APPROACH = ApproachRules(
    side=(2, 0xFFFF, 6, 1, 20, 0, 9, 2, 3, 3, 4, 4, 5, 5, 6, 6),
    up=(1, 11, 0, 4, 2, 8, 0xFFFF, 4, 7, 7, 8, 8, 9, 9, 10, 10),
    down=(4, 12, 3, 5, 2, 5, 5, 3, 11, 11, 12, 12, 13, 13, 14, 14),
    up_override=0xFFFA,
    down_override=6,
)

BRAWLER = BrawlerRules(
    approach=APPROACH,
    down_range=90,
    close_range=80,
    heavy_range=110,
    gloat_odds=40,
    strike_cooldown=7,
    heavy_cooldown=8,
    thrust_cooldown=15,
    after_hit_cooldown=9,
    strike_action=0x14,
    heavy_action=0x18,
    thrust_action=0x0C,
    defend_action=0x1C,
    dagger_damage=4,
    thrust_script=300,
    finish_script=310,
)

SIDE_SCRIPTS = (70, 71, 72, 0, 0, 0, 0, 0)
UP_SCRIPTS = (80, 81, 82, 83, 0, 0, 0, 0)
DOWN_SCRIPTS = (90, 91, 92, 93, 0, 0, 0, 0)
TROGG_TABLES = ScriptTables(
    idle_script=11,
    after_hit_script=13,
    hurt_scripts=(0, 21, 22, 23, 24, 25, 26, 27, 28),
    action_scripts=tuple(50 + i for i in range(9)),
    walk_scripts=SIDE_SCRIPTS + UP_SCRIPTS + DOWN_SCRIPTS,
    damage_table=(0, 1, 1, 1, 0, 1, 1, 0, 1),
    defence_table=(),
)
REACH, TOO_CLOSE, DEPTH = 60, 30, 4


def make_trogg_fight(
    x: int, y: int = 100, *, kind: int = AXE_TYPE, tables: ScriptTables = TROGG_TABLES
) -> tuple[Fight, int]:
    """The two test knights (first fighter at x 100, depth 100) and a Trogg at ``(x, y)``."""
    fight, _ = make_fight()
    install_brawler(fight, BRAWLER, RULES)
    keys = fight.register_tables(tables)
    handle = fight.alloc_record()
    trogg = fight.actor(handle)
    keys.assign(trogg)
    trogg.x, trogg.y, trogg.facing, trogg.type = x, y, 3, kind
    trogg.reach_x, trogg.too_close_x, trogg.depth_reach = REACH, TOO_CLOSE, DEPTH
    trogg.hp = trogg.hp_max = 20
    return fight, handle
