"""Standalone ModernGL OpenGL 3.3 tests for 2.5D scene renderer & HUD (T8.2)."""

from __future__ import annotations

import moderngl
import numpy as np
import pytest

from pymoonstone.assets.cel import CelFrame, CelFrameMeta, DecodedCel
from pymoonstone.assets.palette import default_combat_palette
from pymoonstone.render.renderer import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    PLAYFIELD_Y_OFFSET,
    MoonstoneRenderer,
)
from pymoonstone.render.scene import (
    LAYER_ACTOR,
    LAYER_CORPSE,
    LAYER_FOREGROUND,
    LAYER_PROJECTILE,
    LAYER_SHADOW,
    BitmapFont,
    CombatHudState,
    OverworldHudState,
    SceneRenderer,
    SceneSprite,
    apply_palette_fx,
    default_bitmap_font,
    render_combat_hud,
    render_overworld_hud,
    sort_sprites_painter,
)
from pymoonstone.sim.palette_fx import PaletteFxState

pytestmark = pytest.mark.gl


def _make_solid_frame(width: int, height: int, index: int, *, hot_x: int = 0) -> CelFrame:
    pixels = np.full((height, width), index, dtype=np.uint8)
    mask = pixels != 0
    meta = CelFrameMeta(
        index=0,
        data_offset=0,
        width=width,
        height=height,
        hot_and_flags=((hot_x & 0x0F) << 4) | 0x01,
        plane_bits=0x1F,
    )
    return CelFrame(meta=meta, pixels=pixels, mask=mask)


def _make_asymmetric_frame() -> CelFrame:
    pixels = np.zeros((8, 8), dtype=np.uint8)
    pixels[:, 0:2] = 6  # Left side: knight red
    pixels[:, 6:8] = 20  # Right side: green
    meta = CelFrameMeta(
        index=0,
        data_offset=0,
        width=8,
        height=8,
        hot_and_flags=0x01,
        plane_bits=0x1F,
    )
    return CelFrame(meta=meta, pixels=pixels, mask=pixels != 0)


def test_sort_sprites_painter_depth_and_layers() -> None:
    dummy = _make_solid_frame(4, 4, 1)
    fg = SceneSprite(frame=dummy, x=100, y=80, layer=LAYER_FOREGROUND, order=0)
    proj = SceneSprite(frame=dummy, x=100, y=80, layer=LAYER_PROJECTILE, order=0)
    actor_b = SceneSprite(frame=dummy, x=100, y=80, layer=LAYER_ACTOR, order=2)
    actor_a = SceneSprite(frame=dummy, x=100, y=80, layer=LAYER_ACTOR, order=1)
    corpse = SceneSprite(frame=dummy, x=100, y=80, layer=LAYER_CORPSE, order=0)
    shadow = SceneSprite(frame=dummy, x=100, y=80, layer=LAYER_SHADOW, order=0)
    far = SceneSprite(frame=dummy, x=100, y=40, layer=LAYER_FOREGROUND, order=0)
    near = SceneSprite(frame=dummy, x=100, y=120, layer=LAYER_SHADOW, order=0)

    ordered = sort_sprites_painter([near, fg, actor_b, proj, corpse, far, actor_a, shadow])
    assert ordered == [far, shadow, corpse, actor_a, actor_b, proj, fg, near]


def test_scene_painter_overlap_altitude_and_gl_output() -> None:
    ctx = moderngl.create_standalone_context(require=330)
    renderer = MoonstoneRenderer(ctx)
    scene = SceneRenderer(renderer)
    try:
        palette = default_combat_palette()
        scene.set_palette(palette)

        bg = np.full((200, 320), 1, dtype=np.uint8)
        far_sprite = SceneSprite(frame=_make_solid_frame(16, 16, 6), x=100, y=80)
        near_sprite = SceneSprite(frame=_make_solid_frame(16, 16, 20), x=100, y=90, z=10)

        ordered = scene.compose_scene(background=bg, sprites=[near_sprite, far_sprite])
        assert ordered == [far_sprite, near_sprite]
        scene.flush_to_fbo()

        rgba = renderer.read_canvas_rgba()
        assert rgba.shape == (CANVAS_HEIGHT, CANVAS_WIDTH, 4)

        # Near sprite has y=90, z=10 -> y - z = 80, so it overlaps far sprite at (100, 28 + 80)
        # and must win because its ground depth y=90 > 80.
        center_px = tuple(int(c) for c in rgba[PLAYFIELD_Y_OFFSET + 80, 100, :3])
        assert center_px == palette[20]

        # Top/bottom 28px borders remain untouched (index 0 -> black)
        assert tuple(int(c) for c in rgba[0, 100, :3]) == palette[0]
        assert tuple(int(c) for c in rgba[255, 100, :3]) == palette[0]
    finally:
        scene.release()
        renderer.release()
        ctx.release()


def test_scene_mirrored_sprite_and_palette_fx_bridge() -> None:
    ctx = moderngl.create_standalone_context(require=330)
    renderer = MoonstoneRenderer(ctx)
    scene = SceneRenderer(renderer)
    try:
        asym = _make_asymmetric_frame()
        unflipped = SceneSprite(frame=asym, x=80, y=100, mirrored=False)
        mirrored = SceneSprite(frame=asym, x=160, y=100, mirrored=True)

        scene.compose_scene(sprites=[unflipped, mirrored])

        # Build a $0RGB palette via sim/palette_fx with distinct index 6 (blue) and 20 (yellow)
        words = [0x000] * 32
        words[6] = 0x00F  # pure blue
        words[20] = 0xFF0  # pure yellow
        fx = PaletteFxState(words)
        rgb = apply_palette_fx(renderer, fx.current_palette())
        scene.set_palette(rgb)
        scene.flush_to_fbo()

        rgba = renderer.read_canvas_rgba()
        # Unflipped at x=80 -> draw_x=76: left [76..78) is index 6 (blue), right [82..84) is 20
        assert tuple(int(c) for c in rgba[PLAYFIELD_Y_OFFSET + 100, 76, :3]) == (0, 0, 255)
        assert tuple(int(c) for c in rgba[PLAYFIELD_Y_OFFSET + 100, 83, :3]) == (255, 255, 0)

        # Mirrored at x=160 -> draw_x=156: left [156..158) is 20 (yellow), right [162..164) is 6
        assert tuple(int(c) for c in rgba[PLAYFIELD_Y_OFFSET + 100, 156, :3]) == (255, 255, 0)
        assert tuple(int(c) for c in rgba[PLAYFIELD_Y_OFFSET + 100, 163, :3]) == (0, 0, 255)
    finally:
        scene.release()
        renderer.release()
        ctx.release()


def test_bitmap_font_cel_loaders_and_hud_rendering_gl() -> None:
    ctx = moderngl.create_standalone_context(require=330)
    renderer = MoonstoneRenderer(ctx)
    scene = SceneRenderer(renderer)
    try:
        palette = default_combat_palette()
        scene.set_palette(palette)

        # Test multi-frame .font cel loader and single-frame .f strip cel loader
        glyph_a = _make_solid_frame(8, 8, 31)
        multi_cel = DecodedCel(
            frame_count=2,
            packed_size=0,
            unpacked_bits=0,
            frames=(_make_solid_frame(8, 8, 0), glyph_a),
            raw_planes=b"",
        )
        font_multi = BitmapFont.from_cel(multi_cel, base_char=ord(" "))
        assert int(font_multi.glyphs[ord("!")].max()) == 31

        strip_frame = _make_solid_frame(16, 8, 15)
        strip_cel = DecodedCel(
            frame_count=1,
            packed_size=0,
            unpacked_bits=0,
            frames=(strip_frame,),
            raw_planes=b"",
        )
        font_strip = BitmapFont.from_cel(strip_cel, base_char=ord("A"))
        assert int(font_strip.glyphs[ord("B")].max()) == 15

        # Render combat HUD in top/bottom 28px borders
        font = default_bitmap_font()
        scene.compose_scene()
        combat_hud = CombatHudState(
            hp=75,
            max_hp=100,
            stamina=40,
            max_stamina=100,
            lives=3,
            score=1250,
            knight_name="SIRAGNER",
            enemy_name="TROGG",
            enemy_hp=50,
            enemy_max_hp=100,
        )
        render_combat_hud(scene.canvas, combat_hud, font)
        scene.flush_to_fbo()

        rgba_combat = renderer.read_canvas_rgba()
        # Top border has text/bars, bottom border has LIVES/SCORE text
        assert int(rgba_combat[:PLAYFIELD_Y_OFFSET, :, :3].max()) > 0
        assert int(rgba_combat[PLAYFIELD_Y_OFFSET + 200 :, :, :3].max()) > 0
        # HP bar fill at (x=35, y=18) should be palette index 8
        assert tuple(int(c) for c in rgba_combat[18, 35, :3]) == palette[8]

        # Render overworld HUD
        scene.compose_scene()
        overworld_hud = OverworldHudState(
            knight_name="GALAHAD",
            hp=90,
            max_hp=100,
            gold=240,
            lives=2,
            moon_phase=14,
            turn=7,
            score=4800,
        )
        render_overworld_hud(scene.canvas, overworld_hud, font)
        scene.flush_to_fbo()

        rgba_overworld = renderer.read_canvas_rgba()
        assert int(rgba_overworld[:PLAYFIELD_Y_OFFSET, :, :3].max()) > 0
        assert int(rgba_overworld[PLAYFIELD_Y_OFFSET + 200 :, :, :3].max()) > 0
    finally:
        scene.release()
        renderer.release()
        ctx.release()
