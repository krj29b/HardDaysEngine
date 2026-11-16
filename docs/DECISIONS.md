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

