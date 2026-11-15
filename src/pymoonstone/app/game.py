"""Scene coordinator, headless deterministic session, and 50 Hz game host (T8.3)."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING

import moderngl
import numpy as np
import pygame

from pymoonstone.app.audio import AudioEngine
from pymoonstone.app.combat_bridge import (
    build_mog_encounter,
    clamp_entity_to_arena,
    resolve_melee_attack_points,
)
from pymoonstone.app.config import find_adf_images
from pymoonstone.app.input import (
    InputFrame,
    InputRouter,
    PlayerInput,
    ReplayRecording,
    load_replay,
    save_replay,
)
from pymoonstone.app.location_screens import (
    LocationKind,
    LocationMenuState,
    render_location_menu,
    render_new_day_screen,
    render_status_screen,
    step_location_menu,
)
from pymoonstone.app.loop import Accumulator50Hz
from pymoonstone.app.scene_assets import (
    GameAssetBundle,
    build_intro_cinematic_frame,
    make_sprite,
    select_knight_frame,
    select_monster_frame,
)
from pymoonstone.assets.adf import DiskSet
from pymoonstone.assets.palette import RGBColor, default_combat_palette
from pymoonstone.render.renderer import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    PLAYFIELD_HEIGHT,
    MoonstoneRenderer,
)
from pymoonstone.render.scene import (
    BitmapFont,
    CombatHudState,
    OverworldHudState,
    SceneRenderer,
    SceneSprite,
    default_bitmap_font,
    render_combat_hud,
    render_overworld_hud,
)
from pymoonstone.sim.collision import DEFAULT_DEPTH_THRESHOLD, depth_close
from pymoonstone.sim.knight_combat import (
    KnightAction,
    KnightState,
    apply_knight_damage,
    step_knight_vbl,
)
from pymoonstone.sim.locations import (
    distribute_lair_loot,
    perform_stonehenge_ritual,
)
from pymoonstone.sim.monster_ai import (
    LairEncounter,
    MonsterArchetype,
    MonsterState,
    ProjectileState,
    TargetSnapshot,
    apply_hit_to_monster,
)
from pymoonstone.sim.overworld import (
    EncounterTrigger,
    NodeKind,
    OverworldState,
    TriggerKind,
    create_campaign,
    legal_moves,
    move_knight,
    resolve_lair_victory,
)
from pymoonstone.sim.palette_fx import PaletteFxState, pack_rgb12
from pymoonstone.sim.prng import DEFAULT_SEED
from pymoonstone.sim.types import Direction, EntityId, KnightId, Pos25D

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    import numpy.typing as npt

    from pymoonstone.assets.arena import DecodedArena
    from pymoonstone.assets.cel import CelFrame
    from pymoonstone.assets.hit import HitFrame

_ATTACK_ACTIONS: frozenset[KnightAction] = frozenset(
    {
        KnightAction.OVERHEAD_SLASH,
        KnightAction.THRUST,
        KnightAction.LOW_SWEEP,
    }
)
_KNIGHT_MELEE_RANGE_X = 36
_DAGGER_SPEED_X = 4
_DAGGER_TTL_VBLS = 45
_DAGGER_DAMAGE = 6

_ARCHETYPE_HIT_KEYS: dict[MonsterArchetype, str] = {
    MonsterArchetype.TROGG_SPEAR: "troggspear1",
    MonsterArchetype.TROGG_AXE: "troggaxe1",
    MonsterArchetype.RATMAN: "ratmen1",
    MonsterArchetype.MUDMAN: "mudmen1",
    MonsterArchetype.BEASTMAN: "be1",
    MonsterArchetype.DEMON: "demon1",
    MonsterArchetype.TROLL: "troll1",
    MonsterArchetype.BALOK: "balok1",
    MonsterArchetype.DRAGON: "dragon1",
    MonsterArchetype.GUARDIAN: "he1",
}


class GameScene(IntEnum):
    """Top-level game scene state machine (IMPLEMENTATION_PLAN §5 T8.3)."""

    TITLE = 0
    KNIGHT_SELECT = 1
    OVERWORLD = 2
    COMBAT = 3
    VICTORY = 4
    GAME_OVER = 5
    LOCATION = 6
    STATUS = 7
    NEW_DAY = 8


@dataclass(slots=True)
class GoreParticle:
    """Short-lived ``blo.cel`` gore sprite spawned on combat hits."""

    x: int
    y: int
    z: int
    frame_index: int
    ttl_ticks: int = 12


@dataclass(slots=True)
class ActiveEncounter:
    """Runtime state for an active arena combat encounter."""

    trigger: EncounterTrigger
    encounter: LairEncounter
    knight_ids: tuple[KnightId, ...]
    arena: DecodedArena | None = None
    swing_spent: dict[KnightId, bool] = field(default_factory=dict)
    knight_projectiles: list[ProjectileState] = field(default_factory=list)
    gore_particles: list[GoreParticle] = field(default_factory=list)


@dataclass(slots=True, frozen=True)
class HeadlessSessionResult:
    """Output summary from :func:`run_headless_session`."""

    session: GameSession
    final_hash: str
    ticks_executed: int
    scene: GameScene
    recording: ReplayRecording


def _make_knight_combat_state(knight_id: KnightId, index: int) -> KnightState:
    return KnightState(
        knight_id=knight_id,
        entity_id=EntityId(index + 1),
        x=80 + index * 24,
        y=95 + (index % 2) * 14,
        direction=Direction.RIGHT,
        input_port=int(knight_id),
    )


def _palette_to_rgb12(palette: Sequence[RGBColor]) -> tuple[int, ...]:
    words = [pack_rgb12(r >> 4, g >> 4, b >> 4) for r, g, b in palette[:32]]
    while len(words) < 32:
        words.append(0)
    return tuple(words)


_PORTRAIT_SLOT_TO_KNIGHT: tuple[KnightId, ...] = (
    KnightId.BLUE,
    KnightId.YELLOW,
    KnightId.GREEN,
    KnightId.RED,
)


class GameSession:
    """Deterministic 50 Hz multi-scene Moonstone game session (T8.3)."""

    def __init__(
        self,
        *,
        seed: int = DEFAULT_SEED,
        players: Sequence[KnightId] = (KnightId.RED,),
        start_scene: GameScene = GameScene.TITLE,
        audio: AudioEngine | None = None,
        disk_set: DiskSet | None = None,
    ) -> None:
        roster = tuple(players) if players else (KnightId.RED,)
        self.seed = seed
        self.players: tuple[KnightId, ...] = roster
        self.scene: GameScene = start_scene
        self.tick_count: int = 0
        self.paused: bool = False
        self.selected_player_count: int = len(roster)
        self.menu_cursor: int = 0
        self.gore_enabled: bool = True
        self.practice_mode: bool = False
        self.knight_pick_active: bool = False
        self.knight_pick_slot: int = 0
        self.knight_taken_mask: int = 0
        self.picked_knights: list[KnightId] = []
        self._menu_prev_dx: int = 0
        self._menu_prev_dy: int = 0
        self._menu_prev_fire: bool = False
        self._ow_prev_fire: bool = False
        self._ow_prev_secondary: bool = False
        self._end_prev_fire: bool = False
        self.selected_move_index: int = 0
        self.audio = audio
        self.disk_set = disk_set
        self.asset_bundle: GameAssetBundle | None = (
            GameAssetBundle(disk_set) if disk_set is not None else None
        )
        graph = self.asset_bundle.build_overworld_graph() if self.asset_bundle is not None else None
        self.overworld: OverworldState = create_campaign(roster, graph=graph, seed=seed)
        self.knight_combat: dict[KnightId, KnightState] = {
            kid: _make_knight_combat_state(kid, idx) for idx, kid in enumerate(roster)
        }
        self.active_encounter: ActiveEncounter | None = None
        self.location_menu: LocationMenuState | None = None
        self.location_knight_id: KnightId = roster[0]
        self.palette_fx: PaletteFxState = PaletteFxState(
            _palette_to_rgb12(default_combat_palette())
        )
        self._pending_fade_in: bool = False
        self.last_status: str = "PRESS ENTER TO BEGIN"

    def _reset_campaign(self, count: int, custom_roster: Sequence[KnightId] = ()) -> None:
        clamped = max(1, min(4, count))
        roster = (
            tuple(custom_roster[:clamped])
            if custom_roster
            else tuple(KnightId(i) for i in range(clamped))
        )
        self.players = roster
        self.selected_player_count = len(roster)
        self.selected_move_index = 0
        graph = self.asset_bundle.build_overworld_graph() if self.asset_bundle is not None else None
        self.overworld = create_campaign(roster, graph=graph, seed=self.seed)
        self.knight_combat = {
            kid: _make_knight_combat_state(kid, idx) for idx, kid in enumerate(roster)
        }
        self.active_encounter = None
        self.location_menu = None
        self.location_knight_id = roster[0]
        self.practice_mode = False

    def _player_input(self, frame: InputFrame, kid: KnightId) -> PlayerInput:
        direct = frame.for_player(kid)
        if kid not in self.players:
            return direct
        slot_kid = KnightId(self.players.index(kid))
        if slot_kid == kid:
            return direct
        slot_inp = frame.for_player(slot_kid)
        if slot_inp != PlayerInput():
            return slot_inp
        return direct

    def step_tick(self, frame: InputFrame) -> None:
        """Advance the game session by exactly one 50 Hz PAL VBL tick."""
        self.tick_count += 1
        primary = self._player_input(frame, self.players[0])
        if primary.pause and self.scene in (GameScene.OVERWORLD, GameScene.COMBAT):
            self.paused = not self.paused
            return
        if self.paused:
            return

        self._step_active_scene(frame, primary)
        self.palette_fx.step_vbl()
        if self.audio is not None:
            self.audio.step_vbl()

    def _step_active_scene(self, frame: InputFrame, primary: PlayerInput) -> None:
        if self.scene == GameScene.TITLE:
            self._step_title(primary)
        elif self.scene == GameScene.KNIGHT_SELECT:
            self._step_knight_select(primary)
        elif self.scene == GameScene.OVERWORLD:
            self._step_overworld(frame)
        elif self.scene == GameScene.COMBAT:
            self._step_combat(frame)
        elif self.scene == GameScene.LOCATION:
            self._step_location(frame)
        elif self.scene == GameScene.STATUS:
            self._step_status(frame)
        elif self.scene == GameScene.NEW_DAY:
            self._step_new_day(primary)
        else:
            self._step_end_screen(primary)

    def _play_title_music(self) -> None:
        if self.audio is not None and self.asset_bundle is not None:
            mod = self.asset_bundle.load_title_music()
            if mod is not None:
                self.audio.play_music(mod)

    def _play_victory_music(self) -> None:
        if self.audio is not None and self.asset_bundle is not None:
            mod = self.asset_bundle.load_victory_music()
            if mod is not None:
                self.audio.play_music(mod)

    def _enter_victory(self, status: str) -> None:
        self.scene = GameScene.VICTORY
        self.last_status = status
        self._pending_fade_in = True
        self._play_victory_music()

    def _enter_game_over(self) -> None:
        self.scene = GameScene.GAME_OVER
        self.last_status = "GAME OVER"
        self._pending_fade_in = True

    def _step_title(self, primary: PlayerInput) -> None:
        finished = False
        if self.asset_bundle is not None:
            player = self.asset_bundle.get_intro_player()
            finished = player is not None and player.is_finished(self.tick_count)
        if primary.confirm or primary.fire or finished:
            self.scene = GameScene.KNIGHT_SELECT
            self.last_status = "SELECT KNIGHTS"

    def _step_knight_pick(self, dx_edge: int, confirm: bool) -> None:
        if dx_edge != 0:
            step = 1 if dx_edge > 0 else -1
            for offset in range(1, 5):
                cand = (self.knight_pick_slot + step * offset) & 3
                if not (self.knight_taken_mask & (1 << cand)):
                    self.knight_pick_slot = cand
                    break
        if not confirm:
            return
        slot = self.knight_pick_slot & 3
        self.knight_taken_mask |= 1 << slot
        self.picked_knights.append(_PORTRAIT_SLOT_TO_KNIGHT[slot])
        if len(self.picked_knights) >= self.selected_player_count:
            self.knight_pick_active = False
            self._reset_campaign(self.selected_player_count, self.picked_knights)
            self.scene = GameScene.OVERWORLD
            self.last_status = f"DAY {self.overworld.day}"
            return
        for cand in range(4):
            if not (self.knight_taken_mask & (1 << cand)):
                self.knight_pick_slot = cand
                break

    def _confirm_main_menu(self) -> None:
        if self.menu_cursor == 1:
            self.gore_enabled = not self.gore_enabled
            return
        if self.menu_cursor == 2:
            self._reset_campaign(self.selected_player_count)
            self.practice_mode = True
            self._start_combat(
                EncounterTrigger(
                    kind=TriggerKind.WANDERING_MONSTER,
                    knight_id=self.players[0],
                    node_id=0,
                    day=self.overworld.day,
                    moon_phase=self.overworld.moon_phase,
                    encounter_kind=0,
                ),
                self.players[0],
            )
            return
        if (
            self.menu_cursor == 3
            and self.asset_bundle is not None
            and self.asset_bundle.get_intro_player() is not None
        ):
            self.knight_pick_active = True
            self.knight_pick_slot = 0
            self.knight_taken_mask = 0
            self.picked_knights = []
            return
        self._reset_campaign(self.selected_player_count)
        self.scene = GameScene.OVERWORLD
        self.last_status = f"DAY {self.overworld.day}"

    def _adjust_main_menu(self, dx_edge: int, dy_edge: int) -> None:
        if dy_edge != 0:
            self.menu_cursor = max(0, min(3, self.menu_cursor + (1 if dy_edge > 0 else -1)))
        if dx_edge == 0:
            return
        if self.menu_cursor == 0:
            delta = 1 if dx_edge > 0 else -1
            self.selected_player_count = ((self.selected_player_count - 1 + delta) % 4) + 1
        elif self.menu_cursor == 1:
            self.gore_enabled = not self.gore_enabled

    def _step_knight_select(self, primary: PlayerInput) -> None:
        dx_edge = primary.dx if primary.dx != self._menu_prev_dx else 0
        dy_edge = primary.dy if primary.dy != self._menu_prev_dy else 0
        fire_edge = primary.fire and not self._menu_prev_fire
        self._menu_prev_dx = primary.dx
        self._menu_prev_dy = primary.dy
        self._menu_prev_fire = primary.fire
        confirm = primary.confirm or fire_edge

        if self.knight_pick_active:
            self._step_knight_pick(dx_edge, confirm)
            return

        self._adjust_main_menu(dx_edge, dy_edge)
        if confirm:
            self._confirm_main_menu()

    def _confirm_overworld_move(self, kid: KnightId, target_node: int) -> None:
        prev_day = self.overworld.day
        result = move_knight(self.overworld, target_node)
        self.selected_move_index = 0
        self._handle_overworld_triggers(kid, result.triggers)
        if self.scene == GameScene.OVERWORLD and self.overworld.day > prev_day:
            self.scene = GameScene.NEW_DAY
            self.last_status = f"DAY {self.overworld.day}"

    def _handle_overworld_game_over(self) -> bool:
        if not self.overworld.game_over:
            return False
        if self.overworld.winner is not None:
            self._enter_victory(f"{self.overworld.winner.name} WINS")
        else:
            self._enter_game_over()
        return True

    def _cycle_overworld_move(self, inp: PlayerInput, move_count: int) -> None:
        delta = inp.dx or inp.dy
        if delta != 0:
            self.selected_move_index = (
                self.selected_move_index + (1 if delta > 0 else -1)
            ) % move_count

    def _step_overworld(self, frame: InputFrame) -> None:
        if self._handle_overworld_game_over():
            return

        kid = self.overworld.current_knight_id
        inp = self._player_input(frame, kid)
        moves = legal_moves(self.overworld, kid)
        if not moves:
            self._enter_game_over()
            return

        secondary_edge = inp.secondary and not self._ow_prev_secondary
        self._ow_prev_secondary = inp.secondary
        if secondary_edge:
            self.scene = GameScene.STATUS
            self.last_status = "STATUS"
            return

        self._cycle_overworld_move(inp, len(moves))

        fire_edge = inp.fire and not self._ow_prev_fire
        self._ow_prev_fire = inp.fire
        if inp.confirm or fire_edge:
            self._confirm_overworld_move(kid, moves[self.selected_move_index % len(moves)])

    def _open_location(self, knight_id: KnightId, location: LocationKind) -> None:
        self.location_knight_id = knight_id
        self.location_menu = LocationMenuState(location=location)
        self.scene = GameScene.LOCATION
        self.last_status = location.name

    def _handle_overworld_triggers(
        self,
        knight_id: KnightId,
        triggers: tuple[EncounterTrigger, ...],
    ) -> None:
        for trig in triggers:
            if trig.kind == TriggerKind.VICTORY:
                self._enter_victory(f"{knight_id.name} WINS")
                return
            if trig.kind == TriggerKind.TOWN_VISIT:
                node = self.overworld.graph.nodes.get(trig.node_id)
                loc_kind = (
                    LocationKind.WATERDEEP
                    if node is not None and node.kind == NodeKind.WATERDEEP
                    else LocationKind.HIGHWOOD
                )
                self._open_location(knight_id, loc_kind)
                return
            if trig.kind == TriggerKind.STONEHENGE_RITUAL:
                if self.overworld.knights[knight_id].has_all_moonstones:
                    ritual = perform_stonehenge_ritual(
                        self.overworld,
                        knight_id,
                        knight_combat=self.knight_combat[knight_id],
                    )
                    if ritual.victory:
                        self._enter_victory(f"{knight_id.name} WINS")
                        return
                self._open_location(knight_id, LocationKind.STONEHENGE)
                return
            if trig.kind in (
                TriggerKind.WANDERING_MONSTER,
                TriggerKind.LAIR_BOSS,
                TriggerKind.DRAGON_INTERCEPT,
            ):
                self._start_combat(trig, knight_id)
                return

    def _step_location(self, frame: InputFrame) -> None:
        if self.location_menu is None:
            self.scene = GameScene.OVERWORLD
            return
        kid = self.location_knight_id
        inp = self._player_input(frame, kid)
        res = step_location_menu(self.location_menu, inp, self.knight_combat[kid], self.overworld)
        if res.victory:
            self.location_menu = None
            self._enter_victory(f"{kid.name} WINS")
            return
        if res.left_location:
            self.location_menu = None
            self.scene = GameScene.OVERWORLD
            self.last_status = f"DAY {self.overworld.day}"

    def _step_status(self, frame: InputFrame) -> None:
        inp = self._player_input(frame, self.overworld.current_knight_id)
        fire_edge = inp.fire and not self._ow_prev_fire
        secondary_edge = inp.secondary and not self._ow_prev_secondary
        self._ow_prev_fire = inp.fire
        self._ow_prev_secondary = inp.secondary
        if inp.confirm or fire_edge or secondary_edge:
            self.scene = GameScene.OVERWORLD
            self.last_status = f"DAY {self.overworld.day}"

    def _step_new_day(self, primary: PlayerInput) -> None:
        fire_edge = primary.fire and not self._ow_prev_fire
        self._ow_prev_fire = primary.fire
        if primary.confirm or fire_edge:
            self.scene = GameScene.OVERWORLD
            self.last_status = f"DAY {self.overworld.day}"

    def _step_end_screen(self, primary: PlayerInput) -> None:
        fire_edge = primary.fire and not self._end_prev_fire
        self._end_prev_fire = primary.fire
        if primary.confirm or fire_edge:
            self._reset_campaign(self.selected_player_count)
            self.scene = GameScene.KNIGHT_SELECT
            self.last_status = "SELECT KNIGHTS"
            self._pending_fade_in = True
            self._play_title_music()

    def _try_play_sample(self, *names: str) -> None:
        if self.audio is None:
            return
        for name in names:
            try:
                self.audio.play_sample(name)
                return
            except KeyError:
                continue

    def _spawn_gore(self, active: ActiveEncounter, x: int, y: int, z: int) -> None:
        if not self.gore_enabled:
            return
        active.gore_particles.append(GoreParticle(x=x, y=y, z=z, frame_index=self.tick_count % 6))

    def _step_gore_particles(self, active: ActiveEncounter) -> None:
        if not active.gore_particles:
            return
        remaining: list[GoreParticle] = []
        for gp in active.gore_particles:
            gp.ttl_ticks -= 1
            if gp.ttl_ticks > 0:
                remaining.append(gp)
        active.gore_particles = remaining

    def _start_combat(self, trigger: EncounterTrigger, knight_id: KnightId) -> None:
        kstate = self.knight_combat[knight_id]
        kstate.x = 80
        kstate.y = 95
        kstate.z = 0
        kstate.direction = Direction.RIGHT
        kstate.action = KnightAction.IDLE
        kstate.action_timer = 0
        mog_tables = self.asset_bundle.get_mog_tables() if self.asset_bundle is not None else None
        arena_loaded = (
            self.asset_bundle.get_combat_arena(max(0, trigger.lair_index))
            if self.asset_bundle is not None
            else None
        )
        arena = arena_loaded[0] if arena_loaded is not None else None
        encounter = build_mog_encounter(trigger, (knight_id,), mog_tables, arena=arena)
        self.active_encounter = ActiveEncounter(
            trigger=trigger,
            encounter=encounter,
            knight_ids=(knight_id,),
            arena=arena,
        )
        self.scene = GameScene.COMBAT
        self._pending_fade_in = True
        self.last_status = f"COMBAT: {trigger.kind.name}"

    def _spawn_knight_dagger(self, kstate: KnightState, active: ActiveEncounter) -> None:
        vx = -_DAGGER_SPEED_X if kstate.direction.is_left else _DAGGER_SPEED_X
        active.knight_projectiles.append(
            ProjectileState(
                owner_id=kstate.entity_id,
                projectile_type_id=0,
                pos=Pos25D(x=kstate.x, y=kstate.y, z=max(8, kstate.z + 12)),
                vx=vx,
                vy=0,
                vz=0,
                direction=kstate.direction,
                damage=_DAGGER_DAMAGE + max(0, kstate.strength - 1),
                ttl_ticks=_DAGGER_TTL_VBLS,
            )
        )
        self._try_play_sample("throw.a", "swish.a", "knifethr.a")

    def _step_knight_projectiles(self, active: ActiveEncounter) -> None:
        if not active.knight_projectiles:
            return
        remaining: list[ProjectileState] = []
        for proj in active.knight_projectiles:
            if not proj.active or proj.ttl_ticks <= 0:
                continue
            proj.ttl_ticks -= 1
            proj.pos = proj.pos.translate(proj.vx, proj.vy, proj.vz)
            if not (0 <= proj.pos.x < CANVAS_WIDTH):
                continue
            if self._hit_monster_with_projectile(proj, active):
                continue
            remaining.append(proj)
        active.knight_projectiles = remaining

    def _hit_monster_with_projectile(self, proj: ProjectileState, active: ActiveEncounter) -> bool:
        for monster in active.encounter.monsters:
            if not monster.is_alive:
                continue
            if abs(proj.pos.x - monster.pos.x) <= 14 and depth_close(
                proj.pos.y, monster.pos.y, DEFAULT_DEPTH_THRESHOLD
            ):
                apply_hit_to_monster(monster, proj.damage, proj.pos.x)
                self._spawn_gore(active, monster.pos.x, monster.pos.y, monster.pos.z)
                self._try_play_sample("hit.a", "splat.a", "swordhit.a")
                return True
        return False

    def _clamp_monsters_to_arena(self, active: ActiveEncounter) -> None:
        if active.arena is None:
            return
        for monster in active.encounter.monsters:
            if monster.is_alive:
                monster.pos = clamp_entity_to_arena(monster.pos, active.arena)

    def _step_combat(self, frame: InputFrame) -> None:
        active = self.active_encounter
        if active is None:
            self.scene = GameScene.OVERWORLD
            return

        targets: list[TargetSnapshot] = []
        for kid in active.knight_ids:
            kstate = self.knight_combat[kid]
            kres = step_knight_vbl(
                kstate,
                self._player_input(frame, kid).to_knight_input(),
                prng=self.overworld.prng,
            )
            clamped = clamp_entity_to_arena(kstate.pos, active.arena)
            kstate.x, kstate.y, kstate.z = clamped.x, clamped.y, clamped.z
            if kres.threw_secondary:
                self._spawn_knight_dagger(kstate, active)
            if self.audio is not None and kres.vm_events:
                self.audio.handle_combat_events(kres.vm_events)
            self._resolve_knight_melee(kstate, active)
            targets.append(
                TargetSnapshot(
                    entity_id=kstate.entity_id,
                    pos=kstate.pos,
                    alive=kstate.is_alive,
                    hp=kstate.hp,
                )
            )

        self._step_knight_projectiles(active)
        enc_res = active.encounter.step_vbl(targets, self.overworld.prng)
        self._clamp_monsters_to_arena(active)
        self._resolve_monster_attacks(active, enc_res)
        self._step_gore_particles(active)

        if self.audio is not None:
            for mres in enc_res.monster_results:
                self.audio.handle_combat_events(mres.combat_events)

        self._check_combat_resolution(active)

    def _knight_frame_info(self, knight: KnightState) -> tuple[CelFrame | None, HitFrame | None]:
        if self.asset_bundle is None:
            return None, None
        kcel = self.asset_bundle.get_knight_cel(int(knight.knight_id))
        if kcel is None or not kcel.frames:
            return None, None
        fidx = select_knight_frame(kcel, knight.action, self.tick_count)
        frame = kcel.frames[fidx]
        hit_sets = self.asset_bundle.get_hit_sets()
        if not hit_sets:
            return frame, None
        preferred = f"kn{(int(knight.knight_id) % 4) + 1}"
        hset = hit_sets.get(preferred) or hit_sets.get("kn4") or hit_sets.get("kn1")
        hit_frame = hset.frames[fidx] if hset is not None and fidx < len(hset.frames) else None
        return frame, hit_frame

    def _monster_frame_info(
        self, archetype: MonsterArchetype, attacking: bool
    ) -> tuple[CelFrame | None, HitFrame | None]:
        if self.asset_bundle is None:
            return None, None
        mcel = self.asset_bundle.get_monster_cel(archetype)
        if mcel is None or not mcel.frames:
            return None, None
        fidx = select_monster_frame(mcel, self.tick_count, attacking)
        frame = mcel.frames[fidx]
        hit_sets = self.asset_bundle.get_hit_sets()
        if not hit_sets:
            return frame, None
        hset = hit_sets.get(_ARCHETYPE_HIT_KEYS.get(archetype, ""))
        hit_frame = hset.frames[fidx] if hset is not None and fidx < len(hset.frames) else None
        return frame, hit_frame

    def _resolve_knight_melee(self, knight: KnightState, active: ActiveEncounter) -> None:
        if knight.action not in _ATTACK_ACTIONS:
            active.swing_spent[knight.knight_id] = False
            return
        if active.swing_spent.get(knight.knight_id, False):
            return
        k_frame, hit_frame = self._knight_frame_info(knight)
        k_w = k_frame.meta.width if k_frame is not None else 0
        k_h = k_frame.meta.height if k_frame is not None else 0
        for monster in active.encounter.monsters:
            if not monster.is_alive:
                continue
            m_frame, _ = self._monster_frame_info(monster.params.archetype, attacking=False)
            if resolve_melee_attack_points(
                attacker_id=knight.entity_id,
                attacker_pos=knight.pos,
                attacker_screen_x=knight.x,
                attacker_screen_y=knight.y - knight.z,
                attacker_cel_width=k_w,
                attacker_cel_height=k_h,
                attacker_flipped=knight.direction.is_left,
                hit_frame=hit_frame,
                defender_id=monster.entity_id,
                defender_pos=monster.pos,
                defender_screen_x=monster.pos.x,
                defender_screen_y=monster.pos.y - monster.pos.z,
                defender_frame=m_frame,
                defender_flipped=monster.direction.is_left,
                fallback_reach_x=_KNIGHT_MELEE_RANGE_X,
            ):
                apply_hit_to_monster(monster, knight.attack_damage(), knight.x)
                active.swing_spent[knight.knight_id] = True
                self._spawn_gore(active, monster.pos.x, monster.pos.y, monster.pos.z)
                self._try_play_sample("swordhit.a", "hit.a", "splat.a")
                break

    def _apply_melee_intent(self, intent: object, active: ActiveEncounter) -> None:
        from pymoonstone.sim.monster_ai import MeleeAttackIntent

        if not isinstance(intent, MeleeAttackIntent):
            return
        attacker = next(
            (m for m in active.encounter.monsters if m.entity_id == intent.attacker_id), None
        )
        m_frame, hit_frame = (
            self._monster_frame_info(attacker.params.archetype, attacking=True)
            if attacker is not None
            else (None, None)
        )
        m_w = m_frame.meta.width if m_frame is not None else 0
        m_h = m_frame.meta.height if m_frame is not None else 0
        for kid in active.knight_ids:
            defender = self.knight_combat[kid]
            k_frame, _ = self._knight_frame_info(defender)
            if resolve_melee_attack_points(
                attacker_id=intent.attacker_id,
                attacker_pos=intent.pos,
                attacker_screen_x=intent.pos.x,
                attacker_screen_y=intent.pos.y - intent.pos.z,
                attacker_cel_width=m_w,
                attacker_cel_height=m_h,
                attacker_flipped=intent.direction.is_left,
                hit_frame=hit_frame,
                defender_id=defender.entity_id,
                defender_pos=defender.pos,
                defender_screen_x=defender.x,
                defender_screen_y=defender.y - defender.z,
                defender_frame=k_frame,
                defender_flipped=defender.direction.is_left,
                depth_threshold=intent.depth_threshold,
                fallback_reach_x=intent.reach_x,
            ):
                self._damage_knight_with_fx(
                    defender, active, intent.damage, intent.attacker_id, intent.pos
                )

    def _damage_knight_with_fx(
        self,
        defender: KnightState,
        active: ActiveEncounter,
        damage: int,
        attacker_id: EntityId,
        attacker_pos: Pos25D,
    ) -> None:
        prev_hp = defender.hp
        apply_knight_damage(
            defender,
            damage,
            attacker_id=attacker_id,
            attacker_pos=attacker_pos,
        )
        if defender.hp < prev_hp:
            self.palette_fx.trigger_blood_flash()
            self._spawn_gore(active, defender.x, defender.y, defender.z)
            self._try_play_sample("hurt.a", "hit.a", "splat.a")

    def _resolve_monster_attacks(
        self,
        active: ActiveEncounter,
        enc_res: object,
    ) -> None:
        from pymoonstone.sim.monster_ai import EncounterTickResult

        if not isinstance(enc_res, EncounterTickResult):
            return
        by_entity = {
            self.knight_combat[kid].entity_id: self.knight_combat[kid] for kid in active.knight_ids
        }
        for mres in enc_res.monster_results:
            if mres.melee_intent is not None:
                self._apply_melee_intent(mres.melee_intent, active)
        for phit in enc_res.projectile_hits:
            if phit.target_id in by_entity:
                self._damage_knight_with_fx(
                    by_entity[phit.target_id],
                    active,
                    phit.damage,
                    phit.owner_id,
                    phit.pos,
                )

    def _finish_practice_combat(self) -> None:
        self.practice_mode = False
        self.active_encounter = None
        self.scene = GameScene.KNIGHT_SELECT
        self.last_status = "PRACTICE COMPLETE"

    def _handle_combat_victory(self, active: ActiveEncounter) -> None:
        if self.practice_mode:
            self._finish_practice_combat()
            return
        lead_id = active.knight_ids[0]
        kstate = self.knight_combat[lead_id]
        if active.trigger.kind == TriggerKind.LAIR_BOSS and active.trigger.lair_index >= 0:
            resolve_lair_victory(self.overworld, lead_id, active.trigger.lair_index)
            distribute_lair_loot(
                kstate,
                active.trigger.lair_index,
                self.overworld.prng,
                overworld=self.overworld,
            )
        else:
            kstate.xp += 25
            kstate.gold += 15
            ow_k = self.overworld.knights[lead_id]
            ow_k.xp = kstate.xp
            ow_k.gold = kstate.gold
        self.active_encounter = None
        self.scene = GameScene.OVERWORLD
        self.last_status = "ENCOUNTER WON"

    def _handle_combat_defeat(self, active: ActiveEncounter) -> None:
        if self.practice_mode:
            self._finish_practice_combat()
            return
        for kid in active.knight_ids:
            kstate = self.knight_combat[kid]
            ow_k = self.overworld.knights[kid]
            ow_k.lives = kstate.lives
            if kstate.lives > 0:
                kstate.hp = kstate.max_hp
                kstate.stamina = kstate.max_stamina
                kstate.action = KnightAction.IDLE
            else:
                ow_k.alive = False
        self.active_encounter = None
        if all(not k.alive for k in self.overworld.knights.values()):
            self.overworld.game_over = True
            self._enter_game_over()
        else:
            self.scene = GameScene.OVERWORLD
            self.last_status = "KNIGHT FALLEN"

    def _check_combat_resolution(self, active: ActiveEncounter) -> None:
        if active.encounter.completed:
            self._handle_combat_victory(active)
        elif all(not self.knight_combat[kid].is_alive for kid in active.knight_ids):
            self._handle_combat_defeat(active)

    def state_hash(self) -> str:
        """Return a deterministic SHA-256 hex digest of the full integer simulation state."""
        knights_payload = [
            {
                "id": int(kid),
                "node": self.overworld.knights[kid].node_id,
                "keys": self.overworld.knights[kid].keys_mask,
                "stones": self.overworld.knights[kid].moonstones_mask,
                "lives": self.overworld.knights[kid].lives,
                "gold": self.overworld.knights[kid].gold,
                "xp": self.overworld.knights[kid].xp,
                "alive": self.overworld.knights[kid].alive,
                "x": self.knight_combat[kid].x,
                "y": self.knight_combat[kid].y,
                "z": self.knight_combat[kid].z,
                "dir": int(self.knight_combat[kid].direction),
                "hp": self.knight_combat[kid].hp,
                "stamina": self.knight_combat[kid].stamina,
                "action": int(self.knight_combat[kid].action),
                "action_timer": self.knight_combat[kid].action_timer,
                "daggers": self.knight_combat[kid].daggers,
            }
            for kid in self.overworld.turn_order
        ]
        encounter_payload: dict[str, object] | None = None
        if self.active_encounter is not None:
            enc = self.active_encounter.encounter
            encounter_payload = {
                "tick": enc.tick_count,
                "wave": enc.current_wave,
                "completed": enc.completed,
                "monsters": [
                    {
                        "id": int(m.entity_id),
                        "state": int(m.state),
                        "hp": m.hp,
                        "x": m.pos.x,
                        "y": m.pos.y,
                        "z": m.pos.z,
                        "dir": int(m.direction),
                    }
                    for m in enc.monsters
                ],
                "projectiles": len(enc.projectiles),
            }
        payload = {
            "tick": self.tick_count,
            "scene": int(self.scene),
            "paused": self.paused,
            "prng": self.overworld.prng.state,
            "day": self.overworld.day,
            "turn_idx": self.overworld.turn_index,
            "total_turns": self.overworld.total_turns,
            "claimed_stones": self.overworld.claimed_moonstones_mask,
            "winner": int(self.overworld.winner) if self.overworld.winner is not None else None,
            "game_over": self.overworld.game_over,
            "dragon": [self.overworld.dragon.node_id, self.overworld.dragon.patrol_index],
            "knights": knights_payload,
            "encounter": encounter_payload,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _render_overworld_hud(self, scene_renderer: SceneRenderer, font: BitmapFont) -> None:
        kid = self.overworld.current_knight_id
        kstate = self.knight_combat[kid]
        moves = legal_moves(self.overworld, kid)
        target = moves[self.selected_move_index % len(moves)] if moves else -1
        ow_k = self.overworld.knights[kid]
        render_overworld_hud(
            scene_renderer.canvas,
            OverworldHudState(
                knight_name=kid.name,
                hp=kstate.hp,
                max_hp=kstate.max_hp,
                gold=ow_k.gold,
                lives=ow_k.lives,
                moon_phase=self.overworld.day_of_cycle,
                turn=self.overworld.total_turns + 1,
                score=ow_k.xp,
            ),
            font,
        )
        font.draw_text(
            scene_renderer.canvas,
            f"NODE:{ow_k.node_id}->{target}",
            104,
            246,
            color_index=31,
        )

    def _render_combat_hud(self, scene_renderer: SceneRenderer, font: BitmapFont) -> None:
        if self.active_encounter is None:
            return
        kid = self.overworld.current_knight_id
        kstate = self.knight_combat[kid]
        monsters = self.active_encounter.encounter.monsters
        enemy = monsters[0] if monsters else None
        render_combat_hud(
            scene_renderer.canvas,
            CombatHudState(
                hp=kstate.hp,
                max_hp=kstate.max_hp,
                stamina=kstate.stamina,
                max_stamina=kstate.max_stamina,
                lives=kstate.lives,
                score=kstate.xp,
                knight_name=kid.name,
                enemy_name=enemy.params.archetype.name if enemy else "",
                enemy_hp=enemy.hp if enemy else 0,
                enemy_max_hp=enemy.params.max_hp if enemy else 0,
            ),
            font,
        )

    def _compose_picture_or_solid(
        self,
        scene_renderer: SceneRenderer,
        *names: str,
        sprites: Sequence[SceneSprite] = (),
        fill_index: int = 1,
    ) -> None:
        if self.asset_bundle is not None:
            pic = self.asset_bundle.get_picture(*names)
            if pic is not None:
                scene_renderer.set_palette(pic.palette)
                scene_renderer.compose_scene(
                    background=pic.pixels[:PLAYFIELD_HEIGHT, :CANVAS_WIDTH],
                    sprites=sprites,
                    border_index=0,
                )
                return
        bg: npt.NDArray[np.uint8] = np.full(
            (PLAYFIELD_HEIGHT, CANVAS_WIDTH), np.uint8(fill_index), dtype=np.uint8
        )
        scene_renderer.set_palette(default_combat_palette())
        scene_renderer.compose_scene(background=bg, sprites=sprites, border_index=0)

    def _append_target_marker(
        self,
        sprites: list[SceneSprite],
        mi_cel: object,
        active_kid: KnightId,
        color_slot: dict[KnightId, int],
    ) -> None:
        from pymoonstone.assets.cel import DecodedCel

        if not isinstance(mi_cel, DecodedCel):
            return
        moves = legal_moves(self.overworld, active_kid)
        if not moves:
            return
        target_id = moves[self.selected_move_index % len(moves)]
        tnode = self.overworld.graph.nodes.get(target_id)
        if tnode is None:
            return
        flag_idx = 15 + color_slot.get(active_kid, 0)
        fsp = make_sprite(mi_cel, flag_idx, x=tnode.x - 5, y=tnode.y - 4, layer=3, order=50)
        if fsp is not None:
            sprites.append(fsp)

    def _overworld_sprites(self) -> list[SceneSprite]:
        if self.asset_bundle is None:
            return []
        sprites: list[SceneSprite] = []
        color_slot = {
            KnightId.BLUE: 0,
            KnightId.YELLOW: 1,
            KnightId.GREEN: 2,
            KnightId.RED: 3,
        }
        mi_cel = self.asset_bundle.get_cel("mi.c")
        if mi_cel is not None:
            active_kid = self.overworld.current_knight_id
            for idx, kid in enumerate(self.overworld.turn_order):
                ow_k = self.overworld.knights[kid]
                if not ow_k.alive:
                    continue
                node = self.overworld.graph.nodes.get(ow_k.node_id)
                px = (node.x - 5) if node is not None else 40 + idx * 32
                py = (node.y - 4) if node is not None else 90
                c_idx = color_slot.get(kid, idx & 3)
                frame_idx = c_idx + (5 if kid == active_kid else 0)
                sp = make_sprite(mi_cel, frame_idx, x=px, y=py, layer=2, order=idx)
                if sp is not None:
                    sprites.append(sp)
            self._append_target_marker(sprites, mi_cel, active_kid, color_slot)
        self._append_dragon_marker(sprites)
        return sprites

    def _append_dragon_marker(self, sprites: list[SceneSprite]) -> None:
        if self.asset_bundle is None:
            return
        dnode = self.overworld.graph.nodes.get(self.overworld.dragon.node_id)
        if dnode is None:
            return
        mi_cel = self.asset_bundle.get_cel("mi.c")
        if mi_cel is not None:
            dframe = 34 + ((self.tick_count // 8) % 5)
            dsp = make_sprite(mi_cel, dframe, x=dnode.x - 12, y=dnode.y - 10, layer=3, order=99)
            if dsp is not None:
                sprites.append(dsp)
                return
        dragon_cel = self.asset_bundle.get_cel("DRAGON1.CEL")
        if dragon_cel is not None:
            dsp = make_sprite(dragon_cel, 0, x=dnode.x, y=dnode.y, layer=3, order=99)
            if dsp is not None:
                sprites.append(dsp)

    def _combat_sprites(self, active: ActiveEncounter) -> list[SceneSprite]:
        sprites: list[SceneSprite] = []
        if self.asset_bundle is None:
            return sprites
        for idx, kid in enumerate(active.knight_ids):
            kstate = self.knight_combat[kid]
            kcel = self.asset_bundle.get_knight_cel(int(kid))
            if kcel is None:
                continue
            fidx = select_knight_frame(kcel, kstate.action, self.tick_count)
            sp = make_sprite(
                kcel,
                fidx,
                x=kstate.x,
                y=kstate.y,
                z=kstate.z,
                flipped=kstate.direction.is_left,
                layer=2,
                order=idx,
            )
            if sp is not None:
                sprites.append(sp)
        for idx, monster in enumerate(active.encounter.monsters):
            if not monster.is_alive:
                continue
            mcel = self.asset_bundle.get_monster_cel(monster.params.archetype)
            if mcel is None:
                continue
            attacking = monster.state in (
                MonsterState.MELEE_ATTACK,
                MonsterState.RANGED_ATTACK,
                MonsterState.BREATH_ATTACK,
            )
            fidx = select_monster_frame(mcel, self.tick_count, attacking)
            sp = make_sprite(
                mcel,
                fidx,
                x=monster.pos.x,
                y=monster.pos.y,
                z=monster.pos.z,
                flipped=monster.direction.is_left,
                layer=2,
                order=10 + idx,
            )
            if sp is not None:
                sprites.append(sp)
        self._append_gore_sprites(sprites, active)
        return sprites

    def _append_gore_sprites(self, sprites: list[SceneSprite], active: ActiveEncounter) -> None:
        if not self.gore_enabled or not active.gore_particles or self.asset_bundle is None:
            return
        blo_cel = self.asset_bundle.get_blood_cel()
        if blo_cel is None:
            return
        for idx, gp in enumerate(active.gore_particles):
            gsp = make_sprite(
                blo_cel, gp.frame_index, x=gp.x, y=gp.y, z=gp.z, layer=3, order=50 + idx
            )
            if gsp is not None:
                sprites.append(gsp)

    def _apply_scene_palette(
        self, scene_renderer: SceneRenderer, base_palette: Sequence[RGBColor]
    ) -> None:
        words = _palette_to_rgb12(base_palette)
        if self._pending_fade_in:
            self._pending_fade_in = False
            self.palette_fx.fade_in(words, divisor=1, from_black=True)
        elif not self.palette_fx.is_fading and not self.palette_fx.is_flashing:
            self.palette_fx.set_palette(words)
        if self.palette_fx.is_fading or self.palette_fx.is_flashing:
            scene_renderer.set_palette_fx(self.palette_fx.current_palette())
        else:
            scene_renderer.set_palette(base_palette)

    def _render_combat_scene(self, scene_renderer: SceneRenderer, font: BitmapFont) -> None:
        active = self.active_encounter
        if active is None:
            self._compose_picture_or_solid(scene_renderer)
            return
        lair_idx = max(0, active.trigger.lair_index)
        arena_loaded = (
            self.asset_bundle.get_combat_arena(lair_idx) if self.asset_bundle is not None else None
        )
        sprites = self._combat_sprites(active)

        if arena_loaded is not None:
            _arena_data, arena_bg, pal = arena_loaded
            self._apply_scene_palette(scene_renderer, pal)
            scene_renderer.compose_scene(background=arena_bg, sprites=sprites, border_index=0)
        else:
            self._compose_picture_or_solid(scene_renderer, sprites=sprites)
        self._render_combat_hud(scene_renderer, font)

    def _render_knight_select_scene(self, scene_renderer: SceneRenderer, font: BitmapFont) -> None:
        menu_rendered = False
        if self.asset_bundle is not None:
            player = self.asset_bundle.get_intro_player()
            if player is not None:
                if self.knight_pick_active:
                    menu_bg, menu_pal = player.build_knight_select(
                        self.asset_bundle, self.knight_pick_slot, self.knight_taken_mask
                    )
                else:
                    menu_bg, menu_pal = player.build_main_menu(
                        self.asset_bundle,
                        self.selected_player_count,
                        self.menu_cursor,
                        gore_on=self.gore_enabled,
                    )
                if menu_bg is not None:
                    self._apply_scene_palette(scene_renderer, menu_pal)
                    scene_renderer.compose_scene(background=menu_bg, border_index=0)
                    menu_rendered = True
        if not menu_rendered:
            self._compose_picture_or_solid(scene_renderer, "ch.piv")
            font.draw_text(
                scene_renderer.canvas,
                f"KNIGHTS: {self.selected_player_count} (ARROWS+ENTER)",
                64,
                236,
                color_index=31,
            )

    def _render_title_scene(self, scene_renderer: SceneRenderer, font: BitmapFont) -> None:
        if self.asset_bundle is not None:
            bg, pal, sprites = build_intro_cinematic_frame(self.asset_bundle, self.tick_count)
            if bg is not None:
                self._apply_scene_palette(scene_renderer, pal)
                scene_renderer.compose_scene(background=bg, sprites=sprites, border_index=0)
                return
        self._compose_picture_or_solid(scene_renderer, "bg1a.PIV", "mindscape")
        font.draw_text(scene_renderer.canvas, "PRESS ENTER", 116, 236, color_index=31)

    def _render_overworld_scene(self, scene_renderer: SceneRenderer, font: BitmapFont) -> None:
        sprites = self._overworld_sprites()
        map_pic = self.asset_bundle.get_test_picture(8) if self.asset_bundle is not None else None
        if map_pic is not None:
            self._apply_scene_palette(scene_renderer, map_pic.palette)
            scene_renderer.compose_scene(
                background=map_pic.pixels[:PLAYFIELD_HEIGHT, :CANVAS_WIDTH],
                sprites=sprites,
                border_index=0,
            )
        else:
            self._compose_picture_or_solid(scene_renderer, "bg2.PIV", sprites=sprites)
        self._render_overworld_hud(scene_renderer, font)

    def _render_location_scene(self, scene_renderer: SceneRenderer, font: BitmapFont) -> None:
        if self.location_menu is not None:
            render_location_menu(
                scene_renderer,
                self.location_menu,
                self.knight_combat[self.location_knight_id],
                self.overworld,
                font,
                bundle=self.asset_bundle,
            )
        else:
            self._compose_picture_or_solid(scene_renderer, "ch.piv")

    def _render_end_scene(self, scene_renderer: SceneRenderer, font: BitmapFont) -> None:
        if self.scene == GameScene.VICTORY:
            self._compose_picture_or_solid(scene_renderer, "WI1.P", "WI2.P")
            font.draw_text(scene_renderer.canvas, "VICTORY!", 128, 236, color_index=30)
        else:
            self._compose_picture_or_solid(scene_renderer, "message.piv")
            font.draw_text(scene_renderer.canvas, "GAME OVER", 124, 236, color_index=15)

    def render_to_scene(self, scene_renderer: SceneRenderer, font: BitmapFont) -> None:
        """Render the active game scene and HUD onto ``scene_renderer``."""
        if self.scene == GameScene.TITLE:
            self._render_title_scene(scene_renderer, font)
        elif self.scene == GameScene.KNIGHT_SELECT:
            self._render_knight_select_scene(scene_renderer, font)
        elif self.scene == GameScene.OVERWORLD:
            self._render_overworld_scene(scene_renderer, font)
        elif self.scene == GameScene.COMBAT:
            self._render_combat_scene(scene_renderer, font)
        elif self.scene == GameScene.LOCATION:
            self._render_location_scene(scene_renderer, font)
        elif self.scene == GameScene.STATUS:
            kid = self.overworld.current_knight_id
            render_status_screen(
                scene_renderer,
                self.knight_combat[kid],
                self.overworld,
                font,
                bundle=self.asset_bundle,
            )
        elif self.scene == GameScene.NEW_DAY:
            render_new_day_screen(scene_renderer, self.overworld, font, bundle=self.asset_bundle)
        else:
            self._render_end_scene(scene_renderer, font)

        if self.palette_fx.is_fading or self.palette_fx.is_flashing:
            scene_renderer.set_palette_fx(self.palette_fx.current_palette())
        scene_renderer.flush_to_fbo()


def run_headless_session(
    *,
    ticks: int = 60,
    seed: int = DEFAULT_SEED,
    players: Sequence[KnightId] = (KnightId.RED,),
    frames: Sequence[InputFrame] = (),
    replay_path: Path | None = None,
    record_path: Path | None = None,
    start_scene: GameScene = GameScene.TITLE,
) -> HeadlessSessionResult:
    """Run a deterministic headless 50 Hz session and optionally record/verify replay."""
    active_seed = seed
    active_players = tuple(players)
    scripted_frames: tuple[InputFrame, ...] = tuple(frames)

    if replay_path is not None:
        loaded = load_replay(replay_path)
        active_seed = loaded.seed
        active_players = loaded.players
        scripted_frames = loaded.frames

    total_ticks = len(scripted_frames) if replay_path is not None else max(0, ticks)
    session = GameSession(seed=active_seed, players=active_players, start_scene=start_scene)
    recorded: list[InputFrame] = []

    for idx in range(total_ticks):
        frame = scripted_frames[idx] if idx < len(scripted_frames) else InputFrame()
        if frame.quit_requested:
            break
        session.step_tick(frame)
        recorded.append(frame)

    recording = ReplayRecording(
        seed=active_seed,
        players=session.players,
        frames=tuple(recorded),
    )
    if record_path is not None:
        save_replay(record_path, recording)

    return HeadlessSessionResult(
        session=session,
        final_hash=session.state_hash(),
        ticks_executed=len(recorded),
        scene=session.scene,
        recording=recording,
    )


def _open_optional_disk_set(adf_dir: Path) -> DiskSet | None:
    images = find_adf_images(adf_dir)
    if len(images) < 3:
        return None
    return DiskSet.from_directory(adf_dir)


def _step_window_ticks(
    ticks: int,
    session: GameSession,
    router: InputRouter,
    replay_rec: ReplayRecording | None,
    replay_idx: int,
    recorded: list[InputFrame],
    max_ticks: int | None,
) -> tuple[bool, int]:
    for _ in range(ticks):
        if replay_rec is not None:
            if replay_idx >= len(replay_rec.frames):
                return False, replay_idx
            frame = replay_rec.frames[replay_idx]
            replay_idx += 1
        else:
            frame = router.sample_frame()
        if frame.quit_requested:
            return False, replay_idx
        session.step_tick(frame)
        recorded.append(frame)
        if max_ticks is not None and session.tick_count >= max_ticks:
            return False, replay_idx
    return True, replay_idx


def run_game_window(
    adf_dir: Path,
    *,
    scale: int = 3,
    seed: int = DEFAULT_SEED,
    record_path: Path | None = None,
    replay_path: Path | None = None,
    max_ticks: int | None = None,
) -> int:
    """Open the interactive ``pygame-ce`` + ModernGL 3.3 Moonstone game window."""
    replay_rec = load_replay(replay_path) if replay_path is not None else None
    active_seed = replay_rec.seed if replay_rec is not None else seed
    active_players = replay_rec.players if replay_rec is not None else (KnightId.RED,)

    disk_set = _open_optional_disk_set(adf_dir)
    audio = AudioEngine(headless=False)
    session = GameSession(
        seed=active_seed,
        players=active_players,
        audio=audio,
        disk_set=disk_set,
    )
    if session.asset_bundle is not None:
        session.asset_bundle.preload_audio(audio)
        title_mod = session.asset_bundle.load_title_music()
        if title_mod is not None:
            audio.play_music(title_mod)

    pygame.init()
    pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 3)
    pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 3)
    pygame.display.gl_set_attribute(pygame.GL_CONTEXT_PROFILE_MASK, pygame.GL_CONTEXT_PROFILE_CORE)
    win_w = CANVAS_WIDTH * max(1, scale)
    win_h = CANVAS_HEIGHT * max(1, scale)
    pygame.display.set_mode((win_w, win_h), pygame.OPENGL | pygame.DOUBLEBUF | pygame.RESIZABLE)

    ctx = moderngl.create_context(require=330)
    base_renderer = MoonstoneRenderer(ctx)
    scene_renderer = SceneRenderer(base_renderer)
    scene_renderer.set_palette(default_combat_palette())
    font = default_bitmap_font()
    router = InputRouter(init_joysticks=True)
    acc = Accumulator50Hz()
    recorded: list[InputFrame] = []
    prev_ns = time.perf_counter_ns()
    replay_idx = 0

    try:
        running = True
        while running:
            for event in pygame.event.get():
                router.process_pygame_event(event)
            now_ns = time.perf_counter_ns()
            ticks = acc.advance(now_ns - prev_ns)
            prev_ns = now_ns
            running, replay_idx = _step_window_ticks(
                ticks, session, router, replay_rec, replay_idx, recorded, max_ticks
            )
            cur_w, cur_h = pygame.display.get_window_size()
            session.render_to_scene(scene_renderer, font)
            base_renderer.present_to_screen((0, 0, cur_w, cur_h), scanlines=True)
            pygame.display.set_caption(
                f"pymoonstone | {session.scene.name} | {session.last_status}"
            )
            pygame.display.flip()
            pygame.time.wait(2)
    finally:
        if record_path is not None:
            save_replay(
                record_path,
                ReplayRecording(seed=active_seed, players=session.players, frames=tuple(recorded)),
            )
        scene_renderer.release()
        base_renderer.release()
        audio.close()
        pygame.quit()
    return 0
