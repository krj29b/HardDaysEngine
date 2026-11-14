"""Standalone ModernGL OpenGL 3.3 core renderer and inspector smoke tests (T4.1, T4.2, T4.4)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import moderngl
import numpy as np
import pytest

from pymoonstone.assets import decode_cel, decode_picture, parse_collide_hit
from pymoonstone.cli.inspect_assets import main as inspect_cli_main
from pymoonstone.cli.main import main as main_cli
from pymoonstone.render import CANVAS_HEIGHT, CANVAS_WIDTH, MoonstoneRenderer
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
    renderer = MoonstoneRenderer(ctx)
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
