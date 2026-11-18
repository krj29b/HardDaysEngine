"""Record tables, inventories and the job-pass sounds of a fight (ARCHITECTURE.md §9.4, §10.19)."""

from __future__ import annotations

import pytest

from harddaysengine.sim.actor import Actor
from harddaysengine.sim.fight import FightError
from harddaysengine.sim.inventory import Inventory
from harddaysengine.sim.sound import play
from tests.unit.knight_setup import TABLES, make_fight


def test_tables_are_read_by_byte_offset() -> None:
    fight, keys = make_fight()
    assert fight.table_long(keys.damage_table, 8) == TABLES.damage_table[2]
    assert fight.add_table((-1,)) not in (0, keys.damage_table)
    with pytest.raises(FightError, match="byte offset 6"):
        fight.table_long(keys.damage_table, 6)
    with pytest.raises(FightError, match="byte offset 36"):
        fight.table_long(keys.damage_table, 36)
    with pytest.raises(FightError, match="byte offset -4"):
        fight.table_long(keys.damage_table, 0xFFFC)
    with pytest.raises(FightError, match="no table"):
        fight.table_long(999, 0)


def test_registered_tables_are_shared_by_the_records_they_are_assigned_to() -> None:
    fight, keys = make_fight()
    assert fight.actor(1).hurt_scripts == fight.actor(2).hurt_scripts == keys.hurt_scripts
    assert fight.tables[keys.walk_scripts] == list(TABLES.walk_scripts)
    other = Actor()
    keys.assign(other)
    assert (other.idle_script, other.after_hit_script) == (10, 12)
    assert other.defence_table == keys.defence_table


def test_inventory_slots_are_addressed_by_even_offsets() -> None:
    inventory = Inventory()
    inventory.counts[0x16 // 2] = 0x105
    assert inventory.byte(0x16) == 0x05
    with pytest.raises(IndexError):
        inventory.byte(3)
    with pytest.raises(IndexError):
        inventory.byte(0x18)
    fight, _ = make_fight()
    assert fight.inventory_byte(Actor(), 0x08) == 0


def test_a_parry_sound_reaches_the_frame_output() -> None:
    fight, _ = make_fight()
    knight, other = fight.actor(1), fight.actor(2)
    knight.action, knight.attacker = 0x10, 2
    other.action = 8
    job = fight.start_job(0, 1, 1, (100, 0, 100), 1, 0x0C)
    assert job is not None
    output = fight.step(0)
    assert output.sounds == [play(7)]
    assert fight.step(6).sounds == []
