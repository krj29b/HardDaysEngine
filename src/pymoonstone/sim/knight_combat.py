"""Pure-integer 50 Hz Knight combat controller & equipment state (ARCHITECTURE.md §7.3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING

from pymoonstone.sim.collision import (
    DEFAULT_ARENA_BOUNDS,
    DEFAULT_DEPTH_THRESHOLD,
    ArenaBounds,
    HitResult,
    ObstacleSegment,
    positions_depth_close,
    resolve_arena_movement,
)
from pymoonstone.sim.combat_vm import (
    ArenaBounds as VmArenaBounds,
    CombatEvent,
    CombatJob,
    step_vbl,
)
from pymoonstone.sim.types import Direction, EntityId, KnightId, Pos25D, Rect2D

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence, Set as AbstractSet

    from pymoonstone.sim.prng import SimPrng

_DEFAULT_ENTITY_ID: EntityId = EntityId(1)

# Joystick bitmask flags matching Knight.uwInput (ARCHITECTURE.md §7.3).
INPUT_RIGHT: int = 0x01
INPUT_LEFT: int = 0x02
INPUT_DOWN: int = 0x04
INPUT_UP: int = 0x08
INPUT_FIRE: int = 0x10

# Movement speeds in integer pixels per 50 Hz VBL tick.
WALK_SPEED_X: int = 2
WALK_SPEED_Y: int = 1
RUN_SPEED_X: int = 3
RUN_SPEED_Y: int = 2
ROLL_SPEED_X: int = 3

# Action durations in 50 Hz VBL ticks.
OVERHEAD_SLASH_VBLS: int = 18
THRUST_VBLS: int = 15
LOW_SWEEP_VBLS: int = 18
ROLL_VBLS: int = 18
THROW_VBLS: int = 14
HIT_STUN_VBLS: int = 15
HIT_STUN_INVULN_VBLS: int = 20
KNOCKDOWN_VBLS: int = 36
POST_KNOCKDOWN_INVULN_VBLS: int = 25
DEATH_VBLS: int = 50

# Stamina costs and regeneration (integer units).
RUN_STAMINA_COST: int = 1
ROLL_STAMINA_COST: int = 15
ATTACK_STAMINA_COST: int = 8
BLOCK_STAMINA_COST: int = 5
STAMINA_REGEN_INTERVAL_VBLS: int = 4
STAMINA_REGEN_AMOUNT: int = 1

# Block mitigation base value.
BLOCK_BASE_MITIGATION: int = 6

# Base damage per sword tier (1..4) and move multiplier.
BASE_SWORD_DAMAGE: tuple[int, ...] = (6, 10, 14, 18, 24)


class KnightAction(IntEnum):
    """Combat action states for a Knight (ARCHITECTURE.md §7.3)."""

    IDLE = 0
    WALK = 1
    RUN = 2
    ROLL = 3
    BLOCK = 4
    OVERHEAD_SLASH = 5
    THRUST = 6
    LOW_SWEEP = 7
    THROW_SECONDARY = 8
    HIT_STUN = 9
    KNOCKDOWN = 10
    DEATH = 11


class SecondaryWeapon(IntEnum):
    """Selectable secondary combat equipment."""

    THROWING_KNIFE = 0
    FIREBALL_SCROLL = 1


_TIMED_ACTION_VBLS: dict[KnightAction, int] = {
    KnightAction.OVERHEAD_SLASH: OVERHEAD_SLASH_VBLS,
    KnightAction.THRUST: THRUST_VBLS,
    KnightAction.LOW_SWEEP: LOW_SWEEP_VBLS,
    KnightAction.ROLL: ROLL_VBLS,
    KnightAction.THROW_SECONDARY: THROW_VBLS,
}

_ATTACK_ACTIONS: frozenset[KnightAction] = frozenset(
    {
        KnightAction.OVERHEAD_SLASH,
        KnightAction.THRUST,
        KnightAction.LOW_SWEEP,
    }
)


@dataclass(slots=True, frozen=True)
class KnightInput:
    """8-direction + fire-button input sample for one 50 Hz VBL tick."""

    dx: int = 0
    dy: int = 0
    fire: bool = False
    secondary: bool = False
    weapon_switch: bool = False
    run: bool = False

    def normalized(self) -> KnightInput:
        """Clamp ``dx`` and ``dy`` to ``-1..1``."""
        ndx = -1 if self.dx < 0 else (1 if self.dx > 0 else 0)
        ndy = -1 if self.dy < 0 else (1 if self.dy > 0 else 0)
        return KnightInput(
            dx=ndx,
            dy=ndy,
            fire=self.fire,
            secondary=self.secondary,
            weapon_switch=self.weapon_switch,
            run=self.run,
        )

    def to_bitmask(self) -> int:
        """Encode ``dx``, ``dy``, and ``fire`` into the 68k ``uwInput`` bitmask (§7.3)."""
        mask = 0
        if self.dx > 0:
            mask |= INPUT_RIGHT
        elif self.dx < 0:
            mask |= INPUT_LEFT
        if self.dy > 0:
            mask |= INPUT_DOWN
        elif self.dy < 0:
            mask |= INPUT_UP
        if self.fire:
            mask |= INPUT_FIRE
        return mask

    @classmethod
    def from_bitmask(
        cls,
        mask: int,
        *,
        secondary: bool = False,
        weapon_switch: bool = False,
        run: bool = False,
    ) -> KnightInput:
        """Decode a 68k ``uwInput`` joystick bitmask into a :class:`KnightInput`."""
        dx = (1 if mask & INPUT_RIGHT else 0) - (1 if mask & INPUT_LEFT else 0)
        dy = (1 if mask & INPUT_DOWN else 0) - (1 if mask & INPUT_UP else 0)
        return cls(
            dx=dx,
            dy=dy,
            fire=bool(mask & INPUT_FIRE),
            secondary=secondary,
            weapon_switch=weapon_switch,
            run=run,
        )


@dataclass(slots=True, frozen=True)
class DamageOutcome:
    """Result of resolving a single incoming hit against a Knight."""

    knight_id: KnightId
    damage_taken: int
    blocked: bool
    evaded: bool
    stunned: bool
    knocked_down: bool
    killed: bool
    remaining_hp: int


@dataclass(slots=True, frozen=True)
class KnightStepResult:
    """Deterministic output of one 50 Hz Knight VBL step."""

    action: KnightAction
    pos: Pos25D
    direction: Direction
    threw_secondary: bool
    secondary_weapon: SecondaryWeapon
    remaining_ammo: int
    vm_events: tuple[CombatEvent, ...]


@dataclass(slots=True)
class KnightState:
    """132-byte ``Knight`` combat and RPG state record (ARCHITECTURE.md §7.3)."""

    knight_id: KnightId = KnightId.RED
    entity_id: EntityId = _DEFAULT_ENTITY_ID
    active: bool = True
    x: int = 160
    y: int = 100
    z: int = 0
    direction: Direction = Direction.RIGHT
    input_port: int = 0
    hp: int = 100
    max_hp: int = 100
    stamina: int = 100
    max_stamina: int = 100
    armour: int = 0
    shield: int = 0
    sword_tier: int = 1
    secondary_weapon: SecondaryWeapon = SecondaryWeapon.THROWING_KNIFE
    daggers: int = 5
    strength: int = 1
    constitution: int = 1
    endurance: int = 1
    lives: int = 3
    gold: int = 0
    xp: int = 0
    keys: int = 0
    moonstones: int = 0
    action: KnightAction = KnightAction.IDLE
    action_timer: int = 0
    invuln_timer: int = 0
    stun_timer: int = 0
    knockdown_timer: int = 0
    regen_tick: int = 0
    depth_reach: int = DEFAULT_DEPTH_THRESHOLD
    body_box: Rect2D | None = None
    hit_target: EntityId | None = None
    attacker: EntityId | None = None
    hit_x: int = 0
    hit_y: int = 0
    actor_type: int = 0
    last_input_mask: int = 0
    script_ids: dict[KnightAction, int] = field(default_factory=dict)

    @property
    def pos(self) -> Pos25D:
        """Current 2.5D integer position."""
        return Pos25D(self.x, self.y, self.z)

    @property
    def is_alive(self) -> bool:
        """True when active, HP > 0, and not in ``DEATH`` state."""
        return self.active and self.hp > 0 and self.action != KnightAction.DEATH

    @property
    def is_invulnerable(self) -> bool:
        """True during invulnerability frames or evasive roll."""
        return self.invuln_timer > 0 or self.action == KnightAction.ROLL

    @property
    def is_incapacitated(self) -> bool:
        """True during hit-stun, knockdown, or death."""
        return self.action in (
            KnightAction.HIT_STUN,
            KnightAction.KNOCKDOWN,
            KnightAction.DEATH,
        )

    def attack_damage(self) -> int:
        """Compute integer melee damage from ``sword_tier``, ``strength``, and action."""
        tier = max(0, min(len(BASE_SWORD_DAMAGE) - 1, self.sword_tier))
        base = BASE_SWORD_DAMAGE[tier] + max(0, self.strength - 1) * 2
        if self.action == KnightAction.OVERHEAD_SLASH:
            return base + 4
        if self.action == KnightAction.LOW_SWEEP:
            return base + 1
        return base

    def cycle_secondary_weapon(self) -> SecondaryWeapon:
        """Switch to the next secondary weapon and return it."""
        if self.secondary_weapon == SecondaryWeapon.THROWING_KNIFE:
            self.secondary_weapon = SecondaryWeapon.FIREBALL_SCROLL
        else:
            self.secondary_weapon = SecondaryWeapon.THROWING_KNIFE
        return self.secondary_weapon


KnightCombatRecord = KnightState


def decode_knight_action(knight: KnightState, raw_input: KnightInput) -> KnightAction:
    """Decode 8-direction + fire-button input into a :class:`KnightAction`."""
    inp = raw_input.normalized()
    if inp.secondary and knight.daggers > 0:
        return KnightAction.THROW_SECONDARY
    if inp.fire:
        return _decode_fire_action(knight, inp)
    if inp.dx == 0 and inp.dy == 0:
        return KnightAction.IDLE
    if inp.run and knight.stamina >= RUN_STAMINA_COST:
        return KnightAction.RUN
    return KnightAction.WALK


def _decode_fire_action(knight: KnightState, inp: KnightInput) -> KnightAction:
    """Decode directional + fire button combinations."""
    if inp.dy < 0:
        return KnightAction.OVERHEAD_SLASH
    if inp.dy > 0:
        return KnightAction.ROLL if inp.dx != 0 else KnightAction.LOW_SWEEP
    if inp.dx == -knight.direction.sign:
        return KnightAction.BLOCK
    return KnightAction.THRUST


def _tick_status_timers(knight: KnightState) -> bool:
    """Advance invulnerability, knockdown, stun, and action lock timers.

    Returns ``True`` if the knight is locked and cannot accept new input this tick.
    """
    if knight.invuln_timer > 0:
        knight.invuln_timer -= 1
    if knight.action == KnightAction.DEATH:
        if knight.action_timer > 0:
            knight.action_timer -= 1
        return True
    if knight.knockdown_timer > 0:
        knight.knockdown_timer -= 1
        if knight.knockdown_timer == 0:
            knight.action = KnightAction.IDLE
            knight.invuln_timer = max(knight.invuln_timer, POST_KNOCKDOWN_INVULN_VBLS)
        return True
    if knight.stun_timer > 0:
        knight.stun_timer -= 1
        if knight.stun_timer == 0:
            knight.action = KnightAction.IDLE
        return True
    if knight.action_timer > 0:
        knight.action_timer -= 1
        if knight.action_timer == 0:
            knight.action = KnightAction.IDLE
        return True
    return False


def _apply_action_costs(knight: KnightState, action: KnightAction) -> bool:
    """Start a timed action, deduct stamina/ammo, and return True if secondary thrown."""
    if action == KnightAction.RUN:
        knight.stamina = max(0, knight.stamina - RUN_STAMINA_COST)
        return False
    if action == KnightAction.ROLL:
        if knight.stamina < ROLL_STAMINA_COST:
            knight.action = KnightAction.WALK
            return False
        knight.stamina -= ROLL_STAMINA_COST
        knight.action_timer = ROLL_VBLS
        return False
    if action in _ATTACK_ACTIONS:
        knight.stamina = max(0, knight.stamina - ATTACK_STAMINA_COST)
        knight.action_timer = _TIMED_ACTION_VBLS[action]
        return False
    if action == KnightAction.THROW_SECONDARY and knight.daggers > 0:
        knight.daggers -= 1
        knight.action_timer = THROW_VBLS
        return True
    return False


def _compute_step_delta(knight: KnightState, inp: KnightInput) -> tuple[int, int]:
    """Compute integer ``(dx, dy)`` movement for the current action."""
    if knight.action == KnightAction.WALK:
        return inp.dx * WALK_SPEED_X, inp.dy * WALK_SPEED_Y
    if knight.action == KnightAction.RUN:
        return inp.dx * RUN_SPEED_X, inp.dy * RUN_SPEED_Y
    if knight.action == KnightAction.ROLL:
        move_dir = inp.dx if inp.dx != 0 else knight.direction.sign
        return move_dir * ROLL_SPEED_X, 0
    return 0, 0


def _regen_stamina(knight: KnightState) -> None:
    """Regenerate stamina slowly while idle or walking."""
    if knight.action not in (KnightAction.IDLE, KnightAction.WALK):
        return
    if knight.stamina >= knight.max_stamina:
        knight.regen_tick = 0
        return
    knight.regen_tick += 1
    regen_interval = max(1, STAMINA_REGEN_INTERVAL_VBLS - min(2, knight.endurance - 1))
    if knight.regen_tick >= regen_interval:
        knight.regen_tick = 0
        knight.stamina = min(knight.max_stamina, knight.stamina + STAMINA_REGEN_AMOUNT)


def _sync_and_step_vm(
    knight: KnightState,
    job: CombatJob | None,
    scripts: Mapping[int, bytes] | Sequence[bytes] | None,
    *,
    prng: SimPrng | None,
    bounds: ArenaBounds,
) -> tuple[CombatEvent, ...]:
    """Synchronize knight state into ``CombatJob`` and advance one 50 Hz VBL."""
    if job is None:
        return ()
    job.x = knight.x
    job.y = knight.y
    job.z = knight.z
    job.direction = knight.direction
    job.owner_id = knight.entity_id
    desired_script = knight.script_ids.get(knight.action)
    if desired_script is not None and job.script_id != desired_script:
        job.script_id = desired_script
        job.pc = 0
        job.running = True
    if scripts is None:
        return ()
    script = scripts[job.script_id]
    vm_bounds = VmArenaBounds(bounds.min_x, bounds.max_x, bounds.min_y, bounds.max_y)
    events = step_vbl(job, script, prng=prng, bounds=vm_bounds)
    knight.x = job.x
    knight.y = job.y
    knight.z = job.z
    knight.direction = job.direction
    return events


def step_knight_vbl(
    knight: KnightState,
    raw_input: KnightInput,
    *,
    obstacles: Sequence[ObstacleSegment] = (),
    bounds: ArenaBounds = DEFAULT_ARENA_BOUNDS,
    job: CombatJob | None = None,
    scripts: Mapping[int, bytes] | Sequence[bytes] | None = None,
    prng: SimPrng | None = None,
) -> KnightStepResult:
    """Advance ``knight`` by one 50 Hz PAL VBL tick (ARCHITECTURE.md §7.1, §7.3)."""
    inp = raw_input.normalized()
    knight.last_input_mask = inp.to_bitmask()

    if not knight.active:
        return KnightStepResult(
            action=knight.action,
            pos=knight.pos,
            direction=knight.direction,
            threw_secondary=False,
            secondary_weapon=knight.secondary_weapon,
            remaining_ammo=knight.daggers,
            vm_events=(),
        )

    threw_secondary = False
    locked = _tick_status_timers(knight)
    if not locked:
        if inp.weapon_switch:
            knight.cycle_secondary_weapon()
        new_action = decode_knight_action(knight, inp)
        if inp.dx != 0 and new_action != KnightAction.BLOCK:
            knight.direction = Direction.LEFT if inp.dx < 0 else Direction.RIGHT
        knight.action = new_action
        threw_secondary = _apply_action_costs(knight, new_action)
        _regen_stamina(knight)

    move_dx, move_dy = _compute_step_delta(knight, inp)
    if move_dx != 0 or move_dy != 0:
        resolved = resolve_arena_movement(knight.pos, move_dx, move_dy, obstacles, bounds=bounds)
        knight.x = resolved.x
        knight.y = resolved.y
        knight.z = resolved.z

    vm_events = _sync_and_step_vm(knight, job, scripts, prng=prng, bounds=bounds)
    return KnightStepResult(
        action=knight.action,
        pos=knight.pos,
        direction=knight.direction,
        threw_secondary=threw_secondary,
        secondary_weapon=knight.secondary_weapon,
        remaining_ammo=knight.daggers,
        vm_events=vm_events,
    )


def apply_knight_damage(
    knight: KnightState,
    damage: int,
    *,
    attacker_id: EntityId | None = None,
    attacker_pos: Pos25D | None = None,
    knockdown: bool = False,
    hit_x: int = 0,
    hit_y: int = 0,
) -> DamageOutcome:
    """Resolve an incoming hit against ``knight`` with block, armour, and i-frames."""
    if not knight.is_alive:
        return DamageOutcome(knight.knight_id, 0, False, True, False, False, False, knight.hp)
    if knight.is_invulnerable:
        return DamageOutcome(knight.knight_id, 0, False, True, False, False, False, knight.hp)
    if attacker_pos is not None and not positions_depth_close(
        knight.pos, attacker_pos, knight.depth_reach
    ):
        return DamageOutcome(knight.knight_id, 0, False, True, False, False, False, knight.hp)

    if knight.action == KnightAction.BLOCK:
        return _resolve_blocked_hit(knight, damage)

    actual = max(1, damage - knight.armour) if damage > 0 else 0
    knight.hp = max(0, knight.hp - actual)
    knight.attacker = attacker_id
    knight.hit_x = hit_x
    knight.hit_y = hit_y

    if knight.hp == 0:
        knight.action = KnightAction.DEATH
        knight.action_timer = DEATH_VBLS
        knight.stun_timer = 0
        knight.knockdown_timer = 0
        knight.lives = max(0, knight.lives - 1)
        return DamageOutcome(knight.knight_id, actual, False, False, False, False, True, 0)

    if knockdown:
        knight.action = KnightAction.KNOCKDOWN
        knight.knockdown_timer = KNOCKDOWN_VBLS
        knight.action_timer = 0
        knight.stun_timer = 0
        knight.invuln_timer = KNOCKDOWN_VBLS + POST_KNOCKDOWN_INVULN_VBLS
        return DamageOutcome(knight.knight_id, actual, False, False, False, True, False, knight.hp)

    knight.action = KnightAction.HIT_STUN
    knight.stun_timer = HIT_STUN_VBLS
    knight.action_timer = 0
    knight.invuln_timer = HIT_STUN_INVULN_VBLS
    return DamageOutcome(knight.knight_id, actual, False, False, True, False, False, knight.hp)


def _resolve_blocked_hit(knight: KnightState, damage: int) -> DamageOutcome:
    """Mitigate damage when the knight is actively blocking."""
    mitigation = BLOCK_BASE_MITIGATION + knight.shield * 2 + knight.armour
    chip_damage = max(0, damage - mitigation)
    knight.stamina = max(0, knight.stamina - BLOCK_STAMINA_COST)
    knight.hp = max(0, knight.hp - chip_damage)
    if knight.hp == 0:
        knight.action = KnightAction.DEATH
        knight.action_timer = DEATH_VBLS
        knight.lives = max(0, knight.lives - 1)
        return DamageOutcome(knight.knight_id, chip_damage, True, False, False, False, True, 0)
    return DamageOutcome(knight.knight_id, chip_damage, True, False, False, False, False, knight.hp)


def resolve_knight_hits(
    knight: KnightState,
    hits: Sequence[HitResult],
    *,
    damage_by_hit_type: Mapping[int, int] | None = None,
    default_damage: int = 10,
    knockdown_hit_types: AbstractSet[int] = frozenset({2}),
) -> list[DamageOutcome]:
    """Apply collision :class:`HitResult` records targeting ``knight`` in deterministic order."""
    outcomes: list[DamageOutcome] = []
    for hit in hits:
        if hit.defender_id != knight.entity_id:
            continue
        dmg = (
            damage_by_hit_type.get(hit.hit_type, default_damage)
            if damage_by_hit_type is not None
            else default_damage
        )
        outcome = apply_knight_damage(
            knight,
            dmg,
            attacker_id=hit.attacker_id,
            knockdown=hit.hit_type in knockdown_hit_types,
            hit_x=hit.hit_x,
            hit_y=hit.hit_y,
        )
        outcomes.append(outcome)
    return outcomes
