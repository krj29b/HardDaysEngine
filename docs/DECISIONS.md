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
**Status:** PROPOSED (2026-11-14). Confirm in Phase 4.
**Context:** All Moonstone pictures are 320×200. The brief asks for a 320×256 canvas.
**Decision (proposed):** Keep the 320×256 canvas and place the 320×200 area at y = 28, drawn 1:1.
The unused border remains available for debug read-outs.

## ADR-007: Python 3.12 pinned for development
**Status:** ACCEPTED (2026-11-14)
**Context:** The host default is Python 3.14. `requires-python` is `>=3.12`.
**Decision:** `.python-version` = 3.12, so uv provisions the interpreter that mypy targets
(`python_version = "3.12"`) and the version CI uses.
