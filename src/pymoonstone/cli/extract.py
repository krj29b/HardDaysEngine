"""``pymoonstone-extract`` console script for headless ADF asset extraction (T3.6)."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import TYPE_CHECKING

from pymoonstone import __version__
from pymoonstone.app.config import ConfigError, find_adf_images, resolve_adf_dir
from pymoonstone.assets.extract import extract_all_assets

if TYPE_CHECKING:
    from collections.abc import Sequence


def build_extract_parser() -> argparse.ArgumentParser:
    """Build the argument parser for ``pymoonstone-extract``."""
    parser = argparse.ArgumentParser(
        prog="pymoonstone-extract",
        description="Extract Moonstone ADF assets into PNG/WAV/MOD files and manifest.json.",
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
        "--output-dir",
        default="build/assets",
        help="destination directory for extracted assets (default: ./build/assets)",
    )
    parser.add_argument(
        "--no-cel-pngs",
        action="store_true",
        help="skip writing individual per-frame cel PNGs (still decodes and records metadata)",
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


def main(argv: Sequence[str] | None = None) -> int:
    """Console entry point for ``pymoonstone-extract``."""
    parser = build_extract_parser()
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
    return run_extraction(
        adf_dir,
        Path(args.output_dir),
        export_cel_pngs=not bool(args.no_cel_pngs),
    )
