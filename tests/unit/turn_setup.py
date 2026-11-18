"""Fabricated calendar/gift/flight tables for headless tests (§11.19)."""

from harddaysengine.sim.turn_rules import GiftRules, MapDragonRules, TextLine, TurnRules

GIFTS = GiftRules(
    ((33, 6), (66, 0), (100, 2)), (70, 71, 72) * 3, (20, 60, 95), tuple(range(0, 24, 2))
)
DRAGON = MapDragonRules(
    2,
    ((4, 2, 20), (6, 2, 0), (8, 2, 40), (10, 1, 3), (77, 1, 40), (12, 1, 0)),
    tuple(100 + i * 8 for i in range(16)),
    124,
    (20, 0, 30),
    3,
    2,
    80,
    40,
    300,
    295,
    65526,
    65531,
    180,
    0,
    3,
)
TURN_RULES = TurnRules(
    tuple(range(40, 48)),
    (TextLine("New Round", 0, 90, 1),),
    (TextLine("Farewell", 0, 90, 1),),
    320,
    110,
    10,
    DRAGON,
    GIFTS,
    ((73, 1, 1), (54, 4, 5), (77, 1, 20), (96, 4, 5)),
)
