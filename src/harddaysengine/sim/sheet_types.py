"""Sheet state and immutable geometry/rules (ARCHITECTURE.md §12.26)."""

from dataclasses import dataclass, field

from harddaysengine.sim.shop_types import ShopRules


@dataclass(slots=True, frozen=True)
class SheetRules:
    buttons: tuple[tuple[str | None, ...], ...]
    sizes: tuple[tuple[int, int], ...]
    panels: tuple[tuple[tuple[int, int, int, int], ...], ...]
    stat_price: int
    gold_cap: int
    rest_test: int
    rest_cap: int
    haste_limit: int
    hawk_limit: int
    protection_limit: int
    shop: ShopRules | None = None
    jingle_wait: int = 0


@dataclass(slots=True, frozen=True)
class SheetIcon:
    frame: int
    x: int
    y: int
    mirror: bool = False


@dataclass(slots=True, frozen=True)
class SheetText:
    text: str
    x: int
    y: int


@dataclass(slots=True, frozen=True)
class SheetRegion:
    x: int
    y: int
    width: int
    height: int
    button: int = 0
    side: int = 0
    type: int = 0
    mask: int = 0
    field: int = 0
    id: int = 0

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height


@dataclass(slots=True)
class SheetState:
    number: int
    knight: int
    other: int = 0
    lair: int = -1
    previous: int = 9
    pointer_x: int = 160
    pointer_y: int = 100
    changes: int = 0
    last_item: int = 65535
    done: bool = False
    hand_over: bool = False
    bad_luck: int = 0
    read_only: bool = False
    hover: str = ""
    wait: int = 0
    sounds: list[str] = field(default_factory=list)
    pending_sounds: list[str] = field(default_factory=list)
    icons: list[SheetIcon] = field(default_factory=list)
    texts: list[SheetText] = field(default_factory=list)
    regions: list[SheetRegion] = field(default_factory=list)
