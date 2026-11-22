"""Draft display settings for the game-window preview (ARCHITECTURE.md §13.10)."""

from __future__ import annotations

from dataclasses import replace

from harddaysengine.render.presentation import (
    MAX_CRT_CURVATURE,
    MAX_CRT_STRENGTH,
    CrtMaskMode,
    CrtSettings,
    FilterPreset,
)

_CRT_SLIDERS = (
    ("mask_strength", "Mask strength", 0.05, MAX_CRT_STRENGTH),
    ("scanlines", "Scanlines", 0.05, MAX_CRT_STRENGTH),
    ("curvature", "Curvature", 0.02, MAX_CRT_CURVATURE),
    ("bloom", "Bloom", 0.05, MAX_CRT_STRENGTH),
)


def cycle_filter(preset: FilterPreset, step: int) -> FilterPreset:
    """Advance one position through the filter presets."""
    presets = tuple(FilterPreset)
    return presets[(presets.index(preset) + step) % len(presets)]


class DisplayPreview:
    """Editable preset and CRT controls, separate from the applied window settings."""

    def __init__(self, preset: FilterPreset, crt: CrtSettings) -> None:
        self.preset = preset
        self.crt = crt
        self.selected = 0

    def rows(self) -> tuple[tuple[str, str], ...]:
        """Return the currently editable labels and formatted values."""
        preset = (("Filter", self.preset.value),)
        if self.preset is not FilterPreset.CRT:
            return preset
        return (
            *preset,
            ("Mask pattern", self.crt.mask.value),
            *((label, f"{getattr(self.crt, field):.2f}") for field, label, _, _ in _CRT_SLIDERS),
        )

    def move(self, step: int) -> None:
        """Select the next or previous visible option."""
        self.selected = (self.selected + step) % len(self.rows())

    def cycle(self, step: int) -> None:
        """Change the preset and return focus to its row."""
        self.preset = cycle_filter(self.preset, step)
        self.selected = 0

    def adjust(self, direction: int) -> None:
        """Step the selected value, wrapping presets and clamping numeric controls."""
        if self.selected == 0:
            self.cycle(direction)
        elif self.selected == 1:
            masks = tuple(CrtMaskMode)
            selected_mask = (masks.index(self.crt.mask) + direction) % len(masks)
            self.crt = replace(self.crt, mask=masks[selected_mask])
        else:
            field, _, step, maximum = _CRT_SLIDERS[self.selected - 2]
            value = round(float(getattr(self.crt, field)) + step * direction, 2)
            value = max(0.0, min(maximum, value))
            match field:
                case "mask_strength":
                    self.crt = replace(self.crt, mask_strength=value)
                case "scanlines":
                    self.crt = replace(self.crt, scanlines=value)
                case "curvature":
                    self.crt = replace(self.crt, curvature=value)
                case "bloom":
                    self.crt = replace(self.crt, bloom=value)
