"""The knight handler: joystick, fire, walking (ARCHITECTURE.md §10.19)."""

from __future__ import annotations

import pytest

from harddaysengine.sim.fight import FightError, Restart, Wall
from harddaysengine.sim.fight_routines import DAGGER_THROW, DAGGER_TYPE, HOP, HOP_STEP
from harddaysengine.sim.fighters import KNIGHT_TYPE, knight_handler
from tests.unit.knight_setup import RULES, TABLES, make_fight

FIRE, RIGHT, LEFT, DOWN, UP = 0x10, 0x01, 0x02, 0x04, 0x08


def _run(word: int, handle: int = 1, port: int = 0) -> tuple[Restart, int]:
    fight, _ = make_fight()
    fight.ports[port] = word
    result = knight_handler(RULES, fight, handle)
    assert isinstance(result, Restart)
    return result, handle


def test_install_registers_handlers_and_routines() -> None:
    fight, _ = make_fight()
    assert {KNIGHT_TYPE, DAGGER_TYPE} <= set(fight.handlers)
    assert {DAGGER_THROW, HOP, HOP_STEP} <= set(fight.routines)


def test_no_input_plays_idle_and_clears_the_action() -> None:
    fight, _ = make_fight()
    knight = fight.actor(1)
    knight.action = 8
    result = knight_handler(RULES, fight, 1)
    assert result == Restart(TABLES.idle_script, 100, 0, 100, 1)
    assert (knight.action, knight.input) == (0, 0)


def test_each_knight_reads_its_own_port() -> None:
    fight, _ = make_fight()
    fight.ports[1] = FIRE | RIGHT
    assert knight_handler(RULES, fight, 1) == Restart(TABLES.idle_script, 100, 0, 100, 1)
    result = knight_handler(RULES, fight, 2)
    action = RULES.actions_left[RIGHT]
    assert fight.actor(2).action == action
    assert isinstance(result, Restart)
    assert result.script == TABLES.action_scripts[action // 4]


@pytest.mark.parametrize("direction", [0, RIGHT, LEFT, UP, DOWN, UP | RIGHT, DOWN | LEFT])
def test_fire_picks_the_action_of_the_facing_table(direction: int) -> None:
    fight, _ = make_fight()
    fight.ports[0] = FIRE | direction
    result = knight_handler(RULES, fight, 1)
    action = RULES.actions_right[direction]
    assert fight.actor(1).action == action
    assert result == Restart(TABLES.action_scripts[action // 4], 100, 0, 100, 1)


@pytest.mark.parametrize(
    "direction",
    [
        LEFT | RIGHT,
        DOWN | LEFT | RIGHT,
        UP | LEFT | RIGHT,
        UP | DOWN,
        UP | DOWN | RIGHT,
        UP | DOWN | LEFT,
        UP | DOWN | LEFT | RIGHT,
    ],
)
@pytest.mark.parametrize("facing", [1, 3])
def test_opposing_or_out_of_range_fire_inputs_fall_back_to_idle_action(
    direction: int, facing: int
) -> None:
    fight, _ = make_fight()
    fight.actor(1).facing = facing
    fight.ports[0] = FIRE | direction
    result = knight_handler(RULES, fight, 1)
    assert fight.actor(1).action == 0
    assert result == Restart(TABLES.action_scripts[0], 100, 0, 100, facing)


def test_an_action_outside_the_table_raises() -> None:
    fight, _ = make_fight()
    fight.actor(1).facing = 3
    fight.ports[0] = FIRE | RIGHT
    fight.tables[fight.actor(1).action_scripts] = [0] * 4
    with pytest.raises(FightError, match="outside the table"):
        knight_handler(RULES, fight, 1)


def test_a_joystick_word_with_other_bits_raises() -> None:
    fight, _ = make_fight()
    fight.ports[0] = 0x20
    with pytest.raises(FightError, match="joystick word"):
        knight_handler(RULES, fight, 1)


def test_the_confused_knight_has_both_axes_swapped() -> None:
    fight, _ = make_fight()
    fight.confused, fight.confused_on = 1, True
    fight.ports[0] = FIRE | UP | RIGHT
    knight_handler(RULES, fight, 1)
    assert fight.actor(1).input == FIRE | DOWN | LEFT
    assert fight.actor(1).action == RULES.actions_right[DOWN | LEFT]
    fight.ports[0] = FIRE | UP
    knight_handler(RULES, fight, 1)
    assert fight.actor(1).input == FIRE | DOWN
    fight.confused_on = False
    knight_handler(RULES, fight, 1)
    assert fight.actor(1).input == FIRE | UP


def test_walking_right_steps_by_the_new_phase_and_plays_its_walk_script() -> None:
    fight, _ = make_fight()
    knight = fight.actor(1)
    knight.anim_phase, knight.block_latched = 1, True
    fight.ports[0] = RIGHT
    result = knight_handler(RULES, fight, 1)
    step = RULES.steps.side[2]
    assert (knight.anim_phase, knight.x, knight.facing) == (2, 100 + step, 1)
    assert not knight.block_latched
    assert result == Restart(TABLES.walk_scripts[2], 100 + step, 0, 100, 1)


def test_walking_left_turns_the_knight() -> None:
    fight, _ = make_fight()
    knight = fight.actor(1)
    fight.ports[0] = LEFT
    knight_handler(RULES, fight, 1)
    assert (knight.facing, knight.x) == (3, 100 - RULES.steps.side[1])


@pytest.mark.parametrize(
    ("word", "dy", "block"),
    [(UP, -RULES.steps.up[1], 32), (DOWN, RULES.steps.down[1], 64)],
)
def test_walking_in_depth_uses_the_up_and_down_blocks(word: int, dy: int, block: int) -> None:
    fight, _ = make_fight()
    fight.ports[0] = word
    result = knight_handler(RULES, fight, 1)
    assert fight.actor(1).y == 100 + dy
    assert isinstance(result, Restart)
    assert result.script == TABLES.walk_scripts[(block + 4) // 4]


def test_a_sideways_bit_selects_the_sideways_block() -> None:
    fight, _ = make_fight()
    fight.ports[0] = UP | RIGHT
    result = knight_handler(RULES, fight, 1)
    knight = fight.actor(1)
    assert (knight.x, knight.y) == (100 + RULES.steps.side[1], 100 - RULES.steps.up[1])
    assert isinstance(result, Restart)
    assert result.script == TABLES.walk_scripts[1]


def test_a_blocked_walk_restores_the_phase_and_idles() -> None:
    fight, _ = make_fight()
    knight = fight.actor(1)
    knight.x, knight.anim_phase = 290, 3
    fight.ports[0] = RIGHT
    result = knight_handler(RULES, fight, 1)
    assert (knight.x, knight.anim_phase, knight.input) == (290, 3, 0)
    assert result == Restart(TABLES.idle_script, 290, 0, 100, 1)


def test_a_wall_blocks_the_walk() -> None:
    fight, _ = make_fight()
    knight = fight.actor(1)
    knight.box_min_x, knight.box_max_x, knight.box_min_y, knight.box_max_y = 90, 120, 60, 100
    fight.walls = (Wall(110, 140, 120),)
    fight.ports[0] = RIGHT
    knight_handler(RULES, fight, 1)
    assert knight.x == 100


def test_hurt_and_hit_come_before_the_joystick() -> None:
    fight, _ = make_fight()
    fight.ports[0] = RIGHT
    knight, other = fight.actor(1), fight.actor(2)
    other.action = 4
    knight.attacker = 2
    result = knight_handler(RULES, fight, 1)
    assert isinstance(result, Restart)
    assert result.script == TABLES.hurt_scripts[1]
    knight.attacker, knight.hit_target = 0, 2
    result = knight_handler(RULES, fight, 1)
    assert isinstance(result, Restart)
    assert result.script == TABLES.after_hit_script
    assert knight.x == 100
