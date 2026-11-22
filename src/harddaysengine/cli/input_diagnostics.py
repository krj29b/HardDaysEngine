"""``harddaysengine-input-diagnostics`` console script (ARCHITECTURE.md §13.12, T16.2-T16.3)."""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from harddaysengine import __version__
from harddaysengine.app.input_diag_report import (
    DEFAULT_AXIS_EPSILON,
    DEFAULT_COALESCE_MS,
    DEFAULT_MAX_RECORDS,
)
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
        "-o",
        "--output",
        default=None,
        help="write a flushed UTF-8 JSON Lines diagnostic report to this path",
    )
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
    parser.add_argument(
        "--max-records",
        type=int,
        default=DEFAULT_MAX_RECORDS,
        help=f"maximum emitted report records before stopping (default: {DEFAULT_MAX_RECORDS})",
    )
    parser.add_argument(
        "--coalesce-ms",
        type=int,
        default=DEFAULT_COALESCE_MS,
        help=f"coalescing window in ms for high-rate motion/axes (default: {DEFAULT_COALESCE_MS})",
    )
    parser.add_argument(
        "--axis-epsilon",
        type=float,
        default=DEFAULT_AXIS_EPSILON,
        help=f"deadband delta for raw axis noise coalescing (default: {DEFAULT_AXIS_EPSILON})",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="disable motion/axis coalescing while keeping the --max-records bound",
    )
    return parser


def _validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.duration is not None and (not math.isfinite(args.duration) or args.duration < 0.0):
        parser.error("--duration must be a non-negative finite number")
    if args.max_records < 1:
        parser.error("--max-records must be a positive integer")
    if args.coalesce_ms < 0:
        parser.error("--coalesce-ms must be a non-negative integer")
    if not math.isfinite(args.axis_epsilon) or args.axis_epsilon < 0.0:
        parser.error("--axis-epsilon must be a non-negative finite number")


def main(argv: Sequence[str] | None = None) -> int:
    """Console entry point for ``harddaysengine-input-diagnostics``."""
    parser = build_input_diagnostics_parser()
    args = parser.parse_args(argv)
    _validate_args(parser, args)
    options = DiagnosticsOptions(
        no_window=bool(args.no_window),
        duration_s=args.duration,
        output_path=Path(args.output) if args.output is not None else None,
        max_records=args.max_records,
        coalesce_ms=args.coalesce_ms,
        axis_epsilon=args.axis_epsilon,
        raw=bool(args.raw),
    )
    try:
        run_input_diagnostics(options)
    except OSError as exc:
        print(f"Error writing diagnostic report: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
