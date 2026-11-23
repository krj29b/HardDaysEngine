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


def _set_crt_slider(crt: CrtSettings, field: str, value: float) -> CrtSettings:
    if field == "mask_strength":
        return replace(crt, mask_strength=value)
    if field == "scanlines":
        return replace(crt, scanlines=value)
    if field == "curvature":
        return replace(crt, curvature=value)
    return replace(crt, bloom=value)


class DisplayPreview:
    """Editable preset, HD mode, and CRT controls, separate from applied window settings."""

    def __init__(
        self,
        preset: FilterPreset,
        crt: CrtSettings,
        *,
        hd_available: bool = False,
        hd_enabled: bool = False,
    ) -> None:
        self.preset = preset
        self.crt = crt
        self.hd_available = hd_available
        self.hd_enabled = hd_enabled and hd_available
        self.selected = 0

    def rows(self) -> tuple[tuple[str, str], ...]:
        """Return the currently editable labels and formatted values."""
        head: list[tuple[str, str]] = [("Filter", self.preset.value)]
        if self.hd_available:
            head.append(("HD mode", "on" if self.hd_enabled else "off"))
        if self.preset is not FilterPreset.CRT:
            return tuple(head)
        return (
            *head,
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

    def toggle_hd(self) -> bool:
        """Toggle draft HD mode when a pack is loaded (§14.7)."""
        if self.hd_available:
            self.hd_enabled = not self.hd_enabled
        return self.hd_enabled

    def adjust(self, direction: int) -> None:
        """Step the selected value, wrapping presets and clamping numeric controls."""
        if self.selected == 0:
            self.cycle(direction)
            return
        if self.hd_available and self.selected == 1:
            self.toggle_hd()
            return
        crt_offset = self.selected - (2 if self.hd_available else 1)
        if crt_offset == 0:
            masks = tuple(CrtMaskMode)
            selected_mask = (masks.index(self.crt.mask) + direction) % len(masks)
            self.crt = replace(self.crt, mask=masks[selected_mask])
        else:
            field, _, step, maximum = _CRT_SLIDERS[crt_offset - 1]
            value = round(float(getattr(self.crt, field)) + step * direction, 2)
            value = max(0.0, min(maximum, value))
            self.crt = _set_crt_slider(self.crt, field, value)
