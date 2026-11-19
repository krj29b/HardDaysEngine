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
  *Accept:* `uv sync` succeeds; `harddaysengine --version` prints the version.
- **T1.2 Agent governance:** `AGENTS.md`, `GEMINI.md`, `DEAD_ENDS.md`, `docs/{IMPLEMENTATION_PLAN,PROGRESS,DECISIONS}.md`.
- **T1.3 Quality gate:** `scripts/quality_gate.py` runs all seven gates and stops at the first
  failure. Radon must actually fail on rank ≥ C. *Accept:* negative-control probes show
  import-linter, radon and the layering test each failing on a planted violation.
- **T1.4 Config and ADF discovery:** `app/config.py` (CLI > env > `config.toml` > `./disks`),
  `harddaysengine --list-disks`, and an `adf_dir` pytest fixture that skips without disks.
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
- **T3.6** `harddaysengine-extract`: writes `./build/assets/` with PNG/WAV outputs and a deterministic
  JSON manifest (sorted keys, SHA-256 of every source and output, no timestamps).
- **T3.7** Headless tests: synthetic fixtures for CI; `adf` tests check real file counts,
  palette words and frame tables against values computed independently.

### Phase 4: Asset and hitbox inspector
- **T4.1** `render/`: canvas FBO, indexed-texture palette shader, sprite quads, CRT post-pass.
- **T4.2** `render/overlay.py`: GL line primitives, hurt areas in green, weapon points and boxes in red.
- **T4.3** `app/`: window host, 50 Hz accumulator, inspector key bindings
  (next/prev asset, next/prev frame, play/pause, overlay toggle).
- **T4.4** `harddaysengine --inspect-assets` and `harddaysengine-inspect`, with `gl`-marked smoke tests
  run on a standalone context.

### Phase 5: Deterministic 50 Hz simulation foundation & executable table loaders
*(Designed for parallel multi-agent execution — each task owns disjoint modules.)*
- **T5.1 Simulation PRNG & fixed-point types (`src/harddaysengine/sim/prng.py`, `src/harddaysengine/sim/types.py`, `tests/unit/test_prng.py`):**
  - Deterministic 68k PRNG (`rand_u16`, `rand_range`, seed state) and core integer value types
    (`Pos25D`, `Rect2D`, `Direction`, `KnightId`, `EntityId`, `TickCount`).
  - *Accept:* 100% integer-only (`test_layering.py` passes); unit tests verify deterministic sequence reproduction and state serialization.
- **T5.2 Runtime executable table decoders (`src/harddaysengine/assets/tables.py`, `tests/unit/test_tables.py`):**
  - Extract runtime data tables from `program` and `mog` hunks (as specified in `ARCHITECTURE.md` §5 and §7):
    combat action-to-animation script pointer tables, creature parameter records (`mog` `S_4`/`S_33`),
    Lair/region metadata, and 4-voice synth sequence streams (`mog` `S_44`).
  - *Accept:* Pure data records (`dataclass(slots=True, frozen=True)`) passed into `sim/` without `sim/` performing I/O; verified against synthetic hunks and `@pytest.mark.adf` real-disk tests.
- **T5.3 2.5D depth & hurtbox/hitbox collision resolver (`src/harddaysengine/sim/collision.py`, `tests/unit/test_collision.py`):**
  - Implement the 2.5D depth-band gate (`abs(attacker_y - defender_y) <= depth_threshold`, default `10` px per `ARCHITECTURE.md` §4.1),
    cel bitplane hurtbox AABB & mask testing (`draw_flags & 1`), `collide.hit` attack-point vs hurtbox intersection (`draw_flags & 2`),
    and `.t` arena obstacle/boundary clamping.
  - *Accept:* Deterministic unit tests covering front/back depth rejection, mirrored sprites, obstacle blocking, and multi-target hits.
- **T5.4 50 Hz PAL palette & VBL effect state machine (`src/harddaysengine/sim/palette_fx.py`, `tests/unit/test_palette_fx.py`):**
  - Pure integer simulation of the 32-colour 12-bit `$0RGB` palette state (`ARCHITECTURE.md` §8.2):
    step-toward-target fading, colour-range rotation/cycling (`cycle_step`), and lightning/blood flash overrides.
  - *Accept:* Unit tests verifying exact per-VBL `$0RGB` transitions and zero float usage.

### Phase 6: Combat bytecode VM, knight controls & monster AI
*(Parallelizable across `sim/combat_vm.py`, `sim/knight_combat.py`, and `sim/monster_ai.py` once `T5.1`–`T5.3` complete.)*
- **T6.1 Combat animation & bytecode VM (`src/harddaysengine/sim/combat_vm.py`, `tests/unit/test_combat_vm.py`):**
  - Implement the 50 B `CombatJob` state and 6-byte frame draw / variable-length control opcode interpreter
    (`$00..$1F` draw frame with `(dx, dy)` displacement and `draw_flags`; control opcodes `$80..$D4`, `$FF` per `ARCHITECTURE.md` §7.2–§7.3)
    stepping on the 6-VBL (normal) / 5-VBL (fast) combat frame divisor.
  - *Accept:* Deterministic execution of multi-frame sequences, loop/branch opcodes, velocity integration, and sound/spawn triggers.
- **T6.2 Knight combat controller & equipment state (`src/harddaysengine/sim/knight_combat.py`, `tests/unit/test_knight_combat.py`):**
  - Implement the 132 B `Knight` record (`ARCHITECTURE.md` §7.4) and 8-direction + fire-button action decoder
    (walk, run, roll, block, overhead slash, thrust, low sweep, weapon switch, hit-stun, knockdown, and death states).
  - *Accept:* Full state-transition coverage for sword & secondary weapon moves, stamina/hit-point deduction, and invulnerability frames.
- **T6.3 Monster AI state machines & Lair encounters (`src/harddaysengine/sim/monster_ai.py`, `tests/unit/test_monster_ai.py`):**
  - Implement monster perception, approach/retreat movement in 2.5D, attack selection, projectile/hazard spawning,
    and boss behaviours (Balok, Dragon, Guardians) driven by runtime tables from `T5.2`.
  - *Accept:* Deterministic N-tick battle simulations between a Knight and each monster archetype with reproducible outcomes from a fixed PRNG seed.

### Phase 7: Overworld board game & locations
*(Independent of Phase 6 combat internals — can run in parallel after `T5.1`.)*
- **T7.1 Overworld map, turn order & moon phase cycle (`src/harddaysengine/sim/overworld.py`, `tests/unit/test_overworld.py`):**
  - Implement the 1–4 knight board-game loop (`program` state machine): region graph and node movement,
    28-day moon phase progression, Lair key/Moonstone tracking, Dragon patrol movement on the map, and PvP encounter triggers.
  - *Accept:* Full multi-turn deterministic board simulation from campaign start to Stonehenge victory condition.
- **T7.2 Sacred places, towns & shops (`src/harddaysengine/sim/locations.py`, `tests/unit/test_locations.py`):**
  - Implement Highwood (merchant, healer, tavern dice game), Waterdeep (mystic, spell/scroll purchase, training),
    Stonehenge (Moonstone altar ritual), and Lair loot distribution.
  - *Accept:* Unit tests covering gold/inventory transactions, stat upgrades, dice game PRNG rules, and Moonstone placement.

### Phase 8: Audio synthesizer, multi-scene presentation & full game loop
- **T8.1 Paula 4-voice VBL synth & 8SVX mixer (`src/harddaysengine/app/audio.py`, `tests/unit/test_audio_mixer.py`):**
  - Implement the 50 Hz 4-voice waveform/envelope synthesizer (`mog` `S_44`/`S_45` per `ARCHITECTURE.md` §6.2),
    `.a` 8SVX sample playback triggered by combat VM opcodes, and `.cmp` ProTracker module playback via `pygame.mixer`.
  - *Accept:* Headless buffer synthesis tests verifying 4-channel PCM generation and sample mixing without requiring audio hardware in CI.
- **T8.2 Multi-sprite 2.5D scene renderer & HUD (`src/harddaysengine/render/scene.py`, `tests/gl/test_scene_gl.py`):**
  - Extend `render/` with Y-sorted 2.5D sprite batching (shadows, corpses, projectiles, knights, monsters, foreground arena obstacles),
    bitmap font text rendering (`.f` / `.font`), and combat/overworld HUD bars.
  - *Accept:* Standalone GL 3.3 tests verifying painter's-algorithm Y-sorting, palette slot swapping, and HUD rendering.
- **T8.3 Input router, scene coordinator & playable CLI (`src/harddaysengine/app/input.py`, `src/harddaysengine/app/game.py`, `tests/unit/test_game_session.py`):**
  - Wire 4-player keyboard/gamepad input (`app/input.py`), scene transitions (Title → Knight Select → Overworld → Arena Combat → Victory),
    and `uv run harddaysengine` playable entry point with deterministic replay recording/verification (`--record` / `--replay`).
  - *Accept:* End-to-end headless session test running a scripted overworld + combat encounter with 100% deterministic state hash.

### Phase 11: Original-model rebuild of fights, overworld and places
Source: `PLAYABILITY_AUDIT.md` §6 (Audit v3). Depends on ADR-009. Phases 9 and 10 are superseded
where they conflict. Reference reading for every task is `reference/moonstone-ace` (clone of
`Jarskih/moonstone-ace-public`; git-ignored): the header comments in `include/game/*.hpp` and
`include/game/rules/*.hpp` describe each original routine in prose and are the fastest way in.
`reference/openmoonstone` (`joetsoi/OpenMoonstone`) is for behaviour cross-checks only.

Rules for the whole phase:
- **Spec first.** Each task names the `ARCHITECTURE.md` section it implements. Write that section
  (own words, ADR-001) before the code.
- **Data from the disks.** Colours, prices, script addresses, creature definitions, button layouts
  and spawn lists are read from `mog` at runtime. A table that cannot be located yet blocks its
  task; it is not replaced by literals.
- **Evidence.** Presentation tasks attach a contact sheet from `scripts/audit_shots.py`; rule tasks
  have `adf` tests whose expected values are computed independently of the code under test.
- **No parallel models.** When a task replaces a module, the old module and its tests are deleted in
  the same commit.

#### 11A: Tooling and specification
- **T11.1 Audit harness (`scripts/audit_shots.py`, `tests/adf/test_scene_smoke.py`):** drive
  `GameSession` on the real disks through every scene, place and creature type; write PNG contact
  sheets under `build/audit/`. *Accept:* one command produces the sheets; the smoke test renders
  every scene without an exception; nothing under `build/` is tracked.
- **T11.2 Spec: game state records (`ARCHITECTURE.md` §9):** the knight record as used on the map
  and in fights, the 24-byte inventory and its slot meanings, lair records and their loot, map
  nodes, the active-fight block, the item and weapon ids. *Accept:* every field the later tasks
  read or write has an offset, width and meaning, with the conflicts between sources noted.
- **T11.3 Spec: fights (§10):** arena set-up per fight kind, creature definitions (cel slots, hit
  sets, sound bank, record initialisation, spawn lists, waves), the fight loop order and end timers,
  contact and damage rules, the engine routines scripts call, the fight palette assembly, the
  on-screen feedback. The nine AI handlers and the knight's hurt rules get a one-line row in
  §10.16 here; each is specified in full by the task that implements it (T11.9, T11.10a to i).
- **T11.4 Spec: overworld and turns (§11):** map frame order, movement and clipping, terrain mask,
  move budget, proximity scan and node menu, lair visibility, dragon flight, turn scheduler, clock
  and moon, computer-controlled knights.
- **T11.5 Spec: places and sheets (§12):** the visit prologue and classification by node id, both
  town button screens, the shared sheet loop with its button tables per sheet number, smith and
  market rules, temple, healer, dice house, mystic, Stonehenge offer and ritual, wizard, valley,
  home villages, the ending hand-over.

#### 11B: Fights (fixes the sprite errors)
- **T11.6 Fight palette from data (`assets/palette.py`, `app/`):** indices 0, 1..5 and 16..31 from
  the backdrop and region rules, 6..8 from the first fighter's class row, 9..15 from the creature
  scene row. Remove `default_combat_palette` from the fight path and the colour literals in
  `app/game.py`. *Accept:* in every arena, index 5 equals the backdrop picture's entry; no RGB
  literal for tunic or creature colours remains in `src/`; sheet reviewed for all four regions.
- **T11.7 Creature definitions (`assets/tables.py`):** decode each creature's cel slot list, hit
  sets, sound bank, script tables, type byte, hit points and tunables, the arena rows, spawn
  lists and the lair-to-arena table, as §10.15 specifies: data by offset, code operands at
  CRC-identified positions with the instruction word checked, no unchecked pattern scan. *Accept:* for every creature, every draw
  instruction reachable from its scripts names a slot that is loaded and a frame inside that cel
  (no modulo, no slot fallback); the `adf` test walks all scripts and asserts this.
- **T11.8 Fight engine (`sim/fight.py`, replaces the replay in `app/combat_bridge.py`):** ten jobs
  with persistent script state, the frame-set lists selected by `$C0`, second scripts, spawned
  objects, owner pokes, motion blocks, engine routines, depth sort, attack and hurt lists, contact
  pass, background stamps. `app/` renders the jobs' draw lists. Split (ADR-011): **T11.8a** the
  engine in `sim/` (`sim/actor.py`, `sim/fight_script.py`, `sim/fight_contact.py`, `sim/fight.py`,
  `assets/fight_program.py`). *Accept:* a recorded trace of a knight idle, walk and each attack
  matches the draw lists computed by a second, independent script walker in the test; a job's
  script position survives across ticks (no restart per frame). **T11.8b** (after T11.9 and
  T11.10a) `app/` draws the jobs' draw lists and `sim/combat_vm.py` is deleted with its tests.
  *Accept:* reviewed contact sheet of a Trogg fight from the real disks; no import of
  `combat_vm` remains.
  T11.8b is split into **T11.8b1**, the ordered draw-list presenter and independent real-disk
  audit path (§10.21), and **T11.8b2**, campaign host integration and old-caller/VM removal.
  The split keeps presentation ordering and stamp correctness independently reviewable while
  the host's campaign/location record boundary is replaced. T11.8b is complete only when both
  subtasks are done; ADR-011's temporary coexistence remains until T11.8b2.
- **T11.9 Knight control (`sim/fighters.py`, replaces `sim/knight_combat.py`):** joystick to action
  through the two action tables, walk scripts by phase and direction block, hurt scripts by the
  attacker's action, damage and defence tables, daggers, blocking, arena obstacles. Stamina and
  score are removed. *Accept:* each of the nine actions plays its own script to its end; a hit
  reduces hit points by the table value for that action and equipment.
- **T11.10 Creature AI handlers (`sim/fight_ai/`, replaces `sim/monster_ai.py`):** one handler per
  type, called only on a hit link or when the script ended, returning the next script. Split as
  `T11.10a` Troggs, `b` Ratmen, `c` Mudmen, `d` flyer, `e` Troll, `f` Balok, `g` Guardian, `h`
  Dragon, `i` computer knight. *Accept per handler:* a seeded fight on the real disks reaches a
  decided outcome; the creature dies through its hurt script's death branch; sheet shows the whole
  creature in idle, walk, attack, hurt and death.
- **T11.11 Fight loop and outcomes (`sim/fight.py`, `app/game.py`):** frame pacing, end timers,
  waves, defeat settlement and lives, the practice fight (knight against knight), the low-health
  palette pulse (the original has no bars or score).
  *Accept:* win and loss both return to the caller with the right record changes; practice returns
  to the title; the hit flash leaves the HUD colours alone.

#### 11C: Overworld
- **T11.12 Map movement and proximity (`sim/overworld.py` rewritten, `app/`):** pixel position,
  edge clipping, terrain slow-down, move budget, map sprites, proximity scan with highlight, node
  menu, lair records and visibility, and the original random number generator (§11.2) in place
  of the LCG in `sim/prng.py`. The node graph and `_CANONICAL_ROAD_SITES` are deleted.
  *Accept:* holding a direction moves the knight one pixel per unblocked frame; stepping away from
  the start does not start a fight; standing on a lair or place lists it in the node menu.
- **T11.13 Turns, clock and dragon (`sim/turns.py`):** turn hand-over, day and moon advance, the
  "Next Day" screen from `ch.piv`, dragon flight and attack on the map, game over when no human
  knight is left. *Accept:* a round visits each of the four knights once and ends with "Next Day"; the day
  counter and the moon step every fourth round (§11.12); the moon frame
  follows the phase table.
- **T11.14 Computer-controlled knights on the map (`sim/ai_map.py`):** roam target, walk line,
  shopping wish, opponent choice, item use. *Accept:* seeded multi-day run is deterministic and the
  PRNG draw order matches the specified frame order.

#### 11D: Places and sheets (the missing screens)
- **T11.15 Sheet engine (`sim/sheets.py`, `app/sheet_screen.py`):** the shared screen loop, panels,
  item icons from `ki.cel`, pointer from `po.cel`, the use and take button tables, the click
  dispatch, inventory transfer, item use. *Accept:* sheets 2 (lair loot) and 9 (status / temple)
  render and operate from the real tables.
  Split into **T11.15a**, the checked runtime button-table and panel-list decoder,
  and **T11.15b**, the shared sheet simulation, presenter, campaign callers and
  real-disk contact-sheet acceptance. The first subtask independently validates
  the executable-built sparse data before the shared screen loop consumes it.
- **T11.16 Towns (`app/town_screen.py`, replaces `app/location_screens.py`):** Highwood and
  Waterdeep button screens with all five buttons each, routed to smith, market, dice house, healer,
  temple and mystic as the button tables say. The invented goods, services and training menu are
  deleted with `sim/locations.py`. *Accept:* every button on both scrolls is selectable and opens
  its screen; Exit returns to the map.
  Split into **T11.16a**, the checked runtime town layout/identity decoder, and
  **T11.16b**, the presenter, host navigation, obsolete-model removal and
  real-disk contact-sheet acceptance. Subtask a must independently verify all
  five button records, pointer starts, original picture/name strings and the
  Space-key sheet selection. Parent acceptance still requires subtask b.
  Subtask b is further split into **T11.16b1** (original presenter, host caller
  cutover, shared-sheet returns, spent exit and obsolete-model removal) and
  **T11.16b2** (original loading-text prologue and complete child-screen acceptance
  after T11.17/18). T11.18 depends on b1, avoiding a circular dependency on its
  own caller acceptance. Service requests report unavailable until T11.18;
  T11.17 supplies the original transactions on shared sheets 5 and 6.
  Final parent acceptance still requires every button to open its real screen.
- **T11.17 Smith and market (sheets 5 and 6):** fixed-price smith, market stock and price list,
  buy and sell-back, gold cap, including the documented quirks. *Accept:* prices and caps come from
  `mog`; buying and selling change gold and inventory exactly as specified.
- **T11.18 Dice house, healer, mystic:** `tav.piv` / `dice.piv` / `dice.cel` with the payout table;
  `hea.piv` with donation and rest; `mys.piv` with `mys.cel`. *Accept:* each screen shows its
  animation cels and applies its rule; sheets reviewed.
  Split into **T11.18a**, runtime healer prices/cap, mystic donation bands and dice payout
  decoding plus pure donation/service transactions (synthetic edge cases and independent
  real-disk table/RNG checks), and **T11.18b**, original animated screen loops, text/scripts,
  input/waits, host snapshots and town returns with reviewed contact sheets. The three
  screen loops plus executable decoding and transaction rules exceed one session. Parent
  acceptance remains unchanged and requires both subtasks.
  T11.18b is split again: **T11.18b1**, the shared donation screen with the healer and the
  mystic (decoded openings, regions, redraw list, reply lists and tables, tick phases, host
  child screen and contact sheet), and **T11.18b2**, the dice house, which also needs the
  thrower's fight-engine job (type `$44`) with its handler and scripts, the dice table picture
  and the class colour ramp. Both are done (T11.18b1 / ADR-036, T11.18b2 / ADR-037); the dice
  thrower runs in its own fight engine instance on the hunk-42 scripts.
- **T11.19 Stonehenge:** the offer sheet, the ritual screen (`hen1.p`, `hen1.c`, `he.a`, timed
  fades), the win condition (the moonstone of the current phase) and the hand-over to the ending.
- **T11.20 Wizard's tower:** `WI2.P`, `WI1.P`, `Wi1.C`, `wz.a`, then the temple sheet; the visit
  uses up the turn. `WI1.P` stops being the victory picture.
  Split into **T11.20a**, exact gift result records and checked runtime dialogue (including
  the selected stat-table row and shared item/gold message cycles), and **T11.20b**, the
  three animated stages, assets/scripts, PAL waits, host/hash integration, spent temple
  return and reviewed real-disk contact sheet. The complete screen and its decoding exceed
  one session. Parent acceptance is unchanged and requires all three parts.
- **T11.21 Valley, home villages, meetings:** the guardian fight and its reward, the life granted
  at a home village, knight-meets-knight duel with the scroll dialog, meeting and exchange sheets,
  the dragon hoard sheet.
  Split into **T11.21a**, home villages, **T11.21b**, the Valley's complete guardian/reward caller,
  and **T11.21c**, knight meetings, the protection-scroll dialog, exchanges and dragon hoard
  sheets. The independent blocking callers, ownership changes and real-disk acceptance exceed
  one session. Parent acceptance is unchanged and requires both parts.
- **T11.22 Ending and game over:** the `program` ending sequence with `vmusic.cmp`; the farewell
  text screen on `message.piv` with its own palette.

#### 11E: Close-out
- **T11.23 Sound wiring:** script sound opcodes to the synth, per-creature banks loaded with the
  arena, music per scene.
- **T11.24 Campaign verification:** a recorded real-disk replay from knight select through a lair
  win, its loot sheet, a town purchase and a day change, with a deterministic state hash; README
  controls updated; `PROGRESS.md` statuses for Phases 6 to 10 corrected to what survived.
- **T11.25 Checked `program` intro pass & `mog` title/knight-select screens (`assets/intro_data.py`,
  `sim/intro_types.py`, `sim/intro.py`, `app/intro_player.py`):** decode the `program` first pass
  (loader progress steps, credit pages, forward wipe, picture setup, `SERIES` and the seven intro
  scene bodies) and the `mog` title-menu and knight-select screen layouts from the user's
  executables at runtime (ADR-044 follow-up), run the intro on the `sim/` VBL generator and 40-slot
  job pool, and remove all hardcoded scene tables, palette words and hunk offsets from
  `app/intro_player.py`. *Accept:* synthetic and real-disk tests verify all decoded operands and
  VBL playback; portrait names match the `mog` `$1EFE..$1FBC` order; contact sheets `intro.png` and
  `menu_map.png` reviewed.

Milestones: **M1** one creature fight that looks and plays right (T11.1, T11.2, T11.3, T11.6 to
T11.11, Troggs first). **M2** walking the map into that fight and out again (T11.4, T11.5, T11.12,
T11.13, T11.15 for the loot sheet). **M3** all places (T11.16 to T11.21). **M4** a campaign that
can be finished (the remaining T11.10 handlers, T11.14, T11.22 to T11.24).

## 6. Multi-agent (`/teamwork`) ownership rules

To prevent merge conflicts when running a swarm of parallel agents:
1. **One task (`T<id>`) per worker** (or tightly coupled pair by explicit assignment), using `workspace: "share"` for authors editing code.
2. **Disjoint file ownership:** each task in Phases 5–8 lists its target module and test file. Workers must not modify files owned by another active `IN PROGRESS` task without coordinator approval.
3. **`docs/PROGRESS.md` & `src/harddaysengine/<layer>/__init__.py`:** workers update only their own row in `PROGRESS.md` (or report back to the coordinator to land status updates and `__init__.py` re-exports cleanly).
4. **Full gate before handoff:** every worker must run `uv run python scripts/quality_gate.py` in its workspace before reporting completion.
