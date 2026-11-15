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
from pymoonstone.app.config import find_adf_images
from pymoonstone.app.input import (
    InputFrame,
    InputRouter,
    PlayerInput,
    ReplayRecording,
    load_replay,
    save_replay,
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
from pymoonstone.assets.palette import default_combat_palette
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
    HealerService,
    distribute_lair_loot,
    perform_stonehenge_ritual,
    visit_healer,
)
from pymoonstone.sim.monster_ai import (
    LairEncounter,
    MonsterArchetype,
    MonsterSpawnSpec,
    MonsterState,
    TargetSnapshot,
    WaveSpec,
    apply_hit_to_monster,
    default_monster_params,
)
from pymoonstone.sim.overworld import (
    EncounterTrigger,
    OverworldState,
    TriggerKind,
    create_campaign,
    legal_moves,
    move_knight,
    resolve_lair_victory,
)
from pymoonstone.sim.prng import DEFAULT_SEED
from pymoonstone.sim.types import Direction, EntityId, KnightId, Pos25D

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    import numpy.typing as npt

_ATTACK_ACTIONS: frozenset[KnightAction] = frozenset(
    {
        KnightAction.OVERHEAD_SLASH,
        KnightAction.THRUST,
        KnightAction.LOW_SWEEP,
    }
)
_KNIGHT_MELEE_RANGE_X = 36


class GameScene(IntEnum):
    """Top-level game scene state machine (IMPLEMENTATION_PLAN §5 T8.3)."""

    TITLE = 0
    KNIGHT_SELECT = 1
    OVERWORLD = 2
    COMBAT = 3
    VICTORY = 4
    GAME_OVER = 5


@dataclass(slots=True)
class ActiveEncounter:
    """Runtime state for an active arena combat encounter."""

    trigger: EncounterTrigger
    encounter: LairEncounter
    knight_ids: tuple[KnightId, ...]
    swing_spent: dict[KnightId, bool] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class HeadlessSessionResult:
    """Output summary from :func:`run_headless_session`."""

    session: GameSession
    final_hash: str
    ticks_executed: int
    scene: GameScene
    recording: ReplayRecording


def _is_melee_in_range(
    attacker_x: int,
    attacker_y: int,
    attacker_dir: Direction,
    target_pos: Pos25D,
    reach_x: int,
    depth_threshold: int,
) -> bool:
    dx = target_pos.x - attacker_x
    facing_ok = (dx >= 0 and not attacker_dir.is_left) or (dx <= 0 and attacker_dir.is_left)
    return (
        facing_ok and abs(dx) <= reach_x and depth_close(attacker_y, target_pos.y, depth_threshold)
    )


def _make_knight_combat_state(knight_id: KnightId, index: int) -> KnightState:
    return KnightState(
        knight_id=knight_id,
        entity_id=EntityId(index + 1),
        x=80 + index * 24,
        y=95 + (index % 2) * 14,
        direction=Direction.RIGHT,
        input_port=int(knight_id),
    )


def _archetype_for_trigger(trigger: EncounterTrigger) -> MonsterArchetype:
    if trigger.kind == TriggerKind.DRAGON_INTERCEPT:
        return MonsterArchetype.DRAGON
    if trigger.kind == TriggerKind.LAIR_BOSS:
        lair_bosses = (
            MonsterArchetype.TROLL,
            MonsterArchetype.BALOK,
            MonsterArchetype.DEMON,
            MonsterArchetype.GUARDIAN,
        )
        idx = max(0, min(len(lair_bosses) - 1, trigger.lair_index))
        return lair_bosses[idx]
    wandering = (
        MonsterArchetype.TROGG_SPEAR,
        MonsterArchetype.RATMAN,
        MonsterArchetype.MUDMAN,
        MonsterArchetype.BEASTMAN,
    )
    return wandering[trigger.encounter_kind % len(wandering)]


def _build_encounter(
    trigger: EncounterTrigger, knight_ids: tuple[KnightId, ...]
) -> ActiveEncounter:
    archetype = _archetype_for_trigger(trigger)
    params = default_monster_params(archetype)
    spawns = (
        MonsterSpawnSpec(
            entity_id=EntityId(100),
            params=params,
            pos=Pos25D(x=240, y=95, z=0),
            direction=Direction.LEFT,
        ),
    )
    encounter = LairEncounter(waves=(WaveSpec(wave_index=0, spawns=spawns),))
    return ActiveEncounter(
        trigger=trigger,
        encounter=encounter,
        knight_ids=knight_ids,
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
        self.knight_pick_active: bool = False
        self.knight_pick_slot: int = 0
        self.knight_taken_mask: int = 0
        self.picked_knights: list[KnightId] = []
        self._menu_prev_dx: int = 0
        self._menu_prev_dy: int = 0
        self.selected_move_index: int = 0
        self.overworld: OverworldState = create_campaign(roster, seed=seed)
        self.knight_combat: dict[KnightId, KnightState] = {
            kid: _make_knight_combat_state(kid, idx) for idx, kid in enumerate(roster)
        }
        self.active_encounter: ActiveEncounter | None = None
        self.audio = audio
        self.disk_set = disk_set
        self.asset_bundle: GameAssetBundle | None = (
            GameAssetBundle(disk_set) if disk_set is not None else None
        )
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
        self.overworld = create_campaign(roster, seed=self.seed)
        self.knight_combat = {
            kid: _make_knight_combat_state(kid, idx) for idx, kid in enumerate(roster)
        }
        self.active_encounter = None

    def step_tick(self, frame: InputFrame) -> None:
        """Advance the game session by exactly one 50 Hz PAL VBL tick."""
        self.tick_count += 1
        primary = frame.for_player(self.players[0])
        if primary.pause and self.scene in (GameScene.OVERWORLD, GameScene.COMBAT):
            self.paused = not self.paused
            return
        if self.paused:
            return

        if self.scene == GameScene.TITLE:
            self._step_title(primary)
        elif self.scene == GameScene.KNIGHT_SELECT:
            self._step_knight_select(primary)
        elif self.scene == GameScene.OVERWORLD:
            self._step_overworld(frame)
        elif self.scene == GameScene.COMBAT:
            self._step_combat(frame)

        if self.audio is not None:
            self.audio.step_vbl()

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
        self.picked_knights.append(KnightId(slot))
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
        self._menu_prev_dx = primary.dx
        self._menu_prev_dy = primary.dy
        confirm = primary.confirm or primary.fire

        if self.knight_pick_active:
            self._step_knight_pick(dx_edge, confirm)
            return

        self._adjust_main_menu(dx_edge, dy_edge)
        if confirm:
            self._confirm_main_menu()

    def _step_overworld(self, frame: InputFrame) -> None:
        if self.overworld.game_over:
            self.scene = (
                GameScene.VICTORY if self.overworld.winner is not None else GameScene.GAME_OVER
            )
            return

        kid = self.overworld.current_knight_id
        inp = frame.for_player(kid)
        moves = legal_moves(self.overworld, kid)
        if not moves:
            self.scene = GameScene.GAME_OVER
            return

        if inp.dx > 0 or inp.dy > 0:
            self.selected_move_index = (self.selected_move_index + 1) % len(moves)
        elif inp.dx < 0 or inp.dy < 0:
            self.selected_move_index = (self.selected_move_index - 1) % len(moves)

        if inp.confirm or inp.fire:
            idx = self.selected_move_index % len(moves)
            target_node = moves[idx]
            result = move_knight(self.overworld, target_node)
            self.selected_move_index = 0
            self._handle_overworld_triggers(kid, result.triggers)

    def _handle_overworld_triggers(
        self,
        knight_id: KnightId,
        triggers: tuple[EncounterTrigger, ...],
    ) -> None:
        for trig in triggers:
            if trig.kind == TriggerKind.VICTORY:
                self.scene = GameScene.VICTORY
                self.last_status = f"{knight_id.name} WINS"
                return
            if trig.kind == TriggerKind.TOWN_VISIT:
                visit_healer(
                    self.knight_combat[knight_id],
                    service=HealerService.RESTORE_HP,
                    overworld=self.overworld,
                )
            elif trig.kind == TriggerKind.STONEHENGE_RITUAL:
                ritual = perform_stonehenge_ritual(
                    self.overworld,
                    knight_id,
                    knight_combat=self.knight_combat[knight_id],
                )
                if ritual.victory:
                    self.scene = GameScene.VICTORY
                    return
            elif trig.kind in (
                TriggerKind.WANDERING_MONSTER,
                TriggerKind.LAIR_BOSS,
                TriggerKind.DRAGON_INTERCEPT,
            ):
                self._start_combat(trig, knight_id)
                return

    def _start_combat(self, trigger: EncounterTrigger, knight_id: KnightId) -> None:
        kstate = self.knight_combat[knight_id]
        kstate.x = 80
        kstate.y = 95
        kstate.z = 0
        kstate.direction = Direction.RIGHT
        kstate.action = KnightAction.IDLE
        kstate.action_timer = 0
        self.active_encounter = _build_encounter(trigger, (knight_id,))
        self.scene = GameScene.COMBAT
        self.last_status = f"COMBAT: {trigger.kind.name}"

    def _step_combat(self, frame: InputFrame) -> None:
        active = self.active_encounter
        if active is None:
            self.scene = GameScene.OVERWORLD
            return

        targets: list[TargetSnapshot] = []
        for kid in active.knight_ids:
            kstate = self.knight_combat[kid]
            step_knight_vbl(
                kstate,
                frame.for_player(kid).to_knight_input(),
                prng=self.overworld.prng,
            )
            self._resolve_knight_melee(kstate, active)
            targets.append(
                TargetSnapshot(
                    entity_id=kstate.entity_id,
                    pos=kstate.pos,
                    alive=kstate.is_alive,
                    hp=kstate.hp,
                )
            )

        enc_res = active.encounter.step_vbl(targets, self.overworld.prng)
        self._resolve_monster_attacks(active, enc_res)

        if self.audio is not None:
            for mres in enc_res.monster_results:
                self.audio.handle_combat_events(mres.combat_events)

        self._check_combat_resolution(active)

    def _resolve_knight_melee(self, knight: KnightState, active: ActiveEncounter) -> None:
        if knight.action not in _ATTACK_ACTIONS:
            active.swing_spent[knight.knight_id] = False
            return
        if active.swing_spent.get(knight.knight_id, False):
            return
        for monster in active.encounter.monsters:
            if not monster.is_alive:
                continue
            if _is_melee_in_range(
                knight.x,
                knight.y,
                knight.direction,
                monster.pos,
                _KNIGHT_MELEE_RANGE_X,
                DEFAULT_DEPTH_THRESHOLD,
            ):
                apply_hit_to_monster(monster, knight.attack_damage(), knight.x)
                active.swing_spent[knight.knight_id] = True
                break

    def _apply_melee_intent(self, intent: object, active: ActiveEncounter) -> None:
        from pymoonstone.sim.monster_ai import MeleeAttackIntent

        if not isinstance(intent, MeleeAttackIntent):
            return
        for kid in active.knight_ids:
            defender = self.knight_combat[kid]
            if _is_melee_in_range(
                intent.pos.x,
                intent.pos.y,
                intent.direction,
                defender.pos,
                intent.reach_x,
                intent.depth_threshold,
            ):
                apply_knight_damage(
                    defender,
                    intent.damage,
                    attacker_id=intent.attacker_id,
                    attacker_pos=intent.pos,
                )

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
                apply_knight_damage(
                    by_entity[phit.target_id],
                    phit.damage,
                    attacker_id=phit.owner_id,
                    attacker_pos=phit.pos,
                )

    def _check_combat_resolution(self, active: ActiveEncounter) -> None:
        if active.encounter.completed:
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
            return

        if all(not self.knight_combat[kid].is_alive for kid in active.knight_ids):
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
                self.scene = GameScene.GAME_OVER
                self.last_status = "GAME OVER"
            else:
                self.scene = GameScene.OVERWORLD
                self.last_status = "KNIGHT FALLEN"

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
            f"NODE:{ow_k.node_id} -> {target}",
            104,
            236,
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

    def _overworld_sprites(self) -> list[SceneSprite]:
        if self.asset_bundle is None:
            return []
        sprites: list[SceneSprite] = []
        marker_cel = self.asset_bundle.get_cel("ov1.cel", "KN1.ob")
        if marker_cel is not None:
            for idx, kid in enumerate(self.overworld.turn_order):
                ow_k = self.overworld.knights[kid]
                if not ow_k.alive:
                    continue
                node = self.overworld.graph.nodes.get(ow_k.node_id)
                px = node.x if node is not None else 40 + idx * 32
                py = node.y if node is not None else 90
                sp = make_sprite(marker_cel, idx, x=px, y=py, layer=2, order=idx)
                if sp is not None:
                    sprites.append(sp)
        self._append_dragon_marker(sprites)
        return sprites

    def _append_dragon_marker(self, sprites: list[SceneSprite]) -> None:
        if self.asset_bundle is None:
            return
        dragon_cel = self.asset_bundle.get_cel("DRAGON1.CEL")
        dnode = self.overworld.graph.nodes.get(self.overworld.dragon.node_id)
        if dragon_cel is None or dnode is None:
            return
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
        return sprites

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
            scene_renderer.set_palette(pal)
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
                    scene_renderer.set_palette(menu_pal)
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

    def render_to_scene(self, scene_renderer: SceneRenderer, font: BitmapFont) -> None:
        """Render the active game scene and HUD onto ``scene_renderer``."""
        if self.scene == GameScene.TITLE:
            if self.asset_bundle is not None:
                bg, pal, sprites = build_intro_cinematic_frame(self.asset_bundle, self.tick_count)
                if bg is not None:
                    scene_renderer.set_palette(pal)
                    scene_renderer.compose_scene(background=bg, sprites=sprites, border_index=0)
                else:
                    self._compose_picture_or_solid(scene_renderer, "bg1a.PIV", "mindscape")
                    font.draw_text(scene_renderer.canvas, "PRESS ENTER", 116, 236, color_index=31)
            else:
                self._compose_picture_or_solid(scene_renderer, "bg1a.PIV", "mindscape")
                font.draw_text(scene_renderer.canvas, "PRESS ENTER", 116, 236, color_index=31)
        elif self.scene == GameScene.KNIGHT_SELECT:
            self._render_knight_select_scene(scene_renderer, font)
        elif self.scene == GameScene.OVERWORLD:
            self._compose_picture_or_solid(
                scene_renderer, "bg2.PIV", sprites=self._overworld_sprites()
            )
            self._render_overworld_hud(scene_renderer, font)
        elif self.scene == GameScene.COMBAT:
            self._render_combat_scene(scene_renderer, font)
        elif self.scene == GameScene.VICTORY:
            self._compose_picture_or_solid(scene_renderer, "WI1.P", "WI2.P")
            font.draw_text(scene_renderer.canvas, "VICTORY!", 128, 236, color_index=30)
        else:
            self._compose_picture_or_solid(scene_renderer, "message.piv")
            font.draw_text(scene_renderer.canvas, "GAME OVER", 124, 236, color_index=15)

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
