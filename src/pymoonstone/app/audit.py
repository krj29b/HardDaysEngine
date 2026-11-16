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

from pymoonstone.app.fight_audit import TROGG_ARENAS, build_fight_audit, fight_audit_frames
from pymoonstone.app.game import GameScene, GameSession
from pymoonstone.app.input import InputFrame, PlayerInput
from pymoonstone.app.location_screens import LocationKind
from pymoonstone.assets.adf import DiskSet
from pymoonstone.assets.palette import default_combat_palette
from pymoonstone.render.renderer import MoonstoneRenderer
from pymoonstone.render.scene import SceneRenderer, default_bitmap_font
from pymoonstone.sim.overworld import EncounterTrigger, TriggerKind
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
_CREATURE_TYPE_COUNT = 4
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

    def new_session(self) -> GameSession:
        """Return a fresh single-player session on the real disks."""
        return GameSession(disk_set=DiskSet.from_directory(self._adf_dir), players=(_PRIMARY,))

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


def _fight_shots(rig: AuditRig) -> Iterator[AuditShot]:
    for creature in range(_CREATURE_TYPE_COUNT):
        session = rig.new_session()
        session._start_combat(
            EncounterTrigger(
                kind=TriggerKind.WANDERING_MONSTER,
                knight_id=_PRIMARY,
                node_id=0,
                day=1,
                moon_phase=session.overworld.moon_phase,
                creature_type=creature,
                encounter_kind=creature,
            ),
            _PRIMARY,
        )
        _step(session, 60)
        yield rig.capture(session, f"fight{creature}_idle")
        _step(session, 30, PlayerInput(dx=1))
        yield rig.capture(session, f"fight{creature}_walk")
        _step(session, 4, PlayerInput(dx=1, fire=True))
        yield rig.capture(session, f"fight{creature}_attack")
        _step(session, 8, PlayerInput(dx=1, fire=True))
        yield rig.capture(session, f"fight{creature}_attack_late")


def _region_shots(rig: AuditRig) -> Iterator[AuditShot]:
    """One lair arena per region, to review the fight palette (ARCHITECTURE.md §10.13)."""
    for lair_index, region in enumerate(_REGION_NAMES):
        session = rig.new_session()
        session._start_combat(
            EncounterTrigger(
                kind=TriggerKind.LAIR_BOSS,
                knight_id=_PRIMARY,
                node_id=_NO_NODE,
                day=1,
                moon_phase=session.overworld.moon_phase,
                lair_index=lair_index,
            ),
            _PRIMARY,
        )
        _step(session, 60)
        yield rig.capture(session, f"region_{region}")


def _engine_fight_shots(rig: AuditRig) -> Iterator[AuditShot]:
    bundle = rig.fight_assets()
    for name in TROGG_ARENAS:
        audit = build_fight_audit(bundle, name)
        for label, scene in fight_audit_frames(audit):
            yield rig.capture_fight(scene, f"engine_{name}_{label}")


AUDIT_SCENARIOS: dict[str, Callable[[AuditRig], Iterator[AuditShot]]] = {
    "intro": _intro_shots,
    "menu_map": _menu_and_map_shots,
    "places": _place_shots,
    "fights": _fight_shots,
    "regions": _region_shots,
    "engine_fights": _engine_fight_shots,
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
