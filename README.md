# pymoonstone

A clean-room Python 3.12+ reimplementation of the 1991 Amiga game
*Moonstone: A Hard Days Knight* (Mindscape / Rob Anderson).

- **Simulation (`sim/`)**: deterministic 50 Hz integer/fixed-point kernel. No floats, no
  wall clock, no `random`, no I/O.
- **Assets (`assets/`)**: reads your original Amiga `.adf` disk images (OFS), unpacks and decodes
  pictures, cels, hit sets and audio into `./build/assets/` with a deterministic JSON manifest.
- **Render (`render/`)**: ModernGL (OpenGL 3.3 core) 320x256 virtual canvas, palette textures,
  CRT post-process, debug hitbox overlay.
- **App (`app/`) / CLI (`cli/`)**: `pygame-ce` window host, 50 Hz fixed-timestep loop, input,
  audio mixer, command-line entry points.

## Bring your own Amiga disks

This repository contains **no game data**. Put your three original disk images (901,120-byte
`.adf` files, any names) into `./disks/`, or point to them with `--adf-dir`, the
`PYMOONSTONE_ADF_DIR` environment variable, or `adf_dir` in `config.toml`. Never commit the
images or anything extracted from them.

## Development

```powershell
uv sync                              # install runtime + dev dependencies (Python 3.12)
uv run python scripts/quality_gate.py  # ruff, mypy, import-linter, radon, vulture, pytest
uv run pytest -m "not adf and not gl"  # tests that need neither disks nor OpenGL
uv run pymoonstone --version
uv run pymoonstone --list-disks      # show which .adf images were found
```

Agents: start with [AGENTS.md](AGENTS.md).

## Licence

MIT (see [LICENSE](LICENSE)). The reference projects in `reference/` are used as
specifications only; see `docs/DECISIONS.md` ADR-001.
