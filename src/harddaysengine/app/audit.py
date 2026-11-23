"""Real-disk audit harness: render every scene to labelled contact sheets (T11.1).

The harness drives a :class:`GameSession` on the user's own disks, renders each scene through
the GL canvas and writes PNG contact sheets for visual review (ADR-009, point 4). Its output is
derived from game data and must stay under the git-ignored ``build/`` directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import moderngl
import numpy as np
from PIL import Image, ImageDraw

from harddaysengine.app.ending_audit import ending_shots
from harddaysengine.app.fight_audit import (
    TROGG_ARENAS,
    build_caster_audit,
    build_computer_knight_audit,
    build_dragon_audit,
    build_drake_audit,
    build_fight_audit,
    build_flyer_audit,
    build_guardian_audit,
    build_snatcher_audit,
    build_stalker_audit,
    caster_pose_frames,
    computer_knight_pose_frames,
    dragon_pose_frames,
    drake_pose_frames,
    fight_audit_frames,
    fight_host_input,
    fight_host_melee_input,
    flyer_pose_frames,
    guardian_pose_frames,
    snatcher_pose_frames,
    stalker_pose_frames,
)
from harddaysengine.app.fight_runtime import FightLoader
from harddaysengine.app.game import GameScene, GameSession
from harddaysengine.app.hd_host import FightHdHistory, HdHostPresenter
from harddaysengine.app.input import InputFrame, PlayerInput
from harddaysengine.app.map_scene import compose_map
from harddaysengine.app.meeting_audit import meeting_shots
from harddaysengine.app.valley_audit import valley_shots
from harddaysengine.app.village_audit import village_exit, village_shots, village_temple
from harddaysengine.app.wizard_audit import wizard_shots
from harddaysengine.assets.adf import DiskSet
from harddaysengine.assets.cel import flip_cel_frame
from harddaysengine.assets.hit import HitFrame, HitSet, parse_collide_hit
from harddaysengine.assets.palette import default_combat_palette
from harddaysengine.render.overlay import OverlayLine, build_frame_overlay
from harddaysengine.render.renderer import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    PLAYFIELD_Y_OFFSET,
    HardDaysRenderer,
)
from harddaysengine.render.scene import SceneRenderer, default_bitmap_font
from harddaysengine.sim.m68k import s16
from harddaysengine.sim.overworld import EncounterTrigger, MapSprite, TriggerKind, open_menu, scan
from harddaysengine.sim.stonehenge import StonePhase
from harddaysengine.sim.towns import TownEntry
from harddaysengine.sim.types import KnightId

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping, Sequence

    import numpy.typing as npt

    from harddaysengine.app.fight_scene import FightScene
    from harddaysengine.app.scene_assets import GameAssetBundle
    from harddaysengine.assets.cel import DecodedCel
    from harddaysengine.assets.hd_pack import HdPack
    from harddaysengine.render.presentation import DisplaySettings
    from harddaysengine.sim.fight_script import Draw

_PRIMARY = KnightId.RED
_LABEL_HEIGHT = 12
_SHEET_COLUMNS = 4
DEFAULT_PRESENT_SIZE: tuple[int, int] = (1920, 1080)
PRESENTATION_SCENARIOS: tuple[str, ...] = (
    "presentation",
    "menu_map",
    "places",
    "fights",
    "next_day",
)
_INTRO_SAMPLE_TICKS: tuple[int, ...] = (50, 415, 900, 1500, 2500, 3250, 4100, 5050)
_REGION_NAMES: tuple[str, ...] = ("forest", "glen", "swamp", "wasteland")
_NO_NODE = -1
_END_SCENES: tuple[GameScene, ...] = (
    GameScene.STATUS,
    GameScene.NEW_DAY,
    GameScene.GAME_OVER,
)


@dataclass(slots=True, frozen=True)
class AuditShot:
    """One captured canvas with the label shown on the contact sheet."""

    name: str
    rgb: npt.NDArray[np.uint8]


def _frame(player_input: PlayerInput | None = None) -> InputFrame:
    if player_input is None:
        return InputFrame()
    return InputFrame(players={_PRIMARY: player_input})


def _step(session: GameSession, ticks: int, player_input: PlayerInput | None = None) -> None:
    frame = _frame(player_input)
    for _ in range(ticks):
        session.step_tick(frame)


def _tap(session: GameSession, player_input: PlayerInput) -> None:
    """Press for one tick, then release, so edge-triggered handlers see one press."""
    _step(session, 1, player_input)
    _step(session, 2)


def parse_present_size(spec: str) -> tuple[int, int]:
    """Parse a ``WxH`` presentation resolution string such as ``1920x1080`` (T13.9)."""
    parts = spec.lower().split("x")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        msg = f"size must be WxH positive integers (e.g. 1920x1080), got {spec!r}"
        raise ValueError(msg)
    width, height = int(parts[0]), int(parts[1])
    if width < 1 or height < 1:
        msg = f"size dimensions must be >= 1, got {spec!r}"
        raise ValueError(msg)
    return (width, height)


def compose_side_by_side(
    classic_rgb: npt.NDArray[np.uint8],
    hd_rgb: npt.NDArray[np.uint8],
) -> npt.NDArray[np.uint8]:
    """Combine Classic (left) and HD (right) RGB frames at matching dimensions (§14.7)."""
    target_h, target_w = hd_rgb.shape[:2]
    src_h, src_w = classic_rgb.shape[:2]
    if (src_h, src_w) != (target_h, target_w):
        if target_h % src_h == 0 and target_w % src_w == 0:
            left = np.repeat(
                np.repeat(classic_rgb, target_h // src_h, axis=0),
                target_w // src_w,
                axis=1,
            )
        else:
            resized = Image.fromarray(classic_rgb).resize(
                (target_w, target_h), Image.Resampling.NEAREST
            )
            left = np.asarray(resized, dtype=np.uint8)
    else:
        left = classic_rgb
    return np.ascontiguousarray(np.concatenate((left, hd_rgb), axis=1))


def _resolve_hit_frame(
    hit_sets: Mapping[str, HitSet], cel_name: str, frame_index: int
) -> HitFrame | None:
    hit_set = hit_sets.get(Path(cel_name).stem.lower())
    if hit_set is None or frame_index >= len(hit_set.frames):
        return None
    return hit_set.frames[frame_index]


def _draw_hitbox_lines(
    draw: Draw,
    cels: Mapping[str, DecodedCel],
    hit_sets: Mapping[str, HitSet],
    y_offset: int,
) -> list[OverlayLine]:
    if draw.stamp or draw.text:
        return []
    cel = cels.get(draw.cel)
    if cel is None or not 0 <= draw.frame < len(cel.frames):
        return []
    raw = cel.frames[draw.frame]
    if raw.meta.width <= 0 or raw.meta.height <= 0:
        return []
    shift = 0 if draw.mirrored else raw.meta.hot_x
    draw_x = s16(draw.x) - shift
    draw_y = s16(draw.y) + PLAYFIELD_Y_OFFSET + y_offset
    frame = flip_cel_frame(raw) if draw.mirrored else raw
    hit_frame = _resolve_hit_frame(hit_sets, draw.cel, draw.frame)
    return build_frame_overlay(draw_x, draw_y, frame, hit_frame)


def _build_fight_hitbox_lines(
    history: FightHdHistory,
    hit_sets: Mapping[str, HitSet],
    *,
    y_offset: int = 0,
) -> list[OverlayLine]:
    lines: list[OverlayLine] = []
    for draw in history.last_draws:
        lines.extend(_draw_hitbox_lines(draw, history.cels, hit_sets, y_offset))
    return lines


class AuditRig:
    """Standalone GL canvas plus the session factory used by all audit scenarios."""

    def __init__(
        self,
        adf_dir: Path,
        *,
        display: DisplaySettings | None = None,
        present_size: tuple[int, int] | None = None,
        hd_pack: HdPack | None = None,
        show_hitboxes: bool = False,
    ) -> None:
        self._adf_dir = adf_dir
        self._ctx = moderngl.create_standalone_context(require=330)
        self._base = HardDaysRenderer(self._ctx)
        self._scene = SceneRenderer(self._base)
        self._scene.set_palette(default_combat_palette())
        self._font = default_bitmap_font()
        self._display = display
        self._hd_host: HdHostPresenter | None = (
            HdHostPresenter(hd_pack) if hd_pack is not None else None
        )
        self._show_hitboxes = show_hitboxes
        self._hit_sets: dict[str, HitSet] | None = None
        self._present_size: tuple[int, int] | None = None
        self._present_tex: moderngl.Texture | None = None
        self._present_fbo: moderngl.Framebuffer | None = None
        if display is not None:
            size = DEFAULT_PRESENT_SIZE if present_size is None else present_size
            if size[0] < 1 or size[1] < 1:
                msg = f"present_size must be positive, got {size!r}"
                raise ValueError(msg)
            self._present_size = size
            self._present_tex = self._ctx.texture(size, 4)
            self._present_fbo = self._ctx.framebuffer(color_attachments=[self._present_tex])

    def _load_hit_sets(self) -> dict[str, HitSet]:
        if self._hit_sets is None:
            disks = DiskSet.from_directory(self._adf_dir)
            self._hit_sets = (
                parse_collide_hit(disks.read_file("collide.hit"))
                if disks.has_file("collide.hit")
                else {}
            )
        return self._hit_sets

    def _draw_overlay_classic(self, lines: Sequence[OverlayLine]) -> None:
        if not lines:
            return
        self._base.canvas_fbo.use()
        self._ctx.viewport = (0, 0, CANVAS_WIDTH, CANVAS_HEIGHT)
        self._base.draw_overlay_lines(lines)

    def _read_present_rgb(
        self,
        *,
        hd_tex: moderngl.Texture | None = None,
        hd_scale: int = 1,
    ) -> npt.NDArray[np.uint8]:
        assert self._present_fbo is not None
        assert self._display is not None
        assert self._present_size is not None
        width, height = self._present_size
        self._base.present_to_framebuffer(
            self._present_fbo,
            (width, height),
            settings=self._display,
            hd_tex=hd_tex,
            hd_scale=hd_scale,
        )
        raw = self._present_fbo.read(components=4)
        arr = np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 4))
        return np.ascontiguousarray(arr[::-1, :, :3])

    def _read_classic_rgb(self) -> npt.NDArray[np.uint8]:
        if self._present_fbo is not None:
            return self._read_present_rgb()
        rgba = self._base.read_canvas_rgba()
        return np.ascontiguousarray(rgba[..., :3])

    def _read_hd_rgb(self, hd_tex: moderngl.Texture | None) -> npt.NDArray[np.uint8]:
        assert self._hd_host is not None
        compositor = self._hd_host.compositor
        if hd_tex is None or compositor is None:
            classic = self._read_classic_rgb()
            if self._present_fbo is not None:
                return classic
            scale = self._hd_host.scale
            return np.ascontiguousarray(np.repeat(np.repeat(classic, scale, axis=0), scale, axis=1))
        if self._present_fbo is not None:
            return self._read_present_rgb(hd_tex=hd_tex, hd_scale=self._hd_host.scale)
        rgba = compositor.read_canvas_rgba()
        return np.ascontiguousarray(rgba[..., :3])

    def _read_shot(self, name: str, *, hd_tex: moderngl.Texture | None = None) -> AuditShot:
        classic = self._read_classic_rgb()
        if self._hd_host is None:
            return AuditShot(name=name, rgb=classic)
        hd_rgb = self._read_hd_rgb(hd_tex)
        return AuditShot(name=name, rgb=compose_side_by_side(classic, hd_rgb))

    def _fight_overlay_lines(
        self,
        history: FightHdHistory | None,
        *,
        y_offset: int = 0,
    ) -> list[OverlayLine]:
        if not self._show_hitboxes or history is None:
            return []
        return _build_fight_hitbox_lines(history, self._load_hit_sets(), y_offset=y_offset)

    def _apply_hd_overlay(
        self,
        hd_tex: moderngl.Texture | None,
        lines: Sequence[OverlayLine],
    ) -> None:
        if hd_tex is None or not lines or self._hd_host is None:
            return
        compositor = self._hd_host.compositor
        if compositor is None:
            return
        compositor.output_fbo.use()
        self._ctx.viewport = (0, 0, compositor.canvas_size[0], compositor.canvas_size[1])
        self._base.draw_overlay_lines(lines)

    def new_session(self, players: tuple[KnightId, ...] = (_PRIMARY,)) -> GameSession:
        """Return a fresh single-player session on the real disks."""
        return GameSession(disk_set=DiskSet.from_directory(self._adf_dir), players=players)

    def capture(self, session: GameSession, name: str) -> AuditShot:
        """Render the session's current scene and return its canvas or presented frame as RGB."""
        session.render_to_scene(self._scene, self._font)
        fight_scene = (
            session.active_encounter.runtime.scene if session.active_encounter is not None else None
        )
        lines = self._fight_overlay_lines(
            fight_scene.hd_history if fight_scene is not None else None,
            y_offset=fight_scene.fight.shake.offset if fight_scene is not None else 0,
        )
        self._draw_overlay_classic(lines)
        hd_tex: moderngl.Texture | None = None
        if self._hd_host is not None:
            compositor = self._hd_host.ensure_active(self._ctx, enabled=True)
            if compositor is not None:
                hd_tex = self._hd_host.render_session_hd(session, compositor)
                self._apply_hd_overlay(hd_tex, lines)
        return self._read_shot(name, hd_tex=hd_tex)

    def capture_fight(self, scene: FightScene, name: str) -> AuditShot:
        """Capture the persistent engine's last output without stepping it (§10.17)."""
        scene.render(self._scene)
        self._scene.flush_to_fbo()
        lines = self._fight_overlay_lines(scene.hd_history, y_offset=scene.fight.shake.offset)
        self._draw_overlay_classic(lines)
        hd_tex: moderngl.Texture | None = None
        if self._hd_host is not None:
            compositor = self._hd_host.ensure_active(self._ctx, enabled=True)
            if compositor is not None:
                hd_tex = self._hd_host.render_fight_history(
                    compositor,
                    scene.hd_history,
                    live_palette=scene.palette,
                    background_y_offset=scene.fight.shake.offset,
                )
                self._apply_hd_overlay(hd_tex, lines)
        return self._read_shot(name, hd_tex=hd_tex)

    def capture_map_frames(self, frames: tuple[int, ...]) -> AuditShot:
        """Preview every used mi.c frame separately from natural map captures (§11.15)."""
        bundle = self.fight_assets()
        picture, cel = bundle.get_test_picture(8), bundle.get_cel("mi.c")
        if picture is None or cel is None:
            raise ValueError("map previews require the original picture and sprites")
        sprites = tuple(MapSprite(frame, 60 + index * 25, 90) for index, frame in enumerate(frames))
        compose_map(self._scene.canvas, picture.pixels, cel, sprites)
        self._scene.set_palette(picture.palette)
        self._scene.flush_to_fbo()
        hd_tex: moderngl.Texture | None = None
        if self._hd_host is not None:
            compositor = self._hd_host.ensure_active(self._ctx, enabled=True)
            if compositor is not None:
                compositor.set_palettes(picture.palette, picture.palette)
                compositor.begin_scene()
                compositor.draw_picture("test_8", picture.pixels[:200, :320])
                for sprite in sprites:
                    compositor.draw_cel_frame(
                        "mi.c",
                        cel.frames[sprite.frame],
                        sprite.x,
                        sprite.y + PLAYFIELD_Y_OFFSET,
                    )
                hd_tex = compositor.output_texture
        return self._read_shot("mi_frames_" + "_".join(map(str, frames)), hd_tex=hd_tex)

    def fight_assets(self) -> GameAssetBundle:
        """Return the real-disk bundle used for fight-engine audit fixtures."""
        from harddaysengine.app.scene_assets import GameAssetBundle

        return GameAssetBundle(DiskSet.from_directory(self._adf_dir))

    def release(self) -> None:
        """Free the GL resources."""
        if self._hd_host is not None:
            self._hd_host.release()
            self._hd_host = None
        if self._present_fbo is not None:
            self._present_fbo.release()
            self._present_fbo = None
        if self._present_tex is not None:
            self._present_tex.release()
            self._present_tex = None
        self._scene.release()
        self._base.release()
        self._ctx.release()


def _intro_shots(rig: AuditRig) -> Iterator[AuditShot]:
    session = rig.new_session()
    for tick in _INTRO_SAMPLE_TICKS:
        while session.tick_count < tick and session.scene == GameScene.TITLE:
            _step(session, 1)
        yield rig.capture(session, f"intro_t{tick}")


def _menu_and_map_shots(rig: AuditRig) -> Iterator[AuditShot]:
    session = rig.new_session()
    _step(session, 5)
    _tap(session, PlayerInput(skip_intro=True))
    _step(session, 40)
    yield rig.capture(session, "main_menu")
    for _ in range(3):
        _tap(session, PlayerInput(dy=1))
    _tap(session, PlayerInput(confirm=True))
    _step(session, 30)
    yield rig.capture(session, "knight_select")
    _tap(session, PlayerInput(confirm=True))
    _step(session, 60)
    yield rig.capture(session, "map_start")
    for move in range(3):
        _tap(session, PlayerInput(dx=1))
        _tap(session, PlayerInput(confirm=True))
        _step(session, 30)
        name = f"map_move{move}_{session.scene.name.lower()}"
        village = session.scene == GameScene.VILLAGE
        if village:
            village_temple(session)
        yield rig.capture(session, name)
        if village:
            village_exit(session)
    for scene in _END_SCENES:
        session.scene = scene
        _step(session, 20)
        yield rig.capture(session, f"screen_{scene.name.lower()}")


def _pixel_map_shots(rig: AuditRig) -> Iterator[AuditShot]:
    """Original map frames, proximity highlights and the numbered panel (§11.18)."""
    session = rig.new_session()
    session.scene = GameScene.OVERWORLD
    _step(session, 40)
    yield rig.capture(session, "pixel_start")
    _step(session, 16, PlayerInput(dx=-1))
    yield rig.capture(session, "pixel_walk")
    world = session.overworld
    node = next(n for n in world.data().nodes if n.kind == 25)
    actor = world.current_actor
    actor.map_x, actor.map_y = node.x, node.y
    world.move = 0
    scan(world)
    yield rig.capture(session, "place_highlight")
    lair = world.lairs[0]
    actor.map_x, actor.map_y = lair.map_x, lair.map_y
    scan(world)
    yield rig.capture(session, "lair_highlight")
    other = world.actors[1]
    other.map_x, other.map_y = actor.map_x, actor.map_y
    scan(world)
    open_menu(world)
    yield rig.capture(session, "numbered_arrivals")
    world.menu = False
    world.travel = True
    scan(world)
    yield rig.capture(session, "travel_frame")
    world.ambush = True
    scan(world)
    yield rig.capture(session, "ambush_frame")
    for frames in (
        tuple(range(0, 5)),
        tuple(range(5, 10)),
        tuple(range(10, 15)),
        tuple(range(15, 20)),
        tuple(range(20, 25)),
        tuple(range(25, 32)),
        (32,),
        (33, 42),
        tuple(range(43, 47)),
    ):
        yield rig.capture_map_frames(frames)


def _turn_and_dragon_shots(rig: AuditRig) -> Iterator[AuditShot]:
    """Four-human round boundary, loaded moon previews and genuine flight jobs (§11.19)."""
    session = rig.new_session(tuple(KnightId))
    session.scene = GameScene.OVERWORLD
    for turn in range(4):
        _step(session, 20)
        yield rig.capture(session, f"human_turn{turn}")
        session.step_tick(InputFrame(key=ord("E")))
        session.step_tick(InputFrame(key=ord("E")))
    _step(session, 40)
    yield rig.capture(session, "natural_round_end")
    rules = session.overworld.turn_rules
    if rules is None:
        raise ValueError("calendar audit requires original rules")
    for index, frame in enumerate(rules.moons):
        session.overworld.moon_frame = frame
        yield rig.capture(session, f"moon_preview{index}_frame{frame}")
    session = rig.new_session(tuple(KnightId))
    session.scene = GameScene.OVERWORLD
    session.overworld.day = 2
    session._reenter_map()
    for tick in (2, 10, 40, 100, 160, 240, 320):
        while session.tick_count < tick:
            _step(session, 1)
        yield rig.capture(session, f"dragon_flight_t{tick}")
    session = rig.new_session()
    session.scene = GameScene.OVERWORLD
    session.step_tick(InputFrame(key=ord("Q")))
    session.step_tick(InputFrame(key=ord("Q")))
    _step(session, 30)
    yield rig.capture(session, "farewell")


def _settle_fade(session: GameSession) -> None:
    """Wait through blocking PAL fades, including chained town entry (§12.33)."""
    for _ in range(128):
        if not session.palette_fx.is_fading and not session.blocking_fade:
            return
        _step(session, 1)
    raise ValueError("audit fade did not finish")


def _audit_return_town(session: GameSession) -> None:
    """Close actual child screens using their original regions and fire waits."""
    if session.sheet_screen is not None:
        screen = session.sheet_screen
        done = next(r for r in screen.state.regions if r.id == 7)
        screen.state.pointer_x, screen.state.pointer_y = done.x, done.y
        _tap(session, PlayerInput(fire=True))
    elif session.service_screen is not None:
        _tap(session, PlayerInput(fire=True))
        _audit_service_region(session, 2, 1)
        _step(session, session.service_screen.screens.close_wait)
        _tap(session, PlayerInput(fire=True))
    elif session.dice_screen is not None:
        run = session.dice_screen.run
        leave = next(r for r in run.house.regions if r.kind == 2)
        run.state.pointer_x, run.state.pointer_y = leave.x, leave.y
        _step(session, 12, PlayerInput(fire=True))
    _settle_fade(session)
    if session.scene != GameScene.LOCATION:
        raise ValueError("town child did not return to its retained parent")


def _place_shots(rig: AuditRig) -> Iterator[AuditShot]:
    """Both welcomes, all ten button outcomes, child returns and spent exits (§12.33)."""
    for waterdeep in (False, True):
        name = "waterdeep" if waterdeep else "highwood"
        session = rig.new_session()
        session.actors[_PRIMARY].gold = 40
        session._open_town(_PRIMARY, waterdeep=waterdeep)
        town = session.town_screen
        if town is None:
            raise ValueError("town audit requires original town assets")
        while town.state.entry == TownEntry.MAP_OUT:
            _step(session, 1)
        yield rig.capture(session, f"{name}_welcome")
        _settle_fade(session)
        yield rig.capture(session, f"{name}_town")
        for button in range(1, 5):
            _audit_town_button(session, button)
            _settle_fade(session)
            yield rig.capture(session, f"{name}_button{button}_screen")
            _audit_return_town(session)
            yield rig.capture(session, f"{name}_button{button}_returned")
        session.step_tick(InputFrame(key=32))
        _settle_fade(session)
        yield rig.capture(session, f"{name}_space")
        _audit_return_town(session)
        _audit_town_button(session, 5)
        _settle_fade(session)
        yield rig.capture(session, f"{name}_button5_spent_exit")


def _host_fight_session(rig: AuditRig, name: str, lair_index: int = -1) -> GameSession:
    """Equipped audit knight on the real campaign host; these are not campaign defaults."""
    session = rig.new_session()
    session.scene = GameScene.OVERWORLD
    if session.asset_bundle is None:
        raise ValueError("fight audit requires the original assets")
    session.fight_loader = FightLoader(session.asset_bundle)
    arena = next(a for a in session.fight_loader.defs.arenas if a.name == name)
    knight = session.knight_combat[_PRIMARY]
    knight.hp = knight.max_hp = 50
    knight.strength, knight.sword_tier, knight.daggers = 3, 4, 99
    session._start_combat(
        EncounterTrigger(
            kind=TriggerKind.LAIR_BOSS if lair_index >= 0 else TriggerKind.WANDERING_MONSTER,
            knight_id=_PRIMARY,
            node_id=_NO_NODE,
            day=1,
            moon_phase=session.overworld.moon_phase,
            lair_index=lair_index,
            creature_type=arena.handler_offsets[0],
        ),
        _PRIMARY,
    )
    if session.active_encounter is None:
        raise RuntimeError(session.last_status)
    return session


def _fight_shots(rig: AuditRig) -> Iterator[AuditShot]:
    for name in TROGG_ARENAS:
        session = _host_fight_session(rig, name)
        for tick in (6, 120, 240, 480, 960):
            while session.tick_count < tick and session.active_encounter is not None:
                session.step_tick(fight_host_input(session))
            if session.active_encounter is None:
                break
            yield rig.capture(session, f"host_{name}_vbl{tick}")


def _region_shots(rig: AuditRig) -> Iterator[AuditShot]:
    """One supported Trogg fight per region, through the real campaign host (§10.13)."""
    for lair_index, region in enumerate(_REGION_NAMES):
        session = _host_fight_session(rig, TROGG_ARENAS[0], lair_index)
        for _ in range(60):
            session.step_tick(fight_host_input(session))
        yield rig.capture(session, f"region_{region}")


def _engine_fight_shots(rig: AuditRig) -> Iterator[AuditShot]:
    bundle = rig.fight_assets()
    for name in TROGG_ARENAS:
        audit = build_fight_audit(bundle, name)
        for label, scene in fight_audit_frames(audit):
            yield rig.capture_fight(scene, f"engine_{name}_{label}")


def _caster_shots(rig: AuditRig) -> Iterator[AuditShot]:
    bundle = rig.fight_assets()
    for label, scene in caster_pose_frames(bundle):
        yield rig.capture_fight(scene, f"ratmen_pose_{label}")
    for label, scene in fight_audit_frames(build_caster_audit(bundle)):
        yield rig.capture_fight(scene, f"ratmen_seeded_{label}")


def _snatcher_shots(rig: AuditRig) -> Iterator[AuditShot]:
    bundle = rig.fight_assets()
    for label, scene in snatcher_pose_frames(bundle):
        yield rig.capture_fight(scene, f"mudmen_pose_{label}")
    for label, scene in fight_audit_frames(build_snatcher_audit(bundle)):
        yield rig.capture_fight(scene, f"mudmen_seeded_{label}")
    audit = build_snatcher_audit(bundle)
    for frame in range(300):
        audit.scene.advance(frame * 6)
        if frame in (5, 12, 20):
            yield rig.capture_fight(audit.scene, f"mudmen_idle_loss_frame{frame + 1}")
        if not audit.scene.fight.fight_active:
            yield rig.capture_fight(audit.scene, f"mudmen_idle_loss_outcome{frame + 1}")
            return
    raise RuntimeError("idle Mudmen audit did not reach an outcome")


def _flyer_shots(rig: AuditRig) -> Iterator[AuditShot]:
    bundle = rig.fight_assets()
    for label, scene in flyer_pose_frames(bundle):
        yield rig.capture_fight(scene, f"flyer_pose_{label}")
    for label, scene in fight_audit_frames(build_flyer_audit(bundle)):
        yield rig.capture_fight(scene, f"flyer_seeded_{label}")
    audit = build_flyer_audit(bundle)
    audit.scene.fight.actor(1).hp = 10
    for frame in range(300):
        audit.scene.advance(frame * 6)
        if frame in (20, 40, 60):
            yield rig.capture_fight(audit.scene, f"flyer_idle_loss_frame{frame + 1}")
        if not audit.scene.fight.fight_active:
            yield rig.capture_fight(audit.scene, f"flyer_idle_loss_outcome{frame + 1}")
            return
    raise RuntimeError("idle Flyer audit did not reach an outcome")


def _stalker_shots(rig: AuditRig) -> Iterator[AuditShot]:
    bundle = rig.fight_assets()
    for label, scene in stalker_pose_frames(bundle):
        yield rig.capture_fight(scene, f"troll_pose_{label}")
    for label, scene in fight_audit_frames(build_stalker_audit(bundle)):
        yield rig.capture_fight(scene, f"troll_seeded_{label}")
    audit = build_stalker_audit(bundle)
    audit.scene.fight.actor(1).hp = 20
    for frame in range(300):
        audit.scene.advance(frame * 6)
        if frame in (10, 20, 40):
            yield rig.capture_fight(audit.scene, f"troll_idle_loss_frame{frame + 1}")
        if not audit.scene.fight.fight_active:
            yield rig.capture_fight(audit.scene, f"troll_idle_loss_outcome{frame + 1}")
            return
    raise RuntimeError("idle Troll audit did not reach an outcome")


def _drake_shots(rig: AuditRig) -> Iterator[AuditShot]:
    bundle = rig.fight_assets()
    for label, scene in drake_pose_frames(bundle):
        yield rig.capture_fight(scene, f"balok_pose_{label}")
    for label, scene in fight_audit_frames(build_drake_audit(bundle)):
        yield rig.capture_fight(scene, f"balok_seeded_{label}")
    audit = build_drake_audit(bundle)
    audit.scene.fight.actor(1).hp = 20
    for frame in range(1000):
        audit.scene.advance(frame * 6)
        if frame in (20, 60, 120, 180):
            yield rig.capture_fight(audit.scene, f"balok_idle_loss_frame{frame + 1}")
        if not audit.scene.fight.fight_active:
            yield rig.capture_fight(audit.scene, f"balok_idle_loss_outcome{frame + 1}")
            return
    raise RuntimeError("idle Balok audit did not reach an outcome")


def _guardian_shots(rig: AuditRig) -> Iterator[AuditShot]:
    bundle = rig.fight_assets()
    for label, scene in guardian_pose_frames(bundle):
        yield rig.capture_fight(scene, f"guardian_pose_{label}")
    for label, scene in fight_audit_frames(build_guardian_audit(bundle)):
        yield rig.capture_fight(scene, f"guardian_seeded_{label}")
    audit = build_guardian_audit(bundle)
    audit.scene.fight.actor(1).hp = 20
    for frame in range(1000):
        audit.scene.advance(frame * 6)
        if frame in (20, 60, 120, 180):
            yield rig.capture_fight(audit.scene, f"guardian_idle_loss_frame{frame + 1}")
        if not audit.scene.fight.fight_active:
            yield rig.capture_fight(audit.scene, f"guardian_idle_loss_outcome{frame + 1}")
            return
    raise RuntimeError("idle Guardian audit did not reach an outcome")


def _dragon_shots(rig: AuditRig) -> Iterator[AuditShot]:
    bundle = rig.fight_assets()
    for label, scene in dragon_pose_frames(bundle):
        yield rig.capture_fight(scene, f"dragon_pose_{label}")
    for label, scene in fight_audit_frames(build_dragon_audit(bundle)):
        yield rig.capture_fight(scene, f"dragon_seeded_{label}")
    audit = build_dragon_audit(bundle)
    audit.scene.fight.actor(1).hp = 20
    for frame in range(1000):
        audit.scene.advance(frame * 6)
        if frame in (20, 60, 120, 180):
            yield rig.capture_fight(audit.scene, f"dragon_idle_loss_frame{frame + 1}")
        if not audit.scene.fight.fight_active:
            yield rig.capture_fight(audit.scene, f"dragon_idle_loss_outcome{frame + 1}")
            return
    raise RuntimeError("idle Dragon audit did not reach an outcome")


def _computer_knight_shots(rig: AuditRig) -> Iterator[AuditShot]:
    bundle = rig.fight_assets()
    for label, scene in computer_knight_pose_frames(bundle):
        yield rig.capture_fight(scene, f"computer_knight_pose_{label}")
    for label, scene in fight_audit_frames(build_computer_knight_audit(bundle)):
        yield rig.capture_fight(scene, f"computer_knight_seeded_{label}")
    audit = build_computer_knight_audit(bundle)
    audit.scene.fight.actor(1).hp = 20
    for frame in range(1500):
        audit.scene.advance(frame * 6)
        if frame in (20, 60, 120, 180):
            yield rig.capture_fight(audit.scene, f"computer_knight_idle_loss_frame{frame + 1}")
        if not audit.scene.fight.fight_active:
            yield rig.capture_fight(audit.scene, f"computer_knight_idle_loss_outcome{frame + 1}")
            return
    raise RuntimeError("idle computer knight audit did not reach an outcome")


def _sheet_shots(rig: AuditRig) -> Iterator[AuditShot]:
    """Shared sheets with explicit inventory fixtures and real assets (§12.26)."""
    for kid in KnightId:
        session = rig.new_session((kid,))
        session.scene = GameScene.OVERWORLD
        world = session.overworld
        actor = world.current_actor
        actor.progress, actor.gold, actor.daggers = 6, 73, 5
        world.inventories[actor.inventory].counts[:] = [2] * 10 + [15, 15]
        session._open_sheet(9)
        _step(session, 24)
        yield rig.capture(session, f"sheet9_{kid.name.lower()}_inventory_fixture")
    session = rig.new_session()
    world = session.overworld
    world.lairs[0].gold = 43
    world.lairs[0].loot.counts[:] = [2] * 10 + [15, 15]
    session._open_sheet(2, lair=0)
    _step(session, 24)
    yield rig.capture(session, "sheet2_loot_fixture")
    world.travel = True
    session._open_sheet(2, lair=0)
    _step(session, 24)
    yield rig.capture(session, "sheet2_read_only_fixture")
    world.travel = False
    session._open_sheet(8)
    _step(session, 24)
    yield rig.capture(session, "sheet8_acquisition_fixture")
    session._open_sheet(11)
    _step(session, 24)
    yield rig.capture(session, "sheet11_wyrm_fixture")
    session = rig.new_session((KnightId.BLUE,))
    legacy = session.knight_combat[KnightId.BLUE]
    legacy.hp = legacy.max_hp = 200
    legacy.strength, legacy.sword_tier, legacy.daggers = 3, 4, 5
    session.overworld.lairs[1].creatures_left = 1
    session.overworld.lairs[1].gold = 7
    session.overworld.lairs[1].loot.counts[0] = 1
    session._dispatch_lair(1)
    for _ in range(18000):
        session.step_tick(fight_host_melee_input(session))
        if session.scene != GameScene.COMBAT:
            break
    if session.scene != GameScene.STATUS or session.last_defeat_mask:
        raise RuntimeError("sheet audit did not win the naturally spawned Trogg lair")
    _step(session, 24)
    yield rig.capture(session, "sheet2_natural_lair_win_equipment_fixture")


def _shop_shots(rig: AuditRig) -> Iterator[AuditShot]:
    """Original shop panels and trades through the town caller (§12.29)."""
    for waterdeep in (False, True):
        name = "waterdeep" if waterdeep else "highwood"
        session = rig.new_session()
        actor = session.overworld.current_actor
        actor.gold, actor.hp = 149, 1
        session._open_town(_PRIMARY, waterdeep=waterdeep)
        _audit_town_button(session, 1)
        _step(session, 24)
        yield rig.capture(session, f"{name}_smith_offers_gold_fixture")
        _audit_shop_click(session, 92, 1, 1)
        yield rig.capture(session, f"{name}_smith_mail")
        _audit_shop_click(session, 88, 1, 2)
        yield rig.capture(session, f"{name}_smith_claymore")
    session = rig.new_session()
    session.overworld.current_actor.gold = 149
    session._open_town(_PRIMARY, waterdeep=False)
    _audit_town_button(session, 4)
    _step(session, 24)
    yield rig.capture(session, "market_natural_starting_stock_gold_fixture")
    _audit_shop_click(session, 8, 1)
    yield rig.capture(session, "market_after_talisman_buy")
    _audit_shop_click(session, 8, 0)
    yield rig.capture(session, "market_after_talisman_sale")


def _audit_service_region(session: GameSession, code: int, ticks: int) -> None:
    screen = session.service_screen
    if screen is None:
        raise ValueError("service audit requires its screen")
    region = next(r for r in screen.screens.donation.regions if r.code == code)
    screen.state.pointer_x, screen.state.pointer_y = region.x, region.y
    _step(session, ticks, PlayerInput(fire=True))
    _step(session, 1)


def _service_visit(
    rig: AuditRig, name: str, button: int, gold: int, donation_ticks: int
) -> Iterator[AuditShot]:
    session = rig.new_session()
    actor = session.actors[_PRIMARY]
    actor.gold, actor.hp, actor.hp_max = gold, 5, 30
    session._open_town(_PRIMARY, waterdeep=True)
    _step(session, 24)
    _audit_town_button(session, button)
    _step(session, 24)
    yield rig.capture(session, f"{name}_greeting")
    _tap(session, PlayerInput(fire=True))
    yield rig.capture(session, f"{name}_donation_gold_fixture")
    _audit_service_region(session, 5, donation_ticks)
    yield rig.capture(session, f"{name}_donation_after_plus")
    _audit_service_region(session, 3, 1)
    yield rig.capture(session, f"{name}_reply")
    _step(session, 60)
    _tap(session, PlayerInput(fire=True))
    _settle_fade(session)
    yield rig.capture(session, f"{name}_returned_to_town")


def _service_shots(rig: AuditRig) -> Iterator[AuditShot]:
    """Healer and mystic screens through the town caller (§12.31)."""
    yield from _service_visit(rig, "healer", 3, 40, 75)
    yield from _service_visit(rig, "healer_small", 3, 40, 12)
    yield from _service_visit(rig, "mystic", 4, 40, 30)


def _dice_round(rig: AuditRig, seed: int, bet: int) -> tuple[GameSession, list[AuditShot]]:
    """Enter the dice house, bet ``bet`` and play until the roll is shown (§12.32)."""
    session = rig.new_session()
    session.actors[_PRIMARY].gold = 40
    session._open_town(_PRIMARY, waterdeep=False)
    _step(session, 24)
    _audit_town_button(session, 2)
    _step(session, 24)
    screen = session.dice_screen
    if screen is None:
        raise ValueError("dice audit requires the dice house")
    session.overworld.prng.state = seed
    shots = [rig.capture(session, "dice_round_gold_fixture")]
    region = next(r for r in screen.run.house.regions if r.kind == 1 and r.code == bet)
    screen.run.state.pointer_x, screen.run.state.pointer_y = region.x, region.y
    for _ in range(40):
        if screen.run.state.throw:
            break
        _step(session, 1, PlayerInput(fire=True))
    _step(session, 9)
    shots.append(rig.capture(session, f"dice_throw_bet_{bet}"))
    for _ in range(400):
        if screen.run.state.dice:
            break
        _step(session, 1)
    return session, shots


def _dice_shots(rig: AuditRig) -> Iterator[AuditShot]:
    """The dice house through the town caller: a lost and a won roll, then leave (§12.32)."""
    wanted = {False: "lost", True: "won"}
    for seed in range(1, 4096):
        session, shots = _dice_round(rig, seed * 0x9E3779B1 & 0xFFFFFFFF, 2)
        screen = session.dice_screen
        if screen is None:
            raise ValueError("the dice round left the screen")
        won = screen.run.state.lines[0].text.startswith(screen.run.house.won[0].text)
        label = wanted.pop(won, None)
        if label is None:
            continue
        if label == "lost":
            yield from shots
        yield rig.capture(session, f"dice_roll_{label}")
        if label == "won":
            _step(session, 30)
            _tap(session, PlayerInput(fire=True))
            _step(session, 2)
            yield rig.capture(session, "dice_next_round")
            leave = next(r for r in screen.run.house.regions if r.kind == 2)
            screen.run.state.pointer_x, screen.run.state.pointer_y = leave.x, leave.y
            _step(session, 12, PlayerInput(fire=True))
            _settle_fade(session)
            yield rig.capture(session, "dice_left_to_town")
        if not wanted:
            return
    raise ValueError("no seed produced both dice outcomes")


def _audit_town_button(session: GameSession, number: int) -> None:
    _settle_fade(session)
    screen = session.town_screen
    if screen is None:
        raise ValueError("town audit requires its parent screen")
    region = screen.data.buttons[number - 1]
    screen.state.pointer_x, screen.state.pointer_y = region.x, region.y
    session.step_tick(_frame(PlayerInput(fire=True)))


def _audit_shop_click(session: GameSession, field: int, side: int, mask: int = 0) -> None:
    screen = session.sheet_screen
    if screen is None:
        raise ValueError("shop audit requires its shared sheet")
    region = next(
        r
        for r in screen.state.regions
        if r.field == field and r.side == side and r.mask == mask and not r.id
    )
    screen.state.pointer_x, screen.state.pointer_y = region.x, region.y
    session.step_tick(_frame(PlayerInput(fire=True)))


def _stone_until(session: GameSession, predicate: Callable[[], bool]) -> None:
    for _ in range(2000):
        if predicate():
            return
        _step(session, 1)
    raise ValueError("Stonehenge audit continuation did not finish")


def _stone_phase(session: GameSession, phase: StonePhase) -> bool:
    screen = session.stone_screen
    return screen is not None and screen.state.phase == phase and not session.palette_fx.is_fading


def _stone_offer(rig: AuditRig, kind: int) -> GameSession:
    session = rig.new_session()
    session.actors[_PRIMARY].kind = kind
    session.actors[_PRIMARY].lives = 3
    session.inventories[1].counts[0] = 1
    session._open_stonehenge(_PRIMARY)
    _stone_until(session, lambda: _stone_phase(session, StonePhase.OFFER))
    _step(session, 10)
    return session


def _stone_close_sheet(session: GameSession) -> None:
    sheet = session.sheet_screen
    if sheet is None:
        raise ValueError("Stonehenge audit requires its shared sheet")
    _settle_fade(session)
    done = next(r for r in sheet.state.regions if r.id == 7)
    sheet.state.pointer_x, sheet.state.pointer_y = done.x, done.y
    _tap(session, PlayerInput(fire=True))


def _stone_ritual_shots(rig: AuditRig, kind: int) -> Iterator[AuditShot]:
    session = _stone_offer(rig, kind)
    if kind == 0:
        yield rig.capture(session, "stone_offer")
    _tap(session, PlayerInput(fire=True))
    _stone_until(session, lambda: session.sheet_screen is not None)
    _settle_fade(session)
    if kind == 0:
        yield rig.capture(session, "stone_sheet3")
    sheet = session.sheet_screen
    if sheet is None:
        raise ValueError("Stonehenge audit requires offer sheet3")
    item = next(r for r in sheet.state.regions if r.type == 12 and r.field == 0)
    sheet.state.pointer_x, sheet.state.pointer_y = item.x, item.y
    _tap(session, PlayerInput(fire=True))
    _stone_until(session, lambda: _stone_phase(session, StonePhase.RITUAL_MESSAGE))
    if kind == 0:
        yield rig.capture(session, "stone_ritual_message")
    _stone_until(session, lambda: _stone_phase(session, StonePhase.RITUAL))
    yield rig.capture(session, f"stone_ritual_class{kind}")
    if kind == 0:
        yield from _stone_finish_shots(rig, session)


def _stone_finish_shots(rig: AuditRig, session: GameSession) -> Iterator[AuditShot]:
    for ticks in (120, 120, 120):
        _step(session, ticks)
        yield rig.capture(session, f"stone_natural_tick{session.tick_count}")
    _stone_until(
        session, lambda: session.sheet_screen is not None and session.sheet_screen.state.number == 9
    )
    _settle_fade(session)
    yield rig.capture(session, "stone_blessed_temple")
    _stone_close_sheet(session)
    _stone_until(session, lambda: session.scene == GameScene.OVERWORLD)
    _settle_fade(session)
    yield rig.capture(session, "stone_spent_exit")


def _stone_shots(rig: AuditRig) -> Iterator[AuditShot]:
    """Natural offer/ritual for all four class ramps, refusal and encoded ending (§12.34)."""
    for kind in range(4):
        yield from _stone_ritual_shots(rig, kind)
    session = _stone_offer(rig, 3)
    _tap(session, PlayerInput(fire=True))
    _stone_until(session, lambda: session.sheet_screen is not None)
    _stone_close_sheet(session)
    _stone_until(
        session, lambda: session.sheet_screen is not None and session.sheet_screen.state.number == 9
    )
    _settle_fade(session)
    yield rig.capture(session, "stone_declined_temple")
    session = rig.new_session()
    session.overworld.moon_frame = 46
    session.inventories[1].counts[11] = 4
    session._open_stonehenge(_PRIMARY)
    _stone_until(session, lambda: _stone_phase(session, StonePhase.QUEST))
    _step(session, 30)
    yield rig.capture(session, "stone_quest_complete")
    _tap(session, PlayerInput(fire=True))
    if session.ending_request is None:
        raise ValueError("Stonehenge audit did not emit an ending request")
    flags = session.ending_request.flags
    _step(session, 40)
    yield rig.capture(session, f"stone_program_flags_{flags:02X}")


def _next_day_shots(rig: AuditRig) -> Iterator[AuditShot]:
    """Natural Next Day screen (T12.1 palette), representative moon phases and farewell (T13.9)."""
    session = rig.new_session(tuple(KnightId))
    session.scene = GameScene.OVERWORLD
    for _ in range(4):
        _step(session, 20)
        session.step_tick(InputFrame(key=ord("E")))
        session.step_tick(InputFrame(key=ord("E")))
    _step(session, 40)
    yield rig.capture(session, "next_day_round1")
    rules = session.overworld.turn_rules
    if rules is None or not rules.moons:
        raise ValueError("next_day audit requires original turn rules")
    mid_frame = rules.moons[len(rules.moons) // 2]
    session.overworld.moon_frame = mid_frame
    yield rig.capture(session, f"next_day_moon_f{mid_frame}")
    last_frame = rules.moons[-1]
    session.overworld.moon_frame = last_frame
    yield rig.capture(session, f"next_day_moon_f{last_frame}")
    farewell = rig.new_session()
    farewell.scene = GameScene.OVERWORLD
    farewell.step_tick(InputFrame(key=ord("Q")))
    farewell.step_tick(InputFrame(key=ord("Q")))
    _step(farewell, 30)
    yield rig.capture(farewell, "farewell")


def _presentation_shots(rig: AuditRig) -> Iterator[AuditShot]:
    """Four representative scenes for 1:1 presentation review (T13.9, ADR-053)."""
    s_map = rig.new_session()
    _step(s_map, 5)
    _tap(s_map, PlayerInput(skip_intro=True))
    _step(s_map, 40)
    for _ in range(3):
        _tap(s_map, PlayerInput(dy=1))
    _tap(s_map, PlayerInput(confirm=True))
    _step(s_map, 30)
    _tap(s_map, PlayerInput(confirm=True))
    _step(s_map, 60)
    yield rig.capture(s_map, "map_start")

    s_town = rig.new_session()
    s_town.actors[_PRIMARY].gold = 40
    s_town._open_town(_PRIMARY, waterdeep=False)
    _settle_fade(s_town)
    yield rig.capture(s_town, "highwood_town")

    s_fight = _host_fight_session(rig, TROGG_ARENAS[0])
    while s_fight.tick_count < 120 and s_fight.active_encounter is not None:
        s_fight.step_tick(fight_host_input(s_fight))
    yield rig.capture(s_fight, "trogg_fight")

    s_day = rig.new_session(tuple(KnightId))
    s_day.scene = GameScene.OVERWORLD
    for _ in range(4):
        _step(s_day, 20)
        s_day.step_tick(InputFrame(key=ord("E")))
        s_day.step_tick(InputFrame(key=ord("E")))
    _step(s_day, 40)
    yield rig.capture(s_day, "next_day")


AUDIT_SCENARIOS: dict[str, Callable[[AuditRig], Iterator[AuditShot]]] = {
    "ending": ending_shots,
    "meetings": meeting_shots,
    "valley": valley_shots,
    "villages": village_shots,
    "wizard": wizard_shots,
    "stonehenge": _stone_shots,
    "shops": _shop_shots,
    "services": _service_shots,
    "dice": _dice_shots,
    "sheets": _sheet_shots,
    "computer_knight": _computer_knight_shots,
    "dragon": _dragon_shots,
    "guardian": _guardian_shots,
    "balok": _drake_shots,
    "troll": _stalker_shots,
    "flyer": _flyer_shots,
    "mudmen": _snatcher_shots,
    "intro": _intro_shots,
    "menu_map": _menu_and_map_shots,
    "pixel_map": _pixel_map_shots,
    "turns_clock_dragon": _turn_and_dragon_shots,
    "next_day": _next_day_shots,
    "presentation": _presentation_shots,
    "places": _place_shots,
    "fights": _fight_shots,
    "regions": _region_shots,
    "engine_fights": _engine_fight_shots,
    "ratmen": _caster_shots,
}


def build_contact_sheet(
    shots: list[AuditShot],
    columns: int = _SHEET_COLUMNS,
    *,
    max_cell_width: int | None = None,
) -> Image.Image:
    """Tile ``shots`` into one labelled RGB image, optionally capping each cell's width."""
    if not shots:
        raise ValueError("no audit shots to tile")
    height, width = shots[0].rgb.shape[:2]
    if max_cell_width is not None and width > max_cell_width:
        cell_w = max_cell_width
        cell_h = max(1, round(height * max_cell_width / width))
    else:
        cell_w = width
        cell_h = height
    row_height = cell_h + _LABEL_HEIGHT
    rows = (len(shots) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * cell_w, rows * row_height), (40, 40, 40))
    draw = ImageDraw.Draw(sheet)
    for index, shot in enumerate(shots):
        x = (index % columns) * cell_w
        y = (index // columns) * row_height
        draw.text((x + 2, y), shot.name, fill=(255, 255, 0))
        img = Image.fromarray(shot.rgb)
        if (cell_w, cell_h) != (width, height):
            img = img.resize((cell_w, cell_h), Image.Resampling.LANCZOS)
        sheet.paste(img, (x, y + _LABEL_HEIGHT))
    return sheet


def _sheet_layout(
    shot_count: int,
    present_size: tuple[int, int] | None,
    *,
    hd_compare: bool = False,
) -> tuple[int, int | None]:
    if hd_compare:
        if present_size is not None:
            return (1, 1280)
        return (min(2, max(1, shot_count)), 1280)
    if present_size is None:
        return (_SHEET_COLUMNS, None)
    if shot_count <= 4:
        return (min(2, max(1, shot_count)), None)
    cols = 2 if shot_count <= 10 else _SHEET_COLUMNS
    max_w = 960 if present_size[0] > 960 else None
    return (cols, max_w)


def _sheet_filename(
    name: str,
    display: DisplaySettings | None,
    resolved_size: tuple[int, int] | None,
    *,
    hd_compare: bool,
) -> str:
    if display is not None and resolved_size is not None:
        prefix = f"{name}_hd" if hd_compare else name
        return f"{prefix}_{display.filter.value}_{resolved_size[0]}x{resolved_size[1]}.png"
    return f"{name}_hd_compare.png" if hd_compare else f"{name}.png"


def run_audit(
    adf_dir: Path,
    out_dir: Path,
    scenarios: Sequence[str] | None = None,
    *,
    display: DisplaySettings | None = None,
    present_size: tuple[int, int] | None = None,
    hd_pack: HdPack | None = None,
    show_hitboxes: bool = False,
) -> dict[str, Path]:
    """Capture selected scenarios, writing one contact sheet per scenario.

    When ``display`` is provided, each shot is rendered through the real presentation path into an
    offscreen framebuffer of ``present_size`` (default ``1920x1080``) and written as
    ``<scenario>_<preset>_<W>x<H>.png``. When ``hd_pack`` is provided, every shot is composed
    side-by-side (Classic left, HD right) for visual artist QA (§14.7).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    resolved_size = (
        None
        if display is None
        else (DEFAULT_PRESENT_SIZE if present_size is None else present_size)
    )
    hd_compare = hd_pack is not None
    rig = AuditRig(
        adf_dir,
        display=display,
        present_size=resolved_size,
        hd_pack=hd_pack,
        show_hitboxes=show_hitboxes,
    )
    written: dict[str, Path] = {}
    try:
        if scenarios is not None:
            names = tuple(scenarios)
        elif display is not None or hd_compare:
            names = PRESENTATION_SCENARIOS
        else:
            names = tuple(AUDIT_SCENARIOS)
        for name in names:
            shots = list(AUDIT_SCENARIOS[name](rig))
            cols, max_w = _sheet_layout(len(shots), resolved_size, hd_compare=hd_compare)
            filename = _sheet_filename(name, display, resolved_size, hd_compare=hd_compare)
            path = out_dir / filename
            build_contact_sheet(shots, columns=cols, max_cell_width=max_w).save(path)
            written[name] = path
    finally:
        rig.release()
    return written
