# Implementation Plan

## 1. Goal

A faithful, clean-room Python reimplementation of *Moonstone: A Hard Days Knight* (Amiga, 1991):
- a deterministic 50 Hz integer simulation,
- a headless asset pipeline that reads the user's original `.adf` images,
- a ModernGL presentation layer hosted by `pygame-ce`.

## 2. Stack

| Concern | Choice |
|---|---|
| Language | Python ≥ 3.12 (pinned to 3.12 through `.python-version`) |
| Window, input, audio | `pygame-ce` (in `app/` only) |
| GPU | `moderngl`, OpenGL 3.3 core (in `render/` only) |
| ADF / OFS | `amitools` plus our own decoders (`assets/`) |
| Arrays | `numpy` (decoding, bitplane conversion, render uploads) |
| Manifest schema | `pydantic` v2 |
| ECS (simulation entities) | `esper` (pure Python, deterministic iteration order) |
| Image export | `pillow` (PNG output of the extractor) |
| Gates | ruff (lint, format, McCabe ≤ 10), mypy strict, import-linter, radon (≤ B), vulture (≥ 80 %), pytest + coverage |

## 3. Layers

```
cli ──▶ app ──▶ render ──▶ assets ──▶ sim
```

| Layer | Responsibility | May not import |
|---|---|---|
| `sim` | 50 Hz kernel, 68000 integer semantics (`m68k.py`), PRNG, collision, knight and monster state machines, overworld rules | any other layer, `pygame`, `moderngl`, `PIL`, `amitools`, I/O and clock modules (`pathlib`, `os`, `io`, `time`, `random`, ...) |
| `assets` | ADF/OFS access, LZSS/RNC unpackers, picture/cel/hit/8SVX/module decoders, hunk-executable table readers, manifest, extractor | `render`, `app`, `cli`, `pygame`, `moderngl` |
| `render` | 320×256 canvas FBO, palette lookup and cycling, sprite batches, CRT pass, debug line overlay | `app`, `cli`, `pygame`, `amitools` |
| `app` | config, window and GL context creation, 50 Hz accumulator loop, input router, audio mixer | `cli` |
| `cli` | console scripts | nothing |

Contracts: `.importlinter` checks the import graph. `tests/unit/test_layering.py` additionally catches
`TYPE_CHECKING` and `import_module` imports and float usage in `sim`.

## 4. Timing model

- One simulation tick = one PAL VBL, at a fixed **50.00 Hz** (the original hardware runs at 49.92 Hz).
- Scene logic that originally ran every N VBLs (fight frames: 6 VBLs, sometimes 5) is modelled
  with integer per-scene divisors inside `sim`. The exact table goes in `ARCHITECTURE.md` (Phase 2).
- The `app` loop uses a fixed-timestep accumulator. Rendering may interpolate, but never
  feeds back into `sim`.

## 5. Phases and tasks

Status lives in `PROGRESS.md`. Every task ends with a green `scripts/quality_gate.py`.

### Phase 1: Governance and skeleton
- **T1.1 Scaffold:** packages for every layer, `pyproject.toml`, `.importlinter`, MIT LICENSE,
  `.gitignore` (no `.adf` or `build/`), README, `__version__`.
  *Accept:* `uv sync` succeeds; `pymoonstone --version` prints the version.
- **T1.2 Agent governance:** `AGENTS.md`, `GEMINI.md`, `DEAD_ENDS.md`, `docs/{IMPLEMENTATION_PLAN,PROGRESS,DECISIONS}.md`.
- **T1.3 Quality gate:** `scripts/quality_gate.py` runs all seven gates and stops at the first
  failure. Radon must actually fail on rank ≥ C. *Accept:* negative-control probes show
  import-linter, radon and the layering test each failing on a planted violation.
- **T1.4 Config and ADF discovery:** `app/config.py` (CLI > env > `config.toml` > `./disks`),
  `pymoonstone --list-disks`, and an `adf_dir` pytest fixture that skips without disks.
- **T1.5 68000 arithmetic:** `sim/m68k.py` covering u8/u16/u32, s8/s16/s32, truncating `c_div`/`c_mod`,
  `DIVS.W`/`DIVU.W` with overflow → `None`, and `MULS.W`/`MULU.W`.
- **T1.6 CI:** GitHub Actions on Windows and Ubuntu, Python 3.12, running the full gate.
  `adf` tests skip on CI because no disks are present.

### Phase 2: Format and behaviour specification (`docs/ARCHITECTURE.md`)
- **T2.1 Disks:** OFS layout, the per-disk file catalogue, marker files, duplicate resolution (`kn1.ob`).
- **T2.2 Codecs:** LZSS (byte level, edge cases) and RNC ProPack (`.cmp`).
- **T2.3 Graphics:** picture header and palette words, planar layout (320×200, 4/5 planes),
  cel family header and frame table, plane-presence bits, transparency, mirroring, `.stile`
  tile maps, the `test` picture pack, fonts.
- **T2.4 Combat data:** `collide.hit` grammar, attack points vs hurt areas, `.t` arena object
  tables, fight-script opcodes, the per-frame anchor and offset model, depth (2.5D) bands.
- **T2.5 Executable data:** the Amiga hunk format; where in `program`/`mog` the animation, timing,
  AI and creature tables live (offsets identified by CRC-checked file version). Read at runtime only.
- **T2.6 Audio:** the `.a` 8SVX banks, the `music.cmp`/`vmusic.cmp` module format and its
  ProTracker deviations, and the fight synth sequencer data.
- **T2.7 Timing:** VBL-driven scene divisors, fight frame budget, input sampling.
- **T2.8 Palette:** scene palette assembly (backdrop / knight / creature / region ranges), the
  forced entries (index 0 black, 15 blood red), palette cycling.

### Phase 3: ADF asset pipeline
- **T3.1** ADF volume access and disk identification by marker files.
- **T3.2** LZSS and RNC unpackers with synthetic round-trip and edge-case tests.
- **T3.3** Picture and cel decoders producing indexed numpy arrays plus palettes (RGB 4-4-4 → 8-8-8).
- **T3.4** `collide.hit` parser and frame metadata.
- **T3.5** 8SVX / sample bank decoder, plus the module loader.
- **T3.6** `pymoonstone-extract`: writes `./build/assets/` with PNG/WAV outputs and a deterministic
  JSON manifest (sorted keys, SHA-256 of every source and output, no timestamps).
- **T3.7** Headless tests: synthetic fixtures for CI; `adf` tests check real file counts,
  palette words and frame tables against values computed independently.

### Phase 4: Asset and hitbox inspector
- **T4.1** `render/`: canvas FBO, indexed-texture palette shader, sprite quads, CRT post-pass.
- **T4.2** `render/overlay.py`: GL line primitives, hurt areas in green, weapon points and boxes in red.
- **T4.3** `app/`: window host, 50 Hz accumulator, inspector key bindings
  (next/prev asset, next/prev frame, play/pause, overlay toggle).
- **T4.4** `pymoonstone --inspect-assets` and `pymoonstone-inspect`, with `gl`-marked smoke tests
  run on a standalone context.

### Phase 5: Deterministic 50 Hz simulation foundation & executable table loaders
*(Designed for parallel multi-agent execution — each task owns disjoint modules.)*
- **T5.1 Simulation PRNG & fixed-point types (`src/pymoonstone/sim/prng.py`, `src/pymoonstone/sim/types.py`, `tests/unit/test_prng.py`):**
  - Deterministic 68k PRNG (`rand_u16`, `rand_range`, seed state) and core integer value types
    (`Pos25D`, `Rect2D`, `Direction`, `KnightId`, `EntityId`, `TickCount`).
  - *Accept:* 100% integer-only (`test_layering.py` passes); unit tests verify deterministic sequence reproduction and state serialization.
- **T5.2 Runtime executable table decoders (`src/pymoonstone/assets/tables.py`, `tests/unit/test_tables.py`):**
  - Extract runtime data tables from `program` and `mog` hunks (as specified in `ARCHITECTURE.md` §5 and §7):
    combat action-to-animation script pointer tables, creature parameter records (`mog` `S_4`/`S_33`),
    Lair/region metadata, and 4-voice synth sequence streams (`mog` `S_44`).
  - *Accept:* Pure data records (`dataclass(slots=True, frozen=True)`) passed into `sim/` without `sim/` performing I/O; verified against synthetic hunks and `@pytest.mark.adf` real-disk tests.
- **T5.3 2.5D depth & hurtbox/hitbox collision resolver (`src/pymoonstone/sim/collision.py`, `tests/unit/test_collision.py`):**
  - Implement the 2.5D depth-band gate (`abs(attacker_y - defender_y) <= depth_threshold`, default `10` px per `ARCHITECTURE.md` §4.1),
    cel bitplane hurtbox AABB & mask testing (`draw_flags & 1`), `collide.hit` attack-point vs hurtbox intersection (`draw_flags & 2`),
    and `.t` arena obstacle/boundary clamping.
  - *Accept:* Deterministic unit tests covering front/back depth rejection, mirrored sprites, obstacle blocking, and multi-target hits.
- **T5.4 50 Hz PAL palette & VBL effect state machine (`src/pymoonstone/sim/palette_fx.py`, `tests/unit/test_palette_fx.py`):**
  - Pure integer simulation of the 32-colour 12-bit `$0RGB` palette state (`ARCHITECTURE.md` §8.2):
    step-toward-target fading, colour-range rotation/cycling (`cycle_step`), and lightning/blood flash overrides.
  - *Accept:* Unit tests verifying exact per-VBL `$0RGB` transitions and zero float usage.

### Phase 6: Combat bytecode VM, knight controls & monster AI
*(Parallelizable across `sim/combat_vm.py`, `sim/knight_combat.py`, and `sim/monster_ai.py` once `T5.1`–`T5.3` complete.)*
- **T6.1 Combat animation & bytecode VM (`src/pymoonstone/sim/combat_vm.py`, `tests/unit/test_combat_vm.py`):**
  - Implement the 50 B `CombatJob` state and 6-byte frame draw / variable-length control opcode interpreter
    (`$00..$1F` draw frame with `(dx, dy)` displacement and `draw_flags`; control opcodes `$80..$D4`, `$FF` per `ARCHITECTURE.md` §7.2–§7.3)
    stepping on the 6-VBL (normal) / 5-VBL (fast) combat frame divisor.
  - *Accept:* Deterministic execution of multi-frame sequences, loop/branch opcodes, velocity integration, and sound/spawn triggers.
- **T6.2 Knight combat controller & equipment state (`src/pymoonstone/sim/knight_combat.py`, `tests/unit/test_knight_combat.py`):**
  - Implement the 132 B `Knight` record (`ARCHITECTURE.md` §7.4) and 8-direction + fire-button action decoder
    (walk, run, roll, block, overhead slash, thrust, low sweep, weapon switch, hit-stun, knockdown, and death states).
  - *Accept:* Full state-transition coverage for sword & secondary weapon moves, stamina/hit-point deduction, and invulnerability frames.
- **T6.3 Monster AI state machines & Lair encounters (`src/pymoonstone/sim/monster_ai.py`, `tests/unit/test_monster_ai.py`):**
  - Implement monster perception, approach/retreat movement in 2.5D, attack selection, projectile/hazard spawning,
    and boss behaviours (Balok, Dragon, Guardians) driven by runtime tables from `T5.2`.
  - *Accept:* Deterministic N-tick battle simulations between a Knight and each monster archetype with reproducible outcomes from a fixed PRNG seed.

### Phase 7: Overworld board game & locations
*(Independent of Phase 6 combat internals — can run in parallel after `T5.1`.)*
- **T7.1 Overworld map, turn order & moon phase cycle (`src/pymoonstone/sim/overworld.py`, `tests/unit/test_overworld.py`):**
  - Implement the 1–4 knight board-game loop (`program` state machine): region graph and node movement,
    28-day moon phase progression, Lair key/Moonstone tracking, Dragon patrol movement on the map, and PvP encounter triggers.
  - *Accept:* Full multi-turn deterministic board simulation from campaign start to Stonehenge victory condition.
- **T7.2 Sacred places, towns & shops (`src/pymoonstone/sim/locations.py`, `tests/unit/test_locations.py`):**
  - Implement Highwood (merchant, healer, tavern dice game), Waterdeep (mystic, spell/scroll purchase, training),
    Stonehenge (Moonstone altar ritual), and Lair loot distribution.
  - *Accept:* Unit tests covering gold/inventory transactions, stat upgrades, dice game PRNG rules, and Moonstone placement.

### Phase 8: Audio synthesizer, multi-scene presentation & full game loop
- **T8.1 Paula 4-voice VBL synth & 8SVX mixer (`src/pymoonstone/app/audio.py`, `tests/unit/test_audio_mixer.py`):**
  - Implement the 50 Hz 4-voice waveform/envelope synthesizer (`mog` `S_44`/`S_45` per `ARCHITECTURE.md` §6.2),
    `.a` 8SVX sample playback triggered by combat VM opcodes, and `.cmp` ProTracker module playback via `pygame.mixer`.
  - *Accept:* Headless buffer synthesis tests verifying 4-channel PCM generation and sample mixing without requiring audio hardware in CI.
- **T8.2 Multi-sprite 2.5D scene renderer & HUD (`src/pymoonstone/render/scene.py`, `tests/gl/test_scene_gl.py`):**
  - Extend `render/` with Y-sorted 2.5D sprite batching (shadows, corpses, projectiles, knights, monsters, foreground arena obstacles),
    bitmap font text rendering (`.f` / `.font`), and combat/overworld HUD bars.
  - *Accept:* Standalone GL 3.3 tests verifying painter's-algorithm Y-sorting, palette slot swapping, and HUD rendering.
- **T8.3 Input router, scene coordinator & playable CLI (`src/pymoonstone/app/input.py`, `src/pymoonstone/app/game.py`, `tests/unit/test_game_session.py`):**
  - Wire 4-player keyboard/gamepad input (`app/input.py`), scene transitions (Title → Knight Select → Overworld → Arena Combat → Victory),
    and `uv run pymoonstone` playable entry point with deterministic replay recording/verification (`--record` / `--replay`).
  - *Accept:* End-to-end headless session test running a scripted overworld + combat encounter with 100% deterministic state hash.

## 6. Multi-agent (`/teamwork`) ownership rules

To prevent merge conflicts when running a swarm of parallel agents:
1. **One task (`T<id>`) per worker** (or tightly coupled pair by explicit assignment), using `workspace: "share"` for authors editing code.
2. **Disjoint file ownership:** each task in Phases 5–8 lists its target module and test file. Workers must not modify files owned by another active `IN PROGRESS` task without coordinator approval.
3. **`docs/PROGRESS.md` & `src/pymoonstone/<layer>/__init__.py`:** workers update only their own row in `PROGRESS.md` (or report back to the coordinator to land status updates and `__init__.py` re-exports cleanly).
4. **Full gate before handoff:** every worker must run `uv run python scripts/quality_gate.py` in its workspace before reporting completion.
