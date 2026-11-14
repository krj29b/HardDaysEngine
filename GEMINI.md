# GEMINI.md

All agent rules for this repository live in [AGENTS.md](AGENTS.md). Read it fully before any
change. The essentials, so they're never missed:

- Every task closes with a green `uv run python scripts/quality_gate.py`.
- `sim/` is deterministic: no floats, no `random`/`time`/I/O, integers only, 50 Hz ticks.
- Never commit `.adf` images or anything extracted from them. `reference/` is a specification,
  not code to copy (clean room, ADR-001).
- No `pass`/`TODO`/placeholder implementations, and no gate suppressions.
