"""A knight's reactions to a blow taken or landed, damage and blocking (ARCHITECTURE.md §10.19).

The rule of a blow is chosen by the other record's type through the two rule tables read from
``mog`` (:attr:`KnightRules.hurt_rules`, :attr:`KnightRules.hit_rules`). Each rule returns the
knight's handler result: **keep**, **end** or the next script at the knight's position.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pymoonstone.sim.fight import END, KEEP, FightError, HandlerResult, restart_from
from pymoonstone.sim.fighter_rules import HitRule, HurtRule
from pymoonstone.sim.inventory import SLOT_MOONSTONES, SLOT_PROTECTION
from pymoonstone.sim.m68k import s16, u8, u16

if TYPE_CHECKING:
    from collections.abc import Callable

    from pymoonstone.sim.actor import Actor
    from pymoonstone.sim.fight import Fight
    from pymoonstone.sim.fighter_rules import KnightRules

# Action codes (§9.7) and type bytes (§9.8) the rules compare with.
ACTION_STRIKE_4 = 0x04
ACTION_STRIKE_8 = 0x08
ACTION_STRIKE_18 = 0x18
ACTION_BLOCK = 0x1C
ACTION_HEAVY = 0x20
TYPE_KNIGHT = 0x0C
TYPE_MAP_KNIGHT = 0x10
TYPE_FIRST_AXE = 0x18
_BLOCK_LATCH = 0x80
_RATMEN_CURSE_ENTRY = 4
_RATMEN_STRIKE_ENTRY = 8
_FACING_MIRROR = 0x02
_FACING_LEFT = 3
_HOP_FACING = 1
_PROTECTION_MASK = 63
_WORD_BITS = 16


@dataclass(slots=True)
class Blow:
    """One blow between the knight (``me``) and the other record (``other``)."""

    fight: Fight
    rules: KnightRules
    me_handle: int
    other_handle: int

    @property
    def me(self) -> Actor:
        """The knight."""
        return self.fight.actor(self.me_handle)

    @property
    def other(self) -> Actor:
        """The attacker (hurt rules) or the target (hit rules)."""
        return self.fight.actor(self.other_handle)


# ---- numbers ---------------------------------------------------------------------------------


def contact_damage(fight: Fight, rules: KnightRules, attacker: Actor) -> int:
    """The attacker's contact damage for its current action (§10.19), a 16-bit word.

    A record without a damage table (the spear Trogg) counts its entry as 0 (§10.20).
    """
    entry = 0
    if attacker.damage_table != 0:
        entry = fight.table_long(attacker.damage_table, attacker.action)
    amount = entry + attacker.strength
    for sword, bonus in rules.damage.sword_bonus:
        if attacker.sword == sword:
            amount += bonus
    amount = u16(amount)
    if attacker.action == ACTION_HEAVY:
        amount = u16(amount << 1)
    stones = fight.inventory_byte(attacker, SLOT_MOONSTONES)
    if any(
        stones >> bit & 1 and fight.moon_frame == frame for bit, frame in rules.damage.moonstones
    ):
        amount = u16(amount << 1)
    return amount


def protected(fight: Fight, defender: Actor, amount: int) -> int:
    """``amount`` shifted right by the defender's protection count, modulo 64 (§10.19)."""
    count = fight.inventory_byte(defender, SLOT_PROTECTION) & _PROTECTION_MASK
    return 0 if count >= _WORD_BITS else u16(amount) >> count


def block_check(blow: Blow) -> bool:
    """Whether the knight's stance stops the blow; sets the latch, asks for the clang (§10.19)."""
    me, attacker = blow.me, blow.other
    need = blow.fight.table_long(me.defence_table, attacker.action) & 0xFFFF
    if need != me.action:
        return False
    if me.action == ACTION_BLOCK:
        if me.behaviour_flags & _BLOCK_LATCH:
            return False
        me.behaviour_flags |= _BLOCK_LATCH
        return True
    if me.facing == attacker.facing:
        return False
    blow.fight.sounds.append(blow.rules.clang_sound)
    return True


# ---- results ---------------------------------------------------------------------------------


def _take(actor: Actor, amount: int) -> None:
    actor.hp = u16(actor.hp - amount)


def _next(me: Actor, script: int) -> HandlerResult:
    return END if script == 0 else restart_from(me, script)


def _hurt_script(blow: Blow) -> HandlerResult:
    """The common ending: ``hurt_scripts[attacker's action]``."""
    me = blow.me
    return _next(me, blow.fight.table_long(me.hurt_scripts, blow.other.action))


def _own_action_script(blow: Blow) -> HandlerResult:
    me = blow.me
    return _next(me, blow.fight.table_long(me.action_scripts, me.action))


def _face_away(blow: Blow) -> None:
    blow.me.facing = u8(blow.other.facing ^ _FACING_MIRROR)


def _finishing_blow(blow: Blow) -> bool:
    """No own hit, out of hit points and not the demo: the attacker finishes the knight."""
    me = blow.me
    return me.hit_target == 0 and s16(me.hp) <= 0 and not blow.fight.demo


def _attacker_finishes(blow: Blow, script: int) -> HandlerResult:
    blow.fight.restart_job(blow.other_handle, script)
    return END


# ---- hurt rules ------------------------------------------------------------------------------


def _contact(blow: Blow) -> HandlerResult:
    me, scripts = blow.me, blow.rules.scripts
    if s16(me.hp) > 0:
        _take(me, contact_damage(blow.fight, blow.rules, blow.other))
        return _hurt_script(blow)
    hard = blow.other.action == ACTION_STRIKE_8
    return _next(me, scripts.contact_down_hard if hard else scripts.contact_down)


def knight_duel_hurt(blow: Blow) -> HandlerResult:
    """Knight-versus-knight block, damage and down reaction (§10.19 / §10.31)."""
    if s16(blow.me.hp) <= 0:
        return _next(blow.me, blow.rules.scripts.axe_down_hard)
    if block_check(blow):
        return _own_action_script(blow)
    return _contact(blow)


def _axe(blow: Blow) -> HandlerResult:
    me, attacker, scripts = blow.me, blow.other, blow.rules.scripts
    if s16(me.hp) <= 0:
        hard = attacker.type == TYPE_FIRST_AXE
        return _next(me, scripts.axe_down_hard if hard else scripts.axe_down)
    if block_check(blow):
        return _own_action_script(blow)
    _take(me, blow.fight.table_long(attacker.damage_table, attacker.action))
    return _hurt_script(blow)


def _spear(blow: Blow) -> HandlerResult:
    if block_check(blow):
        return _next(blow.me, blow.rules.scripts.spear_parry)
    _take(blow.me, blow.rules.amounts.spear)
    if _finishing_blow(blow):
        return _attacker_finishes(blow, blow.rules.scripts.spear_kill)
    return _hurt_script(blow)


def _ratmen(blow: Blow) -> HandlerResult:
    me, attacker, scripts = blow.me, blow.other, blow.rules.scripts
    if attacker.action == ACTION_STRIKE_4:
        _take(me, blow.fight.table_long(attacker.damage_table, _RATMEN_CURSE_ENTRY))
        me.life_loss = 1
        return _next(me, scripts.ratmen_curse)
    if attacker.action == ACTION_STRIKE_8:
        _take(me, blow.fight.table_long(attacker.damage_table, _RATMEN_STRIKE_ENTRY))
        return _next(me, scripts.ratmen_strike)
    return _contact(blow)


def _flyer(blow: Blow) -> HandlerResult:
    me, scripts = blow.me, blow.rules.scripts
    _take(me, blow.rules.amounts.flyer)
    same = me.facing == blow.other.facing
    if _finishing_blow(blow):
        kill = scripts.flyer_kill_same if same else scripts.flyer_kill_facing
        return _attacker_finishes(blow, kill)
    return _next(me, scripts.flyer_hurt_same if same else scripts.flyer_hurt_facing)


def _guardian(blow: Blow) -> HandlerResult:
    amounts, action = blow.rules.amounts, blow.other.action
    if action == ACTION_HEAVY:
        _take(blow.me, amounts.guardian_heavy)
        _face_away(blow)
    elif action == ACTION_STRIKE_4:
        _take(blow.me, amounts.guardian_strike)
        _face_away(blow)
    else:
        _take(blow.me, amounts.guardian_other)
    return _hurt_script(blow)


def _stalker(blow: Blow) -> HandlerResult:
    me = blow.me
    _take(me, blow.rules.amounts.stalker)
    if blow.other.action == ACTION_HEAVY and s16(me.hp) <= 0:
        return _next(me, blow.rules.scripts.stalker_flung)
    return _hurt_script(blow)


def _drake(blow: Blow) -> HandlerResult:
    if blow.other.action == ACTION_STRIKE_8:
        _face_away(blow)
    _take(blow.me, blow.rules.amounts.drake)
    return _hurt_script(blow)


def _dragon_flight(blow: Blow) -> HandlerResult:
    blow.other.action = ACTION_HEAVY
    _take(blow.me, protected(blow.fight, blow.me, blow.rules.amounts.dragon_flight))
    return _hurt_script(blow)


def _dragon(blow: Blow) -> HandlerResult:
    me, amounts = blow.me, blow.rules.amounts
    me.y = u16(blow.other.y - amounts.dragon_depth)
    if blow.other.action != ACTION_STRIKE_4:
        return _dragon_flight(blow)
    _take(me, protected(blow.fight, me, amounts.dragon_strike))
    return _hurt_script(blow)


def _dragon_object(blow: Blow) -> HandlerResult:
    fight, me = blow.fight, blow.me
    amount = u16(blow.other.type - blow.rules.amounts.object_offset)
    _take(me, protected(fight, me, amount))
    fight.hop_facing = _HOP_FACING
    fight.hop_table = blow.rules.hop_table
    me.facing = _FACING_LEFT
    return _next(me, blow.rules.scripts.object_hop)


_HURT: dict[HurtRule, Callable[[Blow], HandlerResult]] = {
    HurtRule.CONTACT: _contact,
    HurtRule.KNIGHT: knight_duel_hurt,
    HurtRule.AXE: _axe,
    HurtRule.SPEAR: _spear,
    HurtRule.RATMEN: _ratmen,
    HurtRule.FLYER: _flyer,
    HurtRule.GUARDIAN: _guardian,
    HurtRule.STALKER: _stalker,
    HurtRule.DRAKE: _drake,
    HurtRule.DRAGON: _dragon,
    HurtRule.DRAGON_FLIGHT: _dragon_flight,
    HurtRule.DRAGON_OBJECT: _dragon_object,
}


def knight_hurt(fight: Fight, rules: KnightRules, handle: int) -> HandlerResult:
    """The knight was hit: the hurt rule of the attacker's type (§10.19)."""
    blow = Blow(fight, rules, handle, fight.actor(handle).attacker)
    rule = rules.hurt_rules.get(blow.other.type)
    if rule is None:
        raise FightError(f"no hurt rule for attacker type ${blow.other.type:02X}")
    return _HURT[rule](blow)


# ---- hit rules -------------------------------------------------------------------------------


def _carry_on(blow: Blow) -> HandlerResult:
    me, target = blow.me, blow.other
    if target.type in (TYPE_KNIGHT, TYPE_MAP_KNIGHT):
        if s16(target.hp) > 0:
            if target.action == ACTION_BLOCK:
                return KEEP
        elif me.action == ACTION_STRIKE_8 and not blow.fight.demo:
            return KEEP
    if target.action == ACTION_STRIKE_18:
        return KEEP
    return _next(me, me.after_hit_script)


def knight_hit(fight: Fight, rules: KnightRules, handle: int) -> HandlerResult:
    """The knight's blow landed: the hit rule of the target's type (§10.19)."""
    blow = Blow(fight, rules, handle, fight.actor(handle).hit_target)
    rule = rules.hit_rules.get(blow.other.type)
    if rule is None:
        raise FightError(f"no hit rule for target type ${blow.other.type:02X}")
    if rule is HitRule.HURT_BY_FLIGHT:
        return _dragon_flight(blow)
    return _carry_on(blow)
