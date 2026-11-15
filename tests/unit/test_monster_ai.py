"""Unit tests for deterministic 50 Hz monster AI and Lair encounters."""

from __future__ import annotations

import pytest

from pymoonstone.sim.collision import ArenaBounds, ObstacleSegment
from pymoonstone.sim.combat_vm import OP_END_FRAME, OP_JUMP, OP_SPAWN_PROJECTILE
from pymoonstone.sim.monster_ai import (
    CreatureNodeParams,
    LairEncounter,
    MonsterActor,
    MonsterArchetype,
    MonsterSpawnSpec,
    MonsterState,
    TargetSnapshot,
    WaveSpec,
    apply_hit_to_monster,
    default_monster_params,
    params_from_creature_node,
    select_target,
    step_monster_vbl,
)
from pymoonstone.sim.prng import SimPrng
from pymoonstone.sim.types import Direction, EntityId, Pos25D


def _make_loop_script() -> bytes:
    """Minimal 6-VBL combat script that yields each frame and loops."""
    return bytes([OP_END_FRAME, 0x00, OP_JUMP, 3, 0, 0, 0, 0])


def _make_projectile_script(type_id: int = 7) -> bytes:
    """Combat script that spawns a projectile on frame 1 and yields."""
    return bytes(
        [OP_SPAWN_PROJECTILE, 0, 0, 0, 0, type_id, OP_END_FRAME, 0x00, OP_JUMP, 3, 0, 0, 0, 0]
    )


def _run_duel_trace(
    archetype: MonsterArchetype,
    seed: int,
    ticks: int = 180,
) -> tuple[tuple[int, int, int, int, int], list[tuple[int, int, int]]]:
    """Run a deterministic N-tick encounter between a Knight target and a monster."""
    prng = SimPrng(seed)
    params = default_monster_params(archetype)
    monster = MonsterActor(
        entity_id=EntityId(10),
        params=params,
        pos=Pos25D(220, 95, 20 if archetype == MonsterArchetype.DRAGON else 0),
    )
    knight_pos = Pos25D(110, 95, 0)
    knight_hp = 100
    scripts = {i: _make_loop_script() for i in range(6)}

    melee_count = 0
    proj_count = 0
    trajectory: list[tuple[int, int, int]] = []

    for tick in range(ticks):
        target = TargetSnapshot(
            entity_id=EntityId(1),
            pos=knight_pos,
            hp=knight_hp,
            alive=knight_hp > 0,
        )
        res = step_monster_vbl(monster, [target], prng, scripts=scripts)
        if res.melee_intent is not None:
            melee_count += 1
            knight_hp = max(0, knight_hp - res.melee_intent.damage)
        proj_count += len(res.spawned_projectiles)
        if tick % 30 == 0:
            trajectory.append((monster.pos.x, monster.pos.y, int(monster.state)))

    summary = (
        monster.pos.x,
        monster.pos.y,
        monster.pos.z,
        int(monster.state),
        melee_count + proj_count,
    )
    return summary, trajectory


@pytest.mark.parametrize("archetype", list(MonsterArchetype))
def test_all_archetypes_reproducible_from_fixed_seed(archetype: MonsterArchetype) -> None:
    summary_a, trace_a = _run_duel_trace(archetype, seed=0xCAFE_BABE, ticks=180)
    summary_b, trace_b = _run_duel_trace(archetype, seed=0xCAFE_BABE, ticks=180)

    assert summary_a == summary_b
    assert trace_a == trace_b
    assert summary_a[4] > 0


def test_target_selection_prefers_nearest_living_target() -> None:
    prng = SimPrng(42)
    monster = MonsterActor(
        entity_id=EntityId(10),
        params=default_monster_params(MonsterArchetype.RATMAN),
        pos=Pos25D(150, 90, 0),
    )
    dead_near = TargetSnapshot(entity_id=EntityId(1), pos=Pos25D(152, 90, 0), hp=0, alive=False)
    far_alive = TargetSnapshot(entity_id=EntityId(2), pos=Pos25D(280, 90, 0), hp=40, alive=True)
    close_alive = TargetSnapshot(entity_id=EntityId(3), pos=Pos25D(175, 92, 0), hp=40, alive=True)

    chosen = select_target(monster, [dead_near, far_alive, close_alive], prng)
    assert chosen is not None
    assert chosen.entity_id == EntityId(3)
    assert monster.target_id == EntityId(3)


def test_arena_obstacles_clamp_monster_approach() -> None:
    prng = SimPrng(7)
    monster = MonsterActor(
        entity_id=EntityId(11),
        params=default_monster_params(MonsterArchetype.TROGG_AXE),
        pos=Pos25D(100, 80, 0),
    )
    monster.state = MonsterState.APPROACH
    wall = ObstacleSegment(x1=80, x2=140, y=90)
    target = TargetSnapshot(entity_id=EntityId(1), pos=Pos25D(100, 120, 0), hp=50)

    for _ in range(25):
        step_monster_vbl(monster, [target], prng, obstacles=[wall])

    assert monster.pos.y <= 90


def test_low_hp_standard_monster_retreats() -> None:
    prng = SimPrng(99)
    params = default_monster_params(MonsterArchetype.RATMAN)
    monster = MonsterActor(
        entity_id=EntityId(12),
        params=params,
        pos=Pos25D(130, 90, 0),
        hp=4,
    )
    target = TargetSnapshot(entity_id=EntityId(1), pos=Pos25D(110, 90, 0), hp=50)

    saw_retreat = False
    for _ in range(10):
        res = step_monster_vbl(monster, [target], prng)
        if res.state == MonsterState.RETREAT:
            saw_retreat = True

    assert saw_retreat
    assert monster.pos.x > 130


def test_combat_job_spawn_opcode_emits_projectile_intent() -> None:
    prng = SimPrng(123)
    monster = MonsterActor(
        entity_id=EntityId(13),
        params=default_monster_params(MonsterArchetype.DEMON),
        pos=Pos25D(200, 90, 0),
    )
    scripts = {i: _make_projectile_script(type_id=9) for i in range(6)}
    target = TargetSnapshot(entity_id=EntityId(1), pos=Pos25D(100, 90, 0), hp=50)

    res = step_monster_vbl(monster, [target], prng, scripts=scripts)
    assert any(p.projectile_type_id == 9 for p in res.spawned_projectiles)


def test_dragon_fly_patrol_and_breath_hazard() -> None:
    prng = SimPrng(2025)
    dragon = MonsterActor(
        entity_id=EntityId(20),
        params=default_monster_params(MonsterArchetype.DRAGON),
        pos=Pos25D(260, 95, 24),
    )
    target = TargetSnapshot(entity_id=EntityId(1), pos=Pos25D(110, 95, 0), hp=100)

    saw_fly = False
    saw_hazard = False
    for _ in range(120):
        res = step_monster_vbl(dragon, [target], prng)
        if res.state == MonsterState.FLY_PATROL:
            saw_fly = True
        if any(p.is_hazard for p in res.spawned_projectiles):
            saw_hazard = True

    assert saw_fly
    assert saw_hazard


def test_balok_enrages_below_half_hp_and_guardian_guards() -> None:
    prng = SimPrng(55)
    balok = MonsterActor(
        entity_id=EntityId(21),
        params=default_monster_params(MonsterArchetype.BALOK),
        pos=Pos25D(200, 90, 0),
        hp=40,
    )
    balok.state = MonsterState.MELEE_ATTACK
    balok.state_ticks = balok.params.attack_duration_ticks - 1
    target = TargetSnapshot(entity_id=EntityId(1), pos=Pos25D(170, 90, 0), hp=80)
    step_monster_vbl(balok, [target], prng)
    assert balok.cooldown_ticks < balok.params.attack_cooldown_ticks

    guardian = MonsterActor(
        entity_id=EntityId(22),
        params=default_monster_params(MonsterArchetype.GUARDIAN),
        pos=Pos25D(180, 90, 0),
    )
    guardian.cooldown_ticks = 15
    step_monster_vbl(guardian, [target], prng)
    assert guardian.state == MonsterState.GUARD_STANCE

    guarded_hit = apply_hit_to_monster(guardian, raw_damage=12, attacker_x=150)
    assert guarded_hit.actual_damage == 1


def test_hit_stun_knockback_and_death_transition() -> None:
    prng = SimPrng(17)
    monster = MonsterActor(
        entity_id=EntityId(30),
        params=default_monster_params(MonsterArchetype.TROGG_SPEAR),
        pos=Pos25D(150, 90, 0),
    )
    target = TargetSnapshot(entity_id=EntityId(1), pos=Pos25D(120, 90, 0), hp=50)

    dmg = apply_hit_to_monster(monster, raw_damage=10, attacker_x=120)
    assert not dmg.killed
    assert dmg.new_state == MonsterState.KNOCKBACK
    start_x = monster.pos.x

    for _ in range(monster.params.hit_stun_ticks + 1):
        step_monster_vbl(monster, [target], prng)

    assert monster.pos.x > start_x
    assert monster.state != MonsterState.KNOCKBACK

    lethal = apply_hit_to_monster(monster, raw_damage=100, attacker_x=120)
    assert lethal.killed
    assert lethal.new_state == MonsterState.DYING

    for _ in range(20):
        step_monster_vbl(monster, [target], prng)

    assert monster.is_dead
    assert not monster.job.active


def test_lair_encounter_waves_and_projectile_hits() -> None:
    prng = SimPrng(31415)
    wave1 = WaveSpec(
        wave_index=0,
        spawns=(
            MonsterSpawnSpec(
                entity_id=EntityId(100),
                params=default_monster_params(MonsterArchetype.MUDMAN),
                pos=Pos25D(240, 90, 0),
                direction=Direction.LEFT,
            ),
        ),
    )
    wave2 = WaveSpec(
        wave_index=1,
        spawns=(
            MonsterSpawnSpec(
                entity_id=EntityId(101),
                params=default_monster_params(MonsterArchetype.GUARDIAN),
                pos=Pos25D(260, 90, 0),
                direction=Direction.LEFT,
            ),
        ),
    )
    encounter = LairEncounter(waves=(wave1, wave2), bounds=ArenaBounds())
    target = TargetSnapshot(entity_id=EntityId(1), pos=Pos25D(130, 90, 0), hp=100)

    saw_proj_hit = False
    for _ in range(150):
        res = encounter.step_vbl([target], prng)
        if res.projectile_hits:
            saw_proj_hit = True
            break

    assert saw_proj_hit

    apply_hit_to_monster(encounter.monsters[0], raw_damage=200, attacker_x=130)
    for _ in range(25):
        res = encounter.step_vbl([target], prng)
        if encounter.current_wave == 1:
            break

    assert encounter.current_wave == 1
    assert not encounter.completed
    assert encounter.monsters[0].entity_id == EntityId(101)

    apply_hit_to_monster(encounter.monsters[0], raw_damage=500, attacker_x=130)
    for _ in range(25):
        res = encounter.step_vbl([target], prng)
        if res.encounter_completed:
            break

    assert encounter.completed


def test_params_from_creature_node_applies_runtime_fields() -> None:
    node = CreatureNodeParams(creature_type=5, defence=7, aux_value=12)
    params = params_from_creature_node(
        MonsterArchetype.DEMON,
        node,
        script_ids={"idle": 10, "melee": 12},
    )
    # node.defence is not flat armor subtraction against knight contact damage
    assert params.defense == default_monster_params(MonsterArchetype.DEMON).defense
    assert params.max_hp == default_monster_params(MonsterArchetype.DEMON).max_hp + 12
    assert params.idle_script_id == 10
    assert params.melee_script_id == 12
