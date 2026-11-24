"""HD GPU scene renderers for overworld, towns, sheets, services, and menus (T19.6, §14.7)."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import numpy as np

from harddaysengine.app.intro_player import _draw_mog_bold_line, _draw_title_text_and_banner
from harddaysengine.app.map_scene import draw_map_text
from harddaysengine.render.renderer import CANVAS_HEIGHT, CANVAS_WIDTH, PLAYFIELD_Y_OFFSET
from harddaysengine.render.scene import blit_indexed_sprite, palette_fx_to_rgb
from harddaysengine.sim.m68k import s16
from harddaysengine.sim.meeting import MeetingPhase
from harddaysengine.sim.overworld import list_rows, map_sprites
from harddaysengine.sim.service_screen import Phase as ServicePhase, number3
from harddaysengine.sim.service_screen_types import CelDraw, LabelDraw, NumberDraw
from harddaysengine.sim.towns import TownEntry
from harddaysengine.sim.valley import ValleyPhase

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    import moderngl

    from harddaysengine.app.game import GameSession
    from harddaysengine.app.meeting_screen import MeetingScreen
    from harddaysengine.app.service_screen import ServiceScreen
    from harddaysengine.app.sheet_screen import SheetScreen
    from harddaysengine.app.town_screen import TownScreen
    from harddaysengine.app.valley_screen import ValleyScreen
    from harddaysengine.assets.palette import RGBColor
    from harddaysengine.render.hd_compositor import HdCompositor
    from harddaysengine.sim.overworld import Arrival, OverworldState

    type _PaletteResolver = Callable[[GameSession, Sequence[RGBColor]], tuple[RGBColor, ...]]


def _row_text(world: OverworldState, row: Arrival) -> str:
    rules = world.data()
    if row.kind == 2:
        return rules.lair_text
    if row.kind == 1:
        return rules.battle_text + world.names[row.key - 1]
    index = row.kind - 21
    if not 0 <= index < len(rules.place_texts):
        raise ValueError("map menu place text outside its original table")
    return rules.place_texts[index]


def _draw_map_dragon_hd(session: GameSession, compositor: HdCompositor) -> None:
    if not session.overworld.dragon_active or session.map_runtime is None:
        return
    history = session.map_runtime.scene.hd_history
    for draw in history.last_draws:
        cel = history.cels.get(draw.cel)
        if cel is None or not 0 <= draw.frame < len(cel.frames):
            continue
        frame = cel.frames[draw.frame]
        shift = 0 if draw.mirrored else frame.meta.hot_x
        compositor.draw_cel_frame(
            draw.cel,
            frame,
            s16(draw.x) - shift,
            s16(draw.y) + PLAYFIELD_Y_OFFSET,
            mirrored=draw.mirrored,
        )


def _draw_map_menu_hd(session: GameSession, compositor: HdCompositor) -> None:
    world = session.overworld
    bundle = session.asset_bundle
    if not world.menu or bundle is None:
        return
    cel = bundle.get_cel("mi.c")
    font = bundle.get_cel("Small.font")
    if cel is None or font is None:
        return
    compositor.draw_cel_frame("mi.c", cel.frames[32], 50, 100 + PLAYFIELD_Y_OFFSET)
    compositor.draw_cel_frame(
        "mi.c", cel.frames[world.current_actor.kind], 55, 105 + PLAYFIELD_Y_OFFSET
    )
    overlay = np.zeros((CANVAS_HEIGHT, CANVAS_WIDTH), dtype=np.uint8)
    rules = world.data()
    headline = world.names[int(world.current_knight_id)] + rules.may_text
    draw_map_text(overlay, font, rules.char_map, headline, 65, 105)
    for index, row in enumerate(list_rows(world)):
        draw_map_text(
            overlay,
            font,
            rules.char_map,
            f"{index + 1} " + _row_text(world, row),
            55,
            115 + index * 6,
        )
    compositor.draw_indexed_overlay(overlay, cache_key="map_menu_text")


def render_overworld_hd(
    session: GameSession,
    compositor: HdCompositor,
    resolve_palette: _PaletteResolver,
) -> moderngl.Texture | None:
    """Render the overworld map with live cycling mask or ``cycle_frames`` (§14.3.2, §14.7)."""
    world = session.overworld
    bundle = session.asset_bundle
    if world.rules is None or bundle is None:
        return None
    picture, cel = bundle.get_test_picture(8), bundle.get_cel("mi.c")
    if picture is None or cel is None:
        return None

    steady = session._overworld_palette(picture.palette)
    live = resolve_palette(session, steady)
    compositor.set_palettes(live, picture.palette, fade_reference=steady)
    compositor.begin_scene()

    period = world.rules.cycle_period
    cycle_index = (session.tick_count // period) if (world.colors.active and period > 0) else 0
    compositor.draw_picture("test_8", picture.pixels[:200, :320], cycle_index=cycle_index)

    for sprite in map_sprites(world):
        if 0 <= sprite.frame < len(cel.frames):
            compositor.draw_cel_frame(
                "mi.c",
                cel.frames[sprite.frame],
                s16(sprite.x),
                s16(sprite.y) + PLAYFIELD_Y_OFFSET,
            )
    _draw_map_dragon_hd(session, compositor)
    _draw_map_menu_hd(session, compositor)
    return compositor.output_texture


def render_town_hd(
    session: GameSession,
    compositor: HdCompositor,
    screen: TownScreen,
    resolve_palette: _PaletteResolver,
) -> moderngl.Texture | None:
    """Render town welcome prologue or painted town buttons + hardware pointer (§14.7)."""
    entry = screen.state.entry
    if entry == TownEntry.MAP_OUT:
        return render_overworld_hd(session, compositor, resolve_palette)
    if entry == TownEntry.WELCOME_OUT:
        if screen.message is None or screen.font is None:
            return None
        live = resolve_palette(session, screen.message.palette)
        compositor.set_palettes(live, screen.message.palette)
        compositor.begin_scene()
        compositor.draw_picture(screen.welcome.picture, screen.message.pixels[:200, :320])
        overlay = np.zeros((CANVAS_HEIGHT, CANVAS_WIDTH), dtype=np.uint8)
        for line in screen.welcome.lines:
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
        compositor.draw_indexed_overlay(overlay, cache_key="town_welcome_text")
        return compositor.output_texture

    live = resolve_palette(session, screen.picture.palette)
    compositor.set_palettes(live, screen.picture.palette)
    compositor.begin_scene()
    compositor.draw_picture(screen.data.picture, screen.picture.pixels[:200, :320])
    compositor.draw_indexed_overlay(
        screen.pointer_pixels,
        x=screen.state.pointer_x,
        y=screen.state.pointer_y + PLAYFIELD_Y_OFFSET,
        cache_key="town_pointer",
    )
    return compositor.output_texture


def render_sheet_hd(
    session: GameSession,
    compositor: HdCompositor,
    screen: SheetScreen,
    resolve_palette: _PaletteResolver,
) -> moderngl.Texture:
    """Render shared sheet icons (``ki.cel``), text overlay, and ``po.cel`` pointer (§14.7)."""
    steady = palette_fx_to_rgb(screen.palette(session.overworld))
    template = palette_fx_to_rgb(screen.template_palette())
    live = resolve_palette(session, steady)
    compositor.set_palettes(live, template, fade_reference=steady)
    compositor.begin_scene()
    state = screen.state
    for icon in state.icons:
        frame = screen.cel.frames[icon.frame]
        hot = 0 if icon.mirror else frame.meta.hot_x
        compositor.draw_cel_frame(
            "ki.cel",
            frame,
            icon.x - hot,
            icon.y + PLAYFIELD_Y_OFFSET,
            mirrored=icon.mirror,
        )
    overlay = np.zeros((CANVAS_HEIGHT, CANVAS_WIDTH), dtype=np.uint8)
    for text in state.texts:
        draw_map_text(overlay, screen.font, screen.mapping, text.text, text.x, text.y)
    draw_map_text(overlay, screen.font, screen.mapping, state.hover, 4, 2)
    compositor.draw_indexed_overlay(overlay, cache_key="sheet_text")
    compositor.draw_cel_frame(
        "po.cel",
        screen.pointer.frames[0],
        state.pointer_x,
        state.pointer_y + PLAYFIELD_Y_OFFSET,
    )
    return compositor.output_texture


def _draw_service_donation_hd(
    compositor: HdCompositor,
    screen: ServiceScreen,
    overlay: np.ndarray,
) -> None:
    state = screen.state
    opening = screen.screens.opening(state.kind)
    cel_file = screen.screens.donation.cel_file
    for draw in screen.screens.donation.draws:
        if isinstance(draw, CelDraw):
            idx = opening.portrait if draw.frame is None else draw.frame
            frame = screen.cel.frames[idx]
            compositor.draw_cel_frame(
                cel_file, frame, draw.x - frame.meta.hot_x, draw.y + PLAYFIELD_Y_OFFSET
            )
        elif isinstance(draw, LabelDraw):
            draw_map_text(
                overlay,
                screen.font,
                screen.char_map,
                draw.text,
                draw.x,
                draw.y,
                style=draw.style,
                span=screen.span,
            )
        elif isinstance(draw, NumberDraw):
            val = state.donation.purse if draw.purse else state.donation.amount
            draw_map_text(
                overlay,
                screen.font,
                screen.char_map,
                number3(val),
                draw.x,
                draw.y,
                style=draw.style,
                span=screen.span,
            )


def render_service_hd(
    session: GameSession,
    compositor: HdCompositor,
    screen: ServiceScreen,
    resolve_palette: _PaletteResolver,
) -> moderngl.Texture:
    """Render healer/mystic backdrop, donation cels/labels, and hardware pointer (§14.7)."""
    state = screen.state
    opening = screen.screens.opening(state.kind)
    live = resolve_palette(session, screen.picture.palette)
    compositor.set_palettes(live, screen.picture.palette)
    compositor.begin_scene()
    compositor.draw_picture(opening.picture, screen.picture.pixels[:200, :320])

    overlay = np.zeros((CANVAS_HEIGHT, CANVAS_WIDTH), dtype=np.uint8)
    if state.phase == ServicePhase.DONATION:
        _draw_service_donation_hd(compositor, screen, overlay)
    else:
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
    blit_indexed_sprite(
        overlay, screen.pointer, state.pointer_x, state.pointer_y + PLAYFIELD_Y_OFFSET
    )
    compositor.draw_indexed_overlay(overlay, cache_key="service_overlay")
    return compositor.output_texture


def render_valley_hd(
    session: GameSession,
    compositor: HdCompositor,
    screen: ValleyScreen,
    resolve_palette: _PaletteResolver,
    render_combat: Callable[[], moderngl.Texture | None],
) -> moderngl.Texture | None:
    """Render Valley message/loading screen, or delegate MAP_OUT / COMBAT_OUT (§14.7)."""
    state = screen.state
    if state.phase == ValleyPhase.MAP_OUT:
        return render_overworld_hd(session, compositor, resolve_palette)
    if state.phase == ValleyPhase.COMBAT_OUT:
        return render_combat()
    base = screen.palette()
    live = resolve_palette(session, base)
    compositor.set_palettes(live, base)
    compositor.begin_scene()
    compositor.draw_picture(screen.data.loading.picture, screen.picture.pixels[:200, :320])
    loading = state.phase in (ValleyPhase.LOADING, ValleyPhase.LOADING_OUT)
    lines = (
        screen.data.loading.messages[state.loading_index]
        if loading
        else screen.data.victory
        if state.won
        else screen.data.missing
    )
    overlay = np.zeros((CANVAS_HEIGHT, CANVAS_WIDTH), dtype=np.uint8)
    for line in lines:
        draw_map_text(
            overlay,
            screen.font,
            screen.mapping,
            line.text,
            line.x,
            line.y,
            style=line.style | screen.data.style,
            span=screen.span,
        )
    compositor.draw_indexed_overlay(overlay, cache_key="valley_text")
    return compositor.output_texture


def render_meeting_hd(
    session: GameSession,
    compositor: HdCompositor,
    screen: MeetingScreen,
    resolve_palette: _PaletteResolver,
    render_combat: Callable[[], moderngl.Texture | None],
) -> moderngl.Texture | None:
    """Render knight meeting protection dialog, or delegate MAP_OUT / COMBAT_OUT (§14.7)."""
    if screen.state.phase == MeetingPhase.MAP_OUT:
        return render_overworld_hd(session, compositor, resolve_palette)
    if screen.state.phase == MeetingPhase.COMBAT_OUT:
        return render_combat()
    base = screen.palette()
    live = resolve_palette(session, base)
    compositor.set_palettes(live, base)
    compositor.begin_scene()
    compositor.draw_picture(screen.data.picture, screen.picture.pixels[:200, :320])
    overlay = np.zeros((CANVAS_HEIGHT, CANVAS_WIDTH), dtype=np.uint8)
    for line in screen.lines:
        draw_map_text(
            overlay,
            screen.font,
            screen.mapping,
            line.text,
            line.x,
            line.y,
            style=line.style | screen.data.style,
            span=screen.span,
        )
    compositor.draw_indexed_overlay(overlay, cache_key="meeting_text")
    return compositor.output_texture


def _draw_calendar_moon_hd(
    session: GameSession,
    compositor: HdCompositor,
    moon_x: int,
    moon_y: int,
) -> None:
    if session.asset_bundle is None:
        return
    icons = session.asset_bundle.get_cel("ki.cel")
    if icons is not None and 0 <= session.overworld.moon_frame < len(icons.frames):
        compositor.draw_cel_frame(
            "ki.cel",
            icons.frames[session.overworld.moon_frame],
            moon_x,
            moon_y + PLAYFIELD_Y_OFFSET,
        )


def render_calendar_hd(
    session: GameSession,
    compositor: HdCompositor,
    resolve_palette: _PaletteResolver,
    *,
    farewell: bool = False,
) -> moderngl.Texture | None:
    """Render Next Day (``ch.piv`` + moon ``ki.cel``) or Game Over farewell screen (§14.7)."""
    bundle = session.asset_bundle
    rules = session.overworld.turn_rules
    if bundle is None or rules is None:
        return None
    pic_name = "message.piv" if farewell else "ch.piv"
    picture = bundle.get_picture(pic_name)
    font = bundle.get_cel("bold.f")
    if picture is None or font is None:
        return None
    base = (
        session._picture_target_palette("message.piv")
        if farewell
        else session._calendar_target_palette()
    )
    live = resolve_palette(session, base)
    compositor.set_palettes(live, base)
    compositor.begin_scene()
    compositor.draw_picture(pic_name, picture.pixels[:200, :320])

    overlay = np.zeros((CANVAS_HEIGHT, CANVAS_WIDTH), dtype=np.uint8)
    for row in rules.farewell if farewell else rules.next_day:
        draw_map_text(
            overlay,
            font,
            session.overworld.data().char_map,
            row.text,
            row.x,
            row.y,
            style=row.style | 8,
            span=rules.span,
        )
    compositor.draw_indexed_overlay(overlay, cache_key="calendar_text")
    if not farewell:
        _draw_calendar_moon_hd(session, compositor, rules.moon_x, rules.moon_y)
    return compositor.output_texture


def _render_knight_pick_hd(
    session: GameSession,
    compositor: HdCompositor,
    resolve_palette: _PaletteResolver,
) -> moderngl.Texture | None:
    bundle = session.asset_bundle
    if bundle is None:
        return None
    player = bundle.get_intro_player()
    if player is None or player.menu_data is None:
        return None
    bold = bundle.get_cel(player.plan.font.file)
    sel = bundle.get_cel("sel.cel")
    ks = player.menu_data.knight_select
    base = palette_fx_to_rgb(ks.palette)
    live = resolve_palette(session, base)
    compositor.set_palettes(live, base)
    compositor.begin_scene()
    slot = session.knight_pick_slot & 3
    if bold is not None:
        text_buf = np.zeros((200, 320), dtype=np.uint8)
        for line in ks.lines:
            _draw_mog_bold_line(text_buf, bold, player.menu_data.char_map, line, ks.span)
        name_line = replace(
            ks.lines[0],
            text=ks.knight_names[slot],
            x=ks.name_x,
            y=ks.name_y,
            style=ks.name_style,
        )
        _draw_mog_bold_line(text_buf, bold, player.menu_data.char_map, name_line, ks.span)
        compositor.draw_indexed_overlay(
            text_buf, x=0, y=PLAYFIELD_Y_OFFSET, cache_key="knight_select_text"
        )
    if sel is not None and len(sel.frames) >= ks.portrait_first_frame + 4:
        for idx in range(4):
            if not (session.knight_taken_mask & (1 << idx)):
                compositor.draw_cel_frame(
                    "sel.cel",
                    sel.frames[ks.portrait_first_frame + idx],
                    ks.portrait_x[idx],
                    ks.portrait_y + PLAYFIELD_Y_OFFSET,
                )
        compositor.draw_cel_frame(
            "sel.cel",
            sel.frames[ks.highlight_frame],
            ks.portrait_x[slot],
            ks.portrait_y + PLAYFIELD_Y_OFFSET,
        )
    return compositor.output_texture


def _render_main_menu_hd(
    session: GameSession,
    compositor: HdCompositor,
    resolve_palette: _PaletteResolver,
) -> moderngl.Texture | None:
    bundle = session.asset_bundle
    if bundle is None:
        return None
    player = bundle.get_intro_player()
    ch_pic = bundle.get_picture("ch.piv")
    if player is None or player.menu_data is None or ch_pic is None:
        return None
    bold = bundle.get_cel(player.plan.font.file)
    sel = bundle.get_cel("sel.cel")
    base_list = list(ch_pic.palette)
    if len(base_list) < 32:
        base_list.extend((0, 0, 0) for _ in range(32 - len(base_list)))
    base = tuple(base_list[:32])
    live = resolve_palette(session, base)
    compositor.set_palettes(live, base)
    compositor.begin_scene()
    compositor.draw_picture("ch.piv", ch_pic.pixels[:200, :320])
    title = player.menu_data.title
    if bold is not None:
        text_buf = np.zeros((200, 320), dtype=np.uint8)
        _draw_title_text_and_banner(
            text_buf,
            bold,
            player.menu_data,
            session.selected_player_count,
            gore_on=session.gore_enabled,
        )
        compositor.draw_indexed_overlay(
            text_buf, x=0, y=PLAYFIELD_Y_OFFSET, cache_key="main_menu_text"
        )
    if sel is not None and len(sel.frames) > title.cursor_frame:
        cursor_y = title.cursor_y[session.menu_cursor % len(title.cursor_y)]
        compositor.draw_cel_frame(
            "sel.cel",
            sel.frames[title.cursor_frame],
            title.cursor_x,
            cursor_y + PLAYFIELD_Y_OFFSET,
        )
    return compositor.output_texture


def render_menu_hd(
    session: GameSession,
    compositor: HdCompositor,
    resolve_palette: _PaletteResolver,
) -> moderngl.Texture | None:
    """Render main menu (``ch.piv`` + ``sel.cel``) or knight-select portrait screen (§14.7)."""
    if session.knight_pick_active:
        return _render_knight_pick_hd(session, compositor, resolve_palette)
    return _render_main_menu_hd(session, compositor, resolve_palette)
