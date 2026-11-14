"""Unit tests for pure-integer Knight combat controller & equipment state (T6.2)."""

from __future__ import annotations

import pytest

from pymoonstone.sim.collision import ArenaBounds, HitResult, ObstacleSegment
from pymoonstone.sim.combat_vm import OP_END_FRAME, CombatJob, DrawFrameEvent
from pymoonstone.sim.knight_combat import (
    ATTACK_STAMINA_COST,
    BLOCK_STAMINA_COST,
    DEATH_VBLS,
    HIT_STUN_INVULN_VBLS,
    HIT_STUN_VBLS,
    KNOCKDOWN_VBLS,
    POST_KNOCKDOWN_INVULN_VBLS,
    ROLL_STAMINA_COST,
    ROLL_VBLS,
    RUN_STAMINA_COST,
    KnightAction,
    KnightCombatRecord,
    KnightInput,
    KnightState,
    SecondaryWeapon,
    apply_knight_damage,
    decode_knight_action,
    resolve_knight_hits,
    step_knight_vbl,
)
from pymoonstone.sim.types import Direction, EntityId, KnightId, Pos25D

pytestmark = pytest.mark.sim


def _make_draw_script(cel_slot: int = 1, frame_index: int = 0) -> bytes:
    return bytes([cel_slot, frame_index, 0, 0x01, 0x00, 0x00, OP_END_FRAME])


def test_knight_combat_record_alias_and_bitmask_roundtrip() -> None:
    assert KnightCombatRecord is KnightState
    inp = KnightInput(dx=1, dy=-1, fire=True)
    mask = inp.to_bitmask()
    decoded = KnightInput.from_bitmask(mask)
    assert decoded.dx == 1
    assert decoded.dy == -1
    assert decoded.fire is True

    clamped = KnightInput(dx=5, dy=-9, fire=False).normalized()
    assert clamped.dx == 1
    assert clamped.dy == -1


def test_directional_and_fire_action_decoding() -> None:
    knight = KnightState(direction=Direction.RIGHT, stamina=100, daggers=3)

    assert decode_knight_action(knight, KnightInput()) == KnightAction.IDLE
    assert decode_knight_action(knight, KnightInput(dx=1, dy=0)) == KnightAction.WALK
    assert decode_knight_action(knight, KnightInput(dx=1, dy=1, run=True)) == KnightAction.RUN
    assert (
        decode_knight_action(knight, KnightInput(dy=-1, fire=True)) == KnightAction.OVERHEAD_SLASH
    )
    assert (
        decode_knight_action(knight, KnightInput(dx=1, dy=-1, fire=True))
        == KnightAction.OVERHEAD_SLASH
    )
    assert (
        decode_knight_action(knight, KnightInput(dy=1, dx=0, fire=True)) == KnightAction.LOW_SWEEP
    )
    assert decode_knight_action(knight, KnightInput(dx=1, dy=1, fire=True)) == KnightAction.ROLL
    assert decode_knight_action(knight, KnightInput(dx=0, dy=0, fire=True)) == KnightAction.THRUST
    assert decode_knight_action(knight, KnightInput(dx=1, dy=0, fire=True)) == KnightAction.THRUST
    assert decode_knight_action(knight, KnightInput(dx=-1, dy=0, fire=True)) == KnightAction.BLOCK

    knight.direction = Direction.LEFT
    assert decode_knight_action(knight, KnightInput(dx=1, dy=0, fire=True)) == KnightAction.BLOCK
    assert decode_knight_action(knight, KnightInput(dx=-1, dy=0, fire=True)) == KnightAction.THRUST


def test_run_falls_back_to_walk_when_stamina_exhausted() -> None:
    knight = KnightState(stamina=0)
    assert decode_knight_action(knight, KnightInput(dx=1, run=True)) == KnightAction.WALK

    knight.stamina = RUN_STAMINA_COST
    res = step_knight_vbl(knight, KnightInput(dx=1, run=True))
    assert res.action == KnightAction.RUN
    assert knight.stamina == 0


def test_secondary_weapon_switch_and_ammo_consumption() -> None:
    knight = KnightState(daggers=2, secondary_weapon=SecondaryWeapon.THROWING_KNIFE)

    res1 = step_knight_vbl(knight, KnightInput(secondary=True, weapon_switch=True))
    assert res1.action == KnightAction.THROW_SECONDARY
    assert res1.threw_secondary is True
    assert res1.secondary_weapon == SecondaryWeapon.FIREBALL_SCROLL
    assert res1.remaining_ammo == 1
    assert knight.daggers == 1

    while knight.action_timer > 0:
        step_knight_vbl(knight, KnightInput())

    res2 = step_knight_vbl(knight, KnightInput(secondary=True))
    assert res2.action == KnightAction.THROW_SECONDARY
    assert res2.threw_secondary is True
    assert res2.remaining_ammo == 0

    while knight.action_timer > 0:
        step_knight_vbl(knight, KnightInput())

    res3 = step_knight_vbl(knight, KnightInput(secondary=True))
    assert res3.action == KnightAction.IDLE
    assert res3.threw_secondary is False
    assert res3.remaining_ammo == 0


def test_attack_stamina_and_damage_scaling() -> None:
    knight = KnightState(stamina=50, sword_tier=2, strength=3)
    res = step_knight_vbl(knight, KnightInput(dy=-1, fire=True))
    assert res.action == KnightAction.OVERHEAD_SLASH
    assert knight.stamina == 50 - ATTACK_STAMINA_COST
    overhead_dmg = knight.attack_damage()

    knight.action = KnightAction.LOW_SWEEP
    sweep_dmg = knight.attack_damage()
    knight.action = KnightAction.THRUST
    thrust_dmg = knight.attack_damage()
    assert overhead_dmg > sweep_dmg > thrust_dmg


def test_block_vs_hit_stun_and_armour_deduction() -> None:
    knight = KnightState(hp=100, stamina=40, armour=3, shield=2, action=KnightAction.BLOCK)
    blocked = apply_knight_damage(knight, 15, attacker_id=EntityId(9))
    assert blocked.blocked is True
    assert blocked.stunned is False
    assert blocked.damage_taken == 2  # 15 - (6 + 2*2 + 3) = 2
    assert knight.hp == 98
    assert knight.stamina == 40 - BLOCK_STAMINA_COST

    knight.action = KnightAction.IDLE
    unblocked = apply_knight_damage(knight, 15, attacker_id=EntityId(9), hit_x=162, hit_y=90)
    assert unblocked.blocked is False
    assert unblocked.stunned is True
    assert unblocked.damage_taken == 12  # 15 - 3 = 12
    assert knight.hp == 86
    assert knight.action == KnightAction.HIT_STUN
    assert knight.stun_timer == HIT_STUN_VBLS
    assert knight.invuln_timer == HIT_STUN_INVULN_VBLS
    assert knight.attacker == EntityId(9)
    assert (knight.hit_x, knight.hit_y) == (162, 90)


def test_roll_iframes_and_low_stamina_fallback() -> None:
    knight = KnightState(hp=100, stamina=ROLL_STAMINA_COST)
    res = step_knight_vbl(knight, KnightInput(dx=1, dy=1, fire=True))
    assert res.action == KnightAction.ROLL
    assert knight.stamina == 0
    assert knight.action_timer == ROLL_VBLS
    assert knight.is_invulnerable is True

    evaded = apply_knight_damage(knight, 30, attacker_id=EntityId(2))
    assert evaded.evaded is True
    assert evaded.damage_taken == 0
    assert knight.hp == 100

    for _ in range(ROLL_VBLS):
        step_knight_vbl(knight, KnightInput())
    assert knight.action == KnightAction.IDLE

    knight.stamina = 0
    fallback = step_knight_vbl(knight, KnightInput(dx=1, dy=1, fire=True))
    assert fallback.action == KnightAction.WALK


def test_knockdown_post_invuln_and_depth_gate() -> None:
    knight = KnightState(hp=100, x=160, y=100, depth_reach=10)

    far_miss = apply_knight_damage(
        knight,
        20,
        attacker_id=EntityId(3),
        attacker_pos=Pos25D(160, 120, 0),
    )
    assert far_miss.evaded is True
    assert knight.hp == 100

    kd = apply_knight_damage(
        knight,
        20,
        attacker_id=EntityId(3),
        attacker_pos=Pos25D(160, 105, 0),
        knockdown=True,
    )
    assert kd.knocked_down is True
    assert knight.action == KnightAction.KNOCKDOWN
    assert knight.knockdown_timer == KNOCKDOWN_VBLS

    for _ in range(KNOCKDOWN_VBLS):
        step_knight_vbl(knight, KnightInput(dx=1))
    assert knight.action == KnightAction.IDLE
    assert knight.invuln_timer >= POST_KNOCKDOWN_INVULN_VBLS - 1


def test_death_transition_and_locks() -> None:
    knight = KnightState(hp=10, lives=3, x=160, y=100)
    lethal = apply_knight_damage(knight, 25, attacker_id=EntityId(5))
    assert lethal.killed is True
    assert lethal.remaining_hp == 0
    assert knight.hp == 0
    assert knight.lives == 2
    assert knight.action == KnightAction.DEATH
    assert knight.action_timer == DEATH_VBLS
    assert knight.is_alive is False

    res = step_knight_vbl(knight, KnightInput(dx=1, fire=True))
    assert res.action == KnightAction.DEATH
    assert res.pos == Pos25D(160, 100, 0)

    ignored = apply_knight_damage(knight, 10, attacker_id=EntityId(5))
    assert ignored.evaded is True
    assert knight.lives == 2


def test_resolve_knight_hits_and_arena_obstacles() -> None:
    knight = KnightState(entity_id=EntityId(1), knight_id=KnightId.BLUE, hp=50, x=100, y=100)
    obstacles = (ObstacleSegment(x1=95, x2=110, y=101),)
    bounds = ArenaBounds(min_x=10, max_x=300, min_y=30, max_y=155)

    res = step_knight_vbl(knight, KnightInput(dy=1), obstacles=obstacles, bounds=bounds)
    assert res.pos.y == 100  # blocked from crossing y=101

    hits = [
        HitResult(
            attacker_id=EntityId(9),
            defender_id=EntityId(2),
            hit_x=50,
            hit_y=50,
            hit_type=1,
            point_index=0,
        ),
        HitResult(
            attacker_id=EntityId(9),
            defender_id=EntityId(1),
            hit_x=100,
            hit_y=90,
            hit_type=2,
            point_index=0,
        ),
    ]
    outcomes = resolve_knight_hits(knight, hits, damage_by_hit_type={2: 12})
    assert len(outcomes) == 1
    assert outcomes[0].knocked_down is True
    assert knight.hp == 38


def test_combat_vm_sync_and_stamina_regen() -> None:
    knight = KnightState(
        entity_id=EntityId(1),
        hp=100,
        stamina=95,
        max_stamina=100,
        endurance=3,
        script_ids={KnightAction.IDLE: 0, KnightAction.WALK: 1},
    )
    job = CombatJob(job_id=0, script_id=0, vbl_countdown=1)
    scripts = {
        0: _make_draw_script(cel_slot=1, frame_index=2),
        1: _make_draw_script(cel_slot=1, frame_index=4),
    }

    res = step_knight_vbl(knight, KnightInput(dx=1), job=job, scripts=scripts)
    assert res.action == KnightAction.WALK
    assert job.script_id == 1
    assert any(isinstance(ev, DrawFrameEvent) and ev.frame_index == 4 for ev in res.vm_events)

    for _ in range(10):
        step_knight_vbl(knight, KnightInput(), job=job, scripts=scripts)
    assert knight.stamina > 95
