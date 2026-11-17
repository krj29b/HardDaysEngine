"""Real-disk audit harness: render every scene to labelled contact sheets (T11.1).

The harness drives a :class:`GameSession` on the user's own disks, renders each scene through
the GL canvas and writes PNG contact sheets for visual review (ADR-009, point 4). Its output is
derived from game data and must stay under the git-ignored ``build/`` directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import moderngl
import numpy as np
from PIL import Image, ImageDraw

from pymoonstone.app.fight_audit import (
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
    flyer_pose_frames,
    guardian_pose_frames,
    snatcher_pose_frames,
    stalker_pose_frames,
)
from pymoonstone.app.fight_runtime import FightLoader
from pymoonstone.app.game import GameScene, GameSession
from pymoonstone.app.input import InputFrame, PlayerInput
from pymoonstone.app.location_screens import LocationKind
from pymoonstone.app.map_scene import compose_map
from pymoonstone.assets.adf import DiskSet
from pymoonstone.assets.palette import default_combat_palette
from pymoonstone.render.renderer import MoonstoneRenderer
from pymoonstone.render.scene import SceneRenderer, default_bitmap_font
from pymoonstone.sim.overworld import EncounterTrigger, MapSprite, TriggerKind, open_menu, scan
from pymoonstone.sim.types import KnightId

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence
    from pathlib import Path

    import numpy.typing as npt

    from pymoonstone.app.fight_scene import FightScene
    from pymoonstone.app.scene_assets import GameAssetBundle

_PRIMARY = KnightId.RED
_LABEL_HEIGHT = 12
_SHEET_COLUMNS = 4
_INTRO_SAMPLE_TICKS: tuple[int, ...] = (50, 400, 900, 1500, 2500)
_TOWN_BUTTON_COUNTS: tuple[tuple[LocationKind, int], ...] = (
    (LocationKind.HIGHWOOD, 4),
    (LocationKind.WATERDEEP, 5),
)
_REGION_NAMES: tuple[str, ...] = ("forest", "glen", "swamp", "wasteland")
_NO_NODE = -1
_END_SCENES: tuple[GameScene, ...] = (
    GameScene.STATUS,
    GameScene.NEW_DAY,
    GameScene.VICTORY,
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


class AuditRig:
    """Standalone GL canvas plus the session factory used by all audit scenarios."""

    def __init__(self, adf_dir: Path) -> None:
        self._adf_dir = adf_dir
        self._ctx = moderngl.create_standalone_context(require=330)
        self._base = MoonstoneRenderer(self._ctx)
        self._scene = SceneRenderer(self._base)
        self._scene.set_palette(default_combat_palette())
        self._font = default_bitmap_font()

    def new_session(self, players: tuple[KnightId, ...] = (_PRIMARY,)) -> GameSession:
        """Return a fresh single-player session on the real disks."""
        return GameSession(disk_set=DiskSet.from_directory(self._adf_dir), players=players)

    def capture(self, session: GameSession, name: str) -> AuditShot:
        """Render the session's current scene and return its canvas as RGB."""
        session.render_to_scene(self._scene, self._font)
        rgba = self._base.read_canvas_rgba()
        return AuditShot(name=name, rgb=np.ascontiguousarray(rgba[..., :3]))

    def capture_fight(self, scene: FightScene, name: str) -> AuditShot:
        """Capture the persistent engine's last output without stepping it (§10.17)."""
        scene.render(self._scene)
        self._scene.flush_to_fbo()
        rgba = self._base.read_canvas_rgba()
        return AuditShot(name=name, rgb=np.ascontiguousarray(rgba[..., :3]))

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
        rgba = self._base.read_canvas_rgba()
        return AuditShot(
            name="mi_frames_" + "_".join(map(str, frames)), rgb=np.ascontiguousarray(rgba[..., :3])
        )

    def fight_assets(self) -> GameAssetBundle:
        """Return the real-disk bundle used for fight-engine audit fixtures."""
        from pymoonstone.app.scene_assets import GameAssetBundle

        return GameAssetBundle(DiskSet.from_directory(self._adf_dir))

    def release(self) -> None:
        """Free the GL resources."""
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
        yield rig.capture(session, f"map_move{move}_{session.scene.name.lower()}")
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


def _place_shots(rig: AuditRig) -> Iterator[AuditShot]:
    for kind in LocationKind:
        session = rig.new_session()
        session._open_location(_PRIMARY, kind)
        _step(session, 40)
        yield rig.capture(session, f"place_{kind.name.lower()}")
    for town, button_count in _TOWN_BUTTON_COUNTS:
        for button in range(button_count):
            session = rig.new_session()
            session._open_location(_PRIMARY, town)
            _step(session, 20)
            for _ in range(button):
                _tap(session, PlayerInput(dy=1))
            _tap(session, PlayerInput(confirm=True))
            _step(session, 20)
            yield rig.capture(session, f"{town.name.lower()}_button{button}")


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


AUDIT_SCENARIOS: dict[str, Callable[[AuditRig], Iterator[AuditShot]]] = {
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
    "places": _place_shots,
    "fights": _fight_shots,
    "regions": _region_shots,
    "engine_fights": _engine_fight_shots,
    "ratmen": _caster_shots,
}


def build_contact_sheet(shots: list[AuditShot], columns: int = _SHEET_COLUMNS) -> Image.Image:
    """Tile ``shots`` into one labelled RGB image."""
    if not shots:
        raise ValueError("no audit shots to tile")
    height, width = shots[0].rgb.shape[:2]
    cell_height = height + _LABEL_HEIGHT
    rows = (len(shots) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * width, rows * cell_height), (40, 40, 40))
    draw = ImageDraw.Draw(sheet)
    for index, shot in enumerate(shots):
        x = (index % columns) * width
        y = (index // columns) * cell_height
        draw.text((x + 2, y), shot.name, fill=(255, 255, 0))
        sheet.paste(Image.fromarray(shot.rgb), (x, y + _LABEL_HEIGHT))
    return sheet


def run_audit(
    adf_dir: Path, out_dir: Path, scenarios: Sequence[str] | None = None
) -> dict[str, Path]:
    """Capture selected scenarios (all by default), writing one contact sheet per scenario."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rig = AuditRig(adf_dir)
    written: dict[str, Path] = {}
    try:
        names = tuple(AUDIT_SCENARIOS) if scenarios is None else tuple(scenarios)
        for name in names:
            scenario = AUDIT_SCENARIOS[name]
            path = out_dir / f"{name}.png"
            build_contact_sheet(list(scenario(rig))).save(path)
            written[name] = path
    finally:
        rig.release()
    return written
