"""ModernGL 50 Hz Asset & Hitbox Inspector controller and window host (T4.3, T4.4)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import moderngl
import pygame

from harddaysengine.app.hd_host import HdHostPresenter
from harddaysengine.app.loop import (
    FIGHT_FRAME_DIVISOR_FAST,
    FIGHT_FRAME_DIVISOR_NORMAL,
    RefreshPacer,
)
from harddaysengine.app.window import WindowController
from harddaysengine.assets.arena import composite_arena_background, decode_arena
from harddaysengine.assets.cel import CelFrame, DecodedCel, decode_cel, flip_cel_frame
from harddaysengine.assets.extract import CEL_EXTENSIONS, PICTURE_EXTENSIONS, encode_rgba_png_bytes
from harddaysengine.assets.hd_pack import HdPack
from harddaysengine.assets.hd_pack_schema import HdAssetKind
from harddaysengine.assets.hit import HitFrame, HitSet, parse_collide_hit
from harddaysengine.assets.palette import default_combat_palette
from harddaysengine.assets.picture import (
    PICTURE_HEIGHT,
    PICTURE_WIDTH,
    DecodedPicture,
    decode_picture,
    decode_picture_pack,
)
from harddaysengine.render.hd_compositor import HdCompositor
from harddaysengine.render.overlay import build_frame_overlay
from harddaysengine.render.presentation import DisplaySettings
from harddaysengine.render.renderer import PLAYFIELD_Y_OFFSET, HardDaysRenderer
from harddaysengine.render.viewport import initial_window_size

if TYPE_CHECKING:
    import numpy as np
    import numpy.typing as npt

    from harddaysengine.assets.adf import DiskSet


@dataclass(slots=True, frozen=True)
class BackgroundItem:
    """Named 320x200 background (composited Lair arena or standalone picture)."""

    name: str
    picture: DecodedPicture


@dataclass(slots=True, frozen=True)
class SpriteSetItem:
    """Named cel sprite set with optional `collide.hit` weapon attack-point table."""

    name: str
    stem: str
    cel: DecodedCel
    hit_set: HitSet | None


def _add_lair_backgrounds(
    disk_set: DiskSet,
    test_pics: list[DecodedPicture],
    out: list[BackgroundItem],
) -> None:
    if len(test_pics) < 7:
        return
    for entry in disk_set.list_entries():
        if entry.norm_name.endswith(".t"):
            arena = decode_arena(entry.norm_name, disk_set.read_file(entry.norm_name))
            if arena.is_lair:
                comp = composite_arena_background(arena, test_pics)
                out.append(BackgroundItem(name=entry.name.strip(), picture=comp))


def _collect_backgrounds(disk_set: DiskSet) -> list[BackgroundItem]:
    backgrounds: list[BackgroundItem] = []
    test_pics = decode_picture_pack(disk_set.read_file("test")) if disk_set.has_file("test") else []
    _add_lair_backgrounds(disk_set, test_pics, backgrounds)

    for entry in disk_set.list_entries():
        norm = entry.norm_name
        if norm.endswith(PICTURE_EXTENSIONS) or norm == "mindscape":
            pic = decode_picture(disk_set.read_file(norm))
            backgrounds.append(BackgroundItem(name=entry.name.strip(), picture=pic))

    if not backgrounds and test_pics:
        backgrounds.append(BackgroundItem(name="test_0", picture=test_pics[0]))
    if not backgrounds:
        fallback = DecodedPicture(
            width=PICTURE_WIDTH,
            height=PICTURE_HEIGHT,
            planes=5,
            packed_size=0,
            raw_palette=tuple([0] * 32),
            palette=default_combat_palette(),
            pixels=_blank_playfield(),
            raw_planes=b"\x00" * (5 * 8000),
        )
        backgrounds.append(BackgroundItem(name="default", picture=fallback))
    return backgrounds


def _blank_playfield() -> npt.NDArray[np.uint8]:
    import numpy as np

    return np.zeros((PICTURE_HEIGHT, PICTURE_WIDTH), dtype=np.uint8)


def _collect_sprite_sets(disk_set: DiskSet) -> list[SpriteSetItem]:
    hit_sets: dict[str, HitSet] = {}
    if disk_set.has_file("collide.hit"):
        hit_sets = parse_collide_hit(disk_set.read_file("collide.hit"))

    combat_items: list[SpriteSetItem] = []
    other_items: list[SpriteSetItem] = []
    for entry in disk_set.list_entries():
        norm = entry.norm_name
        if not norm.endswith(CEL_EXTENSIONS):
            continue
        cel = decode_cel(disk_set.read_file(norm))
        if not any(f.meta.width > 0 and f.meta.height > 0 for f in cel.frames):
            continue
        stem = Path(norm).stem.lower()
        hit = hit_sets.get(stem)
        item = SpriteSetItem(name=entry.name.strip(), stem=stem, cel=cel, hit_set=hit)
        if hit is not None or stem.startswith(
            ("kn", "troll", "dragon", "balok", "demon", "trogg", "ratmen", "mudmen")
        ):
            combat_items.append(item)
        else:
            other_items.append(item)
    return combat_items + other_items


class InspectorState:
    """Deterministic state machine for the 50 Hz Asset & Hitbox Inspector (T4.3)."""

    def __init__(
        self,
        backgrounds: list[BackgroundItem],
        sprites: list[SpriteSetItem],
    ) -> None:
        if not backgrounds:
            msg = "InspectorState requires at least one background"
            raise ValueError(msg)
        if not sprites:
            msg = "InspectorState requires at least one sprite set"
            raise ValueError(msg)
        self.backgrounds = backgrounds
        self.sprites = sprites
        self.bg_index: int = 0
        self.cel_index: int = 0
        self.frame_index: int = 0
        self.playing: bool = True
        self.show_overlay: bool = True
        self.flipped: bool = False
        self.scanlines: bool = True
        self.fast_mode: bool = False
        self.vbl_counter: int = 0
        self._snap_to_nonempty_frame(1)

    @property
    def current_background(self) -> BackgroundItem:
        """Currently selected Lair or picture background."""
        return self.backgrounds[self.bg_index]

    @property
    def current_sprite_set(self) -> SpriteSetItem:
        """Currently selected cel sprite set."""
        return self.sprites[self.cel_index]

    @property
    def current_frame(self) -> CelFrame:
        """Current cel frame (horizontally mirrored if `self.flipped` is True)."""
        raw_frame = self.current_sprite_set.cel.frames[self.frame_index]
        return flip_cel_frame(raw_frame) if self.flipped else raw_frame

    @property
    def current_hit_frame(self) -> HitFrame | None:
        """Weapon attack-point record for the current frame, if registered in `collide.hit`."""
        hs = self.current_sprite_set.hit_set
        if hs is None or self.frame_index >= len(hs.frames):
            return None
        return hs.frames[self.frame_index]

    def _snap_to_nonempty_frame(self, direction: int) -> None:
        frames = self.current_sprite_set.cel.frames
        total = len(frames)
        for _ in range(total):
            f = frames[self.frame_index]
            if f.meta.width > 0 and f.meta.height > 0:
                return
            self.frame_index = (self.frame_index + direction) % total

    def next_background(self) -> None:
        """Select the next Lair/arena background."""
        self.bg_index = (self.bg_index + 1) % len(self.backgrounds)

    def prev_background(self) -> None:
        """Select the previous Lair/arena background."""
        self.bg_index = (self.bg_index - 1) % len(self.backgrounds)

    def next_cel(self) -> None:
        """Select the next Knight/Monster cel set."""
        self.cel_index = (self.cel_index + 1) % len(self.sprites)
        self.frame_index = 0
        self._snap_to_nonempty_frame(1)

    def prev_cel(self) -> None:
        """Select the previous Knight/Monster cel set."""
        self.cel_index = (self.cel_index - 1) % len(self.sprites)
        self.frame_index = 0
        self._snap_to_nonempty_frame(1)

    def next_frame(self) -> None:
        """Advance to the next non-empty cel frame."""
        total = len(self.current_sprite_set.cel.frames)
        self.frame_index = (self.frame_index + 1) % total
        self._snap_to_nonempty_frame(1)

    def prev_frame(self) -> None:
        """Step back to the previous non-empty cel frame."""
        total = len(self.current_sprite_set.cel.frames)
        self.frame_index = (self.frame_index - 1) % total
        self._snap_to_nonempty_frame(-1)

    def toggle_play(self) -> None:
        """Toggle 50 Hz animation playback."""
        self.playing = not self.playing

    def toggle_overlay(self) -> None:
        """Toggle the green/red hurtbox and hitbox wireframe overlay."""
        self.show_overlay = not self.show_overlay

    def toggle_flip(self) -> None:
        """Toggle horizontal sprite mirroring (`mirrorCelHeader` / `mirrorCelPlanes`)."""
        self.flipped = not self.flipped

    def toggle_scanlines(self) -> None:
        """Toggle CRT scanline post-processing."""
        self.scanlines = not self.scanlines

    def toggle_speed(self) -> None:
        """Toggle between normal (6 VBL) and fast (5 VBL) combat animation speed."""
        self.fast_mode = not self.fast_mode

    def select_cel_by_name(self, name: str) -> bool:
        """Select a sprite set by case-insensitive name or stem."""
        target = Path(name.strip()).stem.lower()
        for idx, item in enumerate(self.sprites):
            if item.stem == target or item.name.lower() == name.strip().lower():
                self.cel_index = idx
                self.frame_index = 0
                self._snap_to_nonempty_frame(1)
                return True
        return False

    def select_background_by_name(self, name: str) -> bool:
        """Select a background by case-insensitive name or stem."""
        target = Path(name.strip()).stem.lower()
        for idx, item in enumerate(self.backgrounds):
            if Path(item.name).stem.lower() == target or item.name.lower() == name.strip().lower():
                self.bg_index = idx
                return True
        return False

    def tick_vbl(self) -> None:
        """Advance one 50 Hz PAL VBL tick."""
        self.vbl_counter += 1
        if not self.playing:
            return
        divisor = FIGHT_FRAME_DIVISOR_FAST if self.fast_mode else FIGHT_FRAME_DIVISOR_NORMAL
        if self.vbl_counter % divisor == 0:
            self.next_frame()

    def status_title(self, *, hd_mode: str | None = None) -> str:
        """Format a concise HUD title string describing the active asset, frame, and hitboxes."""
        bg = self.current_background.name
        sp = self.current_sprite_set
        fr = self.current_frame
        hf = self.current_hit_frame
        pts = hf.count if hf is not None else 0
        ov = "ON" if self.show_overlay else "OFF"
        fl = "FLIP" if self.flipped else "NORM"
        suffix = f" | Mode: {hd_mode}" if hd_mode is not None else ""
        return (
            f"HardDaysEngine Inspector | BG: {bg} | Cel: {sp.name} "
            f"[{self.frame_index + 1}/{sp.cel.frame_count}] "
            f"{fr.meta.width}x{fr.meta.height} ({fl}) | "
            f"HitPts: {pts} | Overlay: {ov}{suffix}"
        )


def render_inspector_frame_hd(
    state: InspectorState,
    renderer: HardDaysRenderer,
    compositor: HdCompositor,
) -> moderngl.Texture:
    """Render the active inspector background, cel, and scaled hitbox overlay in HD (§14.7)."""
    bg_item = state.current_background
    bg_kind = HdAssetKind.ARENA if bg_item.name.casefold().endswith(".t") else HdAssetKind.PICTURE
    compositor.set_palettes(bg_item.picture.palette, bg_item.picture.palette)
    compositor.begin_scene()
    compositor.draw_picture(bg_item.name, bg_item.picture.pixels, kind=bg_kind)

    sp = state.current_sprite_set
    raw_frame = sp.cel.frames[state.frame_index]
    sprite_frame = state.current_frame
    if sprite_frame.meta.width > 0 and sprite_frame.meta.height > 0:
        draw_x = 160 - (sprite_frame.meta.width // 2) - sprite_frame.meta.hot_x
        draw_y = PLAYFIELD_Y_OFFSET + 100 - (sprite_frame.meta.height // 2)
        compositor.draw_cel_frame(
            sp.name,
            raw_frame,
            draw_x,
            draw_y,
            mirrored=state.flipped,
            clip_rect=None,
        )
        if state.show_overlay:
            lines = build_frame_overlay(draw_x, draw_y, sprite_frame, state.current_hit_frame)
            compositor.output_fbo.use()
            renderer.ctx.viewport = (0, 0, compositor.canvas_size[0], compositor.canvas_size[1])
            renderer.draw_overlay_lines(lines)
    return compositor.output_texture


def build_inspector_state(disk_set: DiskSet) -> InspectorState:
    """Construct an `InspectorState` populated with Lair backgrounds and cels from `disk_set`."""
    backgrounds = _collect_backgrounds(disk_set)
    sprites = _collect_sprite_sets(disk_set)
    state = InspectorState(backgrounds=backgrounds, sprites=sprites)
    state.select_cel_by_name("kn4")
    return state


def run_inspector_headless(
    disk_set: DiskSet,
    *,
    frames: int = 12,
    cel_name: str | None = None,
    background_name: str | None = None,
    screenshot_path: Path | None = None,
    hd_pack: HdPack | None = None,
) -> npt.NDArray[np.uint8]:
    """Run the ModernGL inspector headlessly on a standalone OpenGL 3.3 context and return RGBA."""
    state = build_inspector_state(disk_set)
    if cel_name is not None:
        state.select_cel_by_name(cel_name)
    if background_name is not None:
        state.select_background_by_name(background_name)

    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    compositor = HdCompositor(ctx, hd_pack) if hd_pack is not None else None
    try:
        bg = state.current_background.picture
        renderer.upload_background(bg.pixels, bg.palette)
        for _ in range(max(1, frames)):
            if compositor is not None:
                render_inspector_frame_hd(state, renderer, compositor)
            else:
                renderer.render_to_canvas(
                    state.current_frame,
                    hit_frame=state.current_hit_frame,
                    show_overlay=state.show_overlay,
                )
            state.tick_vbl()
        rgba = (
            compositor.read_canvas_rgba() if compositor is not None else renderer.read_canvas_rgba()
        )
        if screenshot_path is not None:
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            screenshot_path.write_bytes(encode_rgba_png_bytes(rgba))
        return rgba
    finally:
        if compositor is not None:
            compositor.release()
        renderer.release()
        ctx.release()


def _step_frame(state: InspectorState, forward: bool) -> None:
    state.playing = False
    if forward:
        state.next_frame()
    else:
        state.prev_frame()


def _cycle_bg(state: InspectorState, renderer: HardDaysRenderer, forward: bool) -> None:
    if forward:
        state.next_background()
    else:
        state.prev_background()
    bg = state.current_background.picture
    renderer.upload_background(bg.pixels, bg.palette)


def _handle_keydown(key: int, state: InspectorState, renderer: HardDaysRenderer) -> bool:
    if key in (pygame.K_ESCAPE, pygame.K_q):
        return False
    bg_keys = {pygame.K_b: True, pygame.K_PAGEDOWN: True, pygame.K_PAGEUP: False}
    if key in bg_keys:
        _cycle_bg(state, renderer, bg_keys[key])
        return True
    actions = {
        pygame.K_RIGHT: lambda: _step_frame(state, True),
        pygame.K_LEFT: lambda: _step_frame(state, False),
        pygame.K_UP: state.prev_cel,
        pygame.K_DOWN: state.next_cel,
        pygame.K_SPACE: state.toggle_play,
        pygame.K_h: state.toggle_overlay,
        pygame.K_TAB: state.toggle_overlay,
        pygame.K_f: state.toggle_flip,
        pygame.K_m: state.toggle_flip,
        pygame.K_c: state.toggle_scanlines,
        pygame.K_s: state.toggle_speed,
    }
    action = actions.get(key)
    if action is not None:
        action()
    return True


def _pump_events(
    state: InspectorState, renderer: HardDaysRenderer, window: WindowController
) -> bool:
    for event in pygame.event.get():
        prev_preset = window.filter_preset
        if window.consume_event(event):
            if window.filter_preset is not prev_preset:
                state.scanlines = window.filter_preset.value == "scanlines"
            continue
        if event.type == pygame.QUIT:
            return False
        if event.type == pygame.KEYDOWN and not _handle_keydown(event.key, state, renderer):
            return False
    return True


def _prepare_inspector_state(
    disk_set: DiskSet,
    display: DisplaySettings | None,
    cel_name: str | None,
    background_name: str | None,
) -> InspectorState:
    state = build_inspector_state(disk_set)
    if display is not None:
        state.scanlines = display.scanlines
    if cel_name is not None:
        state.select_cel_by_name(cel_name)
    if background_name is not None:
        state.select_background_by_name(background_name)
    return state


def _present_inspector_frame(
    state: InspectorState,
    renderer: HardDaysRenderer,
    settings: DisplaySettings,
    hd_host: HdHostPresenter | None = None,
) -> None:
    cur_w, cur_h = pygame.display.get_window_size()
    hd_tex: moderngl.Texture | None = None
    hd_scale = 1
    mode_label: str | None = None
    if hd_host is not None and hd_host.pack is not None:
        compositor = hd_host.ensure_active(renderer.ctx, enabled=settings.hd_enabled)
        if compositor is not None:
            hd_tex = render_inspector_frame_hd(state, renderer, compositor)
            hd_scale = hd_host.scale
            mode_label = "HD"
        else:
            mode_label = "Classic"
    if hd_tex is None:
        renderer.render_to_canvas(
            state.current_frame,
            hit_frame=state.current_hit_frame,
            show_overlay=state.show_overlay,
        )
    if cur_w > 0 and cur_h > 0:
        renderer.present_to_screen(
            (0, 0, cur_w, cur_h),
            settings=settings,
            scanlines=state.scanlines,
            hd_tex=hd_tex,
            hd_scale=hd_scale,
        )
    pygame.display.set_caption(state.status_title(hd_mode=mode_label))
    pygame.display.flip()


def _build_inspector_hd_host(settings: DisplaySettings, hd_pack: HdPack | None) -> HdHostPresenter:
    loaded = hd_pack
    if loaded is None and settings.hd_pack_dir is not None:
        loaded = HdPack.load(settings.hd_pack_dir)
    return HdHostPresenter(loaded)


def run_inspector_window(
    disk_set: DiskSet,
    *,
    scale: int = 3,
    display: DisplaySettings | None = None,
    cel_name: str | None = None,
    background_name: str | None = None,
    max_frames: int | None = None,
    hd_pack: HdPack | None = None,
) -> int:
    """Open the interactive pygame-ce + ModernGL 50 Hz Asset & Hitbox Inspector window."""
    settings = display if display is not None else DisplaySettings(scale=max(1, scale))
    hd_host = _build_inspector_hd_host(settings, hd_pack)
    state = _prepare_inspector_state(disk_set, display, cel_name, background_name)

    pygame.init()
    pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 3)
    pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 3)
    pygame.display.gl_set_attribute(pygame.GL_CONTEXT_PROFILE_MASK, pygame.GL_CONTEXT_PROFILE_CORE)
    window = WindowController(
        initial_window_size(settings),
        settings.fullscreen,
        initial_filter=settings.filter,
        initial_crt=settings.crt,
        initial_hd_available=hd_host.pack is not None,
        initial_hd_enabled=settings.hd_enabled,
        vsync=settings.vsync,
    )
    window.open()

    ctx = moderngl.create_context(require=330)
    renderer = HardDaysRenderer(ctx)
    bg = state.current_background.picture
    renderer.upload_background(bg.pixels, bg.palette)

    pacer = RefreshPacer(refresh_hz=window.get_refresh_rate(), vsync=settings.vsync)
    prev_ns = time.perf_counter_ns()
    rendered_frames = 0
    try:
        running = True
        while running:
            prev_mode = window.mode
            running = _pump_events(state, renderer, window)
            if window.mode is not prev_mode:
                pacer.set_refresh_rate(window.get_refresh_rate())
            now_ns = time.perf_counter_ns()
            step = pacer.step(now_ns - prev_ns)
            prev_ns = now_ns
            for _ in range(step.ticks):
                state.tick_vbl()

            if step.should_present:
                _present_inspector_frame(
                    state, renderer, window.apply_display_settings(settings), hd_host
                )
                rendered_frames += 1
                if max_frames is not None and rendered_frames >= max_frames:
                    break
            elif not settings.vsync:
                pygame.time.wait(1)
    finally:
        hd_host.release()
        renderer.release()
        pygame.quit()
    return 0
