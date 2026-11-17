"""Invented loop operands for original fight setup tests (§10.24)."""

from pymoonstone.sim.fight_setup import FightSetupRules

SETUP = FightSetupRules(
    frame_vbls=4,
    enter_script=300,
    knight_x=240,
    knight_height=0,
    knight_facing=3,
    opponent_x=40,
    opponent_facing=1,
    lane_bottom=210,
    lane_offset=40,
    strength_limit=2,
    hp_limits=(25, 55, 85),
    class_base=5,
    class_limit=12,
    class_cap=11,
    death_delay=7,
    warning_hp=9,
    warning_period=2,
    warning_rows=(
        (0x222, 0x333, 0x444),
        (0x555, 0x666, 0x777),
        (0x888, 0x999, 0xAAA),
        (0xBBB, 0xCCC, 0xDDD),
        (0xEEE, 0xFFF, 0x111),
    ),
)
