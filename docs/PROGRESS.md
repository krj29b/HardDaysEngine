# Progress Tracker

Statuses: `TODO` · `IN PROGRESS (<agent>/<date>)` · `DONE` · `BLOCKED (<reason>)`
Rules: claim a task before you start it. When you finish, set it to `DONE` and add notes (gotchas,
deviations, follow-ups). Task details and acceptance criteria are in `IMPLEMENTATION_PLAN.md` §5.

| ID | Task | Deps | Status | Notes |
|---|---|---|---|---|
| T1.1 | Scaffold | – | DONE | Layers `cli/app/render/assets/sim`; Python pinned to 3.12; `uv sync` installs 60 packages (amitools 0.9.0 builds from sdist on Windows). The `pymoonstone-extract` / `pymoonstone-inspect` console scripts are declared but their modules land in T3.6 / T4.4. |
| T1.2 | Agent governance | T1.1 | DONE | `AGENTS.md` (protocol), `GEMINI.md` (pointer), `DEAD_ENDS.md`, plan, progress, ADRs. |
| T1.3 | Quality gate | T1.1 | DONE | `radon cc` always exits 0, so the gate parses `radon cc -j` and fails on rank ≥ C. Ruff's McCabe misses some complexity: boolean operators aren't counted, so a probe scoring 12 in radon passed ruff C901. Removed the `ANN101`/`ANN102` ignores (rules removed from ruff, which warned). Format and lint now cover `scripts/`. The sim ban list in `.importlinter` was extended with `random`, `time`, `datetime`, `os`, `io`, `threading`, `secrets`, `uuid`. |
| T1.4 | Config & ADF discovery | T1.1 | DONE | `app/config.py`, `--list-disks`, `adf_dir` fixture. The real disk-set test (`-m adf`) passes locally. |
| T1.5 | 68000 arithmetic | T1.1 | DONE | `sim/m68k.py`; `DIVS.W`/`DIVU.W` return `None` on overflow (V set, destination unchanged). |
| T1.6 | CI | T1.3 | DONE | `.github/workflows/ci.yml` runs `scripts/quality_gate.py` on Windows and Ubuntu. |
| T2.1 | Spec: disks | T1.* | DONE | Documented in `docs/ARCHITECTURE.md` §1: 901,120 B OFS geometry, hash/header/data block layouts, 3-disk inventory (33/88/91 files), marker files (`program`+`mog`, `HE1.ob`, `be1.c`), leading-space `" drag.cel"` on Disk A, and largest-wins duplicate rule (`kn1.ob` 5 B stub vs 20,760 B). |
| T2.2 | Spec: codecs | T2.1 | DONE | Documented in `docs/ARCHITECTURE.md` §2: Moonstone forward LZSS (MSB-first 8-item flag byte, 16-bit backref `count = 34 - (w >> 11)`, `dist = w & 0x7FF`) and RNC1 backward bit/byte stream (`RNC\x01`). |
| T2.3 | Spec: graphics | T2.2 | DONE | Documented in `docs/ARCHITECTURE.md` §3: `.piv`/`.p` 320×200 planar pictures, 9-picture `test` pack sizes, `.cel`/`.ob`/`.c`/`.f`/`.font` 10 B header + 10 B frame table (`offset`, `width`, `height`, `hot_and_flags`, `plane_bits`), in-place mirroring, and `.stile` tile maps. |
| T2.4 | Spec: combat data | T2.3 | DONE | Documented in `docs/ARCHITECTURE.md` §4: 2.5D coordinates (`uwX`, `uwY` depth, `uwHeight` altitude, `depthClose <= 10`), hurtbox cel bitplane masks (`draw_flags & 1`), `collide.hit` ASCII grammar & binary layout (`draw_flags & 2`), and `.t` arena obstacle + 2,400 B tile scripts. |
| T2.5 | Spec: executable data | T2.1 | DONE | Documented in `docs/ARCHITECTURE.md` §5: AmigaDOS Hunk format (`HUNK_HEADER`, `CODE`, `DATA`, `BSS`, `RELOC32`) and key data hunks in `program` (35 hunks) and `mog` (46 hunks, `S_4`, `S_33`, `S_44`, `S_45`). |
| T2.6 | Spec: audio | T2.2 | DONE | Documented in `docs/ARCHITECTURE.md` §6: 13 `.a` LZSS 8-bit signed PCM banks, 131 × 14 B `SynthInst` records in `mog` `S_45`, 168-sequence 4-voice VBL synth in `S_44`, and `.cmp` RNC1 ProTracker (`M.K.`) modules. |
| T2.7 | Spec: timing | T2.5 | DONE | Documented in `docs/ARCHITECTURE.md` §7: 50 Hz PAL VBL clock, 6/5 VBL combat frame divisor (`8.33`/`10.0` Hz), 6-byte draw opcodes (`$00..$1F`), control opcodes (`$80..$D4`, `$FF`), 132 B `Knight` record, and 50 B `CombatJob`. |
| T2.8 | Spec: palette | T2.3 | DONE | Documented in `docs/ARCHITECTURE.md` §8: 12-bit `$0RGB` word decoding (`bit 15` flag vs `<< 1`), 32-colour 5-plane combat palette layout (knight class `6..8`, creature `9..14`, blood `15`, region `16..28`), and VBL fade/cycle/flash. |
| T3.1 | ADF access & disk identification | T2.1 | DONE | `assets/adf.py`: `OFSVolume` & `DiskSet` identify Disks A (`program`+`mog`), B (`HE1.ob`), and C (`be1.c`), resolve duplicates by largest size (`KN1.ob` 20,760 B on B beats 5 B stub on A), and support `T_LIST` extension blocks. |
| T3.2 | LZSS / RNC unpackers | T2.2 | DONE | `assets/lzss.py` (forward MSB-first LZSS) and `assets/rnc.py` (backward sentinel-bit RNC1 stream decoding `music.cmp` to 123,228 B and `vmusic.cmp` to 87,792 B). |
| T3.3 | Picture & cel decoders | T2.3, T3.2 | DONE | `assets/palette.py`, `assets/picture.py`, `assets/cel.py`: vectorized numpy `unpackbits`/`packbits` planar-to-indexed conversion, 32-colour OCS palette decoding, 9-picture `test` pack splitting, and cel horizontal mirroring. |
| T3.4 | collide.hit & frame metadata | T2.4 | DONE | `assets/hit.py` & `assets/arena.py`: `_DigitStream` parses all 14 `collide.hit` weapon point sets (including contiguous 3-digit coordinates and double-`99` EOF sentinel), computes cel hurtbox bounds, and composites `.t` arena/Lair backgrounds. |
| T3.5 | Audio decoders | T2.6 | DONE | `assets/audio.py` & `assets/hunk.py`: decodes all 13 `.a` sound banks (12 single-section Hunk-wrapped banks + `wn.a` IFF 8SVX), exports 8-bit RIFF WAVs, unpacks `.cmp` `FLT4`/`M.K.` modules, and extracts 131 synth instruments from `mog` `S_45`. |
| T3.6 | Extractor & manifest | T3.1–T3.5 | DONE | `assets/manifest.py`, `assets/extract.py`, `cli/extract.py` (`pymoonstone-extract` & `pymoonstone --extract-assets`): writes PNG/WAV/MOD assets and deterministic `manifest.json` with SHA-256 digests and sorted keys. |
| T3.7 | Headless asset tests | T3.6 | DONE | `tests/fixtures/synthetic_adf.py`, `tests/unit/test_assets_synthetic.py`, and `tests/adf/test_real_disks.py` (66 passed, 95% total coverage). |
| T4.1 | Renderer core | T3.3 | DONE | `render/renderer.py`: `MoonstoneRenderer` (OpenGL 3.3 core `#version 330 core`, 320×256 virtual canvas FBO, `R8` indexed texture + `32×1` RGBA palette lookup texture, aspect-preserving blitter, CRT scanline shader, and `read_canvas_rgba()`). Zero `pygame` imports. |
| T4.2 | Hitbox overlay | T4.1, T3.4 | DONE | `render/overlay.py`: builds green hurtbox wireframe rectangles (`HURTBOX_OUTER_COLOR`, `HURTBOX_TIGHT_COLOR`) and red weapon hitbox + attack-point crosshairs (`HITBOX_BOX_COLOR`, `HITBOX_POINT_COLOR`) rendered via `moderngl.LINES`. |
| T4.3 | App host & 50 Hz loop | T4.1 | DONE | `app/loop.py` (`Accumulator50Hz` integer nanosecond 50 Hz fixed-timestep clock) and `app/inspector.py` (`pygame-ce` OpenGL 3.3 core window host + standalone headless runner). |
| T4.4 | Inspector CLI | T4.2, T4.3 | DONE | `cli/inspect_assets.py` & `cli/main.py` (`pymoonstone-inspect` and `pymoonstone --inspect-assets`): supports `--cel`, `--background`, `--knight`, `--no-overlay`, `--headless-frames`, and `--screenshot`, plus interactive keyboard controls and `tests/gl/test_renderer_gl.py`. |
| T5.1 | Sim PRNG & core integer types | T1.5, T2.7 | DONE | `sim/prng.py` (`SimPrng` 32-bit 68k-width LCG) & `sim/types.py` (`Pos25D`, `Rect2D`, `Direction`, `KnightId`, `EntityId`, `TickCount`). 100% coverage, zero floats/I/O. |
| T5.2 | Runtime executable table decoders | T2.5, T3.5 | DONE | `assets/tables.py` (`load_mog_tables`, `load_program_tables`, combat action offsets, creature/lair nodes, map sites, encounter bands, built-in 8x8 font, 4-voice synth streams/waveforms/instruments). 97% coverage (including `@pytest.mark.adf`). |
| T5.3 | 2.5D depth & hitbox/hurtbox resolver | T3.4, T5.1 | DONE | `sim/collision.py` (`depth_close`, `CelMask`, `resolve_combat_hits`, `bodies_blocked`, `resolve_arena_movement`). 96% coverage, zero floats/I/O. |
| T5.4 | 50 Hz PAL palette & VBL effect sim | T2.8, T5.1 | DONE | `sim/palette_fx.py` (`PaletteFxState`, `CycleRange`, `FlashOverride`, `cycle_step`, nibble fading & flash restore). 100% coverage, zero floats/I/O. |
| T6.1 | Combat animation & bytecode VM | T5.1, T5.2 | DONE | `sim/combat_vm.py` (`CombatJob`, `CombatWork`, `step_frame`, `step_vbl`, `step_pool_vbl`, 6 B draw & `$80..$D4`/`$FF` control opcodes, 6/5 VBL divisors). 99% coverage, zero floats/I/O. |
| T6.2 | Knight combat controller & equipment | T5.3, T6.1 | DONE | `sim/knight_combat.py` (`KnightCombatRecord`/`KnightState`, 8-way + fire move table, secondary weapon switch/ammo, block/armour/stamina, hit-stun, knockdown, i-frames, death). 96% coverage, zero floats/I/O. |
| T6.3 | Monster AI state machines & Lairs | T5.2, T5.3, T6.1 | DONE | `sim/monster_ai.py` (`MonsterArchetype` x10 incl. Balok/Dragon/Guardians, 2.5D perception, `SimPrng` attack selection, projectiles, `LairEncounter` multi-wave state). 95% coverage, zero floats/I/O. |
| T7.1 | Overworld map, turn order & moon cycle | T5.1, T5.2 | DONE | `sim/overworld.py` (`OverworldGraph`, `OverworldState`, `create_campaign`, `move_knight`, 28-day moon cycle, Dragon patrol, Lair keys, Moonstones, PvP duels, Stonehenge victory). 96% coverage, zero floats/I/O. |
| T7.2 | Sacred places, towns & shops | T7.1 | DONE | `sim/locations.py` (Highwood merchant/healer/tavern dice, Waterdeep mystic/training, Stonehenge altar ritual, Lair loot distribution). 94% coverage, zero floats/I/O. |
| T8.1 | Paula 4-voice VBL synth & 8SVX mixer | T3.5, T5.2 | DONE | `app/audio.py` (`PaulaSynthMixer`, `AudioEngine`, 4-voice 50 Hz VBL synth `S_44`/`S_45`, `.a`/8SVX sample mixing, `.cmp` ProTracker playback with headless fallback). |
| T8.2 | Multi-sprite 2.5D scene renderer & HUD | T4.1, T5.4, T6.1 | DONE | `render/scene.py` (`SceneRenderer`, `SceneSprite` painter Y-sorting, numpy blitting, `BitmapFont`, combat/overworld HUDs, `$0RGB` palette LUT bridge). 4/4 GL tests pass. |
| T8.3 | Input router, scene host & playable CLI | T6.2, T6.3, T7.2, T8.1, T8.2 | DONE | `app/input.py` (4-player keyboard + `pygame.joystick` `InputRouter`, `ReplayRecording`), `app/game.py` (`GameSession` state machine `TITLE -> KNIGHT_SELECT -> OVERWORLD -> COMBAT -> VICTORY/GAME_OVER`, `state_hash()`, `run_headless_session`, `run_game_window`), and `cli/main.py` (`--play`, `--record`, `--replay`, `--headless-ticks`, `--seed`). Follow-ups for full Amiga gameplay tracked in `T9.1`–`T9.6`. |
| T9.1 | Authentic overworld graph & road/lair alignment | T5.2, T7.1, T8.3 | DONE | Added pure-integer `MapSiteInput`, `build_overworld_graph_from_sites()`, and `create_campaign(..., graph=...)` in `sim/overworld.py` + unit tests in `tests/unit/test_overworld.py`. |
| T9.2 | Edge-triggered overworld controls, node menu & New Day | T9.1 | DONE | Wired `program`/`mog` `build_overworld_graph()` into `GameSession`, added `GameScene.STATUS` (`secondary`) and `GameScene.NEW_DAY` lunar banner, and connected `TOWN_VISIT`/`STONEHENGE_RITUAL` to `GameScene.LOCATION` via `app/location_screens.py`. |
| T9.3 | Interactive location & status screens | T7.2, T9.2 | DONE | Created `app/location_screens.py` (`LocationMenuState`, `step_location_menu`, `render_location_menu`, `render_status_screen`, `render_new_day_screen`) + 5 unit tests in `tests/unit/test_location_screens.py`, plus `GameAssetBundle` runtime loaders (`get_program_tables`, `get_mog_tables`, `get_hit_sets`, `load_victory_music`, `get_blood_cel`, `build_overworld_graph`) in `app/scene_assets.py`. |
| T9.4 | `mog`-driven combat encounters, scripts & hitboxes | T5.3, T6.1, T6.3, T8.3 | DONE | Created `app/combat_bridge.py` (`build_mog_encounter`, `cel_mask_from_frame`, `resolve_melee_attack_points`, `clamp_entity_to_arena`) + 8 unit tests in `tests/unit/test_combat_bridge.py`. |
| T9.5 | Combat projectiles, gore, SFX & Practice loop | T9.4 | DONE | Wired `build_mog_encounter`, `resolve_melee_attack_points`, `clamp_entity_to_arena`, throwing daggers (`secondary`), `blo.cel` gore particles gated by `gore_enabled`, `.a` combat SFX triggers, and `Practice` return-to-menu loop in `app/game.py`. |
| T9.6 | Palette FX, scene music switching & end-screen loop | T5.4, T8.1, T9.3, T9.5 | DONE | Integrated `PaletteFxState` fades/flashes in `GameSession`, `load_victory_music()` / `load_title_music()` switching, `VICTORY`/`GAME_OVER` return-to-menu on confirm, and full-loop tests in `tests/unit/test_game_session.py` (200 passed + 6 GL passed). |

## Session log

- 2026-11-14 · Antigravity · Phase 1 (T1.1–T1.6) · Skeleton, governance, gate and CI. Full gate
  green: ruff format/lint, mypy strict (10 files), import-linter 4/4 kept, radon all A/B,
  vulture clean, pytest 46 passed (98 % coverage; uncovered: `__main__.py`). Negative controls:
  a planted `sim/_probe.py` importing `time`, with complexity 12, broke import-linter, the radon
  gate and `test_layering`.
- 2026-11-15 · Antigravity · Phases 2–4 (T2.1–T2.8, T3.1–T3.7, T4.1–T4.4) · Completed binary
  format & timing specification in `docs/ARCHITECTURE.md` (§1–§8), implemented the headless ADF
  asset pipeline (`assets/adf.py`, `lzss.py`, `rnc.py`, `palette.py`, `picture.py`, `cel.py`,
  `hit.py`, `arena.py`, `audio.py`, `hunk.py`, `manifest.py`, `extract.py`), and built the
  ModernGL 3.3 + `pygame-ce` 50 Hz asset & hitbox inspector (`render/renderer.py`,
  `render/overlay.py`, `app/loop.py`, `app/inspector.py`, `cli/inspect_assets.py`). Verified
  extraction of all 32 pictures, 50 cels, 56 arenas, 13 sound banks, and 2 tracker modules from
  `./disks/`, and verified all quality gates (`scripts/quality_gate.py` + `pytest -m gl`).
- 2026-11-15 · Antigravity Swarm · Phases 5–8 (T5.1–T5.4, T6.1–T6.3, T7.1–T7.2, T8.1–T8.3) ·
  Completed the pure-integer 50 Hz simulation core (`sim/prng.py`, `types.py`, `collision.py`,
  `palette_fx.py`, `combat_vm.py`, `knight_combat.py`, `monster_ai.py`, `overworld.py`,
  `locations.py`), runtime executable table loaders (`assets/tables.py`), Paula 4-voice synth &
  8SVX mixer (`app/audio.py`), 2.5D multi-sprite scene renderer & HUD (`render/scene.py`), and
  4-player input router, scene host, replay system & playable CLI (`app/input.py`, `app/game.py`,
  `cli/main.py`). Verified with `scripts/quality_gate.py` (177 passed) and `pytest -m gl` (6 passed).
