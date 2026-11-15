# Progress Tracker

Statuses: `TODO` · `IN PROGRESS (<agent>/<date>)` · `DONE` · `BLOCKED (<reason>)`
Rules: claim a task before you start it. When you finish, set it to `DONE` and add notes (gotchas,
deviations, follow-ups). Task details and acceptance criteria are in `IMPLEMENTATION_PLAN.md` §5
and the Remediation Plan in `docs/PLAYABILITY_AUDIT.md`.

| ID | Task | Deps | Status | Notes |
|---|---|---|---|---|
| T1.1 | Scaffold | – | DONE | Layers `cli/app/render/assets/sim`; Python pinned to 3.12; `uv sync` installs 60 packages (amitools 0.9.0 builds from sdist on Windows). The `pymoonstone-extract` / `pymoonstone-inspect` console scripts are declared but their modules land in T3.6 / T4.4. |
| T1.2 | Agent governance | T1.1 | DONE | `AGENTS.md` (protocol), `GEMINI.md` (pointer), `DEAD_ENDS.md`, plan, progress, ADRs. |
| T1.3 | Quality gate | T1.1 | DONE | `radon cc` always exits 0, so the gate parses `radon cc -j` and fails on rank ≥ C. Ruff's McCabe misses some complexity: boolean operators aren't counted, so a probe scoring 12 in radon passed ruff C901. Removed the `ANN101`/`ANN102` ignores (rules removed from ruff, which warned). Format and lint now cover `scripts/`. The sim ban list in `.importlinter` was extended with `random`, `time`, `datetime`, `os`, `io`, `threading`, `secrets`, `uuid`. |
| T1.4 | Config & ADF discovery | T1.1 | DONE | `app/config.py`, `--list-disks`, `adf_dir` fixture. The real disk-set test (`-m adf`) passes locally. |
| T1.5 | 68000 arithmetic | T1.1 | DONE | `sim/m68k.py`; `DIVS.W`/`DIVU.W` return `None` on overflow (V set, destination unchanged). |
| T1.6 | CI | T1.3 | DONE | `.github/workflows/ci.yml` runs `scripts/quality_gate.py` on Windows and Ubuntu. |
| T2.1 | Spec: disks | T1.* | DONE | Documented in `docs/ARCHITECTURE.md` §1. |
| T2.2 | Spec: codecs | T2.1 | DONE | Documented in `docs/ARCHITECTURE.md` §2. |
| T2.3 | Spec: graphics | T2.2 | DONE | Documented in `docs/ARCHITECTURE.md` §3. |
| T2.4 | Spec: combat data | T2.3 | DONE | Documented in `docs/ARCHITECTURE.md` §4. |
| T2.5 | Spec: executable data | T2.1 | DONE | Documented in `docs/ARCHITECTURE.md` §5. |
| T2.6 | Spec: audio | T2.2 | DONE | Documented in `docs/ARCHITECTURE.md` §6. |
| T2.7 | Spec: timing | T2.5 | DONE | Documented in `docs/ARCHITECTURE.md` §7 (incl. fight-script bytecode). |
| T2.8 | Spec: palette | T2.3 | DONE | Documented in `docs/ARCHITECTURE.md` §8. |
| T3.1 | ADF access & disk identification | T2.1 | DONE | `assets/adf.py`: OFSVolume & DiskSet. |
| T3.2 | LZSS / RNC unpackers | T2.2 | DONE | `assets/lzss.py`, `assets/rnc.py`. |
| T3.3 | Picture & cel decoders | T2.3, T3.2 | DONE | `assets/palette.py`, `picture.py`, `cel.py`. |
| T3.4 | collide.hit & frame metadata | T2.4 | DONE | `assets/hit.py` & `assets/arena.py`. |
| T3.5 | Audio decoders | T2.6 | DONE | `assets/audio.py` & `assets/hunk.py`. |
| T3.6 | Extractor & manifest | T3.1–T3.5 | DONE | `pymoonstone-extract` / `--extract-assets`. |
| T3.7 | Headless asset tests | T3.6 | DONE | Synthetic + `@pytest.mark.adf`. |
| T4.1 | Renderer core | T3.3 | DONE | `render/renderer.py` (OpenGL 3.3, 320×256). |
| T4.2 | Hitbox overlay | T4.1, T3.4 | DONE | `render/overlay.py`. |
| T4.3 | App host & 50 Hz loop | T4.1 | DONE | `app/loop.py`, inspector host. |
| T4.4 | Inspector CLI | T4.2, T4.3 | DONE | `--inspect-assets`, headless frames. |
| T5.1 | Sim PRNG & core integer types | T1.5, T2.7 | DONE | `sim/prng.py`, `sim/types.py`. |
| T5.2 | Runtime executable table decoders | T2.5, T3.5 | DONE | `assets/tables.py`. |
| T5.3 | 2.5D depth & hitbox/hurtbox resolver | T3.4, T5.1 | DONE | `sim/collision.py`. |
| T5.4 | 50 Hz PAL palette & VBL effect sim | T2.8, T5.1 | DONE | `sim/palette_fx.py`. |
| T6.1 | Combat animation & bytecode VM | T5.1, T5.2 | DONE | `sim/combat_vm.py` (real opcode set present; wiring/composition still incomplete). |
| T6.2 | Knight combat controller & equipment | T5.3, T6.1 | DONE | `sim/knight_combat.py`. |
| T6.3 | Monster AI state machines & Lairs | T5.2, T5.3, T6.1 | DONE | `sim/monster_ai.py`. |
| T7.1 | Overworld map, turn order & moon cycle | T5.1, T5.2 | DONE | `sim/overworld.py`. |
| T7.2 | Sacred places, towns & shops | T7.1 | DONE | `sim/locations.py`. |
| T8.1 | Paula 4-voice VBL synth & 8SVX mixer | T3.5, T5.2 | DONE | `app/audio.py`. |
| T8.2 | Multi-sprite 2.5D scene renderer & HUD | T4.1, T5.4, T6.1 | DONE | `render/scene.py`. |
| T8.3 | Input router, scene host & playable CLI | T6.2, T6.3, T7.2, T8.1, T8.2 | DONE | `app/input.py`, `app/game.py`, CLI. |
| T9.1 | Authentic overworld graph & road/lair alignment | T5.2, T7.1, T8.3 | DONE | Graph helpers exist; real-disk node count / moves_per_turn still wrong (see T10.5). |
| T9.2 | Edge-triggered overworld controls, node menu & New Day | T9.1 | DONE | STATUS / NEW_DAY / LOCATION wiring. |
| T9.3 | Interactive location & status screens | T7.2, T9.2 | DONE | `app/location_screens.py` (sub-location contrast still broken — T10.7). |
| T9.4 | `mog`-driven combat encounters, scripts & hitboxes | T5.3, T6.1, T6.3, T8.3 | DONE | Bridge exists; script pointer resolution & multi-part composition incomplete (T10.1–T10.4). |
| T9.5 | Combat projectiles, gore, SFX & Practice loop | T9.4 | DONE | Daggers, gore, SFX, Practice loop. |
| T9.6 | Palette FX, scene music switching & end-screen loop | T5.4, T8.1, T9.3, T9.5 | DONE | Fades/music/end screens (entry fade timing still off — T10.8). |

## Phase 10 — Fidelity & Playability Remediation (post-Audit v2)

Source of truth: `docs/PLAYABILITY_AUDIT.md` (Remediation Plan v2). Goal: make the game **actually playable** on real ADFs with multi-part sprites, working melee, and correct overworld turns.

| ID | Task | Deps | Status | Notes |
|---|---|---|---|---|
| T10.1 | Real `mog` fight-script pointer tables | T5.2, T6.1, T9.4 | DONE | Verified `KnightScriptTables` (`idle=0x1064`, `alt=0x1080`, 9 `action_scripts`, 9 `hurt_scripts`, `walk_scripts`) and 11 `MonsterScriptTables` decoded from `mog` `S_0`/`S_1`/`S_4`; all 12 `KnightAction`s execute real `S_4` scripts via `combat_bridge.compose_knight_pieces` & `GameSession` with multi-part draw lists (`test_tables.py`, `test_combat_bridge.py`, `test_playability_v2.py`: 34 passed). |
| T10.2 | Multi-part knight & monster composition | T10.1, T8.2 | DONE | `compose_knight_pieces` (`KN1.ob..KN4.ob`) + multi-slot fallback when `mog_s4=None` (`_fallback_multipart_knight_pieces`), and `compose_monster_pieces` + `get_monster_cels` (`scene_assets.py`) assemble layered bodies for knights and major monsters. |
| T10.3 | Top-left sprite anchor math | T4.1, T10.2 | DONE | Added `top_left: bool = False` to `SceneSprite`, `compute_sprite_draw_origin`, `MoonstoneRenderer.render_to_canvas`, and `cel_screen_origin`; fight-script draws position at top-left `(X+dx, Z-alt+dy)` (`tests/gl/test_scene_gl.py`, `test_renderer_gl.py`). |
| T10.4 | Melee hit detection & winnable combat | T5.3, T10.2, T10.3 | DONE | Restricted `kn4` `collide.hit` matching to `cel_slot == 3` (`KN4.ob`) `DRAW_FLAG_ATTACK` pieces; kept `base.defense` in `params_from_creature_node`; verified Trogg defeat on real disks (`test_adf_knight_attacks_and_defeats_trogg`). |
| T10.5 | Real-disk overworld graph & moves_per_turn | T7.1, T9.1 | DONE | `build_overworld_graph` populates canonical `ROAD` nodes alongside `mog` `S_2` sites + 4 lairs (`35` nodes) with `moves_per_turn = 3`; `test_adf_overworld_3_moves_and_stay_in_overworld` passes. |
| T10.6 | Knight class & creature combat palettes | T5.4, T9.6 | DONE | `_combat_palette` patches indices `6..8` for active `KnightId` class colors and `9..15` for creature scene colors (`15` = blood red); `_overworld_palette` replaces `test[8]` magenta index `31` with a grayscale highlight matching `mi.c` frames (`test_adf_overworld_marker_frames_and_highlight_palette`). |
| T10.7 | Town sub-location backdrops & text contrast | T9.3 | DONE | `location_backdrop_names(..., parent_location=...)` preserves `WaterDeep.PIV` for Waterdeep sub-menus; `render_location_menu` draws a dark filled panel behind sub-menu items (`test_waterdeep_sublocation_preserves_backdrop`). |
| T10.8 | Scene-entry fade-in timing | T5.4, T9.6 | DONE | `_begin_fade_in` starts `palette_fx.fade_in` on scene transition and advances in `step_tick`, eliminating pitch-black first frames (`test_adf_blue_knight_combat_palette_and_fade_in`). |
| T10.9 | Playability verification suite | T10.1–T10.8 | DONE | Verified via `test_playability_v2.py` (`13` synthetic + ADF checks), `scripts/quality_gate.py` (`7/7` gates, `238 passed`), `pytest -m adf` (`15 passed`), `pytest -m gl` (`8 passed`), and documented in `docs/PLAYABILITY_AUDIT.md` §5. |

## Session log

- 2026-11-14 · Antigravity · Phase 1 (T1.1–T1.6) · Skeleton, governance, gate and CI.
- 2026-11-15 · Antigravity · Phases 2–4 (T2.1–T2.8, T3.1–T3.7, T4.1–T4.4) · Spec + asset pipeline + inspector.
- 2026-11-15 · Antigravity Swarm · Phases 5–8 (T5.1–T5.4, T6.1–T6.3, T7.1–T7.2, T8.1–T8.3) · Sim core, tables, audio, scene renderer, game loop.
- 2026-11-15 · Antigravity · Phase 9 (T9.1–T9.6) · Overworld/location/combat bridge polish; marked DONE but Playability Audit v2 shows remaining fidelity gaps.
- 2026-11-15 · — · Phase 10 opened from Playability Audit v2. Priority order: T10.1 → T10.2 → T10.3 → T10.4 (combat path), then T10.5 (overworld), then T10.6–T10.8 (presentation), close with T10.9 verification.
- 2026-11-15 · Antigravity · Phase 10 (`T10.1`–`T10.9`) · Audited and fixed multi-slot knight fallback (`combat_bridge.py`), overworld index-31 highlight palette (`game.py`), added `T10.2`/`T10.6` tests (`test_playability_v2.py`), and verified `quality_gate.py` (`238 passed`), `-m adf` (`15 passed`), and `-m gl` (`8 passed`).
