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
    render_new_day_screen,
    render_status_screen,
    step_location_menu,
)
from pymoonstone.app.loop import Accumulator50Hz
from pymoonstone.app.scene_assets import (
    GameAssetBundle,
    build_intro_cinematic_frame,
    make_sprite,
)
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
    OverworldHudState,
    SceneRenderer,
    SceneSprite,
    default_bitmap_font,
    render_overworld_hud,
)
from pymoonstone.sim.actor import Actor
from pymoonstone.sim.fight_setup import SpawnLanes, settle_defeats
from pymoonstone.sim.inventory import Inventory
from pymoonstone.sim.location_state import KnightState
from pymoonstone.sim.locations import (
    perform_stonehenge_ritual,
)
from pymoonstone.sim.m68k import s8, s16, u8, u16
from pymoonstone.sim.overworld import (
    EncounterTrigger,
    NodeKind,
    OverworldState,
    RegionId,
    TriggerKind,
    create_campaign,
    end_turn,
    legal_moves,
    move_knight,
    resolve_maths_tower,
    resolve_valley_of_the_gods,
    step_dragon_flight,
)
from pymoonstone.sim.palette_fx import PaletteFxState, pack_rgb12
from pymoonstone.sim.prng import DEFAULT_SEED
from pymoonstone.sim.rng import Rng
from pymoonstone.sim.types import KnightId

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    import numpy.typing as npt

    from pymoonstone.assets.cel import DecodedCel


_REGION_WANDERING_ARENAS: dict[int, str] = {
    int(RegionId.FOREST): "fo1.t",
    int(RegionId.GLEN): "gl1.t",
    int(RegionId.SWAMP): "sw1.t",
    int(RegionId.WASTELAND): "wa1.t",
    int(RegionId.VALLEY): "fo1.t",
}

_REGION_LAIR_ARENAS: tuple[str, ...] = ("fol1.t", "gll1.t", "swl1.t", "wal1.t")
_OW_REPEAT_INITIAL_TICKS = 12
_OW_REPEAT_STEP_TICKS = 6
_MI_LAIR_FRAME = 20
_MI_LAIR_HIGHLIGHT_FRAME = 31
_MI_DRAGON_BASE = 34
_MI_DRAGON_COUNT = 8
_MI_PLACE_IDS: frozenset[int] = frozenset({21, 22, 23, 24, 25, 26, 27, 28, 30})

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
        self._ow_prev_dx: int = 0
        self._ow_prev_dy: int = 0
        self._ow_repeat_ticks: int = 0
        self._ow_prev_fire: bool = False
        self._ow_prev_secondary: bool = False
        self._ow_prev_end_turn: bool = False
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
            kid: KnightState(knight_id=kid) for kid in roster
        }
        self.actors = [Actor(kind=_KNIGHT_KINDS[kid]) for kid in KnightId]
        self.dragon_actor = Actor()
        self.inventories = {handle: Inventory() for handle in range(1, 5)}
        self.fight_rng = Rng(seed)
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
        self.knight_combat = {kid: KnightState(knight_id=kid) for kid in roster}
        self.active_encounter = None
        self.location_menu = None
        self.location_knight_id = roster[0]
        self.practice_mode = False
        self.actors = [Actor(kind=_KNIGHT_KINDS[kid]) for kid in KnightId]
        self.dragon_actor = Actor()
        self.inventories = {handle: Inventory() for handle in range(1, 5)}
        self.fight_rng = Rng(self.seed)
        self.spawn_lanes = SpawnLanes()
        self.lair_counts.clear()
        self.bad_luck = self.last_defeat_mask = 0

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
        out = list(base_palette[:32])
        while len(out) < 32:
            out.append((0, 0, 0))
        phase = (self.tick_count // 3) % 10
        step = phase if phase <= 5 else 10 - phase
        level = 10 + step
        shade = level * 17
        out[31] = (shade, shade, shade)
        return tuple(out)

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
            self.scene = GameScene.OVERWORLD
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

    def _confirm_overworld_move(self, kid: KnightId, target_node: int) -> None:
        prev_day = self.overworld.day
        result = move_knight(self.overworld, target_node)
        self.selected_move_index = 0
        self._handle_overworld_triggers(kid, result.triggers)
        if self.scene == GameScene.OVERWORLD and self.overworld.day > prev_day:
            self.scene = GameScene.NEW_DAY
            self.last_status = f"DAY {self.overworld.day}"

    def _end_overworld_turn(self, kid: KnightId) -> None:
        prev_day = self.overworld.day
        result = end_turn(self.overworld)
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
        if delta == 0:
            self._ow_prev_dx = 0
            self._ow_prev_dy = 0
            self._ow_repeat_ticks = 0
            return
        dx_edge = inp.dx != 0 and inp.dx != self._ow_prev_dx
        dy_edge = inp.dy != 0 and inp.dy != self._ow_prev_dy
        self._ow_prev_dx = inp.dx
        self._ow_prev_dy = inp.dy
        if dx_edge or dy_edge:
            self._ow_repeat_ticks = _OW_REPEAT_INITIAL_TICKS
        elif self._ow_repeat_ticks > 1:
            self._ow_repeat_ticks -= 1
            return
        else:
            self._ow_repeat_ticks = _OW_REPEAT_STEP_TICKS
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

        end_turn_edge = inp.weapon_switch and not self._ow_prev_end_turn
        self._ow_prev_end_turn = inp.weapon_switch
        if end_turn_edge:
            self._end_overworld_turn(kid)
            return

        self._cycle_overworld_move(inp, len(moves))

        fire_edge = inp.fire and not self._ow_prev_fire
        self._ow_prev_fire = inp.fire
        if inp.confirm or fire_edge:
            self._confirm_overworld_move(kid, moves[self.selected_move_index % len(moves)])
        else:
            self._step_idle_dragon(kid)

    def _step_idle_dragon(self, kid: KnightId) -> None:
        if self.tick_count % 2 != 0:
            return
        dragon_trig = step_dragon_flight(self.overworld)
        if dragon_trig is not None:
            self._handle_overworld_triggers(kid, (dragon_trig,))

    def _open_location(self, knight_id: KnightId, location: LocationKind) -> None:
        self.location_knight_id = knight_id
        self.location_menu = LocationMenuState(location=location)
        self.scene = GameScene.LOCATION
        self.last_status = location.name

    def _handle_special_site_trigger(
        self,
        knight_id: KnightId,
        trig: EncounterTrigger,
    ) -> bool:
        ow_k = self.overworld.knights[knight_id]
        kstate = self.knight_combat[knight_id]
        if trig.kind == TriggerKind.HOME_CASTLE:
            kstate.lives = ow_k.lives
            kstate.hp = kstate.max_hp
            self.last_status = "HOME CASTLE"
            return True
        if trig.kind == TriggerKind.VALLEY_OF_THE_GODS:
            xp = resolve_valley_of_the_gods(self.overworld, knight_id)
            kstate.lives = ow_k.lives
            kstate.xp = ow_k.xp
            self.last_status = f"VALLEY BLESSING +{xp}XP"
            return True
        if trig.kind == TriggerKind.MATHS_TOWER:
            xp = resolve_maths_tower(self.overworld, knight_id)
            kstate.xp = ow_k.xp
            kstate.gold = ow_k.gold
            self.last_status = f"MATHS TOWER +{xp}XP"
            return True
        return False

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
                node = self.overworld.graph.nodes.get(trig.node_id)
                loc_kind = (
                    LocationKind.WATERDEEP
                    if node is not None and node.kind == NodeKind.WATERDEEP
                    else LocationKind.HIGHWOOD
                )
                self._open_location(knight_id, loc_kind)
                return
            if trig.kind == TriggerKind.STONEHENGE_RITUAL:
                self._open_stonehenge_or_victory(knight_id)
                return
            if self._handle_special_site_trigger(knight_id, trig):
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

    def _arena_name_for_trigger(self, trigger: EncounterTrigger) -> str:
        node = self.overworld.graph.nodes.get(trigger.node_id)
        if node is not None and node.arena_script:
            script = node.arena_script
            return script if script.lower().endswith(".t") else f"{script}.t"
        if trigger.kind == TriggerKind.LAIR_BOSS:
            lair_idx = max(0, trigger.lair_index)
            return _REGION_LAIR_ARENAS[lair_idx % len(_REGION_LAIR_ARENAS)]
        region_id = node.region_id if node is not None else int(RegionId.FOREST)
        return _REGION_WANDERING_ARENAS.get(region_id, "fo1.t")

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
                background, *args, first, int(second_id) + 1, lanes=self.spawn_lanes
            )
            return ActiveEncounter(trigger, runtime, (knight_id, second_id))
        if trigger.kind == TriggerKind.DRAGON_INTERCEPT:
            self._prepare_fight_knights(knight_id)
            runtime = loader.creature(
                loader.arena_for(0x14), background, *args, first, lanes=self.spawn_lanes
            )
            return ActiveEncounter(trigger, runtime, (knight_id,))
        if trigger.kind == TriggerKind.PVP_DUEL:
            opponent_id = trigger.opponent_knight_id
            if opponent_id is None or opponent_id not in self.players:
                raise FightUnavailableError("computer knight fights are not available yet")
            self._prepare_fight_knights(knight_id, opponent_id)
            runtime = loader.duel(
                background, *args, first, int(opponent_id) + 1, lanes=self.spawn_lanes
            )
            return ActiveEncounter(trigger, runtime, (knight_id, opponent_id))
        arena = loader.arena_for(trigger.creature_type)
        lair_count = self._fight_lair_count(trigger, loader)
        self._prepare_fight_knights(knight_id)
        runtime = loader.creature(
            arena, background, *args, first, lair_count=lair_count, lanes=self.spawn_lanes
        )
        return ActiveEncounter(trigger, runtime, (knight_id,))

    def _fight_lair_count(self, trigger: EncounterTrigger, loader: FightLoader) -> int | None:
        if trigger.kind != TriggerKind.LAIR_BOSS:
            return None
        if not 0 <= trigger.lair_index < len(loader.defs.lairs):
            raise FightUnavailableError("invalid lair index")
        return self.lair_counts.setdefault(
            trigger.lair_index, loader.defs.lairs[trigger.lair_index].creatures_left
        )

    def _prepare_fight_knights(self, *kids: KnightId) -> None:
        for kid in kids:
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
        try:
            active = self._fight_runtime(trigger, knight_id)
        except FightUnavailableError as exc:
            self.last_status = f"FIGHT UNAVAILABLE: {exc}"
            self.practice_mode = False
            return
        active.runtime.fight.engine.gore_off = not self.gore_enabled
        self.active_encounter = active
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
        for kid in active.knight_ids:
            actor, legacy = self.actors[int(kid)], self.knight_combat[kid]
            legacy.hp, legacy.daggers, legacy.lives = s16(actor.hp), actor.daggers, s8(actor.lives)
            ow = self.overworld.knights[kid]
            ow.lives, ow.alive = legacy.lives, legacy.lives > 0
        if active.trigger.kind == TriggerKind.LAIR_BOSS:
            self.lair_counts[active.trigger.lair_index] = fight.total
            if won:
                actor = fight.actor(active.runtime.first)
                actor.progress = u16(actor.progress + 1)
        self.bad_luck = 0
        active.runtime.release()
        if self.practice_mode:
            self._finish_practice_combat()
            return
        self.active_encounter = None
        self.overworld.moves_remaining = 0
        if all(not k.alive for k in self.overworld.knights.values()):
            self.overworld.game_over = True
            self._enter_game_over()
        else:
            self.scene = GameScene.OVERWORLD
            self.last_status = "FIGHT WON" if won else "KNIGHT FALLEN"

    def _render_combat_scene(self, scene_renderer: SceneRenderer, font: BitmapFont) -> None:
        if self.active_encounter is None:
            self._compose_picture_or_solid(scene_renderer)
        else:
            self.active_encounter.runtime.scene.render(scene_renderer)

    def state_hash(self) -> str:
        """Hash the campaign summary and mutable persistent fight state deterministically."""
        knights_payload = [asdict(self.knight_combat[kid]) for kid in self.overworld.turn_order]
        encounter_payload = (
            self.active_encounter.runtime.snapshot() if self.active_encounter is not None else None
        )
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
            "dragon": [
                self.overworld.dragon.node_id,
                self.overworld.dragon.patrol_index,
                self.overworld.dragon.x,
                self.overworld.dragon.y,
                int(self.overworld.dragon.facing_left),
            ],
            "knights": knights_payload,
            "encounter": encounter_payload,
            "fight_lanes": self.spawn_lanes.counter,
            "flyer_toggle": self.fight_loader.flyer.toggle if self.fight_loader else 0,
            "drake_cells": asdict(self.fight_loader.drake) if self.fight_loader else None,
            "lair_counts": self.lair_counts,
            "bad_luck": self.bad_luck,
            "defeats": self.last_defeat_mask,
            "fight_progress": [a.progress for a in self.actors],
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
            f"MOVES:{self.overworld.moves_remaining}",
            8,
            246,
            color_index=30,
        )
        font.draw_text(
            scene_renderer.canvas,
            f"NODE:{ow_k.node_id} TO {target}",
            104,
            246,
            color_index=31,
        )

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

    def _selected_target_node_id(self, active_kid: KnightId) -> int | None:
        moves = legal_moves(self.overworld, active_kid)
        if not moves:
            return None
        return moves[self.selected_move_index % len(moves)]

    def _highlighted_overworld_nodes(self, target_node_id: int | None) -> set[int]:
        highlighted = {
            self.overworld.knights[kid].node_id
            for kid in self.overworld.turn_order
            if self.overworld.knights[kid].alive
        }
        if target_node_id is not None:
            highlighted.add(target_node_id)
        return highlighted

    def _build_site_marker_sprite(
        self,
        mi_cel: DecodedCel,
        node_id: int,
        is_highlighted: bool,
    ) -> SceneSprite | None:
        node = self.overworld.graph.nodes[node_id]
        frame_count = len(mi_cel.frames)
        if node.kind == NodeKind.LAIR and node_id not in self.overworld.cleared_lair_nodes:
            use_hl = is_highlighted and frame_count > _MI_LAIR_HIGHLIGHT_FRAME
            frame_id = _MI_LAIR_HIGHLIGHT_FRAME if use_hl else _MI_LAIR_FRAME
            return make_sprite(
                mi_cel, frame_id, x=node.x, y=node.y, layer=2, order=50 + node_id, top_left=True
            )
        if node_id in _MI_PLACE_IDS and frame_count > node_id and is_highlighted:
            return make_sprite(
                mi_cel, node_id, x=node.x, y=node.y, layer=1, order=80 + node_id, top_left=True
            )
        return None

    def _append_lair_and_place_markers(
        self,
        mi_cel: DecodedCel,
        target_node_id: int | None,
        sprites: list[SceneSprite],
    ) -> None:
        if not mi_cel.frames:
            return
        highlighted = self._highlighted_overworld_nodes(target_node_id)
        for node_id in sorted(self.overworld.graph.nodes):
            sp = self._build_site_marker_sprite(mi_cel, node_id, node_id in highlighted)
            if sp is not None:
                sprites.append(sp)

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
            target_node_id = self._selected_target_node_id(active_kid)
            self._append_lair_and_place_markers(mi_cel, target_node_id, sprites)
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
        dragon = self.overworld.dragon
        mi_cel = self.asset_bundle.get_cel("mi.c")
        if mi_cel is not None and mi_cel.frames:
            span = min(_MI_DRAGON_COUNT, max(1, len(mi_cel.frames) - _MI_DRAGON_BASE))
            dframe = _MI_DRAGON_BASE + ((dragon.anim_phase + (self.tick_count // 6)) % span)
            dsp = make_sprite(
                mi_cel,
                dframe,
                x=dragon.x,
                y=dragon.y - 6,
                flipped=dragon.facing_left,
                layer=3,
                order=99,
                top_left=True,
            )
            if dsp is not None:
                sprites.append(dsp)
                return
        dnode = self.overworld.graph.nodes.get(dragon.node_id)
        if dnode is None:
            return
        dragon_cel = self.asset_bundle.get_cel("DRAGON1.CEL")
        if dragon_cel is not None:
            dsp = make_sprite(dragon_cel, 0, x=dnode.x, y=dnode.y, layer=3, order=99)
            if dsp is not None:
                sprites.append(dsp)

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
        sprites = self._overworld_sprites()
        map_pic = self.asset_bundle.get_test_picture(8) if self.asset_bundle is not None else None
        if map_pic is not None:
            self._apply_scene_palette(scene_renderer, self._overworld_palette(map_pic.palette))
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
