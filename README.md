# HardDaysEngine

A clean-room Python 3.12+ reimplementation of the 1991 Amiga game *Moonstone: A Hard Days Knight* (Mindscape / Rob Anderson), built around a deterministic 50 Hz integer simulation core and a ModernGL + `pygame-ce` presentation layer.

## Play on Windows

Download `HardDaysEngine-Windows-x64.zip` from the assets of a
[GitHub Release](https://github.com/krj29b/HardDaysEngine/releases) that includes a Windows build.
Extract the whole ZIP, put your three original Amiga disk images in its `disks` folder,
then double-click `HardDaysEngine.exe`. The ZIP includes Python and the engine's libraries.
Your graphics driver must support OpenGL 3.3.

### Publishing the Windows ZIP

Push a version tag such as `v1.2.3` on a commit containing the **Windows portable release**
workflow. GitHub Actions builds the ZIP on Windows, extracts and tests it, then attaches it
to the release for that tag. It creates the release if needed. To test before tagging,
select **Actions → Windows portable release → Run workflow** on a branch and download the
`windows-portable` workflow artifact. To run for an existing tag that contains the workflow,
use `gh workflow run windows-release.yml --ref <tag>`.

## Architecture

The codebase is organized into strict unidirectional layers (`cli -> app -> render -> assets -> sim`):

- **Simulation (`src/harddaysengine/sim/`)**: deterministic 50 Hz integer/fixed-point kernel (persistent combat jobs and handlers, pixel map, turn scheduler, sheets and places, palette FX, PRNG). No floats, no wall clock, no `random`, no I/O.
- **Assets (`src/harddaysengine/assets/`)**: reads original Amiga `.adf` disk images (OFS) at runtime, unpacks RNC1/LZSS streams, and decodes pictures, cels, `collide.hit` attack sets, `.t` arena layouts, `program`/`mog` hunk tables, and audio banks.
- **Render (`src/harddaysengine/render/`)**: ModernGL (OpenGL 3.3 core) `320x256` virtual canvas, indexed palette textures, optional CRT post-process, and debug hitbox overlay.
- **App & CLI (`src/harddaysengine/app/`, `src/harddaysengine/cli/`)**: `pygame-ce` window host, 50 Hz fixed-timestep loop, keyboard/gamepad input, audio mixer, and CLI entry points.

## Bring Your Own Amiga Disks

This repository contains **no game data**. Place your three original Amiga disk images (901,120-byte `.adf` files, any filenames) into `./disks/`, or specify their location using:

1. `--adf-dir <path>`
2. `HARDDAYSENGINE_ADF_DIR` environment variable
3. `adf_dir = "<path>"` in `config.toml`

Never commit `.adf` images or anything extracted from them.

## Running the Game

```powershell
uv sync                                  # install runtime + dev dependencies (Python 3.12)
uv run harddaysengine --list-disks          # verify your three .adf images are detected
uv run harddaysengine                       # launch the game (default 3x scale)
```

Useful CLI flags:

- `--scale <N>`: initial window size, 1..8 canvas pixels per pixel (default `3`)
- `--filter faithful|scanlines|crt|smooth`: presentation preset (default `faithful`, crisp pixels without effects)
- `--scale-mode integer|sharp-bilinear|fit`: viewport scaling mode (default `integer`)
- `--aspect square|pal`: source pixel shape (default `square`; `pal` gives 4:3 on the full 320x256 frame)
- `--crop full|playfield`: show the full 320x256 PAL canvas or crop to the 320x200 playfield (default `full`)
- `--fullscreen off|desktop|exclusive`: start windowed, borderless desktop fullscreen, or exclusive fullscreen (default `off`)
- `--vsync` / `--no-vsync`: enable or disable vertical sync (default `true`; `--no-vsync` presents once per 50 Hz tick)
- `--surround off|blur|solid`: decorative widescreen side-bar fill (default `off`)

Display settings can also live in `config.toml` (CLI flags win; unknown keys are an error):

```toml
[display]
scale = 4
filter = "crt"
scale_mode = "integer"
aspect = "square"
crop = "full"
fullscreen = "off"
vsync = true
crt_mask = "aperture"       # aperture, shadow, none
crt_mask_strength = 0.25    # 0.0..1.0
crt_scanlines = 0.65        # 0.0..1.0
crt_curvature = 0.08        # 0.0..0.3 (0 disables curvature and corner mask)
crt_bloom = 0.22            # 0.0..1.0 (0 disables bloom)
surround = "off"            # off, blur, solid
surround_brightness = 0.28  # 0.0..1.0 (blur mode)
surround_color = "#101014"  # #RRGGBB (solid mode)
```

Presentation presets (`--filter` or `F9` / `Shift+F9` at runtime; all presets run strictly after the palette lookup and never affect simulation or replay hashes):

| Preset | Look |
|---|---|
| `faithful` *(default)* | Even, crisp pixels at the selected aspect and scale mode with no effects |
| `scanlines` | Crisp pixels with output-grid scanlines (no non-integer moiré banding) |
| `crt` | Linear-light intensity-widening scanline beams, aperture/shadow phosphor mask, optional barrel curvature + rounded bezel, and separable bloom |
| `smooth` | 4× xBR-style edge-directed upscaler preserving 90° corners and dithered textures across palette cycling |

- `--inspect-assets`: launch the 50 Hz ModernGL Lair Background, Sprite, and Hitbox Inspector (`--cel kn4 --background fol1.t`)
- `--extract-assets`: unpack ADF assets into `--output-dir` (default `./build/assets`) with `manifest.json`
- `--headless-frames <N>`: run `N` 50 Hz ticks on a standalone OpenGL 3.3 context without opening a window

### Controls

At the title, press Enter to open the menu. Up/Down selects a row; Left/Right on the
player-count row chooses 1–4 humans. Select Start, then choose portraits with Left/Right
and confirm each with Enter. Keyboard slots follow portrait selection order.

| Context | Player 1 keys |
|---|---|
| Move on the map / move a sheet or town pointer | Arrow keys |
| Enter a nearby place or lair / select a pointer region | Enter |
| Choose a row when several map destinations overlap | 1–9 |
| Status on the map | Space or Left Ctrl |
| End the current map turn | E or Tab |
| Fight | Arrows + Space; direction selects the sword move, guard or dagger throw |
| Pause map or combat | P |
| Continue a message or Next Day | Press and release Enter or Space |
| Leave a sheet or town | Move the pointer to its close/Exit region and confirm |
| Quit the campaign to farewell on the map | Q |
| Cycle filter preset (not recorded in replays) | F9 (Shift+F9 backwards) |
| Toggle fullscreen (not recorded in replays) | F11 or Alt+Enter |

Player 2 uses WASD/F, player 3 IJKL/U, and player 4 the numeric keypad/Keypad 0 for
movement/fire. Gamepads use the directional pad or left stick and button 0 for fire.
Practice uses two human knights. All nine creature handlers, loot, shops, services,
place callers and the original ending are implemented; combat requires the original disks.
See [docs/PROGRESS.md](docs/PROGRESS.md) for acceptance evidence and documented deviations.

## Development & Verification

```powershell
uv run python scripts/quality_gate.py    # ruff format/lint, mypy --strict, import-linter, radon, vulture, pytest
uv run pytest -m "not adf and not gl"    # unit tests needing neither disks nor OpenGL
uv run pytest -m adf                     # integration tests against your local ./disks/*.adf
uv run pytest -m gl                      # ModernGL rendering tests
uv run python scripts/verify_campaign.py                              # input-only campaign replay + reviewed checkpoints
uv run python scripts/audit_shots.py --present crt --size 1920x1080  # per-preset presentation contact sheets into build/audit/
```

The campaign command writes `build/campaign/replay.json`, `verification.json` (checkpoint
and final SHA-256 state hashes), and `campaign.png`. It selects four human knights, walks
into a lair, wins with starting equipment, takes loot, buys a potion in Highwood, and advances
the day and moon. It replays the saved inputs in a fresh session and verifies every checkpoint;
rendering must leave those hashes unchanged. This is one integration route, alongside the
separate creature, place, ending and audio tests. Generated game-derived files stay in `build/`.
Use `--adf-dir <path>` or `--out-dir <path>` to change the command's paths.

See [AGENTS.md](AGENTS.md) for contributor/agent rules and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/DECISIONS.md](docs/DECISIONS.md), and [docs/PROGRESS.md](docs/PROGRESS.md) for technical documentation.

## Acknowledgements

HardDaysEngine is a homage to *Moonstone: A Hard Days Knight* and to the people who created it,
above all Rob Anderson, and to Mindscape, who published it in 1991. This project exists out of
admiration for their work and aims to preserve it for players who own the original game.

Thanks also to the authors of the `moonstone-ace` and `openmoonstone` projects, whose research
documented much of the original behaviour.

## License

MIT License — see [LICENSE](LICENSE).

*Moonstone: A Hard Days Knight* is (c) 1991 Mindscape / Rob Anderson. This license covers the `harddaysengine` source code only, not the original game or any data extracted from it. External reference projects (`moonstone-ace` and `openmoonstone`) are not included in this repository and were consulted as specifications only (see [docs/DECISIONS.md](docs/DECISIONS.md), ADR-001).

This is an independent, unofficial fan project. It is not affiliated with, endorsed by, or sponsored by Mindscape, Rob Anderson, or any current rights holder of *Moonstone*. All trademarks belong to their respective owners; the game's title is used only to describe compatibility. You must own the original game to use this software.
