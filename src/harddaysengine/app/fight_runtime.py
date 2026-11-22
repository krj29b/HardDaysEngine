"""Campaign bridge to persistent fight jobs (ARCHITECTURE.md §10.22; ADR-011).

Unsupported handlers fail before a fight starts. There is no fallback creature or second
combat model. T11.11 completes campaign scaling, entry lanes and settlement.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from functools import partial
from typing import TYPE_CHECKING

from harddaysengine.app.fight_scene import FightScene
from harddaysengine.assets.arena import DecodedArena
from harddaysengine.assets.caster_rules import parse_caster_rules
from harddaysengine.assets.computer_knight_rules import parse_computer_knight_rules
from harddaysengine.assets.creature_rules import parse_brawler_rules
from harddaysengine.assets.dragon_rules import parse_dragon_rules
from harddaysengine.assets.drake_rules import parse_drake_rules
from harddaysengine.assets.fight_defs import ActorInit, ArenaDef, SpawnEntry, parse_fight_defs
from harddaysengine.assets.fight_palette import assemble_fight_palette
from harddaysengine.assets.fight_program import (
    CREATURE_LIST,
    KNIGHT_LIST,
    cel_files,
    cel_shapes,
    fight_constants,
    frame_lists,
    script_image,
    script_tables,
)
from harddaysengine.assets.fight_setup import parse_fight_setup
from harddaysengine.assets.fighter_rules import parse_knight_rules
from harddaysengine.assets.flyer_rules import parse_flyer_rules
from harddaysengine.assets.guardian_rules import parse_guardian_rules
from harddaysengine.assets.hunk import parse_hunk_executable
from harddaysengine.assets.mog_code import MogCode
from harddaysengine.assets.screen_fx import parse_shake_rules
from harddaysengine.assets.snatcher_rules import parse_snatcher_rules
from harddaysengine.assets.sound_rules import parse_sound_rules
from harddaysengine.assets.stalker_rules import parse_stalker_rules
from harddaysengine.assets.valley_data import valley_arena
from harddaysengine.render.scene import palette_fx_to_rgb
from harddaysengine.sim.fight import (
    DRAGON,
    DrakeCells,
    Fight,
    FlyerCells,
    TableKeys,
    Wall,
    pause_all,
)
from harddaysengine.sim.fight_ai.brawler import BRAWLER_TYPES, install_brawler
from harddaysengine.sim.fight_ai.caster import CASTER_TYPE, install_caster
from harddaysengine.sim.fight_ai.dragon import DRAGON_TYPE, install_dragon
from harddaysengine.sim.fight_ai.drake import DRAKE_TYPE, install_drake
from harddaysengine.sim.fight_ai.flyer import FLYER_TYPE, install_flyer
from harddaysengine.sim.fight_ai.guardian import GUARDIAN_TYPE, install_guardian
from harddaysengine.sim.fight_ai.knight import COMPUTER_KNIGHT_TYPE, install_computer_knight
from harddaysengine.sim.fight_ai.snatcher import SNATCHER_TYPE, install_snatcher
from harddaysengine.sim.fight_ai.stalker import STALKER_TYPE, install_stalker
from harddaysengine.sim.fight_setup import (
    FightSetupRules,
    FightWarnings,
    SpawnLanes,
    lane_depth,
    scale_wave,
)
from harddaysengine.sim.fighters import KNIGHT_TYPE, install_knight_control
from harddaysengine.sim.m68k import s16, u8, u16
from harddaysengine.sim.palette_fx import pack_rgb12, step_color
from harddaysengine.sim.screen_fx import SCREEN_SHAKE, screen_shake
from harddaysengine.sim.sound import install_sounds

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from harddaysengine.app.scene_assets import GameAssetBundle
    from harddaysengine.assets.cel import DecodedCel
    from harddaysengine.assets.fight_defs import CelFile, CelSet
    from harddaysengine.assets.picture import DecodedPicture
    from harddaysengine.sim.actor import Actor
    from harddaysengine.sim.fight import FrameOutput
    from harddaysengine.sim.inventory import Inventory
    from harddaysengine.sim.rng import Rng

FIGHT_VBLS = 6
_REGIONS = (4, 0, 8, 12)
_SUPPORTED_TYPES = (
    *BRAWLER_TYPES,
    CASTER_TYPE,
    SNATCHER_TYPE,
    FLYER_TYPE,
    STALKER_TYPE,
    DRAKE_TYPE,
    GUARDIAN_TYPE,
    DRAGON_TYPE,
)


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
    rules: FightSetupRules
    edge: int
    lanes: SpawnLanes
    toggle: int = 0

    def start(self, fight: Fight) -> None:
        """Use the decoded first list or the arena's one-entry alternating setup (§10.27)."""
        if self.arena.one_at_a_time or not self.arena.first_wave:
            self.next(fight)
        else:
            for entry in self.arena.first_wave[: fight.max_alive]:
                self.spawn(fight, entry)

    def spawn(self, fight: Fight, entry: SpawnEntry) -> None:
        handle = fight.alloc_record()
        actor = fight.actor(handle)
        _initialise(actor, self.arena.creature, self.keys)
        position = (entry.x, entry.height, self.lanes.next(self.edge, self.rules))
        actor.x, actor.height, actor.y = (u16(v) for v in position)
        actor.facing, actor.frame_list = entry.facing, CREATURE_LIST
        actor.hit_target = actor.attacker = 0
        fight.start_job(
            actor.idle_script, handle, CREATURE_LIST, position, entry.facing, actor.type
        )
        fight.alive_now = u16(fight.alive_now + 1)

    def next(self, fight: Fight) -> None:
        """Use a fixed entry or alternate two entry records, starting with entry 1 (§10.28)."""
        if len(self.arena.next_creature) == 1:
            self.spawn(fight, self.arena.next_creature[0])
            return
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
    rules: FightSetupRules | None = None
    warnings: FightWarnings | None = None
    lanes: SpawnLanes | None = None

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
        self.fight.advance_vbl(vbl, ticks=1)
        if self.rules is not None and self.warnings is not None:
            self.warnings.step(self.rules)
            if self.fight.guardian.fade:
                for index in range(2, 8):
                    self.warnings.colors[index] = step_color(self.warnings.colors[index], 0)
            self.scene.palette = palette_fx_to_rgb(self.warnings.colors)
        budget = FIGHT_VBLS if self.rules is None else self.rules.frame_vbls
        self.phase = (self.phase + 1) % budget
        if self.phase:
            return None
        output = self.scene.advance(vbl)
        self._after_frame()
        if not self.fight.fight_active:
            self.fight.end_timer = u8(self.fight.end_timer - 1)
            self.finished = self.fight.end_timer == 0
        return output

    def _after_frame(self) -> None:
        if self.rules is None:
            return
        if self.warnings is not None:
            self.warnings.register(self.fight, self.opponent, self.rules)
        if self.fight.fight_active and s16(self.fight.actor(self.first).hp) < 0:
            self.fight.end_timer = self.rules.death_delay
            self.fight.fight_active = False

    def release(self) -> None:
        """Free script and warning jobs after the caller has settled the final records (§10.24)."""
        for job in self.fight.engine.jobs:
            job.active = job.running = False
        if self.warnings is not None:
            self.warnings.ramps.clear()

    def snapshot(self) -> dict[str, object]:
        """Serializable mutable fight state, including scripts, work blocks and spawn cursor."""
        f, e = self.fight, self.fight.engine
        payload: dict[str, object] = {
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
            "ai_day": f.ai_day,
            "ai_last_action": f.ai_last_action,
            "rng": f.rng.state,
            "sound_cells": asdict(f.sound_cells),
            "approach": asdict(f.approach),
            "caster": asdict(f.caster),
            "snatch_flags": f.snatch_flags,
            "flyer": asdict(f.flyer),
            "drake": asdict(f.drake),
            "guardian": asdict(f.guardian),
            "dragon_cells": asdict(f.dragon_cells),
            "beam_word": f.beam_word,
            "shake": asdict(f.shake),
            "effect_vbl": f.effect_vbl,
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
        }
        payload.update(self._clock_snapshot())
        return payload

    def _clock_snapshot(self) -> dict[str, object]:
        return {
            "warnings": None if self.warnings is None else asdict(self.warnings),
            "lane": None if self.lanes is None else self.lanes.counter,
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
        self.snatcher_rules = parse_snatcher_rules(executable)
        self.flyer_rules = parse_flyer_rules(executable)
        self.flyer = FlyerCells(self.flyer_rules.initial_toggle)
        self.stalker_rules = parse_stalker_rules(executable, self.defs.frame_set_lists)
        self.drake_rules = parse_drake_rules(
            executable, self.caster_rules.flight, self.stalker_rules.spark
        )
        self.dragon_rules = parse_dragon_rules(
            executable,
            self.caster_rules.flight,
            self.stalker_rules.spark,
            self.defs.frame_set_lists,
        )
        self.computer_knight_rules = parse_computer_knight_rules(executable)
        self.guardian_rules = parse_guardian_rules(executable)
        self.sound_rules = parse_sound_rules(executable)
        self.sound_cells = self.sound_rules.initial_cells()
        self.drake = DrakeCells()
        self.shake_rules = parse_shake_rules(executable)
        self.setup_rules = parse_fight_setup(executable)

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

    def _background(self, name: str) -> tuple[DecodedArena, DecodedPicture] | None:
        """The Valley uses the original packed picture and default wall, without tiles (§12.38)."""
        if name != "valley":
            return self.bundle.get_arena(name)
        stage = valley_arena(self.mog)
        picture = self.bundle.get_test_picture(stage.picture)
        if picture is None:
            raise FightUnavailableError("missing original Guardian backdrop")
        return DecodedArena("valley", 3, False, stage.obstacles, ()), picture

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
        loaded = self._background(arena_name)
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
        install_computer_knight(f, self.computer_knight_rules, self.knight_rules)
        install_brawler(f, self.brawler_rules, self.knight_rules)
        install_caster(f, self.caster_rules, self.knight_rules)
        install_snatcher(f, self.snatcher_rules, self.knight_rules)
        install_flyer(f, self.flyer_rules, self.knight_rules)
        install_stalker(f, self.stalker_rules, self.knight_rules)
        install_drake(f, self.drake_rules, self.knight_rules)
        install_guardian(f, self.guardian_rules, self.knight_rules)
        install_dragon(f, self.dragon_rules, self.knight_rules)
        install_sounds(f, self.sound_rules)
        f.sound_cells = self.sound_cells
        f.drake = self.drake
        f.flyer = self.flyer
        f.shake_rules = self.shake_rules
        f.routines[SCREEN_SHAKE] = partial(screen_shake, self.shake_rules)
        keys = f.register_tables(script_tables(self.defs.knight))
        for actor in knights:
            _initialise(actor, self.defs.knight, keys)
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
        *,
        lair_count: int | None = None,
        lanes: SpawnLanes | None = None,
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
        cursor = SpawnLanes() if lanes is None else lanes
        edge = (
            max(self.knight_rules.clip.top, *(w.y for w in f.walls))
            if f.walls
            else self.knight_rules.clip.top
        )
        # Single-player/first fighter uses Amiga port 1 (input_port=2, §10.1/§10.23).
        self._enter_knight(f, first, cursor, edge, 2)
        if arena.creature.actor_type == DRAGON_TYPE:
            self._dragon(f, arena)
            pause_all(f)
            return self._runtime(scene, first, cursor)
        if arena.creature.actor_type == GUARDIAN_TYPE:
            self._guardian(f, arena)
            pause_all(f)
            return self._runtime(scene, first, cursor)
        if arena.creature.actor_type == CASTER_TYPE:
            self._caster_object(f, arena)
        waves = CreatureWaves(
            arena, f.register_tables(script_tables(arena.creature)), self.setup_rules, edge, cursor
        )
        f.max_alive, f.total = arena.max_alive, arena.total
        scale_wave(
            f,
            self.knight_rules,
            self.setup_rules,
            arena.cut_row,
            lair_count=lair_count,
            single=arena.one_at_a_time,
            cap=arena.alive_cap,
        )
        f.next_creature = waves.next
        waves.start(f)
        pause_all(f)
        return self._runtime(scene, first, cursor, waves=waves)

    def _runtime(
        self,
        scene: FightScene,
        first: int,
        lanes: SpawnLanes,
        *,
        opponent: int = 0,
        waves: CreatureWaves | None = None,
    ) -> FightRuntime:
        colors = [pack_rgb12(r >> 4, g >> 4, b >> 4) for r, g, b in scene.palette]
        return FightRuntime(
            scene,
            first,
            opponent,
            waves,
            rules=self.setup_rules,
            warnings=FightWarnings(colors),
            lanes=lanes,
        )

    def _enter_knight(
        self,
        fight: Fight,
        handle: int,
        lanes: SpawnLanes,
        edge: int,
        port: int,
        *,
        second: bool = False,
    ) -> None:
        rules = self.setup_rules
        pos = (
            rules.opponent_x if second else rules.knight_x,
            rules.knight_height,
            lanes.next(edge, rules),
        )
        _start_knight(
            fight,
            handle,
            pos,
            rules.opponent_facing if second else rules.knight_facing,
            port,
            CREATURE_LIST if second else KNIGHT_LIST,
            rules.enter_script,
        )

    def _fixed_actor(self, fight: Fight, init: ActorInit, handle: int, keys: TableKeys) -> None:
        """Place a fixed arena actor using only its decoded record (§10.30)."""
        x, height, depth = init.position
        if x is None or height is None or depth is None:
            raise FightUnavailableError("fixed fight actor lacks its original position")
        if init.frame_set is None or init.facing is None or init.actor_type is None:
            raise FightUnavailableError("fixed fight actor lacks its type or frame list")
        actor = fight.actor(handle)
        _initialise(actor, init, keys)
        actor.idle_script, actor.after_hit_script = init.idle_script, init.after_hit_script
        actor.active = 1
        actor.hit_target = actor.attacker = actor.action = actor.input = 0
        actor.x, actor.height, actor.y = (u16(v) for v in (x, height, depth))
        actor.frame_list, actor.facing = init.frame_set, init.facing
        fight.start_job(
            init.idle_script,
            handle,
            init.frame_set,
            (x, height, depth),
            init.facing,
            init.actor_type,
        )

    def _dragon(self, fight: Fight, arena: ArenaDef) -> None:
        """Use persistent dragon handle 5 and the two ordered decoded objects (§10.30)."""
        keys = fight.register_tables(script_tables(arena.creature))
        self._fixed_actor(fight, arena.creature, DRAGON, keys)
        handles = []
        for init in arena.objects:
            handle = fight.alloc_record()
            self._fixed_actor(fight, init, handle, keys)
            handles.append(handle)
        if len(handles) != 2:
            raise FightUnavailableError("Dragon arena does not define two linked objects")
        fight.dragon_cells.link_a, fight.dragon_cells.link_b = handles
        fight.alive_now = fight.max_alive = fight.total = 1

    def _guardian(self, fight: Fight, arena: ArenaDef) -> None:
        """Start the fixed Guardian actor and reserve its separate body record (§10.29)."""
        init = arena.creature
        x, height, depth = init.position
        if x is None or height is None or depth is None:
            raise FightUnavailableError("Guardian initial position missing")
        position = (x, height, depth)
        if init.facing is None or init.frame_set is None:
            raise FightUnavailableError("Guardian initial position or frame list missing")
        handle = fight.alloc_record()
        actor = fight.actor(handle)
        keys = fight.register_tables(script_tables(init))
        _initialise(actor, init, keys)
        actor.x, actor.height, actor.y = (u16(v) for v in position)
        actor.facing, actor.frame_list = init.facing, init.frame_set
        fight.guardian.main, fight.guardian.body = handle, fight.alloc_record()
        fight.guardian.target = fight.first
        fight.start_job(
            init.idle_script, handle, init.frame_set, position, init.facing, GUARDIAN_TYPE
        )
        fight.alive_now = fight.max_alive = fight.total = 1

    def _caster_object(self, fight: Fight, arena: ArenaDef) -> None:
        """Create Ratmen's flight anchor before the wave records (§10.23)."""
        rules = self.caster_rules
        edge = max((wall.y for wall in fight.walls), default=self.knight_rules.clip.top)
        edge = max(edge, self.knight_rules.clip.top)
        depth = lane_depth(3, edge, self.setup_rules)
        for obj in arena.objects:
            if obj.actor_type is None or obj.frame_set is None:
                raise FightUnavailableError("Ratmen flight anchor lacks type or frame set")
            fight.second = fight.spawn_actor(
                obj.idle_script,
                obj.frame_set,
                (rules.object_x, u16(depth - rules.object_height), depth),
                rules.object_facing,
                obj.actor_type,
            )

    def _duel_knights(
        self,
        fight: Fight,
        first: int,
        second: int,
        computers: frozenset[int],
        cursor: SpawnLanes,
        edge: int,
    ) -> None:
        port = 2
        for handle in (first, second):
            computer = handle in computers
            self._enter_knight(
                fight, handle, cursor, edge, 4 if computer else port, second=handle == second
            )
            if computer:
                actor = fight.actor(handle)
                actor.type = COMPUTER_KNIGHT_TYPE
                job = fight.find_job(handle)
                if job is None:
                    raise FightUnavailableError("computer knight job missing after setup")
                job.type = COMPUTER_KNIGHT_TYPE
            else:
                port = 1

    def duel(
        self,
        background: str,
        knights: Sequence[Actor],
        dragon: Actor,
        inventories: Mapping[int, Inventory],
        rng: Rng,
        first: int,
        second: int,
        *,
        lanes: SpawnLanes | None = None,
        computers: frozenset[int] = frozenset(),
        day: int = 0,
        moon_frame: int = 0,
    ) -> FightRuntime:
        if first == second or not {first, second} <= {1, 2, 3, 4}:
            raise FightUnavailableError("duel requires two different knight handles")
        if not computers <= {first, second} or computers == {first, second}:
            raise FightUnavailableError("duel requires at least one human knight")
        for handle in computers:
            knights[handle - 1].kind = 4
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
        cursor = SpawnLanes() if lanes is None else lanes
        edge = max((w.y for w in scene.fight.walls), default=self.knight_rules.clip.top)
        edge = max(edge, self.knight_rules.clip.top)
        scene.fight.ai_day = u16(day)
        scene.fight.moon_frame = moon_frame
        self._duel_knights(scene.fight, first, second, computers, cursor, edge)
        pause_all(scene.fight)
        return self._runtime(scene, first, cursor, opponent=second)


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
    script: int | None = None,
) -> None:
    actor = fight.actor(handle)
    actor.x, actor.height, actor.y = pos
    actor.facing, actor.input_port, actor.type = facing, port, KNIGHT_TYPE
    actor.active, actor.frame_list = 1, frame_list
    actor.action = actor.input = actor.hit_target = actor.attacker = 0
    fight.start_job(
        actor.idle_script if script is None else script,
        handle,
        frame_list,
        pos,
        facing,
        KNIGHT_TYPE,
    )
