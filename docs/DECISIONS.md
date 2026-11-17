# Architecture Decision Records

## ADR-001: Clean room; the references are specifications
**Status:** ACCEPTED (2026-11-14)
**Context:** `reference/moonstone-ace` is PolyForm Noncommercial 1.0.0 and `reference/openmoonstone`
is AGPL-3.0. Their terms can't be combined with each other or with pymoonstone's MIT licence.
**Decision:** Formats and behaviour are described in our own words in `docs/ARCHITECTURE.md`, and
code is written from that document. No code is transliterated from either reference. No byte tables
from the original executables are placed in source; such tables are read at runtime from the
user's own `program`/`mog` files.
**Consequences:** Phase 2 must produce a spec complete enough to implement from. Behavioural
parity is checked by tests against the real disks (`adf` marker), not by reference diffing.

## ADR-002: moonstone-ace for Amiga formats; OpenMoonstone for behaviour only
**Status:** ACCEPTED (2026-11-14)
**Context:** OpenMoonstone reads the DOS release. moonstone-ace was lifted from the Amiga 68k
code and verified against it.
**Decision:** Amiga byte layouts come from moonstone-ace. OpenMoonstone informs combat, AI and
movement semantics. Where they disagree, moonstone-ace wins, and the conflict is noted in the spec.

## ADR-003: Layering `cli → app → render → assets → sim`
**Status:** ACCEPTED (2026-11-14)
**Decision:** As in `.importlinter`. pyhexen's separate `core/` package is folded into `sim/`
(e.g. `sim/m68k.py`), because `sim` is the bottom layer and must stay importable alone.
`render` gets a `moderngl.Context` from `app` and never touches pygame. Audio mixing sits in `app`.

## ADR-004: Quality gate = pyhexen's ruff + pytest, plus mypy, import-linter, radon, vulture
**Status:** ACCEPTED (2026-11-14)
**Context:** pyhexen gates only on ruff and pytest, and checks layering with an AST test.
**Decision:** Keep pyhexen's AST layering test, extended with a float ban for `sim`. Add the
user-specified tools in `scripts/quality_gate.py`. Because `radon cc` never fails on its own,
the gate parses its JSON output and fails on rank ≥ C.

## ADR-005: Fixed 50.00 Hz tick = one PAL VBL
**Status:** ACCEPTED (2026-11-14)
**Context:** Real PAL VBL is 49.92 Hz. The fight logic advances every 5–6 VBLs.
**Decision:** The simulation ticks at exactly 50 Hz. Slower scene logic uses integer tick
divisors in `sim`.

## ADR-006: 320×256 PAL canvas, 320×200 game area
**Status:** ACCEPTED (2026-11-15)
**Context:** All Moonstone pictures are 320×200. The brief asks for a 320×256 canvas.
**Decision:** Keep the 320×256 virtual canvas (`CANVAS_WIDTH = 320`, `CANVAS_HEIGHT = 256`) and place the 320×200 game viewport at `GAME_OFFSET_Y = 28`, drawn 1:1. The top and bottom 28-pixel borders remain available for HUD/debug read-outs.

## ADR-007: Python 3.12 pinned for development
**Status:** ACCEPTED (2026-11-14)
**Context:** The host default is Python 3.14. `requires-python` is `>=3.12`.
**Decision:** `.python-version` = 3.12, so uv provisions the interpreter that mypy targets
(`python_version = "3.12"`) and the version CI uses.

## ADR-008: Disjoint per-module task ownership for multi-agent (`/teamwork`) execution
**Status:** ACCEPTED (2026-11-15)
**Context:** Parallel worker agents operating in `workspace: "share"` worktrees can cause merge conflicts if multiple tasks edit the same source or test file simultaneously.
**Decision:** Every task in Phases 5–8 (`T5.1`–`T8.3`) specifies explicit, disjoint source and test file paths in `IMPLEMENTATION_PLAN.md` and `PROGRESS.md`. Workers claim a single `T<id>` and modify only its owned files; shared index files (`__init__.py` and `PROGRESS.md` status merges) are coordinated when landing branches.

## ADR-009: One model per subsystem, taken from the original; the invented models are removed
**Status:** ACCEPTED (2026-11-15, decided by the user: replace the six invented modules)
**Context:** Audit v3 (`PLAYABILITY_AUDIT.md` §6) found that the overworld, the fights and the
places are original designs of this project (node graph, timer state machines, text-menu shops)
with the real assets attached afterwards. Two remediation passes patched symptoms and left the
models in place. The tests assert those models, so the gate stays green while the game is not
playable.
**Decision:**
1. `sim/` holds the original's records and rules: the knight record and its inventory, lairs, map
   nodes, the ten fight jobs, the turn scheduler. The fight script interpreter is the only source
   of fighter animation *and* state; `app/` reads the jobs' draw lists and never runs scripts itself.
2. `sim/overworld.py`, `sim/locations.py`, `sim/knight_combat.py`, `sim/monster_ai.py`,
   `app/combat_bridge.py` and `app/location_screens.py` are replaced, not patched. Their tests go
   with them.
3. Every Phase 11 implementation task starts from a new `ARCHITECTURE.md` section; a task whose
   section is missing is blocked.
4. A presentation task is `DONE` only with a reviewed contact sheet from the real disks
   (`scripts/audit_shots.py`), in addition to the gate.
**Consequences:** Roughly 5,600 lines of `sim/` and `app/` and their tests are rewritten. The
decoders, renderer, audio, intro and title code stay. Supersedes the parts of ADR-008 that tie
tasks to the Phase 5 to 8 module list.
**Alternative considered:** keep the graph overworld and text shops and repair only the fight
rendering. Cheaper, but the result is a different game with Moonstone's art, and the fight
defects come from the same split between an invented state machine and replayed scripts.

## ADR-010: Fight definitions are read by executing bounded store runs of `mog` code
**Status:** ACCEPTED (2026-11-16)
**Context:** The creature, arena and knight values (§10.15) are immediates inside the routines of
`mog` hunk 0 that fill the script tables and initialise records. One checked operand read per
value would need several hundred hard-coded positions.
**Decision:** `assets/mog_code.py` gives two readers: single operands at known positions with the
instruction word checked, and `StoreRun`, a minimal interpreter for the dozen store instructions
the table-filling routines use. `assets/fight_defs.py` holds only routine entry and exit offsets
for the supported CRC, runs each routine and reads the stored tables and record fields. An
unknown instruction or terminator raises `MogVersionError`. The module is `fight_defs.py`, not
`tables.py` as the plan suggested, because `tables.py` is already the generic `mog` / `program`
table loader. `MogCode` accepts a `supported` CRC tuple so unit tests can feed it synthetic code.
**Consequences:** A new `mog` version needs only new entry offsets. Table extents come from the
`LEA` targets the runs see (§10.15). The decoder imports nothing from `sim/` or `app/`.
**Alternative considered:** a pattern scan for `MOVE.L #imm,d16(A1)` runs. Rejected by §10.15 (no
unchecked scans), and it cannot tell one routine's writes from another's.

## ADR-011: The fight engine is built in `sim/` first; `app/` moves to it in a second step
**Status:** ACCEPTED (2026-11-16)
**Context:** T11.8 asks for the engine and for `app/` to render its draw lists. The old
interpreter `sim/combat_vm.py` is still imported by `sim/knight_combat.py` and `sim/monster_ai.py`,
which T11.9 and T11.10 replace, and a fight in `app/` cannot run on the new engine before a knight
handler and one creature handler exist.
**Decision:**
1. T11.8 is split. T11.8a builds the engine (§10.17, §10.18) in `sim/fight_script.py`,
   `sim/fight_contact.py`, `sim/fight.py` and the 132-byte record in `sim/actor.py`, with the
   asset builder `assets/fight_program.py`. T11.8b, after T11.9 and T11.10a, moves `app/` onto it
   and deletes `sim/combat_vm.py`. Until then the two interpreters coexist; nothing new imports
   the old one. This is the one sanctioned exception to "no parallel models" (ADR-009), and it ends
   with T11.8b.
2. Actors are referred to by handles (1..4 knights, 5 dragon, 6..25 pool, 26 the spare record the
   link clear touches; 0 none), script fields by positions in the script hunk, frame lists by their
   number 1..4. Record fields hold raw unsigned values so byte-addressed pokes work unchanged.
3. Handlers are registered per type byte in `Fight.handlers`, engine routines per name in
   `Fight.routines`. The `$B0` operands are named by the asset builder (hunk, offset, entry word
   checked). A routine that has no implementation yet is reported in
   `FrameOutput.skipped_calls`; a type without a handler raises.
4. Where the original hangs or corrupts memory (pool full, list overflow, a script without a
   marker, a next-creature routine that spawns nothing, the contact test's plane-count overflow),
   the engine raises or uses the obvious reading, and §10.17 / §10.18 say so.
**Consequences:** The engine is tested on its own (synthetic scripts, and the real knight scripts
against an independent walker). The fight on screen does not change until T11.8b.
**Alternative considered:** rewire `app/` in T11.8 with temporary handlers that only replay idle
scripts. Rejected: a placeholder handler is exactly the rigour theatre AGENTS.md §3 forbids, and
the screen would show standing fighters only.

## ADR-012: Knight rules are read from `mog` code; record tables live in the fight by key
**Status:** ACCEPTED (2026-11-16)
**Context:** The knight's reactions (§10.19) are 68k code: flat amounts, fixed scripts, sword and
moonstone pairs, step and action tables, and two jump tables indexed by type byte. Record fields
such as `damage_table` are addresses of BSS tables that both knights share and that arena pokes
change for one fight.
**Decision:**
1. `assets/fighter_rules.py` reads every number, script, table and the hop table at positions of
   the supported version, each after its instruction word is compared, and fills `KnightRules`
   (`sim/fighter_rules.py`). The two rule tables are read by running their fill routine (§10.15
   store run); each entry names a rule by the entry offset of its code, whose first instruction
   word is compared. Action codes (§9.7) and type bytes (§9.8) stay named constants in `sim/`.
2. `Fight.tables` holds tables of longs by key; record fields hold the key. `register_tables`
   stores one `ScriptTables` and `TableKeys.assign` points any number of records at it, so both
   knights share one set as in the original. A read outside a table or at an offset that is not a
   multiple of 4 raises (the original takes an address error or reads other memory).
3. Handlers and routines get their rules through `functools.partial`; `install_knight_control`
   registers them.
**Consequences:** A new `mog` version needs only new positions. Creature handlers (T11.10) use the
same table store and rule record pattern.
**Alternative considered:** constants for the amounts (5, 3, 10, ...) in `sim/`. Rejected:
AGENTS.md §3 requires original tables and tunables to be read at runtime.

## ADR-013: Creature handlers live in `sim/fight_ai/`; the fight owns the original generator
**Status:** ACCEPTED (2026-11-16, T11.10a)
**Context:** The brawler is the first creature handler (§10.20). It draws from the original
random generator (§11.2), shares its approach and step helpers with the dragon (T11.10h), and
needs a fight-level latch that a knight script also sets.
**Decision:**
1. One module per handler under `sim/fight_ai/` (`brawler.py`), plus `approach.py` for the
   shared helpers. Each handler takes a frozen rules record (`BrawlerRules`) that
   `assets/creature_rules.py` reads from `mog` (operands with instruction words checked,
   relocated scripts, step tables via their `LEA`s, the handler entry via the handler table's
   fill routine run as a store run, the helpers by call site and first instruction word).
   `install_<handler>` registers it per type byte.
2. `sim/rng.py` implements the generator of §11.2 now. `Fight.rng` holds it; the map will hand
   its own instance to the fight (T11.11 / T11.12). `sim/prng.py` stays until T11.12 removes it.
3. The helpers' scratch words (`ApproachCells`) and the gloat latch are `Fight` cells, as in the
   original, not handler state.
4. `MogCode` gains public `hunk_word`, `operand` and `relocated`; `assets/fighter_rules.py` uses
   them instead of private copies.
**Consequences:** The remaining handlers follow the same pattern. A routine that a fixed
script calls but the T11.7 walker never reached is added to the routine table by the task that
meets it (here: "set the gloat latch", `$71C8`).
**Alternative considered:** draw from the existing `SimPrng` for now. Rejected: it is invented
(§11.2), so the brawler's choices would differ from the original's.

## ADR-014: Consume fight draws once and retain the composed frame
**Status:** ACCEPTED (2026-11-16, T11.8b1; implements ADR-009 / ADR-011)
**Context:** The fight engine emits projected screen coordinates, per-draw mirror snapshots,
second-script draws and background stamps. The general scene compositor sorts sprites by
screen Y, which would change that order. Reapplying stamps during rendering would also make
presentation depend on the number of host render calls.
**Decision:** `render/fight.py` consumes the ordered draw list into a retained indexed frame
and a separately stamped background. `app/fight_scene.py` owns the bridge: `advance(vbl)`
runs one persistent `Fight.step` and accepts its draws once; `render` displays its retained
frame with a palette and never steps simulation. Cel and frame lookup is exact. NumPy slicing
performs transparency, clipping and mirroring without per-pixel Python loops. The existing
scene compositor uploads the completed picture without sprites to sort.
**Consequences:** Synthetic tests can verify the pixel contract without GL or disks. The
real-disk `engine_fights` audit scenario runs single-Trogg fixtures through this bridge and
the GL renderer. T11.8b is split into T11.8b1 (presentation) and T11.8b2 (campaign host and
old-caller removal); ADR-011's temporary coexistence ends only with T11.8b2. T11.11 still owns
campaign waves, pacing and settlement. No game data or generated contact sheets are committed.

## ADR-015: Host cutover and the temporary legacy sheet boundary
**Status:** ACCEPTED (2026-11-17, T11.8b2; user explicitly approved the host cutover and
obsolete-file removals after automatic approval review requested confirmation)
**Context:** ADR-011 requires the host to use persistent Fight jobs and remove the old VM.
Legacy locations still require a knight-shaped data record until their scheduled sheet
migration. Only the brawler creature handlers are implemented in the replacement engine.
**Decision:** The session owns persistent Actor records, inventories and the original
fight generator. `FightLoader` reads executable definitions and creates a FightScene around
those records. `FightRuntime` samples normalized joystick words, advances frames every six
VBLs, preserves the decoded base waves and counts down the decoded post-fight delay. Missing
handlers are reported explicitly before activation. Audio accepts integer engine sound IDs.
The legacy sheet data is isolated in `sim/location_state.py` with no combat behavior; the host
translates values only on entry/return, not every tick. Old controller/VM/bridge modules and
their model-specific tests are removed, with persistent-engine host tests replacing them.
**Consequences:** The original fight generator persists across encounters; T11.12 joins the
map to that same instance. T11.11 still owns full wave scaling, spawn lanes, enter scripts,
low-health feedback and settlement/loot fidelity. Unsupported creature fights remain visibly
unavailable until their handlers land. The eight obsolete controller/VM/bridge source and
test files are removed. The host and independent runtime are both exercised on real disks,
including win/loss, practice returning to title for either loser, and input replay. Both
duel knights use the same rules and scripts, with separate frame lists and joystick ports.
Replay recordings keep the initial roster so a menu-driven roster change replays correctly;
the headless CLI uses its resolved disk directory for the same runtime assets as the window.

## ADR-016: Ratmen reuse fight-owned caster cells and trajectory slots
**Status:** ACCEPTED (2026-11-17, T11.10b; follows ADR-013)
**Context:** Ratmen share grab/slam/initial-throw flags, an attack wait and six keyed flight
slots. Their aerial movement uses these slots rather than knight dagger objects. The arena
also owns an idle object used as the first flight's destination.
**Decision:** `sim/fight_ai/caster.py` follows the existing handler/rules registration pattern.
`assets/caster_rules.py` reads tunables and relocated scripts from checked executable operands,
validating handler-table identity and helper calls. `sim/trajectory.py` implements the shared
word-wrapping fixed-point helper; mutable request, slots and caster cells belong to `Fight`
and are included in runtime snapshots. `FightLoader` creates the decoded Ratmen anchor before
spawning creatures and applies decoded HP/damage variants for a caller-supplied moon frame.
**Consequences:** Ratmen become available in the host. Calendar-to-original-moon-frame wiring
remains T11.12/13, and complete campaign waves/entry/settlement remain T11.11. Handler acceptance
uses a seeded single-opponent fixture with the original anchor; win, loss and full mutable-state
replay are checked independently of unfinished campaign wave logic. Script-pose previews are
labelled separately from natural fight frames in the `ratmen` contact sheet. The two newly
encountered sound-picker entry points join the existing deferred sound calls (T11.23), without
pretending those calls already implement their random draws or audio. Flight pool exhaustion,
division by zero and quotient overflow raise; meaningless low-memory coordinate writes are
omitted, as documented in §10.23.

## ADR-017: Keep fight entry and outcome cells at the existing host boundary
**Status:** ACCEPTED (2026-11-17, T11.11; follows ADR-013 / ADR-015)
**Context:** The persistent engine needs original spawn lanes, wave scaling, entry scripts,
low-health warnings and caller settlement while the map and sheet replacements are pending.
**Decision:** Checked executable operands are decoded by `assets/fight_setup.py` into frozen
`FightSetupRules`. Integer lane, scaling, warning and defeat rules live in `sim/fight_setup.py`.
`FightRuntime` owns the encounter clock and warning ramps; `GameSession` retains the lane cursor,
lair remaining counts and defeat mask in replay state. Warning ramps use palette state outside
the script-job pool and advance on VBLs, independently of rendering. Completion restores defeated
knights and decrements lives before releasing jobs, updates lair counts even on loss, awards
progress on a lair win and clears bad luck. No substitute loot or inventory reward is added.
**Consequences:** Original caller sheets remain T11.15 / T11.21; original map and calendar state
remain T11.12 / T11.13. Missing creature handlers still report unavailable. This completes the
fight-host work deferred by ADR-015 without changing the layer boundaries or introducing another
combat model. Specification §10.24 records word wrapping, warning ownership and timer ordering.

## ADR-018: Mudmen share encounter flags and retain classic grab quirks
**Status:** ACCEPTED (2026-11-17, T11.10c; follows ADR-013)
**Context:** The Mudmen handler lives in executable hunk 40 and coordinates all Mudmen
through one byte, while per-creature flags and timers stay in the actor record.
**Decision:** `assets/snatcher_rules.py` reads its checked operands, relocated scripts
and step table into frozen rule records. `sim/fight_ai/snatcher.py` uses the existing
approach, movement, contact-damage and job helpers. `Fight.snatch_flags` is initialized
per encounter and included in runtime snapshots. The host registers type `$04`.
The classic first-fighter target, second-port escape input, timer underflow, local
carry-bit retention and ineffective reversed-register restart are preserved (§10.25).
The reference port's later cooperative-player changes are excluded.
**Consequences:** Seeded real-disk win/loss and full-state replay exercise natural
handler/script execution. Separately labelled pose previews cover idle, walk, attack,
hurt and death on the Mudmen audit sheet. Newly reached hunk-40 sound pickers are
identified and remain deferred, including their PRNG draws, with sound wiring T11.23.

## ADR-019: Flyer alternation persists; raster variation uses deterministic ticks
**Status:** ACCEPTED (2026-11-17, T11.10d; follows ADR-005 / ADR-013)
**Context:** The Flyer uses an alternation word stored beside its step table, and
reads low bits of the Amiga raster register to vary every other return lane. CPU
instruction timing and the raster hardware are outside the fixed 50 Hz simulation.
**Decision:** `assets/flyer_rules.py` decodes checked operands, relocated tables and
finishing scripts. `sim/fight_ai/flyer.py` uses the original PRNG once per edge turn,
the existing contact damage and fight outcomes, and preserves the single idle-script
pause quirk. `FightLoader` retains the decoded `FlyerCells` across fights; runtime
snapshots and the host hash include it even between encounters. Before handlers,
`Fight.step(vbl)` sets `beam_word = u16(vbl)` as the explicit deterministic raster
surrogate (§10.26). This does not consume a random draw or consult host time.
**Consequences:** Lane variation is reproducible but does not emulate cycle-exact
raster timing. Synthetic tests check draw count, shared state, boundaries, phase
order, hurt/finish priority and demo exclusions; real-disk tests check natural
win/death, idle loss, state hashes and persistence across encounters. The Flyer audit
separates script-pose previews from natural handler traces and includes late death
frames. Sound pickers remain deferred to T11.23 as for the other handlers.

## ADR-020: Troll rules reuse jobs, contact damage and VBL effects
**Status:** ACCEPTED (2026-11-17, T11.10e; follows ADR-013 / ADR-017)
**Context:** The Troll uses fixed hurt/attack scripts, movement from the old phase,
an ordinary hit-spark object, and a heavy-impact DIW shake routine not
previously reached by the engine. Its empty first-wave list means one alternating
spawn while retaining a cap of two for subsequent refills.
**Decision:** `assets/stalker_rules.py` validates hunk-40 instructions and reads the
steps, scripts, hop table and spark definition. `sim/fight_ai/stalker.py` uses the
existing approach, move-apply, damage and hop state. `fight_ai/effects.py` spawns
the shared hit spark through the normal record/job pools. `CreatureWaves.start`
distinguishes list setup from one alternating entry without changing the cap.
`assets/screen_fx.py` reads the period and sentinel table for `sim/screen_fx.py`;
the screen-shake engine call initializes that state, and PAL updates advance it
independently of rendering. The presenter offsets its retained image within the
existing 320×256 canvas. Collision, stamps and palette data remain in game coordinates.
**Consequences:** Snapshots include the shake and its clock. Runtime calls consume
one effect tick and audits consume elapsed ticks; repeated timestamps do not
double-step, and paused time does not produce catchup. Repeated starts restart a
single effect rather than creating the original's duplicate interrupt hooks (§10.27).
The single-opponent outcome fixture explicitly places both fighters inside the
arena; original spawn placement and cap behavior have separate real-disk checks.
The newly reached two-draw sound picker is identified but deferred to T11.23.

## ADR-021: Balok reuses trajectory, aim, hop and impact state
**Status:** ACCEPTED (2026-11-17, T11.10f; follows ADR-013 / ADR-016 / ADR-020)
**Context:** Balok's fixed-script handler pounces through the same trajectory slots
as Ratmen, reads the same aim words, and calls the existing hop, spark and shake
helpers. Its recovery moves the victim's job rather than the actor directly.
**Decision:** `assets/drake_rules.py` checks the handler identity, helpers and
operand instructions and decodes the fixed scripts/tunables. It accepts the already
checked flight and spark definitions rather than duplicating their decoders.
`sim/fight_ai/drake.py` uses the existing flight request/slots and aim words;
`aim_flight` accepts an explicit reach override without changing actor equipment
or reach. The loader retains `DrakeCells` across encounters and both runtime and
inactive-host hashes include them. The one-entry Balok wave does not flip the
two-entry arena toggle. `counter` preserves signed byte-subtraction conditions,
one discarded PRNG draw and the fixed timer reload. The new grab sound-pair
engine call emits its two decoded IDs in order.
**Consequences:** Synthetic tests cover priorities, flight cap versus uncapped aim,
landing, sounds, recovery job placement, threshold decisions and timer overflow.
Real-disk tests cover a natural seeded win through the hurt-script death branch,
idle loss through grab/carry/finishing scripts, complete-state replay, fixed spawn
entry and cross-encounter cells. The audit controller dodges during a flight then
closes for melee, using ordinary inputs. Pose previews and natural traces are
labelled separately. Existing sound pickers and sample-bank playback remain T11.23.

## ADR-022: Guardian owns a reserved body record and encounter stage cells
**Status:** ACCEPTED (2026-11-17, T11.10g; follows ADR-011 / ADR-013 / ADR-017)
**Context:** The Guardian uses two actor records, separate script jobs, staged grab flags
and fixed scripts in executable hunk 40. Its setup is not a creature wave.
**Decision:** Checked operands and relocated pointers in assets/guardian_rules.py supply
sim/fight_ai/guardian.py. The existing loader starts the main actor at its decoded position,
reserves the following record, and bypasses ordinary wave scaling/spawning. Body creation,
facing, removal, victim damage and release use the existing job/record pools. GuardianCells
is encounter-owned, including the previous-target handle and death-fade request; runtime
snapshots include it. Fixed sound IDs are emitted in order. Random sound picker $0AAE and
its PRNG draw remain deferred with other sound pickers to T11.23.
**Consequences:** High-byte cooldown stores, the body's initial x-as-depth typo, staged
priority, previous-target hurt read and job-only victim positioning are preserved (§10.29).
The wrong-register release store into script +106 is omitted as an invalid-memory side effect,
without substituting a cooldown update. Missing jobs cause no zero-address writes. Death
fades target entries 2..7 through the existing PAL host update. The Guardian's hurt script
ends the fight directly rather than decrementing a normal creature wave. Real-disk tests
verify natural win/death, idle loss, exact loaded frames for every fixed script, original
setup operands and full-state replay; pose previews are labelled apart from natural traces.
Valley visit/reward integration remains T11.21.

## ADR-023: Dragon phases reuse persistent actor 5 and the shared fight jobs
**Status:** ACCEPTED (2026-11-17, T11.10h; follows ADR-011 / ADR-013 / ADR-016)
**Context:** The Dragon fight has a persistent actor, two linked objects, two flight phases,
script-triggered ground steps and fire jobs. Its executable also contains misplaced job writes.
**Decision:** assets/dragon_rules.py validates both handler entries and helper destinations,
then decodes phase requests, operands, fixed scripts, sounds and frame-list pointers.
sim/fight_ai/dragon.py uses existing approach, trajectories, hit sparks, contact damage,
hop and job machinery. The loader applies decoded fixed records to actor5 and two ordered
pool records, without ordinary wave scaling. DragonCells belongs to each fight and its
snapshots. The campaign host routes Dragon intercepts into this runtime.
**Consequences:** Signed phase comparisons, object action $14, x bounds, tracker depth,
ignored trajectory depth and classic delayed bite death are preserved (§10.30). Misplaced
restoration updates the width/height of a job two slots later and leaves actor reach reduced.
The ignored-hit flight alias writes the neighboring job x and its own job height; a read
outside the modeled table raises. Restoration writes beyond the table are omitted as invalid
memory corruption. The link-job helper deliberately puts both objects at dragon+10.
Fixed sound IDs are emitted; the newly identified random picker and synth-channel release
callbacks remain deferred with their state/draws to T11.23. Real-disk outcome fixtures use
explicit endgame equipment and ordinary inputs; they verify hurt-script death/win, idle loss,
independent setup operands, exact frames and deterministic replay. Map/calendar and hoard
settlement remain T11.12/13 and T11.21 respectively. No game data is committed.

## ADR-024: Computer knights use the existing duel jobs and knight damage rules
**Status:** ACCEPTED (2026-11-17, T11.10i; follows ADR-011 / ADR-013 / ADR-015)
**Context:** The type-$10 knight chooses guards and attacks from day-indexed odds,
shares a last-action word with overlapping step data, and targets either duel position.
**Decision:** assets/computer_knight_rules.py checks handler identity, helper calls,
relocations and instruction operands before decoding the original ranges, odds and
step words. sim/fight_ai/knight.py uses the existing approach/move helpers, jobs and
knight-versus-knight hurt rule; its hit reply retains its distinct stop conditions.
The loader accepts explicit computer handles and a zero-based day index, selects
the black palette before scene creation, and assigns human ports while skipping
computers. Snapshots include the day and shared action word. The temporary legacy
sheet boundary retains all four knights on creation/reset until the map/sheet tasks
replace it; only humans supply port input and only existing map records synchronize.
**Consequences:** Draw order, the discarded taunt draw, failed-guard distance alias,
high-byte cooldown and overlapping data reads remain original (§10.31). As allowed
by ADR-011, step lookup uses a bounded phase rather than stale high register bits
that address arbitrary memory; invalid day indices raise. Calendar-to-day wiring
and original campaign initialization remain T11.12/13, map AI T11.14 and meeting
sheets/loot T11.21. Seeded ordinary-input wins and idle losses exercise hurt-script
death and deterministic full-state replay, including campaign input and settlement.
Pose previews are labelled separately from natural traces; no game data is committed.


## ADR-025: The pixel map shares persistent actors, inventories and the original PRNG
**Status:** ACCEPTED (2026-11-17, T11.12; follows ADR-009 / ADR-013 / ADR-015)
**Context:** The previous map used invented roads, a separate LCG, independent knight
resources and random walking encounters. The original uses pixel coordinates, terrain
masks, an ordered proximity list and persistent records shared with fights.
**Decision:** assets/map_rules.py checks instruction forms and relocated tables before
loading nodes, terrain, regions, seeds, defaults, names, starts, menu text and palette
parameters. sim/overworld.py owns pixel movement, budget exhaustion, proximity rows,
mode flags, lair records and original palette jobs. The host advances a map frame every
two PAL ticks; palette jobs advance every PAL tick. InputFrame serializes translated
menu keys. Map drawing uses exact mi.c frames in list order and the original Small.font
lookup. No render call initializes or steps map simulation. The road graph and sim/prng.py
are removed; rule tests explicitly inject synthetic MapRules instead of a runtime fallback.
Legacy location screens retain a temporary adapter: resources view the shared actor and
inventory, and sheet/fight entry and exit synchronize their remaining presentation fields.
**Consequences:** Terrain counts persist, edge pushes cost budget, screen entry applies
stale movement without charging, arrival rows retain the five-row-clear quirk and menus
wait for a valid digit without cancel. Invalid table/arrival/frame accesses raise rather
than corrupt memory or substitute frames; mi.c has no frame47 for a frogged computer.
Lair creature depletion alone does not hide a lair. New-game gift rolls remain with the
shared inventory initialization work. Budget exhaustion is exposed for T11.13; until its
scheduler arrives the host reports TURN COMPLETE without inventing day advancement.
T11.13 owns clock/dragon jobs, T11.14 map AI, and T11.15..21 the original place/loot callers.
Synthetic movement/proximity/menu/palette checks replace obsolete graph and LCG tests.
Real-disk tests independently inspect loaded tables, check movement without random fights,
shared state and replay; the pixel_map audit includes natural map captures and labelled
frame previews. No game data or generated contact sheet is committed.


## ADR-026: Suspend the original scheduler at round screens and reuse jobs for map flight
**Status:** ACCEPTED (2026-11-17, T11.13; follows ADR-011 / ADR-013 / ADR-025)
**Context:** Budget exhaustion needs the original four-record scheduler, four-round clock,
curse upkeep and dragon flight. The old human-only round logic and invented lunar banner
have already been superseded. Computer map decisions and inventory screens have separate tasks.
**Decision:** sim/turns.py advances record order and suspends before record0 setup at each
Next Day screen. Its skipped count and pending setup survive that screen. Logical handles
remain KnightId+1; turn_order maps original record indices to selection order, with unused
records assigned to computers. Proximity, daily gifts and dragon target draws share that order.
assets/turn_rules.py reads checked moon/gift/flight tables, text lists and operands. Shared
stat/gift/loot rules in sim/rewards.py implement the computer day gift and dragon settlement;
their state and the fifth actor inventory belong to the campaign. sim/map_dragon.py installs
type28 on the existing Fight/ScriptEngine. app/map_runtime.py retains its real job draws and
canvas; no alternate animation interpreter or frame counter is introduced. Clock day and moon
frame reach the existing fight runtimes. Palette fade snapshots join host replay hashes.
**Consequences:** The Next Day screen uses ch.piv, bold.f, the original linked text records,
loaded moon frames and map palette. Host updates own fade clocks and require release/press/
release; rendering does not advance calendar, jobs or colors. The original byte-wrapped
recency decay, word-wrapped regeneration, flight job's unusual initial position, hover edge
omissions, exact-zero-lives loot branch, sword typo and dragon gold-first double halving are
preserved (§11.19). Invalid text loops/table reads raise; random selection retries are bounded.
Game over uses the original farewell text and returns to title after fire. Computer turns
are scheduled but report unavailable until T11.14 supplies map decisions; no idle AI or
invented turn skipping is substituted. AI goal/walk/roam reset joins those cells in T11.14.
New-game hoard/market/lair rolls remain the initialization follow-up of T11.12, while hoard
and place/meeting sheets remain T11.15..21 and the full ending presentation T11.22. Existing
natural Dragon outcome tests continue to validate the arena; new caller-boundary fixtures
validate map attack and settlement independently. Contact sheets distinguish natural round/
flight captures from moon previews. No game data is committed.
