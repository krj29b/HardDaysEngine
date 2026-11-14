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
