"""Campaign bridge to persistent fight jobs (ARCHITECTURE.md §10.22; ADR-011).

Unsupported handlers fail before a fight starts. There is no fallback creature or second
combat model. T11.11 completes campaign scaling, entry lanes and settlement.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING

from pymoonstone.app.fight_scene import FightScene
from pymoonstone.assets.caster_rules import parse_caster_rules
from pymoonstone.assets.creature_rules import parse_brawler_rules
from pymoonstone.assets.fight_defs import ActorInit, ArenaDef, SpawnEntry, parse_fight_defs
from pymoonstone.assets.fight_palette import assemble_fight_palette
from pymoonstone.assets.fight_program import (
    CREATURE_LIST,
    KNIGHT_LIST,
    cel_files,
    cel_shapes,
    fight_constants,
    frame_lists,
    script_image,
    script_tables,
)
from pymoonstone.assets.fighter_rules import parse_knight_rules
from pymoonstone.assets.hunk import parse_hunk_executable
from pymoonstone.assets.mog_code import MogCode
from pymoonstone.render.scene import palette_fx_to_rgb
from pymoonstone.sim.fight import Fight, TableKeys, Wall
from pymoonstone.sim.fight_ai.brawler import BRAWLER_TYPES, install_brawler
from pymoonstone.sim.fight_ai.caster import CASTER_TYPE, install_caster
from pymoonstone.sim.fighters import KNIGHT_TYPE, install_knight_control
from pymoonstone.sim.m68k import u8, u16
from pymoonstone.sim.palette_fx import pack_rgb12

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pymoonstone.app.scene_assets import GameAssetBundle
    from pymoonstone.assets.cel import DecodedCel
    from pymoonstone.assets.fight_defs import CelFile, CelSet
    from pymoonstone.sim.actor import Actor
    from pymoonstone.sim.fight import FrameOutput
    from pymoonstone.sim.inventory import Inventory
    from pymoonstone.sim.rng import Rng

FIGHT_VBLS = 6
_REGIONS = (4, 0, 8, 12)
_SUPPORTED_TYPES = (*BRAWLER_TYPES, CASTER_TYPE)


class FightUnavailableError(ValueError):
    """Missing original assets or a creature handler that has not been migrated yet."""


def _initialise(actor: Actor, init: ActorInit, keys: TableKeys) -> None:
    keys.assign(actor)
    values = (
        ("type", init.actor_type),
        ("input_port", init.input_port),
        ("hp", init.hp),
        ("hp_max", init.hp_max),
        ("reach_x", init.reach_x),
        ("too_close_x", init.too_close_x),
        ("depth_reach", init.depth_reach),
    )
    for name, value in values:
        if value is not None:
            setattr(actor, name, value)


@dataclass(slots=True)
class CreatureWaves:
    """Decoded spawn lists and alternating cursor (§10.4, §10.5)."""

    arena: ArenaDef
    keys: TableKeys
    toggle: int = 0

    def spawn(self, fight: Fight, entry: SpawnEntry) -> None:
        handle = fight.alloc_record()
        actor = fight.actor(handle)
        _initialise(actor, self.arena.creature, self.keys)
        position = (entry.x, entry.height, entry.y)
        actor.x, actor.height, actor.y = (u16(v) for v in position)
        actor.facing, actor.frame_list = entry.facing, CREATURE_LIST
        actor.hit_target = actor.attacker = 0
        fight.start_job(
            actor.idle_script, handle, CREATURE_LIST, position, entry.facing, actor.type
        )
        fight.alive_now = u16(fight.alive_now + 1)

    def next(self, fight: Fight) -> None:
        """Alternate the original's two entry records, starting with entry 1 (§10.5)."""
        self.toggle ^= 1
        self.spawn(fight, self.arena.next_creature[self.toggle])


@dataclass(slots=True)
class FightRuntime:
    """One active encounter; actor/job state is owned entirely by the persistent fight."""

    scene: FightScene
    first: int
    opponent: int = 0
    waves: CreatureWaves | None = None
    phase: int = 0
    finished: bool = False

    @property
    def fight(self) -> Fight:
        return self.scene.fight

    def step_vbl(self, vbl: int, ports: Sequence[int]) -> FrameOutput | None:
        """Sample ports and run one fight frame every six PAL ticks (§10.7)."""
        if self.finished:
            return None
        if len(ports) != 2:
            raise ValueError("a fight has exactly two joystick ports")
        self.fight.ports[:] = ports
        self.phase = (self.phase + 1) % FIGHT_VBLS
        if self.phase:
            return None
        ending = not self.fight.fight_active
        output = self.scene.advance(vbl)
        if ending:
            self.fight.end_timer = u8(self.fight.end_timer - 1)
            self.finished = self.fight.end_timer == 0
        return output

    def snapshot(self) -> dict[str, object]:
        """Serializable mutable fight state, including scripts, work blocks and spawn cursor."""
        f, e = self.fight, self.fight.engine
        return {
            "first": self.first,
            "opponent": self.opponent,
            "phase": self.phase,
            "finished": self.finished,
            "actors": [asdict(a) for a in f.actors],
            "jobs": [asdict(j) for j in e.jobs],
            "scratch": asdict(e.scratch_work),
            "mirrored": sorted(e.mirrored),
            "box": asdict(e.box),
            "attack": [None if v is None else asdict(v) for v in e.attack.entries],
            "hurt": [None if v is None else asdict(v) for v in e.hurt.entries],
            "cursors": [e.attack.cursor, e.hurt.cursor],
            "vbl": e.vbl,
            "gore_off": e.gore_off,
            "tables": {key: values[:] for key, values in f.tables.items()},
            "inventories": {k: v.counts[:] for k, v in f.inventories.items()},
            "ports": f.ports[:],
            "rng": f.rng.state,
            "approach": asdict(f.approach),
            "caster": asdict(f.caster),
            "flights": asdict(f.flights),
            "cells": [
                f.first,
                f.second,
                f.kept,
                f.fight_active,
                f.end_timer,
                f.alive_now,
                f.max_alive,
                f.total,
                f.demo,
                f.confused,
                f.confused_on,
                f.moon_frame,
                f.hop_facing,
                f.hop_table,
                f.hop_counter,
                f.gloated,
            ],
            "spawn_toggle": None if self.waves is None else self.waves.toggle,
        }


class FightLoader:
    """Decode once, then build fights with the original actor records and handlers (§10.15)."""

    def __init__(self, bundle: GameAssetBundle) -> None:
        self.bundle = bundle
        executable = parse_hunk_executable("mog", bundle.disk_set.read_file("mog"))
        self.mog = MogCode(executable)
        self.defs = parse_fight_defs(executable)
        self.knight_rules = parse_knight_rules(executable)
        self.brawler_rules = parse_brawler_rules(executable)
        self.caster_rules = parse_caster_rules(executable)

    def arena_for(self, handler_offset: int) -> ArenaDef:
        """Resolve the lair's original arena-table byte offset without guessing a type."""
        arena = self.defs.arena_by_handler.get(handler_offset)
        if arena is None:
            raise FightUnavailableError(f"unknown arena handler ${handler_offset:02X}")
        if arena.creature.actor_type not in _SUPPORTED_TYPES:
            raise FightUnavailableError(f"{arena.name} fights are not available yet")
        return arena

    def _cels(self, files: Mapping[str, CelFile]) -> dict[str, DecodedCel]:
        loaded = {}
        for name in files:
            cel = self.bundle.get_cel(name)
            if cel is None:
                raise FightUnavailableError(f"missing fight cel {name}")
            loaded[name] = cel
        return loaded

    def _scene(
        self,
        arena_cels: CelSet,
        arena_name: str,
        scene_code: int,
        knights: Sequence[Actor],
        dragon: Actor,
        inventories: Mapping[int, Inventory],
        rng: Rng,
        first: int,
        second: int = 0,
    ) -> FightScene:
        loaded = self.bundle.get_arena(arena_name)
        hits, palettes = self.bundle.get_hit_sets(), self.bundle.get_fight_palette_tables()
        if loaded is None or hits is None or palettes is None:
            raise FightUnavailableError("missing arena, collide.hit or fight palette tables")
        files = cel_files(self.defs.knight_cels, arena_cels)
        cels = self._cels(files)
        f = Fight(
            script_image(self.mog),
            cel_shapes(files, cels, hits),
            frame_lists(self.defs.knight_cels, arena_cels),
            knights,
            dragon,
            fight_constants(self.mog),
            rng=rng,
        )
        f.inventories = dict(inventories)
        f.walls = tuple(Wall(w.x1, w.x2, w.y) for w in loaded[0].obstacles)
        f.first, f.fight_active = first, True
        f.second = second
        install_knight_control(f, self.knight_rules)
        install_brawler(f, self.brawler_rules, self.knight_rules)
        install_caster(f, self.caster_rules, self.knight_rules)
        keys = f.register_tables(script_tables(self.defs.knight))
        for actor in knights:
            keys.assign(actor)
        picture = loaded[1]
        words = [pack_rgb12(r >> 4, g >> 4, b >> 4) for r, g, b in picture.palette]
        palette = assemble_fight_palette(
            words,
            palettes,
            scene=scene_code,
            region=_REGIONS[loaded[0].region],
            first_kind=f.actor(first).kind,
            second_kind=f.actor(second).kind if second else 0,
        )
        return FightScene(f, picture.pixels, cels, palette_fx_to_rgb(palette))

    def creature(
        self,
        arena: ArenaDef,
        background: str,
        knights: Sequence[Actor],
        dragon: Actor,
        inventories: Mapping[int, Inventory],
        rng: Rng,
        first: int,
        moon_frame: int = 0,
    ) -> FightRuntime:
        if arena.creature.actor_type not in _SUPPORTED_TYPES:
            raise FightUnavailableError(f"{arena.name} fights are not available yet")
        scene = self._scene(
            arena.cels, background, arena.scene, knights, dragon, inventories, rng, first
        )
        f = scene.fight
        f.moon_frame = moon_frame
        arena = _moon_arena(arena, moon_frame)
        knight = f.actor(first)
        for poke in arena.knight_pokes:
            key = knight.action_scripts if poke.table == "action" else knight.hurt_scripts
            f.tables[key][poke.offset // 4] = poke.script
        _start_knight(f, first, (250, 0, 100), 3, 1)
        if arena.creature.actor_type == CASTER_TYPE:
            self._caster_object(f, arena)
        waves = CreatureWaves(arena, f.register_tables(script_tables(arena.creature)))
        f.max_alive, f.total = arena.max_alive, arena.total
        f.next_creature = waves.next
        for entry in arena.first_wave[: f.max_alive]:
            waves.spawn(f, entry)
        return FightRuntime(scene, first, waves=waves)

    def _caster_object(self, fight: Fight, arena: ArenaDef) -> None:
        """Create Ratmen's flight anchor before the wave records (§10.23)."""
        rules = self.caster_rules
        edge = max((wall.y for wall in fight.walls), default=self.knight_rules.clip.top)
        edge = max(edge, self.knight_rules.clip.top)
        for obj in arena.objects:
            if obj.actor_type is None or obj.frame_set is None:
                raise FightUnavailableError("Ratmen flight anchor lacks type or frame set")
            fight.second = fight.spawn_actor(
                obj.idle_script,
                obj.frame_set,
                (rules.object_x, u16(edge - rules.object_height), edge),
                rules.object_facing,
                obj.actor_type,
            )

    def duel(
        self,
        background: str,
        knights: Sequence[Actor],
        dragon: Actor,
        inventories: Mapping[int, Inventory],
        rng: Rng,
        first: int,
        second: int,
    ) -> FightRuntime:
        scene = self._scene(
            self.defs.duel_cels,
            background,
            KNIGHT_TYPE,
            knights,
            dragon,
            inventories,
            rng,
            first,
            second,
        )
        _start_knight(scene.fight, first, (250, 0, 100), 3, 2)
        _start_knight(scene.fight, second, (80, 0, 100), 1, 1, CREATURE_LIST)
        return FightRuntime(scene, first, second)


def _moon_arena(arena: ArenaDef, moon_frame: int) -> ArenaDef:
    for variant in arena.moon_variants:
        if variant.moon_frame == moon_frame:
            creature = replace(arena.creature, hp=variant.hp, damage_table=variant.damage_table)
            return replace(arena, creature=creature)
    return arena


def _start_knight(
    fight: Fight,
    handle: int,
    pos: tuple[int, int, int],
    facing: int,
    port: int,
    frame_list: int = KNIGHT_LIST,
) -> None:
    actor = fight.actor(handle)
    actor.x, actor.height, actor.y = pos
    actor.facing, actor.input_port, actor.type = facing, port, KNIGHT_TYPE
    actor.active, actor.frame_list = 1, frame_list
    actor.action = actor.input = actor.hit_target = actor.attacker = 0
    fight.start_job(actor.idle_script, handle, frame_list, pos, facing, KNIGHT_TYPE)
