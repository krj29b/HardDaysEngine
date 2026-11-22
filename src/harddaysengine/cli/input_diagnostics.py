"""``harddaysengine-input-diagnostics`` console script (ARCHITECTURE.md §13.12, T16.2)."""

from __future__ import annotations

import argparse
import math
from typing import TYPE_CHECKING

from harddaysengine import __version__
from harddaysengine.app.input_diagnostics import DiagnosticsOptions, run_input_diagnostics

if TYPE_CHECKING:
    from collections.abc import Sequence


def build_input_diagnostics_parser() -> argparse.ArgumentParser:
    """Build the argument parser for ``harddaysengine-input-diagnostics``."""
    parser = argparse.ArgumentParser(
        prog="harddaysengine-input-diagnostics",
        description=(
            "Inspect connected SDL input devices and log keyboard, mouse, raw joystick, "
            "and mapped game-controller events without launching the game window or loading disks."
        ),
    )
    parser.add_argument("--version", action="version", version=f"HardDaysEngine {__version__}")
    parser.add_argument(
        "--no-window",
        action="store_true",
        help="run in console-only mode without opening a plain-SDL capture surface",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="stop automatically after the given number of seconds",
    )
    return parser


def _parse_args(parser: argparse.ArgumentParser, argv: Sequence[str] | None) -> argparse.Namespace:
    args = parser.parse_args(argv)
    if args.duration is not None and (not math.isfinite(args.duration) or args.duration < 0.0):
        parser.error("--duration must be a non-negative finite number")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    """Console entry point for ``harddaysengine-input-diagnostics``."""
    parser = build_input_diagnostics_parser()
    args = _parse_args(parser, argv)
    options = DiagnosticsOptions(
        no_window=bool(args.no_window),
        duration_s=args.duration,
    )
    run_input_diagnostics(options)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
