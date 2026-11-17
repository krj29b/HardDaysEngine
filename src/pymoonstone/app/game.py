"""Scene coordinator, headless deterministic session, and 50 Hz game host (T8.3)."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from enum import IntEnum
from typing import TYPE_CHECKING

import moderngl
import numpy as np
import pygame

from pymoonstone.app.audio import AudioEngine
from pymoonstone.app.config import find_adf_images
from pymoonstone.app.fight_runtime import FightLoader, FightRuntime, FightUnavailableError
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
    step_location_menu,
)
from pymoonstone.app.loop import Accumulator50Hz
from pymoonstone.app.map_runtime import MapRuntime
from pymoonstone.app.map_scene import compose_map, draw_calendar_screen, draw_map_menu
from pymoonstone.app.scene_assets import (
    GameAssetBundle,
    build_intro_cinematic_frame,
)
from pymoonstone.app.sheet_screen import SheetScreen
from pymoonstone.assets.adf import DiskSet
from pymoonstone.assets.palette import (
    RGBColor,
    default_combat_palette,
)
from pymoonstone.render.renderer import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    PLAYFIELD_HEIGHT,
    MoonstoneRenderer,
)
from pymoonstone.render.scene import (
    BitmapFont,
    SceneRenderer,
    SceneSprite,
    blit_indexed_sprite,
    default_bitmap_font,
    palette_fx_to_rgb,
)
from pymoonstone.sim.actor import Actor
from pymoonstone.sim.ai_map import computer_frame
from pymoonstone.sim.fight_moves import FIRE
from pymoonstone.sim.fight_setup import SpawnLanes, settle_defeats
from pymoonstone.sim.inventory import Inventory
from pymoonstone.sim.location_state import KnightState
from pymoonstone.sim.locations import (
    perform_stonehenge_ritual,
)
from pymoonstone.sim.m68k import s8, s16, u8, u16
from pymoonstone.sim.map_dragon import (
    automatic_loss,
    settle as settle_dragon,
)
from pymoonstone.sim.overworld import (
    Arrival,
    EncounterTrigger,
    OverworldState,
    TriggerKind,
    map_sprites,
)
from pymoonstone.sim.palette_fx import PaletteFxState, pack_rgb12
from pymoonstone.sim.rewards import transfer_loot
from pymoonstone.sim.rng import DEFAULT_SEED, Rng
from pymoonstone.sim.turns import TurnResult, advance as advance_turn
from pymoonstone.sim.types import KnightId

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    import numpy.typing as npt


_REGION_ARENAS = {0: "gl1.t", 4: "fo1.t", 8: "sw1.t", 12: "wa1.t"}

# Actor ``kind`` of each selectable knight (ARCHITECTURE.md §9.2, §10.13).
_KNIGHT_KINDS: dict[KnightId, int] = {
    KnightId.BLUE: 0,
    KnightId.YELLOW: 1,
    KnightId.GREEN: 2,
    KnightId.RED: 3,
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


@dataclass(slots=True, frozen=True)
class HeadlessSessionResult:
    """Output summary from :func:`run_headless_session`."""

    session: GameSession
    final_hash: str
    ticks_executed: int
    scene: GameScene
    recording: ReplayRecording


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


@dataclass(slots=True)
class ActiveEncounter:
    """Campaign metadata around one authoritative persistent fight (§10.22)."""

    trigger: EncounterTrigger
    runtime: FightRuntime
    knight_ids: tuple[KnightId, ...]


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
        self._end_prev_fire: bool = False
        self.audio = audio
        self.disk_set = disk_set
        self.asset_bundle: GameAssetBundle | None = (
            GameAssetBundle(disk_set) if disk_set is not None else None
        )
        self.knight_combat: dict[KnightId, KnightState] = {
            kid: KnightState(knight_id=kid) for kid in KnightId
        }
        self.actors = [Actor(kind=_KNIGHT_KINDS[kid]) for kid in KnightId]
        self.dragon_actor = Actor()
        self.inventories = {handle: Inventory() for handle in range(1, 6)}
        self.fight_rng = Rng(seed)
        self.overworld = OverworldState(
            self.actors, self.dragon_actor, self.inventories, self.fight_rng, roster
        )
        self.fight_loader: FightLoader | None = None
        self.spawn_lanes = SpawnLanes()
        self.lair_counts: dict[int, int] = {}
        self.bad_luck: int = 0
        self.last_defeat_mask: int = 0
        self.active_encounter: ActiveEncounter | None = None
        self.location_menu: LocationMenuState | None = None
        self.location_knight_id: KnightId = roster[0]
        self.palette_fx: PaletteFxState = PaletteFxState(
            _palette_to_rgb12(default_combat_palette())
        )
        self._pending_fade_in: bool = False
        self.last_status: str = "PRESS ENTER TO BEGIN"
        self._map_key = 0
        self.map_runtime: MapRuntime | None = None
        self._calendar_phase = 0
        self.sheet_screen: SheetScreen | None = None
        self.last_sheet_item = 65535
        if self.asset_bundle is not None:
            self._ensure_map()

    def _reset_campaign(self, count: int, custom_roster: Sequence[KnightId] = ()) -> None:
        clamped = max(1, min(4, count))
        roster = (
            tuple(custom_roster[:clamped])
            if custom_roster
            else tuple(KnightId(i) for i in range(clamped))
        )
        self.players = roster
        self.selected_player_count = len(roster)
        self.knight_combat = {kid: KnightState(knight_id=kid) for kid in KnightId}
        self.active_encounter = None
        self.location_menu = None
        self.sheet_screen = None
        self.last_sheet_item = 65535
        self.location_knight_id = roster[0]
        self.practice_mode = False
        self.actors = [Actor(kind=_KNIGHT_KINDS[kid]) for kid in KnightId]
        self.dragon_actor = Actor()
        self.inventories = {handle: Inventory() for handle in range(1, 6)}
        self.fight_rng = Rng(self.seed)
        self.overworld = OverworldState(
            self.actors, self.dragon_actor, self.inventories, self.fight_rng, roster
        )
        self.spawn_lanes = SpawnLanes()
        self.lair_counts.clear()
        self.bad_luck = self.last_defeat_mask = 0
        self._map_key = 0
        self.map_runtime = None
        self._calendar_phase = 0
        if self.asset_bundle is not None:
            self._ensure_map()

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

    def _picture_target_palette(self, *names: str) -> tuple[RGBColor, ...]:
        get_picture = getattr(self.asset_bundle, "get_picture", None)
        if callable(get_picture) and names:
            pic = get_picture(*names)
            if pic is not None:
                return tuple(pic.palette)
        return default_combat_palette()

    def _overworld_palette(self, base_palette: Sequence[RGBColor]) -> tuple[RGBColor, ...]:
        if self.overworld.colors.active:
            return palette_fx_to_rgb(self.overworld.colors.colors)
        return tuple(base_palette)

    def _overworld_target_palette(self) -> tuple[RGBColor, ...]:
        get_test_picture = getattr(self.asset_bundle, "get_test_picture", None)
        if callable(get_test_picture):
            map_pic = get_test_picture(8)
            if map_pic is not None:
                return self._overworld_palette(map_pic.palette)
        return self._picture_target_palette("bg2.PIV")

    def _begin_fade_in(self, target_palette: Sequence[RGBColor] | None = None) -> None:
        if target_palette is None:
            self._pending_fade_in = True
            return
        self._pending_fade_in = False
        self.palette_fx.fade_in(_palette_to_rgb12(target_palette), divisor=1, from_black=True)

    def _enter_victory(self, status: str) -> None:
        self.scene = GameScene.VICTORY
        self.last_status = status
        self._begin_fade_in(self._picture_target_palette("WI1.P", "WI2.P"))
        self._play_victory_music()

    def _enter_game_over(self) -> None:
        self.scene = GameScene.GAME_OVER
        self.last_status = "GAME OVER"
        self.overworld.colors.active = False
        self.overworld.turns.wait_stage = 0
        self._begin_fade_in(self._picture_target_palette("message.piv"))

    def _step_title(self, primary: PlayerInput) -> None:
        finished = False
        if self.asset_bundle is not None:
            player = self.asset_bundle.get_intro_player()
            finished = player is not None and player.is_finished(self.tick_count)
        if primary.skip_intro or primary.confirm or primary.fire or finished:
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
            self._reenter_map()
            self.last_status = f"DAY {self.overworld.day}"
            self._begin_fade_in(self._overworld_target_palette())
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
            self._reset_campaign(2, (KnightId.BLUE, KnightId.YELLOW))
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
        self._begin_fade_in(self._overworld_target_palette())

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

    def _ensure_map(self) -> bool:
        if self.overworld.rules is not None:
            return True
        if self.asset_bundle is None:
            self.last_status = "ORIGINAL DISKS REQUIRED"
            return False
        self.overworld.install(self.asset_bundle.get_map_rules(), _KNIGHT_KINDS, self.seed)
        self.overworld.turn_rules = self.asset_bundle.get_turn_rules()
        self.overworld.ai_rules = self.asset_bundle.get_ai_map_rules()
        for offset, size, value in self.overworld.turn_rules.dragon_defaults:
            self.dragon_actor.write(offset, size, value)
        for kid in KnightId:
            self._sync_legacy_actor(kid)
        self._start_map_colors()
        return True

    def _start_map_colors(self) -> None:
        if self.asset_bundle is not None:
            picture = self.asset_bundle.get_test_picture(8)
            if picture is not None:
                self.overworld.colors.start(
                    _palette_to_rgb12(picture.palette), self.overworld.data()
                )

    def _sync_legacy_actor(self, kid: KnightId) -> None:
        actor, legacy = self.actors[int(kid)], self.knight_combat[kid]
        legacy.hp, legacy.max_hp = s16(actor.hp), s16(actor.hp_max)
        legacy.strength, legacy.constitution, legacy.endurance = (
            actor.strength,
            actor.constitution,
            actor.endurance,
        )
        legacy.gold, legacy.lives, legacy.daggers = s16(actor.gold), s8(actor.lives), actor.daggers
        legacy.sword_tier, legacy.armour = actor.sword - 21, actor.armour - 27
        legacy.xp = actor.progress
        legacy.keys, legacy.moonstones = (
            self.inventories[int(kid) + 1].byte(20),
            self.inventories[int(kid) + 1].byte(22),
        )

    def _reenter_map(self) -> None:
        self.scene = GameScene.OVERWORLD
        if self._ensure_map():
            self._map_key = 0
            self.overworld.enter()
            self._map_entry_jobs()
            self._start_map_colors()
            self._begin_fade_in(self._overworld_target_palette())

    def _map_entry_jobs(self) -> None:
        world = self.overworld
        rules = world.turn_rules
        if rules is None or self.asset_bundle is None:
            return
        if self.map_runtime is None and s16(world.day) >= s16(rules.dragon.day):
            self.map_runtime = MapRuntime(self.asset_bundle, world, rules)
        if self.map_runtime is not None:
            self.map_runtime.enter(world, self.tick_count)

    def _clear_map_jobs(self) -> None:
        if self.map_runtime is not None:
            self.map_runtime.clear(self.overworld)
        self.overworld.dragon_active = False

    def _map_ready(self) -> bool:
        if self.overworld.game_over:
            if self.overworld.winner is not None:
                self._enter_victory(f"{self.overworld.winner.name} WINS")
            else:
                self._enter_game_over()
            return False
        return self._ensure_map()

    def _step_overworld(self, frame: InputFrame) -> None:
        if not self._map_ready():
            return
        world = self.overworld
        if frame.key:
            self._map_key = frame.key
        world.colors.step(world.data())
        world.phase = (world.phase + 1) % 2
        if world.phase:
            return
        kid = world.current_knight_id
        inp = self._player_input(frame, kid)
        key, self._map_key = self._map_key, 0
        if world.current_actor.kind == 4:
            self._step_computer_map(key)
            return
        if inp.secondary:
            key = ord(" ")
        word = inp.to_joystick_word() | (FIRE if inp.confirm else 0)
        row = world.frame(word, ord("E") if inp.weapon_switch else key)
        if self._map_result(row, key) and self.map_runtime is not None:
            self.map_runtime.advance(world, self.tick_count)

    def _step_computer_map(self, key: int) -> None:
        world = self.overworld
        if world.ai_rules is None:
            raise ValueError("original computer map rules are required")
        row = computer_frame(world, world.ai_rules, key)
        for kid in KnightId:
            self._sync_legacy_actor(kid)
        if row is not None:
            opponent = self.actors[row.key - 1]
            if opponent.frog_days or s8(opponent.lives) <= 0:
                self._computer_meeting_loot(world.current_actor, opponent)
                return
        self.last_status = ""
        if self._map_result(row, ord("Q") if key == ord("Q") else 0) and self.map_runtime:
            self.map_runtime.advance(world, self.tick_count)

    def _computer_meeting_loot(self, winner: Actor, loser: Actor) -> None:
        """Computer settlement shares the original transfer rule (§11.20)."""
        rules = self.overworld.turn_rules
        if rules is None:
            raise ValueError("meeting loot requires original transfer tables")
        winner.progress = u16(winner.progress + 1)
        transfer_loot(winner, loser, self.overworld.ordered_actors(), self.inventories, rules.gifts)
        for kid in KnightId:
            self._sync_legacy_actor(kid)

    def _map_result(self, row: Arrival | None, key: int) -> bool:
        world = self.overworld
        if not world.menu and key == ord("Q"):
            self._enter_game_over()
        elif not world.menu and key == ord(" "):
            self._open_sheet(9)
        elif row is not None:
            self._dispatch_arrival(row)
        elif world.dragon_encounter:
            self._dragon_attack()
        elif world.turn_over:
            self._advance_turns()
        else:
            return not world.menu
        return False

    def _advance_turns(self) -> None:
        world = self.overworld
        if world.turn_rules is None:
            raise ValueError("calendar data is required to advance turns")
        result = advance_turn(world, world.turn_rules)
        for kid in KnightId:
            self._sync_legacy_actor(kid)
        if result == TurnResult.NEXT_DAY:
            self._show_next_day()
        elif result == TurnResult.GAME_OVER:
            self._enter_game_over()
        else:
            self._reenter_map()

    def _show_next_day(self) -> None:
        world = self.overworld
        self.palette_fx.set_palette(
            world.colors.colors or _palette_to_rgb12(self._overworld_target_palette())
        )
        world.colors.active = False
        self.scene = GameScene.NEW_DAY
        self._calendar_phase = 0
        self._map_key = 0
        self.palette_fx.fade_out()

    def _dragon_attack(self) -> None:
        world = self.overworld
        rules = world.turn_rules
        if rules is None:
            raise ValueError("map dragon rules are required")
        world.dragon_encounter = False
        if automatic_loss(world, rules):
            self._clear_map_jobs()
            for kid in KnightId:
                self._sync_legacy_actor(kid)
            self._reenter_map()
            self.last_status = "DRAGON TAKES LOOT"
            return
        self._start_combat(
            EncounterTrigger(
                TriggerKind.DRAGON_INTERCEPT,
                world.current_knight_id,
                -1,
                world.day,
                world.moon_phase,
            ),
            world.current_knight_id,
        )

    def _dispatch_arrival(self, row: Arrival) -> None:
        world, kid = self.overworld, self.overworld.current_knight_id
        if row.kind == 2:
            self._dispatch_lair(row.key - 6)
        elif row.kind in (1, 33):
            self._start_combat(
                EncounterTrigger(
                    TriggerKind.PVP_DUEL,
                    kid,
                    -1,
                    world.day,
                    world.moon_phase,
                    opponent_knight_id=KnightId(row.key - 1),
                ),
                kid,
            )
        elif row.kind in (25, 26):
            self._open_location(
                kid, LocationKind.HIGHWOOD if row.kind == 25 else LocationKind.WATERDEEP
            )
        elif row.kind == 27:
            self._open_stonehenge_or_victory(kid)
        else:
            self.last_status = "PLACE SCREEN UNAVAILABLE"
        if self.scene != GameScene.OVERWORLD:
            world.colors.active = False

    def _dispatch_lair(self, index: int) -> None:
        world, kid = self.overworld, self.overworld.current_knight_id
        lair = world.lairs[index]
        if world.travel or s16(lair.creatures_left) <= 0:
            self._open_sheet(2, lair=index)
            return
        if self.fight_loader is None and self.asset_bundle is not None:
            self.fight_loader = FightLoader(self.asset_bundle)
        if self.fight_loader is None:
            return
        arena = next(a for a in self.fight_loader.defs.arenas if lair.handler in a.handler_offsets)
        trigger = EncounterTrigger(
            TriggerKind.LAIR_BOSS,
            kid,
            -1,
            world.day,
            world.moon_phase,
            lair_index=index,
            creature_type=arena.creature.actor_type or 0,
        )
        self._start_combat(trigger, kid)

    def _open_location(self, knight_id: KnightId, location: LocationKind) -> None:
        if self.overworld.rules is not None:
            self.overworld.setup_turn()
            self._sync_legacy_actor(knight_id)
        self.overworld.colors.active = False
        self._clear_map_jobs()
        self.location_knight_id = knight_id
        self.location_menu = LocationMenuState(location=location)
        self.scene = GameScene.LOCATION
        self.last_status = location.name

    def _open_stonehenge_or_victory(self, knight_id: KnightId) -> None:
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
                loc_kind = LocationKind.WATERDEEP if trig.node_id == 26 else LocationKind.HIGHWOOD
                self._open_location(knight_id, loc_kind)
                return
            if trig.kind == TriggerKind.STONEHENGE_RITUAL:
                self._open_stonehenge_or_victory(knight_id)
                return
            if trig.kind in (
                TriggerKind.WANDERING_MONSTER,
                TriggerKind.LAIR_BOSS,
                TriggerKind.DRAGON_INTERCEPT,
                TriggerKind.PVP_DUEL,
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
        if self.overworld.rules is not None:
            self._prepare_fight_knight(kid)
        if res.victory:
            self.location_menu = None
            self._enter_victory(f"{kid.name} WINS")
            return
        if res.left_location:
            self.location_menu = None
            self._reenter_map()
            self.last_status = f"DAY {self.overworld.day}"

    def _step_status(self, frame: InputFrame) -> None:
        screen = self.sheet_screen
        if screen is None:
            self._open_sheet(9)
            return
        kid = KnightId(screen.state.knight - 1)
        done = screen.step(self.overworld, self._player_input(frame, kid))
        self.bad_luck |= screen.state.bad_luck
        for knight in KnightId:
            self._sync_legacy_actor(knight)
        if done:
            self._close_sheet()

    def _open_sheet(self, number: int, *, lair: int = -1, other: int = 0) -> None:
        if self.asset_bundle is None:
            self.scene = GameScene.OVERWORLD
            self.last_status = "ORIGINAL DISKS REQUIRED FOR SHEETS"
            return
        self.overworld.colors.active = False
        self.sheet_screen = SheetScreen(
            self.asset_bundle,
            self.overworld,
            number,
            knight=int(self.overworld.current_knight_id) + 1,
            lair=lair,
            other=other,
        )
        self.sheet_screen.state.last_item = self.last_sheet_item
        self.scene = GameScene.STATUS
        self._begin_fade_in(palette_fx_to_rgb(self.sheet_screen.palette(self.overworld)))

    def _close_sheet(self) -> None:
        screen = self.sheet_screen
        if screen is None:
            raise ValueError("no shared sheet to close")
        self.last_sheet_item = screen.state.last_item
        if screen.state.lair >= 0:
            if screen.state.read_only:
                self.overworld.restore_position()
            else:
                self.overworld.lairs[screen.state.lair].tidy()
        self.sheet_screen = None
        self._reenter_map()
        self.last_status = f"DAY {self.overworld.day}"

    def _step_new_day(self, primary: PlayerInput) -> None:
        if self.palette_fx.is_fading:
            return
        if self._calendar_phase == 0:
            self._calendar_phase = 1
            self._begin_fade_in(self._overworld_target_palette())
            return
        if self.overworld.turns.wait_fire(primary.fire or primary.confirm):
            self.palette_fx.set_palette([0] * 32)
            self._advance_turns()

    def _step_end_screen(self, primary: PlayerInput) -> None:
        if self.scene == GameScene.GAME_OVER and self.overworld.turn_rules is not None:
            if not self.palette_fx.is_fading and self.overworld.turns.wait_fire(
                primary.fire or primary.confirm
            ):
                self._clear_map_jobs()
                self.scene = GameScene.TITLE
                self._play_title_music()
            return
        fire_edge = primary.fire and not self._end_prev_fire
        self._end_prev_fire = primary.fire
        if primary.confirm or fire_edge:
            self._reset_campaign(self.selected_player_count)
            self.scene = GameScene.KNIGHT_SELECT
            self.last_status = "SELECT KNIGHTS"
            self._pending_fade_in = True
            self._play_title_music()

    def _arena_name_for_trigger(self, trigger: EncounterTrigger) -> str:
        if trigger.kind == TriggerKind.LAIR_BOSS and self.overworld.lairs:
            return self.overworld.lairs[trigger.lair_index].arena
        return _REGION_ARENAS.get(self.overworld.region_code, "fo1.t")

    def _finish_practice_combat(self) -> None:
        self.practice_mode = False
        self.active_encounter = None
        self.scene = GameScene.TITLE
        self.last_status = "PRACTICE COMPLETE"

    def _fight_runtime(self, trigger: EncounterTrigger, knight_id: KnightId) -> ActiveEncounter:
        if self.asset_bundle is None:
            raise FightUnavailableError("original disks are required for fights")
        if self.fight_loader is None:
            self.fight_loader = FightLoader(self.asset_bundle)
        loader = self.fight_loader
        first = int(knight_id) + 1
        background = self._arena_name_for_trigger(trigger)
        args = (self.actors, self.dragon_actor, self.inventories, self.fight_rng)
        if self.practice_mode:
            second_id = self.players[1]
            self._prepare_fight_knights(knight_id, second_id)
            runtime = loader.duel(
                background,
                *args,
                first,
                int(second_id) + 1,
                lanes=self.spawn_lanes,
                moon_frame=self.overworld.moon_frame,
            )
            return ActiveEncounter(trigger, runtime, (knight_id, second_id))
        if trigger.kind == TriggerKind.DRAGON_INTERCEPT:
            self._prepare_fight_knights(knight_id)
            runtime = loader.creature(
                loader.arena_for(0x14),
                background,
                *args,
                first,
                self.overworld.moon_frame,
                lanes=self.spawn_lanes,
            )
            return ActiveEncounter(trigger, runtime, (knight_id,))
        if trigger.kind == TriggerKind.PVP_DUEL:
            opponent_id = trigger.opponent_knight_id
            if opponent_id is None:
                raise FightUnavailableError("duel opponent is missing")
            self._prepare_fight_knights(knight_id, opponent_id)
            runtime = loader.duel(
                background,
                *args,
                first,
                int(opponent_id) + 1,
                lanes=self.spawn_lanes,
                day=self.overworld.day,
                moon_frame=self.overworld.moon_frame,
                computers=frozenset(
                    int(kid) + 1 for kid in (knight_id, opponent_id) if kid not in self.players
                ),
            )
            return ActiveEncounter(trigger, runtime, (knight_id, opponent_id))
        arena = loader.arena_for(trigger.creature_type)
        lair_count = self._fight_lair_count(trigger, loader)
        self._prepare_fight_knights(knight_id)
        runtime = loader.creature(
            arena,
            background,
            *args,
            first,
            self.overworld.moon_frame,
            lair_count=lair_count,
            lanes=self.spawn_lanes,
        )
        return ActiveEncounter(trigger, runtime, (knight_id,))

    def _fight_lair_count(self, trigger: EncounterTrigger, loader: FightLoader) -> int | None:
        if trigger.kind != TriggerKind.LAIR_BOSS:
            return None
        if not 0 <= trigger.lair_index < len(loader.defs.lairs):
            raise FightUnavailableError("invalid lair index")
        if self.overworld.lairs:
            return self.overworld.lairs[trigger.lair_index].creatures_left
        return self.lair_counts.setdefault(
            trigger.lair_index, loader.defs.lairs[trigger.lair_index].creatures_left
        )

    def _prepare_fight_knights(self, *kids: KnightId) -> None:
        for kid in kids:
            if self.overworld.rules is not None and self.actors[int(kid)].kind == 4:
                self._sync_legacy_actor(kid)
            else:
                self._prepare_fight_knight(kid)

    def _prepare_fight_knight(self, kid: KnightId) -> None:
        """Translate only the legacy sheet values at fight entry (§10.22)."""
        legacy, actor = self.knight_combat[kid], self.actors[int(kid)]
        actor.hp, actor.hp_max = u16(legacy.hp), u16(legacy.max_hp)
        actor.strength, actor.constitution, actor.endurance = (
            u8(legacy.strength),
            u8(legacy.constitution),
            u8(legacy.endurance),
        )
        actor.sword = 0x16 + max(0, min(3, legacy.sword_tier - 1))
        actor.armour = 0x1B + max(0, min(3, legacy.armour))
        actor.daggers, actor.lives, actor.gold = (
            u8(legacy.daggers),
            u8(legacy.lives),
            u16(legacy.gold),
        )
        actor.inventory = int(kid) + 1
        inventory = self.inventories[actor.inventory]
        inventory.counts[0x14 // 2] = u8(legacy.keys)
        inventory.counts[0x16 // 2] = u8(legacy.moonstones)

    def _start_combat(self, trigger: EncounterTrigger, knight_id: KnightId) -> None:
        self._clear_map_jobs()
        try:
            active = self._fight_runtime(trigger, knight_id)
        except FightUnavailableError as exc:
            self.last_status = f"FIGHT UNAVAILABLE: {exc}"
            self.practice_mode = False
            return
        active.runtime.fight.engine.gore_off = not self.gore_enabled
        self.active_encounter = active
        self.overworld.colors.active = False
        self.scene = GameScene.COMBAT
        self._begin_fade_in(active.runtime.scene.palette)
        self.last_status = "COMBAT"

    def _step_combat(self, frame: InputFrame) -> None:
        active = self.active_encounter
        if active is None:
            self.scene = GameScene.OVERWORLD
            return
        ports = [0, 0]
        for kid in active.knight_ids:
            if kid not in self.players:
                continue
            actor = self.actors[int(kid)]
            port = 0 if actor.input_port == 1 else 1
            ports[port] = self._player_input(frame, kid).to_joystick_word()
        output = active.runtime.step_vbl(self.tick_count, ports)
        if output is not None and self.audio is not None:
            self.audio.handle_fight_sounds((*output.sounds, *output.tick.sounds))
        if active.runtime.finished:
            self._finish_combat(active)

    def _finish_combat(self, active: ActiveEncounter) -> None:
        """Settle original defeat/lair records; original caller sheets remain T11.15 (§10.24)."""
        fight = active.runtime.fight
        self.last_defeat_mask = settle_defeats(fight, active.runtime.opponent)
        won = not self.last_defeat_mask & 1
        self._settle_map_encounter(active, won)
        self.bad_luck = 0
        active.runtime.release()
        if self.practice_mode:
            self._finish_practice_combat()
            return
        self.active_encounter = None
        self.overworld.spent = self.overworld.budget
        if active.trigger.kind == TriggerKind.LAIR_BOSS and self.overworld.lairs:
            self._open_sheet(2 if won else 9, lair=active.trigger.lair_index if won else -1)
            return
        if self.overworld.turn_rules is None and all(
            not k.alive for k in self.overworld.knights.values()
        ):
            self.overworld.game_over = True
            self._enter_game_over()
        else:
            self._return_from_map_fight(won)

    def _settle_map_encounter(self, active: ActiveEncounter, won: bool) -> None:
        fight = active.runtime.fight
        self._settle_computer_duel(active)
        for kid in active.knight_ids:
            actor, legacy = self.actors[int(kid)], self.knight_combat[kid]
            legacy.hp, legacy.daggers, legacy.lives = s16(actor.hp), actor.daggers, s8(actor.lives)
            ow = self.overworld.knights.get(kid)
            if ow is not None:
                ow.lives = legacy.lives
        if active.trigger.kind == TriggerKind.LAIR_BOSS:
            self.lair_counts[active.trigger.lair_index] = fight.total
            if self.overworld.lairs:
                self.overworld.lairs[active.trigger.lair_index].creatures_left = fight.total
            if won:
                actor = fight.actor(active.runtime.first)
                actor.progress = u16(actor.progress + 1)
        if (
            active.trigger.kind == TriggerKind.DRAGON_INTERCEPT
            and self.overworld.turn_rules is not None
        ):
            settle_dragon(self.overworld, self.overworld.turn_rules, lost=not won)
            for kid in KnightId:
                self._sync_legacy_actor(kid)

    def _settle_computer_duel(self, active: ActiveEncounter) -> None:
        if self.practice_mode or active.trigger.kind != TriggerKind.PVP_DUEL:
            return
        if self.last_defeat_mask not in (1, 2):
            return
        first = active.runtime.fight.actor(active.runtime.first)
        second = active.runtime.fight.actor(active.runtime.opponent)
        winner, loser = (second, first) if self.last_defeat_mask == 1 else (first, second)
        if winner.kind == 4:
            self._computer_meeting_loot(winner, loser)

    def _return_from_map_fight(self, won: bool) -> None:
        self.scene = GameScene.OVERWORLD
        if self.overworld.rules is not None:
            self._reenter_map()
        self.last_status = "FIGHT WON" if won else "KNIGHT FALLEN"

    def _render_combat_scene(self, scene_renderer: SceneRenderer, font: BitmapFont) -> None:
        if self.active_encounter is None:
            self._compose_picture_or_solid(scene_renderer)
        else:
            self.active_encounter.runtime.scene.render(scene_renderer)

    def state_hash(self) -> str:
        """Hash every mutable map/actor/fight cell, independent of rendering (§11.18)."""
        world = asdict(self.overworld)
        world.pop("rules")
        world.pop("turn_rules")
        world.pop("ai_rules")
        payload = {
            "tick": self.tick_count,
            "scene": int(self.scene),
            "paused": self.paused,
            "world": world,
            "map_key": self._map_key,
            "calendar_phase": self._calendar_phase,
            "sheet": asdict(self.sheet_screen.state) if self.sheet_screen else None,
            "last_sheet_item": self.last_sheet_item,
            "palette": self.palette_fx.snapshot(),
            "pending_fade": self._pending_fade_in,
            "map_jobs": self.map_runtime.snapshot() if self.map_runtime else None,
            "legacy": [asdict(self.knight_combat[kid]) for kid in KnightId],
            "encounter": self.active_encounter.runtime.snapshot()
            if self.active_encounter
            else None,
            "fight_lanes": self.spawn_lanes.counter,
            "flyer_toggle": self.fight_loader.flyer.toggle if self.fight_loader else 0,
            "drake_cells": asdict(self.fight_loader.drake) if self.fight_loader else None,
            "lair_counts": self.lair_counts,
            "bad_luck": self.bad_luck,
            "defeats": self.last_defeat_mask,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def _compose_picture_or_solid(
        self,
        scene_renderer: SceneRenderer,
        *names: str,
        sprites: Sequence[SceneSprite] = (),
        fill_index: int = 1,
        palette_override: Sequence[RGBColor] | None = None,
    ) -> None:
        if self.asset_bundle is not None:
            pic = self.asset_bundle.get_picture(*names)
            if pic is not None:
                pal = palette_override if palette_override is not None else pic.palette
                self._apply_scene_palette(scene_renderer, pal)
                scene_renderer.compose_scene(
                    background=pic.pixels[:PLAYFIELD_HEIGHT, :CANVAS_WIDTH],
                    sprites=sprites,
                    border_index=0,
                )
                return
        bg: npt.NDArray[np.uint8] = np.full(
            (PLAYFIELD_HEIGHT, CANVAS_WIDTH), np.uint8(fill_index), dtype=np.uint8
        )
        pal = palette_override if palette_override is not None else default_combat_palette()
        self._apply_scene_palette(scene_renderer, pal)
        scene_renderer.compose_scene(background=bg, sprites=sprites, border_index=0)

    def _apply_scene_palette(
        self, scene_renderer: SceneRenderer, base_palette: Sequence[RGBColor]
    ) -> None:
        words = _palette_to_rgb12(base_palette)
        if self._pending_fade_in:
            self._pending_fade_in = False
            self.palette_fx.fade_in(words, divisor=1, from_black=True)
            self.palette_fx.step_vbl()
        elif not self.palette_fx.is_fading and not self.palette_fx.is_flashing:
            self.palette_fx.set_palette(words)
        if self.palette_fx.is_fading or self.palette_fx.is_flashing:
            scene_renderer.set_palette_fx(self.palette_fx.current_palette())
        else:
            scene_renderer.set_palette(base_palette)

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
        if self.overworld.rules is None or self.asset_bundle is None:
            self._compose_picture_or_solid(scene_renderer)
            return
        world = self.overworld
        picture, cel = self.asset_bundle.get_test_picture(8), self.asset_bundle.get_cel("mi.c")
        if picture is None or cel is None:
            raise ValueError("map picture and sprites are required")
        scene_renderer.set_palette(self._overworld_palette(picture.palette))
        compose_map(scene_renderer.canvas, picture.pixels, cel, map_sprites(world))
        if world.dragon_active and self.map_runtime is not None:
            blit_indexed_sprite(
                scene_renderer.canvas, self.map_runtime.scene.canvas.pixels(), 0, 28
            )
        if world.menu:
            draw_map_menu(scene_renderer.canvas, cel, world, self.asset_bundle)

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
        if self.scene == GameScene.GAME_OVER and self.overworld.turn_rules is not None:
            self._render_calendar(scene_renderer, farewell=True)
            return
        if self.scene == GameScene.VICTORY:
            self._compose_picture_or_solid(scene_renderer, "WI1.P", "WI2.P")
            font.draw_text(scene_renderer.canvas, "VICTORY!", 128, 236, color_index=30)
        else:
            self._compose_picture_or_solid(scene_renderer, "message.piv")
            font.draw_text(scene_renderer.canvas, "GAME OVER", 124, 236, color_index=15)

    def _render_new_day(self, renderer: SceneRenderer, font: BitmapFont) -> None:
        if self._calendar_phase == 0:
            self._render_overworld_scene(renderer, font)
        else:
            self._render_calendar(renderer)

    def _render_calendar(self, renderer: SceneRenderer, *, farewell: bool = False) -> None:
        if self.asset_bundle is None:
            raise ValueError("original assets are required for calendar screens")
        draw_calendar_screen(renderer.canvas, self.asset_bundle, self.overworld, farewell=farewell)
        palette = (
            self._picture_target_palette("message.piv")
            if farewell
            else self._overworld_target_palette()
        )
        renderer.set_palette(palette)

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
            self._render_sheet_scene(scene_renderer)
        elif self.scene == GameScene.NEW_DAY:
            self._render_new_day(scene_renderer, font)
        else:
            self._render_end_scene(scene_renderer, font)

        if self.palette_fx.is_fading or self.palette_fx.is_flashing:
            scene_renderer.set_palette_fx(self.palette_fx.current_palette())
        scene_renderer.flush_to_fbo()

    def _render_sheet_scene(self, renderer: SceneRenderer) -> None:
        if self.sheet_screen is None:
            raise ValueError("status scene has no shared sheet")
        self.sheet_screen.render(renderer, self.overworld)


def run_headless_session(
    *,
    ticks: int = 60,
    seed: int = DEFAULT_SEED,
    players: Sequence[KnightId] = (KnightId.RED,),
    frames: Sequence[InputFrame] = (),
    replay_path: Path | None = None,
    record_path: Path | None = None,
    start_scene: GameScene = GameScene.TITLE,
    adf_dir: Path | None = None,
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
    disk_set = _open_optional_disk_set(adf_dir) if adf_dir is not None else None
    session = GameSession(
        seed=active_seed, players=active_players, start_scene=start_scene, disk_set=disk_set
    )
    recorded: list[InputFrame] = []

    for idx in range(total_ticks):
        frame = scripted_frames[idx] if idx < len(scripted_frames) else InputFrame()
        if frame.quit_requested:
            break
        session.step_tick(frame)
        recorded.append(frame)

    recording = ReplayRecording(
        seed=active_seed,
        players=active_players,
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
                ReplayRecording(seed=active_seed, players=active_players, frames=tuple(recorded)),
            )
        scene_renderer.release()
        base_renderer.release()
        audio.close()
        pygame.quit()
    return 0
