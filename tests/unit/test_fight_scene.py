"""Synthetic integration checks for ordered fight presentation (§3.2, §10.17)."""

from __future__ import annotations

import struct
from unittest.mock import Mock

import numpy as np
import numpy.typing as npt
import pytest

from harddaysengine.app.fight_scene import FightScene
from harddaysengine.assets.cel import CelFrame, CelFrameMeta, DecodedCel
from harddaysengine.assets.fight_program import cel_shape
from harddaysengine.assets.palette import default_combat_palette
from harddaysengine.render.fight import FightCanvas
from harddaysengine.render.scene import SceneRenderer
from harddaysengine.sim.actor import Actor
from harddaysengine.sim.fight import Fight, FightConstants
from harddaysengine.sim.fight_script import Draw, ScriptImage


def _cel(*rows: list[list[int]], shift: int = 0) -> DecodedCel:
    frames = []
    for index, row in enumerate(rows):
        pixels = np.array(row, dtype=np.uint8)
        height, width = pixels.shape
        meta = CelFrameMeta(index, 0, width, height, (shift << 4) | 1, 0x1F)
        frames.append(CelFrame(meta, pixels, pixels != 0))
    return DecodedCel(len(frames), 0, 0, tuple(frames), b"")


def _background() -> npt.NDArray[np.uint8]:
    return np.ones((200, 320), dtype=np.uint8)


def _draw(cel: str, x: int, y: int, *, stamp: bool = False, mirrored: bool = False) -> Draw:
    return Draw(cel, 0, x & 0xFFFF, y & 0xFFFF, mirrored, stamp, False)


def test_order_is_engine_order_even_when_screen_y_decreases() -> None:
    canvas = FightCanvas(_background(), {"a": _cel([[2], [2]]), "b": _cel([[3], [3], [3]])})
    canvas.accept([_draw("a", 50, 51), _draw("b", 50, 50)])
    assert list(canvas.pixels()[50:53, 50]) == [3, 3, 3]


def test_stamps_persist_but_do_not_move_later_draws_behind_earlier_ones() -> None:
    canvas = FightCanvas(_background(), {"a": _cel([[2, 2]]), "b": _cel([[3, 0]])})
    canvas.accept([_draw("a", 10, 20), _draw("b", 10, 20, stamp=True)])
    assert list(canvas.pixels()[20, 10:12]) == [3, 2]
    canvas.accept([])
    assert list(canvas.pixels()[20, 10:12]) == [3, 1]
    canvas.accept([_draw("a", 10, 20)])
    assert list(canvas.pixels()[20, 10:12]) == [2, 2]
    canvas.accept([])
    assert list(canvas.pixels()[20, 10:12]) == [3, 1]


def test_transparency_signed_coordinates_and_playfield_clipping() -> None:
    canvas = FightCanvas(_background(), {"a": _cel([[2, 0, 3], [4, 5, 6]])})
    canvas.accept([_draw("a", -1, -1), _draw("a", 319, 199)])
    assert list(canvas.pixels()[0, :3]) == [5, 6, 1]
    assert canvas.pixels()[199, 319] == 2
    assert canvas.pixels()[0, 319] == 1


def test_mirroring_uses_each_draws_snapshot_and_header_alignment() -> None:
    # A non-word-aligned cel drawn in both orientations in the same frame.
    cel = _cel([[2, 3, 4]], shift=2)
    before = cel.frames[0].pixels.copy()
    canvas = FightCanvas(_background(), {"a": cel})
    canvas.accept([_draw("a", 10, 20), _draw("a", 20, 20, mirrored=True)])
    assert list(canvas.pixels()[20, 8:11]) == [2, 3, 4]
    assert list(canvas.pixels()[20, 20:23]) == [4, 3, 2]
    np.testing.assert_array_equal(cel.frames[0].pixels, before)


def test_inputs_and_display_copies_cannot_change_background() -> None:
    background = _background()
    canvas = FightCanvas(background, {})
    background.fill(9)
    canvas.pixels().fill(8)
    canvas.accept([])
    assert np.all(canvas.pixels() == 1)


@pytest.mark.parametrize("frame", [-1, 1])
def test_invalid_frame_is_rejected_without_partial_stamps(frame: int) -> None:
    canvas = FightCanvas(_background(), {"a": _cel([[2]])})
    invalid = Draw("a", frame, 0, 0, False, False, False)
    with pytest.raises(ValueError, match="outside a"):
        canvas.accept([_draw("a", 0, 0, stamp=True), invalid])
    canvas.accept([])
    assert canvas.pixels()[0, 0] == 1


def test_unknown_cel_and_wrong_background_size_fail() -> None:
    with pytest.raises(ValueError, match="320 by 200"):
        FightCanvas(np.zeros((256, 320), dtype=np.uint8), {})
    canvas = FightCanvas(_background(), {})
    with pytest.raises(KeyError, match="missing"):
        canvas.accept([_draw("missing", 0, 0)])


def _scene() -> FightScene:
    cel = _cel([[2, 3]], [[4, 5]])
    # Frame 1 stamps at the original position. Frame 2 moves up and draws a new cel.
    script = bytes(2) + struct.pack(">BBbBh", 0, 0, 0, 0x10, 0) + bytes((0xFF, 0))
    script += struct.pack(">BBHHH", 0xA0, 0x40, 20, 0xFFF6, 30)
    script += struct.pack(">BBbBh", 0, 1, 0, 0, 0) + bytes((0xFF, 0xFF))
    fight = Fight(
        ScriptImage(script, {}),
        {"a": cel_shape("a", cel, None)},
        {1: ("a",)},
        [Actor() for _ in range(4)],
        Actor(),
        FightConstants(10, 0xFFFF),
    )
    fight.start_job(2, 1, 1, (10, 0, 30), 1, 0x28)
    return FightScene(fight, _background(), {"a": cel}, default_combat_palette())


def test_persistent_engine_frame_survives_zero_or_many_render_calls() -> None:
    scene, control = _scene(), _scene()
    renderer = Mock(spec=SceneRenderer)
    first = scene.advance(6)
    control.advance(6)
    job = scene.fight.find_job(1)
    assert job is not None and job.script == 10
    assert len(first.tick.draws) == 1
    for _ in range(4):
        scene.render(renderer)
    assert job.script == 10
    second = scene.advance(12)
    expected = control.advance(12)
    assert second == expected
    assert second.tick.draws[0].frame == 1
    np.testing.assert_array_equal(scene.canvas.pixels(), control.canvas.pixels())
    assert list(scene.canvas.pixels()[30, 10:12]) == [2, 3]
    assert list(scene.canvas.pixels()[20, 20:22]) == [4, 5]
    assert scene.fight.actor(1).height == 0xFFF6
    assert renderer.compose_scene.call_count == 4
