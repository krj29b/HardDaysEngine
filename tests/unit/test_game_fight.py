"""Game host boundaries on a synthetic persistent engine (ARCHITECTURE.md §10.22)."""

from __future__ import annotations

import struct
from dataclasses import replace
from typing import TYPE_CHECKING
from unittest.mock import Mock, patch

import numpy as np
import pytest

from harddaysengine.app.audio import AudioEngine
from harddaysengine.app.fight_runtime import FightLoader, FightRuntime, FightUnavailableError
from harddaysengine.app.fight_scene import FightScene
from harddaysengine.app.game import GameScene, GameSession
from harddaysengine.app.input import InputFrame, PlayerInput
from harddaysengine.app.scene_assets import GameAssetBundle
from harddaysengine.assets.cel import CelFrame, CelFrameMeta, DecodedCel
from harddaysengine.assets.fight_program import cel_shape
from harddaysengine.assets.palette import default_combat_palette
from harddaysengine.render.scene import SceneRenderer, default_bitmap_font
from harddaysengine.sim.fight import (
    END_FIGHT,
    DrakeCells,
    Fight,
    FightConstants,
    FlyerCells,
    FrameOutput,
)
from harddaysengine.sim.fight_script import ScriptImage, TickOutput
from harddaysengine.sim.overworld import EncounterTrigger, MoonPhase, TriggerKind
from harddaysengine.sim.sound import SoundKind, SoundRequest, play
from harddaysengine.sim.types import KnightId

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from harddaysengine.assets.fight_defs import ArenaDef
    from harddaysengine.sim.actor import Actor
    from harddaysengine.sim.fight_setup import SpawnLanes
    from harddaysengine.sim.inventory import Inventory
    from harddaysengine.sim.rng import Rng


def _trigger() -> EncounterTrigger:
    return EncounterTrigger(
        TriggerKind.WANDERING_MONSTER, KnightId.RED, -1, 1, MoonPhase.NEW_MOON, creature_type=1
    )


def _session(*, audio: AudioEngine | None = None) -> GameSession:
    session = GameSession(start_scene=GameScene.OVERWORLD, audio=audio)
    session.asset_bundle = Mock(spec=GameAssetBundle)
    loader = Mock(spec=FightLoader)
    loader.flyer = FlyerCells()
    loader.drake = DrakeCells()

    def create(
        _arena: ArenaDef,
        _background: str,
        actors: Sequence[Actor],
        dragon: Actor,
        inventories: Mapping[int, Inventory],
        rng: Rng,
        first: int,
        moon_frame: int = 0,
        *,
        lair_count: int | None = None,
        lanes: SpawnLanes | None = None,
    ) -> FightRuntime:
        pixels = np.array([[2, 3]], dtype=np.uint8)
        cel = DecodedCel(
            1, 0, 0, (CelFrame(CelFrameMeta(0, 0, 2, 1, 1, 3), pixels, pixels != 0),), b""
        )
        draw = struct.pack(">BBbBh", 0, 0, 0, 0, 0)
        code = bytes(2) + draw + bytes((0xFF, 0))
        call = len(code)
        code += bytes((0xA4, 7)) + struct.pack(">BBI", 0xB0, 0, 0) + bytes((0xFF, 0xFF))
        fight = Fight(
            ScriptImage(code, {call + 4: END_FIGHT}),
            {"a": cel_shape("a", cel, None)},
            {1: ("a",)},
            actors,
            dragon,
            FightConstants(2, 0xFFFF),
            rng=rng,
        )
        fight.moon_frame = moon_frame
        fight.first, fight.fight_active = first, True
        fight.flyer = loader.flyer
        fight.inventories = inventories
        fight.actor(first).input_port = 1
        fight.start_job(2, first, 1, (50, 0, 80), 1, 0x28)
        scene = FightScene(
            fight, np.ones((200, 320), dtype=np.uint8), {"a": cel}, default_combat_palette()
        )
        return FightRuntime(scene, first)

    loader.creature.side_effect = create
    session.fight_loader = loader
    return session


def test_host_keeps_engine_jobs_and_actor_records_between_ticks_and_renders() -> None:
    session = _session()
    session._start_combat(_trigger(), KnightId.RED)
    assert session.active_encounter is not None
    runtime = session.active_encounter.runtime
    assert runtime.fight.actor(1) is session.actors[0]
    job = runtime.fight.find_job(1)
    assert job is not None
    for _ in range(5):
        session.step_tick(InputFrame())
        assert job.script == 2
    session.step_tick(InputFrame(players={KnightId.RED: PlayerInput(dx=-8, dy=5, fire=True)}))
    assert job.script == 10
    assert runtime.fight.ports == [0x16, 0]
    state = session.state_hash()
    renderer = Mock(spec=SceneRenderer)
    for _ in range(3):
        session.render_to_scene(renderer, default_bitmap_font())
    assert session.state_hash() == state
    assert runtime.fight.find_job(1) is job


@pytest.mark.parametrize("lost", [False, True])
def test_host_returns_engine_health_and_lives_after_the_end_delay(lost: bool) -> None:
    session = _session()
    legacy = session.knight_combat[KnightId.RED]
    legacy.hp, legacy.max_hp, legacy.lives = 57, 80, 2
    session._start_combat(_trigger(), KnightId.RED)
    assert session.active_encounter is not None
    runtime = session.active_encounter.runtime
    actor = session.actors[0]
    actor.hp = 0 if lost else 35
    actor.daggers = 2
    for _ in range(17):
        session.step_tick(InputFrame())
    assert session.scene == GameScene.COMBAT
    session.step_tick(InputFrame())
    assert runtime.finished
    assert session.scene == GameScene.OVERWORLD
    assert session.active_encounter is None
    assert legacy.hp == (80 if lost else 35)
    assert legacy.lives == (1 if lost else 2)
    assert legacy.daggers == 2
    assert session.overworld.spent == session.overworld.budget


def test_unsupported_encounter_preserves_campaign_and_records() -> None:
    session = _session()
    assert session.fight_loader is not None
    session.fight_loader.arena_for = Mock(side_effect=FightUnavailableError("Ratmen unavailable"))
    before = session.state_hash()
    session._start_combat(_trigger(), KnightId.RED)
    assert session.scene == GameScene.OVERWORLD
    assert session.active_encounter is None
    assert session.last_status == "FIGHT UNAVAILABLE: Ratmen unavailable"
    assert session.state_hash() == before


def test_host_dispatches_fight_sound_requests_in_order() -> None:
    audio = Mock(spec=AudioEngine)
    session = _session(audio=audio)
    session._start_combat(_trigger(), KnightId.RED)
    assert session.active_encounter is not None
    requests = [play(1), SoundRequest(SoundKind.FIXED, 2, 3), play(3)]
    output = FrameOutput(TickOutput(), [], requests)
    with patch.object(FightRuntime, "step_vbl", return_value=output):
        session.step_tick(InputFrame())
    audio.handle_fight_sounds.assert_called_once_with([1, 2, 3])


def test_no_disks_does_not_create_synthetic_gameplay() -> None:
    session = GameSession(start_scene=GameScene.OVERWORLD)
    session._start_combat(_trigger(), KnightId.RED)
    assert session.active_encounter is None
    assert "original disks" in session.last_status


@pytest.mark.parametrize("lost", [False, True])
def test_lair_settlement_preserves_remaining_wave_and_awards_only_progress(lost: bool) -> None:
    session = _session()
    session._start_combat(_trigger(), KnightId.RED)
    active = session.active_encounter
    assert active is not None
    active.trigger = replace(active.trigger, kind=TriggerKind.LAIR_BOSS, lair_index=3)
    actor = active.runtime.fight.actor(active.runtime.first)
    actor.hp, actor.progress = (0 if lost else 20), 4
    active.runtime.fight.total = 2 if lost else 0
    session.bad_luck = 1
    inventory = session.inventories[1].counts.copy()
    for _ in range(18):
        session.step_tick(InputFrame())
    assert session.active_encounter is None
    assert session.lair_counts[3] == (2 if lost else 0)
    assert actor.progress == (4 if lost else 5)
    assert session.last_defeat_mask == int(lost)
    assert session.bad_luck == 0
    assert session.inventories[1].counts == inventory
    assert all(not job.active for job in active.runtime.fight.engine.jobs)
    state = session.state_hash()
    session.lair_counts[3] += 1
    assert session.state_hash() != state


def test_pausing_combat_preserves_frame_clock_without_catchup() -> None:
    session = _session()
    session._start_combat(_trigger(), KnightId.RED)
    active = session.active_encounter
    assert active is not None
    session.step_tick(InputFrame(players={KnightId.RED: PlayerInput(pause=True)}))
    assert session.paused
    before = active.runtime.snapshot()
    for _ in range(60):
        session.step_tick(InputFrame())
    assert active.runtime.snapshot() == before
    session.step_tick(InputFrame(players={KnightId.RED: PlayerInput(pause=True)}))
    assert not session.paused
    assert active.runtime.snapshot() == before
    session.step_tick(InputFrame())
    assert active.runtime.phase == 1
