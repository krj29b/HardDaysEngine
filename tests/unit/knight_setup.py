"""Synthetic knight-control rules and a fight with two knights (ARCHITECTURE.md §10.19).

Every number here is invented for the tests and differs from the original's, so a test that
passes proves the code reads the rule record, not a constant of its own.
"""

from __future__ import annotations

import struct

from harddaysengine.sim.actor import Actor
from harddaysengine.sim.fight import Fight, FightConstants, ScriptTables, TableKeys
from harddaysengine.sim.fight_script import CelShape, FrameShape, ScriptImage
from harddaysengine.sim.fighter_rules import (
    ArenaClip,
    DaggerRules,
    DamageRules,
    HitRule,
    HurtAmounts,
    HurtRule,
    HurtScripts,
    KnightRules,
    Probes,
    WalkSteps,
)
from harddaysengine.sim.fighters import install_knight_control
from harddaysengine.sim.inventory import Inventory

KNIGHT = 0x0C
HOP_TABLE = 400
HOP_WORDS = (3, 5, 7)
# Every script position below the hop table is a frame marker that ends the script.
IMAGE = bytes([0xFF]) * HOP_TABLE + struct.pack(">3H", *HOP_WORDS) + bytes(106)

_HURT = {
    0x00: HurtRule.FLYER,
    0x04: HurtRule.CONTACT,
    0x08: HurtRule.GUARDIAN,
    0x0C: HurtRule.KNIGHT,
    0x10: HurtRule.KNIGHT,
    0x14: HurtRule.DRAGON,
    0x18: HurtRule.AXE,
    0x1C: HurtRule.AXE,
    0x20: HurtRule.SPEAR,
    0x24: HurtRule.RATMEN,
    0x28: HurtRule.DRAGON_FLIGHT,
    0x2C: HurtRule.DRAGON_OBJECT,
    0x30: HurtRule.DRAKE,
    0x34: HurtRule.CONTACT,
    0x40: HurtRule.STALKER,
}

RULES = KnightRules(
    steps=WalkSteps(side=(5, 6, 7, 8), up=(1, 2, 3, 4), down=(9, 10, 11, 12)),
    actions_right=tuple((i % 9) * 4 for i in range(16)),
    actions_left=tuple(((i + 3) % 9) * 4 for i in range(16)),
    clip=ArenaClip(reach=20, left=5, right=300, foot=4, bottom=150, top=25),
    probes=Probes(vertical_reach=15, obstacle_reach=40, wall_top=25),
    clang_sound=7,
    damage=DamageRules(
        sword_bonus=((0x17, 1), (0x18, 2), (0x19, 4)),
        moonstones=((0, 50), (3, 50), (1, 51), (2, 52)),
    ),
    amounts=HurtAmounts(
        spear=2,
        flyer=4,
        guardian_heavy=9,
        guardian_strike=6,
        guardian_other=11,
        stalker=13,
        drake=14,
        dragon_depth=2,
        dragon_strike=15,
        dragon_flight=25,
        object_offset=12,
    ),
    scripts=HurtScripts(*range(100, 114)),
    dagger=DaggerRules(
        script=200, flight_script=210, damage=(6,) * 9, action=12, right_edge=300, left_margin=8
    ),
    hop_table=HOP_TABLE,
    hurt_rules=_HURT,
    hit_rules={
        kind: HitRule.HURT_BY_FLIGHT if kind == 0x28 else HitRule.CARRY_ON for kind in _HURT
    },
)

TABLES = ScriptTables(
    idle_script=10,
    after_hit_script=12,
    hurt_scripts=tuple(20 + i for i in range(9)),
    action_scripts=tuple(40 + i for i in range(9)),
    walk_scripts=tuple(60 + i for i in range(24)),
    damage_table=(0, 2, 3, 4, 5, 6, 7, 8, 9),
    defence_table=(0, 0x1C, 0x10, 0, 0, 0x1C, 0, 0, 0x1C),
)

SOLID = FrameShape(16, 16, (0xFFFF,) * 16)
CELS = {"body": CelShape("body", (SOLID,), None)}
LISTS = {1: ("body", None, None, None, None)}


def make_fight() -> tuple[Fight, TableKeys]:
    """Two knights (handles 1 and 2) with the test tables, 20 hit points, facing each other."""
    knights = [Actor() for _ in range(4)]
    fight = Fight(
        image=ScriptImage(IMAGE, {}),
        cels=CELS,
        frame_lists=LISTS,
        knights=knights,
        dragon=Actor(),
        constants=FightConstants(end_delay=35, dead_hp=0xFFFF),
    )
    install_knight_control(fight, RULES)
    keys = fight.register_tables(TABLES)
    for handle, (x, facing, port) in enumerate(((100, 1, 1), (200, 3, 2)), start=1):
        actor = fight.actor(handle)
        keys.assign(actor)
        actor.x, actor.y, actor.facing, actor.input_port = x, 100, facing, port
        actor.hp = actor.hp_max = 20
        actor.strength, actor.sword, actor.type, actor.active = 1, 0x16, KNIGHT, 1
        actor.inventory = handle
        fight.inventories[handle] = Inventory()
    fight.first = 1
    return fight, keys
