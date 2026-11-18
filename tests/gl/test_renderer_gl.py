"""Standalone ModernGL OpenGL 3.3 core renderer and inspector smoke tests (T4.1, T4.2, T4.4)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import moderngl
import numpy as np
import pytest

from harddaysengine.assets import decode_cel, decode_picture, parse_collide_hit
from harddaysengine.assets.cel import CelFrame, CelFrameMeta
from harddaysengine.cli.inspect_assets import main as inspect_cli_main
from harddaysengine.cli.main import main as main_cli
from harddaysengine.render import CANVAS_HEIGHT, CANVAS_WIDTH, HardDaysRenderer
from tests.fixtures.synthetic_adf import (
    build_synthetic_arena,
    build_synthetic_cel,
    build_synthetic_ofs_image,
    build_synthetic_picture,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.gl


def test_moderngl_renderer_canvas_and_hitbox_overlay() -> None:
    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    try:
        pic = decode_picture(build_synthetic_picture(planes=5, fill_index=2))
        renderer.upload_background(pic.pixels, pic.palette)

        cel = decode_cel(build_synthetic_cel(width=24, height=16))
        hit_sets = parse_collide_hit("kn4\n02 01 006 006 018 010\n99\n")
        hf = hit_sets["kn4"].frames[0]

        lines = renderer.render_to_canvas(cel.frames[0], sprite_x=160, sprite_y=100, hit_frame=hf)
        assert len(lines) == 16

        rgba = renderer.read_canvas_rgba()
        assert rgba.shape == (CANVAS_HEIGHT, CANVAS_WIDTH, 4)
        # Verify non-black playfield pixels and bright green/red overlay pixels
        assert int(rgba.max()) > 0
        green_dominant = (rgba[:, :, 1] > 200) & (rgba[:, :, 0] < 80)
        red_dominant = (rgba[:, :, 0] > 200) & (rgba[:, :, 1] < 80)
        assert bool(np.any(green_dominant)) is True
        assert bool(np.any(red_dominant)) is True
    finally:
        renderer.release()
        ctx.release()


def test_inspector_cli_headless_and_screenshot(tmp_path: Path) -> None:
    disks_dir = tmp_path / "disks"
    disks_dir.mkdir()
    test_pack = b"".join(build_synthetic_picture(planes=5, fill_index=i + 1) for i in range(9))
    (disks_dir / "Disk2.adf").write_bytes(
        build_synthetic_ofs_image(
            {
                "HE1.ob": build_synthetic_cel(width=16, height=12),
                "kn4.ob": build_synthetic_cel(width=24, height=16),
                "collide.hit": b"kn4\n00\n01 01 012 008\n99\n",
                "test": test_pack,
                "fol1.t": build_synthetic_arena(),
            }
        )
    )

    shot1 = tmp_path / "shot1.png"
    rc1 = inspect_cli_main(
        [
            "--adf-dir",
            str(disks_dir),
            "--cel",
            "kn4",
            "--background",
            "fol1.t",
            "--headless-frames",
            "6",
            "--screenshot",
            str(shot1),
        ]
    )
    assert rc1 == 0
    assert shot1.is_file()
    assert shot1.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

    shot2 = tmp_path / "shot2.png"
    rc2 = main_cli(
        [
            "--adf-dir",
            str(disks_dir),
            "--inspect-assets",
            "--cel",
            "kn4",
            "--headless-frames",
            "6",
            "--screenshot",
            str(shot2),
        ]
    )
    assert rc2 == 0
    assert shot2.read_bytes() == shot1.read_bytes()


def test_moderngl_renderer_top_left_sprite_ignores_hotspot_and_centering() -> None:
    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    try:
        pic = decode_picture(build_synthetic_picture(planes=5, fill_index=2))
        renderer.upload_background(pic.pixels, pic.palette)

        pixels = np.full((12, 16), 6, dtype=np.uint8)
        frame = CelFrame(
            meta=CelFrameMeta(
                index=0,
                data_offset=0,
                width=16,
                height=12,
                hot_and_flags=0x31,
                plane_bits=0x1F,
            ),
            pixels=pixels,
            mask=pixels != 0,
        )
        # Verify top_left=True places the sprite's top-left at (40, PLAYFIELD_Y_OFFSET + 30)
        lines = renderer.render_to_canvas(
            frame, sprite_x=40, sprite_y=30, show_overlay=False, top_left=True
        )
        assert lines == []

        rgba = renderer.read_canvas_rgba()
        assert tuple(int(c) for c in rgba[30 + 28, 40, :3]) == pic.palette[6]
        assert tuple(int(c) for c in rgba[30 + 28, 39, :3]) == pic.palette[2]
        assert tuple(int(c) for c in rgba[29 + 28, 40, :3]) == pic.palette[2]
    finally:
        renderer.release()
        ctx.release()
