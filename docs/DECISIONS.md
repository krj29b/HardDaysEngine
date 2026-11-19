# Architecture Decision Records

## ADR-001: Clean room; the references are specifications
**Status:** ACCEPTED (2026-11-14)
**Context:** `reference/moonstone-ace` is PolyForm Noncommercial 1.0.0 and `reference/openmoonstone`
is AGPL-3.0. Their terms can't be combined with each other or with HardDaysEngine's MIT licence.
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


## ADR-027: Computer map decisions share campaign records and retain frame continuations
**Status:** ACCEPTED (2026-11-17, T11.14; follows ADR-025 / ADR-026)
**Context:** The scheduler already visits computer knights, but their map turns were unavailable.
Their original rules draw each frame, retain one walking line per turn, and call meetings
synchronously before finishing the frame. The host suspends while an arena scene runs.
**Decision:** `assets/ai_map_rules.py` reads checked executable operands into frozen rules;
`sim/ai_map.py` and `sim/ai_shop.py` implement the original decision order and shopping quirks.
Campaign-owned `MapAiCells` retains sorted rows, target/wish cells, line/error and encounter
continuations. Scheduler classification resets the town and line/roam flags. A meeting resumes
at terrain/charge, and a Dragon return resumes at the budget test, without repeating decisions.
The existing duel runtime and original loot-transfer rule handle computer-initiated meetings.
Computer actors retain their authoritative map resources at fight entry; the legacy sheet
adapter is synchronized for display, without replacing those actor records.
**Consequences:** Equal-distance roam swaps, the fourth null opponent draw, stale sword item,
sword fallthrough, signed thresholds and high-byte dagger debit are preserved (§11.20).
All mutable cells enter host hashes. Real-disk tests independently read operands, reject
changed instructions, compare frame RNG state with an independent recurrence/draw oracle,
and replay natural computer turns through three days with existing encounters and calendar.
Full human protection/meeting/loot sheets remain T11.21, item jingles remain T11.23, and
new-game hoard/market/lair gift initialization retains its existing inventory-task follow-up.
No alternate campaign model, reference implementation code or game data is added.

## ADR-028: Decode sheet initialization through the existing bounded store reader
**Status:** ACCEPTED (2026-11-17, T11.15a)
**Context:** The seven sheet text tables live in BSS and acquire relocated string
pointers through executable initialization. Some cells remain zero, while others
explicitly point at empty strings. Panel lists are embedded in code.
**Decision:** `assets/sheet_data.py` reuses `MogCode` and `StoreRun` for the bounded
initializer and reads the two panel lists through checked relocated loads.
Frozen records retain sparse text cells, signed positions, orientation and list
order, plus the original sheet palette and checked class colour pairs.
An independent real-disk walker verifies every initialized cell and panel
entry. Unsupported code versions and malformed pointers/lists raise.
**Consequences:** T11.15 is split into data decoding (a) and the sheet loop,
presenter, callers and contact-sheet acceptance (b). Parent completion requires
both. This follows the existing layer boundaries and introduces no parallel
sheet model. No original strings, extracted tables or generated images are added
to source control. Specification §12.25 records the initialization boundary.

## ADR-029: Shared sheets own their pointer, dispatch state and ordered draw lists
**Status:** ACCEPTED (2026-11-17, T11.15b; follows ADR-025 / ADR-028)
**Context:** Status was an invented text screen, and lair wins and travel inspection
had no shared loot caller. The original loop couples hit regions to the drawn icons.
**Decision:** `sim/sheets.py` dispatches original sheet actions over persistent
campaign actors and inventories. Frozen rules and geometry live in `sheet_types.py`.
`sheet_layout.py` rebuilds ordered icons, text and regions during updates, while
`sheet_actions.py` handles transfers and item effects. `app/sheet_screen.py` loads
original cels, text/palette tables and checked transaction operands and renders
stored output. The host uses STATUS for shared sheets and hashes their mutable
state, including the retained last-item cell. Status preserves existing Dragon
jobs. Travel inspection restores the saved map position on close. Lair outcomes
open loot or status, and emptied loot is tidied before returning to the map.
**Consequences:** Held fire operates on live regions every PAL tick, and exhausted
items immediately lose their regions. Exact caps, wrapping, sparse button tables,
the byte-indexed armour transfer and last-row moonstone spacing remain specified
quirks. The obsolete status renderer and its metric assertions are removed.
Real-disk tests check rule operands independently, original sheet actions, a
natural Trogg lair win, caller settlement, replay hashes and render purity. The
natural audit uses ordinary heavy-sword inputs with explicit equipment fixtures.
Original wave count write-back remains unchanged, including its word underflow.
Re-entry uses the engine's signed exhaustion test so a wrapped terminal count
continues to open remaining loot.
Town transactions and their callers remain T11.16/17, place-specific callers
remain T11.18–21, and sheet sound playback remains T11.23. New-game inventory
rolls retain the existing initialization follow-up recorded by T11.12/13.


## ADR-030: Decode town buttons as immutable snapshots before replacing the host
**Status:** ACCEPTED (2026-11-17, T11.16a; follows ADR-028 / ADR-029)
**Context:** The legacy Highwood menu omits the market and the existing location
model still drives invented goods and services. The original town builders rewrite
one scratch record between registrations, retaining geometry from previous buttons.
The host replacement also spans sheet continuations, service callers and obsolete
model/test removal, which need their own real-disk presentation acceptance.
**Decision:** Split T11.16 into executable data (a) and host replacement (b).
`assets/town_data.py` checks the two original builders' instruction operands,
relocated scratch pointers and registration calls, then emits five immutable
`sim/town_types.py` records per town. It reads names, picture and pointer filenames,
initial coordinates and Space sheet selection through checked loads. The two
loaders must agree on their shared pointer and name cells. The synthetic fixture
changes widths between buttons to verify inherited fields; an independent real-disk
walker executes stores sequentially and snapshots the byte record at registration.
**Consequences:** No original geometry, strings or game data are committed. Invalid
instruction forms, pointers, extents, ids and text fail explicitly. The frozen
simulation records follow the existing asset-to-simulation boundary and introduce
no new screen loop. T11.16b must consume these records, remove location_screens.py
and locations.py with obsolete tests, wire sheets/services and spent exit, hash
mutable town state, and review the real-disk contact sheet. T11.16 remains open;
T11.17/18 still implement original transactions and services. T11.16a has no
presentation change and therefore needs no contact sheet.


## ADR-031: Original town caller cutover precedes child-screen acceptance
**Status:** ACCEPTED (2026-11-18, T11.16b1; follows ADR-029 / ADR-030)
**Context:** The old location model has invented transactions and ritual rules.
The decoded town layouts are available, while original transactions and service
screens are explicitly scheduled in T11.17 and T11.18. Requiring their complete
screens in T11.16b would make T11.18 depend on its own caller acceptance.
**Decision:** Split T11.16b into b1 (presenter and host cutover) and b2 (loading
prologue, child integration and final every-button acceptance after T11.17/18).
T11.18 depends on b1 instead of the aggregate T11.16. Parent tasks remain blocked
until b2 passes. This changes task sequencing within the accepted architecture.
`sim/towns.py` owns the pointer and pending request. `app/town_screen.py` loads
original picture/cursor assets, applies the hardware-sprite palette offset and
renders without advancing rules. GameSession retains the parent while shared
sheets run, resets the pointer on return, hashes suspended town cells and spends
the turn only at the exit button. Original service requests report unavailable
until T11.18; no legacy transactions are retained. Sheet transactions remain
T11.17. The original loading-text prologue remains b2.
**Consequences:** location_screens.py and locations.py, plus their obsolete tests,
are removed. Stonehenge explicitly reports unavailable pending T11.19 instead of
using the removed all-moonstones ritual model. No original data is committed.
Synthetic tests cover all ten routes, rectangle edges, Space priority and pointer
bounds. Real-disk tests cover loaded sheets/returns, service call boundaries,
held-direction navigation, turn exhaustion, foreign-controller isolation and
replay hashes. GL tests compare every town pixel against the loaded picture plus
hardware-pointer indices and verify render purity. Contact-sheet captures label
service attempts without presenting them as implemented service screens.


## ADR-032: Human shop rules and one-time campaign stock use decoded executable data
**Status:** ACCEPTED (2026-11-18, T11.17; follows ADR-029 / ADR-031)
**Context:** Shared sheets 5/6 had no right-panel offers or transaction handlers,
and no market inventory was initialized. Market rolls follow the hoard rolls
and precede lair loot; adding isolated stock rolls would shift gameplay randomness
away from the original. The initialization follow-up was recorded by T11.12/13.
**Decision:** assets/shop_data.py reads checked comparison/debit pairs, equipment
stores, HP gains, dagger cap, all twelve price stores, sell cap and pointer split
into immutable ShopRules. StockRules captures the original startup counts,
extra stock, shared key masks and lair loot bands. sim/shops.py implements human
transactions; the existing shared-sheet dispatcher and panel builder consume
these records. sim/stock.py completes the ordered hoard/market/key/lair rolls
once during host campaign installation, using the existing Rng/GiftState.
The world owns finite market inventory, which is already covered by the host
hash. Shop opening and rendering do not roll or replenish stock; reset rebuilds
it. Hoard gifts retain actor effects; inventory-only gifts share the same
rejection memory without those effects. No second shop state machine is added.
**Consequences:** Preserve signed byte/word/long comparisons, wrapped additions,
armour downgrades, lowest mask bit, finite stock, mask XOR/OR, ring HP effects,
stat purchases on both sheets, and sound-event ordering. The original invalid
inventory-address HP write remains omitted as specified in §12.11. Redraw
recalculates the displayed knight, so bought sharp swords equip normally.
The ordinary left worn-sword region stays type 2; no invented sell region is
added. No zero-price policy from the reference port's mod system is imported.

The corrected startup stream exposed a legal (305,190) map position indexing
1038 in the original signed byte lookup. §11.21 corrects the nominal-grid-only
allocation model: retain the bounded adjacent executable suffix reachable by
the decoded sprite bottom edge, while keeping both nominal 1000-byte grids.
Negative indices and reads beyond supplied suffixes still raise. This preserves
original clipping, table values and draws, and fixes the existing natural
multi-day replay test without changing its controller, seed or expectations.

Synthetic tests use deliberately different prices/caps and cover transactions,
byte masks, signed boundaries, depletion, sparse price writes and malformed
instruction pairs. Independent real-disk table/RNG oracles verify startup for
all four seed selections; host checks cover both smith callers, natural stock
purchase/sale, no replenishment, reset and hashes. GL checks verify render
purity and the shop audit contact sheet is reviewed. No game data is committed.

## ADR-033: Project renamed from pymoonstone to HardDaysEngine
**Status:** ACCEPTED (2026-11-18)
**Context:** The project name `pymoonstone` contained the original game's title, which could
read as an official or endorsed product and invite trademark complaints.
**Decision:** The project is named **HardDaysEngine**. The Python package, distribution and
console scripts are `harddaysengine`, `harddaysengine-extract` and `harddaysengine-inspect`; the
ADF environment variable is `HARDDAYSENGINE_ADF_DIR`; the base renderer is `HardDaysRenderer`.
The game's title is still used descriptively (to say what the engine is compatible with) in
docs, docstrings and the README, which carries a non-affiliation disclaimer.
**Consequences:** `PYMOONSTONE_ADF_DIR` is no longer read; users must rename the variable.
Existing virtual environments need `uv sync` to install the renamed package. The GitHub
repository and local checkout folder are renamed separately from the code.

## ADR-034: Service transactions precede their three animated screen callers
**Status:** ACCEPTED (2026-11-18, T11.18a; follows ADR-029 / ADR-031)
**Context:** The original dice house, healer and mystic require three screen loops,
executable tables, transaction rules, script jobs and suspended town continuations.
This exceeds one session. Existing town requests already report unavailable pending
the complete original screens; there is no legacy model to retain or replace here.
**Decision:** Split T11.18 into a (checked runtime rule data and pure transactions)
and b (animated screens, text/scripts, input/waits, replay state, town returns and
contact sheets). assets/service_data.py reads checked operands and relocated tables
into frozen sim/service_types.py records. sim/services.py operates on persistent
Actor/Inventory/Rng records and exposes donation cells plus service outcomes for the
later caller. Stat selection and derived values reuse the existing reward helpers.
No new state machine or host route is introduced by part a.
**Consequences:** Preserve equality-only healer life caps, donation overdraft,
signed comparisons, word/byte wrapping, mystic stat-before-percent draw order,
larger-donation penalties, dice rejection/display order and uncapped payouts.
Following the existing bounded-memory policy, omit the mystic's stale-pointer
write when no stat is selected and the dice search's unreachable twelfth row.
Retain the specified zero bonus above the mystic table. Invalid data and bounded
rejection exhaustion raise. Part b still owns actual playability and presentation
acceptance; the parent stays open and town services remain unavailable until then.
Independent local-disk reads and a separate random recurrence check tables and
draw order without committing original strings, tables, extracted assets or images.

## ADR-035: Fight sounds are ordered front-end requests; pickers live in sim
**Status:** ACCEPTED (2026-11-18, T11.23a; refines ADR-016 / ADR-022 / ADR-023 sound notes)
**Context:** T11.23 covers the sound pickers, the original synth sequencer, per-arena sample
banks and scene music. That is more than one session. The pickers draw from the shared generator,
so skipping them left fights that reach one with fewer draws than the original. The other parts
are presentation only. Before this, fights reported sounds as bare ids in two lists (handler
sounds and script `$A4` sounds), so their relative order was lost. Fixed-channel and release
calls were also reported as ordinary ids.
**Decision:** Split T11.23 into a (sound-only engine routines and the request model in `sim`),
b (the hunk-16 channel front end and an exact synth sequencer in `app`, replacing the legacy mixer
logic) and c (per-arena bank slots and instrument relocation, scene and area music, and sheet
sound events). Part a adds `sim/sound.py`: a frozen `SoundRequest` (play, fixed or release, with
channel and release mask), step records for the picker kinds, and `SoundCells`. Fight sounds form
one ordered list: handlers, script `$A4` (through an optional engine callback) and routines append
to `Fight.sounds`, and `TickOutput.sounds` stays empty under a fight. `assets/sound_rules.py`
decodes every sound-only routine from checked instructions, including the relocated call targets
that name the front-end entry and channel. The Dragon and Guardian fixed-sound routines move out
of their rules, because they are fixed-channel requests, not plain ids. The executable keeps the
picker cells in code and data hunks that are never reset, so `FightLoader` owns one `SoundCells`
per session (like the flyer and drake cells) and the fight snapshot includes it.
**Consequences:** Fights that reach a picker now make the original number of draws, which changes
their replays from that point. The host still sends only ids to the legacy mixer until part b; a
release currently plays its silence id there. Replays started from the same loader must restore
the session cells. The two list pickers share one offset word, as in the original, and a read
outside the decoded window raises (it is unreachable from the stored start values).

## ADR-036: The healer and mystic screens are a tick-driven child of the town
**Status:** ACCEPTED (2026-11-18, T11.18b1; follows ADR-031 / ADR-034)
**Context:** The donation screen, healer and mystic are blocking 68k loops: fire waits, frame
waits, a pointer moved by the VBL interrupt, and a transaction in the middle. The dice house
additionally runs a fight-engine job and its scripts. All three in one session was too much.
**Decision:** Split T11.18b into b1 (donation screen, healer, mystic) and b2 (dice house).
`assets/service_screen_data.py` walks the opening, region and redraw routines with a small
checked instruction walker: each instruction must be one of a few known shapes, every relocated
operand must point where that shape expects, and anything else raises. Reply lists, tables and
constants are checked operands at their positions. The linked text-record reader of
`turn_rules` becomes public (`text_list`) and is shared. `sim/service_screen.py` models the
routine as phases (greeting, donation, close wait, close fire, done), one VBL per step. Fire
waits are press-then-release. The pointer moves every VBL, even during a fade or a frame wait.
Only the routine pauses while a palette fade runs, matching the blocking original fades. The
greeting's synth starts are a new `SoundKind.START` (channel and sequence, no busy marking). The
host adds `GameScene.SERVICE` as a child of the retained town, uses the town's pointer sprite,
hashes the screen state, and returns to the town without spending the turn. `render_to_scene`
becomes a dispatch table so that adding a scene does not raise its complexity.
**Consequences:** The mystic's leave clears the donation before the reply check, so it answers
with *nothing given*; its *refused* list is decoded but unreachable, as in the original. A press
that lands inside the 2-frame repeat wait is ignored. The dice house stays explicitly
unavailable until T11.18b2. Text uses `Small.font` (font table entry 0) without narrow spacing.

## ADR-037: The dice thrower runs in its own fight engine instance
**Status:** ACCEPTED (2026-11-18, T11.18b2; follows ADR-036 / ADR-035)
**Context:** The original dice house reuses the fight engine. The thrower is a pool record with a
job of type `$44` that runs hunk-42 scripts. Its handler reads the pointer and fire, takes the
stake, and switches scripts. The throw script calls a sound routine that draws from the shared
generator. A hand-written animation would lose those draws and the original timing.
**Decision:** `sim/dice_house.py` builds a fresh `Fight` each round (the original clears the jobs
each round) with the hunk-42 `ScriptImage` (`script_image(mog, 42)`), the dice cels as frame
list 1, the decoded sound routines, and a type-`$44` handler bound to the visit state. The
handler follows the common exit: a script cell of 0 ends the job. One engine pass runs every
frame-wait VBLs, as in fights. The visit raises if a script calls a routine with no
implementation. `$A8C2` joins the sound rules: one discarded draw, then a fixed-channel request
for the table's first byte. `assets/dice_house_data.py` reads every operand at a checked
position, and walks the region list, the colour-ramp table, the dice draws and the suffix
stores. Roll texts keep their prefix up to the original number field, then add the digits and
the decoded suffix. The host adds `GameScene.DICE` as a town child, with the town's pointer and
its y position, and adds the visit snapshot (state, jobs and pool records) to the replay hash.
**Consequences:** The roll's PRNG position includes the throw script's discarded draws. Entering
with gold not above 0 shows nothing and leaves the town untouched. Each round's fight instance
shares the session generator but not the fight loader's sound cells. Its hunk-42 routines draw
no cell state, so a default `SoundCells` is enough. The thrower's draws use the cel hot spot like
the fights; the dice faces and roll text use the shared cel drawer offsets of §12.31.


## ADR-038: Town welcome loading uses the existing PAL fade continuation
**Status:** ACCEPTED (2026-11-18, T11.16b2; follows ADR-031 / ADR-036 / ADR-037)
**Context:** The three service screens now exist, completing the dependencies of
final town acceptance. Town entry still skipped its message screen. Reference
inspection shows that the town loaders call a fixed welcome list with a stored
town-name pointer; the fourteen-message cycle belongs to the fight-loading path.
**Decision:** assets/town_loading_data.py checks both loaders, their shared text
cell/list, the message/font filenames, font slot and message/fade calls. The
existing linked-text reader accepts validated pointer substitutions without
modifying executable data. Frozen TownWelcome records retain text order/styles.
TownEntry belongs to the retained TownState and enters the existing host hash.
The host advances map fade-out, welcome fade-out and town fade-in on its PAL
palette clock, with transitions after each palette step. Loading and town fades
ignore button/key dispatch; the owner's pointer moves during the town fade.
Child returns reset the pointer and fade the town without repeating the prologue
or spending the turn. The existing LOCATION scene presents each retained phase.
**Consequences:** No render call advances loading, no fire wait or arbitrary disk
loading timer is added, and the town welcome does not advance fight-loading text
state. The existing town/sheet/service/dice models remain authoritative. Actual
child-screen return tests replace the old mocked service-boundary checks. The
places audit now captures both welcomes, all ten outcomes, Space sheets and
returns; original content and generated pictures remain outside version control.

## ADR-039: Stonehenge retains its caller and runs the ritual on the existing script engine
**Status:** ACCEPTED (2026-11-18, T11.19; follows ADR-029 / ADR-035 / ADR-037)
**Context:** The original visit branches between an item offer and a matching-moonstone
hand-over to program. Its ritual has two hunk-42 jobs and a percent-draw sound callback;
a separate animation or a generic victory screen would lose its script state and rules.
**Decision:** assets/stonehenge_data.py checks executable operands, relocated text and
asset pointers, class shade rows, spawn/handler identity, fade periods and wait lengths.
Frozen StoneRules and RitualRules retain these values; the simulation owns the match,
blessing and ending-code rules. RitualRun uses Fight/ScriptEngine with a fresh job pool
and the campaign Rng. The two initial passes precede the decoded frame intervals. The
stop handler ends its job and prevents the following engine tick, matching the original
loop. The percent callback is local to this ritual script image and emits ordered sound
requests without changing the fight sound decoder's scope.

StoneScreen presents retained messages or ordered FightCanvas output and decodes He.a.
GameSession keeps StoneState, ritual work and waits in replay hashes, runs the decoded
blocking PAL fades, and retains shared sheets during their outgoing fades. An offered
item is consumed by existing sheet3 rules; blessing occurs only after natural ritual
completion. Sheet9 closes through the spent exit. A matching stone emits an immutable
EndingRequest with the decoded executable filename and exact boot flags. The quest
message remains visible in the terminal hand-over phase for T11.22 to consume; it does
not fall through to the old wizard-picture victory renderer or return to the map.
**Consequences:** Class colours, text, sound ids, frame waits and bit mappings come from
user disks. Equality-only life caps, byte wrapping and the Stonehenge/damage moonstone
pairing difference remain intact. Actual ending playback is T11.22; exact sample-address
and synth wiring remain T11.23. The ritual bank is decoded and its requests are emitted
through the existing audio boundary. Contact sheets cover both branches, natural ritual
poses, all four class ramps, temple returns and program flags. No game data is committed.

## ADR-040: Wizard dialogue uses the result of the shared reward roll
**Status:** ACCEPTED (2026-11-18, T11.20a)
**Context:** The wizard visit spans three animated screen stages and a temple
continuation. Its existing reward implementation serves computer knights but
discards the selected stat-table row, so a caller cannot choose the original
speech without changing the random stream or guessing among duplicate stat rows.
**Decision:** Split T11.20 into a (immutable reward results and checked runtime
dialogue) and b (animation, remaining screen data, host/hash integration, temple
exit and contact sheet). The existing reward functions return their selected
item/stat row and wizard outcome; callers that only need mutations keep using
the same functions. Decode counted speeches and their relocated tables from mog,
retaining field positions for item/gold substitutions without modifying data.
**Consequences:** No extra random draws, parallel reward model or screen route is
introduced. Message cycles remain campaign-owned. Synthetic boundary checks and
independent real-disk reads verify row identity, ordering and field locations.
Parent T11.20 remains open; the tower is not playable until b, which also removes
the wizard picture from the legacy victory presentation. No game data is tracked.

## ADR-041: Wizard stages retain original script jobs and their wait-before-input order
**Status:** ACCEPTED (2026-11-18, T11.20b; follows ADR-039 / ADR-040)
**Context:** The wizard uses three script-driven stages with different frame waits,
a rotating loading message, two pictures, a class ramp and a shared reward roll.
Restarting scripts for display or accepting fire every tick changes its timing.
**Decision:** Decode the remaining wizard operands and asset references into frozen
rules. A visit owns the original Fight/ScriptEngine pools, using all five aliased
cel slots and ordered FightCanvas composition over text-bearing backgrounds.
Sample held fire only when a decoded frame wait expires. The host retains the
visit through blocking PAL fades and sheet9, and hashes the state independently
of rendering. The session owns the rotating loading-text cursor, available to
later loading callers; rewards reuse T11.20a and the campaign generator.
**Consequences:** Preserve two initial tower passes, continuous jobs during target
fades, fresh pools at stage changes, direct gift-to-farewell palette installation,
and a spent temple exit. Original data comes from user disks. Exact sound-bank
instrument wiring remains T11.23c, as for other original place screens. The wizard
pictures cease to be the legacy victory backdrop; program endings remain T11.22.

## ADR-042: Home villages retain their caller through the shared temple
**Status:** ACCEPTED (2026-11-18, T11.21a)
**Context:** The four home-village place kinds still fall through to an unavailable
screen. They share one life-reward routine and temple caller, while the Valley
requires different combat loading, rewards and turn accounting.
**Decision:** Split T11.21 into home villages (a), Valley (b) and meetings/hoard (c).
Decode village operands into frozen rules. Retain a small integer-only visit
through the map fade, one reward application, shared sheet 9 and temple fade.
Use the existing authoritative actors, sheet presenter, PAL palette state and
map re-entry. All village colours reward the active visitor, as the original
common caller does; the signed byte comparison and wrapping addition are kept.
**Consequences:** No village artwork, duplicate inventory model or random draw is
introduced. Hash ownership and wait state; clear them on campaign reset. The
parent remains open until Valley and meeting acceptance also pass. Tests cover
synthetic operands, signed boundaries, all village/visitor combinations, natural
temple input, replay and rendering. No disk-derived assets are tracked.

## ADR-043: The Valley retains its caller across the existing Guardian fight
**Status:** ACCEPTED (2026-11-18, T11.21b)
**Context:** Guardian jobs already work, but a Valley visit needs a dedicated
packed background/default obstacle, original messages and loading, and different
win/loss turn accounting. A regional lair arena and generic spent fight exit
would change the encounter.
**Decision:** Decode Valley rules/messages and the default arena operands. Extend
the existing fight loader's background selection with the original unpacked
Guardian stage. Retain a Valley caller through fades, the active encounter,
victory input/wait/reward or loss temple, using the authoritative campaign state.
Reuse loading text/cursor, stat picker, shared sheet and map return. Rewards run
once; no-key and victory returns retain spent, loss spends only after the temple.
**Consequences:** Preserve the original extra two-life penalty, wrapping fields
and one moonstone draw. Keep §12.17's bounded no-stat behavior and guaranteed map
re-entry on a zero moonstone draw. Natural combat, original operands, render
purity and replay traces verify the integration; no assets or tables are embedded.

## ADR-044: The ending is decoded from `program` code into a step plan
**Status:** ACCEPTED (2026-11-18, T11.22a)
**Context:** The quest ending is the second pass of `program`: a caption, a loader, four scene
bodies and a closing text. The existing intro player is a hand-written timeline with scene
offsets and frame counts typed in, which ADR-001 forbids repeating for new work. The scene bodies
are straight-line code made of a dozen instruction forms.
**Decision:** `assets/ending_data.py` walks the top level, the loader, the palette tweaks, the
scene bodies, the spawn helpers, the pairs set-up, the bare-tick loop, the caption recolour and
the fade operands. It accepts only the forms in §12.39 and names routines and cells by their
checked addresses. The result is a frozen `EndingPlan` of typed steps (`sim/ending_types.py`).
It reuses `MogCode` for relocated reads, with a separate CRC gate for `program` hunk 0. The
player is a separate task (T11.22b) and executes the plan; it does not reuse the intro timeline.
**Consequences:** No ending value, file name, colour or script offset is embedded in source; a
different `program` build raises instead of playing a wrong ending. `MogCode` error text says
"mog" for some failures on `program`; the ending decoder's own messages say "program". The intro
player stays as it is until someone rewrites it on the same decoder.

## ADR-045: Sheet ownership is explicit in retained encounter continuations
**Status:** ACCEPTED (2026-11-18, T11.21c)
**Context:** A meeting can open a defender's protection sheet or a winner's loot
sheet while the map turn still belongs to the initiator. Generic fight returns
currently skip those sheets, and dragon wins skip their hoard.
**Decision:** Retain one encounter caller across blocking fades, optional message,
duel and sheet. Add explicit owner selection at the existing sheet boundary;
do not swap authoritative actors or the map turn. Reuse original transactions,
fight settlement and AI loot. The dragon caller uses the same retained sheet
return with deferred spending. Decode the original caller operands and text.
**Consequences:** Defender input and failed-scroll confusion remain isolated to
the correct knight. Human meetings retain movement; computer pending decisions
resume once. Acquisition/Wyrm sheets remain in the shared loop. Preserve the
signed loser-life test governing sheet1's take scope. Natural duels,
dragon fights, synthetic operands, replay and contact sheets verify the change.
No additional combat/inventory model or embedded game data is introduced.

## ADR-046: The ending player is a VBL generator over the plan with a pixel-free canvas
**Status:** ACCEPTED (2026-11-18, T11.22b)
**Context:** The ending's control flow nests frame loops, pairs, bare ticks, a wipe loop and
script callbacks that wait in the middle of a job pass. A hand-written state machine for every
nesting level would be large and easy to get wrong. The simulation layer may not hold pixels.
**Decision:** `sim/ending.EndingRun` runs the plan as a Python generator that yields once per
VBL after the palette tick. Callbacks are generators too, so a flash inside a job pass stalls
the frame as in the original. Every screen change is a call on an `EndingCanvas` protocol, made
in the original order; `app/ending_screen.EndingPixels` implements it with numpy buffers. The
generator cannot be serialised, so determinism is shown by hashing an explicit snapshot (step,
tick, cells, jobs, palette, picture cell, wipe and ramp state) at the same points in replays.
The dirty-rectangle restore is modelled as a full copy of the shown buffer, and the incremental
wipe scroll as a window into the block panorama; both give the same image (§12.39.10). The
program palette gets its own model (live palette, registers, one fade, six ramps) because
flashes depend on the registers being written only on change, which `PaletteFxState` does not do.
**Consequences:** No save or restore in the middle of the ending. The disk loading time is not
emulated. Interpreter opcodes whose memory the ending never sets up (`$C4`, `$CC`, `$D0`, `$FD`,
table calls) raise instead of guessing. The invented VICTORY screen is removed; the ending is the
only way a campaign is won, and it returns to the title with a fresh campaign.

## ADR-047: The synth is the original sequencer driving an event-level Paula model
**Status:** ACCEPTED (2026-11-18, T11.23b; follows ADR-035)
**Context:** Until now `PaulaSynthMixer` guessed at the synth: it invented opcodes ($81 to $8B),
read a "voice header" that does not exist, and picked channels by age. Sounds came out wrong or
not at all, and release requests played their silence as a sound. The original synth is a full
sequencer. Its timing depends on Paula: DMA edges, buffer reloads, and audio interrupts that
reload the loop part of an instrument or switch a one-shot off. Every sound and every piece of
music in `mog` goes through it. A cycle-exact Paula, or a per-sample Python loop, would be too
slow.
**Decision:**
- `app/synth.py` implements the hunk-44 sequencer (start, tick, every command, tremolo and
  vibrato stages, envelope, register pass, interrupt routine and fade) from §6.2, in the
  original's register write order. It works on a copy of hunk 44, so references, the voice
  stacks and the init wipe act on the original bytes.
- `app/sound_front.py` implements the hunk-16 busy byte and cursor. Requests from `sim` pass
  through it unchanged.
- `app/paula.py` models the chip at event level. Writes happen at the start of a frame or at a
  reload. DMA rises are latched at the end of a CPU batch. Reloads and interrupts are
  processed in time order. Output is rendered per channel with numpy up to the moment its
  registers change. A loop without interrupts is rendered with a modulo in one numpy call, until
  a write to that channel or the end of the frame.
- `assets/synth_data.py` ties the behaviour to the supported code with a CRC-32 of hunk 44
  `$0000..$0BE7` and hunk 16 `$0000..$0133`, then reads every table address and operand at a
  checked position. The instrument count comes from the terminator row.
- The engine runs the synth tick, then the requests queued during the host tick, then renders.
- The legacy synth tables in `assets/tables.py` and the invented combat SFX aliases go away
  with the mixer.

**Consequences:** Bank instruments keep their raw, unrelocated sample offsets, which read as
silence until T11.23c loads the per-area banks and relocates them. Until then, most effects and
all area music are silent, and only sequences on the nine waveforms sound. The fade and stop-all
entries exist and are tested, but no host caller drives them yet (also T11.23c). A start in the
original happens during the frame after the VBL, and ours happens at the start of that frame:
the difference is under one frame. Bytes the original would mis-dispatch, a tempo of 0, an
instrument past the table and reads outside hunk 44 raise `SynthError`. The game's data reaches
none of them.

## ADR-048: Runtime bank slots and the original program music share the Paula renderer
**Status:** ACCEPTED (2026-11-18, T11.23c; follows ADR-047)
**Context:** The sequencer deliberately leaves bank addresses unresolved. Generic module
playback also gives different results for the program player's speed and effect subset.
**Decision:** Decode the bank loaders and execute their relocation pass over symbolic
(slot, offset) addresses, preserving replacement instruments and short DOS reads. Resolve
those addresses into five persistent app sample-memory regions. The existing sequencer and
Paula renderer play both bank samples and generated waveforms without a second mixer.
Decode the program player's period/vibrato tables from the executable and implement its
50 Hz row/effect rules on the same Paula interface. Only one executable owns audio at once;
entering mog reloads its synth/front-end and startup bank. Headless and audible playback
run the same player. Scene callers load their banks before requesting their first sounds.
Retain screens through original silent fades; record their waits and continuation in the
existing session hash. Map, sheet and service requests retain their original call order.
**Consequences:** No generic ProTracker playback, baked sample offsets or embedded pitch
tables. Sample-memory reads outside loaded data are zero. Original DOS short reads retain
the buffer tail (fresh tails are zero). The sheet buy/sell/coin events are text calls, so
they generate no invented audio. Original-module and relocated-bank traces are verified
against independent disk reads and deterministic PCM hashes. Audio hardware is not needed
for these checks.


## ADR-049: Campaign acceptance records ordinary inputs from a fresh game
**Status:** ACCEPTED (2026-11-19, T11.24)
**Context:** Scene acceptance tests often set up their own records or invoke private callers;
they cannot prove that selection, map navigation, combat, loot, shops and turns connect.
**Decision:** Add a bounded audit driver using only public tick inputs from the title. Use
four human ports to make turn hand-over explicit, the existing fight audit input policy,
and pointer movement to original regions. Save its replay and checkpoint hashes under
`build/`, replay from a fresh session, and review the rendered checkpoints. Record an actual
numeric day/moon advance as well as round screens. Correct historical Phase 6–10 statuses
with explicit replacement/survival notes rather than preserving obsolete DONE claims.
**Consequences:** No cheats, fixture equipment or extracted data enter the recording. The
route verifies integration, while subsystem tests retain responsibility for their full
behaviour. The existing replay format and runtime architecture are unchanged.

The campaign render check exposed legacy palette writes during menu rendering.
Resolve the menu-return fade on the tick and present palettes without mutating
simulation state; remove the render-dependent pending-fade flag. Include selection
and input-edge state in hashes. The older town/Stonehenge bold-text follow-up uses
checked runtime style bits on copied text records, as the existing Valley caller does.

## ADR-050: Decode the `program` intro pass and `mog` title/knight-select screens at runtime
**Status:** ACCEPTED (2026-11-19, T11.25; closes ADR-044 follow-up)
**Context:** The initial `app/intro_player.py` from Phase 8 embedded hardcoded scene tables,
script byte offsets, palette words, credit strings and `mog` hunk-4 text/palette offsets in
Python source, and had swapped knight portrait names 0 and 1. ADR-044 noted that the `program`
ending step-plan decoder (`T11.22a`/`T11.22b`) left the intro pass on the same executable ready
to move onto the checked walker without a second engine.
**Decision:** Extend `assets/ending_data.py` (`_Scene`) for intro sub-scenes (`$0818`, `$0862`),
`Series` (`$04C8`) and `BSR.W` spawn calls, and add `assets/intro_data.py` + `sim/intro_types.py`
to decode the `program` intro top level (`$0076..$0114`), loader (`hunk 8 $0136`), four progress
routines (`hunk 31 $0328..$05A8`), forward progress wipe (`hunk 31 $05AA`), mid-intro picture
setup (`hunk 8 $008E`), all 7 scene bodies, and the `mog` title-menu (`$18FA..$1B42`) and
knight-select (`$1CDC..$1FBC`) layouts with checked instruction words and relocations. Run the
intro plan on a deterministic 50 Hz VBL generator (`sim/intro.py`) sharing `JobPool`,
`ScriptRunner` and `ProgramPalette` with `sim/ending.py`. Because floppy disk read latency is
not emulated, progress steps that overlapped their `period = 2` fade-to-gold with the next disk
read wait `tail_period << 4` (32 VBLs, the same shift `hunk 10 $0C82` uses for `PaletteTo`) so
each screen's gold fade settles before the next white flash. Retain all ten picture buffers
(`0..9`) across scenes in `IntroPixels` so permanent background stamps (such as Scene 3's
altar and druids on `P4`) carry into later scenes.
**Consequences:** Zero hardcoded scene specs, script offsets, palette words, credit texts or
hunk offsets remain in `app/intro_player.py`. The `program` intro runs for 5,332 deterministic
VBLs, and the `mog` knight-select screen maps portrait 0 (Blue) to `"SIR GODBER"` and portrait
1 (Yellow) to `"SIR RICHARD"` directly from the `$1EFE..$1FBC` compare blocks.
