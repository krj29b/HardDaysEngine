"""Write real-disk audit contact sheets to ``build/audit/`` (T11.1, T13.9, T19.7).

Usage:
  ``uv run python scripts/audit_shots.py [--adf-dir DIR] [--out-dir DIR]``
  ``uv run python scripts/audit_shots.py --present faithful --size 1920x1080``
  ``uv run python scripts/audit_shots.py --hd-pack path/to/pack [--hitboxes]``
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from harddaysengine.app.audit import (
    AUDIT_SCENARIOS,
    DEFAULT_PRESENT_SIZE,
    parse_present_size,
    run_audit,
)
from harddaysengine.app.config import find_adf_images, resolve_adf_dir
from harddaysengine.assets.hd_pack import HdPack
from harddaysengine.assets.hd_pack_schema import HdPackError
from harddaysengine.render.presentation import (
    AspectMode,
    CropMode,
    DisplaySettings,
    FilterPreset,
    ScaleMode,
    SurroundMode,
    SurroundSettings,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

ROOT = Path(__file__).resolve().parent.parent
REQUIRED_DISK_COUNT = 3


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser for ``scripts/audit_shots.py``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adf-dir", default=None, help="directory with the three .adf images")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "build" / "audit")
    parser.add_argument(
        "--scenario",
        action="append",
        choices=sorted(AUDIT_SCENARIOS),
        help="capture only this scenario (repeat to select several)",
    )
    parser.add_argument(
        "--present",
        choices=[preset.value for preset in FilterPreset],
        default=None,
        help="render through the real presentation path with this preset into an offscreen FBO",
    )
    parser.add_argument(
        "--size",
        default=None,
        help="offscreen presentation size WxH (e.g. 1920x1080, 1366x768); requires --present",
    )
    parser.add_argument(
        "--scale-mode",
        choices=[mode.value for mode in ScaleMode],
        default=ScaleMode.INTEGER.value,
        help="viewport scaling mode for --present (default: integer)",
    )
    parser.add_argument(
        "--aspect",
        choices=[mode.value for mode in AspectMode],
        default=AspectMode.SQUARE.value,
        help="pixel aspect mode for --present (default: square)",
    )
    parser.add_argument(
        "--crop",
        choices=[mode.value for mode in CropMode],
        default=CropMode.FULL.value,
        help="canvas crop mode for --present (default: full)",
    )
    parser.add_argument(
        "--surround",
        choices=[mode.value for mode in SurroundMode],
        default=SurroundMode.OFF.value,
        help="widescreen surround mode for --present (default: off)",
    )
    parser.add_argument(
        "--hd-pack",
        default=None,
        metavar="DIR",
        help="external HD asset pack directory for side-by-side Classic vs. HD sheets",
    )
    parser.add_argument(
        "--hitboxes",
        action="store_true",
        help="overlay hurtbox and collide.hit weapon hitbox wireframes on fight shots",
    )
    return parser


def _resolve_present_options(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> tuple[DisplaySettings | None, tuple[int, int] | None]:
    if args.present is None:
        if args.size is not None:
            parser.error("--size requires --present")
        return (None, None)
    try:
        present_size = DEFAULT_PRESENT_SIZE if args.size is None else parse_present_size(args.size)
    except ValueError as exc:
        parser.error(str(exc))
    display = DisplaySettings(
        filter=FilterPreset(args.present),
        scale_mode=ScaleMode(args.scale_mode),
        aspect=AspectMode(args.aspect),
        crop=CropMode(args.crop),
        surround=SurroundSettings(mode=SurroundMode(args.surround)),
    )
    return (display, present_size)


def _resolve_hd_pack(
    parser: argparse.ArgumentParser,
    hd_pack_arg: str | None,
) -> HdPack | None:
    if hd_pack_arg is None:
        return None
    try:
        return HdPack.load(Path(hd_pack_arg))
    except HdPackError as exc:
        parser.error(str(exc))


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    display, present_size = _resolve_present_options(parser, args)
    hd_pack = _resolve_hd_pack(parser, args.hd_pack)

    adf_dir = resolve_adf_dir(
        args.adf_dir,
        environ=os.environ,
        config_path=ROOT / "config.toml",
        default=ROOT / "disks",
    )
    if len(find_adf_images(adf_dir)) < REQUIRED_DISK_COUNT:
        print(f"Moonstone ADF images not found in {adf_dir}")
        return 1
    written = run_audit(
        adf_dir,
        args.out_dir,
        args.scenario,
        display=display,
        present_size=present_size,
        hd_pack=hd_pack,
        show_hitboxes=args.hitboxes,
    )
    for name, path in written.items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
