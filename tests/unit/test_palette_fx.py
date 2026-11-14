"""Unit tests for the 50 Hz PAL 12-bit ``$0RGB`` palette & VBL effect state machine."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from pymoonstone.sim.palette_fx import (
    BLACK,
    BLOOD_RED,
    FADE_MAX_STEPS,
    PALETTE_SIZE,
    WHITE,
    CycleRange,
    FlashOverride,
    PaletteFxState,
    cycle_step,
    pack_rgb12,
    rgb12_to_rgb24,
    split_rgb12,
    step_color,
    step_nibble,
    validate_color,
    validate_palette,
)

pytestmark = pytest.mark.sim


def _make_palette(fill: int = BLACK) -> list[int]:
    return [fill] * PALETTE_SIZE


def test_rgb12_helpers_and_validation() -> None:
    assert split_rgb12(0xA4F) == (0xA, 0x4, 0xF)
    assert pack_rgb12(0xA, 0x4, 0xF) == 0xA4F
    assert pack_rgb12(-3, 20, 7) == 0x0F7
    assert rgb12_to_rgb24(0xA05) == (0xAA, 0x00, 0x55)
    assert rgb12_to_rgb24(WHITE) == (0xFF, 0xFF, 0xFF)
    assert validate_color(0x000) == 0x000
    assert validate_color(0xFFF) == 0xFFF

    with pytest.raises(ValueError, match="out of range"):
        validate_color(-1)
    with pytest.raises(ValueError, match="out of range"):
        validate_color(0x1000)
    with pytest.raises(ValueError, match="expected 32"):
        validate_palette([0, 1, 2])


def test_step_nibble_and_step_color() -> None:
    assert step_nibble(3, 7) == 4
    assert step_nibble(7, 3) == 6
    assert step_nibble(5, 5) == 5

    # Mixed directions per nibble: R up (+1), G down (-1), B unchanged
    assert step_color(0x285, 0x645) == 0x375
    assert step_color(0x375, 0x645) == 0x465
    assert step_color(0x465, 0x645) == 0x555
    assert step_color(0x555, 0x645) == 0x645
    assert step_color(0x645, 0x645) == 0x645


def test_fade_in_and_fade_out_exact_vbl_transitions() -> None:
    target = _make_palette(BLACK)
    target[0] = 0x000
    target[1] = 0xFFF
    target[2] = 0x352
    target[15] = BLOOD_RED

    state = PaletteFxState()
    state.fade_in(target, divisor=1)
    assert state.is_fading is True
    assert state.current_palette()[1] == 0x000

    # After 1 VBL: each non-zero nibble steps by +1
    pal = state.step_vbl()
    assert pal[0] == 0x000
    assert pal[1] == 0x111
    assert pal[2] == 0x111
    assert pal[15] == 0x100

    # After 3 VBLs total: index 2 reaches 0x332 -> 0x342 -> 0x352 at VBL 5
    for _ in range(2):
        pal = state.step_vbl()
    assert pal[1] == 0x333
    assert pal[2] == 0x332

    for _ in range(2):
        pal = state.step_vbl()
    assert pal[2] == 0x352
    assert pal[1] == 0x555
    assert pal[15] == 0x500

    # Step remaining 10 VBLs to complete full 15-step 0x000 -> 0xFFF fade
    for _ in range(FADE_MAX_STEPS - 5):
        pal = state.step_vbl()
    assert pal == tuple(target)
    assert state.is_fading is False
    assert state.vbl_count == 15

    # Fade out back to black
    state.fade_out(divisor=1)
    assert state.is_fading is True
    pal = state.step_vbl()
    assert pal[1] == 0xEEE
    assert pal[2] == 0x241
    assert pal[15] == 0x900

    for _ in range(FADE_MAX_STEPS - 1):
        pal = state.step_vbl()
    assert pal == tuple(_make_palette(BLACK))
    assert state.is_fading is False


def test_fade_with_n_vbl_divisor() -> None:
    initial = _make_palette(0x000)
    target = _make_palette(0x000)
    target[5] = 0x204

    state = PaletteFxState(initial)
    state.fade_to(target, divisor=3)

    # VBL 1 & 2: no change yet
    assert state.step_vbl()[5] == 0x000
    assert state.step_vbl()[5] == 0x000
    # VBL 3: first step
    assert state.step_vbl()[5] == 0x101
    # VBL 4 & 5: hold
    assert state.step_vbl()[5] == 0x101
    assert state.step_vbl()[5] == 0x101
    # VBL 6: second step
    assert state.step_vbl()[5] == 0x202
    assert state.is_fading is True
    # VBL 12: reaches 0x204 and stops fading
    for _ in range(6):
        state.step_vbl()
    assert state.current_palette()[5] == 0x204
    assert state.is_fading is False

    with pytest.raises(ValueError, match="divisor"):
        state.fade_to(target, divisor=0)


def test_cycle_step_forward_backward_and_bounds() -> None:
    pal = list(range(PALETTE_SIZE))

    fwd = cycle_step(pal, 16, 19, direction=1)
    assert fwd[16:20] == (19, 16, 17, 18)
    assert fwd[15] == 15
    assert fwd[20] == 20

    bwd = cycle_step(fwd, 16, 19, direction=-1)
    assert bwd == tuple(pal)

    single = cycle_step(pal, 7, 7, direction=1)
    assert single == tuple(pal)

    with pytest.raises(ValueError, match="invalid cycle range"):
        cycle_step(pal, 10, 8)
    with pytest.raises(ValueError, match="invalid cycle range"):
        cycle_step(pal, 0, PALETTE_SIZE)
    with pytest.raises(ValueError, match="direction"):
        cycle_step(pal, 1, 3, direction=2)


def test_vbl_color_cycling_period_and_fade_composition() -> None:
    pal = _make_palette(BLACK)
    pal[20] = 0x111
    pal[21] = 0x222
    pal[22] = 0x333

    state = PaletteFxState(pal)
    slot = state.add_cycle(20, 22, period_vbls=2, direction=1)
    assert slot == 0

    # Tick 1: period not reached
    assert state.step_vbl()[20:23] == (0x111, 0x222, 0x333)
    # Tick 2: rotates forward
    assert state.step_vbl()[20:23] == (0x333, 0x111, 0x222)
    # Tick 3: hold
    assert state.step_vbl()[20:23] == (0x333, 0x111, 0x222)
    # Tick 4: rotates forward again
    assert state.step_vbl()[20:23] == (0x222, 0x333, 0x111)

    state.clear_cycles()
    assert state.step_vbl()[20:23] == (0x222, 0x333, 0x111)


def test_cycling_during_fade_in_preserves_cycled_targets() -> None:
    target = _make_palette(BLACK)
    target[24] = 0x300
    target[25] = 0x030
    target[26] = 0x003

    state = PaletteFxState()
    state.add_cycle(24, 26, period_vbls=1, direction=1)
    state.fade_in(target, divisor=1)

    for _ in range(3):
        state.step_vbl()
    assert state.is_fading is False
    # After 3 forward rotations, [24, 25, 26] returns to original order
    assert state.current_palette()[24:27] == (0x300, 0x030, 0x003)


def test_lightning_and_blood_flash_override_and_restore() -> None:
    base = _make_palette(0x123)
    base[0] = 0x000
    base[15] = BLOOD_RED

    state = PaletteFxState(base)
    state.trigger_lightning_flash(duration_vbls=2, color=WHITE, indices=(0, 1))
    assert state.is_flashing is True
    assert state.current_palette()[0:3] == (WHITE, WHITE, 0x123)
    assert state.base_palette[0:2] == (0x000, 0x123)

    # VBL 1: still flashing (1 VBL remaining)
    pal = state.step_vbl()
    assert state.is_flashing is True
    assert pal[0:2] == (WHITE, WHITE)

    # VBL 2: flash expires and restores cleanly to base palette
    pal = state.step_vbl()
    assert state.is_flashing is False
    assert pal == tuple(base)

    # Blood flash via mapping + manual clear_flash
    state.trigger_flash({0: 0x800, 15: 0xF00}, duration_vbls=3)
    assert state.current_palette()[0] == 0x800
    assert state.current_palette()[15] == 0xF00
    state.clear_flash()
    assert state.is_flashing is False
    assert state.current_palette() == tuple(base)

    # Blood flash helper expires after 3 VBLs while fade continues underneath
    state.fade_out(divisor=1)
    state.trigger_blood_flash(duration_vbls=3)
    for _ in range(2):
        pal = state.step_vbl()
        assert state.is_flashing is True
        assert pal[0] == BLOOD_RED
    pal = state.step_vbl()
    assert state.is_flashing is False
    # Base entry 15 (0xA00) stepped down 3 times -> 0x700
    assert pal[15] == 0x700


def test_set_palette_and_set_color_and_validation_errors() -> None:
    state = PaletteFxState()
    state.set_color(6, 0xC22)
    assert state.current_palette()[6] == 0xC22

    new_pal = _make_palette(0x444)
    state.fade_out()
    assert state.is_fading is True
    state.set_palette(new_pal)
    assert state.is_fading is False
    assert state.current_palette() == tuple(new_pal)

    with pytest.raises(ValueError, match="out of range"):
        state.set_color(32, 0x000)
    with pytest.raises(ValueError, match="period_vbls"):
        CycleRange(0, 3, period_vbls=0)
    with pytest.raises(ValueError, match="invalid cycle range"):
        CycleRange(5, 2)
    with pytest.raises(ValueError, match="direction"):
        CycleRange(0, 3, direction=0)
    with pytest.raises(ValueError, match="remaining_vbls"):
        FlashOverride(((0, WHITE),), remaining_vbls=0)
    with pytest.raises(ValueError, match="out of range"):
        FlashOverride(((32, WHITE),), remaining_vbls=1)


def test_palette_fx_module_uses_zero_floats_and_zero_true_division() -> None:
    src = Path("src/pymoonstone/sim/palette_fx.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        assert not (isinstance(node, ast.Constant) and isinstance(node.value, float | complex))
        assert not isinstance(node, ast.Div)
