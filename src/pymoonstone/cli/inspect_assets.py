"""``pymoonstone-inspect`` console script for the ModernGL 50 Hz Asset & Hitbox Inspector (T4.4)."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import TYPE_CHECKING

from pymoonstone import __version__
from pymoonstone.app.config import ConfigError, find_adf_images, resolve_adf_dir
from pymoonstone.app.inspector import run_inspector_headless, run_inspector_window
from pymoonstone.assets.adf import DiskSet

if TYPE_CHECKING:
    from collections.abc import Sequence


def build_inspect_parser() -> argparse.ArgumentParser:
    """Build the argument parser for ``pymoonstone-inspect``."""
    parser = argparse.ArgumentParser(
        prog="pymoonstone-inspect",
        description="Launch the 50 Hz ModernGL Lair Background, Sprite, and Hitbox Inspector.",
    )
    parser.add_argument("--version", action="version", version=f"pymoonstone {__version__}")
    parser.add_argument(
        "--adf-dir",
        default=None,
        help="directory holding the original .adf images (default: ./disks)",
    )
    parser.add_argument(
        "--config",
        default="config.toml",
        help="path to the TOML configuration file (default: ./config.toml)",
    )
    parser.add_argument(
        "--cel",
        default=None,
        help="initial cel sprite set name to inspect (e.g. kn4, troll1, dragon1)",
    )
    parser.add_argument(
        "--background",
        default=None,
        help="initial Lair or picture background to inspect (e.g. fol1.t, bg1a.PIV)",
    )
    parser.add_argument(
        "--scale",
        type=int,
        default=3,
        help="integer window scaling factor for 320x256 canvas (default: 3)",
    )
    parser.add_argument(
        "--headless-frames",
        type=int,
        default=None,
        help="render N 50 Hz ticks on a standalone OpenGL 3.3 context without opening a window",
    )
    parser.add_argument(
        "--screenshot",
        default=None,
        help="optional PNG path to write the rendered canvas when using --headless-frames",
    )
    return parser


def run_inspector_cli(
    adf_dir: Path,
    *,
    cel_name: str | None = None,
    background_name: str | None = None,
    scale: int = 3,
    headless_frames: int | None = None,
    screenshot: str | None = None,
) -> int:
    """Run the inspector in either standalone headless mode or interactive pygame-ce window mode."""
    images = find_adf_images(adf_dir)
    if not images:
        print(f"No .adf images found in {adf_dir}")
        return 1
    disk_set = DiskSet.from_directory(adf_dir)
    if headless_frames is not None:
        shot_path = Path(screenshot) if screenshot is not None else None
        rgba = run_inspector_headless(
            disk_set,
            frames=headless_frames,
            cel_name=cel_name,
            background_name=background_name,
            screenshot_path=shot_path,
        )
        print(f"Rendered {headless_frames} frame(s) ({rgba.shape[1]}x{rgba.shape[0]} RGBA).")
        return 0
    return run_inspector_window(
        disk_set,
        scale=scale,
        cel_name=cel_name,
        background_name=background_name,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Console entry point for ``pymoonstone-inspect``."""
    parser = build_inspect_parser()
    args = parser.parse_args(argv)
    try:
        adf_dir = resolve_adf_dir(
            args.adf_dir,
            environ=os.environ,
            config_path=Path(args.config),
            default=Path("disks"),
        )
    except ConfigError as exc:
        parser.error(str(exc))
    return run_inspector_cli(
        adf_dir,
        cel_name=args.cel,
        background_name=args.background,
        scale=args.scale,
        headless_frames=args.headless_frames,
        screenshot=args.screenshot,
    )
