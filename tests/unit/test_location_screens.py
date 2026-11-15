"""Unit tests for town/location menus, status sheet, and New Day banner (T9.3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from pymoonstone.app.input import PlayerInput
from pymoonstone.app.location_screens import (
    LocationKind,
    LocationMenuState,
    location_backdrop_names,
    menu_options_for_location,
    render_location_menu,
    render_new_day_screen,
    render_status_screen,
    step_location_menu,
    visit_shop,
)
from pymoonstone.assets.palette import RGBColor, default_combat_palette
from pymoonstone.assets.picture import DecodedPicture
from pymoonstone.render.renderer import CANVAS_HEIGHT, CANVAS_WIDTH, PLAYFIELD_HEIGHT
from pymoonstone.render.scene import BitmapFont, default_bitmap_font
from pymoonstone.sim.knight_combat import KnightState
from pymoonstone.sim.locations import MerchantItemKind
from pymoonstone.sim.overworld import (
    ALL_MOONSTONES_MASK,
    MoonPhase,
    OverworldState,
    create_campaign,
)
from pymoonstone.sim.types import EntityId, KnightId

if TYPE_CHECKING:
    from collections.abc import Sequence

    import numpy.typing as npt

    from pymoonstone.render.scene import SceneSprite


@dataclass
class _FakeSceneRenderer:
    """Headless SceneRenderTarget stub for deterministic pixel assertions."""

    canvas: npt.NDArray[np.uint8] = field(
        default_factory=lambda: np.zeros((CANVAS_HEIGHT, CANVAS_WIDTH), dtype=np.uint8)
    )
    palette: tuple[RGBColor, ...] = ()
    compose_calls: int = 0

    def set_palette(self, palette: Sequence[RGBColor]) -> None:
        self.palette = tuple(palette)

    def compose_scene(
        self,
        *,
        background: npt.NDArray[np.uint8] | None = None,
        sprites: Sequence[SceneSprite] = (),
        border_index: int = 0,
    ) -> list[SceneSprite]:
        self.compose_calls += 1
        self.canvas.fill(np.uint8(border_index & 0x1F))
        if background is not None:
            bh = min(PLAYFIELD_HEIGHT, background.shape[0])
            bw = min(CANVAS_WIDTH, background.shape[1])
            self.canvas[28 : 28 + bh, :bw] = background[:bh, :bw]
        return list(sprites)


class _RecordingFont:
    """Wraps BitmapFont while recording all drawn strings."""

    def __init__(self) -> None:
        self._inner: BitmapFont = default_bitmap_font()
        self.lines: list[str] = []

    def draw_text(
        self,
        canvas: npt.NDArray[np.uint8],
        text: str,
        x: int,
        y: int,
        *,
        color_index: int | None = 31,
        spacing: int = 0,
    ) -> None:
        self.lines.append(text)
        self._inner.draw_text(canvas, text, x, y, color_index=color_index, spacing=spacing)


class _FakeBundle:
    """Minimal GameAssetBundle stand-in serving synthetic DecodedPicture backdrops."""

    def __init__(self, pictures: dict[str, DecodedPicture]) -> None:
        self._pictures = {k.casefold(): v for k, v in pictures.items()}
        self.requested: list[tuple[str, ...]] = []

    def get_picture(self, *names: str) -> DecodedPicture | None:
        self.requested.append(names)
        for name in names:
            pic = self._pictures.get(name.casefold())
            if pic is not None:
                return pic
        return None


def _make_pic(fill: int = 3) -> DecodedPicture:
    pixels = np.full((PLAYFIELD_HEIGHT, CANVAS_WIDTH), np.uint8(fill), dtype=np.uint8)
    return DecodedPicture(
        width=CANVAS_WIDTH,
        height=PLAYFIELD_HEIGHT,
        planes=5,
        packed_size=0,
        raw_palette=(0,) * 32,
        palette=default_combat_palette(),
        pixels=pixels,
        raw_planes=b"",
    )


def _make_knight(gold: int = 500, hp: int = 60) -> KnightState:
    return KnightState(
        knight_id=KnightId.RED,
        entity_id=EntityId(1),
        x=80,
        y=95,
        hp=hp,
        max_hp=100,
        stamina=70,
        max_stamina=100,
        lives=2,
        gold=gold,
        xp=200,
    )


def _make_overworld() -> OverworldState:
    ow = create_campaign((KnightId.RED,), seed=42)
    ow.knights[KnightId.RED].gold = 500
    ow.knights[KnightId.RED].xp = 200
    return ow


def test_location_backdrop_names_cover_all_piv_files() -> None:
    assert "HighWood.PIV" in location_backdrop_names(LocationKind.HIGHWOOD)
    assert "WaterDeep.PIV" in location_backdrop_names(LocationKind.WATERDEEP)
    assert "hea.piv" in location_backdrop_names(LocationKind.HEALER)
    assert "tav.piv" in location_backdrop_names(LocationKind.TAVERN)
    assert "dice.piv" in location_backdrop_names(LocationKind.DICE)
    assert "mys.piv" in location_backdrop_names(LocationKind.MYSTIC)
    assert "ch.piv" in location_backdrop_names(LocationKind.STONEHENGE)


def test_edge_triggered_navigation_and_wraparound() -> None:
    ow = _make_overworld()
    knight = _make_knight()
    state = LocationMenuState(location=LocationKind.HIGHWOOD)
    options = menu_options_for_location(state, knight)
    assert len(options) == 4

    # Holding dy=1 across 3 ticks only advances cursor once.
    for _ in range(3):
        step_location_menu(state, PlayerInput(dy=1), knight, ow)
    assert state.cursor == 1

    # Releasing and pressing Up wraps around to Leave (index 3).
    step_location_menu(state, PlayerInput(), knight, ow)
    step_location_menu(state, PlayerInput(dy=-1), knight, ow)
    assert state.cursor == 0
    step_location_menu(state, PlayerInput(), knight, ow)
    step_location_menu(state, PlayerInput(dy=-1), knight, ow)
    assert state.cursor == 3

    # Confirming Leave twice while held only triggers once.
    res1 = step_location_menu(state, PlayerInput(confirm=True), knight, ow)
    assert res1.left_location is True
    assert state.left is True


def test_highwood_healer_shop_tavern_and_leave() -> None:
    ow = _make_overworld()
    knight = _make_knight(gold=500, hp=60)
    state = LocationMenuState(location=LocationKind.HIGHWOOD)

    # Enter Healer (index 0) and restore HP (index 0).
    step_location_menu(state, PlayerInput(confirm=True), knight, ow)
    assert state.location == LocationKind.HEALER
    step_location_menu(state, PlayerInput(), knight, ow)
    res_heal = step_location_menu(state, PlayerInput(fire=True), knight, ow)
    assert knight.hp == knight.max_hp
    assert knight.gold < 500
    assert "HEALED" in res_heal.status_message

    # Return to HighWood via Leave (index 3).
    state.cursor = 3
    step_location_menu(state, PlayerInput(), knight, ow)
    res_back = step_location_menu(state, PlayerInput(confirm=True), knight, ow)
    assert res_back.left_location is False
    assert int(state.location) == int(LocationKind.HIGHWOOD)

    # Enter Merchant / Shop (index 1) and buy Sword Upgrade (index 0).
    state.cursor = 1
    step_location_menu(state, PlayerInput(), knight, ow)
    step_location_menu(state, PlayerInput(confirm=True), knight, ow)
    assert int(state.location) == int(LocationKind.SHOP)
    prev_sword = knight.sword_tier
    step_location_menu(state, PlayerInput(), knight, ow)
    step_location_menu(state, PlayerInput(confirm=True), knight, ow)
    assert knight.sword_tier == prev_sword + 1

    # Direct visit_shop helper also supports selling.
    sell_res = visit_shop(knight, MerchantItemKind.SWORD_UPGRADE, sell=True, overworld=ow)
    assert sell_res.accepted is True
    assert knight.sword_tier == prev_sword

    # Return to HighWood, enter Tavern (index 2), adjust wager, roll dice.
    state.cursor = len(menu_options_for_location(state, knight)) - 1
    step_location_menu(state, PlayerInput(), knight, ow)
    step_location_menu(state, PlayerInput(confirm=True), knight, ow)
    assert int(state.location) == int(LocationKind.HIGHWOOD)

    state.cursor = 2
    step_location_menu(state, PlayerInput(), knight, ow)
    step_location_menu(state, PlayerInput(confirm=True), knight, ow)
    assert int(state.location) == int(LocationKind.TAVERN)
    step_location_menu(state, PlayerInput(), knight, ow)
    step_location_menu(state, PlayerInput(dx=1), knight, ow)
    assert state.wager == 20
    gold_before_dice = knight.gold
    res_dice = step_location_menu(state, PlayerInput(confirm=True), knight, ow)
    assert "VS" in res_dice.status_message
    assert knight.gold in (gold_before_dice - 20, gold_before_dice, gold_before_dice + 20)

    # Leave Tavern -> HighWood -> Overworld.
    state.cursor = 3
    step_location_menu(state, PlayerInput(), knight, ow)
    step_location_menu(state, PlayerInput(confirm=True), knight, ow)
    assert int(state.location) == int(LocationKind.HIGHWOOD)
    state.cursor = 3
    step_location_menu(state, PlayerInput(), knight, ow)
    res_exit = step_location_menu(state, PlayerInput(confirm=True), knight, ow)
    assert res_exit.left_location is True


def test_waterdeep_mystic_and_stonehenge_ritual() -> None:
    ow = _make_overworld()
    knight = _make_knight(gold=500, hp=80)
    state = LocationMenuState(location=LocationKind.WATERDEEP)

    # Enter Mystic (index 0) and buy Fireball Scroll (index 0) + Key Hint (index 2).
    step_location_menu(state, PlayerInput(confirm=True), knight, ow)
    assert state.location == LocationKind.MYSTIC
    step_location_menu(state, PlayerInput(), knight, ow)
    res_scroll = step_location_menu(state, PlayerInput(confirm=True), knight, ow)
    assert "SCROLL" in res_scroll.status_message
    state.cursor = 2
    step_location_menu(state, PlayerInput(), knight, ow)
    res_hint = step_location_menu(state, PlayerInput(confirm=True), knight, ow)
    assert "LAIR" in res_hint.status_message

    # Stonehenge ritual succeeds when at Stonehenge, full moon, and all 4 moonstones.
    ow.knights[KnightId.RED].node_id = ow.graph.stonehenge_node_id
    ow.knights[KnightId.RED].moonstones_mask = ALL_MOONSTONES_MASK
    ow.day = 15  # FULL_MOON
    assert ow.moon_phase == MoonPhase.FULL_MOON
    sh_state = LocationMenuState(location=LocationKind.STONEHENGE)
    res_ritual = step_location_menu(sh_state, PlayerInput(confirm=True), knight, ow)
    assert res_ritual.victory is True
    assert res_ritual.left_location is True
    assert ow.game_over is True


def test_render_location_status_and_new_day_screens() -> None:
    ow = _make_overworld()
    ow.knights[KnightId.RED].keys_mask = 0b0101
    ow.knights[KnightId.RED].moonstones_mask = 0b0011
    ow.day = 15
    knight = _make_knight(gold=345, hp=77)
    renderer = _FakeSceneRenderer()
    bundle = _FakeBundle({"ch.piv": _make_pic(fill=5), "hea.piv": _make_pic(fill=7)})

    # Location menu uses hea.piv backdrop.
    loc_font = _RecordingFont()
    loc_state = LocationMenuState(location=LocationKind.HEALER, status_message="HEALED")
    render_location_menu(
        renderer,
        loc_state,
        knight,
        ow,
        loc_font,
        bundle=bundle,
    )
    assert "hea.piv" in bundle.requested[-1]
    assert int(renderer.canvas[28, 0]) == 7
    assert any("HEALER" in line for line in loc_font.lines)

    # Status screen renders all required metrics on ch.piv.
    status_font = _RecordingFont()
    render_status_screen(
        renderer,
        knight,
        ow,
        status_font,
        bundle=bundle,
    )
    assert bundle.requested[-1] == ("ch.piv",)
    assert int(renderer.canvas[28, 0]) == 5
    joined = "\n".join(status_font.lines)
    assert "RED" in joined
    assert "77/100" in joined
    assert "70/100" in joined
    assert "LIVES: 2" in joined
    assert "GOLD: 345" in joined
    assert "XP: 200" in joined
    assert "0101" in joined
    assert "0011" in joined
    assert "DAY 15" in joined
    assert "FULL MOON" in joined

    # New Day lunar banner renders DAY <n> and MoonPhase on solid fallback when bundle is None.
    day_font = _RecordingFont()
    render_new_day_screen(renderer, ow, day_font)
    assert int(renderer.canvas[28, 0]) == 1
    day_joined = "\n".join(day_font.lines)
    assert "DAY 15" in day_joined
    assert "FULL MOON" in day_joined
