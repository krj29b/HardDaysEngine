"""Real-disk verification that debug snapshots never leak ADF data (§13.14, ADR-073)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from harddaysengine.app.campaign_save import disk_signature
from harddaysengine.app.debug_dump import (
    DebugDumpContext,
    build_debug_dump,
    write_debug_dump,
)
from harddaysengine.app.game import GameScene, GameSession
from harddaysengine.assets.adf import DiskSet
from harddaysengine.sim.overworld import EncounterTrigger, MoonPhase, TriggerKind
from harddaysengine.sim.types import KnightId

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.adf
def test_real_disk_debug_dump_contains_no_adf_data(adf_dir: Path, tmp_path: Path) -> None:
    disks = DiskSet.from_directory(adf_dir)
    session = GameSession(
        seed=1337,
        players=(KnightId.RED,),
        start_scene=GameScene.OVERWORLD,
        disk_set=disks,
    )
    # Enter a real lair combat encounter so both overworld and active combat are populated.
    session._start_combat(
        EncounterTrigger(
            kind=TriggerKind.LAIR_BOSS,
            knight_id=KnightId.RED,
            node_id=0,
            day=1,
            moon_phase=MoonPhase.NEW_MOON,
            lair_index=0,
            creature_type=session.overworld.lairs[0].handler,
        ),
        KnightId.RED,
    )
    assert session.scene == GameScene.COMBAT
    assert session.active_encounter is not None

    before_hash = session.state_hash()
    payload = build_debug_dump(
        DebugDumpContext(session=session, adf_image_count=3),
        trigger="hotkey_f12",
    )
    written = write_debug_dump(
        tmp_path / "dumps", payload, trigger="hotkey_f12", tick=session.tick_count
    )
    assert session.state_hash() == before_hash

    raw_text = written.read_text(encoding="utf-8")
    loaded = json.loads(raw_text)
    assert loaded["contains_adf_data"] is False
    assert loaded["host"]["disks"] == {
        "loaded": True,
        "adf_image_count": 3,
        "program_mog_sha256": disk_signature(disks),
    }

    # Verify combat summary populated with numeric actors/jobs and zero table/cel leakage.
    combat = loaded["session"]["combat"]
    assert combat is not None
    assert len(combat["active_actors"]) >= 2
    assert len(combat["active_jobs"]) >= 2
    assert "tables" not in combat
    assert "mirrored" not in combat

    # Assert no real decoded knight names, lair arena filenames, or cel filenames appear anywhere.
    for knight_name in session.overworld.names:
        if knight_name.strip():
            assert knight_name not in raw_text
    for lair in session.overworld.lairs:
        if lair.arena.strip():
            assert lair.arena not in raw_text
    for cel_name in session.active_encounter.runtime.scene.hd_history.cels:
        assert cel_name not in raw_text
