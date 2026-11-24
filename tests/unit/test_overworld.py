"""Synthetic acceptance checks for the original pixel map (§11.18)."""

from dataclasses import replace

import pytest

from harddaysengine.sim.fight_moves import DOWN, FIRE, LEFT, RIGHT, UP
from harddaysengine.sim.map_rules import MapColors, MapNode
from harddaysengine.sim.overworld import (
    Arrival,
    MapSprite,
    boxes_touch,
    clip_move,
    list_rows,
    map_sprites,
    open_menu,
    scan,
)
from harddaysengine.sim.types import KnightId
from tests.unit.map_setup import RULES, make_world


def test_loaded_defaults_and_shared_records() -> None:
    world = make_world(seed=5)
    actor = world.current_actor
    assert (actor.map_x, actor.map_y) == (290, 180)
    assert world.prng.state == RULES.seeds[1]
    assert world.budget == actor.derived_endurance * 16
    view = world.knights[KnightId.RED]
    view.gold = 123
    view.lives = -1
    view.keys_mask = 5
    view.moonstones_mask = 10
    assert actor.gold == 123 and actor.lives == 255
    assert not view.alive
    assert view.has_key(2) and view.has_moonstone(3)
    assert world.inventories[1].counts[10:12] == [5, 10]


def test_held_direction_moves_pixels_without_rng_or_fights() -> None:
    world = make_world()
    actor = world.current_actor
    before = world.prng.state
    for _ in range(8):
        assert world.frame(LEFT | UP) is None
    assert (actor.map_x, actor.map_y) == (282, 172)
    assert world.spent == 8
    assert world.prng.state == before
    assert not world.turn_over


@pytest.mark.parametrize(
    ("x", "y", "word", "expected"),
    [
        (0, 0, LEFT | UP, 0),
        (310, 190, RIGHT | DOWN, 0),
        (1, 1, LEFT | UP, LEFT | UP),
        (310, 190, LEFT | UP, LEFT | UP),
        (65535, 65535, LEFT | UP | RIGHT | DOWN, RIGHT | DOWN),
    ],
)
def test_signed_clipping(x: int, y: int, word: int, expected: int) -> None:
    assert clip_move(word, x, y) == expected


def test_clipped_direction_charges_before_clipping() -> None:
    world = make_world()
    world.current_actor.map_x = 310
    world.frame(RIGHT)
    assert world.current_actor.map_x == 310
    assert world.spent == 1 and world.move == 0


def test_terrain_counter_persists_and_blocks_input() -> None:
    world = make_world()
    world.rules = replace(RULES, terrain=bytes([1]) * 1000)
    x = world.current_actor.map_x
    world.frame(LEFT | FIRE)
    assert world.blocked and world.spent == 0
    assert world.current_actor.map_x == x
    world.frame(LEFT)
    assert not world.blocked and world.current_actor.map_x == x - 1
    world.rules = RULES
    world.frame(0)
    assert world.terrain_counter == 2
    world.rules = replace(RULES, terrain=bytes([1]) * 1000)
    world.frame(LEFT)
    assert world.blocked and world.terrain_counter == 3


@pytest.mark.parametrize("mode", ["travel", "ambush"])
def test_modes_bypass_terrain_and_budget(mode: str) -> None:
    world = make_world()
    world.rules = replace(RULES, terrain=bytes([255]) * 1000)
    setattr(world, mode, True)
    world.frame(LEFT)
    assert world.spent == 0 and world.terrain_counter == 0
    assert world.current_actor.map_x == 289


def test_budget_ends_before_final_pixel_and_entry_uses_stale_move() -> None:
    world = make_world()
    world.budget = 2
    world.frame(LEFT)
    world.frame(LEFT)
    assert world.turn_over and world.current_actor.map_x == 289
    world.enter()
    assert world.current_actor.map_x == 288 and world.spent == 2


def test_end_key_and_status_order() -> None:
    world = make_world()
    world.frame(LEFT, ord(" "))
    assert world.spent == 1 and world.current_actor.map_x == 290
    world.frame(0, ord("E"))
    assert world.turn_over and world.spent == world.budget


def test_tile_uses_bottom_centre_and_clamps_to_grid_bounds() -> None:
    world = make_world()
    world.current_actor.map_x, world.current_actor.map_y = 5, 1
    assert world.tile() == 41
    world.current_actor.map_x, world.current_actor.map_y = 400, 400
    assert world.tile() == 999
    assert (world.current_actor.tile_x, world.current_actor.tile_y) == (39, 24)
    world.current_actor.map_x, world.current_actor.map_y = 65520, 65520
    assert world.tile() == 0
    assert (world.current_actor.tile_x, world.current_actor.tile_y) == (0, 0)
    with pytest.raises(ValueError, match="outside loaded grid"):
        world.grid_byte(b"")


def test_proximity_rows_and_highlights_follow_table_order() -> None:
    world = make_world()
    world.current_actor.map_x = world.current_actor.map_y = 50
    world.actors[1].map_x = world.actors[1].map_y = 50
    world.lairs[0].map_x = world.lairs[0].map_y = 50
    scan(world)
    assert list_rows(world) == (Arrival(50 << 16 | 50, 25), Arrival(2, 1), Arrival(6, 2))
    assert world.highlights == [MapSprite(25, 50, 50), MapSprite(9, 50, 50), MapSprite(31, 50, 50)]
    assert open_menu(world) is None and world.menu
    assert world.frame(LEFT, ord("Q")) is None and world.menu
    assert world.frame(0, ord("4")) is None and world.menu
    assert world.frame(0, ord("3")) == Arrival(6, 2)
    assert not world.menu


def test_single_row_dispatch_and_travel_filtering() -> None:
    world = make_world()
    world.current_actor.map_x = world.current_actor.map_y = 50
    scan(world)
    assert world.frame(FIRE) == Arrival(50 << 16 | 50, 25)
    world.travel = True
    world.dragon_touch[:] = [3]
    world.lairs[0].map_x = world.lairs[0].map_y = 50
    scan(world)
    assert list_rows(world) == (Arrival(6, 2),)
    assert world.dragon_touch == [3]


def test_home_castles_are_kind_filtered_and_dead_knights_use_graves() -> None:
    world = make_world()
    world.current_actor.map_x = world.current_actor.map_y = 10
    world.rules = replace(RULES, nodes=tuple(MapNode(i, 10, 10) for i in range(21, 25)))
    world.actors[1].map_x = world.actors[1].map_y = 10
    world.actors[1].lives = 0
    scan(world)
    assert [r.kind for r in list_rows(world)] == [24, 33]
    assert MapSprite(42, 10, 10) in world.highlights


def test_five_row_clear_retains_original_trailing_stale_row() -> None:
    world = make_world()
    world.arrivals[5] = Arrival(99, 33)
    scan(world)
    assert world.arrivals[5] == Arrival(99, 33)
    assert list_rows(world) == ()


def test_full_arrival_list_fails_instead_of_truncating() -> None:
    world = make_world()
    world.current_actor.map_x = world.current_actor.map_y = 50
    world.rules = replace(RULES, nodes=(MapNode(25, 50, 50),) * 10)
    with pytest.raises(ValueError, match="end marker"):
        scan(world)


def test_lair_visibility_is_separate_from_creature_count() -> None:
    world = make_world()
    lair = world.lairs[0]
    lair.creatures_left = 0
    assert MapSprite(20, lair.map_x, lair.map_y) in map_sprites(world)
    lair.gold = 1
    lair.tidy()
    assert lair.map_x != 65535
    lair.gold = 0
    lair.tidy()
    assert (lair.map_x, lair.map_y) == (65535, 65535)


def test_original_asymmetric_box_edges() -> None:
    assert boxes_touch(RULES, 0, 10, 10, 4, 10)
    assert not boxes_touch(RULES, 0, 10, 10, 16, 10)


def test_palette_jobs_tick_without_rendering_and_reverse_at_target() -> None:
    rules = replace(RULES, ramp_target=1, ramp_period=1)
    colors = MapColors()
    palette = (*range(31), 0)
    colors.start(palette, rules)
    colors.step(rules)
    assert colors.colors[31] == 1 and colors.target == 0
    colors.step(rules)
    assert colors.colors[31] == 0 and colors.target == 1
    colors.step(rules)
    assert colors.colors[21:24] == [23, 21, 22]
    colors.active = False
    before = colors.colors.copy()
    colors.step(rules)
    assert colors.colors == before


def test_mode_restore_and_random_spot_draw_order() -> None:
    world = make_world()
    actor = world.current_actor
    world.start_mode(ambush=False)
    world.frame(LEFT)
    world.restore_position()
    assert (actor.map_x, actor.map_y) == (290, 180)
    assert not world.travel and not world.ambush
    world.start_mode(ambush=True)
    world.frame(FIRE)
    assert not world.travel and not world.ambush
    from harddaysengine.sim.rng import Rng

    expected = Rng(world.prng.state)
    x, y = (expected.draw() & 255) + 32, (expected.draw() & 127) + 36
    world.random_spot()
    assert (actor.map_x, actor.map_y) == (x, y)
    assert world.prng.state == expected.state


def test_current_sprite_precedes_proximity_highlights() -> None:
    world = make_world()
    world.highlights[:] = [MapSprite(31, 290, 180)]
    assert map_sprites(world)[-2:] == (MapSprite(8, 290, 180), MapSprite(31, 290, 180))
