"""Double-click entry point for the portable Windows build (ADR-054)."""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from harddaysengine.app.config import ConfigError, find_adf_images, resolve_adf_dir
from harddaysengine.cli.input_diagnostics import main as run_input_diagnostics_cli
from harddaysengine.cli.main import REQUIRED_DISK_COUNT, main

if TYPE_CHECKING:
    from collections.abc import Sequence

INPUT_DIAGNOSTICS_FLAG = "--input-diagnostics"


def launch(argv: Sequence[str] | None = None) -> int:
    """Dispatch Windows portable launcher CLI flags or double-click startup."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == INPUT_DIAGNOSTICS_FLAG:
        return run_input_diagnostics_cli(args[1:])
    if args:
        return main() if argv is None else main(args)
    os.chdir(Path(sys.executable).parent)
    try:
        directory = resolve_adf_dir(
            None, environ=os.environ, config_path=Path("config.toml"), default=Path("disks")
        )
        if len(find_adf_images(directory)) >= REQUIRED_DISK_COUNT:
            return main()
        message = (
            "Copy your three original Moonstone Amiga disk images (.adf) into the folder below.\n\n"
            f"{directory.resolve()}\n\nThen launch HardDaysEngine again."
        )
    except (ConfigError, OSError) as exc:
        message = str(exc)
    ctypes.windll.user32.MessageBoxW(None, message, "HardDaysEngine", 0x40)
    return 1


if __name__ == "__main__":
    raise SystemExit(launch())
