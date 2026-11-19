"""Clean-room Amiga ``program`` intro player and ``mog`` title/select renderer (§7.4, §12.42).

All scene steps, loader progress screens, script offsets, wipe tables, palette tweaks and menu
layouts are decoded from the user's own ``program`` and ``mog`` executables at runtime via
:mod:`harddaysengine.assets.intro_data` and executed on :class:`~harddaysengine.sim.intro.IntroRun`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import numpy as np

from harddaysengine.assets.arena import decode_stile
from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.hunk import HunkFormatError, parse_hunk_executable
from harddaysengine.assets.intro_data import parse_intro_plan, parse_menu_data
from harddaysengine.assets.palette import RGBColor, default_combat_palette
from harddaysengine.render.renderer import CANVAS_WIDTH, PLAYFIELD_HEIGHT
from harddaysengine.render.scene import blit_indexed_sprite, palette_fx_to_rgb
from harddaysengine.sim.ending import SCREEN, WORK, EndingInputs
from harddaysengine.sim.intro import IntroRun

if TYPE_CHECKING:
    from collections.abc import Sequence

    import numpy.typing as npt

    from harddaysengine.app.scene_assets import GameAssetBundle
    from harddaysengine.assets.cel import DecodedCel
    from harddaysengine.assets.picture import DecodedPicture
    from harddaysengine.sim.intro_types import IntroPlan, MogMenuData
    from harddaysengine.sim.turn_rules import TextLine

_BUFFERS = 10
_PLAYFIELD_CLIP: tuple[int, int, int, int] = (0, 0, CANVAS_WIDTH, PLAYFIELD_HEIGHT)


def _blank() -> npt.NDArray[np.uint8]:
    return np.zeros((PLAYFIELD_HEIGHT, CANVAS_WIDTH), dtype=np.uint8)


def _screen_pixels(picture: DecodedPicture) -> npt.NDArray[np.uint8]:
    return picture.pixels[:PLAYFIELD_HEIGHT, :CANVAS_WIDTH].copy()


def _pad_palette_32(palette: Sequence[RGBColor]) -> tuple[RGBColor, ...]:
    out = list(palette[:32])
    while len(out) < 32:
        out.append((0, 0, 0))
    return tuple(out)


def assemble_stile_panorama(
    stile_words: Sequence[int],
    pictures: tuple[npt.NDArray[np.uint8], npt.NDArray[np.uint8], npt.NDArray[np.uint8]],
) -> npt.NDArray[np.uint8]:
    """Assemble the 320x1200 vertical panorama from a ``.stile`` map and 3 ``.piv`` atlases."""
    panorama = np.zeros((1200, CANVAS_WIDTH), dtype=np.uint8)
    for idx, code in enumerate(stile_words[:480]):
        dst_r, dst_c = divmod(idx, 10)
        pic_idx, tile_idx = divmod(code, 80)
        if not (0 <= pic_idx < 3):
            continue
        src_r, src_c = divmod(tile_idx, 10)
        src_pic = pictures[pic_idx]
        y0, x0 = dst_r * 25, dst_c * 32
        sy0, sx0 = src_r * 25, src_c * 32
        panorama[y0 : y0 + 25, x0 : x0 + 32] = src_pic[sy0 : sy0 + 25, sx0 : sx0 + 32]
    return panorama


class _NullCanvas:
    """Pixel-free canvas used once at load time to count the exact VBL duration of the plan."""

    def flip(self) -> None:
        return

    def copy(self, source: int, target: int) -> None:
        _ = source, target

    def clear(self, target: int) -> None:
        _ = target

    def draw(self, target: int, slot: int, frame: int, x: int, y: int, mirrored: bool) -> None:
        _ = target, slot, frame, x, y, mirrored

    def overlay(self, frame: int, x: int, y: int) -> None:
        _ = frame, x, y

    def text(self, target: int, lines: tuple[TextLine, ...]) -> None:
        _ = target, lines

    def message(self, target: int) -> None:
        _ = target

    def wipe(self, target: int, progress: int) -> None:
        _ = target, progress

    def mindscape(self, target: int) -> None:
        _ = target

    def font_cel(self, target: int, frame: int, x: int, y: int) -> None:
        _ = target, frame, x, y


def _picture(bundle: GameAssetBundle, name: str) -> DecodedPicture:
    picture = bundle.get_picture(name)
    if picture is None:
        raise ValueError(f"the intro needs the original picture {name}")
    return picture


def _cel(bundle: GameAssetBundle, name: str) -> DecodedCel:
    cel = bundle.get_cel(name)
    if cel is None:
        raise ValueError(f"the intro needs the original cel {name}")
    return cel


class IntroPixels:
    """Picture buffers and the two screens of the ``program`` intro pass (§12.42)."""

    def __init__(self, bundle: GameAssetBundle, plan: IntroPlan) -> None:
        self.plan = plan
        self.buffers = [_blank() for _ in range(_BUFFERS)]
        self.screens = [_blank(), _blank()]
        self.displayed = 0
        self.mindscape_picture = _picture(bundle, plan.load.mindscape)
        self.message_picture = _picture(bundle, plan.message)
        self._initial_buffers: dict[int, npt.NDArray[np.uint8]] = {}
        for load in plan.load.pictures:
            pixels = _screen_pixels(_picture(bundle, load.name))
            self.buffers[load.buffer] = pixels
            self._initial_buffers[int(load.buffer)] = pixels.copy()
        self.cels = {slot: _cel(bundle, name) for slot, name in plan.load.cels}
        self.overlay_cel = _cel(bundle, plan.load.overlay)
        self.font = _cel(bundle, plan.font.file)
        words = decode_stile(bundle.disk_set.read_file(plan.load.stile))
        self.panorama = assemble_stile_panorama(
            words,
            (self.buffers[2].copy(), self.buffers[3].copy(), self.buffers[4].copy()),
        )

    def reset(self) -> None:
        """Reset all buffers and screens to their initial post-load state."""
        for index in range(_BUFFERS):
            if index in self._initial_buffers:
                self.buffers[index][:] = self._initial_buffers[index]
            else:
                self.buffers[index][:] = 0
        self.screens[0][:] = 0
        self.screens[1][:] = 0
        self.displayed = 0

    def _buffer(self, index: int) -> npt.NDArray[np.uint8]:
        if index == SCREEN:
            return self.screens[self.displayed]
        if index == WORK:
            return self.screens[1 - self.displayed]
        return self.buffers[index]

    def displayed_pixels(self) -> npt.NDArray[np.uint8]:
        return self.screens[self.displayed]

    def flip(self) -> None:
        self.displayed = 1 - self.displayed

    def copy(self, source: int, target: int) -> None:
        self._buffer(target)[:] = self._buffer(source)

    def clear(self, target: int) -> None:
        self._buffer(target)[:] = 0

    def draw(self, target: int, slot: int, frame: int, x: int, y: int, mirrored: bool) -> None:
        pixels = self.cels[slot].frames[frame].pixels
        blit_indexed_sprite(
            self._buffer(target),
            pixels,
            x,
            y,
            mirrored=mirrored,
            clip_rect=_PLAYFIELD_CLIP,
        )

    def overlay(self, frame: int, x: int, y: int) -> None:
        pixels = self.overlay_cel.frames[frame].pixels
        blit_indexed_sprite(self._buffer(WORK), pixels, x, y, clip_rect=_PLAYFIELD_CLIP)

    def message(self, target: int) -> None:
        self._buffer(target)[:] = _screen_pixels(self.message_picture)

    def mindscape(self, target: int) -> None:
        self._buffer(target)[:] = _screen_pixels(self.mindscape_picture)

    def font_cel(self, target: int, frame: int, x: int, y: int) -> None:
        pixels = self.font.frames[frame].pixels
        blit_indexed_sprite(self._buffer(target), pixels, x, y, clip_rect=_PLAYFIELD_CLIP)

    def wipe(self, target: int, progress: int) -> None:
        window = self.panorama[progress : progress + PLAYFIELD_HEIGHT]
        self._buffer(target)[: window.shape[0]] = window

    def text(self, target: int, lines: tuple[TextLine, ...]) -> None:
        """Draw ``lines`` using ``program``'s decoded font and charmap (§12.39.7, §12.42.3)."""
        font, data = self.plan.font, self.plan.scripts.data
        buffer = self._buffer(target)
        for line in lines:
            if line.style & ~3:
                raise ValueError(f"intro text style {line.style} is not used by program")
            glyphs = [self.font.frames[data[font.charmap + ord(ch) - 0x20]] for ch in line.text]
            width = sum(glyph.meta.width - font.bias for glyph in glyphs)
            x = ((font.width - width) & 0xFFFF) >> 1 if line.style & 1 else line.x
            for glyph in glyphs:
                blit_indexed_sprite(buffer, glyph.pixels, x, line.y, clip_rect=_PLAYFIELD_CLIP)
                x += glyph.meta.width - font.bias


def _draw_mog_bold_line(
    buf: npt.NDArray[np.uint8],
    font: DecodedCel,
    char_map: bytes,
    line: TextLine,
    span: int,
) -> None:
    """Draw one ``mog`` bold-font text line onto ``buf`` (§11.18/19, §12.42.6)."""
    bias = 3 if line.style & 8 else 0
    glyphs = [font.frames[char_map[(ord(ch) - 32) & 255]] for ch in line.text]
    x = line.x
    if line.style & 5:
        width = sum(g.meta.width - bias for g in glyphs)
        x = (span - width) >> 1 if line.style & 1 else span - width
    for glyph in glyphs:
        blit_indexed_sprite(buf, glyph.pixels, x, line.y, clip_rect=_PLAYFIELD_CLIP)
        x += glyph.meta.width - bias


def _count_total_ticks(plan: IntroPlan, inputs: EndingInputs) -> int:
    probe = IntroRun(plan, inputs, _NullCanvas())
    while not probe.advance():
        pass
    return probe.tick


def _try_parse_mog_menu(bundle: GameAssetBundle) -> MogMenuData | None:
    if not bundle.disk_set.has_file("mog"):
        return None
    try:
        mog_exe = parse_hunk_executable("mog", bundle.disk_set.read_file("mog"))
        return parse_menu_data(mog_exe)
    except (HunkFormatError, MogVersionError):
        return None


def _draw_title_text_and_banner(
    buf: npt.NDArray[np.uint8],
    bold: DecodedCel,
    menu_data: MogMenuData,
    selected_player_count: int,
    *,
    gore_on: bool,
) -> None:
    title = menu_data.title
    if len(bold.frames) > title.banner.frame:
        blit_indexed_sprite(
            buf,
            bold.frames[title.banner.frame].pixels,
            title.banner.x,
            title.banner.y,
            clip_rect=_PLAYFIELD_CLIP,
        )
    gore_text = title.gore_on_text if gore_on else title.gore_off_text
    for idx, line in enumerate(title.lines):
        active = line
        if idx == title.players_line_index:
            active = replace(line, text=str(selected_player_count))
        elif idx == title.gore_line_index:
            active = replace(line, text=gore_text)
        _draw_mog_bold_line(buf, bold, menu_data.char_map, active, title.span)


@dataclass(slots=True)
class IntroCinematicPlayer:
    """Clean-room player for the decoded ``program`` intro and ``mog`` title/select screens."""

    plan: IntroPlan
    pixels: IntroPixels
    inputs: EndingInputs
    run: IntroRun
    menu_data: MogMenuData | None
    _total_ticks: int

    @property
    def panorama(self) -> npt.NDArray[np.uint8]:
        return self.pixels.panorama

    @property
    def credit_frames(self) -> tuple[tuple[TextLine, ...], ...]:
        return self.plan.load.progress.credits

    @classmethod
    def try_build(cls, bundle: GameAssetBundle) -> IntroCinematicPlayer | None:
        """Build an :class:`IntroCinematicPlayer` if Disk 1 intro assets exist in ``bundle``."""
        if not bundle.disk_set.has_file("program"):
            return None
        try:
            prog_exe = parse_hunk_executable("program", bundle.disk_set.read_file("program"))
            plan = parse_intro_plan(prog_exe)
        except (HunkFormatError, MogVersionError):
            return None

        required = (
            plan.load.mindscape,
            plan.message,
            plan.font.file,
            plan.load.stile,
            plan.load.overlay,
            *(p.name for p in plan.load.pictures),
            *(name for _, name in plan.load.cels),
        )
        if not all(bundle.disk_set.has_file(name) for name in required):
            return None

        pixels = IntroPixels(bundle, plan)
        palettes = {plan.load.mindscape: pixels.mindscape_picture.raw_palette}
        for load in plan.load.pictures:
            palettes[load.name] = _picture(bundle, load.name).raw_palette
        widths = {
            slot: tuple(frame.meta.width for frame in cel.frames)
            for slot, cel in pixels.cels.items()
        }
        inputs = EndingInputs(palettes, pixels.message_picture.raw_palette, widths)
        return cls(
            plan=plan,
            pixels=pixels,
            inputs=inputs,
            run=IntroRun(plan, inputs, pixels),
            menu_data=_try_parse_mog_menu(bundle),
            _total_ticks=_count_total_ticks(plan, inputs),
        )

    def total_ticks(self) -> int:
        """Return total 50 Hz VBL duration of the starting cinematic before main menu."""
        return self._total_ticks

    def is_finished(self, tick_count: int) -> bool:
        """Return ``True`` once ``tick_count`` reaches the end of the intro cinematic."""
        return tick_count >= self._total_ticks

    def render_tick(
        self,
        bundle: GameAssetBundle,
        tick_count: int,
    ) -> tuple[npt.NDArray[np.uint8], tuple[RGBColor, ...]]:
        """Return ``(playfield_pixels, palette)`` for ``tick_count`` at 50 Hz (§12.42)."""
        _ = bundle
        cycle_len = max(1, self._total_ticks)
        target_tick = min(max(0, tick_count), cycle_len - 1)
        if self.run.tick > target_tick + 1 or self.run.tick == 0:
            self.pixels.reset()
            self.run = IntroRun(self.plan, self.inputs, self.pixels)
        while self.run.tick <= target_tick and not self.run.done:
            self.run.advance()
        return self.pixels.displayed_pixels().copy(), palette_fx_to_rgb(self.run.palette.regs)

    def build_main_menu(
        self,
        bundle: GameAssetBundle,
        selected_player_count: int,
        menu_cursor: int = 0,
        *,
        gore_on: bool = True,
    ) -> tuple[npt.NDArray[np.uint8] | None, tuple[RGBColor, ...]]:
        """Render the original Amiga ``mog`` title menu on ``ch.piv`` (§12.42.6)."""
        ch_pic = bundle.get_picture("ch.piv")
        if ch_pic is None:
            return None, default_combat_palette()
        buf = _screen_pixels(ch_pic)
        if self.menu_data is None:
            return buf, _pad_palette_32(ch_pic.palette)
        title = self.menu_data.title
        bold = bundle.get_cel(self.plan.font.file)
        if bold is not None:
            _draw_title_text_and_banner(
                buf,
                bold,
                self.menu_data,
                selected_player_count,
                gore_on=gore_on,
            )
        sel = bundle.get_cel("sel.cel")
        if sel is not None and len(sel.frames) > title.cursor_frame:
            cursor_y = title.cursor_y[menu_cursor % len(title.cursor_y)]
            blit_indexed_sprite(
                buf,
                sel.frames[title.cursor_frame].pixels,
                title.cursor_x,
                cursor_y,
                clip_rect=_PLAYFIELD_CLIP,
            )
        return buf, _pad_palette_32(ch_pic.palette)

    def build_knight_select(
        self,
        bundle: GameAssetBundle,
        selected_slot: int,
        taken_mask: int = 0,
    ) -> tuple[npt.NDArray[np.uint8] | None, tuple[RGBColor, ...]]:
        """Render the original Amiga ``mog`` Select a Knight portrait screen (§12.42.6)."""
        if self.menu_data is None:
            return None, default_combat_palette()
        ks = self.menu_data.knight_select
        buf = _blank()
        bold = bundle.get_cel(self.plan.font.file)
        sel = bundle.get_cel("sel.cel")
        slot = selected_slot & 3
        if bold is not None:
            for line in ks.lines:
                _draw_mog_bold_line(buf, bold, self.menu_data.char_map, line, ks.span)
            name_line = replace(
                ks.lines[0],
                text=ks.knight_names[slot],
                x=ks.name_x,
                y=ks.name_y,
                style=ks.name_style,
            )
            _draw_mog_bold_line(buf, bold, self.menu_data.char_map, name_line, ks.span)
        if sel is not None and len(sel.frames) >= ks.portrait_first_frame + 4:
            for idx in range(4):
                if not (taken_mask & (1 << idx)):
                    blit_indexed_sprite(
                        buf,
                        sel.frames[ks.portrait_first_frame + idx].pixels,
                        ks.portrait_x[idx],
                        ks.portrait_y,
                        clip_rect=_PLAYFIELD_CLIP,
                    )
            blit_indexed_sprite(
                buf,
                sel.frames[ks.highlight_frame].pixels,
                ks.portrait_x[slot],
                ks.portrait_y,
                clip_rect=_PLAYFIELD_CLIP,
            )
        return buf, palette_fx_to_rgb(ks.palette)
