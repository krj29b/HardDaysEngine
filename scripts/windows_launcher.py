"""Double-click entry point for the portable Windows build (ADR-054)."""

import ctypes
import os
import sys
from pathlib import Path

from harddaysengine.app.config import ConfigError, find_adf_images, resolve_adf_dir
from harddaysengine.cli.main import REQUIRED_DISK_COUNT, main


def launch() -> int:
    if len(sys.argv) > 1:
        return main()
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
