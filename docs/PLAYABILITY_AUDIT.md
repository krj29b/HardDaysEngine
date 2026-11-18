# Playability Audit v2 & Remediation Plan (`harddaysengine`)

**Date:** 2026-11-15  
**Target Commit:** `0db1146` (`main`, post-Remediation Pass 1)  
**Environment:** Headless & live ModernGL 3.3 + `pygame-ce` 2.5.8 against the original 3 Amiga ADF disk images (`disks/Moonstone_Disk_A.adf`, `Moonstone_Disk_B.adf`, `Moonstone_Disk_C.adf`).

---

## 1. Executive Summary

Following the first remediation pass (`1873c05..0db1146`), several presentation and input issues from Audit v1 are demonstrably improved:
- **Arena `.t` Backdrop Compositing (`src/harddaysengine/assets/arena.py`):** Forest (`fo1.t`), Glen (`gl1.t`), Swamp (`sw1.t`), and Wasteland (`wa1.t`) arenas now dispatch `0x0300` foreground/object tiles to `test[0]`/`test[2]`/`test[3]` and base tiles to `test[1]` with signed `dest_x`/`dest_y` clipping, eliminating the black gaps and grass-block corruption seen in Audit v1.
- **Main Town Menu Layout (`src/harddaysengine/app/location_screens.py`):** Main town screens (`WaterDeep.PIV` on the left scroll, `HighWood.PIV` on the right scroll) now use the embedded calligraphy artwork labels and align a hollow selection rectangle over the parchment scroll instead of drawing duplicate white ASCII text over the scroll.
- **Intro Skip & Edge-Triggered Navigation (`src/harddaysengine/app/game.py`):** `SPACE` / `RETURN` / `ESCAPE` skips the intro cleanly, and single directional/confirm taps on the overworld and menus are edge-triggered.
- **Audio Sample Extraction (`src/harddaysengine/app/scene_assets.py`):** Embedded IFF `8SVX` chunks inside `.a` sound banks now register 59 named sample keys at `16,574 Hz`.

**Why the game is STILL unplayable at `0db1146`:**
An empirical frame-by-frame audit across all game states (`MAIN_MENU`, `KNIGHT_SELECT`, `OVERWORLD`, `COMBAT`, `LOCATION`, `STATUS`, `NEW_DAY`) revealed **8 root-cause defects** (5 Critical, 2 High, 1 Medium):

1. **CRITICAL — `mog` Fight Script Execution Always Fails (`_run_mog_action_draws` returns `()`):**
   - `parse_combat_action_tables` (`src/harddaysengine/assets/tables.py:248-255`) reads `kActionRight` and `kActionLeft` at `S_4[4130]` and `S_4[4152]`. Those tables do **not** contain byte offsets of fight scripts in `S_4`; they contain 11 joystick-direction-to-action-index words (`0, 4, 8, 12, 16, 20, 24, 28, 32` — byte offsets into an 9-entry `BE32` script-pointer table `ulActionScripts` at `Knight+34`).
   - Passing `28` (`0x1C`) directly as a script PC in `S_4` reads `S_4` header data (`00 6c 00 02...`) instead of the knight fight scripts (`S_4[4198..]`), returning `()` on every action.
   - Even when pointed at the real `mog` `S_4` scripts, `src/harddaysengine/sim/combat_vm.py` implements **fictitious opcodes** (`0x80` end, `0x81` jump, `0x82` free, `0xFF` 1-byte end-of-frame, raw `cel_slot = op`) instead of the real `mog` fight-script bytecode (`[slot*4][frame][dy:s8][flags][dx:s16]`, 2-byte `$FF $00` frame marker / `$FF $FF` script-end marker, `$80` facing, `$84` jump, `$88` loop1, `$8C` motion block, `$94` loop2, `$98` gore jump, `$A0` move, `$A4` sound, `$B0` engine call, `$B4` ifdead, `$BC` kill, `$C0` frame set, `$D0` reset work, `$FD`/`$FE` loop return).

2. **CRITICAL — Knight & Monster Sprites Render as Single Disembodied Limb/Torso Cels:**
   - In Moonstone, both knights (`KN1.ob`..`KN4.ob`) and monsters (`TROGGSpear1.CEL`..`TROGGSpear2.CEL`, etc.) are **multi-part layered sprites** assembled each tick by executing multiple 6-byte draw instructions (`[slot*4][frame][dy:s8][flags][dx:s16]`) before `$FF $00`.
   - Because `_run_mog_action_draws` returns `()`, `compose_knight_pieces` falls back to `select_knight_body_frame`, which draws a single raw frame from `KN1.ob`:
     - `WALK` at `t=8` draws `KN1.ob[4]` (`22×44` **disembodied legs** with no torso or head).
     - `RUN` at `t=8` and `ROLL` draw `KN1.ob[5]` (`36×19` **detached upper torso** with no legs).
     - `BLOCK` draws `KN1.ob[7]` (`27×27` **detached upper body** with no legs).
     - `OVERHEAD_SLASH`, `THRUST`, and `LOW_SWEEP` at `t=0..5` all draw `KN1.ob[0]` (the static idle stance!).
   - Likewise, `select_monster_frame` (`src/harddaysengine/app/scene_assets.py:421-427`) cycles raw single frame indices `(offset + (tick // 6) % 4)` in `TROGGSpear1.CEL`, rendering **disembodied Trogg legs/loincloth** with no upper body, head, or spear.

3. **CRITICAL — Player Attacks Still Land 0 Hits (`landed_ticks = 0`) & Monsters Stunlock the Player:**
   - In `resolve_composed_melee_attack` (`src/harddaysengine/app/combat_bridge.py:553-593`), fallback reach (`fallback_reach_x`) is used **only** when `not attacks or not defender_Piece_list`.
   - When the knight swings (`OVERHEAD_SLASH`, `THRUST`, `LOW_SWEEP`), fallback `select_knight_body_frame` picks `KN1.ob[0]` (`23×54`) or `KN1.ob[2]` (`21×55`) and attaches `kn4.hit[0]` (`max_x=4, max_y=29`, points at `x=4` inside the knight's own torso) or `kn4.hit[2]` (`x=5..15`).
   - Because `attacks` is non-empty, `resolve_composed_melee_attack` skips `fallback_reach_x` and runs `contact_test` using points at `x=4..15` inside the knight's own body!
   - Meanwhile, the Trogg's melee reach (`66 px`) hits the knight from `x=206 -> 140` and `x=176 -> 140`, putting the knight into `HIT_STUN` before the Trogg ever enters the knight's body box. Over a 40-tick attack test at point-blank combat range, **0 player attacks landed**.

4. **CRITICAL — Sprite Coordinate / Anchor Math Centers Cels (`-w//2, -h//2`) Instead of Top-Left `(X+dx, Y+Z+dy)`:**
   - Both `cel_screen_origin` (`src/harddaysengine/app/combat_bridge.py:380-385`) and `HardDaysRenderer._draw_sprite` (`src/harddaysengine/render/renderer.py:262-263`) subtract `w // 2` and `h // 2` from `(x, y)` (when `hot_x == 0`), treating `(x, y)` as the center of every cel frame.
   - In the original Amiga engine (`combat_script.cpp:249-258`), cel frames have no per-frame hotspot in their header (`byte8` is the runtime mirror state bit `0x01`, not `hot_x`!). Every draw command places the frame's **top-left corner** at:
     - `screen_x = X + dx` when facing right, or `screen_x = X - dx - width` when mirrored (facing left).
     - `screen_y = Y + Z + dy` (where `dy` is a negative signed byte placing the cel above the ground baseline `Z`).
   - Centering by `-w//2, -h//2` causes multi-part legs, torsos, and sword blades to float at incompatible heights and misaligns all `collide.hit` coordinates.

5. **CRITICAL — Overworld Still Has Only 13 Nodes and `moves_per_turn = 1` on Real Disks:**
   - `parse_map_sites` (`src/harddaysengine/assets/tables.py:299-309`) reads `MOG_MAP_SITES_COUNT = 10` records from `mog` `S_2[100]`, hitting terminator `0xFFFF` at index 9 (`9` sites: IDs `21..28, 30`).
   - In `build_overworld_graph_from_sites` (`src/harddaysengine/app/scene_assets.py:239-277`), `moves_per_turn` is set to `3 if len(sites) >= 20 else 1`.
   - Because `len(sites)` is always `9` on the real `mog` executable, `build_overworld_graph()` on real disks **still produces a 13-node graph with `moves_per_turn = 1`**. Moving a single step (`Node 22 -> Node 30`) immediately exhausts `moves_remaining`, increments `day` to `2`, and transitions to `NEW_DAY`.

6. **HIGH — Combat & Overworld Knight Palette Colors Are Wrong:**
   - Selecting **Sir Richard (Blue Knight)** in `KNIGHT_SELECT` still renders a **Red Knight** in combat (`08_combat_walk_t15_WALK.png`) and **magenta markers** on the overworld (`04_overworld_initial.png`).
   - In combat, `KN1.ob`..`KN4.ob` use palette indices `6, 7, 8` for the knight's tunic, and monster cels use indices `9..15`. `game.py:707` calls `assemble_combat_palette(default_combat_palette(), bg_pic.palette)` without patching indices `6..8` for the active knight's class color (Blue/Red/Green/Yellow) or indices `9..15` for the creature's scene palette row (`sceneColors`).

7. **HIGH — Town Sub-Locations (`MERCHANT`, `TAVERN`, `HEALER`, `MYSTIC`) Use Wrong Backdrop & Draw Unreadable White Text Over White Castle Spires:**
   - Entering Waterdeep (`WaterDeep.PIV`, scroll on left) and selecting `Merchant` switches `sub_location` to `"MERCHANT"`.
   - `_backdrop_candidates` (`src/harddaysengine/app/location_screens.py:92-104`) falls back to `HighWood.PIV` (which has its Town menu scroll on the right and a bright white castle tower on the left) and draws white ASCII item prices (`+ SWORD UPGRADE 100G`, etc.) at `x = 18` directly on top of the white castle artwork where they are nearly invisible, while the unused `"Visit / Merchant / Tavern / Healer / High Temple / Exit"` scroll remains displayed on the right.

8. **MEDIUM — `_pending_fade_in` Starts in `render_to_scene` Instead of on Scene Entry:**
   - In `src/harddaysengine/app/game.py:1355-1357`, `_pending_fade_in` triggers `self.palette_fx.fade_in(..., from_black=True)` inside `_apply_scene_palette` during `render_to_scene()`. Any simulation ticks stepped before the first render call do not advance the fade, causing the first rendered frame of every fade-in scene (`COMBAT`, `OVERWORLD`, `MAIN_MENU`) to be 100% pitch black (`RGB = (0, 0, 0)`).

---

## 2. Detailed Technical Root Causes

### 2.1 Why `_run_mog_action_draws` Returns `()` and How `mog` Fight Scripts Actually Work

#### Defect 1A: `kActionRight` / `kActionLeft` (`S_4[4130]`, `S_4[4152]`) Are Action-Table Byte Offsets, Not Script Pointers
On the real `mog` executable (`S_4`, `39,320` bytes):
```text
CombatActionTables(
    right_offsets=(0, 8, 20, 0, 28, 4, 16, 0, 32, 24, 12),
    left_offsets =(0, 20, 8, 0, 28, 16, 4, 0, 32, 12, 24),
)
```
In the original 68k engine:
- `kActionRight` and `kActionLeft` translate an 11-entry joystick direction lookup into an action code `uwAction` (`0, 4, 8, 12, 16, 20, 24, 28, 32`, i.e., `index * 4` for indices `0..8`).
- The knight's `Knight` struct stores a pointer to its 9-entry `BE32` action script table at `+34` (`ulActionScripts`), walk script table at `+46` (`ulWalkScripts`), hurt script table at `+30` (`ulHurtScripts`), and idle script at `+22` (`ulIdleScript`).
- Passing `right_offsets[slot]` (`28`) as the bytecode PC in `S_4` reads `S_4[28:52]` (`00 6c 00 02 00 00 00 24...`), which is unrelated table data near the start of `S_4`.

#### Defect 1B: `combat_vm.py` Implements Invented Opcodes Instead of `mog` Fight-Script Bytecode
Comparing `src/harddaysengine/sim/combat_vm.py` against the actual `mog` `S_4` bytecode (`S_4[4198..]`, documented in `MONSTER_KIT.md` §1.2):

| Feature | Current `combat_vm.py` (Broken) | Actual `mog` `S_4` Bytecode |
|---|---|---|
| Draw opcode byte 0 | `cel_slot = script[pc]` (`0..31`) | `slot = (script[pc] & 0x1F) >> 2` (encoded as `slot * 4`: `$00, $04, $08, $0C, $10`) |
| Frame tick marker | `$FF` (1 byte: `pc += 1`) | **2 bytes:** `$FF $00` (`pc += 2`, yield tick), `$FF $FF` (yield tick AND end script), `$FF $FE` (yield tick AND loop2 step) |
| Set facing | `0x94` | `$80 <facing:u8>` (2 bytes: `1`=right, `3`=left, `$FF`=toggle) |
| Jump / arm next | `0x81 <u32>` (5 bytes) | `$84 <mode:u8> <target:BE32>` (6 bytes: `mode == 3` jumps immediately, else arms continuation at next `$FF`) |
| Loop 1 | `0x83 <u8>` / `0x84` | `$88 <count:u8>` (2 bytes, `0` = random `1..31`) closed by 1-byte `$FD` |
| Motion block | Not implemented | `$8C <param, count, flags, vy, vylim, vx, vxlim>` (8 bytes) |
| Loop 2 | Not implemented | `$94 <count:u8>` (2 bytes) closed by 1-byte `$FE` or 2-byte `$FF $FE` |
| Gore conditional jump | Not implemented | `$98 <pad:u8> <target:BE32>` (6 bytes: jumps when Gore is off) |
| Move `(dx, dy, dz)` | `0x8A <s16> <s16>` (5 bytes) | `$A0 <pad:u8> <dx:s16> <dy:s16> <dz:s16>` (8 bytes) |
| Play sound | `0x8E <u16>` (3 bytes) | `$A4 <sound_id:u8>` (2 bytes) |
| Poke owner record | Not implemented | `$A8 <pad:u8> <offset:u16> <value:BE32>` (8 bytes) |
| Second script | Not implemented | `$AC <pad:u8> <target:BE32>` (6 bytes) |
| Engine routine | Not implemented | `$B0 <pad:u8> <routine_id:BE32>` (6 bytes, e.g. `LAB_0005` `fightCreatureDied`) |
| Jump if HP <= 0 | Not implemented | `$B4 <pad:u8> <target:BE32>` (6 bytes, `ifdead`) |
| Spawn object | `0x90` | `$B8 <pad:u8> <target:BE32>` (6 bytes) |
| Kill job | `0x82` (1 byte) | `$BC <pad:u8>` (2 bytes) |
| Select frame set | Not implemented | `$C0 <set_id:u8>` (2 bytes: `1`=knights `KN1..KN4`, `2`=creature `0..4`, `3`=map, `4`=effects) |
| Facing / field branches | Not implemented | `$C4` (6 bytes), `$C8` (8 bytes), `$CC` (8 bytes) |
| Reset work block | Not implemented | `$D0 <pad:u8>` (2 bytes) |

Because real `mog` scripts begin with opcodes like `$D0 $00` (`reset work`), `$88 $02` (`loop 2`), and end every frame with `$FF $00` or `$FF $FF`, `combat_vm.py` either raises `ScriptError` or misinterprets the second byte of `$FF $00` / `$FF $FF` on the next tick.

---

### 2.2 Multi-Part Cel Layering & Coordinate Anchoring (`KN1..KN4` and Monster Cels)

In `mog`, a single frame of animation is composed of **multiple 6-byte draw commands** executed sequentially before `$FF`:
- Example from `mog` `S_4` at offset `4198` (`0x1066`, knight idle/stance script):
  1. `00 01 2d 01 ff f3`: slot `0` (`KN1.ob`), frame `1` (legs), `dy = +45` (or signed offset), `flags = 0x01` (hurtbox), `dx = -13`
  2. `00 00 f7 01 ff f7`: slot `0` (`KN1.ob`), frame `0` (torso), `dy = -9`, `flags = 0x01` (hurtbox), `dx = -9`
  3. `0c 01 fa 01 00 04`: slot `3` (`0x0c >> 2 = 3`, `KN4.ob`), frame `1` (sword/head), `dy = -6`, `flags = 0x01`, `dx = +4`
  4. `ff ff`: end of frame & script.
- In `HardDaysRenderer._draw_sprite` (`src/harddaysengine/render/renderer.py:258-266`):
  ```python
  hot_x = meta.hot_x if meta.hot_x != 0 else w // 2
  hot_y = h // 2
  x0 = sprite.x - (w - hot_x if sprite.flip_x else hot_x)
  y0 = sprite.y - hot_y
  ```
  Wait: `meta.hot_x` comes from `CelFrameMeta.hot_and_flags` (`rec[8]` of the 10-byte cel frame header), which in Moonstone is **always `0x01`** (the runtime unflipped state bit, `MONSTER_KIT.md` §1.1: *"byte8 is runtime state... The converter always writes $01"*)!
  Because `meta.hot_x == 1` (`!= 0`), `HardDaysRenderer._draw_sprite` uses `hot_x = 1` and `hot_y = h // 2`, while `combat_bridge.py:381` uses `hot_x = meta.hot_x if meta.hot_x != 0 else w // 2` (`1`) and `hot_y = h // 2`!
  - Neither matches the Amiga hardware blitter! In the Amiga engine, `rec[8]` is **not** a hotspot offset; every cel is drawn with its **top-left corner** at `(screen_x, screen_y)` where:
    - When facing right (`not flipped`): `screen_x = actor_x + dx`
    - When facing left (`flipped`): `screen_x = actor_x - dx - frame_width`
    - Vertically: `screen_y = actor_z - actor_altitude + dy`

---

### 2.3 Why Player Attacks Never Hit (`combat_bridge.py` & `collide.hit`)

In `B/collide.hit`, `kn4` has 82 frame records corresponding to the 82 frames of `KN4.ob` (the knight's sword/weapon overlay cel table, slot `3` / opcode `0x0C`)!
- In `combat_bridge.py:482-514`:
  - When `_run_mog_action_draws` fails, `compose_knight_pieces` falls back to `select_knight_body_frame`, which selects frames `0, 2, 10` from **`KN1.ob` (slot `0`, the body cel)**, and then erroneously looks up `kn4.hit[frame_idx]` (which belongs to **`KN4.ob`**, not `KN1.ob`!) and attaches `kn4.hit[0]` to `KN1.ob[0]`.
  - Worse still, `kn4.hit` only defines attack points for `KN4.ob` weapon frames (`slot == 3`). Attaching `kn4.hit` to `KN1.ob` (`slot == 0`) tests the wrong cel's points at the wrong screen offset.
  - Because `attacks` is non-empty (`kn4.hit[0]` has 2 points at `(4, 29)`), `resolve_composed_melee_attack` (`combat_bridge.py:588`) does not use `fallback_reach_x`, tests `(4, 29)` inside the knight's own chest, and **misses 100% of attacks**.

---

### 2.4 Why Overworld Movement Ends the Day After 1 Hop (`tables.py` & `scene_assets.py`)

1. `parse_map_sites` (`src/harddaysengine/assets/tables.py:299-309`) only reads 9 static landmark sites (`site_id = 21..28, 30`) from `mog` `S_2[100]`, plus 4 regional lairs (`0..3`) = 13 nodes total.
2. In `src/harddaysengine/app/scene_assets.py:259`:
   ```python
   moves_per_turn = 3 if len(sites) >= 20 else 1
   ```
   Because `len(sites)` is `9` on the real disk, `moves_per_turn` is hardcoded to `1`!
3. Even if road waypoints are synthesized or loaded from the map connectivity table, a knight on the real disk must have `moves_per_turn = 3` (or road-step movement where road steps between landmarks do not immediately end the turn on the first key press), and intermediate road nodes must be populated when `len(sites) == 9` so the 35-node graph (`overworld.py`) is used on real disks too, not only in synthetic unit tests.

---

### 2.5 Combat & Overworld Knight Palette Indices (`game.py` & `arena.py`)

Per `MONSTER_KIT.md` §1.1 (`display_fx.cpp:142-243`):
- **Combat Palette (`32` colors):**
  - Index `0`: forced black (`(0, 0, 0)`), transparent for sprites.
  - Indices `1..5`: from arena backdrop picture (`test[4..7]`).
  - Indices `6..8`: **first fighter's tunic colors by knight class** (Blue, Red, Green, Yellow). Currently `default_combat_palette()` hardcodes red into indices `6..8`, so every knight looks red in combat regardless of who was chosen in `KNIGHT_SELECT`.
  - Indices `9..15`: **creature colors per scene code** (`sceneColors` in `mog`, with index `15` forced to blood red `(238, 0, 0)` outside the dragon scene).
  - Indices `16..31`: from arena backdrop picture (`test[4..7]`).

---

### 2.6 Town Sub-Location Backdrops & Text Placement (`location_screens.py`)

When entering a sub-location (`MERCHANT`, `TAVERN`, `HEALER`, `MYSTIC`, `TEMPLE`, `SAGE`):
- `_backdrop_candidates` (`src/harddaysengine/app/location_screens.py:92-104`) falls back to `("HighWood.PIV", "WaterDeep.PIV")` when `"SHOP.PIV"` / `"TAVERN.PIV"` do not exist on disk (no such `.PIV` files exist on Disks A/B/C).
- Falling back to `HighWood.PIV` while inside `WaterDeep` switches the background from Waterdeep to Highwood and draws white text at `x = 18, y = 44..128` directly on top of Highwood's bright white castle tower, while leaving Highwood's main menu scroll (`Visit / Merchant / Tavern / Healer / High Temple / Exit`) visible on the right!
- **Fix:** Sub-locations inside a town should either:
  1. Keep the current town's own `.PIV` backdrop (`WaterDeep.PIV` for Waterdeep, `HighWood.PIV` for Highwood) and draw a dark/parchment modal panel (or opaque shadow-backed menu box) for the shop/service items, **or**
  2. Render a dedicated dark/framed shop overlay panel so white/gold bitmap font text has guaranteed high contrast and never collides with bright background pixels.

---

## 3. Prioritized Remediation Plan v2

### Phase 1: Real `mog` Fight-Script Interpreter & Multi-Part Sprite Composition (CRITICAL — P0)
**Files:** `src/harddaysengine/sim/combat_vm.py`, `src/harddaysengine/assets/tables.py`, `src/harddaysengine/app/combat_bridge.py`, `src/harddaysengine/app/scene_assets.py`, `docs/ARCHITECTURE.md`
1. **Update `docs/ARCHITECTURE.md` §7.2** to match the verified `mog` fight-script bytecode specification:
   - Draw instruction `0x00..0x7F` (6 bytes): `slot = (op & 0x1F) >> 2`, `frame`, `dy:s8`, `flags:u8`, `dx:s16`.
   - 2-byte frame boundary markers: `$FF $00` (`pc += 2`, end frame), `$FF $FF` (end frame & end script), `$FF $FE` (end frame & loop2 step).
   - Control opcodes: `$80` (2B facing), `$84` (6B jump/arm), `$88` (2B loop1), `$8C` (8B motion block), `$94` (2B loop2), `$98` (6B gore jump), `$A0` (8B move), `$A4` (2B sound), `$A8` (8B poke), `$AC` (6B second script), `$B0` (6B engine call), `$B4` (6B ifdead), `$B8` (6B spawn), `$BC` (2B kill), `$C0` (2B frame set), `$C4` (6B facing branch), `$C8`/`$CC` (8B field branch), `$D0` (2B reset work), `$FD` (1B loop1 end), `$FE` (1B loop2 end).
2. **Fix `src/harddaysengine/sim/combat_vm.py`** to decode the real `mog` bytecode opcodes and `slot = (op & 0x1F) >> 2`, while keeping backward compatibility if needed for `program` `S_2` intro scripts.
3. **Fix `src/harddaysengine/assets/tables.py` & `src/harddaysengine/app/combat_bridge.py`** to extract the actual knight & creature script pointer tables from `mog` `S_4` (idle script, walk scripts, 9 action scripts, 9 hurt scripts) instead of treating `kActionRight` / `kActionLeft` (`0..32`) as script offsets.
4. **Fix Monster Multi-Part Rendering (`src/harddaysengine/app/scene_assets.py` & `src/harddaysengine/app/game.py`):**
   - Load all cel slots for the active monster (`TROGGSpear1.CEL` + `TROGGSpear2.CEL`, etc.) and compose all layered cel pieces for each animation frame (or run the creature's `mog` idle/walk/attack/hurt script draws) so monsters render as complete bodies with weapons instead of disembodied legs.

### Phase 2: Sprite Top-Left Anchor Math & Melee Hit Detection (CRITICAL — P0)
**Files:** `src/harddaysengine/render/renderer.py`, `src/harddaysengine/app/combat_bridge.py`, `src/harddaysengine/app/game.py`
1. **Fix Top-Left Sprite Positioning:**
   - Stop treating `CelFrameMeta.hot_and_flags` (`0x01`) as `hot_x = 1` or centering combat cels by `-w // 2, -h // 2`.
   - For fight-script draws, place each cel's top-left corner at:
     - `screen_x = actor_x + dx` (facing right) or `screen_x = actor_x - dx - width` (facing left / mirrored).
     - `screen_y = actor_z - actor_altitude + dy`.
2. **Fix `collide.hit` Slot Matching & Melee Hit Resolution (`combat_bridge.py`):**
   - Attach `kn4.hit[frame_index]` **only** to `KN4.ob` (`cel_slot == 3`) draw pieces flagged with `DRAW_FLAG_ATTACK` (`flags & 0x02`), never to `KN1.ob` (`cel_slot == 0`) body pieces.
   - Ensure `resolve_composed_melee_attack` checks `contact_test` on the actual weapon attack pieces and falls back to `fallback_reach_x` whenever no weapon attack piece with hit points was produced or when attack boxes overlap within reach.
   - Verify with an automated test on real disks (`@pytest.mark.adf`) that a player knight walking up to a Trogg/Ratman and thrusting/slashing lands hits and kills the monster within a normal fight.

### Phase 3: Overworld Graph Road Nodes & `moves_per_turn = 3` on Real Disks (CRITICAL — P0)
**Files:** `src/harddaysengine/app/scene_assets.py`, `src/harddaysengine/sim/overworld.py`
1. In `build_overworld_graph_from_sites` (`src/harddaysengine/app/scene_assets.py:239-277`), when `mog` `S_2` provides the 9 landmark sites (`21..28, 30`) and 4 lairs (`0..3`), populate the connecting `ROAD` nodes (the 35-node canonical Moonstone road network) and set `moves_per_turn = 3` so real-disk gameplay matches the 35-node overworld topology and gives each knight 3 moves per day.

### Phase 4: Knight Class Palette Colors, Sub-Location Readability, & Scene Entry Fade-In (HIGH/MEDIUM — P1)
**Files:** `src/harddaysengine/app/game.py`, `src/harddaysengine/assets/arena.py`, `src/harddaysengine/app/location_screens.py`
1. **Combat & Overworld Knight Palette Colors:**
   - Patch combat palette indices `6..8` with the active knight's class ramp (`KnightId.BLUE`, `RED`, `GREEN`, `YELLOW`) and indices `9..15` with the creature's scene palette colors (`index 15` = blood red).
   - Ensure overworld knight/flag sprites (`mi.c`) use the correct frame index and palette colors for each knight.
2. **Town Sub-Location Contrast & Backdrop (`location_screens.py`):**
   - Keep the current town's backdrop (`WaterDeep.PIV` in Waterdeep, `HighWood.PIV` in Highwood) when entering `MERCHANT`/`TAVERN`/`HEALER` instead of switching Waterdeep's merchant to `HighWood.PIV`.
   - Render a dark filled panel (`draw_rect(..., filled=True)`) behind sub-location menu text so white/gold bitmap font text is 100% legible over any background art.
3. **Scene Entry Fade-In Timing (`game.py`):**
   - Trigger `palette_fx.fade_in` immediately when transitioning scenes (or start fade at step `1/N`) so the first rendered frame is not completely black when stepping ticks before `render_to_scene()`.

---

## 4. Verification Criteria for Remediation Pass 2

Before closing Remediation Pass 2, verify all of the following on real ADF disks (`disks/`):
1. **Multi-Part Sprite Verification:** Every `KnightAction` (`IDLE`, `WALK`, `RUN`, `ROLL`, `BLOCK`, `OVERHEAD_SLASH`, `THRUST`, `LOW_SWEEP`, `HIT_STUN`, `KNOCKDOWN`, `DEATH`) and every monster type renders a complete multi-part body (legs + torso + head/weapon) grounded on the same baseline `Z`, never a single disembodied leg or torso cel.
2. **Combat Winnability Verification:** Automated combat simulation on real disks confirms `landed_ticks > 0` and `monster.hp` drops from `24 -> 0` when the player knight attacks an approaching monster.
3. **Overworld Multi-Step Turn Verification:** Starting a game on real disks builds the full road + site graph (`>= 31` nodes) with `moves_per_turn = 3`; moving 1 step decrements `moves_remaining` from `3 -> 2` and stays on `GameScene.OVERWORLD` (does not jump to `NEW_DAY` after 1 move).
4. **Visual & Quality Gate Verification:**
   - Blue Knight renders in blue tunic in combat.
   - Waterdeep sub-menus (`MERCHANT`, `TAVERN`, `HEALER`) render legible text over a dark backing panel without switching to Highwood's backdrop.

---

## 5. Remediation Pass 2 Verification Results (`T10.1`–`T10.9`)

Verified across [tests/unit/test_playability_v2.py](file:///c:/_WORKSPACE/ANTIGRAVITY/harddaysengine/tests/unit/test_playability_v2.py), [tests/unit/test_combat_bridge.py](file:///c:/_WORKSPACE/ANTIGRAVITY/harddaysengine/tests/unit/test_combat_bridge.py), [tests/unit/test_tables.py](file:///c:/_WORKSPACE/ANTIGRAVITY/harddaysengine/tests/unit/test_tables.py), and real `.adf` disks:
1. **Multi-Part Sprite Verification (`T10.1`–`T10.2`):** All 12 `KnightAction`s execute real `mog` `S_4` fight scripts (`KnightScriptTables`, `KN1.ob..KN4.ob`) and emit multi-part draw lists; `compose_knight_pieces` also provides a multi-slot (`KN1`+`KN4`) fallback when `mog_s4` is absent; all 10 `MonsterArchetype`s compose multi-part pieces across multi-slot cels (`TROGGSpear1+2`, `Balok1..3`, `Demon1..4`, etc.).
2. **Top-Left Anchor & Combat Winnability (`T10.3`–`T10.4`):** `SceneSprite.top_left=True` anchors fight-script pieces at `(pos.x + dx, pos.y - pos.z + dy)`, `kn4` `collide.hit` attaches only to `cel_slot == 3` (`KN4.ob`) attack pieces, `params_from_creature_node` keeps `base.defense`, and `test_adf_knight_attacks_and_defeats_trogg` defeats a real Trogg (`trogg.hp == 0`).
3. **Overworld Multi-Step Turns (`T10.5`):** `test_adf_overworld_3_moves_and_stay_in_overworld` confirms 35 nodes on real disks with `moves_remaining = 3 -> 2` on first step while staying in `GameScene.OVERWORLD`.
4. **Palettes, Sub-Locations, Fade-In, & Quality Gate (`T10.6`–`T10.9`):**
   - `test_adf_blue_knight_combat_palette_and_fade_in` confirms non-black frame 1 and blue tunic colors at palette indices `6..8`.
   - `test_overworld_marker_palette_replaces_magenta_index_31` and `test_adf_overworld_marker_frames_and_highlight_palette` confirm `test[8]` index `31` uses a grayscale highlight instead of `(255, 17, 255)` magenta and `mi.c` uses class-specific frames (`5..8` / `15..18`).
   - Waterdeep sub-locations preserve `WaterDeep.PIV` with dark panel contrast.
   - `uv run python scripts/quality_gate.py`: **7/7 gates PASSED** (`238 passed, 8 deselected`, `958` blocks graded A/B).
   - `uv run pytest -m adf`: **15 passed**; `uv run pytest -m gl`: **8 passed**.
   - `uv run python scripts/quality_gate.py` passes all 7 gates (`ruff format`, `ruff check`, `mypy --strict`, `lint-imports`, `radon`, `vulture`, `pytest`).

---

## 6. Audit v3 (2026-11-15, commit `5ad7ec3`): why the game is still unplayable

Method: `GameSession` driven on the real disks, every scene rendered through the GL canvas and
reviewed as contact sheets; decoded tables probed; behaviour compared with `reference/moonstone-ace`
(docs `GAME_FLOW.md`, `GAME_STATE.md`, `MONSTER_KIT.md`, and the header comments of
`include/game/*.hpp`). The gate is green (248 tests) at this commit, so the tests do not measure
playability: most of them assert the behaviour of models that the original game does not have.

### 6.1 Root cause

Three subsystems are **invented designs**, not reimplementations, and the presentation layer was
then wired to them:

| Area | What the original does | What the code does |
|---|---|---|
| Overworld | Free one-pixel-per-step movement on the map picture, terrain slow-down mask, move budget, proximity scan that lists nearby nodes / knights / lairs, node menu | A 35-node graph with hand-placed road nodes (`_CANONICAL_ROAD_SITES`), three hops per day, an encounter on almost every hop |
| Fights | The fight script *is* the fighter's state: ten jobs run scripts, the AI handler only picks the next script, damage comes from per-action tables, death happens inside the hurt scripts | Timer state machines (`sim/knight_combat.py`, `sim/monster_ai.py`) with invented stamina, reach and score; the scripts are replayed from their start every frame only to choose sprites (`_run_mog_action_draws`) |
| Places | Button screens per town plus one shared item "sheet" loop (smith, market, temple, loot, exchange, offer) and dedicated dice / healer / mystic / ritual / wizard screens | Text menus with invented goods and services (`SWORD UPGRADE`, `STAMINA TONIC`, `TRAINING`, `FIREBALL SCROLL`, `LAIR KEY`) |

`docs/ARCHITECTURE.md` has no section for any of the three, so there was nothing to implement from.

### 6.2 Observed defects

1. **First overworld step starts a Balok fight** in a wasteland arena; the knight takes damage with
   no input.
2. **Arena colours are wrong**: `assemble_combat_palette` overwrites indices 1..5 with a grey ramp.
   The backdrops use index 5 for about 8,900 pixels (black in the picture), which is the white
   foliage and white ground on screen.
3. **Knight tunic and creature colours are literals** in `app/game.py`
   (`_KNIGHT_COMBAT_TUNIC_WORDS`, `_CREATURE_SCENE_COLOR_WORDS`) instead of the `mog` scene tables.
4. **Creatures render as scrambled parts** (Trogg, Balok), as a stretched knight-like figure
   (Ratman) or as stripes (Beastman): `_draws_to_pieces` ignores the frame-set opcode `$C0`, wraps
   out-of-range frame numbers with a modulo and substitutes slot 0 for a missing cel slot.
5. **Creature definitions are mapped to the wrong creatures**: `parse_monster_script_tables`
   reads the creature initialisers out of `S_0` code (necessary: the original keeps those values
   as code operands) and keys records by type byte, but `_ARCHETYPE_CREATURE_TYPE` assigns six of
   them to the wrong creature (§9.9 of the spec). The scan is unchecked pattern matching, and
   the fixed scripts that most creature AIs play by themselves are not located at all, so seven
   of eleven records have no action scripts. *(Corrected 2026-11-15: the first version of this
   item called the scan itself a guess.)*
6. **Fight HUD is invented** (HP / ST bars, score), and the hit flash recolours the HUD.
7. **Town sub-screens**: no smith, market or temple sheet, no item icons (`ki.cel`), no pointer
   (`po.cel`); Highwood's "High Temple" button is not selectable; dice, healer and mystic show the
   right picture under an invented text menu.
8. **Stonehenge** is a two-line menu over `ch.piv`; the ritual assets (`hen1.p`, `hen1.c`) are
   never shown. **The wizard's tower does not exist**, and its picture (`WI1.P`) is used as the
   victory screen.
9. **Game-over screen** shows `message.piv` with the wrong palette (nearly black).
10. **Status and new-day screens** are text over the title backdrop rather than the status sheet and
    the "Next Day" moon screen.
11. Not present at all: lair loot, knight-versus-knight duel and exchange, dragon hoard, valley
    guardian, home villages, computer-controlled knights, the ending.

What works and is kept: the disk and codec layer, picture / cel / hit / arena decoders, the intro
cinematic, the title menu and knight select, the map picture and its markers, the audio decoders,
the fight-script opcode decoder (`sim/combat_vm.py`), the renderer.

The remediation is Phase 11 in `IMPLEMENTATION_PLAN.md`; the decision it depends on is ADR-009.
