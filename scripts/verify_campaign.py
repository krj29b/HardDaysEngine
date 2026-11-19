"""Record, replay and render the real-disk campaign route (ARCHITECTURE.md §12.41)."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from harddaysengine.app.audit import AuditRig, build_contact_sheet
from harddaysengine.app.campaign_audit import CampaignAudit, CampaignCheckpoint, verify_campaign
from harddaysengine.app.config import resolve_adf_dir
from harddaysengine.app.input import load_replay, save_replay
from harddaysengine.assets.adf import DiskSet

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adf-dir", default=None)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "build" / "campaign")
    args = parser.parse_args()
    adf_dir = resolve_adf_dir(
        args.adf_dir, environ=os.environ, config_path=ROOT / "config.toml", default=ROOT / "disks"
    )
    disks = DiskSet.from_directory(adf_dir)
    audit, rig = CampaignAudit(disks), AuditRig(adf_dir)
    points, shots = [], []
    try:
        for name in audit.run():
            session = audit.session
            point = CampaignCheckpoint(name, session.tick_count, session.state_hash())
            points.append(point)
            shots.append(rig.capture(session, name))
            if session.state_hash() != point.state_hash:
                raise RuntimeError(f"rendering changed campaign state at {name}")
    finally:
        rig.release()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    replay_path = args.out_dir / "replay.json"
    save_replay(replay_path, audit.recording())
    final_hash = verify_campaign(disks, load_replay(replay_path), points)
    build_contact_sheet(shots).save(args.out_dir / "campaign.png")
    report = {"final_hash": final_hash, "checkpoints": [asdict(point) for point in points]}
    (args.out_dir / "verification.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Verified {len(audit.frames)} ticks, {len(points)} checkpoints: {final_hash}")
    print(f"Replay, verification report and contact sheet: {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
