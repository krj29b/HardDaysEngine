"""Real-data sheet interactions, host transitions and render purity (§12.26)."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING

import pytest

from harddaysengine.app.game import GameScene, GameSession
from harddaysengine.app.input import InputFrame, PlayerInput
from harddaysengine.assets.adf import DiskSet
from harddaysengine.sim.m68k import s16
from harddaysengine.sim.types import KnightId

if TYPE_CHECKING:
    from pathlib import Path

    from harddaysengine.sim.sheet_types import SheetRegion

pytestmark = pytest.mark.adf


def _session(adf_dir: Path) -> GameSession:
    return GameSession(disk_set=DiskSet.from_directory(adf_dir), start_scene=GameScene.OVERWORLD)


def _ticks(session: GameSession, count: int) -> None:
    for _ in range(count):
        session.step_tick(InputFrame())


def _region(session: GameSession, field: int, side: int = 0) -> SheetRegion:
    assert session.sheet_screen is not None
    return next(
        r
        for r in session.sheet_screen.state.regions
        if r.field == field and r.side == side and not r.id
    )


def _click(session: GameSession, region: SheetRegion) -> None:
    assert session.sheet_screen is not None
    state = session.sheet_screen.state
    state.pointer_x, state.pointer_y = region.x + 1, region.y + 1
    session.step_tick(InputFrame(players={KnightId(state.knight - 1): PlayerInput(fire=True)}))


def test_space_opens_status_and_consumes_item_through_loaded_region(adf_dir: Path) -> None:
    session = _session(adf_dir)
    world = session.overworld
    actor = world.current_actor
    inv = world.inventories[actor.inventory]
    actor.hp, actor.life_loss = 1, 1
    inv.counts[0] = 1
    session.step_tick(InputFrame(key=ord(" ")))
    session.step_tick(InputFrame())
    assert session.scene == GameScene.STATUS and session.sheet_screen is not None
    _ticks(session, 24)
    assert world.ai_rules is not None
    assert session.sheet_screen.rules.stat_price == world.ai_rules.stat_price
    _click(session, _region(session, 0))
    assert inv.byte(0) == 0 and actor.hp == actor.hp_max and not actor.life_loss
    assert session.sheet_screen.state.last_item == 0
    done = next(r for r in session.sheet_screen.state.regions if r.id == 7)
    _click(session, done)
    assert session.scene == GameScene.OVERWORLD and session.sheet_screen is None
    assert session.last_sheet_item == 0


def test_lair_loot_gold_items_and_tidy_use_persistent_records(adf_dir: Path) -> None:
    session = _session(adf_dir)
    world, actor = session.overworld, session.overworld.current_actor
    lair = world.lairs[0]
    lair.creatures_left, lair.gold = 0, 7
    lair.loot.counts[0] = 1
    actor.gold = 149
    session._dispatch_lair(0)
    assert session.sheet_screen is not None and session.sheet_screen.state.number == 2
    _ticks(session, 24)
    _click(session, _region(session, 74, 1))
    assert actor.gold == 150 and lair.gold == 6
    actor.gold = 151
    _click(session, _region(session, 74, 1))
    assert actor.gold == 157 and lair.gold == 0
    _click(session, _region(session, 0, 1))
    assert world.inventories[actor.inventory].byte(0) == 1
    done = next(r for r in session.sheet_screen.state.regions if r.id == 7)
    _click(session, done)
    assert session.scene == GameScene.OVERWORLD and lair.map_x == lair.map_y == 65535


def test_travel_inspection_keeps_loot_and_restores_saved_position(adf_dir: Path) -> None:
    session = _session(adf_dir)
    world, actor = session.overworld, session.overworld.current_actor
    saved = actor.map_x, actor.map_y
    world.start_mode(ambush=False)
    actor.map_x, actor.map_y = 100, 80
    world.lairs[0].loot.counts[0], world.lairs[0].gold = 2, 12
    before = asdict(world.lairs[0])
    session._dispatch_lair(0)
    _ticks(session, 24)
    assert session.sheet_screen is not None and session.sheet_screen.state.read_only
    _click(session, _region(session, 0, 1))
    assert asdict(world.lairs[0]) == before
    _click(session, next(r for r in session.sheet_screen.state.regions if r.id == 7))
    assert not world.travel and (actor.map_x, actor.map_y) == saved
    assert asdict(world.lairs[0]) == before


@pytest.mark.parametrize("won", [True, False])
def test_lair_outcome_caller_opens_loot_or_status(adf_dir: Path, won: bool) -> None:
    """Exercise settlement boundary; engine tests cover natural outcomes."""
    session = _session(adf_dir)
    world = session.overworld
    session._dispatch_lair(0)
    active = session.active_encounter
    assert active is not None
    active.runtime.fight.actor(active.runtime.first).hp = 10 if won else 0
    active.runtime.fight.total = 0 if won else 1
    active.runtime.finished = True
    session._finish_combat(active)
    assert session.scene == GameScene.STATUS and session.active_encounter is None
    assert session.sheet_screen is not None
    assert session.sheet_screen.state.number == (2 if won else 9)
    assert world.lairs[0].creatures_left == (0 if won else 1)


def test_sheet_input_replay_hashes_and_foreign_controller_isolation(adf_dir: Path) -> None:
    traces = []
    for _ in range(2):
        session = _session(adf_dir)
        session.overworld.inventories[1].counts[0] = 2
        session._open_sheet(9)
        hashes = []
        for tick in range(90):
            session.step_tick(
                InputFrame(
                    players={
                        KnightId.RED: PlayerInput(dx=-1 if tick < 20 else 1, dy=1),
                        KnightId.BLUE: PlayerInput(dx=1, fire=True),
                    }
                )
            )
            hashes.append(session.state_hash())
        traces.append(hashes)
        assert session.sheet_screen is not None
        assert session.overworld.inventories[1].byte(0) == 2
    assert traces[0] == traces[1]


def test_status_preserves_existing_map_dragon_and_does_not_respawn_it(adf_dir: Path) -> None:
    session = _session(adf_dir)
    world = session.overworld
    world.day = 2
    session._reenter_map()
    assert world.dragon_active and session.map_runtime is not None
    jobs = session.map_runtime.scene.fight.engine.jobs
    active = [job for job in jobs if job.active]
    target, seed = world.dragon.engaged_with, world.prng.state
    session._open_sheet(9)
    _ticks(session, 24)
    assert world.dragon_active and [job for job in jobs if job.active] == active
    assert session.sheet_screen is not None
    _click(session, next(r for r in session.sheet_screen.state.regions if r.id == 7))
    assert world.dragon_active and world.dragon.engaged_with == target
    assert world.prng.state == seed


def test_natural_trogg_lair_win_opens_operable_loot(adf_dir: Path) -> None:
    from harddaysengine.app.fight_audit import fight_host_melee_input

    session = GameSession(
        disk_set=DiskSet.from_directory(adf_dir),
        start_scene=GameScene.OVERWORLD,
        players=(KnightId.BLUE,),
        seed=0x2468ACE1,
    )
    world = session.overworld
    legacy = session.knight_combat[KnightId.BLUE]
    legacy.hp = legacy.max_hp = 200
    legacy.strength, legacy.sword_tier, legacy.daggers = 3, 4, 5
    world.lairs[1].creatures_left = 1
    world.lairs[1].loot.counts[0] = 1
    session._dispatch_lair(1)
    for _ in range(18000):
        session.step_tick(fight_host_melee_input(session))
        if session.scene != GameScene.COMBAT:
            break
    assert session.scene == GameScene.STATUS and session.last_defeat_mask == 0
    assert s16(world.lairs[1].creatures_left) <= 0 and session.sheet_screen is not None
    assert session.sheet_screen.state.number == 2
    _ticks(session, 24)
    _click(session, _region(session, 0, 1))
    assert world.lairs[1].loot.byte(0) == 0
    assert world.inventories[world.current_actor.inventory].byte(0) == 1
    # Keep loot behind so the lair remains visible, including a wrapped terminal count.
    world.lairs[1].gold = 7
    _click(session, next(r for r in session.sheet_screen.state.regions if r.id == 7))
    session._dispatch_lair(1)
    assert session.scene == GameScene.STATUS and session.active_encounter is None
    assert session.sheet_screen is not None and session.sheet_screen.state.number == 2


@pytest.mark.gl
def test_sheet_rendering_is_read_only_and_all_icons_are_loaded(adf_dir: Path) -> None:
    from harddaysengine.app.audit import AuditRig

    rig = AuditRig(adf_dir)
    try:
        for number in (2, 9, 8, 11, 32):
            session = _session(adf_dir)
            session.overworld.inventories[1].counts[:] = [2] * 10 + [15, 15]
            session.overworld.lairs[0].loot.counts[:] = [2] * 10 + [15, 15]
            session._open_sheet(number, lair=0)
            _ticks(session, 24)
            screen = session.sheet_screen
            assert screen is not None
            assert all(0 <= icon.frame < len(screen.cel.frames) for icon in screen.state.icons)
            before = session.state_hash()
            rig.capture(session, f"sheet{number}_once")
            rig.capture(session, f"sheet{number}_twice")
            assert session.state_hash() == before
    finally:
        rig.release()
