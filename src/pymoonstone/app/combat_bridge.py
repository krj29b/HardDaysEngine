"""Bridge `mog` tables, `collide.hit`, cel hurtboxes, and `.t` arena bounds into `app/` (T9.4)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import numpy as np

from pymoonstone.assets.cel import CelFrame, DecodedCel
from pymoonstone.render.scene import SceneSprite
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
from pymoonstone.sim.combat_vm import (
    VBL_DIVISOR_FAST,
    VBL_DIVISOR_NORMAL,
    CombatJob,
    DrawFrameEvent,
    ScriptError,
    step_frame,
)
from pymoonstone.sim.knight_combat import KnightAction
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
    from pymoonstone.assets.hit import HitFrame, HitSet
    from pymoonstone.assets.tables import (
        CombatActionTables,
        CreatureNodeRecord,
        KnightScriptTables,
        MogRuntimeTables,
        MonsterScriptTables,
    )


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

# Byte offsets into mog's LAB_08C8 creature arena handler table (ARCHITECTURE.md §5).
_MOG_CREATURE_OFFSET_ARCHETYPES: dict[int, MonsterArchetype] = {
    0: MonsterArchetype.BEASTMAN,
    4: MonsterArchetype.MUDMAN,
    8: MonsterArchetype.DEMON,
    20: MonsterArchetype.DRAGON,
    24: MonsterArchetype.TROGG_AXE,
    28: MonsterArchetype.TROGG_AXE,
    32: MonsterArchetype.TROGG_SPEAR,
    36: MonsterArchetype.RATMAN,
    48: MonsterArchetype.BALOK,
    64: MonsterArchetype.TROLL,
}


def _archetype_for_trigger(trigger: EncounterTrigger) -> MonsterArchetype:
    if trigger.kind == TriggerKind.DRAGON_INTERCEPT:
        return MonsterArchetype.DRAGON
    if trigger.kind == TriggerKind.LAIR_BOSS:
        idx = max(0, min(len(_LAIR_BOSSES) - 1, trigger.lair_index))
        return _LAIR_BOSSES[idx]
    return _WANDERING_ARCHETYPES[trigger.encounter_kind % len(_WANDERING_ARCHETYPES)]


def _is_mog_offset_table(nodes: Sequence[CreatureNodeRecord]) -> bool:
    return len(nodes) == 24 and any(n.creature_type >= len(MonsterArchetype) for n in nodes)


def _node_search_slice(
    trigger: EncounterTrigger,
    nodes: Sequence[CreatureNodeRecord],
    *,
    mog_table: bool,
) -> Sequence[CreatureNodeRecord]:
    if mog_table and 0 <= trigger.lair_index < 4:
        start = trigger.lair_index * 6
        return nodes[start : start + 6]
    return nodes


def _match_node_by_type(
    creature_type: int,
    nodes: Sequence[CreatureNodeRecord],
) -> CreatureNodeRecord | None:
    if creature_type <= 0:
        return None
    for node in nodes:
        if node.creature_type == creature_type:
            return node
    return None


def _lair_node_at(
    lair_index: int,
    nodes: Sequence[CreatureNodeRecord],
    *,
    mog_table: bool,
) -> CreatureNodeRecord | None:
    if lair_index < 0:
        return None
    idx = lair_index * 6 if (mog_table and lair_index < 4) else lair_index
    return nodes[idx] if idx < len(nodes) else None


def _find_creature_node(
    trigger: EncounterTrigger,
    nodes: Sequence[CreatureNodeRecord],
) -> CreatureNodeRecord | None:
    if not nodes:
        return None
    mog_table = _is_mog_offset_table(nodes)
    if mog_table and trigger.lair_index < 0 and trigger.creature_type == 0:
        return None
    matched = _match_node_by_type(
        trigger.creature_type,
        _node_search_slice(trigger, nodes, mog_table=mog_table),
    )
    if matched is not None:
        return matched
    by_lair = _lair_node_at(trigger.lair_index, nodes, mog_table=mog_table)
    if by_lair is not None:
        return by_lair
    return (
        nodes[trigger.node_id] if not mog_table and trigger.node_id in range(len(nodes)) else None
    )


def _resolve_node_archetype(
    trigger: EncounterTrigger,
    node: CreatureNodeRecord | None,
    *,
    mog_table: bool = False,
) -> MonsterArchetype:
    base = _archetype_for_trigger(trigger)
    if trigger.kind == TriggerKind.DRAGON_INTERCEPT or node is None:
        return base
    if mog_table and node.creature_type in _MOG_CREATURE_OFFSET_ARCHETYPES:
        candidate = _MOG_CREATURE_OFFSET_ARCHETYPES[node.creature_type]
        if trigger.kind != TriggerKind.LAIR_BOSS or candidate.value >= MonsterArchetype.TROLL.value:
            return candidate
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
    nodes = mog_tables.lair_region.creature_nodes
    mog_table = _is_mog_offset_table(nodes)
    node = _find_creature_node(trigger, nodes)
    archetype = _resolve_node_archetype(trigger, node, mog_table=mog_table)
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


KNIGHT_HIT_FRAME_LIMIT = 26
_MIN_BODY_CEL_WIDTH = 20
_MIN_BODY_CEL_HEIGHT = 14

_KNIGHT_ACTION_TABLE_SLOT: dict[KnightAction, int] = {
    KnightAction.IDLE: 0,
    KnightAction.WALK: 1,
    KnightAction.RUN: 2,
    KnightAction.ROLL: 3,
    KnightAction.BLOCK: 4,
    KnightAction.OVERHEAD_SLASH: 5,
    KnightAction.THRUST: 6,
    KnightAction.LOW_SWEEP: 7,
    KnightAction.THROW_SECONDARY: 8,
    KnightAction.HIT_STUN: 9,
    KnightAction.KNOCKDOWN: 10,
    KnightAction.DEATH: 10,
}

_KNIGHT_ATTACK_ACTIONS: frozenset[KnightAction] = frozenset(
    {
        KnightAction.OVERHEAD_SLASH,
        KnightAction.THRUST,
        KnightAction.LOW_SWEEP,
    }
)


@dataclass(slots=True, frozen=True)
class CombatSpritePiece:
    """Single resolved cel piece for multi-part knight/monster rendering & hitboxes."""

    frame: CelFrame
    frame_index: int
    cel_slot: int = 0
    dx: int = 0
    dy: int = 0
    flipped: bool = False
    hurtbox: bool = True
    attack: bool = False
    exclude_body_box: bool = False
    top_left: bool = False


def cel_screen_origin(
    pos: Pos25D,
    frame: CelFrame,
    *,
    dx: int = 0,
    dy: int = 0,
    flipped: bool = False,
    top_left: bool = False,
) -> tuple[int, int]:
    """Compute top-left playfield `(screen_x, screen_y)` for a cel piece."""
    w = frame.meta.width
    if top_left:
        return (pos.x + dx - (w if flipped else 0), pos.y - pos.z + dy)
    h = frame.meta.height
    hot_x = frame.meta.hot_x
    x_offset = hot_x if flipped else -hot_x
    return (pos.x + dx - (w // 2) + x_offset, pos.y - pos.z + dy - (h // 2))


def pieces_to_scene_sprites(
    pieces: Sequence[CombatSpritePiece],
    *,
    pos: Pos25D,
    layer: int = 2,
    order: int = 0,
) -> list[SceneSprite]:
    """Convert `CombatSpritePiece`s into `SceneSprite`s using `top_left` anchoring."""
    sprites: list[SceneSprite] = []
    for idx, piece in enumerate(pieces):
        if piece.top_left:
            sx = pos.x + piece.dx - (piece.frame.meta.width if piece.flipped else 0)
            sprites.append(
                SceneSprite(
                    frame=piece.frame,
                    x=sx,
                    y=pos.y,
                    z=pos.z,
                    dx=0,
                    dy=piece.dy,
                    layer=layer,
                    order=order + idx,
                    mirrored=piece.flipped,
                    top_left=True,
                )
            )
        else:
            sprites.append(
                SceneSprite(
                    frame=piece.frame,
                    x=pos.x,
                    y=pos.y,
                    z=pos.z,
                    dx=piece.dx,
                    dy=piece.dy,
                    layer=layer,
                    order=order + idx,
                    mirrored=piece.flipped,
                    top_left=False,
                )
            )
    return sprites


def _knight_body_indices(cel: DecodedCel) -> list[int]:
    limit = min(len(cel.frames), KNIGHT_HIT_FRAME_LIMIT)
    body = [
        idx
        for idx in range(limit)
        if cel.frames[idx].meta.width >= _MIN_BODY_CEL_WIDTH
        and cel.frames[idx].meta.height >= _MIN_BODY_CEL_HEIGHT
    ]
    return body or list(range(limit))


def _attack_hit_indices(body_indices: list[int], hit_set: HitSet | None) -> list[int]:
    if hit_set is None:
        return []
    return [idx for idx in body_indices if idx < len(hit_set.frames) and hit_set.frames[idx].points]


def select_knight_body_frame(
    cel: DecodedCel,
    action: KnightAction,
    tick_count: int,
    *,
    hit_set: HitSet | None = None,
) -> int:
    """Pick a body-sized knight cel frame index within `collide.hit` bounds (`0..25`)."""
    if not cel.frames:
        return 0
    body_indices = _knight_body_indices(cel)
    if action in _KNIGHT_ATTACK_ACTIONS:
        attack_indices = _attack_hit_indices(body_indices, hit_set)
        if attack_indices:
            return attack_indices[(tick_count // VBL_DIVISOR_NORMAL) % len(attack_indices)]
    slot = _KNIGHT_ACTION_TABLE_SLOT.get(action, 0)
    anim = (
        (tick_count // VBL_DIVISOR_NORMAL) if action in (KnightAction.WALK, KnightAction.RUN) else 0
    )
    return body_indices[(slot + anim) % len(body_indices)]


def _run_mog_action_draws(
    mog_s4: bytes,
    offset: int,
    step_count: int,
    flipped: bool,
) -> tuple[DrawFrameEvent, ...]:
    if offset <= 0 or offset >= len(mog_s4):
        return ()
    job = CombatJob(
        job_id=0,
        pc=offset,
        direction=Direction.LEFT if flipped else Direction.RIGHT,
        frame_divisor=1,
    )
    draws: tuple[DrawFrameEvent, ...] = ()
    for _ in range(step_count + 1):
        if not job.running:
            job.pc = offset
            job.running = True
        try:
            events = step_frame(job, mog_s4)
        except ScriptError:
            return ()
        frame_draws = tuple(ev for ev in events if isinstance(ev, DrawFrameEvent))
        if frame_draws:
            draws = frame_draws
    return draws


_KNIGHT_ACTION_TO_SCRIPT_INDEX: dict[KnightAction, int] = {
    KnightAction.IDLE: 0,
    KnightAction.ROLL: 6,
    KnightAction.BLOCK: 4,
    KnightAction.OVERHEAD_SLASH: 8,
    KnightAction.THRUST: 1,
    KnightAction.LOW_SWEEP: 2,
    KnightAction.THROW_SECONDARY: 5,
}

_KNIGHT_HURT_TO_SCRIPT_INDEX: dict[KnightAction, int] = {
    KnightAction.HIT_STUN: 4,
    KnightAction.KNOCKDOWN: 3,
    KnightAction.DEATH: 1,
}


def _script_at(scripts: Sequence[int], index: int, fallback: int) -> int:
    offset = scripts[index] if 0 <= index < len(scripts) else 0
    return offset or fallback


def _resolve_knight_walk_offset(
    action: KnightAction,
    tick_count: int,
    walk_scripts: Sequence[Sequence[int]],
) -> int | None:
    if action not in (KnightAction.WALK, KnightAction.RUN) or not walk_scripts:
        return None
    walk_block = walk_scripts[0]
    if not walk_block:
        return None
    divisor = VBL_DIVISOR_FAST if action == KnightAction.RUN else VBL_DIVISOR_NORMAL
    phase = (tick_count // divisor) % len(walk_block)
    return walk_block[phase]


def _resolve_knight_script_offset(
    action: KnightAction,
    tick_count: int,
    knight_scripts: KnightScriptTables,
) -> tuple[int, int]:
    """Return `(s4_offset, step_count)` for `action` from decoded `KnightScriptTables`."""
    walk_offset = _resolve_knight_walk_offset(action, tick_count, knight_scripts.walk_scripts)
    if walk_offset is not None:
        return walk_offset, 0
    step_count = tick_count // VBL_DIVISOR_NORMAL
    if action in _KNIGHT_HURT_TO_SCRIPT_INDEX:
        h_idx = _KNIGHT_HURT_TO_SCRIPT_INDEX[action]
        return _script_at(knight_scripts.hurt_scripts, h_idx, knight_scripts.alt_script), step_count
    if action == KnightAction.IDLE:
        return knight_scripts.idle_script, step_count
    a_idx = _KNIGHT_ACTION_TO_SCRIPT_INDEX.get(action, 0)
    return (
        _script_at(knight_scripts.action_scripts, a_idx, knight_scripts.idle_script),
        step_count,
    )


def _draws_to_pieces(
    draws: tuple[DrawFrameEvent, ...],
    default_cel: DecodedCel,
    cel_slots: Sequence[DecodedCel] | None,
) -> tuple[CombatSpritePiece, ...]:
    pieces: list[CombatSpritePiece] = []
    for ev in draws:
        slot_cel = (
            cel_slots[ev.cel_slot]
            if cel_slots is not None
            and 0 <= ev.cel_slot < len(cel_slots)
            and cel_slots[ev.cel_slot].frames
            else default_cel
        )
        if not slot_cel.frames:
            continue
        fidx = ev.frame_index % len(slot_cel.frames)
        pieces.append(
            CombatSpritePiece(
                frame=slot_cel.frames[fidx],
                frame_index=fidx,
                cel_slot=ev.cel_slot,
                dx=ev.dx,
                dy=ev.dy,
                flipped=ev.flipped,
                hurtbox=ev.hurtbox,
                attack=ev.attack,
                exclude_body_box=ev.exclude_body_box,
                top_left=True,
            )
        )
    return tuple(pieces)


def _centered_top_left_dx(width: int, *, flipped: bool, forward: int = 0) -> int:
    offset = (width // 2) + forward if flipped else -(width // 2) + forward
    return offset


def _fallback_multipart_knight_pieces(
    cel_slots: Sequence[DecodedCel],
    action: KnightAction,
    tick_count: int,
    *,
    flipped: bool,
) -> tuple[CombatSpritePiece, ...]:
    slot0 = cel_slots[0]
    slot3 = cel_slots[3]
    is_attack = action in _KNIGHT_ATTACK_ACTIONS
    legs_idx = 1 if len(slot0.frames) > 1 else 0
    legs_frame = slot0.frames[legs_idx]
    body_frame = slot0.frames[0]
    weapon_idx = (
        (1 + (tick_count // VBL_DIVISOR_NORMAL)) % len(slot3.frames)
        if is_attack
        else 1 % len(slot3.frames)
    )
    weapon_frame = slot3.frames[weapon_idx]
    weapon_forward = (-12 if flipped else 12) if is_attack else 0
    pieces: list[CombatSpritePiece] = [
        CombatSpritePiece(
            frame=legs_frame,
            frame_index=legs_idx,
            cel_slot=0,
            dx=_centered_top_left_dx(legs_frame.meta.width, flipped=flipped),
            dy=-legs_frame.meta.height,
            flipped=flipped,
            hurtbox=True,
            attack=False,
            top_left=True,
        ),
    ]
    if len(slot0.frames) > 1:
        pieces.append(
            CombatSpritePiece(
                frame=body_frame,
                frame_index=0,
                cel_slot=0,
                dx=_centered_top_left_dx(body_frame.meta.width, flipped=flipped),
                dy=-body_frame.meta.height,
                flipped=flipped,
                hurtbox=True,
                attack=False,
                top_left=True,
            )
        )
    pieces.append(
        CombatSpritePiece(
            frame=weapon_frame,
            frame_index=weapon_idx,
            cel_slot=3,
            dx=_centered_top_left_dx(
                weapon_frame.meta.width, flipped=flipped, forward=weapon_forward
            ),
            dy=-weapon_frame.meta.height,
            flipped=flipped,
            hurtbox=False,
            attack=is_attack,
            top_left=True,
        )
    )
    return tuple(pieces)


def _has_multipart_slots(cel_slots: Sequence[DecodedCel] | None) -> bool:
    return bool(
        cel_slots is not None
        and len(cel_slots) >= 4
        and cel_slots[0].frames
        and cel_slots[3].frames
    )


def _try_knight_mog_draws(
    action: KnightAction,
    tick_count: int,
    *,
    flipped: bool,
    mog_s4: bytes | None,
    knight_scripts: KnightScriptTables | None,
    combat_actions: CombatActionTables | None,
) -> tuple[DrawFrameEvent, ...]:
    if mog_s4 is None:
        return ()
    if knight_scripts is not None:
        offset, step_count = _resolve_knight_script_offset(action, tick_count, knight_scripts)
        draws = _run_mog_action_draws(mog_s4, offset, step_count, flipped)
        if draws:
            return draws
    if combat_actions is None:
        return ()
    slot = _KNIGHT_ACTION_TABLE_SLOT.get(action, 0)
    offsets = combat_actions.left_offsets if flipped else combat_actions.right_offsets
    if not (0 <= slot < len(offsets)):
        return ()
    return _run_mog_action_draws(mog_s4, offsets[slot], tick_count // VBL_DIVISOR_NORMAL, flipped)


def compose_knight_pieces(
    cel: DecodedCel,
    action: KnightAction,
    tick_count: int,
    *,
    flipped: bool = False,
    mog_s4: bytes | None = None,
    combat_actions: CombatActionTables | None = None,
    hit_set: HitSet | None = None,
    knight_scripts: KnightScriptTables | None = None,
    cel_slots: Sequence[DecodedCel] | None = None,
) -> tuple[CombatSpritePiece, ...]:
    """Compose multi-part knight `CombatSpritePiece`s from `mog` bytecode or body fallback."""
    if not cel.frames:
        return ()
    draws = _try_knight_mog_draws(
        action,
        tick_count,
        flipped=flipped,
        mog_s4=mog_s4,
        knight_scripts=knight_scripts,
        combat_actions=combat_actions,
    )
    if draws:
        return _draws_to_pieces(draws, cel, cel_slots)

    if cel_slots is not None and _has_multipart_slots(cel_slots):
        return _fallback_multipart_knight_pieces(cel_slots, action, tick_count, flipped=flipped)

    fidx = select_knight_body_frame(cel, action, tick_count, hit_set=hit_set)
    return (
        CombatSpritePiece(
            frame=cel.frames[fidx],
            frame_index=fidx,
            cel_slot=0,
            flipped=flipped,
            hurtbox=True,
            attack=action in _KNIGHT_ATTACK_ACTIONS,
            top_left=False,
        ),
    )


_ARCHETYPE_CREATURE_TYPE: dict[MonsterArchetype, int] = {
    MonsterArchetype.TROGG_SPEAR: 24,
    MonsterArchetype.TROGG_AXE: 28,
    MonsterArchetype.RATMAN: 32,
    MonsterArchetype.MUDMAN: 4,
    MonsterArchetype.BEASTMAN: 44,
    MonsterArchetype.DEMON: 48,
    MonsterArchetype.TROLL: 36,
    MonsterArchetype.BALOK: 0,
    MonsterArchetype.DRAGON: 20,
    MonsterArchetype.GUARDIAN: 64,
}


def _find_monster_scripts(
    archetype: MonsterArchetype,
    monster_scripts: Sequence[MonsterScriptTables],
) -> MonsterScriptTables | None:
    target = _ARCHETYPE_CREATURE_TYPE.get(archetype)
    if target is None:
        return None
    for entry in monster_scripts:
        if entry.creature_type == target:
            return entry
    return None


def _first_positive(offsets: Sequence[int]) -> int:
    for offset in offsets:
        if offset > 0:
            return offset
    return 0


def _first_nonzero(*values: int) -> int:
    for value in values:
        if value:
            return value
    return 0


def _resolve_monster_walk_offset(
    walk_scripts: Sequence[Sequence[int]],
    tick_count: int,
) -> int | None:
    if not walk_scripts:
        return None
    phases = [p for p in walk_scripts[0] if p > 0]
    if not phases:
        return None
    return phases[(tick_count // VBL_DIVISOR_NORMAL) % len(phases)]


def _select_monster_script_offset(
    ms: MonsterScriptTables,
    tick_count: int,
    *,
    attacking: bool,
    walking: bool,
    hurt: bool,
) -> tuple[int, int]:
    step_count = tick_count // VBL_DIVISOR_NORMAL
    if hurt:
        offset = _first_nonzero(_first_positive(ms.hurt_scripts), ms.alt_script, ms.idle_script)
        return offset, step_count
    if attacking:
        return _first_nonzero(_first_positive(ms.action_scripts), ms.idle_script), step_count
    if walking:
        walk_offset = _resolve_monster_walk_offset(ms.walk_scripts, tick_count)
        if walk_offset is not None:
            return walk_offset, 0
    return ms.idle_script, step_count


def compose_monster_pieces(
    cels: Sequence[DecodedCel],
    archetype: MonsterArchetype,
    tick_count: int,
    *,
    attacking: bool = False,
    walking: bool = False,
    hurt: bool = False,
    flipped: bool = False,
    mog_s4: bytes | None = None,
    monster_scripts: Sequence[MonsterScriptTables] = (),
) -> tuple[CombatSpritePiece, ...]:
    """Compose multi-part monster `CombatSpritePiece`s from `mog` scripts or cel-slot fallback."""
    if not cels:
        return ()
    if mog_s4 is not None and monster_scripts:
        ms = _find_monster_scripts(archetype, monster_scripts)
        if ms is not None:
            offset, step_count = _select_monster_script_offset(
                ms, tick_count, attacking=attacking, walking=walking, hurt=hurt
            )
            draws = _run_mog_action_draws(mog_s4, offset, step_count, flipped)
            if draws:
                return _draws_to_pieces(draws, cels[0], cels)

    fallback: list[CombatSpritePiece] = []
    for slot_idx, cel in enumerate(cels):
        if not cel.frames:
            continue
        step = (tick_count // VBL_DIVISOR_NORMAL) % min(len(cel.frames), 4)
        base = min(len(cel.frames) - 1, 2) if attacking else 0
        fidx = (base + step) % len(cel.frames)
        fallback.append(
            CombatSpritePiece(
                frame=cel.frames[fidx],
                frame_index=fidx,
                cel_slot=slot_idx,
                flipped=flipped,
                hurtbox=True,
                attack=attacking,
                top_left=False,
            )
        )
    return tuple(fallback)


def _build_attack_entries(
    attacker_id: EntityId,
    attacker_pos: Pos25D,
    attacker_pieces: Sequence[CombatSpritePiece],
    attacker_hit_set: HitSet | None,
) -> tuple[AttackEntry, ...]:
    if attacker_hit_set is None or not attacker_hit_set.frames:
        return ()
    is_kn4 = attacker_hit_set.norm_name == "kn4"
    atk_pieces = [p for p in attacker_pieces if p.attack and (not is_kn4 or p.cel_slot == 3)]
    attacks: list[AttackEntry] = []
    for piece in atk_pieces:
        if not (0 <= piece.frame_index < len(attacker_hit_set.frames)):
            continue
        hframe = attacker_hit_set.frames[piece.frame_index]
        if not hframe.points:
            continue
        sx, sy = cel_screen_origin(
            attacker_pos,
            piece.frame,
            dx=piece.dx,
            dy=piece.dy,
            flipped=piece.flipped,
            top_left=piece.top_left,
        )
        attacks.append(
            AttackEntry(
                entity_id=attacker_id,
                pos=attacker_pos,
                screen_x=sx,
                screen_y=sy,
                cel_width=piece.frame.meta.width,
                cel_height=piece.frame.meta.height,
                pattern=_hit_frame_to_pattern(hframe),
                flipped=piece.flipped,
            )
        )
    return tuple(attacks)


def _build_hurt_entries(
    defender_id: EntityId,
    defender_pos: Pos25D,
    defender_pieces: Sequence[CombatSpritePiece],
) -> tuple[HurtEntry, ...]:
    def_pieces = [p for p in defender_pieces if p.hurtbox] or list(defender_pieces)
    hurts: list[HurtEntry] = []
    for piece in def_pieces:
        mask = cel_mask_from_frame(piece.frame)
        if mask.width <= 0 or mask.height <= 0:
            continue
        sx, sy = cel_screen_origin(
            defender_pos,
            piece.frame,
            dx=piece.dx,
            dy=piece.dy,
            flipped=piece.flipped,
            top_left=piece.top_left,
        )
        hurts.append(
            HurtEntry(
                entity_id=defender_id,
                pos=defender_pos,
                screen_x=sx,
                screen_y=sy,
                mask=mask,
                flipped=piece.flipped,
            )
        )
    return tuple(hurts)


def resolve_composed_melee_attack(
    *,
    attacker_id: EntityId,
    attacker_pos: Pos25D,
    attacker_pieces: Sequence[CombatSpritePiece],
    attacker_hit_set: HitSet | None,
    attacker_flipped: bool,
    defender_id: EntityId,
    defender_pos: Pos25D,
    defender_pieces: Sequence[CombatSpritePiece],
    depth_threshold: int = DEFAULT_DEPTH_THRESHOLD,
    fallback_reach_x: int = 36,
) -> bool:
    """Resolve melee hits across multi-part `CombatSpritePiece`s using `collide.hit`."""
    if attacker_id == defender_id:
        return False
    if not depth_close(attacker_pos.y, defender_pos.y, depth_threshold):
        return False

    attacks = _build_attack_entries(attacker_id, attacker_pos, attacker_pieces, attacker_hit_set)
    hurts = _build_hurt_entries(defender_id, defender_pos, defender_pieces)
    if attacks and hurts:
        return bool(resolve_combat_hits(attacks, hurts, depth_threshold=depth_threshold))

    dx = defender_pos.x - attacker_pos.x
    if attacker_flipped:
        return -fallback_reach_x <= dx <= 8
    return -8 <= dx <= fallback_reach_x
