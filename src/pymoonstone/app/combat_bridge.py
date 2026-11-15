"""Bridge `mog` tables, `collide.hit`, cel hurtboxes, and `.t` arena bounds into `app/` (T9.4)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import numpy as np

from pymoonstone.assets.cel import CelFrame
from pymoonstone.sim.collision import (
    DEFAULT_ARENA_BOUNDS,
    DEFAULT_DEPTH_THRESHOLD,
    ArenaBounds,
    AttackEntry,
    AttackPattern,
    AttackPoint,
    CelMask,
    HurtEntry,
    ObstacleSegment as SimObstacleSegment,
    depth_close,
    resolve_arena_movement,
    resolve_combat_hits,
)
from pymoonstone.sim.monster_ai import (
    CreatureNodeParams,
    LairEncounter,
    MonsterArchetype,
    MonsterParams,
    MonsterSpawnSpec,
    WaveSpec,
    default_monster_params,
    params_from_creature_node,
)
from pymoonstone.sim.overworld import EncounterTrigger, TriggerKind
from pymoonstone.sim.types import Direction, EntityId, KnightId, Pos25D

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pymoonstone.assets.arena import DecodedArena
    from pymoonstone.assets.cel import CelFrameMeta
    from pymoonstone.assets.hit import HitFrame
    from pymoonstone.assets.tables import CreatureNodeRecord, MogRuntimeTables


class HurtboxFrame(Protocol):
    """Duck-typed cel frame carrying raw 1-bit `hurtbox_planes`."""

    @property
    def meta(self) -> CelFrameMeta: ...

    @property
    def hurtbox_planes(self) -> Sequence[bytes]: ...


_LAIR_BOSSES: tuple[MonsterArchetype, ...] = (
    MonsterArchetype.TROLL,
    MonsterArchetype.BALOK,
    MonsterArchetype.DEMON,
    MonsterArchetype.GUARDIAN,
)

_WANDERING_ARCHETYPES: tuple[MonsterArchetype, ...] = (
    MonsterArchetype.TROGG_SPEAR,
    MonsterArchetype.RATMAN,
    MonsterArchetype.MUDMAN,
    MonsterArchetype.BEASTMAN,
)

_LAIR_MINIONS: tuple[MonsterArchetype, ...] = (
    MonsterArchetype.TROGG_SPEAR,
    MonsterArchetype.TROGG_AXE,
    MonsterArchetype.RATMAN,
    MonsterArchetype.BEASTMAN,
)


def _archetype_for_trigger(trigger: EncounterTrigger) -> MonsterArchetype:
    if trigger.kind == TriggerKind.DRAGON_INTERCEPT:
        return MonsterArchetype.DRAGON
    if trigger.kind == TriggerKind.LAIR_BOSS:
        idx = max(0, min(len(_LAIR_BOSSES) - 1, trigger.lair_index))
        return _LAIR_BOSSES[idx]
    return _WANDERING_ARCHETYPES[trigger.encounter_kind % len(_WANDERING_ARCHETYPES)]


def _find_creature_node(
    trigger: EncounterTrigger,
    nodes: Sequence[CreatureNodeRecord],
) -> CreatureNodeRecord | None:
    if not nodes:
        return None
    if 0 <= trigger.lair_index < len(nodes):
        return nodes[trigger.lair_index]
    if 0 <= trigger.node_id < len(nodes):
        return nodes[trigger.node_id]
    if trigger.creature_type > 0:
        for node in nodes:
            if node.creature_type == trigger.creature_type:
                return node
    return None


def _resolve_node_archetype(
    trigger: EncounterTrigger,
    node: CreatureNodeRecord | None,
) -> MonsterArchetype:
    base = _archetype_for_trigger(trigger)
    if trigger.kind == TriggerKind.DRAGON_INTERCEPT or node is None:
        return base
    if 0 <= node.creature_type < len(MonsterArchetype):
        candidate = MonsterArchetype(node.creature_type)
        if trigger.kind != TriggerKind.LAIR_BOSS or candidate.value >= MonsterArchetype.TROLL.value:
            return candidate
    return base


def _build_monster_params(
    trigger: EncounterTrigger,
    mog_tables: MogRuntimeTables | None,
) -> MonsterParams:
    if mog_tables is None:
        return default_monster_params(_archetype_for_trigger(trigger))
    node = _find_creature_node(trigger, mog_tables.lair_region.creature_nodes)
    archetype = _resolve_node_archetype(trigger, node)
    if node is None:
        return default_monster_params(archetype)
    return params_from_creature_node(
        archetype,
        CreatureNodeParams(
            creature_type=node.creature_type,
            defence=node.defence,
            aux_value=node.aux_value,
        ),
    )


def _spawn_row(
    count: int,
    params: MonsterParams,
    *,
    start_entity_id: int,
) -> tuple[MonsterSpawnSpec, ...]:
    spawns: list[MonsterSpawnSpec] = []
    for idx in range(count):
        spawns.append(
            MonsterSpawnSpec(
                entity_id=EntityId(start_entity_id + idx),
                params=params,
                pos=Pos25D(x=240 + (idx % 3) * 18, y=90 + (idx % 2) * 18, z=0),
                direction=Direction.LEFT,
            )
        )
    return tuple(spawns)


def build_mog_encounter(
    trigger: EncounterTrigger,
    knight_ids: tuple[KnightId, ...],
    mog_tables: MogRuntimeTables | None = None,
    *,
    arena: DecodedArena | None = None,
) -> LairEncounter:
    """Build a multi-monster/wave `LairEncounter` from `MogRuntimeTables` or defaults."""
    player_count = max(1, len(knight_ids))
    boss_params = _build_monster_params(trigger, mog_tables)
    obstacles = (
        tuple(SimObstacleSegment(obs.x1, obs.x2, obs.y) for obs in arena.obstacles)
        if arena is not None
        else ()
    )

    waves: list[WaveSpec] = []
    if trigger.kind == TriggerKind.LAIR_BOSS and mog_tables is not None:
        minion_arch = _LAIR_MINIONS[max(0, trigger.lair_index) % len(_LAIR_MINIONS)]
        minion_params = default_monster_params(minion_arch)
        minion_count = min(3, player_count + 1)
        waves.append(
            WaveSpec(
                wave_index=0,
                spawns=_spawn_row(minion_count, minion_params, start_entity_id=100),
            )
        )
        waves.append(
            WaveSpec(
                wave_index=1,
                spawns=_spawn_row(1, boss_params, start_entity_id=110),
            )
        )
    elif trigger.kind == TriggerKind.WANDERING_MONSTER:
        count = min(3, player_count + (1 if mog_tables is not None and trigger.day >= 7 else 0))
        waves.append(
            WaveSpec(
                wave_index=0,
                spawns=_spawn_row(count, boss_params, start_entity_id=100),
            )
        )
    else:
        waves.append(
            WaveSpec(
                wave_index=0,
                spawns=_spawn_row(1, boss_params, start_entity_id=100),
            )
        )

    return LairEncounter(waves=tuple(waves), obstacles=obstacles)


def cel_mask_from_frame(frame: CelFrame | HurtboxFrame) -> CelMask:
    """Convert a `CelFrame` (hurtbox planes or indexed pixels) into `sim.collision.CelMask`."""
    w = frame.meta.width
    h = frame.meta.height
    if w <= 0 or h <= 0:
        return CelMask.empty()

    if not isinstance(frame, CelFrame):
        return CelMask.from_bitplanes(w, h, frame.hurtbox_planes)

    mask_arr = frame.mask if frame.mask.shape == (h, w) else (frame.pixels[:h, :w] != 0)
    packed = np.packbits(np.asarray(mask_arr, dtype=np.uint8), axis=1, bitorder="little")
    rows = tuple(int.from_bytes(packed[y].tobytes(), "little") for y in range(h))
    return CelMask(width=w, height=h, rows=rows)


def _resolve_defender_mask(
    defender_mask: CelMask | None,
    defender_frame: CelFrame | None,
) -> CelMask | None:
    mask = (
        defender_mask
        if defender_mask is not None
        else (cel_mask_from_frame(defender_frame) if defender_frame is not None else None)
    )
    if mask is None or mask.width <= 0 or mask.height <= 0:
        return None
    return mask


def _hit_frame_to_pattern(hit_frame: HitFrame) -> AttackPattern:
    return AttackPattern(
        hit_type=hit_frame.hit_type,
        max_x=hit_frame.max_x,
        max_y=hit_frame.max_y,
        points=tuple(AttackPoint(pt.x, pt.y) for pt in hit_frame.points),
    )


def resolve_melee_attack_points(
    *,
    attacker_id: EntityId,
    attacker_pos: Pos25D,
    attacker_screen_x: int,
    attacker_screen_y: int,
    attacker_cel_width: int,
    attacker_cel_height: int,
    attacker_flipped: bool,
    hit_frame: HitFrame | None,
    defender_id: EntityId,
    defender_pos: Pos25D,
    defender_screen_x: int,
    defender_screen_y: int,
    defender_frame: CelFrame | None,
    defender_flipped: bool = False,
    defender_mask: CelMask | None = None,
    depth_threshold: int = DEFAULT_DEPTH_THRESHOLD,
    fallback_reach_x: int = 36,
) -> bool:
    """Test `collide.hit` attack points against defender `CelMask`, with distance fallback."""
    if attacker_id == defender_id:
        return False
    if not depth_close(attacker_pos.y, defender_pos.y, depth_threshold):
        return False

    mask = _resolve_defender_mask(defender_mask, defender_frame)
    if hit_frame is not None and hit_frame.points and mask is not None:
        attack = AttackEntry(
            entity_id=attacker_id,
            pos=attacker_pos,
            screen_x=attacker_screen_x,
            screen_y=attacker_screen_y,
            cel_width=attacker_cel_width,
            cel_height=attacker_cel_height,
            pattern=_hit_frame_to_pattern(hit_frame),
            flipped=attacker_flipped,
        )
        hurt = HurtEntry(
            entity_id=defender_id,
            pos=defender_pos,
            screen_x=defender_screen_x,
            screen_y=defender_screen_y,
            mask=mask,
            flipped=defender_flipped,
        )
        return bool(resolve_combat_hits((attack,), (hurt,), depth_threshold=depth_threshold))

    dx = defender_pos.x - attacker_pos.x
    if attacker_flipped:
        return -fallback_reach_x <= dx <= 8
    return -8 <= dx <= fallback_reach_x


def clamp_entity_to_arena(
    pos: Pos25D,
    arena: DecodedArena | None,
    *,
    dx: int = 0,
    dy: int = 0,
    bounds: ArenaBounds = DEFAULT_ARENA_BOUNDS,
) -> Pos25D:
    """Clamp combat entity movement using `.t` arena obstacle bounds."""
    if arena is None or not arena.obstacles:
        return resolve_arena_movement(pos, dx, dy, (), bounds=bounds)
    sim_obstacles = tuple(SimObstacleSegment(obs.x1, obs.x2, obs.y) for obs in arena.obstacles)
    return resolve_arena_movement(pos, dx, dy, sim_obstacles, bounds=bounds)
