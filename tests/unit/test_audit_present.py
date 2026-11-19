"""Unit tests for presentation audit helpers and size parsing (T13.9, ADR-053)."""

from __future__ import annotations

import numpy as np
import pytest

from harddaysengine.app.audit import (
    AUDIT_SCENARIOS,
    PRESENTATION_SCENARIOS,
    AuditShot,
    build_contact_sheet,
    parse_present_size,
)


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("1920x1080", (1920, 1080)),
        ("1366x768", (1366, 768)),
        ("2560X1080", (2560, 1080)),
        ("320x256", (320, 256)),
    ],
)
def test_parse_present_size_valid(spec: str, expected: tuple[int, int]) -> None:
    assert parse_present_size(spec) == expected


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "1920",
        "1920x",
        "x1080",
        "0x1080",
        "1920x0",
        "-1920x1080",
        "1920x1080x2",
        "1920.0x1080",
        "wide",
    ],
)
def test_parse_present_size_invalid(bad: str) -> None:
    with pytest.raises(ValueError, match="size"):
        parse_present_size(bad)


def test_presentation_scenarios_cover_required_screens() -> None:
    for required in ("menu_map", "places", "fights", "next_day", "presentation"):
        assert required in PRESENTATION_SCENARIOS
        assert required in AUDIT_SCENARIOS


def test_contact_sheet_scales_cells_when_max_cell_width_set() -> None:
    img = np.zeros((1080, 1920, 3), dtype=np.uint8)
    img[:, :, 1] = 200
    shots = [AuditShot(name=f"shot_{i}", rgb=img) for i in range(4)]

    full_sheet = build_contact_sheet(shots, columns=2)
    assert full_sheet.size == (2 * 1920, 2 * (1080 + 12))

    scaled_sheet = build_contact_sheet(shots, columns=2, max_cell_width=960)
    assert scaled_sheet.size == (2 * 960, 2 * (540 + 12))
    assert scaled_sheet.getpixel((10, 20)) == (0, 200, 0)
