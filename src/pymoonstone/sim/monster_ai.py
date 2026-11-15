"""Pure-integer 50 Hz monster AI state machines and Lair encounters (ARCHITECTURE.md §4, §7)."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import IntEnum

from pymoonstone.sim.collision import (
    DEFAULT_ARENA_BOUNDS,
    DEFAULT_DEPTH_THRESHOLD,
    ArenaBounds,
    ObstacleSegment,
    depth_close,
    resolve_arena_movement,
)
from pymoonstone.sim.combat_vm import (
    ArenaBounds as VmArenaBounds,
    CombatEvent,
    CombatJob,
    CombatWork,
    SpawnEvent,
    step_vbl as step_combat_job_vbl,
)
from pymoonstone.sim.m68k import c_div
from pymoonstone.sim.prng import SimPrng
from pymoonstone.sim.types import Direction, EntityId, Pos25D


class MonsterArchetype(IntEnum):
    """Standard region/Lair monsters and boss archetypes (ARCHITECTURE.md §4.2, §7)."""

    TROGG_SPEAR = 0
    TROGG_AXE = 1
    RATMAN = 2
    MUDMAN = 3
    BEASTMAN = 4
    DEMON = 5
    TROLL = 6
    BALOK = 7
    DRAGON = 8
    GUARDIAN = 9


class MonsterState(IntEnum):
    """AI state machine states for standard monsters and bosses."""

    IDLE = 0
    APPROACH = 1
    FLANK = 2
    RETREAT = 3
    MELEE_ATTACK = 4
    RANGED_ATTACK = 5
    FLY_PATROL = 6
    BREATH_ATTACK = 7
    GUARD_STANCE = 8
    HIT_STUN = 9
    KNOCKBACK = 10
    DYING = 11
    DEAD = 12


@dataclass(slots=True, frozen=True)
class CreatureNodeParams:
    """Pure-integer creature/lair node parameters decoupled from `assets` (ARCHITECTURE.md §5.2)."""

    creature_type: int
    defence: int
    aux_value: int = 0


@dataclass(slots=True, frozen=True)
class MonsterParams:
    """Immutable integer tuning and script-ID parameters for a monster archetype."""

    archetype: MonsterArchetype
    max_hp: int
    move_speed_x: int
    move_speed_y: int
    melee_range_x: int
    preferred_range_x: int
    ranged_min_x: int
    ranged_max_x: int
    base_damage: int
    defense: int
    attack_cooldown_ticks: int
    attack_duration_ticks: int
    hit_stun_ticks: int
    knockback_x: int
    aggression_pct: int
    ranged_chance_pct: int
    retreat_hp_pct: int
    depth_threshold: int = DEFAULT_DEPTH_THRESHOLD
    idle_script_id: int = 0
    walk_script_id: int = 1
    melee_script_id: int = 2
    ranged_script_id: int = 3
    hurt_script_id: int = 4
    death_script_id: int = 5
    projectile_type_id: int = 1
    is_boss: bool = False


_ARCHETYPE_PRESETS: dict[MonsterArchetype, MonsterParams] = {
    MonsterArchetype.TROGG_SPEAR: MonsterParams(
        archetype=MonsterArchetype.TROGG_SPEAR,
        max_hp=24,
        move_speed_x=2,
        move_speed_y=1,
        melee_range_x=36,
        preferred_range_x=30,
        ranged_min_x=50,
        ranged_max_x=150,
        base_damage=6,
        defense=1,
        attack_cooldown_ticks=25,
        attack_duration_ticks=18,
        hit_stun_ticks=12,
        knockback_x=3,
        aggression_pct=65,
        ranged_chance_pct=20,
        retreat_hp_pct=25,
        projectile_type_id=1,
    ),
    MonsterArchetype.TROGG_AXE: MonsterParams(
        archetype=MonsterArchetype.TROGG_AXE,
        max_hp=30,
        move_speed_x=2,
        move_speed_y=1,
        melee_range_x=28,
        preferred_range_x=24,
        ranged_min_x=0,
        ranged_max_x=0,
        base_damage=8,
        defense=2,
        attack_cooldown_ticks=30,
        attack_duration_ticks=20,
        hit_stun_ticks=12,
        knockback_x=2,
        aggression_pct=75,
        ranged_chance_pct=0,
        retreat_hp_pct=15,
    ),
    MonsterArchetype.RATMAN: MonsterParams(
        archetype=MonsterArchetype.RATMAN,
        max_hp=20,
        move_speed_x=3,
        move_speed_y=2,
        melee_range_x=26,
        preferred_range_x=22,
        ranged_min_x=0,
        ranged_max_x=0,
        base_damage=5,
        defense=1,
        attack_cooldown_ticks=18,
        attack_duration_ticks=14,
        hit_stun_ticks=10,
        knockback_x=3,
        aggression_pct=80,
        ranged_chance_pct=0,
        retreat_hp_pct=30,
    ),
    MonsterArchetype.MUDMAN: MonsterParams(
        archetype=MonsterArchetype.MUDMAN,
        max_hp=34,
        move_speed_x=1,
        move_speed_y=1,
        melee_range_x=28,
        preferred_range_x=24,
        ranged_min_x=45,
        ranged_max_x=140,
        base_damage=7,
        defense=3,
        attack_cooldown_ticks=32,
        attack_duration_ticks=20,
        hit_stun_ticks=10,
        knockback_x=2,
        aggression_pct=60,
        ranged_chance_pct=35,
        retreat_hp_pct=10,
        projectile_type_id=2,
    ),
    MonsterArchetype.BEASTMAN: MonsterParams(
        archetype=MonsterArchetype.BEASTMAN,
        max_hp=42,
        move_speed_x=2,
        move_speed_y=2,
        melee_range_x=32,
        preferred_range_x=26,
        ranged_min_x=0,
        ranged_max_x=0,
        base_damage=10,
        defense=2,
        attack_cooldown_ticks=24,
        attack_duration_ticks=18,
        hit_stun_ticks=10,
        knockback_x=2,
        aggression_pct=85,
        ranged_chance_pct=0,
        retreat_hp_pct=10,
    ),
    MonsterArchetype.DEMON: MonsterParams(
        archetype=MonsterArchetype.DEMON,
        max_hp=55,
        move_speed_x=2,
        move_speed_y=2,
        melee_range_x=34,
        preferred_range_x=28,
        ranged_min_x=55,
        ranged_max_x=180,
        base_damage=12,
        defense=3,
        attack_cooldown_ticks=22,
        attack_duration_ticks=18,
        hit_stun_ticks=8,
        knockback_x=2,
        aggression_pct=80,
        ranged_chance_pct=45,
        retreat_hp_pct=0,
        projectile_type_id=3,
    ),
    MonsterArchetype.TROLL: MonsterParams(
        archetype=MonsterArchetype.TROLL,
        max_hp=70,
        move_speed_x=1,
        move_speed_y=1,
        melee_range_x=38,
        preferred_range_x=32,
        ranged_min_x=0,
        ranged_max_x=0,
        base_damage=15,
        defense=4,
        attack_cooldown_ticks=36,
        attack_duration_ticks=24,
        hit_stun_ticks=8,
        knockback_x=1,
        aggression_pct=75,
        ranged_chance_pct=0,
        retreat_hp_pct=0,
    ),
    MonsterArchetype.BALOK: MonsterParams(
        archetype=MonsterArchetype.BALOK,
        max_hp=120,
        move_speed_x=2,
        move_speed_y=2,
        melee_range_x=40,
        preferred_range_x=34,
        ranged_min_x=60,
        ranged_max_x=220,
        base_damage=18,
        defense=5,
        attack_cooldown_ticks=24,
        attack_duration_ticks=20,
        hit_stun_ticks=6,
        knockback_x=1,
        aggression_pct=90,
        ranged_chance_pct=55,
        retreat_hp_pct=0,
        projectile_type_id=4,
        is_boss=True,
    ),
    MonsterArchetype.DRAGON: MonsterParams(
        archetype=MonsterArchetype.DRAGON,
        max_hp=160,
        move_speed_x=3,
        move_speed_y=2,
        melee_range_x=48,
        preferred_range_x=42,
        ranged_min_x=60,
        ranged_max_x=240,
        base_damage=22,
        defense=6,
        attack_cooldown_ticks=28,
        attack_duration_ticks=22,
        hit_stun_ticks=6,
        knockback_x=1,
        aggression_pct=85,
        ranged_chance_pct=65,
        retreat_hp_pct=0,
        projectile_type_id=5,
        is_boss=True,
    ),
    MonsterArchetype.GUARDIAN: MonsterParams(
        archetype=MonsterArchetype.GUARDIAN,
        max_hp=100,
        move_speed_x=2,
        move_speed_y=1,
        melee_range_x=36,
        preferred_range_x=30,
        ranged_min_x=64,
        ranged_max_x=200,
        base_damage=14,
        defense=5,
        attack_cooldown_ticks=26,
        attack_duration_ticks=18,
        hit_stun_ticks=6,
        knockback_x=1,
        aggression_pct=80,
        ranged_chance_pct=40,
        retreat_hp_pct=0,
        projectile_type_id=6,
        is_boss=True,
    ),
}


def default_monster_params(archetype: MonsterArchetype) -> MonsterParams:
    """Return baseline pure-integer parameters for ``archetype``."""
    return _ARCHETYPE_PRESETS[archetype]


def params_from_creature_node(
    archetype: MonsterArchetype,
    node: CreatureNodeParams,
    *,
    script_ids: Mapping[str, int] | None = None,
) -> MonsterParams:
    """Build ``MonsterParams`` combining archetype defaults with runtime ``mog`` node values."""
    base = _ARCHETYPE_PRESETS[archetype]
    ids = script_ids or {}
    return MonsterParams(
        archetype=archetype,
        max_hp=max(1, base.max_hp + node.aux_value),
        move_speed_x=base.move_speed_x,
        move_speed_y=base.move_speed_y,
        melee_range_x=base.melee_range_x,
        preferred_range_x=base.preferred_range_x,
        ranged_min_x=base.ranged_min_x,
        ranged_max_x=base.ranged_max_x,
        base_damage=base.base_damage,
        defense=base.defense,
        attack_cooldown_ticks=base.attack_cooldown_ticks,
        attack_duration_ticks=base.attack_duration_ticks,
        hit_stun_ticks=base.hit_stun_ticks,
        knockback_x=base.knockback_x,
        aggression_pct=base.aggression_pct,
        ranged_chance_pct=base.ranged_chance_pct,
        retreat_hp_pct=base.retreat_hp_pct,
        depth_threshold=base.depth_threshold,
        idle_script_id=ids.get("idle", base.idle_script_id),
        walk_script_id=ids.get("walk", base.walk_script_id),
        melee_script_id=ids.get("melee", base.melee_script_id),
        ranged_script_id=ids.get("ranged", base.ranged_script_id),
        hurt_script_id=ids.get("hurt", base.hurt_script_id),
        death_script_id=ids.get("death", base.death_script_id),
        projectile_type_id=base.projectile_type_id,
        is_boss=base.is_boss,
    )


@dataclass(slots=True, frozen=True)
class TargetSnapshot:
    """Read-only target view passed into monster AI perception each VBL tick."""

    entity_id: EntityId
    pos: Pos25D
    hp: int
    alive: bool = True
    blocking: bool = False


@dataclass(slots=True, frozen=True)
class ProjectileIntent:
    """Projectile or hazard spawn intent emitted by monster AI or combat scripts."""

    owner_id: EntityId
    projectile_type_id: int
    pos: Pos25D
    vx: int
    vy: int
    vz: int
    direction: Direction
    damage: int
    ttl_ticks: int
    is_hazard: bool = False


@dataclass(slots=True, frozen=True)
class MeleeAttackIntent:
    """Active melee strike window emitted during ``MELEE_ATTACK``."""

    attacker_id: EntityId
    pos: Pos25D
    direction: Direction
    reach_x: int
    depth_threshold: int
    damage: int


@dataclass(slots=True, frozen=True)
class MonsterDamageEvent:
    """Result of applying a hit to a monster."""

    monster_id: EntityId
    raw_damage: int
    actual_damage: int
    remaining_hp: int
    killed: bool
    new_state: MonsterState


@dataclass(slots=True, frozen=True)
class ProjectileHitEvent:
    """Projectile impact against a target inside the 2.5D depth band."""

    owner_id: EntityId
    target_id: EntityId
    projectile_type_id: int
    damage: int
    pos: Pos25D


@dataclass(slots=True, frozen=True)
class MonsterTickResult:
    """Per-VBL output for a single monster update."""

    monster_id: EntityId
    state: MonsterState
    combat_events: tuple[CombatEvent, ...]
    spawned_projectiles: tuple[ProjectileIntent, ...]
    melee_intent: MeleeAttackIntent | None


@dataclass(slots=True)
class ProjectileState:
    """Active in-arena projectile or breath hazard stepped at 50 Hz."""

    owner_id: EntityId
    projectile_type_id: int
    pos: Pos25D
    vx: int
    vy: int
    vz: int
    direction: Direction
    damage: int
    ttl_ticks: int
    is_hazard: bool = False
    active: bool = True


@dataclass(slots=True)
class MonsterActor:
    """Mutable 50 Hz monster simulation record bound to a ``CombatJob`` slot."""

    entity_id: EntityId
    params: MonsterParams
    pos: Pos25D
    direction: Direction = Direction.LEFT
    state: MonsterState = MonsterState.IDLE
    hp: int = -1
    target_id: EntityId | None = None
    state_ticks: int = 0
    cooldown_ticks: int = 0
    stun_ticks: int = 0
    knockback_vx: int = 0
    flank_dir_y: int = 1
    flight_phase_ticks: int = 0
    attack_emitted: bool = False
    job: CombatJob = field(default_factory=CombatJob)

    def __post_init__(self) -> None:
        if self.hp < 0:
            self.hp = self.params.max_hp
        self.job.owner_id = self.entity_id
        self.job.x = self.pos.x
        self.job.y = self.pos.y
        self.job.z = self.pos.z
        self.job.direction = self.direction
        self.job.script_id = self.params.idle_script_id
        self.job.work = CombatWork()

    @property
    def is_alive(self) -> bool:
        """True while the monster has remaining HP and is not dying/dead."""
        return self.hp > 0 and self.state not in (MonsterState.DYING, MonsterState.DEAD)

    @property
    def is_dead(self) -> bool:
        """True once the monster has finished dying and left combat."""
        return self.state == MonsterState.DEAD

    def sync_job(self) -> None:
        """Synchronize the underlying ``CombatJob`` position and facing with this actor."""
        self.job.x = self.pos.x
        self.job.y = self.pos.y
        self.job.z = self.pos.z
        self.job.direction = self.direction


def _to_vm_bounds(bounds: ArenaBounds) -> VmArenaBounds:
    return VmArenaBounds(
        min_x=bounds.min_x,
        max_x=bounds.max_x,
        min_y=bounds.min_y,
        max_y=bounds.max_y,
    )


def _transition_state(monster: MonsterActor, new_state: MonsterState) -> None:
    if monster.state == new_state:
        return
    monster.state = new_state
    monster.state_ticks = 0
    monster.attack_emitted = False
    script_map = {
        MonsterState.IDLE: monster.params.idle_script_id,
        MonsterState.APPROACH: monster.params.walk_script_id,
        MonsterState.FLANK: monster.params.walk_script_id,
        MonsterState.RETREAT: monster.params.walk_script_id,
        MonsterState.FLY_PATROL: monster.params.walk_script_id,
        MonsterState.GUARD_STANCE: monster.params.idle_script_id,
        MonsterState.MELEE_ATTACK: monster.params.melee_script_id,
        MonsterState.RANGED_ATTACK: monster.params.ranged_script_id,
        MonsterState.BREATH_ATTACK: monster.params.ranged_script_id,
        MonsterState.HIT_STUN: monster.params.hurt_script_id,
        MonsterState.KNOCKBACK: monster.params.hurt_script_id,
        MonsterState.DYING: monster.params.death_script_id,
        MonsterState.DEAD: monster.params.death_script_id,
    }
    target_script = script_map[new_state]
    if new_state == MonsterState.DEAD:
        monster.job.running = False
        monster.job.active = False
    if monster.job.script_id != target_script:
        monster.job.script_id = target_script
        monster.job.pc = 0
        monster.job.running = new_state != MonsterState.DEAD
        monster.job.active = new_state != MonsterState.DEAD


def select_target(
    monster: MonsterActor,
    targets: Sequence[TargetSnapshot],
    prng: SimPrng,
) -> TargetSnapshot | None:
    """Select the nearest living target in 2.5D weighted distance, with PRNG tie-breaking."""
    best: TargetSnapshot | None = None
    best_dist = 0
    for candidate in targets:
        if not candidate.alive or candidate.hp <= 0:
            continue
        dist = abs(candidate.pos.x - monster.pos.x) + abs(candidate.pos.y - monster.pos.y) * 2
        if best is None or dist < best_dist:
            best = candidate
            best_dist = dist
        elif dist == best_dist and prng.rand_bool(1, 2):
            best = candidate
    monster.target_id = best.entity_id if best is not None else None
    return best


def _face_target(monster: MonsterActor, target: TargetSnapshot) -> None:
    if target.pos.x < monster.pos.x:
        monster.direction = Direction.LEFT
    elif target.pos.x > monster.pos.x:
        monster.direction = Direction.RIGHT


def _sign(value: int) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _move_monster(
    monster: MonsterActor,
    dx: int,
    dy: int,
    dz: int,
    obstacles: Sequence[ObstacleSegment],
    bounds: ArenaBounds,
) -> None:
    moved = resolve_arena_movement(monster.pos, dx, dy, obstacles, bounds=bounds)
    new_z = max(0, min(120, moved.z + dz))
    monster.pos = Pos25D(moved.x, moved.y, new_z)
    monster.sync_job()


def _effective_cooldown(monster: MonsterActor) -> int:
    base = monster.params.attack_cooldown_ticks
    if (
        monster.params.archetype == MonsterArchetype.BALOK
        and monster.hp * 2 <= monster.params.max_hp
    ):
        return max(8, c_div(base * 2, 3))
    return base


def _should_retreat(monster: MonsterActor) -> bool:
    pct = monster.params.retreat_hp_pct
    if pct <= 0 or monster.params.is_boss:
        return False
    return monster.hp * 100 <= monster.params.max_hp * pct


def _try_choose_attack(
    monster: MonsterActor,
    abs_dx: int,
    depth_ok: bool,
    prng: SimPrng,
) -> MonsterState | None:
    if monster.cooldown_ticks != 0 or not depth_ok:
        return None
    if (
        abs_dx <= monster.params.melee_range_x
        and prng.rand_range(100) < monster.params.aggression_pct
    ):
        return MonsterState.MELEE_ATTACK
    in_ranged = monster.params.ranged_min_x <= abs_dx <= monster.params.ranged_max_x
    if (
        in_ranged
        and monster.params.ranged_chance_pct > 0
        and prng.rand_range(100) < monster.params.ranged_chance_pct
    ):
        if monster.params.archetype in (MonsterArchetype.BALOK, MonsterArchetype.DRAGON):
            return MonsterState.BREATH_ATTACK
        return MonsterState.RANGED_ATTACK
    return None


def _choose_non_attack_state(
    monster: MonsterActor,
    target: TargetSnapshot,
    abs_dx: int,
    depth_ok: bool,
) -> MonsterState:
    if monster.params.archetype == MonsterArchetype.GUARDIAN and monster.cooldown_ticks > 0:
        return MonsterState.GUARD_STANCE
    if abs_dx <= monster.params.preferred_range_x and not depth_ok:
        monster.flank_dir_y = 1 if target.pos.y >= monster.pos.y else -1
        return MonsterState.FLANK
    return MonsterState.APPROACH


def _choose_combat_action(
    monster: MonsterActor,
    target: TargetSnapshot,
    prng: SimPrng,
) -> None:
    abs_dx = abs(target.pos.x - monster.pos.x)
    depth_ok = depth_close(monster.pos.y, target.pos.y, monster.params.depth_threshold)

    if monster.params.archetype == MonsterArchetype.DRAGON and monster.pos.z > 0:
        _transition_state(monster, MonsterState.FLY_PATROL)
        return

    if _should_retreat(monster) and abs_dx < monster.params.preferred_range_x + 24:
        _transition_state(monster, MonsterState.RETREAT)
        return

    attack_state = _try_choose_attack(monster, abs_dx, depth_ok, prng)
    if attack_state is not None:
        _transition_state(monster, attack_state)
        return

    _transition_state(monster, _choose_non_attack_state(monster, target, abs_dx, depth_ok))


def _step_idle(
    monster: MonsterActor,
    target: TargetSnapshot | None,
    prng: SimPrng,
    _obstacles: Sequence[ObstacleSegment],
    _bounds: ArenaBounds,
    _spawned: list[ProjectileIntent],
) -> MeleeAttackIntent | None:
    if target is None:
        return None
    _face_target(monster, target)
    if monster.params.archetype == MonsterArchetype.DRAGON and monster.state_ticks >= 10:
        _transition_state(monster, MonsterState.FLY_PATROL)
        return None
    _choose_combat_action(monster, target, prng)
    return None


def _step_approach(
    monster: MonsterActor,
    target: TargetSnapshot | None,
    prng: SimPrng,
    obstacles: Sequence[ObstacleSegment],
    bounds: ArenaBounds,
    _spawned: list[ProjectileIntent],
) -> MeleeAttackIntent | None:
    if target is None:
        _transition_state(monster, MonsterState.IDLE)
        return None
    _face_target(monster, target)
    dx_to = target.pos.x - monster.pos.x
    dy_to = target.pos.y - monster.pos.y
    step_x = 0
    if abs(dx_to) > monster.params.preferred_range_x:
        step_x = _sign(dx_to) * monster.params.move_speed_x
    step_y = _sign(dy_to) * min(monster.params.move_speed_y, abs(dy_to))
    _move_monster(monster, step_x, step_y, -min(2, monster.pos.z), obstacles, bounds)

    if monster.state_ticks >= 6:
        _choose_combat_action(monster, target, prng)
    return None


def _step_flank(
    monster: MonsterActor,
    target: TargetSnapshot | None,
    prng: SimPrng,
    obstacles: Sequence[ObstacleSegment],
    bounds: ArenaBounds,
    _spawned: list[ProjectileIntent],
) -> MeleeAttackIntent | None:
    if target is None:
        _transition_state(monster, MonsterState.IDLE)
        return None
    _face_target(monster, target)
    if monster.state_ticks == 1:
        monster.flank_dir_y = 1 if prng.rand_bool(1, 2) else -1
    step_x = monster.direction.sign * monster.params.move_speed_x
    step_y = monster.flank_dir_y * monster.params.move_speed_y
    _move_monster(monster, step_x, step_y, 0, obstacles, bounds)
    if monster.state_ticks >= 12 or depth_close(
        monster.pos.y, target.pos.y, monster.params.depth_threshold
    ):
        _choose_combat_action(monster, target, prng)
    return None


def _step_retreat(
    monster: MonsterActor,
    target: TargetSnapshot | None,
    prng: SimPrng,
    obstacles: Sequence[ObstacleSegment],
    bounds: ArenaBounds,
    _spawned: list[ProjectileIntent],
) -> MeleeAttackIntent | None:
    if target is None:
        _transition_state(monster, MonsterState.IDLE)
        return None
    _face_target(monster, target)
    away_sign = -monster.direction.sign
    step_x = away_sign * monster.params.move_speed_x
    step_y = _sign(monster.pos.y - target.pos.y) * monster.params.move_speed_y
    _move_monster(monster, step_x, step_y, 0, obstacles, bounds)
    if monster.state_ticks >= 16:
        _choose_combat_action(monster, target, prng)
    return None


def _step_melee_attack(
    monster: MonsterActor,
    target: TargetSnapshot | None,
    _prng: SimPrng,
    _obstacles: Sequence[ObstacleSegment],
    _bounds: ArenaBounds,
    _spawned: list[ProjectileIntent],
) -> MeleeAttackIntent | None:
    strike_tick = max(1, c_div(monster.params.attack_duration_ticks, 2))
    intent: MeleeAttackIntent | None = None
    if not monster.attack_emitted and monster.state_ticks >= strike_tick:
        monster.attack_emitted = True
        intent = MeleeAttackIntent(
            attacker_id=monster.entity_id,
            pos=monster.pos,
            direction=monster.direction,
            reach_x=monster.params.melee_range_x,
            depth_threshold=monster.params.depth_threshold,
            damage=monster.params.base_damage,
        )
    if monster.state_ticks >= monster.params.attack_duration_ticks:
        monster.cooldown_ticks = _effective_cooldown(monster)
        next_state = MonsterState.IDLE if target is None else MonsterState.APPROACH
        _transition_state(monster, next_state)
    return intent


def _make_projectile_intent(monster: MonsterActor, *, is_hazard: bool) -> ProjectileIntent:
    speed_x = 4 if monster.params.archetype == MonsterArchetype.DRAGON else 3
    if monster.params.archetype == MonsterArchetype.BALOK:
        speed_x = 4
    vx = monster.direction.sign * speed_x
    spawn_x = monster.pos.x + monster.direction.sign * 16
    return ProjectileIntent(
        owner_id=monster.entity_id,
        projectile_type_id=monster.params.projectile_type_id,
        pos=Pos25D(spawn_x, monster.pos.y, monster.pos.z),
        vx=vx,
        vy=0,
        vz=0,
        direction=monster.direction,
        damage=max(1, monster.params.base_damage - 2),
        ttl_ticks=75,
        is_hazard=is_hazard,
    )


def _step_ranged_or_breath(
    monster: MonsterActor,
    target: TargetSnapshot | None,
    _prng: SimPrng,
    _obstacles: Sequence[ObstacleSegment],
    _bounds: ArenaBounds,
    spawned: list[ProjectileIntent],
) -> MeleeAttackIntent | None:
    if target is not None and monster.state_ticks == 1:
        _face_target(monster, target)
    fire_tick = max(1, c_div(monster.params.attack_duration_ticks, 2))
    if not monster.attack_emitted and monster.state_ticks >= fire_tick:
        monster.attack_emitted = True
        is_hazard = monster.state == MonsterState.BREATH_ATTACK
        spawned.append(_make_projectile_intent(monster, is_hazard=is_hazard))
    if monster.state_ticks >= monster.params.attack_duration_ticks:
        monster.cooldown_ticks = _effective_cooldown(monster)
        _transition_state(monster, MonsterState.IDLE)
    return None


def _step_fly_patrol(
    monster: MonsterActor,
    target: TargetSnapshot | None,
    prng: SimPrng,
    obstacles: Sequence[ObstacleSegment],
    bounds: ArenaBounds,
    _spawned: list[ProjectileIntent],
) -> MeleeAttackIntent | None:
    monster.flight_phase_ticks += 1
    if target is None:
        _move_monster(monster, 0, 0, -min(2, monster.pos.z), obstacles, bounds)
        if monster.pos.z == 0:
            _transition_state(monster, MonsterState.IDLE)
        return None

    _face_target(monster, target)
    dx_to = target.pos.x - monster.pos.x
    dy_to = target.pos.y - monster.pos.y
    swoop_dz = 2 if monster.flight_phase_ticks <= 12 else -2
    step_x = _sign(dx_to) * monster.params.move_speed_x
    step_y = _sign(dy_to) * min(monster.params.move_speed_y, abs(dy_to))
    _move_monster(monster, step_x, step_y, swoop_dz, obstacles, bounds)

    if monster.pos.z == 0 and monster.flight_phase_ticks > 12:
        monster.flight_phase_ticks = 0
        if monster.cooldown_ticks == 0 and prng.rand_range(100) < monster.params.ranged_chance_pct:
            _transition_state(monster, MonsterState.BREATH_ATTACK)
        else:
            _transition_state(monster, MonsterState.APPROACH)
    return None


def _step_guard_stance(
    monster: MonsterActor,
    target: TargetSnapshot | None,
    prng: SimPrng,
    _obstacles: Sequence[ObstacleSegment],
    _bounds: ArenaBounds,
    _spawned: list[ProjectileIntent],
) -> MeleeAttackIntent | None:
    if target is not None:
        _face_target(monster, target)
    if monster.cooldown_ticks == 0 and target is not None:
        _choose_combat_action(monster, target, prng)
    elif monster.state_ticks >= 20:
        _transition_state(monster, MonsterState.APPROACH)
    return None


def _step_stun_or_knockback(
    monster: MonsterActor,
    _target: TargetSnapshot | None,
    _prng: SimPrng,
    obstacles: Sequence[ObstacleSegment],
    bounds: ArenaBounds,
    _spawned: list[ProjectileIntent],
) -> MeleeAttackIntent | None:
    if monster.state == MonsterState.KNOCKBACK and monster.knockback_vx != 0:
        _move_monster(monster, monster.knockback_vx, 0, 0, obstacles, bounds)
    if monster.stun_ticks > 0:
        monster.stun_ticks -= 1
    if monster.stun_ticks <= 0:
        monster.knockback_vx = 0
        _transition_state(monster, MonsterState.IDLE)
    return None


def _step_dying(
    monster: MonsterActor,
    _target: TargetSnapshot | None,
    _prng: SimPrng,
    _obstacles: Sequence[ObstacleSegment],
    _bounds: ArenaBounds,
    _spawned: list[ProjectileIntent],
) -> MeleeAttackIntent | None:
    if monster.state_ticks >= 18:
        _transition_state(monster, MonsterState.DEAD)
    return None


def _step_dead(
    _monster: MonsterActor,
    _target: TargetSnapshot | None,
    _prng: SimPrng,
    _obstacles: Sequence[ObstacleSegment],
    _bounds: ArenaBounds,
    _spawned: list[ProjectileIntent],
) -> MeleeAttackIntent | None:
    return None


_StateHandler = Callable[
    [
        MonsterActor,
        TargetSnapshot | None,
        SimPrng,
        Sequence[ObstacleSegment],
        ArenaBounds,
        list[ProjectileIntent],
    ],
    MeleeAttackIntent | None,
]

_STATE_HANDLERS: dict[MonsterState, _StateHandler] = {
    MonsterState.IDLE: _step_idle,
    MonsterState.APPROACH: _step_approach,
    MonsterState.FLANK: _step_flank,
    MonsterState.RETREAT: _step_retreat,
    MonsterState.MELEE_ATTACK: _step_melee_attack,
    MonsterState.RANGED_ATTACK: _step_ranged_or_breath,
    MonsterState.BREATH_ATTACK: _step_ranged_or_breath,
    MonsterState.FLY_PATROL: _step_fly_patrol,
    MonsterState.GUARD_STANCE: _step_guard_stance,
    MonsterState.HIT_STUN: _step_stun_or_knockback,
    MonsterState.KNOCKBACK: _step_stun_or_knockback,
    MonsterState.DYING: _step_dying,
    MonsterState.DEAD: _step_dead,
}


def apply_hit_to_monster(
    monster: MonsterActor,
    raw_damage: int,
    attacker_x: int,
    *,
    knockback_override: int | None = None,
) -> MonsterDamageEvent:
    """Apply combat damage, hit-stun/knockback, or death transition to ``monster``."""
    if not monster.is_alive:
        return MonsterDamageEvent(
            monster_id=monster.entity_id,
            raw_damage=raw_damage,
            actual_damage=0,
            remaining_hp=monster.hp,
            killed=False,
            new_state=monster.state,
        )

    defense = monster.params.defense
    if monster.state == MonsterState.GUARD_STANCE:
        defense = defense * 2 + 2
    actual = max(1, raw_damage - defense) if raw_damage > 0 else 0
    monster.hp = max(0, monster.hp - actual)

    if monster.hp == 0:
        monster.stun_ticks = 0
        monster.knockback_vx = 0
        _transition_state(monster, MonsterState.DYING)
        return MonsterDamageEvent(
            monster_id=monster.entity_id,
            raw_damage=raw_damage,
            actual_damage=actual,
            remaining_hp=0,
            killed=True,
            new_state=monster.state,
        )

    kb = monster.params.knockback_x if knockback_override is None else knockback_override
    away = -1 if attacker_x >= monster.pos.x else 1
    monster.knockback_vx = away * kb
    monster.stun_ticks = monster.params.hit_stun_ticks
    next_state = MonsterState.KNOCKBACK if kb > 0 else MonsterState.HIT_STUN
    _transition_state(monster, next_state)
    return MonsterDamageEvent(
        monster_id=monster.entity_id,
        raw_damage=raw_damage,
        actual_damage=actual,
        remaining_hp=monster.hp,
        killed=False,
        new_state=monster.state,
    )


def _convert_spawn_events(
    monster: MonsterActor,
    events: Sequence[CombatEvent],
    out: list[ProjectileIntent],
) -> None:
    for event in events:
        if isinstance(event, SpawnEvent) and event.spawn_kind == "projectile":
            out.append(
                ProjectileIntent(
                    owner_id=monster.entity_id,
                    projectile_type_id=event.type_id,
                    pos=Pos25D(event.x, event.y, event.z),
                    vx=event.direction.sign * 3,
                    vy=0,
                    vz=0,
                    direction=event.direction,
                    damage=max(1, monster.params.base_damage - 2),
                    ttl_ticks=75,
                    is_hazard=False,
                )
            )


def step_monster_vbl(
    monster: MonsterActor,
    targets: Sequence[TargetSnapshot],
    prng: SimPrng,
    *,
    scripts: Mapping[int, bytes] | None = None,
    obstacles: Sequence[ObstacleSegment] = (),
    bounds: ArenaBounds = DEFAULT_ARENA_BOUNDS,
) -> MonsterTickResult:
    """Advance ``monster`` by one 50 Hz VBL tick, integrating AI and ``CombatJob``."""
    if monster.cooldown_ticks > 0:
        monster.cooldown_ticks -= 1
    monster.state_ticks += 1

    target = select_target(monster, targets, prng) if monster.is_alive else None
    spawned: list[ProjectileIntent] = []
    handler = _STATE_HANDLERS[monster.state]
    melee_intent = handler(monster, target, prng, obstacles, bounds, spawned)

    combat_events: tuple[CombatEvent, ...] = ()
    if scripts is not None and monster.job.active and monster.job.script_id in scripts:
        script = scripts[monster.job.script_id]
        combat_events = step_combat_job_vbl(
            monster.job,
            script,
            prng=prng,
            bounds=_to_vm_bounds(bounds),
        )
        _convert_spawn_events(monster, combat_events, spawned)

    return MonsterTickResult(
        monster_id=monster.entity_id,
        state=monster.state,
        combat_events=combat_events,
        spawned_projectiles=tuple(spawned),
        melee_intent=melee_intent,
    )


@dataclass(slots=True, frozen=True)
class MonsterSpawnSpec:
    """Single monster placement within a Lair or region encounter wave."""

    entity_id: EntityId
    params: MonsterParams
    pos: Pos25D
    direction: Direction = Direction.LEFT


@dataclass(slots=True, frozen=True)
class WaveSpec:
    """Ordered wave of monsters inside a Lair encounter."""

    wave_index: int
    spawns: tuple[MonsterSpawnSpec, ...]


@dataclass(slots=True, frozen=True)
class EncounterTickResult:
    """Aggregate 50 Hz VBL step output for a ``LairEncounter``."""

    tick: int
    wave_index: int
    wave_cleared: bool
    encounter_completed: bool
    monster_results: tuple[MonsterTickResult, ...]
    projectile_hits: tuple[ProjectileHitEvent, ...]


@dataclass(slots=True)
class LairEncounter:
    """Multi-wave Lair/region encounter coordinator stepped at 50 Hz."""

    waves: tuple[WaveSpec, ...]
    obstacles: tuple[ObstacleSegment, ...] = ()
    bounds: ArenaBounds = DEFAULT_ARENA_BOUNDS
    current_wave: int = 0
    tick_count: int = 0
    completed: bool = False
    monsters: list[MonsterActor] = field(default_factory=list)
    projectiles: list[ProjectileState] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.waves:
            self.completed = True
            return
        self._spawn_wave(self.current_wave)

    def _spawn_wave(self, wave_idx: int) -> None:
        self.monsters = [
            MonsterActor(
                entity_id=spec.entity_id,
                params=spec.params,
                pos=self.bounds.clamp(spec.pos),
                direction=spec.direction,
            )
            for spec in self.waves[wave_idx].spawns
        ]

    def _step_projectiles(self, targets: Sequence[TargetSnapshot]) -> list[ProjectileHitEvent]:
        hits: list[ProjectileHitEvent] = []
        remaining: list[ProjectileState] = []
        for proj in self.projectiles:
            if not proj.active or proj.ttl_ticks <= 0:
                continue
            proj.ttl_ticks -= 1
            proj.pos = proj.pos.translate(proj.vx, proj.vy, proj.vz)
            if not (self.bounds.min_x <= proj.pos.x <= self.bounds.max_x):
                continue
            hit_target = _find_projectile_hit(proj, targets)
            if hit_target is not None:
                hits.append(
                    ProjectileHitEvent(
                        owner_id=proj.owner_id,
                        target_id=hit_target.entity_id,
                        projectile_type_id=proj.projectile_type_id,
                        damage=proj.damage,
                        pos=proj.pos,
                    )
                )
                continue
            remaining.append(proj)
        self.projectiles = remaining
        return hits

    def step_vbl(
        self,
        targets: Sequence[TargetSnapshot],
        prng: SimPrng,
        *,
        scripts: Mapping[int, bytes] | None = None,
    ) -> EncounterTickResult:
        """Advance all active monsters, projectiles, and wave state by one 50 Hz tick."""
        self.tick_count += 1
        if self.completed:
            return EncounterTickResult(
                tick=self.tick_count,
                wave_index=self.current_wave,
                wave_cleared=True,
                encounter_completed=True,
                monster_results=(),
                projectile_hits=(),
            )

        results: list[MonsterTickResult] = []
        for monster in self.monsters:
            res = step_monster_vbl(
                monster,
                targets,
                prng,
                scripts=scripts,
                obstacles=self.obstacles,
                bounds=self.bounds,
            )
            results.append(res)
            for intent in res.spawned_projectiles:
                self.projectiles.append(
                    ProjectileState(
                        owner_id=intent.owner_id,
                        projectile_type_id=intent.projectile_type_id,
                        pos=intent.pos,
                        vx=intent.vx,
                        vy=intent.vy,
                        vz=intent.vz,
                        direction=intent.direction,
                        damage=intent.damage,
                        ttl_ticks=intent.ttl_ticks,
                        is_hazard=intent.is_hazard,
                    )
                )

        proj_hits = self._step_projectiles(targets)
        wave_cleared = all(m.is_dead for m in self.monsters)
        if wave_cleared:
            if self.current_wave + 1 < len(self.waves):
                self.current_wave += 1
                self._spawn_wave(self.current_wave)
            else:
                self.completed = True

        return EncounterTickResult(
            tick=self.tick_count,
            wave_index=self.current_wave,
            wave_cleared=wave_cleared,
            encounter_completed=self.completed,
            monster_results=tuple(results),
            projectile_hits=tuple(proj_hits),
        )


def _find_projectile_hit(
    proj: ProjectileState,
    targets: Sequence[TargetSnapshot],
) -> TargetSnapshot | None:
    for target in targets:
        if not target.alive or target.hp <= 0:
            continue
        if abs(proj.pos.x - target.pos.x) <= 10 and depth_close(proj.pos.y, target.pos.y):
            return target
    return None
