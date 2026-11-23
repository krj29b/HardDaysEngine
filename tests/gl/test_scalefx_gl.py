"""Standalone OpenGL 3.3 tests for the clean-room multi-pass ScaleFX preset (T18.3, §13.4.3)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import moderngl
import numpy as np
import numpy.typing as npt
import pytest

from harddaysengine.app.audio import AudioEngine
from harddaysengine.app.game import GameSession
from harddaysengine.app.input import InputFrame, PlayerInput
from harddaysengine.assets.adf import DiskSet
from harddaysengine.render.presentation import (
    AspectMode,
    CropMode,
    DisplaySettings,
    FilterPreset,
    ScaleMode,
)
from harddaysengine.render.renderer import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    PLAYFIELD_HEIGHT,
    PLAYFIELD_Y_OFFSET,
    HardDaysRenderer,
)
from harddaysengine.render.scene import SceneRenderer, default_bitmap_font
from harddaysengine.sim.types import KnightId

if TYPE_CHECKING:
    from pathlib import Path

    from harddaysengine.render.viewport import PresentationViewport

pytestmark = pytest.mark.gl


def _render_scalefx(
    ctx: moderngl.Context,
    renderer: HardDaysRenderer,
    window_size: tuple[int, int],
    settings: DisplaySettings,
) -> tuple[npt.NDArray[np.uint8], PresentationViewport]:
    win_w, win_h = window_size
    out_tex = ctx.texture((win_w, win_h), 4)
    out_fbo = ctx.framebuffer(color_attachments=[out_tex])
    try:
        pv = renderer.present_to_framebuffer(out_fbo, window_size, settings=settings)
        raw = out_fbo.read(components=4)
        arr = np.frombuffer(raw, dtype=np.uint8).reshape((win_h, win_w, 4))
        return np.ascontiguousarray(arr[::-1, :, :]), pv
    finally:
        out_fbo.release()
        out_tex.release()


def _pixel_block_3x(
    img: npt.NDArray[np.uint8],
    pv: PresentationViewport,
    src_x: int,
    src_y: int,
) -> npt.NDArray[np.uint8]:
    """Extract the 3x3 output block for source pixel ``(src_x, src_y)`` at exact 3x integer."""
    top_y = pv.window_height - (pv.dest_y + pv.dest_height)
    y0 = top_y + (src_y - pv.source.y) * 3
    x0 = pv.dest_x + (src_x - pv.source.x) * 3
    return img[y0 : y0 + 3, x0 : x0 + 3, :3]


def test_scalefx_resolves_45_2_to_1_and_3_to_1_slopes_while_keeping_edges_sharp() -> None:
    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    scene = SceneRenderer(renderer)
    try:
        palette = [(0, 0, 0)] * 32
        palette[1] = (240, 240, 240)
        scene.set_palette(palette)
        scene.canvas[:, :] = 0

        # 1. Filled 45-degree right triangle (hypotenuse: x + y == 120, y in 40..79)
        for y in range(40, 80):
            scene.canvas[y, 120 - y : 100] = 1

        # 2. Filled 2:1 shallow staircase (step left by 2 pixels per row, y in 120..139)
        for dy in range(20):
            y = 120 + dy
            scene.canvas[y, 180 - 2 * dy : 210] = 1

        # 3. Filled 3:1 shallow staircase (step left by 3 pixels per row, y in 170..189)
        for dy in range(20):
            y = 170 + dy
            scene.canvas[y, 260 - 3 * dy : 300] = 1

        # 4. Axis-aligned solid rectangle (straight H/V edges + flat interior)
        scene.canvas[160:190, 40:80] = 1
        scene.flush_to_fbo()

        settings = DisplaySettings(
            filter=FilterPreset.SCALEFX,
            scale_mode=ScaleMode.INTEGER,
            aspect=AspectMode.SQUARE,
            crop=CropMode.FULL,
        )
        img, pv = _render_scalefx(ctx, renderer, (960, 768), settings)
        assert renderer.post_chain.active_texture_count == 4
        assert [tex.size for tex in renderer.post_chain.stage_textures] == [
            (CANVAS_WIDTH, CANVAS_HEIGHT),
            (CANVAS_WIDTH, CANVAS_HEIGHT),
            (CANVAS_WIDTH, CANVAS_HEIGHT),
            (CANVAS_WIDTH * 3, CANVAS_HEIGHT * 3),
        ]

        # 45-degree edge: black (59, 60) has white F=(60,60), H=(59,61), I=(60,61)
        blk_45 = _pixel_block_3x(img, pv, 59, 60)[:, :, 0].astype(int)
        assert blk_45[1, 1] == 0
        assert blk_45[2, 2] == 240
        assert blk_45[2, 1] == 0
        assert blk_45[1, 2] == 0

        # 2:1 shallow staircase: row 130 white starts at x=160, row 131 starts at x=158
        blk_21 = _pixel_block_3x(img, pv, 159, 130)[:, :, 0].astype(int)
        assert blk_21[1, 1] == 0
        assert blk_21[2, 2] == 240
        assert blk_21[2, 1] == 240
        assert blk_21[2, 0] == 0
        # Paired white corner across step at (160, 130) takes top-left + top-mid
        blk_21_pair = _pixel_block_3x(img, pv, 160, 130)[:, :, 0].astype(int)
        assert blk_21_pair[1, 1] == 240
        assert blk_21_pair[0, 0] == 0
        assert blk_21_pair[0, 1] == 0
        assert blk_21_pair[0, 2] == 240

        # 3:1 shallow staircase: row 180 white starts at x=230, row 181 starts at x=227
        blk_31 = _pixel_block_3x(img, pv, 229, 180)[:, :, 0].astype(int)
        assert blk_31[1, 1] == 0
        assert blk_31[2, 2] == 240
        assert blk_31[2, 1] == 240
        assert blk_31[2, 0] == 240

        # Axis-aligned vertical edge and flat interior: exact original colour, zero blur
        assert np.all(_pixel_block_3x(img, pv, 39, 175)[:, :, 0] == 0)
        assert np.all(_pixel_block_3x(img, pv, 40, 175)[:, :, 0] == 240)
        assert np.all(_pixel_block_3x(img, pv, 60, 175)[:, :, 0] == 240)
    finally:
        scene.release()
        renderer.release()
        ctx.release()


def test_scalefx_preserves_90_degree_box_corners_and_1px_thin_diagonals() -> None:
    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    scene = SceneRenderer(renderer)
    try:
        palette = [(0, 0, 0)] * 32
        palette[1] = (230, 230, 230)
        scene.set_palette(palette)
        scene.canvas[:, :] = 0

        # Solid 16x16 square box: all four 90-degree corners must stay sharp
        scene.canvas[40:56, 40:56] = 1

        # 1-pixel thin 45-degree diagonal line (sword-blade geometry)
        for k in range(20):
            scene.canvas[100 + k, 140 - k] = 1
        scene.flush_to_fbo()

        settings = DisplaySettings(
            filter=FilterPreset.SCALEFX,
            scale_mode=ScaleMode.INTEGER,
            crop=CropMode.FULL,
        )
        img, pv = _render_scalefx(ctx, renderer, (960, 768), settings)

        # Box corners stay exact solid 3x3 blocks
        assert np.all(_pixel_block_3x(img, pv, 40, 40)[:, :, 0] == 230)
        assert np.all(_pixel_block_3x(img, pv, 55, 40)[:, :, 0] == 230)
        assert np.all(_pixel_block_3x(img, pv, 40, 55)[:, :, 0] == 230)
        assert np.all(_pixel_block_3x(img, pv, 55, 55)[:, :, 0] == 230)

        # 1px thin diagonal: background pixel (129, 110) connects across its BR corner
        # while the 1px blade pixel (130, 110) preserves its center subpixel
        thin_adj = _pixel_block_3x(img, pv, 129, 110)[:, :, 0].astype(int)
        assert thin_adj[0, 0] == 0
        assert thin_adj[1, 1] == 0
        assert thin_adj[2, 2] == 230
        thin_on = _pixel_block_3x(img, pv, 130, 110)[:, :, 0].astype(int)
        assert thin_on[1, 1] == 230
    finally:
        scene.release()
        renderer.release()
        ctx.release()


def test_scalefx_dithered_checkerboard_stays_stable_across_palette_cycling() -> None:
    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    scene = SceneRenderer(renderer)
    try:
        scene.canvas[:, :] = 0
        for y in range(60, 92):
            for x in range(60, 92):
                parity = (x + y) & 1
                sub = (x >> 1) & 1
                if parity == 0:
                    scene.canvas[y, x] = 1 if sub == 0 else 2
                else:
                    scene.canvas[y, x] = 3 if sub == 0 else 4
        for y in range(60, 92):
            for x in range(92, 112):
                scene.canvas[y, x] = 21 + ((x + y) % 3)

        settings = DisplaySettings(
            filter=FilterPreset.SCALEFX,
            scale_mode=ScaleMode.INTEGER,
            crop=CropMode.FULL,
        )
        water_cycle = [(30, 80, 180), (50, 120, 210), (90, 170, 240)]
        base_forest = [(20, 70, 20), (35, 95, 25), (65, 140, 40), (80, 155, 45)]

        first_interior: npt.NDArray[np.uint8] | None = None
        for step in range(9):
            palette = [(0, 0, 0)] * 32
            palette[1:5] = base_forest
            for i in range(3):
                palette[21 + i] = water_cycle[(i + step) % 3]
            scene.set_palette(palette)
            scene.flush_to_fbo()

            img, pv = _render_scalefx(ctx, renderer, (960, 768), settings)
            top_y = pv.window_height - (pv.dest_y + pv.dest_height)
            y0 = top_y + 66 * 3
            x0 = pv.dest_x + 66 * 3
            patch = img[y0 : y0 + 60, x0 : x0 + 60, :3]

            reshaped = patch.reshape(20, 3, 20, 3, 3)
            max_within_block = int(np.max(reshaped.max(axis=(1, 3)) - reshaped.min(axis=(1, 3))))
            assert max_within_block == 0

            if first_interior is None:
                first_interior = patch.copy()
            else:
                assert np.array_equal(patch, first_interior)
    finally:
        scene.release()
        renderer.release()
        ctx.release()


def test_scalefx_crop_isolation_and_non_multiple_integer_resampling() -> None:
    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    scene = SceneRenderer(renderer)
    try:
        palette = [(0, 0, 0)] * 32
        palette[1] = (255, 0, 255)  # bright magenta in overscan borders
        palette[2] = (16, 40, 110)  # solid dark blue in playfield
        scene.set_palette(palette)
        scene.canvas[:, :] = 1
        scene.canvas[PLAYFIELD_Y_OFFSET : PLAYFIELD_Y_OFFSET + PLAYFIELD_HEIGHT, :] = 2
        scene.flush_to_fbo()

        for win_size, scale_mode in [
            ((1920, 1080), ScaleMode.FIT),
            ((1366, 768), ScaleMode.INTEGER),
            ((960, 600), ScaleMode.INTEGER),
        ]:
            settings = DisplaySettings(
                filter=FilterPreset.SCALEFX,
                crop=CropMode.PLAYFIELD,
                aspect=AspectMode.PAL if win_size != (960, 600) else AspectMode.SQUARE,
                scale_mode=scale_mode,
            )
            img, pv = _render_scalefx(ctx, renderer, win_size, settings)
            assert [tex.size for tex in renderer.post_chain.stage_textures] == [
                (CANVAS_WIDTH, PLAYFIELD_HEIGHT),
                (CANVAS_WIDTH, PLAYFIELD_HEIGHT),
                (CANVAS_WIDTH, PLAYFIELD_HEIGHT),
                (CANVAS_WIDTH * 3, PLAYFIELD_HEIGHT * 3),
            ]
            top_y = pv.window_height - (pv.dest_y + pv.dest_height)
            dest = img[top_y : top_y + pv.dest_height, pv.dest_x : pv.dest_x + pv.dest_width, :3]
            assert int(dest[:, :, 0].max()) == 16
            assert int(dest[:, :, 1].max()) == 40
            assert int(dest[:, :, 2].max()) == 110
    finally:
        scene.release()
        renderer.release()
        ctx.release()


def test_scalefx_preset_switches_leak_zero_textures_and_preserve_state_hash() -> None:
    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    scene = SceneRenderer(renderer)
    audio = AudioEngine(headless=True)
    font = default_bitmap_font()
    try:
        session = GameSession(audio=audio)
        for _ in range(6):
            session.step_tick(InputFrame())
        expected_hash = session.state_hash()
        session.render_to_scene(scene, font)

        configs = (
            DisplaySettings(filter=FilterPreset.FAITHFUL),
            DisplaySettings(filter=FilterPreset.SCANLINES),
            DisplaySettings(filter=FilterPreset.CRT),
            DisplaySettings(filter=FilterPreset.SMOOTH, crop=CropMode.FULL),
            DisplaySettings(filter=FilterPreset.SCALEFX, crop=CropMode.FULL),
            DisplaySettings(
                filter=FilterPreset.SCALEFX,
                crop=CropMode.PLAYFIELD,
                aspect=AspectMode.PAL,
                scale_mode=ScaleMode.FIT,
            ),
        )
        chain = renderer.post_chain
        for idx in range(100):
            cfg = configs[idx % len(configs)]
            _render_scalefx(ctx, renderer, (1366, 768), cfg)
            assert (
                chain.total_textures_created - chain.total_textures_released
                == chain.active_texture_count
            )
            assert session.state_hash() == expected_hash

        _render_scalefx(ctx, renderer, (1366, 768), DisplaySettings(filter=FilterPreset.FAITHFUL))
        assert chain.active_texture_count == 0
        assert chain.total_textures_created == chain.total_textures_released
    finally:
        audio.close()
        scene.release()
        renderer.release()
        ctx.release()


def _enter_overworld_map(session: GameSession) -> None:
    for _ in range(6):
        session.step_tick(InputFrame())
    for _ in range(3):
        session.step_tick(InputFrame(players={KnightId.RED: PlayerInput(confirm=True)}))
        for _ in range(3):
            session.step_tick(InputFrame())
    for _ in range(30):
        session.step_tick(InputFrame())


def _static_forest_mask(
    terrain: bytes,
    base_canvas: npt.NDArray[np.uint8],
    changed_canvas: npt.NDArray[np.bool_],
    cycle_range: tuple[int, int],
    ramp_index: int,
) -> npt.NDArray[np.bool_]:
    grid = np.frombuffer(terrain, dtype=np.uint8).reshape((25, 40)) == 2
    forest_mask = np.repeat(np.repeat(grid, 8, axis=0), 8, axis=1)
    lo, hi = cycle_range
    dynamic_src = (
        changed_canvas | ((base_canvas >= lo) & (base_canvas <= hi)) | (base_canvas == ramp_index)
    )
    # Multi-pass ScaleFX inspects up to 4 source pixels away for 3:1 slopes
    padded = np.pad(dynamic_src, 4, mode="edge")
    near_dynamic = np.zeros_like(dynamic_src)
    for dy in range(9):
        for dx in range(9):
            near_dynamic |= padded[dy : dy + PLAYFIELD_HEIGHT, dx : dx + CANVAS_WIDTH]
    return np.asarray(forest_mask & ~near_dynamic, dtype=np.bool_)


@pytest.mark.adf
def test_real_map_forest_stays_stable_across_scalefx_palette_cycling(adf_dir: Path) -> None:
    """Real-disk acceptance: map dithered forests never crawl under ScaleFX palette cycling."""
    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    scene = SceneRenderer(renderer)
    audio = AudioEngine(headless=True)
    font = default_bitmap_font()
    try:
        session = GameSession(disk_set=DiskSet.from_directory(adf_dir), audio=audio)
        _enter_overworld_map(session)
        rules = session.overworld.rules
        assert rules is not None
        settings = DisplaySettings(
            filter=FilterPreset.SCALEFX,
            crop=CropMode.PLAYFIELD,
            aspect=AspectMode.SQUARE,
            scale_mode=ScaleMode.INTEGER,
        )

        session.render_to_scene(scene, font)
        base_canvas = scene.canvas[
            PLAYFIELD_Y_OFFSET : PLAYFIELD_Y_OFFSET + PLAYFIELD_HEIGHT, :
        ].copy()
        img0, pv = _render_scalefx(ctx, renderer, (960, 600), settings)
        assert pv.dest_width == 960 and pv.dest_height == 600

        changed_canvas = np.zeros_like(base_canvas, dtype=bool)
        frames: list[npt.NDArray[np.uint8]] = [img0]
        for _ in range(3):
            for _ in range(24):
                session.step_tick(InputFrame())
            session.render_to_scene(scene, font)
            cur_canvas = scene.canvas[PLAYFIELD_Y_OFFSET : PLAYFIELD_Y_OFFSET + PLAYFIELD_HEIGHT, :]
            changed_canvas |= cur_canvas != base_canvas
            img_n, _ = _render_scalefx(ctx, renderer, (960, 600), settings)
            frames.append(img_n)

        static_forest = _static_forest_mask(
            rules.terrain,
            base_canvas,
            changed_canvas,
            (rules.cycle_start, rules.cycle_end),
            rules.ramp_index,
        )
        assert int(static_forest.sum()) > 500

        mask_3x = np.repeat(np.repeat(static_forest, 3, axis=0), 3, axis=1)
        for img_n in frames[1:]:
            assert np.array_equal(img_n[mask_3x, :3], img0[mask_3x, :3])
    finally:
        audio.close()
        scene.release()
        renderer.release()
        ctx.release()
