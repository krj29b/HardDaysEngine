# pymoonstone

A clean-room Python 3.12+ reimplementation of the 1991 Amiga game *Moonstone: A Hard Days Knight* (Mindscape / Rob Anderson), built around a deterministic 50 Hz integer simulation core and a ModernGL + `pygame-ce` presentation layer.

## Architecture

The codebase is organized into strict unidirectional layers (`cli -> app -> render -> assets -> sim`):

- **Simulation (`src/pymoonstone/sim/`)**: deterministic 50 Hz integer/fixed-point kernel (persistent combat jobs and handlers, overworld, locations, palette FX, PRNG). No floats, no wall clock, no `random`, no I/O.
- **Assets (`src/pymoonstone/assets/`)**: reads original Amiga `.adf` disk images (OFS) at runtime, unpacks RNC1/LZSS streams, and decodes pictures, cels, `collide.hit` attack sets, `.t` arena layouts, `program`/`mog` hunk tables, and audio banks.
- **Render (`src/pymoonstone/render/`)**: ModernGL (OpenGL 3.3 core) `320x256` virtual canvas, indexed palette textures, optional CRT post-process, and debug hitbox overlay.
- **App & CLI (`src/pymoonstone/app/`, `src/pymoonstone/cli/`)**: `pygame-ce` window host, 50 Hz fixed-timestep loop, keyboard/gamepad input, audio mixer, and CLI entry points.

## Bring Your Own Amiga Disks

This repository contains **no game data**. Place your three original Amiga disk images (901,120-byte `.adf` files, any filenames) into `./disks/`, or specify their location using:

1. `--adf-dir <path>`
2. `PYMOONSTONE_ADF_DIR` environment variable
3. `adf_dir = "<path>"` in `config.toml`

Never commit `.adf` images or anything extracted from them.

## Running the Game

```powershell
uv sync                                  # install runtime + dev dependencies (Python 3.12)
uv run pymoonstone --list-disks          # verify your three .adf images are detected
uv run pymoonstone                       # launch the game (default 3x scale)
```

Useful CLI flags:

- `--scale <N>`: integer window scaling factor (default `3`)
- `--inspect-assets`: launch the 50 Hz ModernGL Lair Background, Sprite, and Hitbox Inspector (`--cel kn4 --background fol1.t`)
- `--extract-assets`: unpack ADF assets into `--output-dir` (default `./build/assets`) with `manifest.json`
- `--headless-frames <N>`: run `N` 50 Hz ticks on a standalone OpenGL 3.3 context without opening a window

### Default Player 1 Controls

- **Move / Navigate**: Arrow keys (`Left` / `Right` / `Up` / `Down`)
- **Attack / Fire**: `Space`; combine with a direction for strikes, blocks and dagger throws
- **Confirm / Select**: `Enter`
- **Status on the map**: `Left Ctrl`
- **End turn on the map**: `Tab`
- **Pause**: `P`

Practice uses two human knights; player 2 moves with `WASD` and fires with `F`.
Combat currently supports the three Trogg variants, Ratmen and human knight duels. Other creature
handlers, complete campaign fight setup and loot screens are still being migrated; see
[docs/PROGRESS.md](docs/PROGRESS.md). Combat requires the original disks.

## Development & Verification

```powershell
uv run python scripts/quality_gate.py    # ruff format/lint, mypy --strict, import-linter, radon, vulture, pytest
uv run pytest -m "not adf and not gl"    # unit tests needing neither disks nor OpenGL
uv run pytest -m adf                     # integration tests against your local ./disks/*.adf
uv run pytest -m gl                      # ModernGL rendering tests
```

See [AGENTS.md](AGENTS.md) for contributor/agent rules and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/DECISIONS.md](docs/DECISIONS.md), and [docs/PROGRESS.md](docs/PROGRESS.md) for technical documentation.

## License

MIT License — see [LICENSE](LICENSE).

*Moonstone: A Hard Days Knight* is (c) 1991 Mindscape / Rob Anderson. This license covers the `pymoonstone` source code only, not the original game or any data extracted from it. External reference projects (`moonstone-ace` and `openmoonstone`) are not included in this repository and were consulted as specifications only (see [docs/DECISIONS.md](docs/DECISIONS.md), ADR-001).

This is an independent, unofficial fan project. It is not affiliated with, endorsed by, or sponsored by Mindscape, Rob Anderson, or any current rights holder of *Moonstone*. All trademarks belong to their respective owners; the game's title is used only to describe compatibility. You must own the original game to use this software.
