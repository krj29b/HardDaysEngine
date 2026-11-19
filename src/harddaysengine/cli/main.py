"""``harddaysengine`` console script (T1.4, T3.6, T4.4, T8.3)."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import TYPE_CHECKING

from harddaysengine import __version__
from harddaysengine.app.config import (
    ConfigError,
    find_adf_images,
    resolve_adf_dir,
    resolve_display_settings,
)
from harddaysengine.app.game import run_game_window, run_headless_session
from harddaysengine.cli.extract import run_extraction
from harddaysengine.cli.inspect_assets import run_inspector_cli
from harddaysengine.render.presentation import (
    AspectMode,
    CropMode,
    FilterPreset,
    FullscreenMode,
    ScaleMode,
)
from harddaysengine.sim.rng import DEFAULT_SEED

if TYPE_CHECKING:
    from collections.abc import Sequence

REQUIRED_DISK_COUNT = 3


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the ``harddaysengine`` command."""
    parser = argparse.ArgumentParser(
        prog="harddaysengine",
        description="Clean-room reimplementation of Moonstone: A Hard Days Knight (1991).",
    )
    parser.add_argument("--version", action="version", version=f"HardDaysEngine {__version__}")
    parser.add_argument(
        "--adf-dir",
        default=None,
        help="directory holding the three original .adf images (default: ./disks)",
    )
    parser.add_argument(
        "--config",
        default="config.toml",
        help="path to the TOML configuration file (default: ./config.toml)",
    )
    parser.add_argument(
        "--list-disks",
        action="store_true",
        help="list the ADF images found in the resolved directory and exit",
    )
    parser.add_argument(
        "--extract-assets",
        action="store_true",
        help="extract ADF assets into --output-dir and write manifest.json",
    )
    parser.add_argument(
        "--output-dir",
        default="build/assets",
        help="destination directory for --extract-assets (default: ./build/assets)",
    )
    parser.add_argument(
        "--inspect-assets",
        action="store_true",
        help="launch the 50 Hz ModernGL Lair Background, Sprite, and Hitbox Inspector",
    )
    parser.add_argument(
        "--cel",
        default=None,
        help="initial cel sprite set name for --inspect-assets (e.g. kn4, troll1)",
    )
    parser.add_argument(
        "--background",
        default=None,
        help="initial Lair or picture background for --inspect-assets (e.g. fol1.t)",
    )
    parser.add_argument(
        "--scale",
        type=int,
        default=None,
        help="initial window size in canvas pixels, 1..8 (default: [display] scale or 3)",
    )
    parser.add_argument(
        "--filter",
        choices=[preset.value for preset in FilterPreset],
        default=None,
        help="presentation preset (default: [display] filter or faithful)",
    )
    parser.add_argument(
        "--scale-mode",
        choices=[mode.value for mode in ScaleMode],
        default=None,
        help="viewport scaling mode (default: [display] scale_mode or integer)",
    )
    parser.add_argument(
        "--aspect",
        choices=[mode.value for mode in AspectMode],
        default=None,
        help="source pixel aspect ratio (default: [display] aspect or square)",
    )
    parser.add_argument(
        "--crop",
        choices=[mode.value for mode in CropMode],
        default=None,
        help="source canvas crop (default: [display] crop or full)",
    )
    parser.add_argument(
        "--fullscreen",
        choices=[mode.value for mode in FullscreenMode],
        default=None,
        help="window fullscreen mode (default: [display] fullscreen or off)",
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
    parser.add_argument(
        "--play",
        action="store_true",
        help="launch the playable Moonstone game window",
    )
    parser.add_argument(
        "--record",
        default=None,
        help="record deterministic 50 Hz input frames to a JSON replay file",
    )
    parser.add_argument(
        "--replay",
        default=None,
        help="replay deterministic 50 Hz input frames from a JSON replay file",
    )
    parser.add_argument(
        "--headless-ticks",
        type=int,
        default=None,
        help="run N 50 Hz simulation ticks headlessly without opening a window",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="deterministic 32-bit simulation seed",
    )
    return parser


def _list_disks(adf_dir: Path) -> int:
    images = find_adf_images(adf_dir)
    print(f"ADF directory: {adf_dir.resolve()}")
    for image in images:
        print(f"  {image.name}")
    print(f"{len(images)} image(s) found; Moonstone needs {REQUIRED_DISK_COUNT}.")
    return 0 if len(images) >= REQUIRED_DISK_COUNT else 1


def _should_launch_game(argv: Sequence[str] | None, args: argparse.Namespace) -> bool:
    if argv is None or len(argv) == 0:
        return True
    return bool(
        args.play
        or args.headless_ticks is not None
        or args.record is not None
        or args.replay is not None
        or args.seed is not None
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        adf_dir = resolve_adf_dir(
            args.adf_dir,
            environ=os.environ,
            config_path=Path(args.config),
            default=Path("disks"),
        )
        display = resolve_display_settings(
            Path(args.config),
            scale=args.scale,
            filter_name=args.filter,
            scale_mode=args.scale_mode,
            aspect=args.aspect,
            crop=args.crop,
            fullscreen=args.fullscreen,
        )
    except ConfigError as exc:
        parser.error(str(exc))
    if args.list_disks:
        return _list_disks(adf_dir)
    if args.extract_assets:
        return run_extraction(adf_dir, Path(args.output_dir))
    if args.inspect_assets:
        return run_inspector_cli(
            adf_dir,
            cel_name=args.cel,
            background_name=args.background,
            display=display,
            headless_frames=args.headless_frames,
            screenshot=args.screenshot,
        )
    if not _should_launch_game(argv, args):
        parser.print_help()
        return 0

    record_path = Path(args.record) if args.record is not None else None
    replay_path = Path(args.replay) if args.replay is not None else None
    seed = DEFAULT_SEED if args.seed is None else args.seed

    if args.headless_ticks is not None:
        result = run_headless_session(
            ticks=args.headless_ticks,
            seed=seed,
            record_path=record_path,
            replay_path=replay_path,
            adf_dir=adf_dir,
        )
        print(f"ticks={result.ticks_executed} scene={result.scene.name} hash={result.final_hash}")
        return 0

    return run_game_window(
        adf_dir,
        display=display,
        seed=seed,
        record_path=record_path,
        replay_path=replay_path,
    )
