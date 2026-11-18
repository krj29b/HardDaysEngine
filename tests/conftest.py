"""Pytest configuration and shared fixtures."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from harddaysengine.app.config import find_adf_images, resolve_adf_dir

REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIRED_DISK_COUNT = 3


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--adf-dir",
        action="store",
        default=None,
        help="directory holding the three Moonstone .adf images for @pytest.mark.adf tests",
    )


@pytest.fixture(scope="session")
def adf_dir(request: pytest.FixtureRequest) -> Path:
    """Directory with the three original disk images (--adf-dir, env, config.toml, ./disks).

    Skips the test cleanly when fewer than three ADF images are available.
    """
    resolved = resolve_adf_dir(
        request.config.getoption("--adf-dir"),
        environ=os.environ,
        config_path=REPO_ROOT / "config.toml",
        default=REPO_ROOT / "disks",
    )
    if len(find_adf_images(resolved)) < REQUIRED_DISK_COUNT:
        pytest.skip(f"Moonstone ADF images not found in {resolved} (see README: BYOA)")
    return resolved
