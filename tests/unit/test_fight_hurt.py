"""The knight's hurt and hit rules, contact damage, protection and blocking (§10.19)."""

from __future__ import annotations

import pytest

from harddaysengine.sim.actor import Actor
from harddaysengine.sim.fight import END, KEEP, Fight, FightError, HandlerResult, Restart
from harddaysengine.sim.fight_hurt import (
    Blow,
    block_check,
    contact_damage,
    knight_hit,
    knight_hurt,
    protected,
)
from harddaysengine.sim.inventory import Inventory
from harddaysengine.sim.sound import play
from tests.unit.knight_setup import RULES, TABLES, make_fight

SCRIPTS = RULES.scripts
AMOUNTS = RULES.amounts


def _hurt(kind: int, action: int, *, hp: int = 20, facing: int = 3) -> tuple[Fight, HandlerResult]:
    """Knight 1 (facing right) is hit by record 2 of type ``kind`` doing ``action``."""
    fight, _ = make_fight()
    knight, attacker = fight.actor(1), fight.actor(2)
    knight.hp, knight.attacker = hp, 2
    attacker.type, attacker.action, attacker.facing = kind, action, facing
    return fight, knight_hurt(fight, RULES, 1)


def _script(result: HandlerResult) -> int:
    assert isinstance(result, Restart)
    return result.script


def _hp(fight: Fight) -> int:
    return fight.actor(1).hp


# ---- damage ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "sword", "expected"),
    [(4, 0x16, 3), (4, 0x17, 4), (8, 0x18, 6), (12, 0x19, 9), (32, 0x17, 22), (32, 0x16, 20)],
)
def test_contact_damage_adds_strength_and_sword_and_doubles_heavy(
    action: int, sword: int, expected: int
) -> None:
    fight, _ = make_fight()
    attacker = fight.actor(2)
    attacker.action, attacker.sword = action, sword
    assert contact_damage(fight, RULES, attacker) == expected


@pytest.mark.parametrize(
    ("stones", "moon", "factor"),
    [(0b0001, 50, 2), (0b1001, 50, 2), (0b0010, 51, 2), (0b0100, 52, 2), (0b0100, 50, 1)],
)
def test_a_matching_moonstone_doubles_once(stones: int, moon: int, factor: int) -> None:
    fight, _ = make_fight()
    attacker = fight.actor(2)
    attacker.action = 4
    fight.inventories[2].counts[0x16 // 2] = stones
    fight.moon_frame = moon
    assert contact_damage(fight, RULES, attacker) == 3 * factor


def test_a_record_without_inventory_has_no_moonstones() -> None:
    fight, _ = make_fight()
    dagger = Actor(action=4, damage_table=fight.actor(2).damage_table)
    fight.moon_frame = 50
    assert contact_damage(fight, RULES, dagger) == 2
    dagger.inventory = 9
    with pytest.raises(FightError, match="no inventory"):
        contact_damage(fight, RULES, dagger)


@pytest.mark.parametrize(("count", "expected"), [(0, 40), (2, 10), (15, 0), (16, 0), (65, 20)])
def test_protection_shifts_the_amount(count: int, expected: int) -> None:
    fight, _ = make_fight()
    fight.inventories[1] = Inventory()
    fight.inventories[1].counts[0x08 // 2] = count
    assert protected(fight, fight.actor(1), 40) == expected


# ---- blocking ----------------------------------------------------------------------------------


def _blow(own_action: int, attacker_action: int, attacker_facing: int = 3) -> Blow:
    fight, _ = make_fight()
    fight.actor(1).action = own_action
    fight.actor(2).action, fight.actor(2).facing = attacker_action, attacker_facing
    return Blow(fight, RULES, 1, 2)


def test_a_wrong_stance_does_not_block() -> None:
    assert not block_check(_blow(0, 4))


def test_the_block_stance_holds_once_until_the_latch_is_released() -> None:
    blow = _blow(0x1C, 4)
    assert block_check(blow)
    assert blow.me.behaviour_flags & 0x80
    assert not block_check(blow)
    assert blow.fight.sounds == []


def test_a_parry_needs_the_fighters_to_face_each_other() -> None:
    blow = _blow(0x10, 8)
    assert block_check(blow)
    assert blow.fight.sounds == [play(RULES.clang_sound)]
    assert not block_check(_blow(0x10, 8, attacker_facing=1))


# ---- hurt rules --------------------------------------------------------------------------------


def test_contact_takes_the_damage_and_plays_the_hurt_script() -> None:
    fight, result = _hurt(0x04, 8)
    assert _hp(fight) == 20 - 4
    assert result == Restart(TABLES.hurt_scripts[2], 100, 0, 100, 1)


@pytest.mark.parametrize(
    ("action", "script"), [(4, SCRIPTS.contact_down), (8, SCRIPTS.contact_down_hard)]
)
def test_contact_on_a_fallen_knight_knocks_it_down(action: int, script: int) -> None:
    fight, result = _hurt(0x34, action, hp=0)
    assert (_hp(fight), _script(result)) == (0, script)


def test_a_knight_blow_is_parried_or_hurts() -> None:
    fight, _ = make_fight()
    knight = fight.actor(1)
    knight.attacker, knight.action = 2, 0x10
    fight.actor(2).action = 8
    assert _script(knight_hurt(fight, RULES, 1)) == TABLES.action_scripts[4]
    assert knight.hp == 20
    _, result = _hurt(0x0C, 0, hp=0)
    assert _script(result) == SCRIPTS.axe_down_hard
    fight, result = _hurt(0x10, 4)
    assert (_hp(fight), _script(result)) == (17, TABLES.hurt_scripts[1])


def test_the_axe_takes_the_raw_table_value() -> None:
    fight, result = _hurt(0x18, 8)
    assert (_hp(fight), _script(result)) == (17, TABLES.hurt_scripts[2])


@pytest.mark.parametrize(
    ("kind", "script"), [(0x18, SCRIPTS.axe_down_hard), (0x1C, SCRIPTS.axe_down)]
)
def test_the_axe_knocks_a_fallen_knight_down(kind: int, script: int) -> None:
    assert _script(_hurt(kind, 8, hp=0)[1]) == script


def test_the_axe_is_parried_by_the_right_stance() -> None:
    fight, _ = make_fight()
    knight = fight.actor(1)
    knight.attacker, knight.action = 2, 0x1C
    fight.actor(2).type, fight.actor(2).action = 0x1C, 4
    assert _script(knight_hurt(fight, RULES, 1)) == TABLES.action_scripts[7]


def test_the_spear_is_parried_or_takes_its_amount() -> None:
    fight, _ = make_fight()
    knight = fight.actor(1)
    knight.attacker, knight.action = 2, 0x1C
    fight.actor(2).type, fight.actor(2).action = 0x20, 4
    assert _script(knight_hurt(fight, RULES, 1)) == SCRIPTS.spear_parry
    fight, result = _hurt(0x20, 4)
    assert (_hp(fight), _script(result)) == (20 - AMOUNTS.spear, TABLES.hurt_scripts[1])


def test_the_spear_finishes_a_knight_unless_it_hits_or_it_is_the_demo() -> None:
    fight, _ = make_fight()
    knight = fight.actor(1)
    knight.attacker, knight.hp = 2, 1
    fight.actor(2).type, fight.actor(2).action = 0x20, 4
    attacker_job = fight.start_job(5, 2, 1, (0, 0, 0), 3, 0x20)
    assert attacker_job is not None
    assert knight_hurt(fight, RULES, 1) is END
    assert (attacker_job.script, attacker_job.running) == (SCRIPTS.spear_kill, True)
    for demo, target in ((True, 0), (False, 2)):
        fight, _ = make_fight()
        fight.demo = demo
        knight = fight.actor(1)
        knight.attacker, knight.hit_target, knight.hp = 2, target, 1
        fight.actor(2).type, fight.actor(2).action = 0x20, 4
        assert _script(knight_hurt(fight, RULES, 1)) == TABLES.hurt_scripts[1]


def test_the_ratmen_curse_and_strike() -> None:
    fight, result = _hurt(0x24, 4)
    assert (_hp(fight), _script(result), fight.actor(1).life_loss) == (18, SCRIPTS.ratmen_curse, 1)
    fight, result = _hurt(0x24, 8)
    assert (_hp(fight), _script(result), fight.actor(1).life_loss) == (17, SCRIPTS.ratmen_strike, 0)
    fight, result = _hurt(0x24, 12)
    assert (_hp(fight), _script(result)) == (15, TABLES.hurt_scripts[3])


@pytest.mark.parametrize(
    ("facing", "script"), [(1, SCRIPTS.flyer_hurt_same), (3, SCRIPTS.flyer_hurt_facing)]
)
def test_the_flyer_hurts_by_its_amount(facing: int, script: int) -> None:
    fight, result = _hurt(0x00, 4, facing=facing)
    assert (_hp(fight), _script(result)) == (20 - AMOUNTS.flyer, script)


@pytest.mark.parametrize(
    ("facing", "script"), [(1, SCRIPTS.flyer_kill_same), (3, SCRIPTS.flyer_kill_facing)]
)
def test_the_flyer_finishes_a_weak_knight(facing: int, script: int) -> None:
    fight, _ = make_fight()
    job = fight.start_job(5, 2, 1, (0, 0, 0), facing, 0x00)
    assert job is not None
    knight = fight.actor(1)
    knight.attacker, knight.hp = 2, AMOUNTS.flyer
    fight.actor(2).type, fight.actor(2).facing = 0x00, facing
    assert knight_hurt(fight, RULES, 1) is END
    assert job.script == script


@pytest.mark.parametrize(
    ("action", "amount", "facing"),
    [
        (32, AMOUNTS.guardian_heavy, 3),
        (4, AMOUNTS.guardian_strike, 3),
        (8, AMOUNTS.guardian_other, 1),
    ],
)
def test_the_guardian(action: int, amount: int, facing: int) -> None:
    fight, result = _hurt(0x08, action, facing=1)
    knight = fight.actor(1)
    assert (knight.hp, knight.facing) == (20 - amount, facing)
    assert _script(result) == TABLES.hurt_scripts[action // 4]


def test_the_stalker_flings_a_knight_its_heavy_blow_kills() -> None:
    fight, result = _hurt(0x40, 32)
    assert (_hp(fight), _script(result)) == (20 - AMOUNTS.stalker, TABLES.hurt_scripts[8])
    fight, result = _hurt(0x40, 32, hp=AMOUNTS.stalker)
    assert _script(result) == SCRIPTS.stalker_flung
    fight, result = _hurt(0x40, 4, hp=AMOUNTS.stalker)
    assert _script(result) == TABLES.hurt_scripts[1]


def test_the_drake_turns_the_knight_on_its_action_8() -> None:
    fight, result = _hurt(0x30, 8, facing=1)
    assert (_hp(fight), fight.actor(1).facing) == (20 - AMOUNTS.drake, 3)
    assert _script(result) == TABLES.hurt_scripts[2]
    fight, _ = _hurt(0x30, 4, facing=1)
    assert fight.actor(1).facing == 1


def test_the_dragon_pulls_the_knight_to_its_depth() -> None:
    fight, _ = make_fight()
    knight, dragon = fight.actor(1), fight.actor(2)
    knight.attacker, dragon.type, dragon.action, dragon.y = 2, 0x14, 4, 120
    fight.inventories[1].counts[0x08 // 2] = 1
    result = knight_hurt(fight, RULES, 1)
    assert (knight.y, knight.hp, dragon.action) == (120 - AMOUNTS.dragon_depth, 20 - 7, 4)
    assert _script(result) == TABLES.hurt_scripts[1]


def test_the_dragon_flight_blow_rewrites_the_attackers_action() -> None:
    for kind in (0x14, 0x28):
        fight, result = _hurt(kind, 8, hp=40)
        assert (_hp(fight), fight.actor(2).action) == (40 - AMOUNTS.dragon_flight, 32)
        assert _script(result) == TABLES.hurt_scripts[8]


def test_the_dragon_object_throws_the_knight() -> None:
    fight, result = _hurt(0x2C, 4, hp=40)
    knight = fight.actor(1)
    assert knight.hp == 40 - (0x2C - AMOUNTS.object_offset)
    assert (knight.facing, fight.hop_facing, fight.hop_table) == (3, 1, RULES.hop_table)
    assert _script(result) == SCRIPTS.object_hop


def test_a_hurt_table_entry_of_zero_ends_the_knights_job() -> None:
    fight, _ = make_fight()
    fight.tables[fight.actor(1).hurt_scripts][1] = 0
    knight = fight.actor(1)
    knight.attacker = 2
    fight.actor(2).type, fight.actor(2).action = 0x04, 4
    assert knight_hurt(fight, RULES, 1) is END


def test_an_attacker_without_a_rule_raises() -> None:
    with pytest.raises(FightError, match=r"no hurt rule for attacker type \$38"):
        _hurt(0x38, 4)


# ---- hit rules ---------------------------------------------------------------------------------


def _hit(kind: int, target_action: int, *, target_hp: int = 20, own: int = 4) -> HandlerResult:
    fight, _ = make_fight()
    knight, target = fight.actor(1), fight.actor(2)
    knight.hit_target, knight.action = 2, own
    target.type, target.action, target.hp = kind, target_action, target_hp
    return knight_hit(fight, RULES, 1)


def test_a_blow_carries_on_into_the_after_hit_script() -> None:
    assert _script(_hit(0x18, 4)) == TABLES.after_hit_script
    assert _script(_hit(0x0C, 4)) == TABLES.after_hit_script


def test_a_blocking_knight_or_a_target_in_action_18_keeps_the_script() -> None:
    assert _hit(0x0C, 0x1C) is KEEP
    assert _hit(0x10, 0x1C) is KEEP
    assert _hit(0x18, 0x18) is KEEP
    assert _script(_hit(0x18, 0x1C)) == TABLES.after_hit_script


def test_a_strike_8_on_a_fallen_knight_keeps_the_script_outside_the_demo() -> None:
    assert _hit(0x0C, 0, target_hp=0, own=8) is KEEP
    assert _script(_hit(0x0C, 0, target_hp=0, own=4)) == TABLES.after_hit_script
    fight, _ = make_fight()
    fight.demo = True
    knight, target = fight.actor(1), fight.actor(2)
    knight.hit_target, knight.action, target.hp = 2, 8, 0
    assert _script(knight_hit(fight, RULES, 1)) == TABLES.after_hit_script


def test_hitting_a_type_28_record_hurts_the_knight() -> None:
    fight, _ = make_fight()
    knight, target = fight.actor(1), fight.actor(2)
    knight.hit_target, knight.hp, target.type, target.action = 2, 40, 0x28, 4
    result = knight_hit(fight, RULES, 1)
    assert (knight.hp, target.action) == (40 - AMOUNTS.dragon_flight, 32)
    assert _script(result) == TABLES.hurt_scripts[8]


def test_a_target_without_a_rule_raises() -> None:
    with pytest.raises(FightError, match=r"no hit rule for target type \$3C"):
        _hit(0x3C, 0)
