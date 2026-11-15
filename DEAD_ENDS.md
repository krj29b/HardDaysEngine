# Dead Ends: failed approaches log

This file records approaches that failed and must **never** be repeated. Add a row whenever an
approach is abandoned, with enough detail that the next agent won't try it again.

| Date | Approach Tried | Why It Failed | Files Touched |
|------|----------------|---------------|---------------|
| 2026-11-14 | Treating OpenMoonstone's DOS loaders (`piv.py`, `cmp.py`, `lz77.rs`) as the Amiga format reference | Not attempted in code; ruled out during alignment. OpenMoonstone reads the **DOS** release, and the Amiga files on the ADFs use different containers. moonstone-ace is the Amiga format reference. | n/a |
| 2026-11-15 | Replaying a fight script from its first byte every frame (`tick // 6` steps in a throwaway job) to pick sprites, while a separate timer state machine holds the fighter's state | Script state (loops, motion blocks, frame set, armed jumps, spawned objects) is lost each frame, and animation and rules disagree. Audit v3 §6; ADR-009. The job must keep its script position across ticks and be the only fighter state. | `app/combat_bridge.py`, `sim/knight_combat.py`, `sim/monster_ai.py` |
| 2026-11-15 | Modelling the overworld as a node graph with hand-placed road nodes and hops per day | The original has no paths: knights move one pixel per step on the map and a proximity scan lists what is near. The graph produced an encounter on nearly every hop. ADR-009. | `sim/overworld.py`, `app/scene_assets.py` |
| 2026-11-15 | Patching symptoms against a green test suite (remediation passes 1 and 2) | The tests asserted the invented models, so they passed while the game stayed unplayable. Presentation work needs reviewed real-disk contact sheets (`scripts/audit_shots.py`). | `tests/unit/test_playability_v2.py` and others |
