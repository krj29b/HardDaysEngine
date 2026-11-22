"""50 Hz PAL 12-bit ``$0RGB`` palette & VBL effect state machine (ARCHITECTURE.md §8)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass

PALETTE_SIZE = 32
NIBBLE_MAX = 0xF
COLOR_MASK = 0x0FFF
BLACK = 0x000
WHITE = 0xFFF
BLOOD_RED = 0xA00
FADE_MAX_STEPS = 15


def validate_color(color: int) -> int:
    """Validate that ``color`` is a 12-bit ``$0RGB`` integer in ``0x000..0xFFF``."""
    if not 0 <= color <= COLOR_MASK:
        raise ValueError(f"12-bit $0RGB color out of range: {color:#x}")
    return color


def validate_palette(colors: Sequence[int]) -> tuple[int, ...]:
    """Validate a 32-entry ``$0RGB`` palette and return it as an immutable tuple."""
    if len(colors) != PALETTE_SIZE:
        raise ValueError(f"expected {PALETTE_SIZE} palette entries, got {len(colors)}")
    return tuple(validate_color(c) for c in colors)


def split_rgb12(color: int) -> tuple[int, int, int]:
    """Extract 4-bit ``(r, g, b)`` nibbles (each ``0..15``) from a 12-bit ``$0RGB`` word."""
    validate_color(color)
    return (color >> 8) & NIBBLE_MAX, (color >> 4) & NIBBLE_MAX, color & NIBBLE_MAX


def pack_rgb12(r: int, g: int, b: int) -> int:
    """Pack 4-bit ``(r, g, b)`` nibbles (clamped to ``0..15``) into a 12-bit ``$0RGB`` word."""
    cr = max(0, min(NIBBLE_MAX, r))
    cg = max(0, min(NIBBLE_MAX, g))
    cb = max(0, min(NIBBLE_MAX, b))
    return (cr << 8) | (cg << 4) | cb


def rgb12_to_rgb24(color: int) -> tuple[int, int, int]:
    """Expand a 12-bit ``$0RGB`` word to 8-bit ``(R, G, B)`` via nibble replication."""
    r, g, b = split_rgb12(color)
    return (r << 4) | r, (g << 4) | g, (b << 4) | b


def step_nibble(current: int, target: int) -> int:
    """Advance a single 4-bit colour nibble by ``±1`` toward ``target``."""
    if current < target:
        return current + 1
    if current > target:
        return current - 1
    return current


def step_color(current: int, target: int) -> int:
    """Step each ``R, G, B`` nibble of ``current`` by ``±1`` toward ``target``."""
    cr, cg, cb = split_rgb12(current)
    tr, tg, tb = split_rgb12(target)
    return pack_rgb12(step_nibble(cr, tr), step_nibble(cg, tg), step_nibble(cb, tb))


def cycle_step(
    palette: Sequence[int],
    start: int,
    end: int,
    direction: int = 1,
) -> tuple[int, ...]:
    """Rotate palette entries over the inclusive range ``[start..end]`` (ARCHITECTURE.md §8.2).

    ``direction == 1`` shifts entries forward (``end`` wraps to ``start``);
    ``direction == -1`` shifts entries backward (``start`` wraps to ``end``).
    """
    validated = validate_palette(palette)
    if not 0 <= start <= end < PALETTE_SIZE:
        raise ValueError(f"invalid cycle range [{start}..{end}] for {PALETTE_SIZE} entries")
    if direction not in (-1, 1):
        raise ValueError(f"direction must be 1 or -1, got {direction}")
    if start == end:
        return validated
    out = list(validated)
    if direction == 1:
        out[start : end + 1] = [out[end], *out[start:end]]
    else:
        out[start : end + 1] = [*out[start + 1 : end + 1], out[start]]
    return tuple(out)


@dataclass(slots=True, frozen=True)
class CycleRange:
    """Configuration for a VBL-driven colour-cycling range (ARCHITECTURE.md §8.2)."""

    start: int
    end: int
    period_vbls: int = 1
    direction: int = 1

    def __post_init__(self) -> None:
        if not 0 <= self.start <= self.end < PALETTE_SIZE:
            raise ValueError(
                f"invalid cycle range [{self.start}..{self.end}] for {PALETTE_SIZE} entries"
            )
        if self.period_vbls < 1:
            raise ValueError(f"period_vbls must be >= 1, got {self.period_vbls}")
        if self.direction not in (-1, 1):
            raise ValueError(f"direction must be 1 or -1, got {self.direction}")


@dataclass(slots=True)
class _ActiveCycle:
    spec: CycleRange
    tick_counter: int = 0


@dataclass(slots=True, frozen=True)
class FlashOverride:
    """Temporary VBL-timed palette override for lightning/hit flashes (ARCHITECTURE.md §8.2)."""

    overrides: tuple[tuple[int, int], ...]
    remaining_vbls: int

    def __post_init__(self) -> None:
        if self.remaining_vbls < 1:
            raise ValueError(f"remaining_vbls must be >= 1, got {self.remaining_vbls}")
        for index, color in self.overrides:
            if not 0 <= index < PALETTE_SIZE:
                raise ValueError(f"palette index out of range: {index}")
            validate_color(color)


class PaletteFxState:
    """Deterministic 50 Hz PAL palette & VBL effect state machine (ARCHITECTURE.md §8.2).

    Manages a 32-entry 12-bit ``$0RGB`` palette supporting per-VBL or N-VBL divisor
    nibble fading, multi-range colour cycling, and temporary lightning/blood flash
    overrides that restore cleanly to the underlying base/faded palette.
    """

    __slots__ = (
        "_base",
        "_cycles",
        "_fade_divisor",
        "_fade_target",
        "_fade_tick",
        "_fading",
        "_flash",
        "_vbl_count",
    )

    def __init__(self, initial_palette: Sequence[int] | None = None) -> None:
        if initial_palette is None:
            self._base: list[int] = [BLACK] * PALETTE_SIZE
        else:
            self._base = list(validate_palette(initial_palette))
        self._fade_target: list[int] = list(self._base)
        self._fade_divisor: int = 1
        self._fade_tick: int = 0
        self._fading: bool = False
        self._cycles: list[_ActiveCycle] = []
        self._flash: FlashOverride | None = None
        self._vbl_count: int = 0

    def snapshot(self) -> dict[str, object]:
        """Replay state of fades, cycles and flashes, including countdowns (§11.19)."""
        return {
            "base": self._base[:],
            "target": self._fade_target[:],
            "divisor": self._fade_divisor,
            "tick": self._fade_tick,
            "fading": self._fading,
            "cycles": [asdict(cycle) for cycle in self._cycles],
            "flash": None if self._flash is None else asdict(self._flash),
            "vbl": self._vbl_count,
        }

    @classmethod
    def from_checkpoint(
        cls,
        base: Sequence[int],
        target: Sequence[int],
        *,
        fade: tuple[int, int, bool],
        cycles: Sequence[tuple[CycleRange, int]],
        flash: FlashOverride | None,
        vbl: int,
    ) -> PaletteFxState:
        """Restore palette cells at a completed host tick (§13.11)."""
        state = cls(base)
        state._fade_target = list(validate_palette(target))
        state._fade_divisor, state._fade_tick, state._fading = fade
        state._cycles = [_ActiveCycle(spec, counter) for spec, counter in cycles]
        state._flash, state._vbl_count = flash, vbl
        return state

    @property
    def vbl_count(self) -> int:
        """Total 50 Hz VBL ticks elapsed."""
        return self._vbl_count

    @property
    def is_fading(self) -> bool:
        """True while a fade transition toward a target palette is active."""
        return self._fading

    @property
    def is_flashing(self) -> bool:
        """True while a temporary flash override is active."""
        return self._flash is not None

    @property
    def base_palette(self) -> tuple[int, ...]:
        """Current 32-entry ``$0RGB`` base palette without flash overrides."""
        return tuple(self._base)

    def current_palette(self) -> tuple[int, ...]:
        """Return the effective 32-entry ``$0RGB`` palette for the current VBL."""
        if self._flash is None:
            return tuple(self._base)
        out = list(self._base)
        for index, color in self._flash.overrides:
            out[index] = color
        return tuple(out)

    def set_palette(self, palette: Sequence[int]) -> None:
        """Immediately set the 32-entry base palette and cancel any active fade."""
        validated = validate_palette(palette)
        self._base = list(validated)
        self._fade_target = list(validated)
        self._fade_tick = 0
        self._fading = False

    def set_color(self, index: int, color: int) -> None:
        """Immediately set a single base palette entry ``0..31``."""
        if not 0 <= index < PALETTE_SIZE:
            raise ValueError(f"palette index out of range: {index}")
        validated = validate_color(color)
        self._base[index] = validated
        if not self._fading:
            self._fade_target[index] = validated

    def fade_to(self, target: Sequence[int], divisor: int = 1) -> None:
        """Step each nibble of the base palette toward ``target`` every ``divisor`` VBLs."""
        if divisor < 1:
            raise ValueError(f"fade divisor must be >= 1, got {divisor}")
        validated = validate_palette(target)
        self._fade_target = list(validated)
        self._fade_divisor = divisor
        self._fade_tick = 0
        self._fading = self._base != self._fade_target

    def fade_in(
        self,
        target: Sequence[int],
        divisor: int = 1,
        *,
        from_black: bool = True,
    ) -> None:
        """Fade in to ``target`` (resetting base to black first when ``from_black`` is True)."""
        validated = validate_palette(target)
        if from_black:
            self._base = [BLACK] * PALETTE_SIZE
        self.fade_to(validated, divisor=divisor)

    def fade_out(self, divisor: int = 1) -> None:
        """Fade the current base palette to all-black (``0x000``)."""
        self.fade_to([BLACK] * PALETTE_SIZE, divisor=divisor)

    def add_cycle(
        self,
        start: int,
        end: int,
        period_vbls: int = 1,
        direction: int = 1,
    ) -> int:
        """Register a colour-cycling range and return its slot index."""
        spec = CycleRange(start=start, end=end, period_vbls=period_vbls, direction=direction)
        self._cycles.append(_ActiveCycle(spec=spec))
        return len(self._cycles) - 1

    def clear_cycles(self) -> None:
        """Remove all active colour-cycling ranges."""
        self._cycles.clear()

    def trigger_flash(
        self,
        overrides: Mapping[int, int] | Sequence[tuple[int, int]],
        duration_vbls: int,
    ) -> None:
        """Activate a temporary VBL-timed palette override for ``duration_vbls`` ticks."""
        pairs = (
            tuple(overrides.items())
            if isinstance(overrides, Mapping)
            else tuple((idx, col) for idx, col in overrides)
        )
        self._flash = FlashOverride(overrides=pairs, remaining_vbls=duration_vbls)

    def trigger_lightning_flash(
        self,
        duration_vbls: int = 2,
        color: int = WHITE,
        indices: Sequence[int] = (0,),
    ) -> None:
        """Trigger a lightning flash override on the given palette indices."""
        self.trigger_flash([(idx, color) for idx in indices], duration_vbls)

    def trigger_blood_flash(
        self,
        duration_vbls: int = 3,
        color: int = BLOOD_RED,
        indices: Sequence[int] = (0,),
    ) -> None:
        """Trigger a blood/hit flash override on the given palette indices."""
        self.trigger_flash([(idx, color) for idx in indices], duration_vbls)

    def clear_flash(self) -> None:
        """Cancel any active flash override immediately."""
        self._flash = None

    def step_vbl(self) -> tuple[int, ...]:
        """Advance all palette effects by one 50 Hz PAL VBL tick and return active palette."""
        self._vbl_count += 1
        self._advance_fade()
        self._advance_cycles()
        self._advance_flash()
        return self.current_palette()

    def _advance_fade(self) -> None:
        if not self._fading:
            return
        self._fade_tick += 1
        if self._fade_tick < self._fade_divisor:
            return
        self._fade_tick = 0
        for i in range(PALETTE_SIZE):
            self._base[i] = step_color(self._base[i], self._fade_target[i])
        if self._base == self._fade_target:
            self._fading = False

    def _advance_cycles(self) -> None:
        for cycle in self._cycles:
            cycle.tick_counter += 1
            if cycle.tick_counter < cycle.spec.period_vbls:
                continue
            cycle.tick_counter = 0
            self._base = list(
                cycle_step(
                    self._base,
                    cycle.spec.start,
                    cycle.spec.end,
                    cycle.spec.direction,
                )
            )
            if self._fading:
                self._fade_target = list(
                    cycle_step(
                        self._fade_target,
                        cycle.spec.start,
                        cycle.spec.end,
                        cycle.spec.direction,
                    )
                )

    def _advance_flash(self) -> None:
        if self._flash is None:
            return
        remaining = self._flash.remaining_vbls - 1
        if remaining <= 0:
            self._flash = None
        else:
            self._flash = FlashOverride(overrides=self._flash.overrides, remaining_vbls=remaining)
