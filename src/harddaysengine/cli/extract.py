"""``harddaysengine-extract`` console script for headless ADF asset extraction (T3.6)."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import TYPE_CHECKING

from harddaysengine import __version__
from harddaysengine.app.config import ConfigError, find_adf_images, resolve_adf_dir
from harddaysengine.assets.adf import ADFFormatError
from harddaysengine.assets.extract import export_hd_template, extract_all_assets

if TYPE_CHECKING:
    from collections.abc import Sequence


def build_extract_parser() -> argparse.ArgumentParser:
    """Build the argument parser for ``harddaysengine-extract``."""
    parser = argparse.ArgumentParser(
        prog="harddaysengine-extract",
        description="Extract Moonstone ADF assets into PNG/WAV/MOD files and manifest.json.",
    )
    parser.add_argument("--version", action="version", version=f"HardDaysEngine {__version__}")
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
        "--output-dir",
        "--output",
        dest="output_dir",
        default="build/assets",
        help="destination directory for extracted assets (default: ./build/assets)",
    )
    parser.add_argument(
        "--no-cel-pngs",
        action="store_true",
        help="skip writing individual per-frame cel PNGs (still decodes and records metadata)",
    )
    parser.add_argument(
        "--hd-template",
        action="store_true",
        help="export a 1x HD asset pack template (pack.toml, PNGs, .mask.png, meta.toml)",
    )
    return parser


def run_extraction(adf_dir: Path, output_dir: Path, *, export_cel_pngs: bool = True) -> int:
    """Execute asset extraction and print summary statistics."""
    images = find_adf_images(adf_dir)
    if not images:
        print(f"No .adf images found in {adf_dir}")
        return 1
    manifest = extract_all_assets(adf_dir, output_dir, export_cel_pngs=export_cel_pngs)
    print(
        f"Extracted {len(manifest.pictures)} pictures, {len(manifest.cels)} cels, "
        f"{len(manifest.arenas)} arenas, {len(manifest.audio_banks)} audio banks, "
        f"{len(manifest.modules)} modules -> {output_dir / 'manifest.json'}"
    )
    return 0


def run_hd_template_export(adf_dir: Path, output_dir: Path) -> int:
    """Export a 1x HD asset pack template from `.adf` images in `adf_dir`."""
    images = find_adf_images(adf_dir)
    if not images:
        print(f"No .adf images found in {adf_dir}")
        return 1
    try:
        summary = export_hd_template(adf_dir, output_dir)
    except ADFFormatError as exc:
        print(f"Error reading ADF images in {adf_dir}: {exc}")
        return 1
    print(
        f"Exported HD template: {summary.picture_count} pictures, {summary.arena_count} arenas, "
        f"{summary.cel_count} cels ({summary.cel_frame_count} frames), "
        f"{summary.mask_count} masks, {summary.meta_count} meta.toml -> {summary.pack_root}"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Console entry point for ``harddaysengine-extract``."""
    parser = build_extract_parser()
    args = parser.parse_args(argv)
    if args.hd_template and args.no_cel_pngs:
        parser.error("--no-cel-pngs cannot be used with --hd-template")
    try:
        adf_dir = resolve_adf_dir(
            args.adf_dir,
            environ=os.environ,
            config_path=Path(args.config),
            default=Path("disks"),
        )
    except ConfigError as exc:
        parser.error(str(exc))
    if args.hd_template:
        return run_hd_template_export(adf_dir, Path(args.output_dir))
    return run_extraction(
        adf_dir,
        Path(args.output_dir),
        export_cel_pngs=not bool(args.no_cel_pngs),
    )
