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

from harddaysengine.app.audio import AudioEngine
from harddaysengine.app.config import find_adf_images
from harddaysengine.app.dice_screen import DiceScreen
from harddaysengine.app.ending_screen import EndingScreen
from harddaysengine.app.fight_runtime import FightLoader, FightRuntime, FightUnavailableError
from harddaysengine.app.input import (
    InputFrame,
    InputRouter,
    PlayerInput,
    ReplayRecording,
    load_replay,
    save_replay,
)
from harddaysengine.app.loop import RefreshPacer
from harddaysengine.app.map_runtime import MapRuntime
from harddaysengine.app.map_scene import compose_map, draw_calendar_screen, draw_map_menu
from harddaysengine.app.meeting_screen import MeetingScreen
from harddaysengine.app.scene_assets import (
    GameAssetBundle,
    build_intro_cinematic_frame,
)
from harddaysengine.app.service_screen import ServiceScreen
from harddaysengine.app.sheet_screen import SheetScreen
from harddaysengine.app.stonehenge_screen import StoneScreen
from harddaysengine.app.town_screen import TownScreen
from harddaysengine.app.valley_screen import ValleyScreen
from harddaysengine.app.window import WindowController
from harddaysengine.app.wizard_screen import WizardScreen
from harddaysengine.assets.adf import DiskSet
from harddaysengine.assets.dice_house_data import SCRIPT_HUNK as DICE_SCRIPT_HUNK, parse_dice_house
from harddaysengine.assets.fight_program import cel_shape, fight_constants, script_image
from harddaysengine.assets.hunk import parse_hunk_executable
from harddaysengine.assets.mog_code import MogCode
from harddaysengine.assets.palette import (
    RGBColor,
    default_combat_palette,
)
from harddaysengine.assets.service_data import parse_service_rules
from harddaysengine.assets.service_screen_data import parse_service_screens
from harddaysengine.assets.sound_rules import parse_sound_rules
from harddaysengine.assets.village_data import parse_village_rules
from harddaysengine.render.presentation import DisplaySettings
from harddaysengine.render.renderer import (
    CANVAS_WIDTH,
    PLAYFIELD_HEIGHT,
    HardDaysRenderer,
)
from harddaysengine.render.scene import (
    BitmapFont,
    SceneRenderer,
    SceneSprite,
    blit_indexed_sprite,
    default_bitmap_font,
    palette_fx_to_rgb,
)
from harddaysengine.render.viewport import initial_window_size
from harddaysengine.sim.actor import Actor
from harddaysengine.sim.ai_map import computer_frame
from harddaysengine.sim.dice_house import DiceEngine, DiceWorld, can_enter
from harddaysengine.sim.fight_moves import FIRE
from harddaysengine.sim.fight_setup import SpawnLanes, settle_defeats
from harddaysengine.sim.inventory import Inventory
from harddaysengine.sim.location_state import KnightState
from harddaysengine.sim.m68k import s8, s16, u8, u16
from harddaysengine.sim.map_dragon import (
    automatic_loss,
    settle as settle_dragon,
)
from harddaysengine.sim.meeting import (
    MeetingAction,
    MeetingPhase,
    after_protection,
    decide as meeting_decide,
)
from harddaysengine.sim.overworld import (
    Arrival,
    EncounterTrigger,
    OverworldState,
    TriggerKind,
    map_sprites,
)
from harddaysengine.sim.palette_fx import PaletteFxState, pack_rgb12
from harddaysengine.sim.rewards import transfer_loot, wizard_gift
from harddaysengine.sim.rng import DEFAULT_SEED, Rng
from harddaysengine.sim.service_screen import ServiceWorld
from harddaysengine.sim.service_screen_types import ServiceKind
from harddaysengine.sim.stock import initialize as initialize_stock
from harddaysengine.sim.stonehenge import EndingRequest, StonePhase, bless
from harddaysengine.sim.towns import TownAction, TownEntry
from harddaysengine.sim.turns import TurnResult, advance as advance_turn
from harddaysengine.sim.types import KnightId
from harddaysengine.sim.valley import (
    ValleyPhase,
    defeat as valley_defeat,
    moonstone as valley_moonstone,
    victory as valley_victory,
)
from harddaysengine.sim.village import VillagePhase, VillageVisit, reward as village_reward
from harddaysengine.sim.wizard import WizardPhase

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    import numpy.typing as npt

    from harddaysengine.sim.dice_house import DiceHouse
    from harddaysengine.sim.service_screen_types import ServiceScreens
    from harddaysengine.sim.service_types import DiceRules, HealerRules, MysticRules
    from harddaysengine.sim.sound import SoundRequest
    from harddaysengine.sim.turn_rules import TurnRules

    ServiceRules = tuple[HealerRules, MysticRules, DiceRules]


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
    ENDING = 4
    GAME_OVER = 5
    LOCATION = 6
    STATUS = 7
    NEW_DAY = 8
    SERVICE = 9
    DICE = 10
    STONEHENGE = 11
    WIZARD = 12
    VILLAGE = 13
    VALLEY = 14
    MEETING = 15


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
        self.town_screen: TownScreen | None = None
        self.stone_screen: StoneScreen | None = None
        self.ending_screen: EndingScreen | None = None
        self.wizard_screen: WizardScreen | None = None
        self.village_visit: VillageVisit | None = None
        self.valley_screen: ValleyScreen | None = None
        self.meeting_screen: MeetingScreen | None = None
        self.loading_cycle = 0
        self.palette_fx: PaletteFxState = PaletteFxState(
            _palette_to_rgb12(default_combat_palette())
        )
        self.last_status: str = "PRESS ENTER TO BEGIN"
        self._map_key = 0
        self.map_runtime: MapRuntime | None = None
        self._calendar_phase = 0
        self.sheet_screen: SheetScreen | None = None
        self.service_screen: ServiceScreen | None = None
        self.dice_screen: DiceScreen | None = None
        self._dice_data: tuple[DiceHouse, DiceEngine] | None = None
        self._service_data: tuple[ServiceScreens, ServiceRules] | None = None
        self.last_sheet_item = 65535
        self._sound_fade_ticks = 0
        self._sound_return = ""
        if self.asset_bundle is not None:
            self._ensure_map()
            if self.audio is not None:
                self.asset_bundle.attach_synth(self.audio)
                if self.scene == GameScene.TITLE:
                    self._play_title_music()
                elif self.scene == GameScene.OVERWORLD:
                    self._map_music()

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
        self.town_screen = None
        self.stone_screen = None
        self.ending_screen = None
        self.wizard_screen = None
        self.village_visit = None
        self.valley_screen = None
        self.meeting_screen = None
        self.loading_cycle = 0
        self.sheet_screen = None
        self.service_screen = None
        self.dice_screen = None
        self.last_sheet_item = 65535
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

    @property
    def blocking_fade(self) -> bool:
        """Whether an original silent OUT still owns its retained caller (§6.3.2)."""
        return bool(self._sound_return or self._sound_fade_ticks)

    def step_tick(self, frame: InputFrame) -> None:
        """Advance the game session by exactly one 50 Hz PAL VBL tick."""
        self.tick_count += 1
        primary = self._player_input(frame, self.players[0])
        if (
            primary.pause
            and not self._sound_return
            and self.scene in (GameScene.OVERWORLD, GameScene.COMBAT)
        ):
            self.paused = not self.paused
            return
        if self.paused:
            return

        self._advance_sound_fade()
        self._step_active_scene(frame, primary)
        self.palette_fx.step_vbl()
        if self.scene == GameScene.LOCATION:
            self._advance_town_entry()
        if self.audio is not None:
            self.audio.step_vbl()

    def _step_active_scene(self, frame: InputFrame, primary: PlayerInput) -> None:
        if self._sound_return:
            self._continue_sound_return()
            return
        scenes: dict[GameScene, Callable[[], None]] = {
            GameScene.TITLE: lambda: self._step_title(primary),
            GameScene.KNIGHT_SELECT: lambda: self._step_knight_select(primary),
            GameScene.OVERWORLD: lambda: self._step_overworld(frame),
            GameScene.COMBAT: lambda: self._step_combat(frame),
            GameScene.LOCATION: lambda: self._step_location(frame),
            GameScene.STATUS: lambda: self._step_status(frame),
            GameScene.NEW_DAY: lambda: self._step_new_day(primary),
            GameScene.SERVICE: lambda: self._step_service(frame),
            GameScene.DICE: lambda: self._step_dice(frame),
            GameScene.STONEHENGE: lambda: self._step_stone(frame),
            GameScene.ENDING: self._step_ending,
            GameScene.WIZARD: lambda: self._step_wizard(frame),
            GameScene.VILLAGE: self._step_village,
            GameScene.VALLEY: lambda: self._step_valley(frame),
            GameScene.MEETING: lambda: self._step_meeting(frame),
        }
        scenes.get(self.scene, lambda: self._step_end_screen(primary))()

    def _load_sound_bank(self, name: str) -> None:
        if self.audio is not None and self.asset_bundle is not None:
            if self.audio.module_player is not None:
                self.asset_bundle.attach_synth(self.audio)
            self.asset_bundle.load_sound_bank(self.audio, name)

    def _map_music(self) -> None:
        if self.audio is not None and self.asset_bundle is not None:
            self._play_requests(self.asset_bundle.get_scene_sounds().map_music)

    def _silent_sound_fade(self) -> None:
        """Set the original silent-fade flag for its full blocking wait (§6.3.2)."""
        if self.asset_bundle is not None:
            self._sound_fade_ticks = self.asset_bundle.get_scene_sounds().fade_wait

    def _advance_sound_fade(self) -> None:
        if self._sound_fade_ticks:
            self._sound_fade_ticks -= 1
            if self.audio is not None:
                data = self.asset_bundle.get_scene_sounds() if self.asset_bundle else None
                if data is not None and self._sound_fade_ticks % data.fade_period == 0:
                    self.audio.fade_step(True)
                if not self._sound_fade_ticks:
                    self.audio.stop_all()
        elif self.audio is not None and self.palette_fx.is_fading:
            self.audio.fade_step(False)

    def _begin_sound_return(self, action: str) -> None:
        """Keep the current screen while its original silent OUT runs (§6.3.2)."""
        if self.asset_bundle is None:
            raise ValueError("silent screen return requires original data")
        self._sound_return = action
        self._silent_sound_fade()
        self.palette_fx.fade_out(divisor=self.asset_bundle.get_scene_sounds().fade_period)

    def _continue_sound_return(self) -> None:
        if self._sound_fade_ticks:
            return
        action, self._sound_return = self._sound_return, ""
        callbacks = {
            "service": self._close_service,
            "dice": self._close_service,
            "fight": self._complete_combat,
            "sheet": self._complete_sheet_return,
            "title": self._return_to_title,
            "town": self._complete_town_return,
        }
        callbacks[action]()

    def _return_to_title(self) -> None:
        self._clear_map_jobs()
        self.scene = GameScene.TITLE
        self._play_title_music()

    def _complete_sheet_return(self) -> None:
        self.sheet_screen = None
        self._reenter_map()

    def _complete_town_return(self) -> None:
        self.town_screen = None
        self._reenter_map()

    def _play_title_music(self) -> None:
        if self.audio is not None and self.asset_bundle is not None:
            mod = self.asset_bundle.load_title_music()
            if mod is not None:
                self.audio.play_music(mod)

    def _play_ending_music(self) -> None:
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

    def _calendar_target_palette(self) -> tuple[RGBColor, ...]:
        """The "Next Day" palette: ``ch.piv`` colours 0..15 over the map's 16..31 (§11.12).

        Decoding the 4-plane picture rewrites only the first 16 words of the shared picture
        palette, so the upper half keeps the map picture's colours (the moon cel uses them).
        """
        picture = self._picture_target_palette("ch.piv")
        return tuple(picture[:16]) + tuple(self._overworld_target_palette()[16:32])

    def _begin_fade_in(self, target_palette: Sequence[RGBColor]) -> None:
        self.palette_fx.fade_in(_palette_to_rgb12(target_palette), divisor=1, from_black=True)

    def _menu_target_palette(self) -> tuple[RGBColor, ...]:
        """Resolve the menu fade during the tick, independently of rendering (§12.41)."""
        if self.asset_bundle is not None:
            player = self.asset_bundle.get_intro_player()
            if player is not None:
                _, palette = player.build_main_menu(
                    self.asset_bundle,
                    self.selected_player_count,
                    self.menu_cursor,
                    gore_on=self.gore_enabled,
                )
                return palette
        return self._picture_target_palette("ch.piv")

    def _enter_ending(self, request: EndingRequest) -> None:
        """Hand over to the program ending pass with the boot flags (§12.39.10)."""
        if self.asset_bundle is None:
            raise ValueError("the ending needs the original disks")
        self.ending_screen = EndingScreen(self.asset_bundle, request.flags)
        if self.audio is not None:
            self.audio.stop_all()
        self.palette_fx.set_palette([0] * 32)
        self.palette_fx.clear_flash()
        self.scene = GameScene.ENDING

    def _step_ending(self) -> None:
        """One VBL of the ending; afterwards mog starts again at the title (§12.39.10)."""
        screen = self.ending_screen
        if screen is None:
            raise ValueError("ending scene has no run")
        music = screen.run.music
        done = screen.advance()
        if screen.run.music and not music:
            self._play_ending_music()
        if done:
            self._reset_campaign(self.selected_player_count, self.players)
            self.scene = GameScene.TITLE
            self._play_title_music()

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
            if self.audio is not None and self.asset_bundle is not None:
                self.asset_bundle.attach_synth(self.audio)

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
        self._map_music()
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
        if self.overworld.turn_rules.stock is not None:
            initialize_stock(
                self.overworld, self.overworld.turn_rules.stock, self.overworld.turn_rules.gifts
            )
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
        self._map_music()
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
        self.last_status = ""
        if self._map_result(row, ord("Q") if key == ord("Q") else 0) and self.map_runtime:
            self.map_runtime.advance(world, self.tick_count)

    def _computer_meeting_loot(self, winner: Actor, loser: Actor, *, progress: int = 1) -> None:
        """Computer settlement shares the original transfer rule (§11.20)."""
        rules = self.overworld.turn_rules
        if rules is None:
            raise ValueError("meeting loot requires original transfer tables")
        winner.progress = u16(winner.progress + progress)
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
        self._map_music()
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
            self._open_meeting(row.key)
        elif row.kind in (21, 22, 23, 24):
            self._open_village(kid)
        elif row.kind in (25, 26):
            self._open_town(kid, waterdeep=row.kind == 26)
        elif row.kind == 27:
            self._open_stonehenge(kid)
        elif row.kind == 28:
            self._open_valley(kid)
        elif row.kind == 30:
            self._open_wizard(kid)
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

    def _open_town(self, knight_id: KnightId, *, waterdeep: bool) -> None:
        """Run the visit prologue and load the original town (§12.13/15)."""
        if self.asset_bundle is None:
            self.last_status = "ORIGINAL DISKS REQUIRED FOR TOWNS"
            return
        screen = TownScreen(self.asset_bundle, waterdeep=waterdeep, knight=int(knight_id) + 1)
        map_palette = self._overworld_target_palette()
        self.overworld.setup_turn()
        self.overworld.colors.active = False
        self._clear_map_jobs()
        self._map_key = 0
        self.town_screen = screen
        screen.state.entry = TownEntry.MAP_OUT
        self.scene = GameScene.LOCATION
        self.last_status = screen.data.name
        self.palette_fx.set_palette(_palette_to_rgb12(map_palette))
        self.palette_fx.fade_out()

    def _advance_town_entry(self) -> None:
        """Continue the blocking original loading path after each PAL palette step (§12.33)."""
        screen = self.town_screen
        if screen is None:
            raise ValueError("town entry requires its retained screen")
        if self.palette_fx.is_fading or screen.state.entry == TownEntry.READY:
            return
        if screen.state.entry == TownEntry.MAP_OUT:
            screen.state.entry = TownEntry.WELCOME_OUT
            if screen.message is None:
                raise ValueError("town welcome picture is not loaded")
            self.palette_fx.set_palette(_palette_to_rgb12(screen.message.palette))
            self.palette_fx.fade_out()
        else:
            screen.state.entry = TownEntry.READY
            self._begin_fade_in(screen.picture.palette)

    def _open_stonehenge(self, knight_id: KnightId) -> None:
        """Original offer or moonstone match, following the visit prologue (§12.34)."""
        if self.asset_bundle is None:
            self.last_status = "ORIGINAL DISKS REQUIRED FOR STONEHENGE"
            return
        owner = int(knight_id) + 1
        self.stone_screen = StoneScreen(
            self.asset_bundle,
            owner,
            self.actors[owner - 1],
            self.inventories[owner],
            self.overworld.moon_frame,
        )
        palette = self._overworld_target_palette()
        self.overworld.setup_turn()
        self.overworld.colors.active = False
        self._clear_map_jobs()
        self._map_key = 0
        self.scene = GameScene.STONEHENGE
        self.palette_fx.set_palette(_palette_to_rgb12(palette))
        self._stone_fade(out=True)

    @property
    def ending_request(self) -> EndingRequest | None:
        """Encoded program hand-over, consumed by the ending host in T11.22 (§12.34)."""
        return None if self.stone_screen is None else self.stone_screen.state.request

    def _open_meeting(self, other: int) -> None:
        """Keep turn ownership separate from defender/winner sheet ownership (§12.40)."""
        if self.asset_bundle is None:
            self.last_status = "ORIGINAL DISKS REQUIRED FOR MEETINGS"
            return
        first = int(self.overworld.current_knight_id) + 1
        if other == first or not 1 <= other <= 4:
            raise ValueError("meeting requires two different knight handles")
        screen = MeetingScreen(self.asset_bundle, first, other, self.overworld.names[other - 1])
        self.meeting_screen = screen
        palette = self._overworld_target_palette()
        self.overworld.colors.active = False
        self._clear_map_jobs()
        self._map_key = 0
        self.scene = GameScene.MEETING
        self.palette_fx.set_palette(_palette_to_rgb12(palette))
        self._meeting_fade(screen, out=True)

    def _meeting_fade(self, screen: MeetingScreen, *, out: bool) -> None:
        screen.state.wait = screen.rules.fade_wait
        if out:
            self.palette_fx.fade_out(divisor=screen.rules.fade_period)
            if screen.state.phase in (MeetingPhase.COMBAT_OUT, MeetingPhase.RESULT_CLOSING):
                self._silent_sound_fade()
        else:
            self.palette_fx.fade_in(
                _palette_to_rgb12(screen.palette()),
                divisor=screen.rules.fade_period,
                from_black=True,
            )

    def _step_meeting(self, frame: InputFrame) -> None:
        screen = self.meeting_screen
        if screen is None:
            raise ValueError("meeting scene has no caller")
        state = screen.state
        state.wait = max(0, state.wait - 1)
        if state.wait or self.palette_fx.is_fading:
            return
        if state.phase == MeetingPhase.PROTECT:
            inp = self._player_input(frame, KnightId(state.second - 1))
            if state.released(inp.fire or inp.confirm):
                state.phase = MeetingPhase.PROTECT_OUT
                self._meeting_fade(screen, out=True)
            return
        self._meeting_transition(screen)

    def _meeting_transition(self, screen: MeetingScreen) -> None:
        state = screen.state
        if state.phase == MeetingPhase.MAP_OUT:
            first, second = self.actors[state.first - 1], self.actors[state.second - 1]
            self._meeting_action(
                screen,
                meeting_decide(first, second, self.inventories[second.inventory], screen.rules),
            )
        elif state.phase == MeetingPhase.PROTECT_OUT:
            state.phase = MeetingPhase.PROTECT_SHEET
            self.last_sheet_item = 65535
            self._open_sheet(screen.rules.temple_sheet, knight=state.second)
        elif state.phase == MeetingPhase.COMBAT_OUT:
            self.active_encounter = None
            if state.hoard:
                state.phase = MeetingPhase.RESULT_SHEET
                self._open_sheet(screen.rules.hoard_sheet, knight=state.first)
            else:
                self._meeting_result(screen)

    def _meeting_action(self, screen: MeetingScreen, action: MeetingAction) -> None:
        if action == MeetingAction.SETTLE:
            self._meeting_result(screen)
        elif action == MeetingAction.PROTECT:
            screen.state.phase = MeetingPhase.PROTECT
            self._meeting_fade(screen, out=False)
        elif action == MeetingAction.RETURN:
            self._meeting_return(screen)
        else:
            self._meeting_fight(screen)

    def _meeting_fight(self, screen: MeetingScreen) -> None:
        state, world = screen.state, self.overworld
        kid, other = KnightId(state.first - 1), KnightId(state.second - 1)
        for who in (kid, other):
            self._sync_legacy_actor(who)
        state.phase = MeetingPhase.COMBAT
        self._start_combat(
            EncounterTrigger(
                TriggerKind.PVP_DUEL,
                kid,
                -1,
                world.day,
                world.moon_phase,
                opponent_knight_id=other,
            ),
            kid,
        )
        active = self.active_encounter
        if active is None:
            self._meeting_return(screen)
            return
        active.runtime.fight.confused = state.confused
        active.runtime.fight.confused_on = bool(state.confused)

    def _meeting_result(self, screen: MeetingScreen) -> None:
        state = screen.state
        if state.defeat == 3:
            if self.actors[state.first - 1].kind == screen.rules.computer_kind:
                self._meeting_return(screen)
                return
            number, winner, loser = screen.rules.temple_sheet, state.first, 0
        else:
            winner, loser = (
                (state.second, state.first) if state.defeat & 1 else (state.first, state.second)
            )
            if self.actors[winner - 1].kind == screen.rules.computer_kind:
                self._computer_meeting_loot(
                    self.actors[winner - 1], self.actors[loser - 1], progress=screen.rules.progress
                )
                self._meeting_return(screen)
                return
            number = screen.rules.loot_sheet
        state.phase = MeetingPhase.RESULT_SHEET
        self._open_sheet(number, knight=winner, other=loser)

    def _meeting_return(self, screen: MeetingScreen) -> None:
        if screen.state.hoard:
            self.overworld.spent = self.overworld.budget
        for kid in KnightId:
            self._sync_legacy_actor(kid)
        self.meeting_screen, self.sheet_screen = None, None
        self._reenter_map()

    def _close_meeting_sheet(self, sheet: SheetScreen) -> None:
        screen = self.meeting_screen
        if screen is None:
            raise ValueError("meeting sheet has no caller")
        state = screen.state
        if state.phase in (MeetingPhase.PROTECT_SHEET, MeetingPhase.RESULT_SHEET):
            state.phase = (
                MeetingPhase.PROTECT_CLOSING
                if state.phase == MeetingPhase.PROTECT_SHEET
                else MeetingPhase.RESULT_CLOSING
            )
            self._meeting_fade(screen, out=True)
            return
        state.wait = max(0, state.wait - 1)
        if state.wait or self.palette_fx.is_fading:
            return
        self.last_sheet_item = sheet.state.last_item
        self.sheet_screen = None
        if state.phase == MeetingPhase.PROTECT_CLOSING:
            self._meeting_protection_result(screen)
        else:
            self._meeting_return(screen)

    def _meeting_protection_result(self, screen: MeetingScreen) -> None:
        state = screen.state
        used = self.last_sheet_item == screen.rules.protection_slot
        if used and not self.bad_luck:
            self._meeting_return(screen)
            return
        state.confused = state.second if used else 0
        action = after_protection(
            self.actors[state.first - 1], self.actors[state.second - 1], screen.rules
        )
        self._meeting_action(screen, action)

    def _finish_meeting_combat(self, active: ActiveEncounter) -> None:
        screen = self.meeting_screen
        if screen is None:
            raise ValueError("meeting fight has no caller")
        screen.state.defeat = settle_defeats(active.runtime.fight, active.runtime.opponent)
        self.last_defeat_mask = screen.state.defeat
        self.bad_luck = 0
        active.runtime.release()
        self._meeting_combat_out(screen, active)

    def _meeting_combat_out(self, screen: MeetingScreen, active: ActiveEncounter) -> None:
        self.scene, screen.state.phase = GameScene.MEETING, MeetingPhase.COMBAT_OUT
        self.palette_fx.set_palette(_palette_to_rgb12(active.runtime.scene.palette))
        self._meeting_fade(screen, out=True)

    def _meeting_snapshot(self) -> dict[str, object] | None:
        return None if self.meeting_screen is None else self.meeting_screen.snapshot()

    def _open_valley(self, knight_id: KnightId) -> None:
        """Retain the original Valley caller through messages, fight and rewards (§12.38)."""
        if self.asset_bundle is None:
            self.last_status = "ORIGINAL DISKS REQUIRED FOR THE VALLEY"
            return
        owner = int(knight_id) + 1
        screen = ValleyScreen(
            self.asset_bundle, owner, self.inventories[owner].byte(20), self.loading_cycle
        )
        self.valley_screen = screen
        palette = self._overworld_target_palette()
        self.overworld.setup_turn()
        self.overworld.colors.active = False
        self._clear_map_jobs()
        self._map_key = 0
        self.scene = GameScene.VALLEY
        self.last_status = "VALLEY OF THE GODS"
        self.palette_fx.set_palette(_palette_to_rgb12(palette))
        self._valley_fade(screen, out=True)

    def _valley_fade(self, screen: ValleyScreen, *, out: bool) -> None:
        screen.state.wait = screen.rules.fade_wait
        if out:
            self.palette_fx.fade_out(divisor=screen.rules.fade_period)
            if screen.state.phase in (
                ValleyPhase.LOADING_OUT,
                ValleyPhase.COMBAT_OUT,
                ValleyPhase.TEMPLE_OUT,
                ValleyPhase.EXIT_OUT,
            ):
                self._silent_sound_fade()
        else:
            self.palette_fx.fade_in(
                _palette_to_rgb12(screen.palette()),
                divisor=screen.rules.fade_period,
                from_black=True,
            )

    def _valley_message(self, screen: ValleyScreen, phase: ValleyPhase) -> None:
        screen.state.phase, screen.state.fire_held = phase, False
        if phase == ValleyPhase.LOADING:
            self.loading_cycle = (self.loading_cycle + 1) % len(screen.data.loading.messages)
            self._play_requests(screen.data.loading.sounds)
        self._valley_fade(screen, out=False)

    def _step_valley(self, frame: InputFrame) -> None:
        screen = self.valley_screen
        if screen is None:
            raise ValueError("Valley scene has no visit")
        state = screen.state
        state.wait = max(0, state.wait - 1)
        if state.wait or self.palette_fx.is_fading:
            return
        if state.phase in (ValleyPhase.MISSING, ValleyPhase.VICTORY):
            inp = self._player_input(frame, KnightId(state.knight - 1))
            if state.released(inp.fire or inp.confirm):
                self._valley_fire(screen)
            return
        self._valley_transition(screen)

    def _valley_fire(self, screen: ValleyScreen) -> None:
        if screen.state.won:
            screen.state.phase = ValleyPhase.REWARD_WAIT
            screen.state.wait = screen.rules.reward_wait
        else:
            screen.state.phase = ValleyPhase.EXIT_OUT
            self._valley_fade(screen, out=True)

    def _valley_transition(self, screen: ValleyScreen) -> None:
        state = screen.state
        if state.phase == ValleyPhase.MAP_OUT:
            self._valley_message(
                screen, ValleyPhase.LOADING if state.matched else ValleyPhase.MISSING
            )
        elif state.phase == ValleyPhase.LOADING:
            state.phase = ValleyPhase.LOADING_OUT
            self._valley_fade(screen, out=True)
        elif state.phase == ValleyPhase.LOADING_OUT:
            self._valley_combat(screen)
        elif state.phase == ValleyPhase.COMBAT_OUT:
            self.active_encounter = None
            if state.won:
                self._valley_message(screen, ValleyPhase.VICTORY)
            else:
                state.phase = ValleyPhase.TEMPLE
                self._open_sheet(9)
        elif state.phase == ValleyPhase.REWARD_WAIT:
            state.phase = ValleyPhase.EXIT_OUT
            self._valley_fade(screen, out=True)
        elif state.phase == ValleyPhase.EXIT_OUT:
            self._valley_return(screen)

    def _valley_combat(self, screen: ValleyScreen) -> None:
        kid = KnightId(screen.state.knight - 1)
        self.overworld.setup_turn()
        self._sync_legacy_actor(kid)
        screen.state.phase = ValleyPhase.COMBAT
        self._start_combat(
            EncounterTrigger(
                TriggerKind.VALLEY_OF_THE_GODS,
                kid,
                -1,
                self.overworld.day,
                self.overworld.moon_phase,
                creature_type=8,
            ),
            kid,
        )

        if self.active_encounter is None:
            self._valley_return(screen)

    def _finish_valley_combat(self, active: ActiveEncounter) -> None:
        screen = self.valley_screen
        if screen is None:
            raise ValueError("Valley fight has no caller")
        state = screen.state
        self.last_defeat_mask = settle_defeats(active.runtime.fight, active.runtime.opponent)
        state.won = not self.last_defeat_mask & 1
        actor, inv = self.actors[state.knight - 1], self.inventories[state.knight]
        self.bad_luck = 0
        active.runtime.release()
        if state.won:
            valley_victory(actor, inv, screen.rules)
        else:
            self.overworld.setup_turn()
            valley_defeat(actor, self.overworld.prng, self._turn_rules().gifts, screen.rules)
        self._sync_legacy_actor(KnightId(state.knight - 1))
        self.scene, state.phase = GameScene.VALLEY, ValleyPhase.COMBAT_OUT
        self.palette_fx.set_palette(_palette_to_rgb12(active.runtime.scene.palette))
        self._valley_fade(screen, out=True)

    def _valley_return(self, screen: ValleyScreen) -> None:
        self._map_key = 0
        self.overworld.setup_turn()
        if screen.state.won:
            valley_moonstone(
                self.inventories[screen.state.knight], self.overworld.prng, screen.rules
            )
        self._sync_legacy_actor(KnightId(screen.state.knight - 1))
        self.valley_screen = None
        self._reenter_map()

    def _close_valley_sheet(self, sheet: SheetScreen) -> None:
        screen = self.valley_screen
        if screen is None:
            raise ValueError("Valley temple has no caller")
        if screen.state.phase == ValleyPhase.TEMPLE:
            screen.state.phase = ValleyPhase.TEMPLE_OUT
            self._valley_fade(screen, out=True)
            return
        screen.state.wait = max(0, screen.state.wait - 1)
        if screen.state.wait or self.palette_fx.is_fading:
            return
        self.last_sheet_item = sheet.state.last_item
        self.sheet_screen, self.valley_screen = None, None
        self.overworld.spent = self.overworld.budget
        self._reenter_map()

    def _valley_snapshot(self) -> dict[str, object] | None:
        return None if self.valley_screen is None else self.valley_screen.snapshot()

    def _open_village(self, knight_id: KnightId) -> None:
        """Retain the original village caller through map OUT and sheet 9 (§12.37)."""
        if self.asset_bundle is None:
            self.last_status = "ORIGINAL DISKS REQUIRED FOR VILLAGES"
            return
        rules = parse_village_rules(
            parse_hunk_executable("mog", self.asset_bundle.disk_set.read_file("mog"))
        )
        self.village_visit = VillageVisit(int(knight_id) + 1, rules, wait=rules.fade_wait)
        palette = self._overworld_target_palette()
        self.overworld.setup_turn()
        self.overworld.colors.active = False
        self._clear_map_jobs()
        self._map_key = 0
        self.scene = GameScene.VILLAGE
        self.last_status = "HOME VILLAGE"
        self.palette_fx.set_palette(_palette_to_rgb12(palette))
        self.palette_fx.fade_out(divisor=rules.fade_period)
        self._silent_sound_fade()

    def _step_village(self) -> None:
        visit = self.village_visit
        if visit is None:
            raise ValueError("village scene has no visit")
        visit.wait = max(0, visit.wait - 1)
        if visit.wait or self.palette_fx.is_fading:
            return
        village_reward(self.actors[visit.knight - 1], visit.rules)
        self._sync_legacy_actor(KnightId(visit.knight - 1))
        visit.phase = VillagePhase.TEMPLE
        self._open_sheet(visit.rules.sheet)

    def _close_village_sheet(self, sheet: SheetScreen) -> None:
        visit = self.village_visit
        if visit is None:
            raise ValueError("village temple has no visit")
        if visit.phase == VillagePhase.TEMPLE:
            visit.phase = VillagePhase.TEMPLE_OUT
            visit.wait = visit.rules.fade_wait
            self.palette_fx.fade_out(divisor=visit.rules.fade_period)
            self._silent_sound_fade()
            return
        visit.wait = max(0, visit.wait - 1)
        if visit.wait or self.palette_fx.is_fading:
            return
        self.last_sheet_item = sheet.state.last_item
        self.sheet_screen, self.village_visit = None, None
        self.overworld.spent = self.overworld.budget
        self._reenter_map()

    def _village_snapshot(self) -> tuple[int, int, int] | None:
        return None if self.village_visit is None else self.village_visit.snapshot()

    def _open_wizard(self, knight_id: KnightId) -> None:
        """Original wizard visit, with retained loading and the shared reward state (§12.36)."""
        if self.asset_bundle is None:
            self.last_status = "ORIGINAL DISKS REQUIRED FOR THE WIZARD"
            return
        owner = int(knight_id) + 1
        self.wizard_screen = WizardScreen(
            self.asset_bundle, owner, self.actors[knight_id].kind, self.loading_cycle
        )
        palette = self._overworld_target_palette()
        self.overworld.setup_turn()
        self.overworld.colors.active = False
        self._clear_map_jobs()
        self._map_key = 0
        self.scene = GameScene.WIZARD
        self.last_status = "WIZARD"
        self.palette_fx.set_palette(_palette_to_rgb12(palette))
        self._wizard_fade(self.wizard_screen, out=True)

    def _wizard_fade(self, screen: WizardScreen, *, out: bool) -> None:
        screen.state.wait = screen.rules.fade_wait
        if out:
            self.palette_fx.fade_out(divisor=screen.rules.fade_period)
            if screen.state.phase in (
                WizardPhase.LOADING_OUT,
                WizardPhase.EXIT_OUT,
                WizardPhase.TEMPLE_OUT,
            ):
                self._silent_sound_fade()
        else:
            self.palette_fx.fade_in(
                _palette_to_rgb12(screen.palette()),
                divisor=screen.rules.fade_period,
                from_black=True,
            )

    def _wizard_stage(self, screen: WizardScreen, stage: int) -> None:
        if stage == 0:
            self._load_sound_bank(screen.rules.bank)
        screen.state.phase = (WizardPhase.ARRIVAL, WizardPhase.GIFT, WizardPhase.FAREWELL)[stage]
        screen.state.wait = 0
        screen.start_stage(stage, self.overworld.prng, self.tick_count)
        if stage == 2:
            self.palette_fx.set_palette(_palette_to_rgb12(screen.palette()))
        else:
            self.palette_fx.fade_in(
                _palette_to_rgb12(screen.palette()),
                divisor=screen.rules.palette_period,
                from_black=True,
            )
        if screen.animation is None:
            raise ValueError("wizard stage did not start")
        self._play_requests(screen.animation.sounds)

    def _step_wizard(self, frame: InputFrame) -> None:
        screen = self.wizard_screen
        if screen is None:
            raise ValueError("wizard scene has no visit")
        if screen.state.phase in (WizardPhase.ARRIVAL, WizardPhase.GIFT, WizardPhase.FAREWELL):
            inp = self._player_input(frame, KnightId(screen.state.knight - 1))
            done = screen.advance(self.tick_count, inp.fire or inp.confirm)
            if screen.animation is None:
                raise ValueError("wizard stage has no animation")
            self._play_requests(screen.animation.sounds)
            if done:
                self._wizard_fire(screen)
            return
        screen.state.wait = max(0, screen.state.wait - 1)
        if not screen.state.wait and not self.palette_fx.is_fading:
            self._wizard_continue(screen)

    def _wizard_fire(self, screen: WizardScreen) -> None:
        if screen.state.phase == WizardPhase.GIFT:
            self._wizard_stage(screen, 2)
        else:
            if screen.state.phase == WizardPhase.FAREWELL:
                self.actors[screen.state.knight - 1].recency = screen.dialogue.recency
                screen.state.phase = WizardPhase.EXIT_OUT
            else:
                screen.state.phase = WizardPhase.ARRIVAL_OUT
            self._wizard_fade(screen, out=True)

    def _wizard_continue(self, screen: WizardScreen) -> None:
        state = screen.state
        if state.phase == WizardPhase.MAP_OUT:
            state.phase = WizardPhase.LOADING
            self.loading_cycle = (self.loading_cycle + 1) % len(screen.rules.loading.messages)
            self._play_requests(screen.rules.loading.sounds)
            self._wizard_fade(screen, out=False)
        elif state.phase == WizardPhase.LOADING:
            state.phase = WizardPhase.LOADING_OUT
            self._wizard_fade(screen, out=True)
        elif state.phase == WizardPhase.LOADING_OUT:
            self._wizard_stage(screen, 0)
        elif state.phase == WizardPhase.ARRIVAL_OUT:
            state.result = wizard_gift(
                self.actors[state.knight - 1],
                self.inventories[state.knight],
                self.overworld.prng,
                self._turn_rules().gifts,
                self.overworld.gifts,
            )
            self._sync_legacy_actor(KnightId(state.knight - 1))
            self._wizard_stage(screen, 1)
        elif state.phase == WizardPhase.EXIT_OUT:
            state.phase = WizardPhase.TEMPLE
            self._open_sheet(9)

    def _close_wizard_sheet(self, sheet: SheetScreen) -> None:
        screen = self.wizard_screen
        if screen is None:
            raise ValueError("wizard temple has no visit")
        if screen.state.phase == WizardPhase.TEMPLE:
            screen.state.phase = WizardPhase.TEMPLE_OUT
            self._wizard_fade(screen, out=True)
            return
        screen.state.wait = max(0, screen.state.wait - 1)
        if screen.state.wait or self.palette_fx.is_fading:
            return
        self.last_sheet_item = sheet.state.last_item
        self.sheet_screen, self.wizard_screen = None, None
        self.overworld.spent = self.overworld.budget
        self._reenter_map()

    def _wizard_snapshot(self) -> dict[str, object] | None:
        return None if self.wizard_screen is None else self.wizard_screen.snapshot()

    def _stone_snapshot(self) -> dict[str, object] | None:
        return None if self.stone_screen is None else self.stone_screen.snapshot()

    def _stone_fade(self, *, out: bool) -> None:
        screen = self.stone_screen
        if screen is None:
            raise ValueError("Stonehenge fade requires its visit")
        screen.state.wait = screen.rules.fade_wait
        if out:
            self.palette_fx.fade_out(divisor=screen.rules.fade_period)
            if screen.state.phase in (
                StonePhase.RITUAL_MESSAGE_OUT,
                StonePhase.RITUAL_OUT,
                StonePhase.TEMPLE_CLOSING,
            ):
                self._silent_sound_fade()
        else:
            self.palette_fx.fade_in(
                _palette_to_rgb12(screen.message_palette()),
                divisor=screen.rules.fade_period,
                from_black=True,
            )

    def _stone_message(self, phase: StonePhase) -> None:
        screen = self.stone_screen
        if screen is None:
            raise ValueError("Stonehenge message requires its visit")
        screen.state.phase, screen.state.fire_held = phase, False
        self._stone_fade(out=False)
        if phase == StonePhase.QUEST:
            screen.state.wait += screen.rules.quest_wait

    def _step_stone(self, frame: InputFrame) -> None:
        screen = self.stone_screen
        if screen is None:
            raise ValueError("Stonehenge scene has no visit")
        state = screen.state
        if state.phase == StonePhase.RITUAL:
            self._step_stone_ritual(screen)
            return
        state.wait = max(0, state.wait - 1)
        if state.wait or self.palette_fx.is_fading:
            return
        kid = KnightId(state.knight - 1)
        inp = self._player_input(frame, kid)
        self._stone_continue(screen, inp.fire or inp.confirm)

    def _stone_continue(self, screen: StoneScreen, fire: bool) -> None:
        state = screen.state
        if state.phase == StonePhase.MAP_OUT:
            self._stone_message(StonePhase.QUEST if state.matched else StonePhase.OFFER)
        elif state.phase in (StonePhase.OFFER, StonePhase.QUEST):
            if not state.released(fire):
                return
            if state.phase == StonePhase.QUEST:
                state.phase = StonePhase.HAND_OVER
                state.request = EndingRequest(screen.rules.program, state.ending)
                self._enter_ending(state.request)
            else:
                state.phase = StonePhase.OFFER_OUT
                self._stone_fade(out=True)
        else:
            self._stone_transition(screen)

    def _stone_transition(self, screen: StoneScreen) -> None:
        state = screen.state
        if state.phase == StonePhase.OFFER_OUT:
            self.last_sheet_item = 65535
            state.phase = StonePhase.OFFER_SHEET
            self._open_sheet(3)
        elif state.phase == StonePhase.RITUAL_MESSAGE:
            state.phase = StonePhase.RITUAL_MESSAGE_OUT
            self._stone_fade(out=True)
        elif state.phase == StonePhase.RITUAL_MESSAGE_OUT:
            state.phase = StonePhase.RITUAL
            self._load_sound_bank(screen.rules.ritual.bank)
            screen.start_ritual(self.overworld.prng)
            self.palette_fx.set_palette([0] * 32)
            self.palette_fx.fade_to(
                _palette_to_rgb12(screen.ritual_palette()),
                divisor=screen.rules.ritual.palette_period,
            )
            self._step_stone_ritual(screen)
        elif state.phase == StonePhase.RITUAL_OUT:
            bless(self.actors[state.knight - 1], self.inventories[state.knight], screen.rules)
            self._stone_temple(screen)

    def _step_stone_ritual(self, screen: StoneScreen) -> None:
        done = screen.advance_ritual(self.tick_count)
        if screen.run is None:
            raise ValueError("Stonehenge ritual did not start")
        self._play_requests(screen.run.sounds)
        if done:
            screen.state.phase = StonePhase.RITUAL_OUT
            self._stone_fade(out=True)

    def _stone_temple(self, screen: StoneScreen) -> None:
        screen.state.phase = StonePhase.TEMPLE
        self._open_sheet(9)

    def _close_stone_sheet(self, sheet: SheetScreen) -> None:
        screen = self.stone_screen
        if screen is None:
            raise ValueError("Stonehenge sheet has no visit")
        state = screen.state
        if state.phase in (StonePhase.OFFER_SHEET, StonePhase.TEMPLE):
            state.phase = (
                StonePhase.OFFER_CLOSING
                if state.phase == StonePhase.OFFER_SHEET
                else StonePhase.TEMPLE_CLOSING
            )
            self._stone_fade(out=True)
            return
        state.wait = max(0, state.wait - 1)
        if state.wait or self.palette_fx.is_fading:
            return
        self.last_sheet_item = sheet.state.last_item
        self.sheet_screen = None
        if state.phase == StonePhase.TEMPLE_CLOSING:
            self.stone_screen = None
            self.overworld.spent = self.overworld.budget
            self._reenter_map()
        elif self.last_sheet_item == 65535:
            self._stone_temple(screen)
        else:
            self.scene = GameScene.STONEHENGE
            self._stone_message(StonePhase.RITUAL_MESSAGE)

    def _handle_overworld_triggers(
        self,
        knight_id: KnightId,
        triggers: tuple[EncounterTrigger, ...],
    ) -> None:
        for trig in triggers:
            if trig.kind == TriggerKind.TOWN_VISIT:
                self._open_town(knight_id, waterdeep=trig.node_id == 26)
                return
            if trig.kind == TriggerKind.STONEHENGE_RITUAL:
                self._open_stonehenge(knight_id)
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
        screen = self.town_screen
        if screen is None:
            raise ValueError("town scene has no original town screen")
        if screen.state.entry != TownEntry.READY:
            return
        kid = KnightId(screen.state.knight - 1)
        action = screen.step(
            self._player_input(frame, kid), frame.key, polling=not self.palette_fx.is_fading
        )
        if action == TownAction.EXIT:
            self.overworld.spent = self.overworld.budget
            self._begin_sound_return("town")
        elif action in (TownAction.SMITH, TownAction.MARKET, TownAction.STATUS):
            number = {
                TownAction.SMITH: 5,
                TownAction.MARKET: 6,
                TownAction.STATUS: screen.data.space_sheet,
            }[action]
            self._open_sheet(number)
        elif action != TownAction.NONE:
            self._open_town_service(action)

    def _open_town_service(self, action: TownAction) -> None:
        """Open the healer or mystic screen; the dice house stays explicit (§12.31)."""
        town = self.town_screen
        if town is None:
            raise ValueError("service call requires its parent town")
        kinds = {TownAction.HEALER: ServiceKind.HEALER, TownAction.MYSTIC: ServiceKind.MYSTIC}
        if self.asset_bundle is None:
            self.last_status = f"{action.name} REQUIRES THE ORIGINAL DISKS"
            town.resume()
            return
        if action == TownAction.DICE:
            self._open_dice(town)
            return
        if action not in kinds:
            raise ValueError(f"no service screen for {action.name}")
        screens, _ = self._services()
        self.service_screen = ServiceScreen(
            self.asset_bundle,
            screens,
            kinds[action],
            knight=town.state.knight,
            pointer=town.pointer_pixels,
            text=(self.overworld.data().char_map, self._turn_rules().span),
        )
        self.scene = GameScene.SERVICE
        self._play_requests(self.service_screen.state.sounds)
        self._begin_fade_in(self.service_screen.picture.palette)

    def _open_dice(self, town: TownScreen) -> None:
        """Enter the dice house unless the player has no gold (§12.32)."""
        player = self.actors[town.state.knight - 1]
        if self.asset_bundle is None or not can_enter(player):
            town.resume()
            return
        self.dice_screen = DiceScreen(
            self.asset_bundle,
            self._dice(),
            knight=(town.state.knight, player.kind),
            pointer=(town.pointer_pixels, town.state.pointer_y),
            text=(self.overworld.data().char_map, self._turn_rules().span),
        )
        self.scene = GameScene.DICE
        self._play_requests(self.dice_screen.run.state.sounds)
        self._begin_fade_in(self.dice_screen.round_palette)

    def _dice(self) -> tuple[DiceHouse, DiceEngine]:
        if self._dice_data is None:
            bundle = self.asset_bundle
            if bundle is None:
                raise ValueError("the dice house requires the original disks")
            executable = parse_hunk_executable("mog", bundle.disk_set.read_file("mog"))
            house = parse_dice_house(executable)
            cel = bundle.get_cel(house.cel_file)
            if cel is None:
                raise ValueError("the dice house requires its cel file")
            mog = MogCode(executable)
            engine = DiceEngine(
                script_image(mog, DICE_SCRIPT_HUNK),
                {house.cel_file: cel_shape(house.cel_file, cel, None)},
                fight_constants(mog),
                parse_sound_rules(executable),
            )
            self._dice_data = (house, engine)
        return self._dice_data

    def _step_dice(self, frame: InputFrame) -> None:
        screen = self.dice_screen
        if screen is None:
            raise ValueError("dice scene has no screen")
        kid = KnightId(screen.run.state.knight - 1)
        _, (_healer, _mystic, dice) = self._services()
        world = DiceWorld(self.actors[kid], self.overworld.prng, dice)
        inp = self._player_input(frame, kid)
        done = screen.step(world, inp, fading=self.palette_fx.is_fading)
        self._play_requests(screen.run.state.sounds)
        self._sync_legacy_actor(kid)
        if done:
            self._begin_sound_return("dice")

    def _services(self) -> tuple[ServiceScreens, ServiceRules]:
        if self._service_data is None:
            if self.asset_bundle is None:
                raise ValueError("service screens require the original disks")
            executable = parse_hunk_executable("mog", self.asset_bundle.disk_set.read_file("mog"))
            screens = parse_service_screens(executable)
            self._service_data = (screens, parse_service_rules(executable))
        return self._service_data

    def _turn_rules(self) -> TurnRules:
        rules = self.overworld.turn_rules
        if rules is None:
            raise ValueError("services need the loaded turn rules")
        return rules

    def _play_requests(self, requests: Sequence[SoundRequest]) -> None:
        if self.audio is not None and requests:
            if self.audio.module_player is not None and self.asset_bundle is not None:
                self.asset_bundle.attach_synth(self.audio)
            self.audio.handle_requests(requests)

    def _step_service(self, frame: InputFrame) -> None:
        screen = self.service_screen
        if screen is None:
            raise ValueError("service scene has no screen")
        kid = KnightId(screen.state.knight - 1)
        _, (healer, mystic, _dice) = self._services()
        world = ServiceWorld(
            self.actors[kid],
            self.inventories[int(kid) + 1],
            self.overworld.prng,
            self._turn_rules().gifts,
            healer,
            mystic,
        )
        inp = self._player_input(frame, kid)
        done = screen.step(world, inp, fading=self.palette_fx.is_fading)
        self._play_requests(screen.state.sounds)
        self._sync_legacy_actor(kid)
        if done:
            self._begin_sound_return("service")

    def _close_service(self) -> None:
        self.service_screen = None
        self.dice_screen = None
        town = self.town_screen
        if town is None:
            raise ValueError("a service screen returns to its town")
        town.resume()
        self.scene = GameScene.LOCATION
        self.last_status = town.data.name
        self._begin_fade_in(town.picture.palette)

    def _step_status(self, frame: InputFrame) -> None:
        screen = self.sheet_screen
        if screen is None:
            self._open_sheet(9)
            return
        kid = KnightId(screen.state.knight - 1)
        done = screen.step(self.overworld, self._player_input(frame, kid))
        if self.audio is not None and self.asset_bundle is not None:
            self._play_requests(self.asset_bundle.get_scene_sounds().sheet(screen.state.sounds))
        self.bad_luck |= screen.state.bad_luck
        for knight in KnightId:
            self._sync_legacy_actor(knight)
        if done:
            self._close_sheet()

    def _open_sheet(
        self, number: int, *, lair: int = -1, other: int = 0, knight: int | None = None
    ) -> None:
        if self.asset_bundle is None:
            self.scene = GameScene.OVERWORLD
            self.last_status = "ORIGINAL DISKS REQUIRED FOR SHEETS"
            return
        self.overworld.colors.active = False
        self.sheet_screen = SheetScreen(
            self.asset_bundle,
            self.overworld,
            number,
            knight=int(self.overworld.current_knight_id) + 1 if knight is None else knight,
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
        if self.meeting_screen is not None:
            self._close_meeting_sheet(screen)
            return
        if self.village_visit is not None:
            self._close_village_sheet(screen)
            return
        if self.valley_screen is not None:
            self._close_valley_sheet(screen)
            return
        if self.wizard_screen is not None:
            self._close_wizard_sheet(screen)
            return
        if self.stone_screen is not None:
            self._close_stone_sheet(screen)
            return
        self.last_sheet_item = screen.state.last_item
        if screen.state.lair >= 0:
            if screen.state.read_only:
                self.overworld.restore_position()
            else:
                self.overworld.lairs[screen.state.lair].tidy()
        if self.town_screen is not None:
            self.sheet_screen = None
            self.town_screen.resume()
            self.scene = GameScene.LOCATION
            self.last_status = self.town_screen.data.name
            self._begin_fade_in(self.town_screen.picture.palette)
            return
        self._begin_sound_return("sheet")
        self.last_status = f"DAY {self.overworld.day}"

    def _step_new_day(self, primary: PlayerInput) -> None:
        if self.palette_fx.is_fading:
            return
        if self._calendar_phase == 0:
            self._calendar_phase = 1
            self._begin_fade_in(self._calendar_target_palette())
            return
        if self.overworld.turns.wait_fire(primary.fire or primary.confirm):
            self.palette_fx.set_palette([0] * 32)
            self._advance_turns()

    def _step_end_screen(self, primary: PlayerInput) -> None:
        if self.scene == GameScene.GAME_OVER and self.overworld.turn_rules is not None:
            if not self.palette_fx.is_fading and self.overworld.turns.wait_fire(
                primary.fire or primary.confirm
            ):
                self._begin_sound_return("title")
            return
        fire_edge = primary.fire and not self._end_prev_fire
        self._end_prev_fire = primary.fire
        if primary.confirm or fire_edge:
            self._reset_campaign(self.selected_player_count)
            self.scene = GameScene.KNIGHT_SELECT
            self.last_status = "SELECT KNIGHTS"
            self._begin_fade_in(self._menu_target_palette())
            self._play_title_music()

    def _arena_name_for_trigger(self, trigger: EncounterTrigger) -> str:
        if trigger.kind == TriggerKind.VALLEY_OF_THE_GODS:
            return "valley"
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
                computers=self._fight_computers(knight_id, opponent_id),
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

    def _fight_computers(self, *kids: KnightId) -> frozenset[int]:
        """Use campaign kinds, or roster setup before map rules are installed (§12.40)."""
        if self.overworld.rules is None:
            return frozenset(int(kid) + 1 for kid in kids if kid not in self.players)
        return frozenset(int(kid) + 1 for kid in kids if self.actors[kid].kind == 4)

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
        if self.audio is not None and self.asset_bundle is not None:
            self.audio.stop_all()
            self._load_sound_bank("kn.a")
            if trigger.kind != TriggerKind.PVP_DUEL and not self.practice_mode:
                if self.fight_loader is None:
                    raise ValueError("combat sound banks require the fight loader")
                bank = self.fight_loader.arena_for(trigger.creature_type).cels.sound_bank
                if bank is not None:
                    self._load_sound_bank(bank)
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
            self.audio.handle_requests(output.sounds)
        if active.runtime.finished:
            self._fight_sound_exit(active)

    def _fight_sound_exit(self, active: ActiveEncounter) -> None:
        if self.audio is not None:
            self.audio.stop_all()
        retained = self.meeting_screen is not None or self.valley_screen is not None
        dragon = (
            active.trigger.kind == TriggerKind.DRAGON_INTERCEPT
            and self.overworld.turn_rules is not None
        )
        if retained or dragon:
            self._finish_combat(active)
        else:
            self._begin_sound_return("fight")

    def _complete_combat(self) -> None:
        active = self.active_encounter
        if active is None:
            raise ValueError("combat close has no encounter")
        self._finish_combat(active)

    def _finish_combat(self, active: ActiveEncounter) -> None:
        """Settle original defeat/lair records; original caller sheets remain T11.15 (§10.24)."""
        if self._finish_caller_fight(active):
            return
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

    def _finish_caller_fight(self, active: ActiveEncounter) -> bool:
        if self.practice_mode:
            return False
        if active.trigger.kind == TriggerKind.VALLEY_OF_THE_GODS:
            self._finish_valley_combat(active)
        elif active.trigger.kind == TriggerKind.PVP_DUEL and self.meeting_screen is not None:
            self._finish_meeting_combat(active)
        elif (
            active.trigger.kind == TriggerKind.DRAGON_INTERCEPT
            and self.overworld.turn_rules is not None
        ):
            self._finish_dragon_combat(active)
        else:
            return False
        return True

    def _finish_dragon_combat(self, active: ActiveEncounter) -> None:
        rules, bundle = self.overworld.turn_rules, self.asset_bundle
        if rules is None or bundle is None:
            raise ValueError("dragon settlement requires original assets and turn rules")
        self.last_defeat_mask = settle_defeats(active.runtime.fight, active.runtime.opponent)
        lost = bool(self.last_defeat_mask & 1)
        settle_dragon(self.overworld, rules, lost=lost, spend=lost)
        self.bad_luck = 0
        active.runtime.release()
        for kid in KnightId:
            self._sync_legacy_actor(kid)
        if lost:
            self.active_encounter = None
            self._return_from_map_fight(False)
            return
        first = int(self.overworld.current_knight_id) + 1
        screen = MeetingScreen(bundle, first, 0, "")
        screen.state.hoard = True
        self.meeting_screen = screen
        self._meeting_combat_out(screen, active)

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
            "selection": (
                self.players,
                self.selected_player_count,
                self.menu_cursor,
                self.gore_enabled,
                self.practice_mode,
                self.knight_pick_active,
                self.knight_pick_slot,
                self.knight_taken_mask,
                self.picked_knights,
            ),
            "input_latches": (
                self._menu_prev_dx,
                self._menu_prev_dy,
                self._menu_prev_fire,
                self._end_prev_fire,
            ),
            "world": world,
            "map_key": self._map_key,
            "calendar_phase": self._calendar_phase,
            "town": asdict(self.town_screen.state) if self.town_screen else None,
            "stone": self._stone_snapshot(),
            "ending": self._ending_snapshot(),
            "wizard": self._wizard_snapshot(),
            "village": self._village_snapshot(),
            "valley": self._valley_snapshot(),
            "meeting": self._meeting_snapshot(),
            "loading_cycle": self.loading_cycle,
            "sheet": asdict(self.sheet_screen.state) if self.sheet_screen else None,
            "service": asdict(self.service_screen.state) if self.service_screen else None,
            "dice": self.dice_screen.run.snapshot() if self.dice_screen else None,
            "last_sheet_item": self.last_sheet_item,
            "palette": self.palette_fx.snapshot(),
            "sound_fade": (self._sound_fade_ticks, self._sound_return),
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
        if self.town_screen is None:
            raise ValueError("town scene has no original town screen")
        entry = self.town_screen.state.entry
        if entry == TownEntry.MAP_OUT:
            self._render_overworld_scene(scene_renderer, font)
        elif entry == TownEntry.WELCOME_OUT:
            self.town_screen.render_welcome(scene_renderer)
        else:
            self.town_screen.render(scene_renderer)

    def _render_end_scene(self, scene_renderer: SceneRenderer, font: BitmapFont) -> None:
        if self.scene == GameScene.GAME_OVER and self.overworld.turn_rules is not None:
            self._render_calendar(scene_renderer, farewell=True)
            return
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
            else self._calendar_target_palette()
        )
        renderer.set_palette(palette)

    def render_to_scene(self, scene_renderer: SceneRenderer, font: BitmapFont) -> None:
        """Render the active game scene and HUD onto ``scene_renderer``."""
        scenes: dict[GameScene, Callable[[], None]] = {
            GameScene.TITLE: lambda: self._render_title_scene(scene_renderer, font),
            GameScene.KNIGHT_SELECT: lambda: self._render_knight_select_scene(scene_renderer, font),
            GameScene.OVERWORLD: lambda: self._render_overworld_scene(scene_renderer, font),
            GameScene.COMBAT: lambda: self._render_combat_scene(scene_renderer, font),
            GameScene.LOCATION: lambda: self._render_location_scene(scene_renderer, font),
            GameScene.STATUS: lambda: self._render_sheet_scene(scene_renderer),
            GameScene.NEW_DAY: lambda: self._render_new_day(scene_renderer, font),
            GameScene.SERVICE: lambda: self._render_service_scene(scene_renderer),
            GameScene.DICE: lambda: self._render_dice_scene(scene_renderer),
            GameScene.STONEHENGE: lambda: self._render_stone_scene(scene_renderer, font),
            GameScene.ENDING: lambda: self._render_ending(scene_renderer),
            GameScene.WIZARD: lambda: self._render_wizard_scene(scene_renderer, font),
            GameScene.VILLAGE: lambda: self._render_overworld_scene(scene_renderer, font),
            GameScene.VALLEY: lambda: self._render_valley_scene(scene_renderer, font),
            GameScene.MEETING: lambda: self._render_meeting_scene(scene_renderer, font),
        }
        scenes.get(self.scene, lambda: self._render_end_scene(scene_renderer, font))()

        if (
            self.palette_fx.is_fading
            or bool(self._sound_return)
            or self.palette_fx.is_flashing
            or self.wizard_screen is not None
            or self.village_visit is not None
            or self._caller_palette_active()
        ):
            scene_renderer.set_palette_fx(self.palette_fx.current_palette())
        scene_renderer.flush_to_fbo()

    def _ending_snapshot(self) -> dict[str, object] | None:
        return None if self.ending_screen is None else self.ending_screen.snapshot()

    def _render_ending(self, renderer: SceneRenderer) -> None:
        if self.ending_screen is None:
            raise ValueError("ending scene has no run")
        self.ending_screen.render(renderer)

    def _caller_palette_active(self) -> bool:
        return self.scene != GameScene.COMBAT and (
            self.valley_screen is not None or self.meeting_screen is not None
        )

    def _render_meeting_scene(self, renderer: SceneRenderer, font: BitmapFont) -> None:
        screen = self.meeting_screen
        if screen is None:
            raise ValueError("meeting scene has no caller")
        if screen.state.phase == MeetingPhase.MAP_OUT:
            self._render_overworld_scene(renderer, font)
        elif screen.state.phase == MeetingPhase.COMBAT_OUT:
            self._render_combat_scene(renderer, font)
        else:
            screen.render(renderer)

    def _render_valley_scene(self, renderer: SceneRenderer, font: BitmapFont) -> None:
        screen = self.valley_screen
        if screen is None:
            raise ValueError("Valley scene has no visit")
        if screen.state.phase == ValleyPhase.MAP_OUT:
            self._render_overworld_scene(renderer, font)
        elif screen.state.phase == ValleyPhase.COMBAT_OUT:
            self._render_combat_scene(renderer, font)
        else:
            screen.render(renderer)

    def _render_wizard_scene(self, renderer: SceneRenderer, font: BitmapFont) -> None:
        if self.wizard_screen is None:
            raise ValueError("wizard scene has no visit")
        if self.wizard_screen.state.phase == WizardPhase.MAP_OUT:
            self._render_overworld_scene(renderer, font)
        else:
            self.wizard_screen.render(renderer)

    def _render_stone_scene(self, renderer: SceneRenderer, font: BitmapFont) -> None:
        if self.stone_screen is None:
            raise ValueError("Stonehenge scene has no visit")
        if self.stone_screen.state.phase == StonePhase.MAP_OUT:
            self._render_overworld_scene(renderer, font)
        else:
            self.stone_screen.render(renderer)

    def _render_dice_scene(self, renderer: SceneRenderer) -> None:
        screen = self.dice_screen
        if screen is None:
            raise ValueError("dice scene has no screen")
        screen.render(renderer, self.actors[screen.run.state.knight - 1].gold)

    def _render_service_scene(self, renderer: SceneRenderer) -> None:
        if self.service_screen is None:
            raise ValueError("service scene has no screen")
        self.service_screen.render(renderer)

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


def _present_game_frame(
    session: GameSession,
    scene_renderer: SceneRenderer,
    base_renderer: HardDaysRenderer,
    font: BitmapFont,
    settings: DisplaySettings,
) -> None:
    cur_w, cur_h = pygame.display.get_window_size()
    session.render_to_scene(scene_renderer, font)
    if cur_w > 0 and cur_h > 0:
        base_renderer.present_to_screen((0, 0, cur_w, cur_h), settings=settings)
    pygame.display.set_caption(f"HardDaysEngine | {session.scene.name} | {session.last_status}")
    pygame.display.flip()


def _pump_game_events(window: WindowController, router: InputRouter, pacer: RefreshPacer) -> None:
    prev_mode = window.mode
    for event in pygame.event.get():
        if not window.consume_event(event):
            router.process_pygame_event(event)
    if window.mode is not prev_mode:
        pacer.set_refresh_rate(window.get_refresh_rate())


def run_game_window(
    adf_dir: Path,
    *,
    display: DisplaySettings | None = None,
    seed: int = DEFAULT_SEED,
    record_path: Path | None = None,
    replay_path: Path | None = None,
    max_ticks: int | None = None,
) -> int:
    """Open the interactive ``pygame-ce`` + ModernGL 3.3 Moonstone game window (§13)."""
    settings = display if display is not None else DisplaySettings()
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

    pygame.init()
    pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 3)
    pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 3)
    pygame.display.gl_set_attribute(pygame.GL_CONTEXT_PROFILE_MASK, pygame.GL_CONTEXT_PROFILE_CORE)
    window = WindowController(
        initial_window_size(settings), settings.fullscreen, vsync=settings.vsync
    )
    window.open()

    ctx = moderngl.create_context(require=330)
    base_renderer = HardDaysRenderer(ctx)
    scene_renderer = SceneRenderer(base_renderer)
    scene_renderer.set_palette(default_combat_palette())
    font = default_bitmap_font()
    router = InputRouter(init_joysticks=True)
    pacer = RefreshPacer(refresh_hz=window.get_refresh_rate(), vsync=settings.vsync)
    recorded: list[InputFrame] = []
    prev_ns = time.perf_counter_ns()
    replay_idx = 0

    try:
        running = True
        while running:
            _pump_game_events(window, router, pacer)
            now_ns = time.perf_counter_ns()
            step = pacer.step(now_ns - prev_ns)
            prev_ns = now_ns
            running, replay_idx = _step_window_ticks(
                step.ticks, session, router, replay_rec, replay_idx, recorded, max_ticks
            )
            if step.should_present:
                _present_game_frame(session, scene_renderer, base_renderer, font, settings)
            elif not settings.vsync:
                pygame.time.wait(1)
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
