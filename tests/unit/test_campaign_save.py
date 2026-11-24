"""Headless v3 campaign checkpoint serialization and schema checks (§13.11)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from harddaysengine.app.campaign_save import (
    CampaignCheckpoint,
    FightCheckpoint,
    MapCheckpoint,
    PaletteCheckpoint,
    WorldCheckpoint,
    read_checkpoint,
    write_checkpoint,
)
from harddaysengine.app.input import InputFrame, ReplayRecording, load_replay, save_replay
from harddaysengine.sim.actor import Actor
from harddaysengine.sim.ai_map_state import MapAiCells
from harddaysengine.sim.fight import DrakeCells, FlyerCells
from harddaysengine.sim.fight_script import BoxCells, Draw, Job, ListEntry
from harddaysengine.sim.inventory import Inventory
from harddaysengine.sim.map_dragon import DragonFlight
from harddaysengine.sim.map_rules import MapColors
from harddaysengine.sim.overworld import Arrival
from harddaysengine.sim.rewards import GiftState
from harddaysengine.sim.sound import SoundCells
from harddaysengine.sim.turns import TurnCells
from harddaysengine.sim.types import KnightId

if TYPE_CHECKING:
    from pathlib import Path

_HASH = "a" * 64


def _sample_checkpoint() -> CampaignCheckpoint:
    jobs = [Job() for _ in range(10)]
    jobs[0].active = True
    jobs[0].owner = 5
    jobs[0].attack.append(ListEntry("mi.c", 0, 12, 34))
    jobs[0].hurt.append(ListEntry("mi.c", 1, 56, 78))
    map_jobs = MapCheckpoint(
        jobs=jobs,
        box=BoxCells(armed=True, min_x=10, max_x=20, min_y=30, max_y=40),
        mirrored={("mi.c", 0)},
        vbl=12,
        effect_vbl=12,
        stamps=[],
        draws=[Draw("mi.c", 0, 12, 34, False, False, False)],
    )
    world = WorldCheckpoint(
        ai=MapAiCells(),
        turns=TurnCells(),
        gifts=GiftState(),
        market=Inventory(),
        lairs=[],
        arrivals=[Arrival() for _ in range(10)],
        highlights=[],
        dragon_touch=[],
        colors=MapColors(),
        turn_index=0,
        spent=0,
        budget=96,
        move=0,
        terrain_counter=0,
        blocked=False,
        travel=False,
        ambush=False,
        saved_x=0,
        saved_y=0,
        region_code=0,
        phase=0,
        menu=False,
        turn_over=False,
        dragon_encounter=False,
        dragon_active=True,
        flight=DragonFlight(),
        day=2,
        moon_index=0,
        moon_frame=45,
        moon_subcount=0,
        total_turns=0,
        claimed_moonstones_mask=0,
        game_over=False,
        names=["A", "B", "C", "D"],
    )
    palette = PaletteCheckpoint(
        base=[0] * 32,
        target=[0] * 32,
        divisor=1,
        tick=0,
        fading=False,
        cycles=[],
        flash=None,
        vbl=0,
    )
    return CampaignCheckpoint(
        version=3,
        data_hash=_HASH,
        state_hash=_HASH,
        seed=1,
        tick=12,
        players=(KnightId.RED,),
        actors=[Actor() for _ in range(4)],
        dragon=Actor(),
        inventories={1: Inventory()},
        rng=12345,
        world=world,
        palette=palette,
        map_jobs=map_jobs,
        fights=FightCheckpoint(
            flyer=FlyerCells(), drake=DrakeCells(), sound=SoundCells(plain=1, list_offsets=[2, 4])
        ),
        paused=False,
        selected_players=1,
        menu_cursor=0,
        gore=True,
        pick_slot=0,
        picked_mask=0,
        picked=[],
        menu_latches=(0, 0, False, False),
        map_fire_latch=False,
        map_key=0,
        calendar_phase=0,
        loading_cycle=0,
        last_sheet_item=0,
        last_status="",
        lanes=0,
        lair_counts={},
        bad_luck=0,
        defeats=0,
    )


def test_v3_checkpoint_and_replay_round_trip(tmp_path: Path) -> None:
    checkpoint = _sample_checkpoint()
    save_path = tmp_path / "save.json"
    write_checkpoint(save_path, checkpoint)
    restored = read_checkpoint(save_path)
    assert restored == checkpoint
    assert restored.version == 3
    assert restored.map_jobs is not None
    assert restored.map_jobs.jobs[0].attack == [ListEntry("mi.c", 0, 12, 34)]
    assert restored.map_jobs.jobs[0].hurt == [ListEntry("mi.c", 1, 56, 78)]

    replay_path = tmp_path / "replay.json"
    save_replay(
        replay_path,
        ReplayRecording(1, (KnightId.RED,), (InputFrame(),), restored.model_dump_json()),
    )
    loaded_replay = load_replay(replay_path)
    assert loaded_replay.start_checkpoint is not None
    assert CampaignCheckpoint.model_validate_json(loaded_replay.start_checkpoint, strict=True) == (
        checkpoint
    )


def test_legacy_checkpoints_and_legacy_fields_are_rejected(tmp_path: Path) -> None:
    checkpoint = _sample_checkpoint()
    payload = json.loads(checkpoint.model_dump_json())

    for old_version in (1, 2):
        old_payload = dict(payload, version=old_version)
        old_path = tmp_path / f"v{old_version}.json"
        old_path.write_text(json.dumps(old_payload), encoding="utf-8")
        with pytest.raises(ValidationError):
            read_checkpoint(old_path)

    legacy_payload = dict(payload, legacy={})
    legacy_path = tmp_path / "legacy_field.json"
    legacy_path.write_text(json.dumps(legacy_payload), encoding="utf-8")
    with pytest.raises(ValidationError):
        read_checkpoint(legacy_path)

    for legacy_key in ("scratch", "attack", "hurt", "cursors"):
        bad_map = dict(payload["map_jobs"], **{legacy_key: None})
        bad_payload = dict(payload, map_jobs=bad_map)
        bad_path = tmp_path / f"bad_{legacy_key}.json"
        bad_path.write_text(json.dumps(bad_payload), encoding="utf-8")
        with pytest.raises(ValidationError):
            read_checkpoint(bad_path)
