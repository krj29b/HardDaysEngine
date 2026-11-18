"""Independent original map reads and host acceptance checks (§11.18)."""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

import pytest

from harddaysengine.app.game import GameScene, GameSession
from harddaysengine.app.input import InputFrame, PlayerInput
from harddaysengine.assets.adf import DiskSet
from harddaysengine.assets.hunk import parse_hunk_executable
from harddaysengine.assets.mog_code import MogCode
from harddaysengine.sim.overworld import list_rows, scan
from harddaysengine.sim.types import KnightId

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.adf


def test_map_tables_match_relocated_executable_data(adf_dir: Path) -> None:
    disks = DiskSet.from_directory(adf_dir)
    session = GameSession(disk_set=disks, start_scene=GameScene.OVERWORLD)
    rules = session.overworld.data()
    mog = MogCode(parse_hunk_executable("mog", disks.read_file("mog")))
    for pos, actual in ((0x8BA, rules.terrain), (0x13FE, rules.regions)):
        pointer = mog.pointer(pos + 2, "independent map read", 36)
        assert actual == mog.hunk_data(pointer.hunk)[pointer.offset : pointer.offset + 1000]
    pointer = mog.pointer(0xA304, "independent seeds")
    assert rules.seeds == struct.unpack_from(">4I", mog.hunk_data(pointer.hunk), pointer.offset)
    pointer = mog.pointer(0xE80, "independent nodes")
    data, pos = mog.hunk_data(pointer.hunk), pointer.offset
    rows = []
    while struct.unpack_from(">h", data, pos)[0] >= 0:
        rows.append(struct.unpack_from(">HHH", data, pos))
        pos += 6
    assert [(n.kind, n.x, n.y) for n in rules.nodes] == rows
    assert len(rules.lairs) == 24
    assert session.asset_bundle is not None
    cel = session.asset_bundle.get_cel("mi.c")
    assert cel is not None
    assert rules.sizes == tuple((f.meta.width, f.meta.height) for f in cel.frames)
    assert rules.ramp_period == mog.hunk_word(36, 0x5F4) & 255
    assert rules.cycle_period == struct.unpack_from(">I", mog.hunk_data(36), 0x610)[0]


def test_original_map_host_moves_and_lists_places_and_lairs(adf_dir: Path) -> None:
    session = GameSession(disk_set=DiskSet.from_directory(adf_dir), start_scene=GameScene.OVERWORLD)
    world = session.overworld
    actor = world.current_actor
    x, y, rng = actor.map_x, actor.map_y, world.prng.state
    movement = InputFrame(players={KnightId.RED: PlayerInput(dx=-1)})
    for _ in range(12):
        session.step_tick(movement)
    assert x - 6 <= actor.map_x < x and actor.map_y == y
    assert session.scene == GameScene.OVERWORLD and session.active_encounter is None
    assert world.prng is session.fight_rng and world.prng.state == rng
    place = next(n for n in world.data().nodes if n.kind == 25)
    actor.map_x, actor.map_y = place.x, place.y
    scan(world)
    assert any(row.kind == 25 for row in list_rows(world))
    lair = world.lairs[0]
    actor.map_x, actor.map_y = lair.map_x, lair.map_y
    scan(world)
    assert any(row.key == 6 and row.kind == 2 for row in list_rows(world))
    assert world.knights[KnightId.RED].actor is actor


def test_map_replay_includes_pal_palette_jobs(adf_dir: Path) -> None:
    disks = DiskSet.from_directory(adf_dir)
    left = GameSession(disk_set=disks, start_scene=GameScene.OVERWORLD, seed=3)
    right = GameSession(disk_set=disks, start_scene=GameScene.OVERWORLD, seed=3)
    before = left.overworld.colors.colors.copy()
    for tick in range(30):
        frame = InputFrame(players={KnightId.RED: PlayerInput(dx=-1 if tick < 16 else 0)})
        left.step_tick(frame)
        right.step_tick(frame)
        assert left.state_hash() == right.state_hash()
    assert left.overworld.colors.colors != before


def test_lair_proximity_dispatches_the_decoded_fight(adf_dir: Path) -> None:
    session = GameSession(disk_set=DiskSet.from_directory(adf_dir), start_scene=GameScene.OVERWORLD)
    world = session.overworld
    actor, lair = world.current_actor, world.lairs[0]
    actor.map_x, actor.map_y = lair.map_x, lair.map_y
    world.move = 0
    scan(world)
    frame = InputFrame(players={KnightId.RED: PlayerInput(fire=True)})
    for _ in range(8):
        session.step_tick(frame)
        if session.scene == GameScene.COMBAT:
            break
    assert session.scene == GameScene.COMBAT
    active = session.active_encounter
    assert active is not None
    assert active.trigger.lair_index == 0
    assert active.runtime.fight.actor(active.runtime.first) is actor
    # The original damage-class cut is applied after the lair count (§10.6).
    assert 0 < active.runtime.fight.total <= lair.creatures_left
    assert not world.colors.active
