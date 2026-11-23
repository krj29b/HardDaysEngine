# Graphic Update Proposal: Upscaled & Recreated HD Graphics

**Status:** Phase A ACCEPTED & DONE as **Phase 18** (`T18.1`–`T18.3`, **ADR-069**). Phases B + C ACCEPTED as **Phase 19** (`T19.1`–`T19.7`, **ADR-071** in `docs/DECISIONS.md`). Phase D remains an exploratory proposal awaiting a separate future ADR.

## 1. Purpose & Guiding Constraints

This document outlines the technical perspectives, trade-offs, and phased roadmap for adding higher-resolution graphics to **HardDaysEngine** — covering real-time GPU upscalers, user-generated offline AI-upscaled packs, and hand-recreated HD artwork.

Any graphics upgrade must preserve the project's core architectural invariants:

1. **Zero simulation impact (`sim/`):** Game logic, 50 Hz PAL timing (ADR-005), 320×200 integer coordinates, `collide.hit` collision boxes, PRNG draws, and replay state hashes remain completely unchanged. Classic and HD modes must produce identical simulation hashes for the same input stream.
2. **Strict layering (`.importlinter`):** `cli -> app -> render -> assets -> sim`. Pack parsing and image decoding live in `assets/` (headless, no `pygame` or `moderngl`); GPU textures, shaders, and compositing live in `render/` (no `pygame`); configuration and host wiring live in `app/` and `cli/`.
3. **Clean-room & BYOA provenance (ADR-001):** Neither original Amiga assets nor upscaled/derivative copyrighted art may ever be committed to the repository. The engine remains an asset-agnostic runtime loader; users supply their own `.adf` disks and any optional external HD packs.
4. **Render purity (ADR-014):** Rendering must remain read-only with respect to simulation state. Permanent fight stamps (blood, corpses) are applied once per simulation step, never re-applied on redraw.
5. **Performance:** No per-pixel Python loops at runtime; all scaling, compositing, and palette effects run on the GPU or via vectorized NumPy operations.

---

## 2. Current Rendering Baseline

HardDaysEngine already has a clean separation between logical scene data and screen presentation:

- **Logical draw output (`sim/`):** Fights emit ordered `Draw(cel, frame, x, y, mirrored, stamp)` records; scenes emit sprite lists, text calls, and 12-bit `$0RGB` palette words (`sim/palette_fx.py`). No simulation code writes raw framebuffer bytes.
- **CPU indexed composition (`render/fight.py`, `render/scene.py`):** `FightCanvas` and `SceneRenderer` blit `uint8` indexed pixels (`0..31`) onto a `320×256` virtual canvas (with the `320×200` playfield at `y = 28`, ADR-006).
- **GPU palette lookup (`render/renderer.py`, `render/scene.py`):** `SceneRenderer.flush_to_fbo()` uploads the indexed canvas and runs a 32-entry palette LUT shader into `canvas_fbo` (`320×256` RGBA).
- **RGB post-processing (`render/postfx.py`, `render/presentation.py`):** `PostProcessChain` executes ordered offscreen GLSL passes (`PostPassSpec`) after palette lookup. Existing presets already include:
  - `faithful` (nearest/integer/sharp-bilinear)
  - `scanlines` (output-grid scanline darkening)
  - `crt` (separable bloom + aperture/shadow phosphor mask + curvature)
  - `smooth` (4× xBR-style edge-directed upscaling shader, `smooth.frag`)
- **Offline extraction (`harddaysengine-extract`):** Already exports pictures, cels, arenas, and `manifest.json` from the user's own `.adf` disks into `build/assets/`.

Because `sim/` already produces structured draw lists rather than raw pixels, true HD graphics can be introduced entirely at the `assets/` + `render/` boundary.

---

## 3. Architectural Perspectives (Four Tiers)

### Tier 1: Enhanced Real-Time GPU Upscaling Shaders *(Lowest Effort)*

Extend `PostProcessChain` in `src/harddaysengine/render/postfx.py` with higher-grade pixel-art scaling presets alongside `smooth`.

- **Candidates:** ScaleFX (multi-pass edge-resolving scaler), xBRZ 4×/6×, Omniscale, or sharper Jinc / advanced CRT presets.
- **Pros:**
  - Zero external asset authoring and zero legal friction.
  - Works automatically across every screen (intro, fights, overworld, towns, sheets, fonts, endings).
  - Preserves 100% of Amiga palette tricks (fades, class swaps, low-health pulses, water/fire color cycling) because it runs post-LUT.
  - Minimal code footprint: new GLSL shaders under `render/shaders/` + entries in `FilterPreset` and `_PRESET_PASSES`.
- **Limitations:**
  - Cannot invent true high-resolution detail beyond the `320×200` source grid.
  - Fine pixel dithering and small bitmap text (`Small.font`) can appear painterly or softened.

---

### Tier 2: External HD Asset Packs *(Best Long-Term Foundation)*

Add an optional runtime override pack system (e.g., `--hd-pack <dir>`, `HARDDAYSENGINE_HD_PACK_DIR`, or `[display] hd_pack_dir` in `config.toml`) keyed by original asset name and frame index, with **automatic per-asset fallback** to the original Amiga disk asset.

- **Two supported workflows for pack creation:**
  1. **User-generated offline upscaling:** A user runs `harddaysengine-extract` on their own disks, processes the extracted PNGs locally with an offline upscaler (e.g., ESRGAN / Real-ESRGAN pixel-art models, Waifu2x, xBRZ), and points the engine at the output directory.
  2. **Hand-recreated / community remaster packs:** Artists replace arenas, creatures, knights, or UI screens incrementally using the exact same pack format.
- **Why incremental fallback is critical:** Moonstone contains hundreds of animation frames (4 knights, 9 creature families, dragon, dice thrower, arenas, map, towns, sheets, intro, ending). Allowing partial packs lets artists ship an "Arenas HD" or "Troggs HD" pack immediately while unreplaced assets fall back cleanly to the original frames (optionally run through the GPU pixel scaler).

---

### Tier 3: GPU HD Compositor ("Remastered Mode") *(True HD Rendering)*

Today's `FightCanvas` and `SceneRenderer` composite onto a `320×256` `uint8` CPU buffer. To display genuine HD art (e.g., 4× = `1280×800` or 6× = `1920×1200` RGBA with smooth alpha blending), `render/` needs a parallel **GPU HD compositor** fed by the same logical draw commands:

- **Fights:** Render the HD arena backdrop, ordered `Draw` quads, and permanent stamps into an HD RGBA framebuffer (`320*S × 200*S`). Permanent stamps (`draw.stamp == True`) render once per `accept(draws)` call into a retained GPU background FBO, preserving ADR-014 render purity without CPU blitting.
- **Scenes / Map / Sheets:** Upload cached HD textures for pictures, icons, and sprites; draw them in original painter/list order at scaled logical coordinates.
- **Additional capabilities unlocked:**
  - True alpha transparency (anti-aliased outlines, soft ground shadows, translucent magic/mist/gore).
  - Optional crisp vector/TTF typography for menus, sheets, and Next Day screens while preserving exact `320×200` logical click regions.
  - Instant live **Classic ↔ HD hot-toggle** (same `sim/` state underneath).
  - Optional widescreen backdrop art extending past `0..320` horizontally while keeping gameplay bounds locked to the original `320×200` playfield.
- **Built-in artist QA:** The existing hitbox wireframe overlay (`render/overlay.py`) scales directly onto the HD canvas so artists can immediately verify sprite alignment against original `collide.hit` hitboxes and weapon reach.

---

### Tier 4: Real-Time Neural Super-Resolution *(Evaluated — Not Recommended)*

Running live neural upscalers (ONNX / PyTorch / vendor-specific APIs) at 50 Hz is **not recommended** for HardDaysEngine:

- Adds heavy, platform-fragile runtime dependencies to a clean lightweight Python package (`pygame-ce`, `moderngl`, `numpy`, `pillow`).
- Produces temporal shimmering on low-framerate (8–10 fps) pixel-art animation cycles and corrupts 1-pixel outlines and bitmap text.
- Offline upscaling into a Tier 2 HD pack yields higher visual quality, deterministic output, and zero runtime ML overhead.

---

## 4. Moonstone-Specific Technical Challenges & Solutions

Unlike static true-color games, Amiga Moonstone relies heavily on **32-color palette (CLUT) manipulation**. A naive RGBA texture swap would break key visuals. The HD renderer must explicitly handle four cases:

### 4.1 Shared Sprites with Dynamic Palette Swaps
- **Problem:** All four knights share the same cel graphics (`k*.cel`) and differ only by palette entries. Regional arenas (glen, forest, swamp, mountain) and certain creatures/variants also swap palette rows dynamically (`assets/fight_palette.py`).
- **Solutions (all three can coexist in the pack manifest):**
  1. **Indexed HD sprites (`mode = "indexed"`):** High-resolution (`2×`/`4×`) palettized PNGs using original indices `0..31`. Every class color, region tint, hit flash, and VBL palette effect works automatically with zero special cases.
  2. **Variant-keyed RGBA (`mode = "rgba"`):** Override files keyed by `(asset, frame, variant)` — e.g. `knight_red`, `knight_blue`, `arena_swamp` — or by canonical palette hash.
  3. **RGBA + Recolor Mask (`mode = "masked"`):** An RGBA diffuse texture paired with a lightweight mask texture identifying tunic/armor/region pixels so the GPU shader recolors only masked regions from the live Amiga palette.

### 4.2 Live 50 Hz VBL Palette Effects (`sim/palette_fx.py`)
- **Fades to black / white & low-health pulsing:** The renderer knows both the scene's base palette and the current tick's animated palette. For RGBA assets, a shader uniform derived from the per-channel ratio/delta between active and base palettes reproduces fades, flashes, and low-health ramps smoothly without requiring re-authored frames.
- **Palette color cycling (e.g., overworld water/torches):** True-color RGBA images cannot cycle CLUT indices. Packs can either:
  - Keep those regions as indexed-HD (`0..31`), or
  - Supply an optional multi-frame animated backdrop sequence stepped by the existing palette job tick.

### 4.3 Permanent Fight Stamps
- **Problem:** `FightCanvas.accept()` stamps blood and corpses permanently onto the arena background (`render/fight.py`).
- **Solution:** Maintain two GPU FBOs per active fight in HD mode: `hd_bg_fbo` (initialized from the HD arena backdrop) and `hd_frame_fbo`. On each `accept(draws)` call:
  1. Render any `draw.stamp == True` entries into `hd_bg_fbo` (once per sim step).
  2. Blit `hd_bg_fbo` to `hd_frame_fbo`, then render all `draws` in exact emitted order.
  3. `render()` displays `hd_frame_fbo` without mutating either FBO.

### 4.4 Hitbox, Anchor, and Mirroring Fidelity
- Original fight draws apply `shift = 0 if draw.mirrored else frame.meta.hot_x` and place frames at `(s16(draw.x) - shift, s16(draw.y))`.
- Every HD sprite frame of integer scale `S` (or fractional scale declared in pack metadata) maps its logical bounding box `(w, h)` and `hot_x` identically into `320×200` playfield coordinates before scaling to the HD FBO.
- Non-matching aspect ratios or off-anchor art must be rejected or explicitly offset in pack metadata so combat reach and hitboxes remain visually faithful.

---

## 5. Draft HD Pack Layout & Configuration

### 5.1 Configuration & Precedence
Mirror existing `adf_dir` resolution (`app/config.py`):
1. CLI flag: `--hd-pack <path>` (and `--hd-mode off|on`)
2. Environment variable: `HARDDAYSENGINE_HD_PACK_DIR`
3. `config.toml` under `[display]`:
   ```toml
   [display]
   hd_pack_dir = "packs/my-hd-pack"
   hd_mode = "auto" # "off" | "auto" | "on"
   ```

### 5.2 Pack Directory Structure (Draft)
```text
my-hd-pack/
├── pack.toml                 # pack metadata, version, default scale, fallback filter
├── pictures/
│   └── <picture_name>.png    # optional HD backdrop override (or <name>__<variant>.png)
├── cels/
│   └── <cel_name>/
│       ├── 000.png           # frame 0 diffuse (RGBA or indexed 0..31)
│       ├── 000.mask.png      # optional palette-recolor mask
│       └── meta.toml         # optional per-cel mode/variant/anchor overrides
└── fonts/                    # optional future HD font overrides
```

### 5.3 Layering Placement
- `assets/hd_pack.py`: Parse `pack.toml`, validate scale/dimensions/modes, lazily load PNGs via `Pillow` into frozen `HdImage` records (headless, testable without OpenGL or real disks).
- `render/hd_compositor.py`: GPU texture cache (LRU / scene-scoped release), HD fight FBO stamping, palette-fade/recolor-mask shader, and seamless fallback rendering of unreplaced indexed frames into the HD FBO.
- `cli/extract.py` (optional enhancement): Add `--hd-template` flag to `harddaysengine-extract` that exports a ready-to-edit pack skeleton + `pack.toml` + hitbox reference metadata from the user's disks.

---

## 6. Testing & Verification Strategy

Following the existing testing discipline (`AGENTS.md` §3):

- **Headless unit tests (`not adf and not gl`):** Use synthetic in-memory PNG packs in `tests/fixtures/` to verify manifest parsing, malformed-pack errors (`HdPackError`), partial fallback resolution, and layer boundary compliance (`lint-imports`).
- **GL presentation tests (`-m gl`):** Verify HD fight FBO stamping (render purity across repeated redraws), mirroring/anchor alignment against classic `FightCanvas`, palette fade shader math, and clean GPU resource release on pack toggle/shutdown.
- **Determinism invariant test:** Run an identical recorded input replay in Classic mode and HD mode and assert byte-identical simulation state hashes at every tick.
- **Visual audit (`scripts/audit_shots.py`):** Extend the audit harness to optionally emit side-by-side Classic vs. HD contact sheets (with and without hitbox wireframes) into `build/audit/`.

---

## 7. Recommended Phased Roadmap

| Phase | Scope | Effort | Key Value |
|---|---|---|---|
| **Phase A** | Add 1–2 higher-grade GPU pixel-art scalers (e.g., **ScaleFX** / **xBRZ**) to `PostProcessChain` (`render/postfx.py`). | Small | Immediate visual upgrade across all screens with zero art dependencies. |
| **Phase B** | Define `pack.toml` schema, implement headless `assets/hd_pack.py` loader with per-frame fallback, and add `--hd-template` export to `harddaysengine-extract`. | Medium | Enables community & user offline-upscaling workflows cleanly under ADR-001. |
| **Phase C** | Build `render/hd_compositor.py` (GPU HD FBOs for fights & scenes, stamp FBO, fade/recolor-mask shader, Classic↔HD hot-toggle). | Medium–Large | Unlocks true 4×/6× RGBA HD sprites & backdrops with full palette-effect fidelity. |
| **Phase D** | Optional HD font rendering for menus/sheets/Next Day screens and optional widescreen backdrop extensions. | Medium | Full modern-remaster presentation polish. |

---

## 8. Open Decisions (To Resolve in an ADR Before Implementation)

1. **Scope of first milestone:** Start with Phase A (additional GPU upscaling shaders only), or commit directly to Phase B+C (HD asset pack + GPU compositor)?
2. **Palette strategy priority:** Should v1 of HD packs support `indexed` + `rgba` with fade-multiplier first, deferring `.mask.png` class recoloring until an artist/pack needs it?
3. **Fallback upscaling inside mixed scenes:** When an HD pack replaces an arena backdrop (`4×`) but leaves a creature unreplaced (`1×`), should the unreplaced sprite render nearest-neighbor into the HD FBO or pass through the `smooth` xBR shader first?
4. **Canonical asset naming:** Confirm stable, case-normalized asset keys from `harddaysengine-extract`'s `manifest.json` so community packs do not break across engine releases.
