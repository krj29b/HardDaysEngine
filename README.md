# HardDaysEngine

Play *Moonstone: A Hard Days Knight* on Windows with your original Amiga disks.
Choose a knight, explore the overworld, and enter lairs for real-time sword fights. Play solo or
take turns on the map with up to four local players.

**[Download for Windows (x64)](https://github.com/krj29b/HardDaysEngine/releases)** | [Screenshots](#screenshots) | [Controls](#controls) | [Run from source](#run-from-source)

## Screenshots

| Intro cinematic | Overworld map |
| --- | --- |
| ![Druid summoning the Moonstone vortex][shot-intro] | ![Overworld map with castles, towns, and lairs][shot-map] |

| Forest lair (Axe Trogg) | Wasteland lair (Balok) |
| --- | --- |
| ![Knight facing an Axe Trogg in a forest lair][shot-forest] | ![Knight facing Balok in a wasteland lair][shot-wasteland] |

| Swamp lair (Mudman) | Red Dragon |
| --- | --- |
| ![Knight facing Mudman in the swamp][shot-swamp] | ![Red Dragon breathing fire in combat][shot-dragon] |

*Captured at 3× integer scale from original Amiga disks in HardDaysEngine (`faithful` preset). Screenshots are shown solely to illustrate the engine; no game data is included in this repository.*

[shot-intro]: https://github.com/user-attachments/assets/1c32b0ca-9f4d-4f67-88a6-170cf8e3a2c2
[shot-map]: https://github.com/user-attachments/assets/5bdbc9e2-ec8b-47e5-9604-b14a40ccaae9
[shot-forest]: https://github.com/user-attachments/assets/0c8cf402-aad2-47af-bbac-c460d488aa91
[shot-wasteland]: https://github.com/user-attachments/assets/bf55813b-5b9d-45be-a728-b7b7dde61048
[shot-swamp]: https://github.com/user-attachments/assets/ab54d166-6e59-49e2-bfdd-6f4d670e2790
[shot-dragon]: https://github.com/user-attachments/assets/dec771a9-5c02-4596-a686-5ef0544fe2b8

## Play on Windows

You need the three original Amiga `.adf` disk images and an OpenGL 3.3 graphics driver.
The Windows build includes Python and the engine's libraries.

1. Open a release with a Windows build. Under **Assets**, download `HardDaysEngine-Windows-x64.zip`
   for 64-bit Windows or `HardDaysEngine-Windows-x86.zip` for 32-bit Windows if available.
2. Extract the whole ZIP and copy your three disk images into its `disks` folder.
3. Double-click `HardDaysEngine.exe` and press Enter at the title screen.

## Bring Your Own Amiga Disks

This repository contains **no game data**. Place your three original Amiga disk images (901,120-byte `.adf` files, any filenames) into `./disks/`, or specify their location using:

1. `--adf-dir <path>`
2. `HARDDAYSENGINE_ADF_DIR` environment variable
3. `adf_dir = "<path>"` in `config.toml`

Never commit `.adf` images or anything extracted from them.

## Optional Campaign Saves

Add the following to `config.toml` to enable saving and loading:

```toml
[gameplay]
save_load = true
```

Press **F8** in the main menu to load, or on the overworld to save or load. Use **Up/Down**
to choose, **Enter** to select and **Esc** to close the panel. Saves use one slot,
`campaign-save.json`, beside the selected config file. Saving again replaces that slot.
Loading resumes the saved turn. The option defaults to `false`.

## Controls

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
| Fight | Arrows + Space; direction selects the sword move, guard or dagger throw (Down + Space breaks free when grabbed/hung) |
| Pause map or combat | P |
| Continue a message or Next Day | Press and release Enter or Space |
| Leave a sheet or town | Move the pointer to its close/Exit region and confirm |
| Quit the campaign to farewell on the map | Q |
| Cycle filter preset (not recorded in replays) | F9 (Shift+F9 backwards) |
| Open live display preview | F10 |
| Toggle fullscreen (not recorded in replays) | F11 or Alt+Enter |

Player 2 uses WASD/F, player 3 IJKL/U, and player 4 the numeric keypad/Keypad 0 for
movement/fire. Gamepads use the directional pad or left stick and button 0 for fire.
Practice uses two human knights. All nine creature handlers, loot, shops, services,
place callers and the original ending are implemented; combat requires the original disks.
See [docs/PROGRESS.md](docs/PROGRESS.md) for acceptance evidence and documented deviations.

## Run from Source

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

Press **F10** while playing to open the live display preview. Use **Up/Down** to choose a setting
and **Left/Right** to change it. Select `crt` to adjust scanlines, mask pattern and strength,
curvature, and bloom. **Enter** applies the settings for the current session. **Esc** or **F10**
discards the preview. **F11** still toggles fullscreen.

Presentation presets (`--filter` or `F9` / `Shift+F9` at runtime; all presets run strictly after the palette lookup and never affect simulation or replay hashes):

| Preset | Look |
|---|---|
| `faithful` *(default)* | Even, crisp pixels at the selected aspect and scale mode with no effects |
| `scanlines` | Crisp pixels with output-grid scanlines (no non-integer moiré banding) |
| `crt` | Linear-light intensity-widening scanline beams, aperture/shadow phosphor mask, optional barrel curvature + rounded bezel, and separable bloom |
| `smooth` | 4× xBR-style edge-directed upscaler preserving 90° corners and dithered textures across palette cycling |

- `--inspect-assets`: launch the 50 Hz ModernGL Lair Background, Sprite, and Hitbox Inspector (`--cel kn4 --background fol1.t`)
- `--extract-assets`: unpack ADF assets into `--output-dir` (default `./build/assets`) with `manifest.json`
- `--inspect-assets --headless-frames <N>` renders `N` inspector frames offscreen using OpenGL 3.3. Add `--screenshot <file.png>` to save the final frame.
- `--headless-ticks <N>` runs `N` gameplay simulation ticks without opening a window and prints the final scene and state hash.

## Architecture

HardDaysEngine is a clean-room reimplementation of the 1991 Amiga game. It runs a deterministic 50 Hz simulation in Python 3.12+ and uses ModernGL and `pygame-ce` for presentation. The codebase is organized into strict unidirectional layers (`cli -> app -> render -> assets -> sim`):

- **Simulation (`src/harddaysengine/sim/`)**: deterministic 50 Hz integer/fixed-point kernel (persistent combat jobs and handlers, pixel map, turn scheduler, sheets and places, palette FX, PRNG). No floats, no wall clock, no `random`, no I/O.
- **Assets (`src/harddaysengine/assets/`)**: reads original Amiga `.adf` disk images (OFS) at runtime, unpacks RNC1/LZSS streams, and decodes pictures, cels, `collide.hit` attack sets, `.t` arena layouts, `program`/`mog` hunk tables, and audio banks.
- **Render (`src/harddaysengine/render/`)**: ModernGL (OpenGL 3.3 core) `320x256` virtual canvas, indexed palette textures, optional CRT post-process, and debug hitbox overlay.
- **App & CLI (`src/harddaysengine/app/`, `src/harddaysengine/cli/`)**: `pygame-ce` window host, 50 Hz fixed-timestep loop, keyboard/gamepad input, audio mixer, and CLI entry points.

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
