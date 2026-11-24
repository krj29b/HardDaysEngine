"""Persistent-engine presentation on the user's disks (T11.8b1, §10.17)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pytest

from harddaysengine.app.audit import AuditRig
from harddaysengine.app.fight_audit import TROGG_ARENAS, build_fight_audit, fight_audit_frames
from harddaysengine.app.scene_assets import GameAssetBundle
from harddaysengine.assets.adf import DiskSet
from harddaysengine.sim.m68k import s16

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.adf


@pytest.mark.parametrize("name", TROGG_ARENAS)
def test_presented_trogg_fight_reaches_its_death_branch(adf_dir: Path, name: str) -> None:
    bundle = GameAssetBundle(DiskSet.from_directory(adf_dir))
    audit = build_fight_audit(bundle, name)
    samples = [(label, scene.canvas.pixels()) for label, scene in fight_audit_frames(audit)]
    assert len(samples) >= 2
    assert samples[-1][0].startswith("outcome_")
    assert s16(audit.scene.fight.actor(audit.opponent).hp) <= 0
    assert s16(audit.scene.fight.actor(1).hp) > 0
    assert not np.array_equal(samples[0][1], samples[-1][1])
    assert np.unique(samples[-1][1]).size > 8


@pytest.mark.gl
def test_repeated_real_gl_captures_do_not_advance_fight(adf_dir: Path) -> None:
    rig = AuditRig(adf_dir)
    try:
        audit = build_fight_audit(rig.fight_assets(), TROGG_ARENAS[0])
        audit.scene.advance(6)
        fight = audit.scene.fight
        state = (
            repr(fight.actors),
            repr(fight.engine.jobs),
            fight.rng.state,
        )
        first = rig.capture_fight(audit.scene, "first")
        second = rig.capture_fight(audit.scene, "second")
        assert state == (
            repr(fight.actors),
            repr(fight.engine.jobs),
            fight.rng.state,
        )
        np.testing.assert_array_equal(first.rgb, second.rgb)
        assert np.unique(first.rgb.reshape(-1, 3), axis=0).shape[0] > 8
    finally:
        rig.release()
