"""VBL screen-shake timing, retriggers, snapshots and rendering contract (§10.27)."""

from dataclasses import asdict
from unittest.mock import Mock

import numpy as np
import pytest

from pymoonstone.app.fight_runtime import FightRuntime
from pymoonstone.render.scene import SceneRenderer
from pymoonstone.sim.screen_fx import ShakeRules, ShakeState
from tests.unit.knight_setup import make_fight
from tests.unit.test_fight_scene import _scene

RULES = ShakeRules(2, (2, -3, 0))


def test_sequence_uses_exact_vbl_period_then_restores() -> None:
    state = ShakeState()
    state.start(RULES)
    offsets = []
    for _ in range(8):
        state.advance(1, RULES)
        offsets.append(state.offset)
    assert offsets == [0, 2, 2, -3, -3, 0, 0, 0]
    assert not state.active


def test_elapsed_ticks_equal_per_tick_updates_and_retrigger_keeps_current_offset() -> None:
    single, batched = ShakeState(), ShakeState()
    single.start(RULES)
    batched.start(RULES)
    for _ in range(5):
        single.advance(1, RULES)
    batched.advance(5, RULES)
    assert single == batched
    batched.start(RULES)
    assert batched.offset == -3 and batched.wait == 2 and batched.index == 0
    batched.advance(100, RULES)
    assert not batched.active and batched.offset == 0


def test_fight_updates_effect_only_once_for_each_timestamp() -> None:
    fight, _ = make_fight()
    fight.shake_rules = RULES
    fight.advance_vbl(10)
    fight.shake.start(RULES)
    fight.advance_vbl(12)
    before = asdict(fight.shake)
    fight.advance_vbl(12)
    assert fight.shake.offset == 2 and asdict(fight.shake) == before
    fight.advance_vbl(14)
    assert fight.shake.offset == -3


@pytest.mark.parametrize("offset", [-7, 8])
def test_whole_background_moves_into_border_without_changing_its_pixels(offset: int) -> None:
    renderer = object.__new__(SceneRenderer)
    renderer.renderer = Mock()
    renderer.canvas = np.zeros((256, 320), dtype=np.uint8)
    background = np.full((200, 320), 9, dtype=np.uint8)
    before = background.copy()
    renderer.compose_scene(background=background, background_y_offset=offset)
    assert np.all(renderer.canvas[28 + offset : 228 + offset] == 9)
    assert np.count_nonzero(renderer.canvas) == 200 * 320
    np.testing.assert_array_equal(background, before)


def test_invalid_zero_period_is_rejected() -> None:
    with pytest.raises(ValueError):
        ShakeRules(0, (1,))


def test_runtime_consumes_one_effect_tick_after_a_pause_without_catchup() -> None:
    runtime = FightRuntime(_scene(), 1)
    runtime.fight.shake_rules = RULES
    runtime.fight.shake.start(RULES)
    runtime.step_vbl(1, [0, 0])
    assert runtime.fight.shake.wait == 1 and runtime.fight.shake.offset == 0
    runtime.step_vbl(100, [0, 0])
    assert runtime.fight.shake.offset == 2 and runtime.fight.shake.active
    assert runtime.fight.shake.wait == 2
