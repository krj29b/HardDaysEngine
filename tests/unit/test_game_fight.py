"""Game host boundaries on a synthetic persistent engine (ARCHITECTURE.md §10.22)."""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING
from unittest.mock import Mock, patch

import numpy as np
import pytest

from pymoonstone.app.audio import AudioEngine
from pymoonstone.app.fight_runtime import FightLoader, FightRuntime, FightUnavailableError
from pymoonstone.app.fight_scene import FightScene
from pymoonstone.app.game import GameScene, GameSession
from pymoonstone.app.input import InputFrame, PlayerInput
from pymoonstone.app.scene_assets import GameAssetBundle
from pymoonstone.assets.cel import CelFrame, CelFrameMeta, DecodedCel
from pymoonstone.assets.fight_program import cel_shape
from pymoonstone.assets.palette import default_combat_palette
from pymoonstone.render.scene import SceneRenderer, default_bitmap_font
from pymoonstone.sim.fight import END_FIGHT, Fight, FightConstants, FrameOutput
from pymoonstone.sim.fight_script import ScriptImage, TickOutput
from pymoonstone.sim.overworld import EncounterTrigger, MoonPhase, TriggerKind
from pymoonstone.sim.types import KnightId

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pymoonstone.assets.fight_defs import ArenaDef
    from pymoonstone.sim.actor import Actor
    from pymoonstone.sim.inventory import Inventory
    from pymoonstone.sim.rng import Rng
    from pymoonstone.sim.fight_setup import SpawnLanes


def _trigger() -> EncounterTrigger:
    return EncounterTrigger(
        TriggerKind.WANDERING_MONSTER, KnightId.RED, -1, 1, MoonPhase.NEW_MOON, creature_type=1
    )


def _session(*, audio: AudioEngine | None = None) -> GameSession:
    session = GameSession(start_scene=GameScene.OVERWORLD, audio=audio)
    session.asset_bundle = Mock(spec=GameAssetBundle)
    loader = Mock(spec=FightLoader)

    def create(
        _arena: ArenaDef,
        _background: str,
        actors: Sequence[Actor],
        dragon: Actor,
        inventories: Mapping[int, Inventory],
        rng: Rng,
        first: int,
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
        fight.first, fight.fight_active = first, True
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
    assert session.overworld.moves_remaining == 0


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


def test_host_dispatches_handler_sounds_before_script_sounds() -> None:
    audio = Mock(spec=AudioEngine)
    session = _session(audio=audio)
    session._start_combat(_trigger(), KnightId.RED)
    assert session.active_encounter is not None
    output = FrameOutput(TickOutput(sounds=[2, 3]), [], [1])
    with patch.object(FightRuntime, "step_vbl", return_value=output):
        session.step_tick(InputFrame())
    audio.handle_fight_sounds.assert_called_once_with((1, 2, 3))


def test_no_disks_does_not_create_synthetic_gameplay() -> None:
    session = GameSession(start_scene=GameScene.OVERWORLD)
    session._start_combat(_trigger(), KnightId.RED)
    assert session.active_encounter is None
    assert "original disks" in session.last_status
