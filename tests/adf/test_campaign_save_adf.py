"""Campaign saves and resumed deterministic replays (§13.11)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pygame
import pytest

from harddaysengine.app.audio import AudioEngine
from harddaysengine.app.campaign_menu import CampaignMenu, SaveAction
from harddaysengine.app.campaign_save import read_checkpoint, write_checkpoint
from harddaysengine.app.game import (
    GameScene,
    GameSession,
    _apply_campaign_request,
    run_headless_session,
)
from harddaysengine.app.input import InputFrame, ReplayRecording, save_replay
from harddaysengine.assets.adf import DiskSet
from harddaysengine.sim.types import KnightId

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.adf


def test_map_dragon_checkpoint_resumes_and_supports_replay(adf_dir: Path, tmp_path: Path) -> None:
    disks = DiskSet.from_directory(adf_dir)
    original = GameSession(
        players=(KnightId.BLUE, KnightId.GREEN), disk_set=disks, start_scene=GameScene.OVERWORLD
    )
    original.overworld.day = 2
    original._reenter_map()
    for _ in range(12):
        original.step_tick(InputFrame())
    assert original.overworld.dragon_active
    save_path = tmp_path / "campaign-save.json"
    write_checkpoint(save_path, original.campaign_checkpoint())

    saved = read_checkpoint(save_path)
    assert saved.version == 3
    assert saved.map_jobs is not None
    replay_start = saved.model_dump_json()
    loaded = GameSession.from_campaign_checkpoint(saved, disks)
    assert loaded.state_hash() == original.state_hash()

    # Legacy v1/v2 checkpoints must be cleanly rejected under the v3 schema.
    for old_version in (1, 2):
        old_path = tmp_path / f"legacy-v{old_version}.json"
        old_path.write_text(
            replay_start.replace('"version":3', f'"version":{old_version}', 1),
            encoding="utf-8",
        )
        with pytest.raises(ValueError):
            read_checkpoint(old_path)
    assert loaded.overworld.actors[0] is loaded.actors[0]
    assert loaded.overworld.inventories[1] is loaded.inventories[1]
    assert loaded.overworld.prng is loaded.fight_rng
    assert loaded.map_runtime is not None and original.map_runtime is not None
    np.testing.assert_array_equal(
        loaded.map_runtime.scene.canvas.pixels(), original.map_runtime.scene.canvas.pixels()
    )

    frames = (InputFrame(),) * 20 + (InputFrame(key=ord("E")),) + (InputFrame(),) * 20
    for frame in frames:
        original.step_tick(frame)
        loaded.step_tick(frame)
        assert original.state_hash() == loaded.state_hash()
    replay_path = tmp_path / "resumed.json"
    save_replay(replay_path, ReplayRecording(original.seed, original.players, frames, replay_start))
    result = run_headless_session(adf_dir=adf_dir, replay_path=replay_path)
    assert result.final_hash == original.state_hash()


def test_campaign_menu_routes_scene_actions_and_preserves_game_on_bad_load(
    adf_dir: Path, tmp_path: Path
) -> None:
    disks = DiskSet.from_directory(adf_dir)
    session = GameSession(disk_set=disks, start_scene=GameScene.KNIGHT_SELECT)
    path = tmp_path / "campaign-save.json"
    menu = CampaignMenu(path)
    audio = AudioEngine(headless=True)

    def tap(key: int) -> None:
        for event_type in (pygame.KEYDOWN, pygame.KEYUP):
            assert menu.consume_event(
                pygame.event.Event(event_type, key=key),
                available=session.has_save_menu,
                can_save=session.scene is GameScene.OVERWORLD,
            )

    try:
        tap(pygame.K_F8)
        assert menu.actions == (SaveAction.LOAD,)
        tap(pygame.K_RETURN)
        assert _apply_campaign_request(menu, session, audio) == (session, None)
        assert menu.status == "No saved game"
        tap(pygame.K_ESCAPE)

        session.scene = GameScene.OVERWORLD
        tap(pygame.K_F8)
        assert menu.actions == (SaveAction.SAVE, SaveAction.LOAD)
        tap(pygame.K_RETURN)
        assert _apply_campaign_request(menu, session, audio) == (session, None)
        assert menu.status == "Game saved"
        tap(pygame.K_DOWN)
        tap(pygame.K_RETURN)
        restored, replay_start = _apply_campaign_request(menu, session, audio)
        assert restored is not session and replay_start is not None and not menu.is_open
        assert restored.state_hash() == session.state_hash()

        path.write_text('{"version": 99}', encoding="utf-8")
        tap(pygame.K_F8)
        tap(pygame.K_DOWN)
        tap(pygame.K_RETURN)
        assert _apply_campaign_request(menu, session, audio) == (session, None)
        assert menu.status == "Cannot load save"
        tap(pygame.K_ESCAPE)
        session.scene = GameScene.COMBAT
        assert not session.has_save_menu
        assert not menu.consume_event(
            pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F8), available=False, can_save=False
        )
        assert not menu.is_open
    finally:
        audio.close()
