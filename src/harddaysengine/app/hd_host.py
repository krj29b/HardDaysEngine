"""Host-side HD pack lifecycle, fight stamp history, and scene presentation (T19.5, §14.6-7)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from harddaysengine.app.hd_scenes import (
    render_calendar_hd,
    render_meeting_hd,
    render_menu_hd,
    render_overworld_hd,
    render_service_hd,
    render_sheet_hd,
    render_town_hd,
    render_valley_hd,
)
from harddaysengine.app.map_scene import draw_map_text
from harddaysengine.assets.fight_palette import (
    REGION_FOREST,
    REGION_GLEN,
    REGION_SWAMP,
    REGION_WASTELAND,
)
from harddaysengine.render.hd_compositor import HdCompositor
from harddaysengine.render.renderer import CANVAS_HEIGHT, CANVAS_WIDTH, PLAYFIELD_Y_OFFSET
from harddaysengine.render.scene import blit_indexed_sprite, palette_fx_to_rgb
from harddaysengine.sim.dice_house import DicePhase
from harddaysengine.sim.m68k import s16, u32
from harddaysengine.sim.service_screen import number3
from harddaysengine.sim.stonehenge import StonePhase
from harddaysengine.sim.wizard import WizardPhase

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    import moderngl
    import numpy.typing as npt

    from harddaysengine.app.dice_screen import DiceScreen
    from harddaysengine.app.game import GameSession
    from harddaysengine.app.stonehenge_screen import StoneScreen
    from harddaysengine.app.wizard_screen import WizardScreen
    from harddaysengine.assets.cel import DecodedCel
    from harddaysengine.assets.hd_pack import HdPack
    from harddaysengine.assets.hd_pack_schema import HdAssetKind
    from harddaysengine.assets.palette import RGBColor
    from harddaysengine.sim.fight_script import Draw

_KNIGHT_CLASS_VARIANTS: tuple[str, ...] = ("blue", "yellow", "green", "red")
_REGION_VARIANTS: dict[int, str] = {
    REGION_GLEN: "glen",
    REGION_FOREST: "forest",
    REGION_SWAMP: "swamp",
    REGION_WASTELAND: "wasteland",
}
_KNIGHT_CEL_PREFIXES = ("kn", "he")


def knight_class_variant(kind: int) -> str:
    """Return the canonical §14.2 HD variant name for knight class ``kind``."""
    if 0 <= kind < len(_KNIGHT_CLASS_VARIANTS):
        return _KNIGHT_CLASS_VARIANTS[kind]
    return "black"


def region_variant(region_code: int) -> str | None:
    """Return the canonical §14.2 HD variant name for fight palette ``region_code``."""
    return _REGION_VARIANTS.get(region_code)


def make_fight_cel_variant_fn(
    first_kind: int,
    *,
    is_duel: bool,
) -> Callable[[Draw], str | None] | None:
    """Resolve knight cel variants for single-knight fights; duels rely on live palette/masks."""
    if is_duel:
        return None
    variant = knight_class_variant(first_kind)

    def _resolve(draw: Draw) -> str | None:
        lower = draw.cel.strip().casefold()
        if lower.endswith(".ob") and lower.startswith(_KNIGHT_CEL_PREFIXES):
            return variant
        return None

    return _resolve


class FightHdHistory:
    """GL-free, pure-data record of a fight's background, stamps, and latest draws (ADR-014).

    Tracks ``prior_stamps`` separately from ``last_draws`` so rebuilding the GPU fight FBOs
    (on mid-fight ``F7`` enable or VBL palette fade/pulse) reproduces exact ``FightCanvas.accept``
    semantics with zero double-blending or double-stamping.
    """

    def __init__(
        self,
        *,
        arena_name: str,
        arena_kind: HdAssetKind,
        arena_variant: str | None,
        background: npt.NDArray[np.uint8],
        cels: Mapping[str, DecodedCel],
        base_palette: Sequence[RGBColor],
        fade_reference: Sequence[RGBColor] | None = None,
        cel_variant_fn: Callable[[Draw], str | None] | None = None,
    ) -> None:
        self.arena_name = arena_name
        self.arena_kind = arena_kind
        self.arena_variant = arena_variant
        self.background = background.copy()
        self.cels = dict(cels)
        self.base_palette = tuple(base_palette)
        self.fade_reference = self.base_palette if fade_reference is None else tuple(fade_reference)
        self.cel_variant_fn = cel_variant_fn
        self.prior_stamps: list[Draw] = []
        self.last_draws: tuple[Draw, ...] = ()
        self.frame_seq = 0

    def record(self, draws: Sequence[Draw]) -> None:
        """Record one simulation frame's emitted draw list once per fight advance."""
        for prev in self.last_draws:
            if prev.stamp:
                self.prior_stamps.append(prev)
        self.last_draws = tuple(draws)
        self.frame_seq += 1


def _effective_session_palette(
    session: GameSession,
    scene_palette: Sequence[RGBColor],
) -> tuple[RGBColor, ...]:
    if (
        session.palette_fx.is_fading
        or bool(session._sound_return)
        or session.palette_fx.is_flashing
        or session.wizard_screen is not None
        or session.village_visit is not None
        or session._caller_palette_active()
    ):
        return palette_fx_to_rgb(session.palette_fx.current_palette())
    return tuple(scene_palette)


class HdHostPresenter:
    """Manage lazy ``HdCompositor`` lifecycle and render HD gameplay scenes (§14.6-7)."""

    def __init__(self, pack: HdPack | None = None) -> None:
        self._pack = pack
        self._compositor: HdCompositor | None = None
        self._synced_history_id: int | None = None
        self._synced_seq: int = -1
        self._synced_palette: tuple[RGBColor, ...] | None = None

    @property
    def pack(self) -> HdPack | None:
        """Loaded HD pack, or ``None`` when none is configured."""
        return self._pack

    @property
    def compositor(self) -> HdCompositor | None:
        """Currently active GPU compositor, or ``None`` when Classic mode is active."""
        return self._compositor

    @property
    def scale(self) -> int:
        """Active HD FBO scale factor (or ``1`` when no pack is loaded)."""
        return self._pack.config.scale if self._pack is not None else 1

    def ensure_active(self, ctx: moderngl.Context, *, enabled: bool) -> HdCompositor | None:
        """Lazily create the compositor when enabled; release GPU resources when toggled off."""
        if not enabled or self._pack is None:
            self.release()
            return None
        if self._compositor is None:
            self._compositor = HdCompositor(ctx, self._pack)
            self._reset_sync()
        return self._compositor

    def _reset_sync(self) -> None:
        self._synced_history_id = None
        self._synced_seq = -1
        self._synced_palette = None

    def render_fight_history(
        self,
        compositor: HdCompositor,
        history: FightHdHistory,
        *,
        live_palette: Sequence[RGBColor],
        background_y_offset: int = 0,
    ) -> moderngl.Texture:
        """Synchronize ``history`` into ``compositor`` once per frame and return HD canvas tex."""
        live = tuple(live_palette)
        hist_id = id(history)
        same_history = self._synced_history_id == hist_id
        same_palette = self._synced_palette == live

        if same_history and same_palette and self._synced_seq == history.frame_seq:
            return compositor.render_fight(background_y_offset=background_y_offset)

        if same_history and same_palette and history.frame_seq == self._synced_seq + 1:
            compositor.accept_fight_draws(
                history.last_draws,
                history.cels,
                variant_for_cel=history.cel_variant_fn,
            )
            self._synced_seq = history.frame_seq
            return compositor.render_fight(background_y_offset=background_y_offset)

        compositor.begin_fight(
            history.arena_name,
            history.background,
            kind=history.arena_kind,
            variant=history.arena_variant,
            palette=live,
            base_palette=history.base_palette,
            fade_reference=history.fade_reference,
        )
        if history.prior_stamps:
            compositor.accept_fight_draws(
                history.prior_stamps,
                history.cels,
                variant_for_cel=history.cel_variant_fn,
            )
        if history.last_draws:
            compositor.accept_fight_draws(
                history.last_draws,
                history.cels,
                variant_for_cel=history.cel_variant_fn,
            )
        self._synced_history_id = hist_id
        self._synced_seq = history.frame_seq
        self._synced_palette = live
        return compositor.render_fight(background_y_offset=background_y_offset)

    def _render_dice_hd(
        self,
        compositor: HdCompositor,
        screen: DiceScreen,
        gold: int,
        live_palette: tuple[RGBColor, ...],
    ) -> moderngl.Texture:
        state = screen.run.state
        base_palette = screen.palette()
        if state.phase in (DicePhase.ROLL_WAIT, DicePhase.ROLL_FIRE):
            compositor.set_palettes(live_palette, base_palette)
            compositor.begin_scene()
            compositor.draw_picture(screen.run.house.table, screen.table.pixels)
            for face, (x, y) in zip(state.dice, screen.run.house.dice_at, strict=True):
                frame = screen.cel.frames[face]
                compositor.draw_cel_frame(
                    screen.run.house.cel_file,
                    frame,
                    x - frame.meta.hot_x,
                    y + PLAYFIELD_Y_OFFSET,
                )
            overlay = np.zeros((CANVAS_HEIGHT, CANVAS_WIDTH), dtype=np.uint8)
            for line in state.lines:
                draw_map_text(
                    overlay,
                    screen.font,
                    screen.char_map,
                    line.text,
                    line.x,
                    line.y,
                    style=line.style,
                    span=screen.span,
                )
            compositor.draw_indexed_overlay(overlay, cache_key="dice_roll_text")
            return compositor.output_texture

        if screen.hd_history is not None:
            self.render_fight_history(compositor, screen.hd_history, live_palette=live_palette)
        else:
            compositor.set_palettes(live_palette, base_palette)
            compositor.begin_scene()
            compositor.draw_picture(screen.run.house.tavern, screen.tavern.pixels)

        overlay = np.zeros((CANVAS_HEIGHT, CANVAS_WIDTH), dtype=np.uint8)
        gx, gy, gstyle = screen.run.house.gold_at
        draw_map_text(
            overlay,
            screen.font,
            screen.char_map,
            number3(u32(s16(gold))),
            gx,
            gy,
            style=gstyle,
            span=screen.span,
        )
        blit_indexed_sprite(
            overlay, screen.pointer, state.pointer_x, state.pointer_y + PLAYFIELD_Y_OFFSET
        )
        compositor.draw_indexed_overlay(overlay, cache_key="dice_round_hud")
        return compositor.output_texture

    def _render_stone_hd(
        self,
        compositor: HdCompositor,
        screen: StoneScreen,
        live_palette: tuple[RGBColor, ...],
    ) -> moderngl.Texture | None:
        if screen.state.phase in (StonePhase.RITUAL, StonePhase.RITUAL_OUT):
            if screen.hd_history is None:
                return None
            return self.render_fight_history(
                compositor, screen.hd_history, live_palette=live_palette
            )
        if screen.message is None or screen.font is None:
            return None
        compositor.set_palettes(live_palette, screen.message_palette())
        compositor.begin_scene()
        compositor.draw_picture(screen.rules.message, screen.message.pixels)
        overlay = np.zeros((CANVAS_HEIGHT, CANVAS_WIDTH), dtype=np.uint8)
        lines = {
            StonePhase.OFFER: screen.rules.offer,
            StonePhase.OFFER_OUT: screen.rules.offer,
            StonePhase.RITUAL_MESSAGE: screen.rules.ritual_text,
            StonePhase.RITUAL_MESSAGE_OUT: screen.rules.ritual_text,
        }.get(screen.state.phase, screen.rules.quest)
        for line in lines:
            draw_map_text(
                overlay,
                screen.font,
                screen.char_map,
                line.text,
                line.x,
                line.y,
                style=line.style,
                span=screen.span,
            )
        compositor.draw_indexed_overlay(overlay, cache_key="stone_msg_text")
        return compositor.output_texture

    def _render_wizard_hd(
        self,
        compositor: HdCompositor,
        screen: WizardScreen,
        live_palette: tuple[RGBColor, ...],
    ) -> moderngl.Texture | None:
        if screen.state.phase in (WizardPhase.LOADING, WizardPhase.LOADING_OUT):
            compositor.set_palettes(live_palette, screen.palette())
            compositor.begin_scene()
            compositor.draw_picture(screen.rules.loading.picture, screen.message.pixels)
            overlay = np.zeros((CANVAS_HEIGHT, CANVAS_WIDTH), dtype=np.uint8)
            for line in screen.rules.loading.messages[screen.state.loading_index]:
                draw_map_text(
                    overlay,
                    screen.loading_font,
                    screen.mapping,
                    line.text,
                    line.x,
                    line.y,
                    style=line.style | 8,
                    span=screen.span,
                )
            compositor.draw_indexed_overlay(overlay, cache_key="wizard_loading_text")
            return compositor.output_texture

        if screen.hd_history is None:
            return None
        self.render_fight_history(compositor, screen.hd_history, live_palette=live_palette)
        stage = screen.hd_stage
        if stage is not None:
            overlay = np.zeros((CANVAS_HEIGHT, CANVAS_WIDTH), dtype=np.uint8)
            x, y, style = screen.rules.speech_at[stage]
            for line in screen.speech(stage):
                draw_map_text(
                    overlay,
                    screen.font,
                    screen.mapping,
                    line,
                    x,
                    y,
                    style=style,
                    span=screen.span,
                )
                y += screen.dialogue.line_step
            compositor.draw_indexed_overlay(overlay, cache_key=f"wizard_speech_{stage}")
        return compositor.output_texture

    def _render_combat_hd(
        self,
        session: GameSession,
        compositor: HdCompositor,
    ) -> moderngl.Texture | None:
        if session.active_encounter is None:
            return None
        scene = session.active_encounter.runtime.scene
        live = _effective_session_palette(session, scene.palette)
        return self.render_fight_history(
            compositor,
            scene.hd_history,
            live_palette=live,
            background_y_offset=scene.fight.shake.offset,
        )

    def _render_stone_scene_hd(
        self,
        session: GameSession,
        compositor: HdCompositor,
    ) -> moderngl.Texture | None:
        screen = session.stone_screen
        if screen is None:
            return None
        if screen.state.phase == StonePhase.MAP_OUT:
            return render_overworld_hd(session, compositor, _effective_session_palette)
        base = (
            screen.ritual_palette()
            if screen.state.phase in (StonePhase.RITUAL, StonePhase.RITUAL_OUT)
            else screen.message_palette()
        )
        live = _effective_session_palette(session, base)
        return self._render_stone_hd(compositor, screen, live)

    def _render_wizard_scene_hd(
        self,
        session: GameSession,
        compositor: HdCompositor,
    ) -> moderngl.Texture | None:
        screen = session.wizard_screen
        if screen is None:
            return None
        if screen.state.phase == WizardPhase.MAP_OUT:
            return render_overworld_hd(session, compositor, _effective_session_palette)
        live = _effective_session_palette(session, screen.palette())
        return self._render_wizard_hd(compositor, screen, live)

    def _render_new_day_hd(
        self,
        session: GameSession,
        compositor: HdCompositor,
    ) -> moderngl.Texture | None:
        if session._calendar_phase == 0:
            return render_overworld_hd(session, compositor, _effective_session_palette)
        return render_calendar_hd(session, compositor, _effective_session_palette, farewell=False)

    def _render_location_hd(
        self,
        session: GameSession,
        compositor: HdCompositor,
    ) -> moderngl.Texture | None:
        if session.town_screen is None:
            return None
        return render_town_hd(session, compositor, session.town_screen, _effective_session_palette)

    def _render_dice_scene_hd(
        self,
        session: GameSession,
        compositor: HdCompositor,
    ) -> moderngl.Texture | None:
        if session.dice_screen is None:
            return None
        live = _effective_session_palette(session, session.dice_screen.palette())
        gold = session.actors[session.dice_screen.run.state.knight - 1].gold
        return self._render_dice_hd(compositor, session.dice_screen, gold, live)

    def render_session_hd(
        self,
        session: GameSession,
        compositor: HdCompositor,
    ) -> moderngl.Texture | None:
        """Render all gameplay scenes in HD; intro/ending fall back to Classic (§14.7)."""
        from harddaysengine.app.game import GameScene

        handlers: dict[GameScene, Callable[[], moderngl.Texture | None]] = {
            GameScene.KNIGHT_SELECT: lambda: render_menu_hd(
                session, compositor, _effective_session_palette
            ),
            GameScene.OVERWORLD: lambda: render_overworld_hd(
                session, compositor, _effective_session_palette
            ),
            GameScene.VILLAGE: lambda: render_overworld_hd(
                session, compositor, _effective_session_palette
            ),
            GameScene.COMBAT: lambda: self._render_combat_hd(session, compositor),
            GameScene.LOCATION: lambda: self._render_location_hd(session, compositor),
            GameScene.STATUS: lambda: (
                None
                if session.sheet_screen is None
                else render_sheet_hd(
                    session, compositor, session.sheet_screen, _effective_session_palette
                )
            ),
            GameScene.NEW_DAY: lambda: self._render_new_day_hd(session, compositor),
            GameScene.SERVICE: lambda: (
                None
                if session.service_screen is None
                else render_service_hd(
                    session, compositor, session.service_screen, _effective_session_palette
                )
            ),
            GameScene.DICE: lambda: self._render_dice_scene_hd(session, compositor),
            GameScene.STONEHENGE: lambda: self._render_stone_scene_hd(session, compositor),
            GameScene.WIZARD: lambda: self._render_wizard_scene_hd(session, compositor),
            GameScene.VALLEY: lambda: (
                None
                if session.valley_screen is None
                else render_valley_hd(
                    session,
                    compositor,
                    session.valley_screen,
                    _effective_session_palette,
                    lambda: self._render_combat_hd(session, compositor),
                )
            ),
            GameScene.MEETING: lambda: (
                None
                if session.meeting_screen is None
                else render_meeting_hd(
                    session,
                    compositor,
                    session.meeting_screen,
                    _effective_session_palette,
                    lambda: self._render_combat_hd(session, compositor),
                )
            ),
            GameScene.GAME_OVER: lambda: render_calendar_hd(
                session, compositor, _effective_session_palette, farewell=True
            ),
        }
        handler = handlers.get(session.scene)
        return None if handler is None else handler()

    def release(self) -> None:
        """Release the active ``HdCompositor`` and reset all sync state."""
        if self._compositor is not None:
            self._compositor.release()
            self._compositor = None
        self._reset_sync()
