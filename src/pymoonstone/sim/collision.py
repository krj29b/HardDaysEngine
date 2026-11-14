"""Pure-integer 2.5D depth, hurtbox/hitbox, and arena obstacle collision (ARCHITECTURE.md §4)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pymoonstone.sim.types import EntityId, Pos25D, Rect2D

if TYPE_CHECKING:
    from collections.abc import Sequence

DEFAULT_DEPTH_THRESHOLD: int = 10
OBSTACLE_DEPTH_BAND: int = 6

ARENA_MIN_X: int = 10
ARENA_MAX_X: int = 320
ARENA_MIN_Y: int = 30
ARENA_MAX_Y: int = 155

DRAW_FLAG_HURTBOX: int = 0x01
DRAW_FLAG_ATTACK: int = 0x02
DRAW_FLAG_NO_BODY_BOX: int = 0x40


@dataclass(slots=True, frozen=True)
class CelMask:
    """1-bit opacity/hurtbox mask for a cel frame (ARCHITECTURE.md §3.2, §4.2).

    Each entry in ``rows`` is an integer bitmask for one scanline, where bit ``x``
    (``(rows[y] >> x) & 1``) is ``1`` when pixel ``(x, y)`` has at least one
    non-zero bitplane.
    """

    width: int
    height: int
    rows: tuple[int, ...]

    @classmethod
    def empty(cls) -> CelMask:
        """Return an empty ``0x0`` mask."""
        return cls(width=0, height=0, rows=())

    @classmethod
    def from_pattern(cls, lines: Sequence[str], *, opaque_char: str = "#") -> CelMask:
        """Build a mask from ASCII rows (e.g. ``('#..', '.#.')``) for deterministic tests."""
        if not lines:
            return cls.empty()
        height = len(lines)
        width = max(len(line) for line in lines)
        rows: list[int] = []
        for line in lines:
            bits = 0
            for x, ch in enumerate(line):
                if ch == opaque_char:
                    bits |= 1 << x
            rows.append(bits)
        return cls(width=width, height=height, rows=tuple(rows))

    @classmethod
    def from_bitplanes(cls, width: int, height: int, planes: Sequence[bytes]) -> CelMask:
        """Build a mask by ORing Amiga MSB-first word-aligned bitplanes (ARCHITECTURE.md §3.2)."""
        if width <= 0 or height <= 0:
            return cls.empty()
        row_bytes = ((width + 15) >> 4) * 2
        expected_size = row_bytes * height
        for plane in planes:
            if len(plane) < expected_size:
                msg = f"Bitplane buffer too short: expected >= {expected_size}, got {len(plane)}"
                raise ValueError(msg)

        rows: list[int] = []
        for y in range(height):
            row_offset = y * row_bytes
            row_bits = 0
            for x in range(width):
                byte_idx = row_offset + (x >> 3)
                bit_shift = 7 - (x & 7)
                if any((plane[byte_idx] >> bit_shift) & 1 for plane in planes):
                    row_bits |= 1 << x
            rows.append(row_bits)
        return cls(width=width, height=height, rows=tuple(rows))

    def test_pixel(self, local_x: int, local_y: int, *, flipped: bool = False) -> bool:
        """Return True if cel-local ``(local_x, local_y)`` hits an opaque bitplane pixel."""
        if local_x < 0 or local_x >= self.width or local_y < 0 or local_y >= self.height:
            return False
        sample_x = (self.width - 1 - local_x) if flipped else local_x
        return bool((self.rows[local_y] >> sample_x) & 1)

    def outer_bounds(self, screen_x: int = 0, screen_y: int = 0) -> Rect2D:
        """Return the full cel bounding rectangle at ``(screen_x, screen_y)``."""
        return Rect2D(screen_x, screen_y, self.width, self.height)

    def tight_bounds(
        self,
        screen_x: int = 0,
        screen_y: int = 0,
        *,
        flipped: bool = False,
    ) -> Rect2D:
        """Return the tightest AABB enclosing all opaque pixels at ``(screen_x, screen_y)``."""
        if self.width <= 0 or self.height <= 0:
            return Rect2D(screen_x, screen_y, 0, 0)

        spans = [
            (y, _row_bit_span(row_bits, self.width))
            for y, row_bits in enumerate(self.rows)
            if row_bits
        ]
        if not spans:
            return Rect2D(screen_x, screen_y, 0, 0)

        min_y = spans[0][0]
        max_y = spans[-1][0]
        min_x = min(span[0] for _, span in spans)
        max_x = max(span[1] for _, span in spans)
        if flipped:
            min_x, max_x = self.width - 1 - max_x, self.width - 1 - min_x
        return Rect2D(
            screen_x + min_x,
            screen_y + min_y,
            max_x - min_x + 1,
            max_y - min_y + 1,
        )


def _row_bit_span(row_bits: int, width: int) -> tuple[int, int]:
    """Return ``(min_x, max_x)`` of set bits in a non-zero row bitmask."""
    masked = row_bits & ((1 << width) - 1)
    min_x = (masked & -masked).bit_length() - 1
    max_x = masked.bit_length() - 1
    return min_x, max_x


@dataclass(slots=True, frozen=True)
class AttackPoint:
    """Single weapon attack point in cel-local coordinates (ARCHITECTURE.md §4.2)."""

    x: int
    y: int


@dataclass(slots=True, frozen=True)
class AttackPattern:
    """Per-frame ``collide.hit`` attack-point set and coarse bounds (ARCHITECTURE.md §4.2)."""

    hit_type: int
    max_x: int
    max_y: int
    points: tuple[AttackPoint, ...]

    @classmethod
    def from_points(
        cls,
        points: Sequence[tuple[int, int]],
        *,
        hit_type: int = 1,
    ) -> AttackPattern:
        """Construct an ``AttackPattern``, computing ``max_x`` and ``max_y`` from points."""
        if not points:
            return cls(hit_type=hit_type, max_x=0, max_y=0, points=())
        pts = tuple(AttackPoint(x, y) for x, y in points)
        max_x = max(pt.x for pt in pts)
        max_y = max(pt.y for pt in pts)
        return cls(hit_type=hit_type, max_x=max_x, max_y=max_y, points=pts)


@dataclass(slots=True, frozen=True)
class HurtEntry:
    """Registered defender hurt cel for the current tick (``draw_flags & 1``)."""

    entity_id: EntityId
    pos: Pos25D
    screen_x: int
    screen_y: int
    mask: CelMask
    flipped: bool = False
    draw_flags: int = DRAW_FLAG_HURTBOX

    @property
    def is_active(self) -> bool:
        """True when ``DRAW_FLAG_HURTBOX`` (``draw_flags & 1``) is set."""
        return bool(self.draw_flags & DRAW_FLAG_HURTBOX)

    @property
    def outer_rect(self) -> Rect2D:
        """Full cel bounding rectangle in screen coordinates."""
        return self.mask.outer_bounds(self.screen_x, self.screen_y)

    @property
    def tight_rect(self) -> Rect2D:
        """Tight opaque-pixel bounding rectangle in screen coordinates."""
        return self.mask.tight_bounds(self.screen_x, self.screen_y, flipped=self.flipped)


@dataclass(slots=True, frozen=True)
class AttackEntry:
    """Registered attacker weapon cel for the current tick (``draw_flags & 2``)."""

    entity_id: EntityId
    pos: Pos25D
    screen_x: int
    screen_y: int
    cel_width: int
    cel_height: int
    pattern: AttackPattern
    flipped: bool = False
    draw_flags: int = DRAW_FLAG_ATTACK

    @property
    def is_active(self) -> bool:
        """True when ``DRAW_FLAG_ATTACK`` (``draw_flags & 2``) is set."""
        return bool(self.draw_flags & DRAW_FLAG_ATTACK)


@dataclass(slots=True, frozen=True)
class HitResult:
    """Resolved weapon-point vs bitplane-hurtbox intersection."""

    attacker_id: EntityId
    defender_id: EntityId
    hit_x: int
    hit_y: int
    hit_type: int
    point_index: int


@dataclass(slots=True, frozen=True)
class ObstacleSegment:
    """Horizontal `.t` arena obstacle wall segment (ARCHITECTURE.md §4.3)."""

    x1: int
    x2: int
    y: int

    @property
    def min_x(self) -> int:
        """Leftmost X coordinate of the segment."""
        return min(self.x1, self.x2)

    @property
    def max_x(self) -> int:
        """Rightmost X coordinate of the segment."""
        return max(self.x1, self.x2)


@dataclass(slots=True, frozen=True)
class ArenaBounds:
    """Walkable 2.5D arena boundary limits (ARCHITECTURE.md §4.1)."""

    min_x: int = ARENA_MIN_X
    max_x: int = ARENA_MAX_X
    min_y: int = ARENA_MIN_Y
    max_y: int = ARENA_MAX_Y

    def clamp(self, pos: Pos25D) -> Pos25D:
        """Clamp ``pos`` to ``[min_x, max_x] x [min_y, max_y]``, preserving ``z``."""
        clamped_x = max(self.min_x, min(self.max_x, pos.x))
        clamped_y = max(self.min_y, min(self.max_y, pos.y))
        return Pos25D(clamped_x, clamped_y, pos.z)


DEFAULT_ARENA_BOUNDS = ArenaBounds()


def depth_close(y_a: int, y_b: int, threshold: int = DEFAULT_DEPTH_THRESHOLD) -> bool:
    """Return True if ground depths pass the 2.5D depth gate (ARCHITECTURE.md §4.1)."""
    return abs(y_a - y_b) <= threshold


def positions_depth_close(
    pos_a: Pos25D,
    pos_b: Pos25D,
    threshold: int = DEFAULT_DEPTH_THRESHOLD,
) -> bool:
    """Return True if two 2.5D positions are within ``threshold`` pixels in ground depth."""
    return pos_a.depth_distance(pos_b) <= threshold


def expand_body_box(current: Rect2D | None, cel_rect: Rect2D, draw_flags: int) -> Rect2D | None:
    """Expand an actor's union body box unless ``DRAW_FLAG_NO_BODY_BOX`` is set (§4.2)."""
    if draw_flags & DRAW_FLAG_NO_BODY_BOX:
        return current
    if cel_rect.width <= 0 or cel_rect.height <= 0:
        return current
    if current is None:
        return cel_rect
    return Rect2D.from_bounds(
        min(current.x, cel_rect.x),
        min(current.y, cel_rect.y),
        max(current.right, cel_rect.right),
        max(current.bottom, cel_rect.bottom),
    )


def bodies_blocked(
    box_a: Rect2D,
    pos_a: Pos25D,
    box_b: Rect2D,
    pos_b: Pos25D,
    depth_threshold: int = DEFAULT_DEPTH_THRESHOLD,
) -> bool:
    """Return True if two actors physically block each other in depth and screen X/Y (§4.2)."""
    return positions_depth_close(pos_a, pos_b, depth_threshold) and box_a.intersects(box_b)


def attack_coarse_rect(attack: AttackEntry) -> Rect2D:
    """Compute the coarse AABB for an attack cel, accounting for horizontal mirroring (§4.2)."""
    if not attack.pattern.points:
        return Rect2D(attack.screen_x, attack.screen_y, 0, 0)
    box_x = (
        attack.screen_x + (attack.cel_width - attack.pattern.max_x)
        if attack.flipped
        else attack.screen_x
    )
    return Rect2D(
        box_x,
        attack.screen_y,
        attack.pattern.max_x + 1,
        attack.pattern.max_y + 1,
    )


def attack_point_screen(attack: AttackEntry, point: AttackPoint) -> tuple[int, int]:
    """Transform a cel-local attack point into screen coordinates, mirroring if flipped (§4.2)."""
    local_x = (attack.cel_width - point.x) if attack.flipped else point.x
    return attack.screen_x + local_x, attack.screen_y + point.y


def check_attack_vs_hurt(
    attack: AttackEntry,
    hurt: HurtEntry,
    depth_threshold: int = DEFAULT_DEPTH_THRESHOLD,
) -> HitResult | None:
    """Test one attack cel against one hurt cel using depth gate, coarse AABB, and bitplane mask."""
    if not attack.is_active or not hurt.is_active:
        return None
    if not attack.pattern.points or attack.entity_id == hurt.entity_id:
        return None
    if not depth_close(attack.pos.y, hurt.pos.y, depth_threshold):
        return None

    def_rect = hurt.outer_rect
    if not def_rect.intersects(attack_coarse_rect(attack)):
        return None

    for idx, pt in enumerate(attack.pattern.points):
        sx, sy = attack_point_screen(attack, pt)
        if not def_rect.contains(sx, sy):
            continue
        if hurt.mask.test_pixel(sx - hurt.screen_x, sy - hurt.screen_y, flipped=hurt.flipped):
            return HitResult(
                attacker_id=attack.entity_id,
                defender_id=hurt.entity_id,
                hit_x=sx,
                hit_y=sy,
                hit_type=attack.pattern.hit_type,
                point_index=idx,
            )
    return None


def resolve_combat_hits(
    attacks: Sequence[AttackEntry],
    hurts: Sequence[HurtEntry],
    depth_threshold: int = DEFAULT_DEPTH_THRESHOLD,
) -> list[HitResult]:
    """Resolve all active attack cels against all active hurt cels in deterministic order.

    At most one hit is recorded per ``(attacker_id, defender_id)`` pair per call,
    while allowing multi-target hits across distinct defenders.
    """
    hits: list[HitResult] = []
    seen_pairs: set[tuple[EntityId, EntityId]] = set()
    for attack in attacks:
        if not attack.is_active:
            continue
        for hurt in hurts:
            pair = (attack.entity_id, hurt.entity_id)
            if pair in seen_pairs or not hurt.is_active:
                continue
            result = check_attack_vs_hurt(attack, hurt, depth_threshold=depth_threshold)
            if result is not None:
                seen_pairs.add(pair)
                hits.append(result)
    return hits


def resolve_arena_movement(
    pos: Pos25D,
    dx: int,
    dy: int,
    obstacles: Sequence[ObstacleSegment],
    *,
    bounds: ArenaBounds = DEFAULT_ARENA_BOUNDS,
    obstacle_depth_band: int = OBSTACLE_DEPTH_BAND,
) -> Pos25D:
    """Apply ``(dx, dy)`` movement clamped by `.t` obstacle walls and arena bounds (§4.1, §4.3)."""
    target_y = _resolve_vertical_obstacles(pos.x, pos.y, pos.y + dy, obstacles)
    target_x = _resolve_horizontal_obstacles(
        pos.x, pos.x + dx, target_y, obstacles, obstacle_depth_band
    )
    return bounds.clamp(Pos25D(target_x, target_y, pos.z))


def _resolve_vertical_obstacles(
    x: int,
    start_y: int,
    target_y: int,
    obstacles: Sequence[ObstacleSegment],
) -> int:
    """Block vertical crossing of obstacle segments whose X span contains ``x``."""
    if target_y == start_y:
        return target_y
    resolved_y = target_y
    for obs in obstacles:
        if not (obs.min_x <= x <= obs.max_x):
            continue
        if start_y < obs.y <= resolved_y:
            resolved_y = obs.y - 1
        elif resolved_y <= obs.y < start_y:
            resolved_y = obs.y + 1
    return resolved_y


def _resolve_horizontal_obstacles(
    start_x: int,
    target_x: int,
    depth_y: int,
    obstacles: Sequence[ObstacleSegment],
    depth_band: int,
) -> int:
    """Block horizontal entry into obstacle X spans when ``|depth_y - obs.y| <= depth_band``."""
    if target_x == start_x:
        return target_x
    resolved_x = target_x
    for obs in obstacles:
        if abs(depth_y - obs.y) > depth_band:
            continue
        if start_x < obs.min_x <= resolved_x:
            resolved_x = obs.min_x - 1
        elif resolved_x <= obs.max_x < start_x:
            resolved_x = obs.max_x + 1
    return resolved_x
