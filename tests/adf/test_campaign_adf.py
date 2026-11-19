"""Continuous input-only campaign acceptance, with a serialized replay (§12.41)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harddaysengine.app.campaign_audit import CampaignAudit, CampaignCheckpoint, verify_campaign
from harddaysengine.app.game import GameScene
from harddaysengine.app.input import load_replay, save_replay
from harddaysengine.assets.adf import DiskSet
from harddaysengine.sim.m68k import s16

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.adf


def test_fresh_campaign_wins_loots_buys_and_advances_day_with_replay(
    adf_dir: Path, tmp_path: Path
) -> None:
    disks = DiskSet.from_directory(adf_dir)
    audit = CampaignAudit(disks)
    points = []
    initial_lives = None
    loot_count = None
    for name in audit.run():
        session, world = audit.session, audit.session.overworld
        points.append(CampaignCheckpoint(name, session.tick_count, session.state_hash()))
        actor = world.ordered_actors()[0]
        if name == "new_campaign":
            initial_lives = actor.lives
            assert actor.hp == actor.hp_max == 20 and actor.strength == 1
            assert len(session.players) == 4 and all(a.kind != 4 for a in world.actors)
        elif name == "lair_entry":
            assert session.active_encounter is not None
            assert 0 < session.active_encounter.runtime.fight.total <= world.data().lairs[19].count
        elif name == "lair_won":
            assert session.last_defeat_mask == 0 and 0 < actor.hp <= actor.hp_max
            assert actor.lives == initial_lives and s16(world.lairs[19].creatures_left) <= 0
            loot_count = world.inventories[actor.inventory].byte(0)
        elif name == "loot_taken":
            assert loot_count is not None
            assert world.inventories[actor.inventory].byte(0) == loot_count + 1
            assert world.lairs[19].loot.byte(0) == world.lairs[19].gold == 0
        elif name == "day_and_moon_advanced":
            assert session.scene == GameScene.NEW_DAY
            assert world.day == world.moon_index == 1 and world.moon_subcount == 0
            assert world.turn_rules is not None
            assert world.moon_frame == world.turn_rules.moons[1]
    assert audit.session.scene == GameScene.OVERWORLD
    assert len(points) == 11
    path = tmp_path / "campaign.json"
    save_replay(path, audit.recording())
    loaded = load_replay(path)
    assert loaded == audit.recording()
    assert verify_campaign(disks, loaded, points) == audit.session.state_hash()
    wrong = [*points[:-1], CampaignCheckpoint(points[-1].name, points[-1].tick, "wrong")]
    with pytest.raises(ValueError, match="campaign_continues"):
        verify_campaign(disks, loaded, wrong)


@pytest.mark.gl
def test_rendered_campaign_checkpoints_replay_without_rendering(adf_dir: Path) -> None:
    from harddaysengine.app.audit import AuditRig

    disks = DiskSet.from_directory(adf_dir)
    audit, rig = CampaignAudit(disks), AuditRig(adf_dir)
    points = []
    try:
        for name in audit.run():
            before = audit.session.state_hash()
            points.append(CampaignCheckpoint(name, audit.session.tick_count, before))
            rig.capture(audit.session, name)
            rig.capture(audit.session, name)
            assert audit.session.state_hash() == before
    finally:
        rig.release()
    assert verify_campaign(disks, audit.recording(), points) == audit.session.state_hash()
