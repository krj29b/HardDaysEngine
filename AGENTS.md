# AGENTS.md: onboarding for AI coding agents

You are working on **pymoonstone**, a clean-room Python reimplementation of *Moonstone: A Hard
Days Knight* (Mindscape, 1991, Amiga). It has a deterministic 50 Hz integer simulation core and a
ModernGL + `pygame-ce` presentation layer. This file is the **entry point for every agent session**.
Read it fully before touching code. (`GEMINI.md` only points here.)

## 1. Read in this order

1. `AGENTS.md` (this file): rules and workflow.
2. `docs/IMPLEMENTATION_PLAN.md`: architecture, phases, and the task list with acceptance
   criteria.
3. `docs/PROGRESS.md`: current status of each task, plus notes and gotchas from earlier sessions.
4. `docs/DECISIONS.md`: architecture decision records (ADRs). Don't contradict an ADR without
   adding a new one that supersedes it.
5. `docs/ARCHITECTURE.md`: the binary format and timing specification (written in Phase 2).
   Implementation is written **from this document**, not from reference code (ADR-001).
6. `DEAD_ENDS.md`: approaches that failed. Never repeat one.

## 2. Session protocol (follow every time)

1. **Pick a task.** In `docs/PROGRESS.md`, choose the first `TODO` task whose dependencies are all
   `DONE`. If the user assigned a task, do that one instead.
2. **Claim it.** Set its status to `IN PROGRESS (<agent>/<date>)`.
3. **Study the spec.** Read the relevant section of `docs/ARCHITECTURE.md`. If it's missing or
   ambiguous, extend the spec first (from `reference/`, see §5), then implement.
4. **Implement with tests.** Satisfy every acceptance criterion in the plan.
5. **Verify.** Run `uv run python scripts/quality_gate.py`. Every gate must pass: ruff format,
   ruff lint (McCabe ≤ 10), mypy `--strict`, import-linter, radon (no function graded C or
   worse), vulture, pytest.
6. **Record.** Set the task to `DONE` in `docs/PROGRESS.md` with short notes (deviations, gotchas,
   follow-ups). Add an ADR to `docs/DECISIONS.md` for every design choice.
7. **Commit.** Use a message like `T<id>: <summary>`, one task per commit where practical.

If a task is too large for one session, split it into `T<id>a`, `T<id>b` and so on in
`PROGRESS.md`, and explain why.

## 3. Hard rules

### Legal and provenance (ADR-001)
- **Never commit game data.** That covers `*.adf`, and anything extracted from the disks: pictures,
  cels, PNG exports, audio, modules, hunk executables, tables, and fixtures derived from real
  disks. `.gitignore` covers `disks/` and `build/`, but you're still responsible.
- **Clean room.** `reference/moonstone-ace` (PolyForm Noncommercial) and `reference/openmoonstone`
  (AGPL-3.0) are *specifications*. Describe formats and behaviour in your own words in
  `docs/ARCHITECTURE.md`, then implement from that. Never transliterate their code, and never paste
  byte tables from the original executables into source. Original tables (animation, timing, AI)
  are **read at runtime** from the user's own `program` / `mog` files.
- Tests that need real disks use `@pytest.mark.adf` and the `adf_dir` fixture, which skips when no
  disks are found. CI tests use **synthetic** ADF images built in `tests/fixtures/`.

### Determinism (`src/pymoonstone/sim/`)
- **No floats.** Integers only; fixed point where the original used it. Wrap to 16 or 32 bits where
  the 68000 relies on overflow.
- **68000 ≠ Python arithmetic.** `DIVS`/`DIVU` truncate toward zero and Python `//` floors, so use
  the helpers in `sim/` for C-style division and modulo.
- **No `random`, `time`, `datetime`, `pathlib`, `os` or any I/O** in `sim/`. Use the simulation PRNG.
  Everything advances in 50 Hz ticks (one PAL VBL per tick).
- Preserve the original **order** of entity updates and PRNG draws.
- These rules are enforced by `.importlinter` and `tests/unit/test_layering.py`.

### Layering (`.importlinter`)
`cli -> app -> render -> assets -> sim`. A layer may import only layers to its right.
- `sim` imports no framework, no I/O module, and no other `pymoonstone` layer.
- `assets` never imports `pygame` or `moderngl`, so it runs headless in CI.
- `render` never imports `pygame`. It receives a `moderngl.Context` from `app`.

### No rigour theatre
- Never write `pass`, `TODO`, `...` bodies, placeholder returns, or `# type: ignore` /
  `# noqa` just to silence a gate. Every function is a complete implementation. If something
  can't be implemented yet, don't add it.
- Never weaken a gate config to make it pass. Fix the code, or propose an ADR and ask the user.

### Performance
- **No per-pixel Python loops at runtime.** Use numpy or a shader. Decoding at extraction time may
  loop when the data is small, but prefer numpy for bitplane conversion.

### Code quality
- Python 3.12+, fully typed (mypy strict), ruff clean, `dataclass(slots=True, frozen=True)` for
  decoded records.
- When implementing behaviour described in the spec, cite the spec section in the docstring,
  for example `"""Picture loader (ARCHITECTURE.md §4.2)."""`.

## 4. Commands

```powershell
uv sync                                  # install deps (Python 3.12, see .python-version)
uv run python scripts/quality_gate.py    # the full gate; must be green before DONE
uv run pytest -m "not adf and not gl"    # tests needing neither disks nor OpenGL
uv run pytest -m adf                     # tests needing the real disks
uv run lint-imports                      # layering contracts only
uv run pymoonstone --version
uv run pymoonstone --list-disks
```

ADF location, in priority order: `--adf-dir`, then the `PYMOONSTONE_ADF_DIR` environment variable,
then `adf_dir =` in `config.toml`, then `./disks/`.

## 5. When stuck

- The original behaviour is the truth. For the Amiga file formats, `reference/moonstone-ace` docs
  and sources are the specification. `reference/openmoonstone` targets the **DOS** release: use it
  for behaviour (combat, AI, movement), never for Amiga byte layouts.
- The references disagree → moonstone-ace wins (it was verified against the original 68k code).
  Note the conflict in `docs/ARCHITECTURE.md`.
- You'd need to change architecture → stop, write a `PROPOSED` ADR in `docs/DECISIONS.md`, and ask
  the user.
