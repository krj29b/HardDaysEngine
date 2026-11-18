"""Synthetic town loop boundaries and caller suspension (ARCHITECTURE.md §12.15)."""

import pytest

from harddaysengine.sim.town_types import TownButton, TownData
from harddaysengine.sim.towns import TownAction, TownState, reset_pointer, step


def _data() -> TownData:
    return TownData(
        "Synthetic town",
        "town.pic",
        "cursor.cel",
        17,
        19,
        32,
        tuple(TownButton(i + 1, 10, 20 + i * 25, 30, 12, 1, 74) for i in range(5)),
    )


@pytest.mark.parametrize("waterdeep", [False, True])
@pytest.mark.parametrize("index", range(5))
def test_every_button_routes_and_suspends_until_return(waterdeep: bool, index: int) -> None:
    data = _data()
    button = data.buttons[index]
    state = TownState(waterdeep, 1, button.x, button.y)
    expected = (
        TownAction.SMITH,
        TownAction.DICE,
        TownAction.HEALER,
        TownAction.MYSTIC if waterdeep else TownAction.MARKET,
        TownAction.EXIT,
    )[index]
    assert step(state, data, 0, 0, True, 32) == expected
    assert state.pending == expected
    before = state.pointer_x, state.pointer_y
    assert step(state, data, 1, 1, True, 32) == TownAction.NONE
    assert (state.pointer_x, state.pointer_y) == before
    reset_pointer(state, data)
    assert state.pending == TownAction.NONE
    assert (state.pointer_x, state.pointer_y) == (17, 19)


def test_region_edges_gaps_and_space_fallthrough() -> None:
    data = _data()
    for x, y in ((40, 20), (10, 32), (9, 20), (10, 19)):
        state = TownState(False, 1, x, y)
        assert step(state, data, 0, 0, True, 0) == TownAction.NONE
        assert step(state, data, 0, 0, True, 32) == TownAction.STATUS
    state = TownState(False, 1, 10, 20)
    assert step(state, data, 0, 0, False, 27) == TownAction.NONE
    assert step(state, data, 0, 0, False, 32) == TownAction.STATUS


def test_pointer_moves_two_pixels_per_pal_tick_and_clamps() -> None:
    state, data = TownState(False, 1, 1, 1), _data()
    step(state, data, -1, -1, False, 0)
    assert (state.pointer_x, state.pointer_y) == (0, 0)
    for _ in range(200):
        step(state, data, 1, 1, False, 0)
    assert (state.pointer_x, state.pointer_y) == (314, 195)
    step(state, data, -1, -1, False, 0)
    assert (state.pointer_x, state.pointer_y) == (312, 193)
