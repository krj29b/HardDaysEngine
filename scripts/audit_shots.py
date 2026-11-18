"""Write real-disk audit contact sheets to ``build/audit/`` (T11.1).

Usage: ``uv run python scripts/audit_shots.py [--adf-dir DIR] [--out-dir DIR]``
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from harddaysengine.app.audit import AUDIT_SCENARIOS, run_audit
from harddaysengine.app.config import find_adf_images, resolve_adf_dir

ROOT = Path(__file__).resolve().parent.parent
REQUIRED_DISK_COUNT = 3


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adf-dir", default=None, help="directory with the three .adf images")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "build" / "audit")
    parser.add_argument(
        "--scenario",
        action="append",
        choices=sorted(AUDIT_SCENARIOS),
        help="capture only this scenario (repeat to select several)",
    )
    args = parser.parse_args()

    adf_dir = resolve_adf_dir(
        args.adf_dir,
        environ=os.environ,
        config_path=ROOT / "config.toml",
        default=ROOT / "disks",
    )
    if len(find_adf_images(adf_dir)) < REQUIRED_DISK_COUNT:
        print(f"Moonstone ADF images not found in {adf_dir}")
        return 1
    for name, path in run_audit(adf_dir, args.out_dir, args.scenario).items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
