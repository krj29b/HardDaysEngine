"""Explicit synthetic map-AI prices and town targets (§11.20)."""

from pymoonstone.sim.ai_map_state import MapAiRules

AI_RULES = MapAiRules(
    engage=25,
    stat_price=3,
    rest_lives=3,
    rest_cap_test=6,
    rest_cap=5,
    minimum_gold=10,
    life_price=26,
    life_threshold=2,
    armour=((77, 30), (51, 29), (31, 28)),
    claymore=(27, 24),
    broad=(11, 23),
    dagger_price=2,
    dagger_threshold=5,
    dagger_cap=10,
    towns=((11, 6, 90, 40), (36, 19, 290, 150)),
)
