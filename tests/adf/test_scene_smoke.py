"""Real-disk scene smoke test: every audit scenario renders without an exception (T11.1)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pytest
from PIL import Image

from harddaysengine.app.audit import AUDIT_SCENARIOS, AuditRig, AuditShot, build_contact_sheet
from harddaysengine.render.renderer import CANVAS_HEIGHT, CANVAS_WIDTH

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.adf, pytest.mark.gl]


@pytest.fixture(scope="module")
def rig(adf_dir: Path) -> AuditRig:
    return AuditRig(adf_dir)


@pytest.mark.parametrize("scenario_name", sorted(AUDIT_SCENARIOS))
def test_scenario_renders_every_shot(rig: AuditRig, scenario_name: str) -> None:
    shots = list(AUDIT_SCENARIOS[scenario_name](rig))

    assert shots
    assert len({shot.name for shot in shots}) == len(shots)
    for shot in shots:
        assert shot.rgb.shape == (CANVAS_HEIGHT, CANVAS_WIDTH, 3)
        # A canvas that is one flat colour means the scene drew nothing.
        assert len(np.unique(shot.rgb.reshape(-1, 3), axis=0)) > 1, shot.name


def test_contact_sheet_tiles_shots_in_rows() -> None:
    red = np.zeros((4, 6, 3), dtype=np.uint8)
    red[..., 0] = 255
    shots = [AuditShot(name=f"s{i}", rgb=red) for i in range(5)]

    sheet = build_contact_sheet(shots, columns=2)

    assert isinstance(sheet, Image.Image)
    assert sheet.size == (2 * 6, 3 * (4 + 12))
    assert sheet.getpixel((0, 12)) == (255, 0, 0)
    assert sheet.getpixel((6, 2 * 16 + 12)) == (40, 40, 40)


def test_contact_sheet_rejects_empty_input() -> None:
    with pytest.raises(ValueError, match="no audit shots"):
        build_contact_sheet([])
