"""Interactive town/location menus, status sheet, and New Day banner (T9.3)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, Protocol

import numpy as np

from pymoonstone.assets.palette import RGBColor, default_combat_palette
from pymoonstone.render.renderer import CANVAS_WIDTH, PLAYFIELD_HEIGHT, PLAYFIELD_Y_OFFSET
from pymoonstone.sim.locations import (
    MAX_TAVERN_WAGER,
    MIN_TAVERN_WAGER,
    HealerService,
    MerchantItemKind,
    MerchantTransactionResult,
    MysticService,
    TrainableStat,
    buy_merchant_item,
    healer_hp_cost,
    merchant_buy_price,
    perform_stonehenge_ritual,
    play_tavern_dice,
    sell_merchant_item,
    train_knight_stat,
    visit_healer,
    visit_mystic,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    import numpy.typing as npt

    from pymoonstone.app.input import PlayerInput
    from pymoonstone.assets.picture import DecodedPicture
    from pymoonstone.render.scene import SceneSprite
    from pymoonstone.sim.location_state import KnightState
    from pymoonstone.sim.overworld import OverworldState

_WAGER_STEP = 10


class SceneRenderTarget(Protocol):
    """Minimal compositor interface satisfied by :class:`SceneRenderer`."""

    canvas: npt.NDArray[np.uint8]

    def set_palette(self, palette: Sequence[RGBColor]) -> None: ...

    def compose_scene(
        self,
        *,
        background: npt.NDArray[np.uint8] | None = None,
        sprites: Sequence[SceneSprite] = (),
        border_index: int = 0,
    ) -> list[SceneSprite]: ...


class FontTarget(Protocol):
    """Minimal bitmap font interface satisfied by :class:`BitmapFont`."""

    def draw_text(
        self,
        canvas: npt.NDArray[np.uint8],
        text: str,
        x: int,
        y: int,
        *,
        color_index: int | None = 31,
        spacing: int = 0,
    ) -> None: ...


class PictureBundle(Protocol):
    """Minimal picture lookup interface satisfied by :class:`GameAssetBundle`."""

    def get_picture(self, *names: str) -> DecodedPicture | None: ...


class LocationKind(IntEnum):
    """Supported town, service, and sacred overworld location screens."""

    HIGHWOOD = 0
    WATERDEEP = 1
    HEALER = 2
    TAVERN = 3
    DICE = 4
    MYSTIC = 5
    STONEHENGE = 6
    SHOP = 7
    TRAINING = 8


_BACKDROP_CANDIDATES: dict[LocationKind, tuple[str, ...]] = {
    LocationKind.HIGHWOOD: ("HighWood.PIV", "highwood.piv", "ch.piv"),
    LocationKind.WATERDEEP: ("WaterDeep.PIV", "waterdeep.piv", "ch.piv"),
    LocationKind.HEALER: ("hea.piv", "HighWood.PIV", "ch.piv"),
    LocationKind.TAVERN: ("tav.piv", "dice.piv", "HighWood.PIV", "ch.piv"),
    LocationKind.DICE: ("dice.piv", "tav.piv", "HighWood.PIV", "ch.piv"),
    LocationKind.MYSTIC: ("mys.piv", "WaterDeep.PIV", "ch.piv"),
    LocationKind.STONEHENGE: ("ch.piv",),
    LocationKind.SHOP: ("HighWood.PIV", "ch.piv"),
    LocationKind.TRAINING: ("WaterDeep.PIV", "ch.piv"),
}

_LOCATION_TITLES: dict[LocationKind, str] = {
    LocationKind.HIGHWOOD: "HIGHWOOD",
    LocationKind.WATERDEEP: "WATERDEEP",
    LocationKind.HEALER: "HEALER",
    LocationKind.TAVERN: "TAVERN",
    LocationKind.DICE: "TAVERN DICE",
    LocationKind.MYSTIC: "MYSTIC",
    LocationKind.STONEHENGE: "STONEHENGE",
    LocationKind.SHOP: "MERCHANT",
    LocationKind.TRAINING: "TRAINING",
}

_SHOP_ITEMS: tuple[tuple[str, MerchantItemKind], ...] = (
    ("SWORD UPGRADE", MerchantItemKind.SWORD_UPGRADE),
    ("ARMOUR UPGRADE", MerchantItemKind.ARMOUR_UPGRADE),
    ("SHIELD UPGRADE", MerchantItemKind.SHIELD_UPGRADE),
    ("THROWING KNIFE", MerchantItemKind.THROWING_KNIFE),
    ("HEALING POTION", MerchantItemKind.HEALING_POTION),
    ("STAMINA TONIC", MerchantItemKind.STAMINA_TONIC),
)

_HEALER_SERVICES: tuple[tuple[str, HealerService], ...] = (
    ("RESTORE HP", HealerService.RESTORE_HP),
    ("RESTORE LIFE", HealerService.RESTORE_LIFE),
    ("LIFT CURSE", HealerService.LIFT_CURSE),
)

_MYSTIC_SERVICES: tuple[tuple[str, MysticService], ...] = (
    ("FIREBALL SCROLL", MysticService.FIREBALL_SCROLL),
    ("BLESSING", MysticService.BLESSING),
    ("KEY HINT", MysticService.KEY_HINT),
    ("LAIR KEY", MysticService.LAIR_KEY),
)

_TRAINING_STATS: tuple[tuple[str, TrainableStat], ...] = (
    ("TRAIN STRENGTH", TrainableStat.STRENGTH),
    ("TRAIN CONSTITUTION", TrainableStat.CONSTITUTION),
    ("TRAIN ENDURANCE", TrainableStat.ENDURANCE),
)

_HIGHWOOD_TARGETS: tuple[LocationKind, ...] = (
    LocationKind.SHOP,
    LocationKind.TAVERN,
    LocationKind.HEALER,
)

_WATERDEEP_TARGETS: tuple[LocationKind, ...] = (
    LocationKind.SHOP,
    LocationKind.TAVERN,
    LocationKind.HEALER,
    LocationKind.MYSTIC,
)

# Scroll button rectangles (x, y_piv, width, height) from Amiga town layouts
# (placevisit.cpp kTownA / kTownB). y_piv is relative to PLAYFIELD_Y_OFFSET (28).
_TOWN_SCROLL_RECTS: dict[LocationKind, tuple[tuple[int, int, int, int], ...]] = {
    LocationKind.HIGHWOOD: (
        (256, 30, 64, 16),
        (256, 66, 64, 16),
        (256, 106, 64, 16),
        (256, 183, 64, 12),
    ),
    LocationKind.WATERDEEP: (
        (0, 26, 64, 16),
        (0, 63, 64, 16),
        (0, 101, 64, 16),
        (0, 134, 64, 31),
        (0, 182, 64, 12),
    ),
}


@dataclass(slots=True)
class LocationMenuState:
    """Interactive menu state machine for towns, shops, and Stonehenge."""

    location: LocationKind
    cursor: int = 0
    parent_location: LocationKind | None = None
    wager: int = 10
    status_message: str = ""
    left: bool = False
    prev_dx: int = 0
    prev_dy: int = 0
    prev_fire: bool = False
    prev_confirm: bool = False


@dataclass(slots=True, frozen=True)
class LocationStepResult:
    """Outcome of one 50 Hz tick in a location menu."""

    left_location: bool
    action_taken: str
    status_message: str
    victory: bool = False


def location_backdrop_names(
    location: LocationKind,
    parent_location: LocationKind | None = None,
) -> tuple[str, ...]:
    """Return ordered picture candidate names for ``location``."""
    if parent_location == LocationKind.WATERDEEP:
        town_fallbacks = ("WaterDeep.PIV", "waterdeep.piv", "ch.piv")
        waterdeep_overrides: dict[LocationKind, tuple[str, ...]] = {
            LocationKind.HEALER: ("hea.piv", *town_fallbacks),
            LocationKind.TAVERN: ("tav.piv", "dice.piv", *town_fallbacks),
            LocationKind.DICE: ("dice.piv", "tav.piv", *town_fallbacks),
            LocationKind.MYSTIC: ("mys.piv", *town_fallbacks),
            LocationKind.SHOP: town_fallbacks,
            LocationKind.TRAINING: town_fallbacks,
        }
        if location in waterdeep_overrides:
            return waterdeep_overrides[location]
    return _BACKDROP_CANDIDATES.get(location, ("ch.piv",))


def visit_shop(
    knight: KnightState,
    item: MerchantItemKind,
    quantity: int = 1,
    *,
    sell: bool = False,
    overworld: OverworldState | None = None,
) -> MerchantTransactionResult:
    """Buy or sell an item at the Highwood merchant."""
    if sell:
        return sell_merchant_item(knight, item, quantity, overworld=overworld)
    return buy_merchant_item(knight, item, quantity, overworld=overworld)


def menu_options_for_location(state: LocationMenuState, knight: KnightState) -> tuple[str, ...]:
    """Return display labels for all selectable rows in ``state.location``."""
    loc = state.location
    if loc == LocationKind.HIGHWOOD:
        return ("MERCHANT", "TAVERN", "HEALER", "LEAVE")
    if loc == LocationKind.WATERDEEP:
        return ("MERCHANT", "TAVERN", "HEALER", "MYSTIC", "LEAVE")
    if loc == LocationKind.HEALER:
        hp_cost = healer_hp_cost(knight)
        return (f"RESTORE HP {hp_cost}G", "RESTORE LIFE 150G", "LIFT CURSE 40G", "LEAVE")
    if loc == LocationKind.SHOP:
        rows = [f"{label} {merchant_buy_price(knight, item)}G" for label, item in _SHOP_ITEMS]
        rows.append("LEAVE")
        return tuple(rows)
    if loc in (LocationKind.TAVERN, LocationKind.DICE):
        return (f"ROLL DICE {state.wager}G", "WAGER +10", "WAGER -10", "LEAVE")
    if loc == LocationKind.MYSTIC:
        return ("FIREBALL SCROLL 60G", "BLESSING 80G", "KEY HINT 30G", "LAIR KEY 120G", "LEAVE")
    if loc == LocationKind.TRAINING:
        return (*(label for label, _ in _TRAINING_STATS), "LEAVE")
    return ("PERFORM RITUAL", "LEAVE")


def _enter_sub_location(state: LocationMenuState, target: LocationKind) -> LocationStepResult:
    state.parent_location = state.location
    state.location = target
    state.cursor = 0
    state.status_message = ""
    return LocationStepResult(False, f"enter_{target.name.lower()}", "")


def _leave_current_location(state: LocationMenuState) -> LocationStepResult:
    if state.parent_location is not None:
        parent = state.parent_location
        state.location = parent
        state.parent_location = None
        state.cursor = 0
        state.status_message = ""
        return LocationStepResult(False, f"return_{parent.name.lower()}", "")
    state.left = True
    return LocationStepResult(True, "leave", "LEFT LOCATION")


def _format_status(text: str) -> str:
    return text.replace("_", " ").upper()


def _confirm_highwood(state: LocationMenuState) -> LocationStepResult:
    if state.cursor < len(_HIGHWOOD_TARGETS):
        return _enter_sub_location(state, _HIGHWOOD_TARGETS[state.cursor])
    return _leave_current_location(state)


def _confirm_waterdeep(state: LocationMenuState) -> LocationStepResult:
    if state.cursor < len(_WATERDEEP_TARGETS):
        return _enter_sub_location(state, _WATERDEEP_TARGETS[state.cursor])
    return _leave_current_location(state)


def _confirm_healer(
    state: LocationMenuState,
    knight: KnightState,
    overworld: OverworldState,
    *,
    cursed: bool,
) -> LocationStepResult:
    if state.cursor >= len(_HEALER_SERVICES):
        return _leave_current_location(state)
    _, service = _HEALER_SERVICES[state.cursor]
    res = visit_healer(knight, service, cursed=cursed, overworld=overworld)
    msg = _format_status(res.reason)
    state.status_message = msg
    return LocationStepResult(False, f"healer_{res.reason}", msg)


def _confirm_shop(
    state: LocationMenuState,
    knight: KnightState,
    overworld: OverworldState,
) -> LocationStepResult:
    if state.cursor >= len(_SHOP_ITEMS):
        return _leave_current_location(state)
    _, item = _SHOP_ITEMS[state.cursor]
    res = visit_shop(knight, item, overworld=overworld)
    msg = _format_status(res.reason)
    state.status_message = msg
    return LocationStepResult(False, f"shop_{res.reason}", msg)


def _adjust_wager(state: LocationMenuState, delta: int) -> str:
    state.wager = max(MIN_TAVERN_WAGER, min(MAX_TAVERN_WAGER, state.wager + delta))
    msg = f"WAGER {state.wager}G"
    state.status_message = msg
    return msg


def _confirm_tavern(
    state: LocationMenuState,
    knight: KnightState,
    overworld: OverworldState,
) -> LocationStepResult:
    if state.cursor == 0:
        res = play_tavern_dice(knight, state.wager, overworld.prng, overworld=overworld)
        msg = f"{_format_status(res.reason)} {res.player_total} VS {res.house_total}"
        state.status_message = msg
        return LocationStepResult(False, f"dice_{res.reason}", msg)
    if state.cursor == 1:
        msg = _adjust_wager(state, _WAGER_STEP)
        return LocationStepResult(False, "wager_up", msg)
    if state.cursor == 2:
        msg = _adjust_wager(state, -_WAGER_STEP)
        return LocationStepResult(False, "wager_down", msg)
    return _leave_current_location(state)


def _confirm_mystic(
    state: LocationMenuState,
    knight: KnightState,
    overworld: OverworldState,
) -> LocationStepResult:
    if state.cursor >= len(_MYSTIC_SERVICES):
        return _leave_current_location(state)
    _, service = _MYSTIC_SERVICES[state.cursor]
    res = visit_mystic(knight, service, overworld=overworld)
    msg = _format_status(res.reason)
    if res.hinted_lair_index is not None:
        msg = f"LAIR {res.hinted_lair_index + 1}"
    elif res.granted_key_index is not None:
        msg = f"KEY {res.granted_key_index + 1} GRANTED"
    state.status_message = msg
    return LocationStepResult(False, f"mystic_{res.reason}", msg)


def _confirm_training(
    state: LocationMenuState,
    knight: KnightState,
    overworld: OverworldState,
) -> LocationStepResult:
    if state.cursor >= len(_TRAINING_STATS):
        return _leave_current_location(state)
    _, stat = _TRAINING_STATS[state.cursor]
    res = train_knight_stat(knight, stat, overworld=overworld)
    msg = _format_status(res.reason)
    state.status_message = msg
    return LocationStepResult(False, f"train_{res.reason}", msg)


def _confirm_stonehenge(
    state: LocationMenuState,
    knight: KnightState,
    overworld: OverworldState,
) -> LocationStepResult:
    if state.cursor != 0:
        return _leave_current_location(state)
    res = perform_stonehenge_ritual(overworld, knight.knight_id, knight_combat=knight)
    msg = _format_status(res.reason)
    state.status_message = msg
    return LocationStepResult(res.victory, f"ritual_{res.reason}", msg, victory=res.victory)


def _dispatch_confirm(
    state: LocationMenuState,
    knight: KnightState,
    overworld: OverworldState,
    *,
    cursed: bool,
) -> LocationStepResult:
    loc = state.location
    if loc == LocationKind.HIGHWOOD:
        return _confirm_highwood(state)
    if loc == LocationKind.WATERDEEP:
        return _confirm_waterdeep(state)
    if loc == LocationKind.HEALER:
        return _confirm_healer(state, knight, overworld, cursed=cursed)
    if loc == LocationKind.SHOP:
        return _confirm_shop(state, knight, overworld)
    if loc in (LocationKind.TAVERN, LocationKind.DICE):
        return _confirm_tavern(state, knight, overworld)
    if loc == LocationKind.MYSTIC:
        return _confirm_mystic(state, knight, overworld)
    if loc == LocationKind.TRAINING:
        return _confirm_training(state, knight, overworld)
    return _confirm_stonehenge(state, knight, overworld)


def _consume_edge_inputs(state: LocationMenuState, inp: PlayerInput) -> tuple[int, int, bool]:
    norm = inp.normalized()
    dx_edge = norm.dx if norm.dx != state.prev_dx else 0
    dy_edge = norm.dy if norm.dy != state.prev_dy else 0
    fire_edge = norm.fire and not state.prev_fire
    confirm_edge = (norm.confirm and not state.prev_confirm) or fire_edge
    state.prev_dx = norm.dx
    state.prev_dy = norm.dy
    state.prev_fire = norm.fire
    state.prev_confirm = norm.confirm
    return dx_edge, dy_edge, confirm_edge


def step_location_menu(
    state: LocationMenuState,
    inp: PlayerInput,
    knight: KnightState,
    overworld: OverworldState,
    *,
    cursed: bool = False,
) -> LocationStepResult:
    """Advance ``state`` by one 50 Hz tick using edge-triggered Up/Down/Confirm."""
    dx_edge, dy_edge, confirm_edge = _consume_edge_inputs(state, inp)
    options = menu_options_for_location(state, knight)
    if dy_edge != 0:
        state.cursor = (state.cursor + (1 if dy_edge > 0 else -1)) % len(options)

    if dx_edge != 0 and state.location in (LocationKind.TAVERN, LocationKind.DICE):
        msg = _adjust_wager(state, _WAGER_STEP if dx_edge > 0 else -_WAGER_STEP)
        return LocationStepResult(False, "wager_adjust", msg)

    if confirm_edge:
        return _dispatch_confirm(state, knight, overworld, cursed=cursed)

    return LocationStepResult(state.left, "idle", state.status_message)


def _brightest_color_index(palette: Sequence[tuple[int, int, int]]) -> int:
    limit = min(len(palette), 32)
    if limit <= 1:
        return 1
    return max(range(1, limit), key=lambda idx: sum(palette[idx]))


def _darkest_color_index(palette: Sequence[tuple[int, int, int]]) -> int:
    limit = min(len(palette), 32)
    if limit <= 0:
        return 0
    return min(range(limit), key=lambda idx: sum(palette[idx]))


def _compose_picture(scene_renderer: SceneRenderTarget, pic: DecodedPicture) -> tuple[int, int]:
    scene_renderer.set_palette(pic.palette)
    scene_renderer.compose_scene(
        background=pic.pixels[:PLAYFIELD_HEIGHT, :CANVAS_WIDTH],
        sprites=(),
        border_index=0,
    )
    return _brightest_color_index(pic.palette), _darkest_color_index(pic.palette)


def _compose_backdrop(
    scene_renderer: SceneRenderTarget,
    bundle: PictureBundle | None,
    names: tuple[str, ...],
    *,
    fill_index: int = 1,
) -> tuple[int, int]:
    if bundle is not None:
        pic = bundle.get_picture(*names)
        if pic is not None:
            return _compose_picture(scene_renderer, pic)
    bg: npt.NDArray[np.uint8] = np.full(
        (PLAYFIELD_HEIGHT, CANVAS_WIDTH), np.uint8(fill_index), dtype=np.uint8
    )
    pal = default_combat_palette()
    scene_renderer.set_palette(pal)
    scene_renderer.compose_scene(background=bg, sprites=(), border_index=0)
    return 31, _darkest_color_index(pal)


def _compose_location_backdrop(
    scene_renderer: SceneRenderTarget,
    bundle: PictureBundle | None,
    location: LocationKind,
    parent_location: LocationKind | None = None,
) -> tuple[int, int, bool]:
    names = location_backdrop_names(location, parent_location=parent_location)
    if bundle is not None and location in _TOWN_SCROLL_RECTS and len(names) > 1:
        town_pic = bundle.get_picture(*names[:-1])
        if town_pic is not None:
            fg, bg_panel = _compose_picture(scene_renderer, town_pic)
            return fg, bg_panel, True
        fg, bg_panel = _compose_backdrop(scene_renderer, bundle, (names[-1],))
        return fg, bg_panel, False
    fg, bg_panel = _compose_backdrop(scene_renderer, bundle, names)
    return fg, bg_panel, False


def draw_rect(
    canvas: npt.NDArray[np.uint8],
    x: int,
    y: int,
    width: int,
    height: int,
    color_index: int,
    *,
    filled: bool = False,
) -> None:
    """Draw a filled or 1px-outlined rectangle clipped to the playfield."""
    x0 = max(0, x)
    y0 = max(PLAYFIELD_Y_OFFSET, y)
    x1 = min(CANVAS_WIDTH, x + width)
    y1 = min(PLAYFIELD_Y_OFFSET + PLAYFIELD_HEIGHT, y + height)
    if x0 >= x1 or y0 >= y1:
        return
    c = np.uint8(color_index & 0x1F)
    if filled:
        canvas[y0:y1, x0:x1] = c
        return
    canvas[y0, x0:x1] = c
    canvas[y1 - 1, x0:x1] = c
    canvas[y0:y1, x0] = c
    canvas[y0:y1, x1 - 1] = c


def _draw_scroll_highlight(
    canvas: npt.NDArray[np.uint8],
    rect: tuple[int, int, int, int],
    color_index: int,
) -> None:
    bx, by, bw, bh = rect
    draw_rect(canvas, bx, PLAYFIELD_Y_OFFSET + by, bw, bh, color_index, filled=False)


def _render_town_scroll_cursor(
    canvas: npt.NDArray[np.uint8],
    location: LocationKind,
    cursor_idx: int,
    label: str,
    font: FontTarget,
    fg: int,
) -> None:
    rects = _TOWN_SCROLL_RECTS[location]
    bx, by, bw, bh = rects[cursor_idx % len(rects)]
    _draw_scroll_highlight(canvas, (bx, by, bw, bh), fg)
    cy = PLAYFIELD_Y_OFFSET + by + max(0, (bh - 8) // 2)
    if bx == 0:
        font.draw_text(canvas, "<", bw + 2, cy, color_index=fg)
    else:
        font.draw_text(canvas, ">", max(0, bx - 10), cy, color_index=fg)
    font.draw_text(canvas, f"> {label}", 100, 240, color_index=fg)


def render_location_menu(
    scene_renderer: SceneRenderTarget,
    state: LocationMenuState,
    knight: KnightState,
    overworld: OverworldState,
    font: FontTarget,
    *,
    bundle: PictureBundle | None = None,
) -> None:
    """Render the active location backdrop, menu options, and knight stats."""
    fg, bg_panel, has_scroll = _compose_location_backdrop(
        scene_renderer, bundle, state.location, state.parent_location
    )
    canvas = scene_renderer.canvas
    title = _LOCATION_TITLES.get(state.location, "LOCATION")
    font.draw_text(canvas, title, 8, 8, color_index=fg)
    font.draw_text(
        canvas, f"GOLD:{knight.gold} HP:{knight.hp}/{knight.max_hp}", 136, 8, color_index=fg
    )
    font.draw_text(canvas, f"DAY {overworld.day}", 8, 240, color_index=fg)

    options = menu_options_for_location(state, knight)
    selected_idx = state.cursor % len(options)
    if has_scroll:
        _render_town_scroll_cursor(
            canvas, state.location, selected_idx, options[selected_idx], font, fg
        )
    else:
        start_y = 44
        max_chars = max((len(label) + 2 for label in options), default=12)
        panel_x = 16
        panel_y = 38
        panel_w = min(CANVAS_WIDTH - panel_x - 16, max(184, max_chars * 8 + 16))
        panel_h = len(options) * 14 + 10
        draw_rect(canvas, panel_x, panel_y, panel_w, panel_h, bg_panel, filled=True)
        draw_rect(canvas, panel_x, panel_y, panel_w, panel_h, fg, filled=False)
        for idx, label in enumerate(options):
            prefix = "+ " if idx == selected_idx else "  "
            font.draw_text(canvas, f"{prefix}{label}", 24, start_y + idx * 14, color_index=fg)

    if state.status_message:
        font.draw_text(canvas, state.status_message[:36], 8, 229, color_index=fg)


def render_status_screen(
    scene_renderer: SceneRenderTarget,
    knight: KnightState,
    overworld: OverworldState,
    font: FontTarget,
    *,
    bundle: PictureBundle | None = None,
) -> None:
    """Render the knight character status sheet on ``ch.piv`` (or solid fallback)."""
    fg, _ = _compose_backdrop(scene_renderer, bundle, ("ch.piv",))
    canvas = scene_renderer.canvas
    ow_knight = overworld.knights.get(knight.knight_id)
    keys_mask = ow_knight.keys_mask if ow_knight is not None else knight.keys
    stones_mask = ow_knight.moonstones_mask if ow_knight is not None else knight.moonstones
    phase_label = overworld.moon_phase.name.replace("_", " ")

    font.draw_text(canvas, f"KNIGHT: {knight.knight_id.name}", 24, 40, color_index=fg)
    font.draw_text(canvas, f"HP: {knight.hp}/{knight.max_hp}", 24, 60, color_index=fg)
    font.draw_text(
        canvas, f"STAMINA: {knight.stamina}/{knight.max_stamina}", 24, 76, color_index=fg
    )
    font.draw_text(canvas, f"LIVES: {knight.lives}", 24, 92, color_index=fg)
    font.draw_text(canvas, f"GOLD: {knight.gold}", 24, 108, color_index=fg)
    font.draw_text(canvas, f"XP: {knight.xp}", 24, 124, color_index=fg)
    font.draw_text(canvas, f"KEYS MASK: {keys_mask:04b}", 24, 144, color_index=fg)
    font.draw_text(canvas, f"MOONSTONES MASK: {stones_mask:04b}", 24, 160, color_index=fg)
    font.draw_text(canvas, f"DAY {overworld.day}: {phase_label}", 24, 184, color_index=fg)
