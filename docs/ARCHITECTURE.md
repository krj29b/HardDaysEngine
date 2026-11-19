# Architecture & Binary Format Specification

This document is the clean-room specification for **HardDaysEngine** (ADR-001). Every loader,
decompressor, simulation rule, and renderer component in `src/harddaysengine/` is implemented from
this specification.

---

## 1. Amiga Disk Layout & OFS Filesystem (`T2.1`)

### 1.1 Standard DD Amiga OFS Geometry
Each original game disk is an 880 KiB double-density (DD) Amiga Old File System (`DOS\0`) floppy
image:
- **Total size:** `901,120` bytes (`1,760` blocks × `512` bytes/block; 80 cylinders × 2 heads ×
  11 sectors).
- **Boot block (blocks 0–1, 1,024 bytes):** Bytes `0..3` hold `"DOS\x00"`.
- **Root block (block 880):**
  - `+0x000`: `BE32` primary type = `2` (`T_HEADER`).
  - `+0x018` (`+24`): 72 `BE32` hash-table entries (block pointers, `0` = empty).
  - `+0x1B0` (`+432`): BCPL volume name (`uint8` length followed by ASCII bytes; all 3 original
    Moonstone disks have `" "` as their volume label).
  - `+0x1FC` (`+508`): `BE32` secondary type = `1` (`ST_ROOT`).
- **Hash function (case-insensitive AmigaDOS):**
  - Seed `h = len(name)`.
  - For each character `c`: fold `'a'..'z'` to uppercase (`c - 32`), then
    `h = ((h * 13 + c) & 0x7FF)`.
  - Bucket index = `h % 72` (stored at root/dir block offset `24 + bucket * 4`).
- **File header / directory block (512 bytes):**
  - `+0x000`: `BE32` primary type = `2` (`T_HEADER`).
  - `+0x004`: `BE32` own block key.
  - `+0x008`: `BE32` high sequence (count of data blocks in this header, up to 72).
  - `+0x010`: `BE32` first data block pointer.
  - `+0x018..+0x137`: 72 `BE32` data block pointers stored in **reverse order**: slot `71`
    (`+0x134`) is the 1st data block, slot `70` (`+0x130`) is the 2nd, down to slot `0` (`+0x018`).
  - `+0x144` (`+324`): `BE32` total file byte size.
  - `+0x1B0` (`+432`): BCPL filename (`uint8` length at `+432`, characters at `+433..`).
  - `+0x1F0` (`+496`): `BE32` next block in the same hash chain (`0` = end).
  - `+0x1F4` (`+500`): `BE32` parent directory block.
  - `+0x1F8` (`+504`): `BE32` extension block (`T_LIST = 16`) when the file exceeds 72 data blocks.
  - `+0x1FC` (`+508`): `BE32` secondary type: `0xFFFFFFFD` (`-3`, `ST_FILE`) for a regular file,
    `2` (`ST_USERDIR`) for a subdirectory.
- **OFS Data Block (512 bytes):**
  - `+0x000`: `BE32` primary type = `8` (`T_DATA`).
  - `+0x004`: `BE32` file header block key.
  - `+0x008`: `BE32` sequence number (`1, 2, ...`).
  - `+0x00C`: `BE32` data size in this block (`1..488`).
  - `+0x010`: `BE32` next data block pointer (`0` = end of file).
  - `+0x014`: `BE32` block checksum (sum of all 128 `BE32` words in the block wraps to `0`).
  - `+0x018..+0x1FF`: `488` bytes of payload data. Contiguous files can be read either by iterating
    the reversed pointer table across header/extension blocks or by following the `+0x010`
    `nextData` chain until `remaining == 0`.

### 1.2 Three-Disk Identification & Inventory
Because all three disks carry the blank volume label `" "`, each disk is identified by its unique
marker file(s), matched case-insensitively:

| Logical Disk | Default Filename | Marker File(s) | File Count | Contents Summary |
|---|---|---|---|---|
| **Disk A** | `Disk1.adf` | `program` **and** `mog` | 32 root + `s/startup-sequence` (33 total) | Executables `program` (60,472 B), `mog` (172,260 B), `nb` (6,428 B), `Crystal` (52 B); overworld & intro pictures (`bg1a..c.PIV`, `bg2..8.PIV`, `message.piv`, `mindscape`); intro/map cels (`au1.cel`, `co1.cel`, `da1.cel`, `dg1.cel`, `dw1.cel`, `ha1.cel`, `Klift1.CEL`, `li1.cel`, `ov1.cel`); tile maps (`co.stile`, `intro.stile`); font (`bold.f`); music (`music.cmp`, `vmusic.cmp`); and a 5-byte stub `kn1.ob`. |
| **Disk B** | `Disk2.adf` | `HE1.ob` | 88 root | Full knight cels `KN1..5.ob` (`KN1.ob` is 20,760 B); header cels `HE1..3.ob`; creature cels (`RATMEN1..2.CEL`, `TROGGAxe1..2.CEL`, `TROGGSpear1..2.CEL`, `TROLL1..2.CEL`); UI/FX cels (`blo.cel`, `ki.cel`, `po.cel`, `sel.cel`, `mi.c`, `Small.font`); `ch.piv`; `collide.hit` (8,360 B); `test` (198,745 B 9-picture pack); 56 `.t` arena files (`fo/gl/sw/wa` `1..8` and `l1..l6`); 7 `.a` sound banks (`he.a`, `kn.a`, `Ra.a`, `re.a`, `to.a`, `tr.a`, `wn.a`). |
| **Disk C** | `Disk3.adf` | `be1.c` | 91 root | Shares 60 identical files with Disk B (all 56 `.t` arena files, `he.a`, `KN5.ob`, `po.cel`, `sel.cel`); adds creature cels (`Balok1..3.CEL`, `Demon1..4.CEL`, `DRAGON1.CEL`, `DRAGON2.CEL`, `DRAGON5.CEL`, `Mudmen1..2.CEL`, `be1.c`, `be2.c`); place/ritual cels & pictures (`dice.cel`, `dice.piv`, `hea.piv`, `hen1.c`, `hen1.p`, `HighWood.PIV`, `mys.cel`, `mys.piv`, `tav.piv`, `WaterDeep.PIV`, `Wi1.C`, `WI1.P`, `WI2.P`); 6 `.a` sound banks (`ba.a`, `be.a`, `dr.a`, `Gu.a`, `mu.a`, `wz.a`). |

**Duplicate file resolution rule:** When indexing a combined directory across Disks A, B, and C,
filenames are normalized (trimmed of leading/trailing whitespace and case-folded). Note that Disk A
contains `" drag.cel"` with a leading space (`0x20`) in its BCPL name (`" drag.cel"`, 9 bytes) and
a 5-byte placeholder `kn1.ob` (`"hello"`), whereas Disk B contains the real 20,760-byte `KN1.ob`.
When the same normalized filename exists on multiple disks, the **largest** entry wins.

---

## 2. Compression Algorithms (`T2.2`)

### 2.1 Moonstone LZSS Forward Stream
Used by Pictures (`.piv`, `.p`, `mindscape`, `test`), Cels (`.cel`, `.ob`, `.c`, `.f`, `.font`),
Arena `.t` blobs, and Audio `.a` sample banks.

- **Input:** `src[0..packed_size]` (and optional `max_output` cap when the caller allocates a fixed
  buffer).
- **Control flow:**
  1. While `src_pos < packed_size` (and `out_pos < max_output` if bounded):
  2. Read 1 flag byte `flags = src[src_pos++]`.
  3. Process 8 items from bit 7 (MSB, mask `0x80`) down to bit 0 (LSB, mask `0x01`), checking
     `src_pos < packed_size` before each item:
     - **Bit == 0 (Literal):** Copy 1 byte `out[out_pos++] = src[src_pos++]`.
     - **Bit == 1 (Back-reference):** Read big-endian 16-bit word
       `word = (src[src_pos] << 8) | src[src_pos + 1]`; `src_pos += 2`.
       - Match length: `count = 34 - (word >> 11)` (5 high bits -> range `3..34` bytes).
       - Lookback distance: `distance = word & 0x07FF` (11 low bits -> range `0..2047` bytes).
       - Copy `count` bytes forward **one byte at a time** (overlapping ranges where `distance < count`
         repeat the newly written bytes; `distance == 0` copies the current output byte onto itself
         and advances `out_pos` by `count`).

### 2.2 Rob Northen RNC Method 1 Backward Stream (`RNC\x01`)
Used by `music.cmp` (`88,187` B -> `123,228` B, title `'introx5'`, signature `'FLT4'`) and
`vmusic.cmp` (`59,154` B -> `87,792` B, title `'vict0ry6yy'`, signature `'FLT4'`) on Disk A.
This is the early **backward stream** variant of RNC1 (distinct from later forward Huffman RNC1).

- **12-byte Header:**
  - `+0`: `BE32` magic = `0x524E4301` (`"RNC\x01"`).
  - `+4`: `BE32` `unpacked_size`.
  - `+8`: `BE32` `packed_size` (byte length of compressed stream starting at `+12`).
- **Pointers:**
  - `read_ptr` starts at `12 + packed_size` (one past the last compressed byte) and decrements
    toward `12`.
  - `write_ptr` starts at `unpacked_size` (one past the last output byte) and decrements toward `0`.
- **MSB-first Sentinel-Bit Backward Reader (`BitReader`):**
  - Initialized by reading one byte: `bits = src[--read_ptr]`.
  - To extract 1 bit:
    - `is_set = (bits & 0x80) != 0`
    - `bits = (bits << 1) & 0xFF`
    - If `bits != 0`: return `is_set`.
    - When `bits == 0` (the sentinel bit `1` was just shifted out into `is_set`), read
      `next_byte = src[--read_ptr]`, set `bits = ((next_byte << 1) | 1) & 0xFF`, and return
      `(next_byte & 0x80) != 0`.
  - To extract `n` bits (`n >= 1`): shift `n` single bits MSB-first into `result`.
- **Main Decompression Loop:**
  1. **Literal run (`copy_literals`):**
     - Read 1 bit. If `0`, literal count = `0`.
     - If `1`, read 1 more bit:
       - If `0`, literal count = `1`.
       - If `1`, step `s` from `3` down to `0` across bit widths `(10, 3, 2, 2)` and bases
         `(14, 7, 4, 1)` (indexed by `s`, so `s = 3` reads `2` bits with base `1` first):
         read `width[s]` bits into `v`; if `s == 0` or `v != (1 << width[s]) - 1`, set
         `count = base[s] + v + 1` and stop cascading.
     - Copy `count` literal bytes backwards: `out[--write_ptr] = src[--read_ptr]`.
  2. **Termination check:** If `read_ptr <= 12`, stop.
  3. **Match length (`read_length`):**
     - Start with `step = 3`; while `get_bit() == 1`, decrement `step` (breaking if `step < 0`).
     - Set `idx = step + 1` (`0..4`), with extra-bit widths `(10, 2, 1, 0, 0)` and bases
       `(10, 6, 4, 3, 2)`: `length = base[idx] + get_bits(width[idx])` (`2..1033`).
  4. **Match offset (`read_offset`):**
     - If `length == 2`:
       - Read 1 bit: if `0`, `offset = get_bits(6)`; if `1`, `offset = 64 + get_bits(9)`.
     - If `length > 2`:
       - Start with `step = 1`; while `get_bit() == 1`, decrement `step` (breaking if `step < 0`).
       - Set `idx = step + 1` (`0..2`), with widths `(12, 5, 8)` and bases `(0x120, 0x000, 0x020)`:
         `offset = base[idx] + get_bits(width[idx])`.
  5. **Backward match copy:**
     - Let `last = length - 1`. Set `copy_ptr = (write_ptr + offset + last)` if `offset != 0` else
       `write_ptr + 1`.
     - Repeat `last + 1` times: `out[--write_ptr] = out[--copy_ptr]`.

---

## 3. Graphics Formats (`T2.3`, `T2.4`)

### 3.1 Full-Screen Pictures (`.piv`, `.p`, `mindscape`, and `test`)
Every full-screen picture decodes to a **320 × 200** planar image (`40` bytes/row × `200` rows =
`8,000` bytes per bitplane), with bitplanes stored **non-interleaved** (all 8,000 bytes of plane 0,
followed by all 8,000 bytes of plane 1, etc.).

- **On-disk Layout:**
  - `+0`: `BE16` `planes` (`4` or `5` in all original files).
  - `+2`: `BE32` `packed_size` (byte length of the LZSS stream following the palette).
  - `+6`: Palette table of `16` `BE16` words (`32` bytes when `planes == 4`) or `32` `BE16` words
    (`64` bytes when `planes > 4`).
  - `+6 + palette_bytes`: LZSS compressed stream of `packed_size` bytes, which decompresses to
    `planes * 8,000` bytes (`32,000` bytes for 4 planes; `40,000` bytes for 5 planes).
- **12-bit Amiga Palette Word Decoding (`picParsePalette`):**
  - Given raw big-endian word `raw_word`:
    - If `raw_word & 0x8000` (bit 15 set): `cell_word = raw_word & 0x7FFF`.
    - Else (bit 15 clear): `cell_word = (raw_word << 1) & 0xFFFF` (doubles each 3-bit channel
      nibble into 4-bit OCS range).
  - The low 12 bits of `cell_word` (`0x0RGB`) give 4-bit red, green, and blue channels:
    - `r4 = (cell_word >> 8) & 0xF`, `g4 = (cell_word >> 4) & 0xF`, `b4 = cell_word & 0xF`.
    - 8-bit sRGB expansion: `r8 = r4 * 17`, `g8 = g4 * 17`, `b8 = b4 * 17` (`0x00..0xFF`).
- **Bitplane-to-Indexed Conversion:**
  - For pixel `(x, y)` with `0 <= x < 320`, `0 <= y < 200`:
    - Byte offset within each plane: `byte_idx = y * 40 + (x >> 3)`.
    - Bit mask within that byte: `bit_mask = 0x80 >> (x & 7)` (MSB is leftmost pixel).
    - Palette index: $\sum_{p=0}^{\text{planes}-1} \left(\text{bit}_{p}(x, y) \ll p\right) \in [0, 2^{\text{planes}}-1]$.
- **Multi-Picture Pack `B/test` (`198,745` bytes):**
  - Concatenation of **9** complete pictures (each with its own `planes + packed_size + palette + LZSS`
    header and body) used by `mog` (`combat_load.hpp`) for combat HUD, region tile sheets, victory,
    and defeat screens.
  - Exact byte sizes of the 9 packed pictures (`dbf_count + 1`):
    1. Picture 0 (`0..22871`, `22,872` B): Forest/Glen foreground/object tile sheet (`5` planes; `BUF_B` for `0x0300` in regions 0 & 1) and HUD/knight palette source.
    2. Picture 1 (`22872..43680`, `20,809` B): Shared main tile sheet (`5` planes; `SCREEN_B` for non-`0x0300` opcodes across all 4 regions).
    3. Picture 2 (`43681..61688`, `18,008` B): Swamp foreground/object tile sheet (`5` planes; `BUF_B` for `0x0300` in region 2).
    4. Picture 3 (`61689..76621`, `14,933` B): Wasteland foreground/object tile sheet (`5` planes; `BUF_B` for `0x0300` in region 3).
    5. Picture 4 (`76622..102114`, `25,493` B): Forest (`fo`, region 0) base backdrop (`5` planes).
    6. Picture 5 (`102115..123046`, `20,932` B): Wasteland (`wa`, region 3) base backdrop (`5` planes).
    7. Picture 6 (`123047..142512`, `19,466` B): Swamp (`sw`, region 2) base backdrop (`5` planes).
    8. Picture 7 (`142513..163413`, `20,901` B): Glen (`gl`, region 1) base backdrop (`5` planes).
    9. Picture 8 (`163414..198744`, `35,331` B): Combat Death / Defeat screen (`5` planes).
  - Note: Computing `6 + (32 if planes == 4 else 64) + packed_size` for each picture yields the
    exact same offsets sequentially without needing hardcoded sizes.

### 3.2 Sprite & Cel Family (`.cel`, `.ob`, `.c`, `.f`, `.font`)
All five extensions share the identical binary container format:
- **10-byte File Header:**
  - `+0`: `BE16` `frame_count` (`N`).
  - `+2`: `BE32` `packed_size` (byte length of the LZSS stream starting at `10 + 10 * N`).
    *(In memory, the Amiga loader overwrites `+2` with the runtime base address of the decoded
    plane data.)*
  - `+6`: `BE32` `unpacked_bits` (`8 * unpacked_byte_size`, so `unpacked_size = unpacked_bits >> 3`).
- **Frame Directory (`N` entries × 10 bytes, at offsets `10 .. 10 + 10 * N - 1`):**
  - `+0`: `BE32` `data_offset` — byte offset into the decompressed plane buffer where this frame's
    planes begin.
  - `+4`: `BE16` `width` — frame width in pixels (`0` indicates an empty/placeholder frame).
  - `+6`: `BE16` `height` — frame height in scanlines (`0` indicates an empty/placeholder frame).
  - `+8`: `uint8` `hot_and_flags`:
    - High nibble (`hot_and_flags >> 4`, `0..15`): horizontal alignment shift subtracted from
      screen `X` before blitting (`draw_x = x - (hot_and_flags >> 4)`).
    - Bit 0 (`hot_and_flags & 1`): orientation flag. Always `1` on disk (unflipped). When flipped
      in-place in memory by `mirrorCelHeader`, bit 0 becomes `0` and the high nibble is replaced by
      `((aligned16 - width) & 0xF) << 4`, where `aligned16 = (width + 15) & ~15`.
  - `+9`: `uint8` `plane_bits` — 5-bit bitmask (`0x00..0x1F`) indicating which of bitplanes `0..4`
    are stored for this frame.
- **Decompressed Frame Plane Data:**
  - Word-aligned row stride: `row_bytes = ((width + 15) >> 4) * 2` (16-pixel / 2-byte word boundary).
  - Single plane size: `plane_size = row_bytes * height`.
  - For each bit `p` in `0..4`:
    - If `(plane_bits >> p) & 1 == 1`: the next `plane_size` bytes at `data_offset` contain bitplane
      `p` (top-to-bottom, left-to-right MSB-first within each 16-bit word).
    - If `(plane_bits >> p) & 1 == 0`: bitplane `p` is all zeroes (`0`) for every pixel in the frame.
  - **Transparency & Cookie-Cut Mask:** Palette index `0` is always transparent. The 1-bit opacity
    (cookie-cut / hurtbox) mask of a frame is the bitwise OR of all present bitplanes over the
    `width × height` pixel grid.
  - **Horizontal Mirroring (`mirrorCelPlanes`):** Reverses the bits of each `row_bytes` scanline
    across the full 16-bit-aligned width `aligned16 = row_bytes * 8`. Because `width` may be smaller
    than `aligned16`, reversing across `aligned16` shifts the visible `width` pixels right by
    `aligned16 - width` pixels; `mirrorCelHeader` sets `hot_x = aligned16 - width` so `x - hot_x`
    re-aligns the mirrored sprite's left edge with `x`.

### 3.3 Screen-Wipe Tile Maps (`.stile`) & Fonts (`bold.f`, `Small.font`)
- **`.stile` (`co.stile`, `intro.stile` on Disk A, `960` bytes each):**
  - Uncompressed array of `480` `BE16` words representing a 10 × 8 grid of 32 × 25 pixel blocks
    across 6 wipe steps (`6 * 80 = 480`, or 480 tile descriptors).
  - For each word `code`: `pic_idx = code // 80` selects source picture `0..2`, and
    `tile_idx = code % 80` selects the block at `tile_x = (tile_idx % 10) * 32`,
    `tile_y = (tile_idx // 10) * 25`.
- **`bold.f` (Disk A, `381` B) & `Small.font` (Disk B, `1,834` B):**
  - Standard Cel containers (`bold.f` has 1 frame of 1-plane glyph strip loaded into a 1,024-byte
    buffer of 128 × 8-byte ASCII glyphs starting at `'!' = 33`; `Small.font` has 59 frames of 8×8
    glyphs for ASCII `0x20..0x5A` with `plane_bits = 0x0F`).

---

## 4. Arena & Combat Data: Hitboxes, Hurtboxes, & `.t` Layouts (`T2.5`)

### 4.1 Coordinate System & 2.5D Depth Sorting
- **Virtual Screen Resolution:** `320 × 200` active playfield inside a `320 × 256` PAL display frame
  (in combat, rows `0..199` hold the arena and HUD; `program` and `mog` clip blits to `0..319`
  horizontally and `0..199` vertically).
- **Entity Coordinates (`Knight` / `CombatJob`):**
  - `uwX` (`int16`): Horizontal position in pixels (arena walk bounds `10 .. 320` after facing offset
    `±25`).
  - `uwY` (in `Knight` offset `+8` / `CombatJob.uwZ` offset `+10`): Ground depth ($Z$ / screen $Y$
    baseline), clipped to `30 .. 155` (`0x1E .. 0x9B`).
  - `uwHeight` (in `Knight` offset `+6` / `CombatJob.uwY` offset `+8`): Altitude above ground
    (subtracted from depth when projecting to screen $Y$: `screen_y = depth - height + dy`).
- **Depth Proximity Gate (`depthClose`):**
  - Two jobs can hit or physically block each other in depth only when
    $|Z_A - Z_B| \le 10$ (`CMP.W #10, D2 / BLE`).
- **Painter's Algorithm Draw Order (`combatTick`):**
  - Active combat jobs (`0..9`) are sorted each frame by ascending `uwZ` (ground depth) so smaller
    $Z$ (farther back) draws first and larger $Z$ (closer to camera) draws last.

### 4.2 Hurtboxes vs. Weapon Hitboxes (`collide.hit` & Cel Bitplane Masks)
Moonstone uses an asymmetric **point-against-bitplane** collision model (`contactScan` / `contactTest`):

1. **Defender Hurtboxes (Green Overlay in Inspector):**
   - There is no separate hurtbox file. When a combat script executes a cel draw instruction
     (`opcode $00..$1F`, 6 bytes: `[slot_op, frame, dy_s8, flags, dx_be16]`), bit 0 of `flags`
     (`flags & 0x01`) registers the drawn cel `(cel_table, frame, screen_x, screen_y)` in the job's
     **Hurt List** (`ulHurtList`, up to 6 entries per job per frame).
   - In addition, any drawn cel without `flags & 0x40` expands the actor's union screen bounding box
     `[box_min_x, box_max_x] × [box_min_y, box_max_y]` (`Knight` offsets `+58, +60, +112, +114`),
     which `blockedMask` uses for body-to-body movement blocking.
   - During `contactTest`, the defender's hurt surface is the **cel frame's bounding rectangle**
     `[def_x, def_x + cel_w) × [def_y, def_y + cel_h)` **combined with the pixel-accurate non-zero
     bitplane mask** of that cel frame (a point hits only if at least one present bitplane has bit `1`
     at `(pt_x - def_x, pt_y - def_y)`).

2. **Attacker Weapon Hitboxes / Attack Point Sets (`B/collide.hit`, Red Overlay in Inspector):**
   - When a combat script executes a cel draw instruction with bit 1 of `flags` (`flags & 0x02`),
     it registers `(cel_table, frame, screen_x, screen_y)` in the job's **Attack List**
     (`ulAttackList`, up to 6 entries per job per frame).
   - `collide.hit` (`8,360` bytes on Disk B) is an ASCII text file defining per-frame weapon attack
     points for 14 cel sets: `kn4`, `TroggSpear2`, `TroggAxe2`, `Ratmen1`, `Mudmen1`, `Balok1`,
     `Dragon1`, `Dragon2`, `Dragon5`, `be1`, `Demon2`, `Demon3`, `Troll1`, `Troll2`.
   - **On-disk ASCII Grammar (`hitParse`):**
     - Each section starts with a line containing the cel stem name (e.g. `kn4`), terminated by LF
       (`0x0A`).
     - Followed by one record per cel frame (indexed `0, 1, 2, ...`) until the 2-character sentinel
       `99`:
       - **Empty frame (`00`):** `"00\n"` (3 bytes: `'0'`, `'0'`, separator) -> emits 1 byte `0x00`
         (`count = 0`).
       - **Populated frame (`01`..`98`):**
         `"<count:2d> <type:2d> <x0:3d> <y0:3d> ... <x_{n-1}:3d> <y_{n-1}:3d>\n"`
         (where each separator is 1 byte, and each coordinate is a 3-digit zero-padded decimal
         integer `000..255` relative to the cel's top-left corner).
       - **End of section (`99`):** `"99\n"` terminates the current cel's frame list; the next line
         (if any) starts the next cel section name.
   - **Decoded Binary Record per Populated Frame (`4 + 2 * count` bytes):**
     - `count` (`uint8`): number of attack points (`1..98`).
     - `hit_type` (`uint8`): attack classification byte (`01` in original sets).
     - `max_x` (`uint8`): $\max_{i} x_i$ (bounding-box width of the attack points).
     - `max_y` (`uint8`): $\max_{i} y_i$ (bounding-box height of the attack points).
     - `points[count]`: pairs of `(x_i: uint8, y_i: uint8)` in cel-local coordinates (`0..cel_w-1`,
       `0..cel_h-1` in unflipped orientation).
   - **Exact `contactTest` Algorithm (`LAB_03DB`):**
     - If the attacker's cel frame is horizontally flipped (`(hot_and_flags & 1) == 0`), the coarse
       attack box $X$ origin is shifted by `cel_w - max_x`.
     - **Step 1 (Coarse AABB Overlap):** Check 1D overlap of `[def_x, def_x + cel_w]` with
       `[att_box_x, att_box_x + max_x]` and `[def_y, def_y + cel_h]` with `[att_y, att_y + max_y]`.
     - **Step 2 (Per-Point Bitplane Test):** For each point `(px, py)` in the frame's hit record:
       - Compute screen coordinates `(sx, sy)` (reflecting `px` as `cel_w - px` when the attacker
         cel is flipped for the bounds check; note the original 68k quirk where the bitplane test
         itself reloads raw `px + att_x`).
       - If `(sx, sy)` lies inside `[def_x, def_x + cel_w) × [def_y, def_y + cel_h)`, test bit
         `15 - ((cx - def_x) & 15)` of word `((cx - def_x) >> 4) * 2 + (cy - def_y) * row_bytes`
         across each present bitplane of the defender cel. The first non-zero bit registers a hit at
         `(cx, cy)`.

### 4.3 Arena `.t` Files (`56` files on Disks B & C)
Arena layout files are named `<region><index>.t` (`fo1..8.t`, `gl1..8.t`, `sw1..8.t`, `wa1..8.t`
for normal sites; `fol1..6.t`, `gll1..6.t`, `swl1..6.t`, `wal1..6.t` for Lair sites).
- **Container:** LZSS blob (`BE32 packed_size` followed by an LZSS stream).
- **Decompressed Payload Structure:**
  1. **Obstacle Wall Segment Table (at offset `+0`):**
     - `+0`: `BE16` `segment_count` (`N`).
     - `+2 .. +2 + 8 * N`: `N` records of 8 bytes each:
       - `+0`: `BE16` `x1` (left $X$ boundary of horizontal wall segment).
       - `+2`: `BE16` `x2` (right $X$ boundary of horizontal wall segment).
       - `+4`: `BE16` `y` (depth $Z$ line of the obstacle).
       - `+6`: `BE16` `unused` (padding / reserved word).
     - Used by the obstacle probe of a walking fighter; the exact test is §10.19 (the earlier
       "`|depth - y| <= 6`" reading was wrong).
  2. **Arena Background Tile Compositor Script (`2,400` bytes / `0x960` bytes following the obstacle table):**
     - Initial canvas: copied from the region's base backdrop in `B/test` (`test[4]` for region 0 Forest,
       `test[7]` for region 1 Glen, `test[6]` for region 2 Swamp, `test[5]` for region 3 Wasteland).
     - Sequence of 6-byte records `(BE16 kind_tile, signed BE16 dest_x, signed BE16 dest_y)` terminated by
       `kind_tile & 0xFF00 == 0xFF00` (up to 400 entries). Negative `dest_x`/`dest_y` are clipped at `0`.
     - High byte `kind = kind_tile & 0xFF00`, low byte `tile_index = kind_tile & 0x00FF`:
       - `0xFF00`: End of script.
       - `0xFE00`: No-op / skip slot.
       - `0x0300`: Blit a `32 × 25` 5-plane cookie-cut tile from the region's **foreground/object
         sheet** (`test[0]` for regions 0/1, `test[2]` for region 2, `test[3]` for region 3), with
         no tile index offset.
       - Any other high byte (`0x0000`, `0x0100`, `0x0200`, `0x0400`): Blit a `32 × 25` 5-plane
         cookie-cut tile from the **shared main tile sheet** (`test[1]`).
     - **Tile Source Coordinates:** Within a 320 × 200 tile sheet (10 columns of 32 px × 8 rows of
       25 px = 80 tiles), `tile_index` (`0..79`) maps to
       `src_x = (tile_index % 10) * 32`, `src_y = (tile_index // 10) * 25`.
     - **Foreground Plane Restore (`fgPlaneSave`):** After `arenaBuild` composites the background,
       rows `24..199` (`7,000` bytes) of plane 3 (`bitplane[3]`) are saved as a foreground mask so
       objects behind foreground scenery elements can be masked during combat.

---

## 5. Amiga Hunk Executables (`program` and `mog`, `T2.6`)

Per ADR-001, original animation scripts, AI tables, palette tables, and synth tables are **never**
pasted into Python source code; they are parsed at runtime from the user's own `program` and `mog`
AmigaDOS Hunk executables on Disk A.

### 5.1 AmigaDOS Hunk Format
- `+0x00`: `BE32` magic = `0x000003F3` (`HUNK_HEADER`).
- `+0x04`: `BE32` resident library string list (`0`).
- `+0x08`: `BE32` `table_size` (number of hunks).
- `+0x0C`: `BE32` `first_hunk` (`0`).
- `+0x10`: `BE32` `last_hunk` (`table_size - 1`).
- `+0x14`: `table_size` `BE32` words giving the allocation size in 32-bit longwords of each hunk
  (bits 30–31 hold `MEMF_CHIP` / `MEMF_FAST` flags; mask with `0x3FFFFFFF` and multiply by 4 for
  allocated byte size).
- **Hunk Blocks (`0 .. table_size - 1`):**
  - `HUNK_CODE` (`0x3E9`) or `HUNK_DATA` (`0x3EA`): followed by `BE32 data_longs` and
    `data_longs * 4` raw big-endian bytes (zero-padded to the header allocation size).
  - `HUNK_BSS` (`0x3EB`): followed by `BE32 alloc_longs` (zero-filled in memory).
  - `HUNK_RELOC32` (`0x3EC`): repeating groups of `(BE32 count, BE32 target_hunk, BE32 offsets[count])`
    until `count == 0`. Each offset in the current hunk has the runtime base address of `target_hunk`
    added to its `BE32` value.
  - `HUNK_END` (`0x3F2`): closes the current hunk.

### 5.2 Key Hunk Sections in `program` (`60,472` B, 34 hunks) & `mog` (`172,260` B, 46 hunks)
- **`program` (Overworld & Intro):**
  - `S_2`: Text strings (`PrgStrings`).
  - `S_3`: Overworld/intro globals and animation scripts (`PrgGlobals`).
  - `S_11`: 10 animation job slots (`PrgJobs`, 30 bytes/job).
  - `S_28`: Built-in 1,024-byte 8×8 bold font (`128 × 8` bytes, overwritten by `bold.f`).
  - `S_32` / `S_33`: Palette constants and VBL fade/cycle state (`PalConst`, `PalState`).
- **`mog` (Main Loop, Map, & Combat):**
  - `S_4`: Master data hunk (`MogTables`): menu descriptors, knight names, creature type definitions
    (`CreatureTypeDef`), encounter bands, combat action tables (`kActionRight`, `kActionLeft`),
    knight/creature combat bytecode scripts (`LAB_07E0..LAB_08C0`), and damage/defense tables.
  - `S_33`: Built-in 1,024-byte 8×8 bold font.
  - `S_44`: Synth sound-effect sequence pointer table (`168` `BE32` offsets at `+0..+671`) and
    bytecode streams.
  - `S_45` (`MEMF_CHIP`): `168` bytes of single-cycle hardware waveforms (`+0..+167`), followed at
    `+168` (`LAB_10A2`) by `131` × 14-byte synth instrument records (`9` internal waveform
    instruments + `122` external `.a` bank sample instruments) and a 14-byte terminator.

---

## 6. Audio Formats: `.a` Sample Banks, Synth Sequencer, & `.cmp` Music (`T2.7`)

### 6.1 `.a` Sound Sample Banks (`13` files across Disks B & C)
- **Files:**
  - Disk B: `he.a` (ritual/screen bank), `kn.a` (knight combat bank), `Ra.a` (Ratmen),
    `re.a` (campaign music), `to.a` (Troll), `tr.a` (all Trogg variants), `wn.a` (unused loader).
  - Disk C: `ba.a` (Balok), `be.a` (Beast), `dr.a` (Dragon), `Gu.a` (Guardian), `mu.a` (Mudmen),
    `wz.a` (Wizard).
- **Container:** 12 of the 13 `.a` banks are wrapped in a 32-byte single-section Amiga Hunk header
  (`HUNK_HEADER` + 1 `HUNK_CODE` section + 4-byte `HUNK_END` footer); `wn.a` is a direct IFF
  `FORM....8SVX` container.
- **Decompressed Payload:** Raw concatenated **8-bit signed PCM** (`int8`, `-128..+127`) waveforms
  (often containing embedded IFF `FORM....8SVX` headers) for the Amiga Paula DACs.
- **Instrument Addressing (`mog` Hunk `S_45` at `+168`, `131` records × `14` bytes):**
  - Each 14-byte `SynthInst` record has:
    - `+0`: `BE16` `loop_flag` (`int16`: `< 0` means looping, `>= 0` means one-shot; §6.2.2).
    - `+2`: `BE16` `loop_offset` (byte offset from sample start for loop point).
    - `+4`: `BE16` `length_words` (sample length in 16-bit words; byte length = `length_words * 2`).
    - `+6`: `BE32` `sample_offset` (for instruments `0..8`, relocated offset in `S_45` `0..167`;
      for instruments `9..130`, byte offset inside one of the 5 runtime bank slots `LAB_05C7..05CB`).
    - `+10`: `BE32` `pitch_offset` (relocated hunk-44 offset of the instrument's period table;
      a note reads the word at byte offset `2 * (4 * note + transpose)`, §6.2.6).
  - **Paula Sampling Frequency:** Given a Paula period word `P` (typically `124..856`), the PAL
    sample rate is $f_s = \lfloor 3,546,895 / P \rfloor\text{ Hz}$ (for standard C-2 period `214`,
    $f_s \approx 16,574\text{ Hz}$; for period `428`, $f_s \approx 8,287\text{ Hz}$).

### 6.2 Sound front end and 4-voice synth sequencer (`mog` hunks 16, 44, 45; T11.23b)

Every `mog` sound, music and effects alike, is a *sequence* started on one of Paula's four
channels. A sequence is a byte stream that the synth in hunk 44 interprets once per PAL VBL. The
hunk-16 front end decides which channel a sound effect gets. Nothing in this subsection feeds
back into the simulation: `sim` only reports requests (§10.32), and `app` plays them. The
behaviour below is implemented in `app/synth.py` and `app/paula.py`; tables, cell values and
operands come from the user's `mog` (`assets/synth_data.py`). Offsets are hunk offsets.

**Supported version.** The decoder accepts `mog` only when:

- its hunk-0 CRC is supported, as for every `mog` decoder;
- the CRC-32 of hunk 44 bytes `$0000..$0BE7` (the zeroed voice records and all synth code) is
  `$59D1AB36`;
- the CRC-32 of hunk 16 bytes `$0000..$0133` (the front end, its two cells and the stop-all
  routine) is `$EBA59B10`.

The behaviour is implemented, not read, so it is tied to exactly that code. Operands are then
read at checked positions: the instruction word must match, and pointers must be relocated into
the expected hunk.

#### 6.2.1 Front end (hunk 16)

Two cells: the *busy byte* (`$00C2`, initially 0) has one bit per channel; the *cursor* word
(`$00BE`, initially 0) remembers the channel used last. Both are code-hunk cells, so they keep
their values for the session.

- *Fixed* start, channel n (`$0000 + 16n`): `MOVEQ #n,D1`, set bit n of the busy byte, jump to
  the synth start. Area music uses these, which keeps its channels away from effects.
- *Release*, channel n (`$0040 + 18n`): AND the busy byte with a mask that clears bit n and bits
  4 to 7, then *play* the silence sequence (`$A7`). The silence lands on the next free channel in
  cursor order, which need not be channel n.
- *Play* (`$0088`): if the low four busy bits are all set, nothing happens. Otherwise step the
  cursor (`+1`, `AND 3`) until it names a channel whose busy bit is clear, and start the sequence
  there. The channel is not marked busy, so effects take channels in turn and can cut each
  other off.
- *Stop all* (`$011A`): clear the busy word, then call the four releases in channel order. Since
  nothing is busy, the four silence starts cover every channel.
- *Init tail* (`$0110`, end of the mog sound set-up): clear the busy word. The cursor is kept.

A *direct* start (§12.31, `SoundKind.START`) calls the synth start without touching the busy
byte. Release requests carry their own mask and silence id, decoded from the caller's code
(§10.32).

#### 6.2.2 Data (hunks 44 and 45)

References inside the synth are hunk-44 offsets. Read from the user's file:

| Item | Source | Meaning |
|---|---|---|
| Sequence table | `LEA (d16,PC)` at `$06FA` (also absolute at `$0A84` and `$0B34`) | 168 longs at `$2254`, relocated into hunk 44. Entry 0 is null (an idle voice). Sequence `$A7` is silence. |
| Instrument table | `LEA abs` at `$0710` (relocated into hunk 45); row size from `MULU #14` at `$0B0A` | 131 rows of 14 bytes at hunk-45 `$00A8`: loop word (signed), loop offset in bytes, length in words, sample address (long), pitch table (hunk-44 long). |
| Vibrato rows | `MULU #15` at `$0A06`, `ADDI.L` at `$0A0A` (relocated) | 15 signed bytes per row, base `$13B6`. The game uses rows 0 to 6. As in the original, rows overlap the envelope rows and the first sequence. |
| Envelope rows | `ASL #3` at `$0AF0`, `ADDI.L` at `$0AF2` (relocated) | 8 bytes per row, base `$13F2`. No sequence in the game selects one. |
| Tempo cell | `LEA (d16,PC),A6` at `$077E` | Word at `$0BEE`, initially 0. Never reset, not even by init. |
| Tempo dividend | `MOVE.L #750,D1` at `$09CE` | Command `$94` stores `750 / argument`. |
| Fade cell, attenuation | `TST.W abs` at `$0B72`, `MOVE.W #16` at `$0B84` | Fade flag word `$0BE8`; per-voice attenuation 16. |
| Rest and park buffers | `$07BC` (hunk-45 `$00A8`); `$032A` and `$06DE` (hunk-45 `$0000`) | One-word buffers. The rest buffer is the first word of the instrument table; the park buffer is the first word of waveform 0. |
| Start period | `MOVE.W #$100` at `$072A` | Period of a freshly started voice. |
| Init wipe | `LEA abs` at `$04F6`, `MOVE.W #$7F` at `$0500` | Init clears `4 + 4 * 128` bytes from `$0BF0`. That covers the four voice stacks (128 bytes each, tops `$0C70`, `$0CF0`, `$0D70`, `$0DF0`), the lock word and the first pitch-table word after them. |

The first nine instruments are the single-cycle waveforms in hunk 45, and their sample longs are
relocated into hunk 45. The other 122 hold offsets inside a sound bank. When an area's bank is
loaded, a relocation routine adds the base of one of five bank buffers to each of them
(§6.2.10). A standalone, unrelocated synth keeps the raw small offset, which is not
sample memory and reads silence. The game installs the relocation data before its bank loads.

**Instrument loop word.** Negative means *looping*: after the first pass, the channel replays
the part from the loop offset to the end. With loop offset 0, the whole sample repeats and no
interrupt is used. Non-negative means a one-shot sample. The nine waveforms loop whole. Most bank
samples are one-shots; ten of them loop, and seven of those start their loop at an offset.

#### 6.2.3 Voice state

Each channel has a voice record (hunk 44, `$94` bytes apart, zero in the file). Its fields are:

- the channel's DMA and interrupt words, and an interrupt countdown;
- the pitch table, the envelope (0 = none) and the stack pointer;
- the instrument's sample address, loop word, loop offset and length;
- the last note, the volume and a retrigger flag;
- the envelope timer, on flag and phase;
- the sequence start and position (0 = idle), the ticks left of the current note and the note
  length;
- the base volume, and the base and output period;
- two tremolo delay/step counter pairs, three vibrato pairs and the loaded 15-word vibrato row;
- the tremolo and vibrato offsets, the repeat flags (bit 0 tremolo, bit 1 vibrato), the
  transpose and the fade attenuation.

#### 6.2.4 Init (`$04CE`)

Init runs once per entry into `mog`:

- Paula is reset: the four channels' audio DMA, interrupts and requests are cleared, and every
  channel is parked on the park buffer at volume 0.
- Each voice's interrupt countdown becomes 2, its sample becomes the park buffer with length
  8 words, and its stack pointer becomes the stack top.
- The init wipe zeroes the stacks and the first pitch-table word.

The init routine itself never touches the tempo cell. But every entry into `mog` loads the
executable from disk again, so the voice records, the stacks, the tempo cell and the front-end
cells all start from their values in the file. Our `Synth.init` and `SoundFront.load` model that
reload followed by the routine. The synth keeps a working copy of hunk 44, so the wipe, the
stacks and the original overflow behaviour all act on the same bytes as in the original.

#### 6.2.5 Start a sequence on a channel (`$0698`)

1. Clear the voice record from offset `$10` to `$8F`. The interrupt countdown survives;
   transpose and fade attenuation are cleared. The stack pointer becomes the stack top.
2. Disable the channel's interrupt, clear its request, stop its DMA, and set volume 0, the park
   buffer, length 1 and period 1. Then enable its DMA again.
3. Sequence start and position become the table entry for the sequence.
4. Load instrument 0. Base and output period become the start period.
5. Write the instrument's sample, its length and the start period to the channel.
6. Write DMACON `$0002`. This stops **channel 1**, whatever channel was started. The quirk is
   kept: any sound start silences whatever channel 1 was playing until that voice's next note.

#### 6.2.6 Tick (`$0342` VBL job, `$076A`)

The tick does nothing while a start is in progress (a lock word); our host never interleaves
the two. Otherwise:

1. Run the **voice step** of voices 0 to 3.
2. Run the **envelope step** of voices 0 to 3.
3. Run the **register pass** of voices 0 to 3.

**Voice step.** An idle voice (position 0) is skipped completely. If the note has ticks left,
go straight to modulation. Otherwise, if the loop offset is 0, put the channel at rest: stop its
DMA and interrupt, clear its request, then set volume 0, the rest buffer, length 1 and period 1,
and restart DMA. Then read the stream:

- A byte below `$80` is a **note**. Store it, and put the channel at rest as above, but without
  restarting DMA. With an envelope, set the envelope's on flag (high byte) and clear its phase,
  the volume and the timer. The base period is the pitch-table word at byte offset
  `2 * (4 * note + transpose)` (16-bit arithmetic, signed index). Set the retrigger flag. Reload
  the tremolo counters and clear the tremolo offset, then do the same for the vibrato. A note
  ends the read.
- A byte of `$80` or more is a **command**. The jump table is indexed by `byte - $80`, so only
  the multiples of 4 from `$80` to `$D4` exist. Any other byte is an error in our model (the
  original would jump through a misaligned table entry).

| Byte | Effect |
|---|---|
| `$80 v` | base volume = v |
| `$84 x`, `$A0 x`, `$A4 x` | skip one byte |
| `$88` | position = sequence start; read on |
| `$8C n` | note length = n × tempo (low word of the unsigned product) |
| `$90` | end the read |
| `$94 d` | tempo = 750 / d (unsigned; d = 0 traps on the 68000 and is an error in our model) |
| `$98 c b...` | note length = (sum of the next c bytes) × tempo. c = 0 means 65,536 bytes, as `DBF` counts. |
| `$9C r` | clear repeat bits 0 and 1, load vibrato row r (15 sign-extended bytes), and clear both tremolo delays, all three vibrato delays and both offsets. The step counters are not touched. |
| `$A8 f` | repeat flags \|= f |
| `$AC` | stop: ticks left = 0, position and sequence start = 0, disable the interrupt, clear the request, stop DMA, clear the retrigger flag, end the read |
| `$B0 s` | push the position (long), then jump to sequence s |
| `$B4` | pop the position |
| `$B8 t` | transpose += signed t; t = 0 sets the transpose to 0 |
| `$BC t` | transpose = signed t |
| `$C0 n` | loop start: push the position and n; n = 0 pushes a null loop |
| `$C4` | loop end: a null loop is popped. Otherwise jump back and count down; at 0 the loop address is cleared, so the body runs n + 1 times. |
| `$C8 e` | envelope = row e |
| `$CC` | no envelope |
| `$D0 i` | load instrument i (loop word, loop offset, length, sample address, pitch table) |
| `$D4 s` | jump to sequence s (no return) |

When the read ends, store the position and set ticks left = note length. A stop also takes this
path, so its modulation and register writes still happen this tick.

*Modulation.* Count ticks left down by one (16 bits). Without an envelope, run the tremolo. For
stage 0, then stage 1:

- a non-zero delay is counted down, which ends the stage search;
- otherwise a non-zero step count is counted down, the stage's delta is added to the tremolo
  offset and the delay is reloaded, which also ends the search.

If no stage acted, clear the tremolo offset and, with repeat bit 0, reload the tremolo counters.
The vibrato works the same way with stages 0 to 2 and repeat bit 1, except that it keeps its
offset when no stage acts. Finally, without an envelope, volume = (base volume + tremolo offset)
AND `$3F`. In every case, output period = base period + vibrato offset.

Row layout (word index): 0 to 1 tremolo steps, 2 to 4 vibrato steps, 5 to 6 tremolo deltas,
7 to 9 vibrato deltas, 10 to 11 tremolo delays, 12 to 14 vibrato delays. Reloading copies the
steps and delays from the row into the counters.

**Envelope step.** Skipped for an idle voice and for a voice without an envelope. A running
timer is counted down, and the step waits until it reaches 0. With envelope bytes e0 to e7:

- *Release* (on flag clear): clear the phase. If the volume is non-zero, timer = e7, and the low
  byte of the volume drops by e6, to 0 on a borrow.
- *Attack* (phase 0): timer = e1; add e0 to the volume's low byte. At or above the base volume
  (unsigned word compare), clamp to it and go to phase 1.
- *Decay* (phase 1): timer = e3; subtract e2 from the low byte, and a result of `$40` or more
  becomes 0. If it is at or above e4, store it. Below e4, go to the next phase and fall into
  sustain.
- *Sustain*: volume = e4. Once the low byte of ticks left is not greater than e5 (signed bytes),
  clear the on flag.

**Register pass.** Write the output period, then volume minus the fade attenuation (at least 0).
If the retrigger flag is set:

1. Clear it, write the sample address and the length, and set the interrupt countdown to 2.
2. Start DMA and clear the channel's request.
3. A one-shot instrument then enables the channel's interrupt. A looping instrument with loop
   offset 0 disables it, because it needs no reload. A looping instrument with a non-zero loop
   offset sets the countdown to 1 and enables it.

#### 6.2.7 Audio interrupt (`$0250`)

While any audio request is pending (checked in channel order 0 to 3), acknowledge it. If that
channel's interrupt is enabled, service it:

- Count the interrupt countdown down.
- Looping instrument: point the channel at the loop part (sample + loop offset, length minus
  loop offset / 2) and disable the interrupt.
- One-shot with countdown 0: disable the interrupt and stop DMA.
- Any other one-shot: point the channel at the park buffer, length 1.
- Clear the request again.

A one-shot therefore takes two interrupts: one at the start of the sample (park the next buffer)
and one when the park buffer starts (switch off).

#### 6.2.8 Fade (`$0B72`)

This routine runs on every step of a palette fade. If the fade flag is set, every voice's
attenuation becomes 16, otherwise 0. The silent fade-out (`LAB_03F1` in the reference) sets the
flag for the 36 frames of the fade, clears it, then calls *stop all*. The attenuation therefore
remains until the next fade step, or until that voice starts a sequence (the silence starts of
stop all reset it). Wiring each silent-fade caller in the host belongs to T11.23c.

#### 6.2.9 Paula model (`app/paula.py`)

This part is not original code. It turns register writes into sound:

- Registers per channel: location, length (0 = 65,536 words), period and volume. A period of 0
  means 65,536; below 124 the DMA rate limits it to 124. The volume uses its low 7 bits, and
  64 or more plays as 64. The location ignores bit 0.
- DMA rising edge: at the end of the CPU batch that caused it (the tick, one start, or one
  interrupt), the channel latches location and length, raises its request and starts playing the
  first byte. Off then on within one batch is a restart; on then off is nothing.
- When the latched buffer runs out, the channel latches the *current* location and length
  registers, raises its request, and continues. A raised request whose interrupt is enabled calls
  the synth's interrupt routine at that moment.
- Writing location or length while a buffer plays only changes the next buffer. Period and
  volume apply from the moment of the write, and the playing position is kept.
- The four channels mix as Amiga stereo: 0 and 3 left, 1 and 2 right. Each output sample is the
  channel's current byte × volume, sampled at the output rate without filtering.
- Address space: the waveform hunk and each of the five bank buffers are separate regions. Any
  address outside a loaded region reads silence.
- Each host VBL runs the synth tick, then that tick's start requests, then renders one fiftieth
  of a second. The original makes its starts during the frame after the VBL, so only sub-frame
  timing differs. ADKCON is not modelled: init writes it to clear modulation, which the synth
  never enables.
- Host output (not original): the host loop runs VBLs in bursts that follow the display (0, 1
  or 2 per 60 Hz frame), so each rendered fiftieth goes into a PCM ring. An SDL audio callback
  drains it at exactly the render rate (no device-side rate change). Playback starts once one
  device chunk plus two VBLs are queued, and starts again the same way after any underrun.
  Above six further VBLs the oldest frames are dropped to the middle of that window, which
  bounds latency when the host clock drifts ahead (`app/audio_stream.py`, ADR-051). Queueing
  one `pygame.mixer` sound per VBL is wrong: a channel holds a single queued sound, so bursts
  overwrite chunks and gaps starve the channel (Audit v4 "jittery sound").

### 6.2.10 Bank loading and instrument relocation (T11.23c)

Hunk16 `$00F2` loads the campaign bank; `$0134..$0268` contains the
individual bank loaders. Each reads its relocated filename, destination cell
and byte count, then calls `$026A`. That reader skips exactly 32 file bytes
before copying the requested bytes. These are memory images, including any
embedded IFF headers; decoding an embedded BODY would move sample offsets.
The requested size includes the file's header on most banks, so DOS returns
fewer bytes after the skip (Wz.a is shorter still). A short read leaves the
old buffer tail untouched; a fresh buffer tail is represented as zero. This
also retains a trailing HUNK_END when it falls within the returned bytes.
The five destination cells in hunk1 are `$140`, `$144`, `$148`, `$14C`, `$150`
(knight, shared creature/screen, campaign, wizard, Ratmen). Loading a new bank
replaces that slot; it does not rewrite the instrument offsets or reset voices.

The relocation entry is the relocated JSR at hunk16 `$010A`, into hunk44
`$1214`. Interpret its bounded instruction forms over symbolic bank addresses:
LEA of an instrument, MOVE.L bank cell to D1, ADD.L D1 to sample field +6,
ADDA.L row stride, CMPA.L end row, byte BEQ/BNE/BRA, MOVE.L bank cell to
sample field +6, ADDI.L offset to that field, and RTS. All table references
must point into hunk45 and all bank cells into hunk1. The ADD forms use each
original sample offset; MOVE followed by ADDI replaces it. The resulting
sample is a pair (slot, byte offset), resolved in app sample memory. Repeat
loading uses these immutable pairs, so addresses never accumulate bank bases.
Waveform instruments retain their hunk45 addresses. Unloaded slots read zero.
The startup campaign bank is loaded on entry to mog; knight and creature
banks are loaded by their existing fight loaders, and wizard/ritual callers
install their decoded banks before starting their first sequences.

### 6.3 `.cmp` Music Modules (`music.cmp`, `vmusic.cmp` on Disk A)
- RNC1-compressed 4-channel, 31-sample **ProTracker (`M.K.`)** modules:
  - Decompress with the RNC1 backward decoder (§2.2).
  - Standard 1,084-byte ProTracker header:
    - `+0..+19`: 20-byte module title.
    - `+20..+949`: 31 × 30-byte sample headers (`+22`: `BE16` length in words, `+24`: `uint8`
      finetune, `+25`: `uint8` volume `0..64`, `+26`: `BE16` repeat start in words, `+28`: `BE16`
      repeat length in words).
    - `+950`: `uint8` song length in pattern positions (`1..128`).
    - `+952..+1079`: 128-byte pattern order table (`max_pattern = max(order_table) + 1`).
    - `+1080..+1083`: `"M.K."` signature.
    - `+1084 .. +1084 + max_pattern * 1024`: Pattern data (`64` rows × `4` channels × `4` bytes).
    - Followed by raw 8-bit signed PCM sample data for all 31 samples.

#### 6.3.1 Original program player (T11.23c)

The supported program's hunk1 CRC-32 is `$9FAA6BB6`. Read the period table
through the PC-relative LEA at `$01D2` (37 words), the vibrato waveform through
`$048C` (32 bytes), and the initial speed from `$00DA`. Finetune is ignored.
Each PAL tick increments the byte counter; at speed it clears the counter and
reads the next row, otherwise it applies effects to all four channels. Initial
speed is 6, so the first row starts on VBL 6. There is no CIA tempo change.
Init finds the sample base using the maximum of all 128 order bytes. It zeroes
four bytes at each sample start, including zero-length slots. Each voice keeps
its current sample, period, volume, slide target/direction/speed, and vibrato
parameter/phase across rows and module stop. A module stop removes the tick
job; it does not stop DMA. Mog initialization then resets Paula.

A sample number loads its sample start, length, repeat start/length and header
volume. A nonzero repeat start replaces the initial length with repeat start
plus repeat length. A nonzero note restarts DMA unless effect3, which sets a
slide target/direction instead. All four new notes start together; afterwards
the loop registers are written in reverse channel order. Sample memory is
signed PCM. Output uses the existing event-level Paula model without synth
interrupts.

Between row ticks: effect0 cycles root/high-nibble/low-nibble semitone offsets
at tick modulo3, looking up the period in the original table; 1 subtracts the
parameter and clamps to113, 2 adds and clamps to856 (12-bit period); 3 remembers
a nonzero speed and moves towards its target, clamping and clearing the target
when reached; 4 remembers a nonzero parameter, uses waveform index
`(phase >> 2) &31`, amplitude `wave * (parameter &15) >>6`, adds/subtracts
according to the phase's sign, then adds `(parameter >>2)&60` to the byte phase.
Every other effect restores the base period. A changes the stored volume by
the high nibble if nonzero, otherwise subtracts the low nibble, clamped0..64.
Effects1/2 use the current row parameter directly, without parameter memory.
Their word arithmetic keeps the upper4 bits while comparing/writing only the
low12: a subtraction that wraps under zero can therefore escape the lower clamp.

On a row, B sets song position to parameter-1 and toggles the break byte; D
also toggles it and ignores its parameter, always proceeding to row0. C writes
the immediate volume (signed-byte comparison with64), without changing the
stored sample volume. F uses only the low5 bits as speed, ignores zero, and
clears the tick counter when accepted. Other commands, including E and sample
offset9, do nothing. A row advances by16 bytes; at1024 it wraps, clears the
break byte and advances the order position. A remaining break also advances
the position and resets row0. Positions wrap at128 and at song length.

#### 6.3.2 Mog scene sounds (T11.23c)

Map music is the four direct starts at hunk0 `$2F20..$2F58`; read each sequence
and channel immediate and require its relocated target to be the synth start.
Direct starts do not reserve busy bits. Existing service, wizard, dice,
loading and ritual data already decode their channel-specific starts.
Sheet click is the PLAY immediate at `$C03A` (the rest/offer/take sites must
agree). Good jingle is four ordered PLAYs at `$CEB4,$CEBE,$CEC8,$CED2`, bad is
two at `$CE8E,$CE98`. Each jingle waits8 VBLs after requesting its voices.
The legacy sheet `buy`, `sell` and `coin` events denote printed text, not
audio: `$C410`, `$C4CC` and `$C284` call the text routine in hunk12. Do not
invent sound requests for them. This corrects earlier sound wording in §12.

Silent fade is hunk0 `$8B46`: set the synth fade flag, start black fade with
period2, wait36 VBLs, clear the flag and stop all. Only its actual callers
use attenuation: farewell, fight close, shared return from places, ritual-message
close and ritual completion, wizard farewell, dice leave, service leave,
and fight-loading close. Normal OUT (`$8B26`) and IN (`$8B7C`) fades run
the fade hook with the flag clear; they do not stop sounds. The host retains
its corresponding caller while a silent fade runs, including already-black
palettes. New starts wait until the caller's fade finishes. Next Day restarts
the same four map sequences, as `$2F5A` calls the shared music entry.

---

## 7. 50 Hz PAL Timing, Bytecode VMs, & Simulation Records (`T2.8`)

### 7.1 50 Hz Hardware Tick & Frame Divisors
- The master simulation clock is the **50 Hz PAL Vertical Blank Interrupt (VBL)** (`20 ms` per tick).
- Every VBL (`LAB_0B9D` increments by 1; `LAB_0B9E` decrements toward 0):
  1. Advances palette fade/cycle/ramp state machines (`palVblUpdate`).
  2. Advances audio (ProTracker `mt_music` in `program`; 4-voice synth `synthTick` in `mog`).
  3. Runs the active frame/animation tick when not throttled.
- **Combat Frame Rate Divisor (`pFrameDelay`, `LAB_05BA` in `mog`):**
  - Normal combat speed: `6` VBLs per combat step ($50 / 6 \approx 8.33\text{ Hz}$, `120 ms` per
    combat frame).
  - Fast combat mode: `5` VBLs per combat step ($50 / 5 = 10.0\text{ Hz}$, `100 ms` per combat
    frame).

### 7.2 Animation & Combat Bytecode Virtual Machines
Both `program` (`animTick`) and `mog` (`combatTick`) execute bytecode scripts from their DATA hunks
on up to **10 concurrent job slots** (sorted each tick by ascending depth `uwZ` for painter's order).

- **Draw Instruction (`opcode 0x00..0x7F`, 6 bytes):**
  - `+0`: `uint8` `op` — `cel_slot = (op & 0x1F) >> 2` (encoded as `slot * 4`: `0x00, 0x04, 0x08, 0x0C, 0x10` for the 5 cel-table slots in `ulFrames`).
  - `+1`: `uint8` `frame_index` (`0..frame_count-1`).
  - `+2`: `int8` `dy` (signed vertical offset added to `job.uwY + job.uwZ`).
  - `+3`: `uint8` `draw_flags`:
    - Bit 0 (`0x01`): Register cel in job's **Hurt List** (`ulHurtList`).
    - Bit 1 (`0x02`): Register cel in job's **Attack List** (`ulAttackList`).
    - Bit 4 (`0x10`): Stamp directly into the static background buffer (`SCREEN_B`) instead of the
      per-frame dirty-rect restore list (used for blood splats and fallen corpses).
    - Bit 5 (`0x20`): Set text-overlay flag (`pTextFlag`).
    - Bit 6 (`0x40`): Exclude this cel from expanding the actor's union body bounding box.
    - Bit 7 (`0x80`): Skip this draw (and its hit/hurt registration) when Gore is off / demo mode is active.
  - `+4`: `BE16` `dx` (`int16` signed horizontal offset). Top-left screen $X$ is `job.uwX + dx` when facing right (`!(ubFlags & 2)`), or `job.uwX - dx - frame_width` when mirrored/facing left (`ubFlags & 2`).
  - Multiple draw instructions execute sequentially within the same tick until a 2-byte `$FF` frame-boundary marker is reached.

- **2-Byte Frame Boundary Markers (`0xFF <next>`):**
  - `0xFF 0x00` (and any `next` other than `0xFF`/`0xFE`): End current frame, advance `ulScript += 2`, and yield until the next tick.
  - `0xFF 0xFF`: End current frame AND terminate the script (`ubRunning = 0`), unless `loop2` is active and has remaining iterations (in which case `ulScript = ulLoop2`).
  - `0xFF 0xFE`: End current frame AND step `loop2` (decrement `ubLoop2Count`; if non-zero jump to `ulLoop2`, else clear `ubLoop2Active` and advance `ulScript += 2`).
  - Before checking `next`, `endOfFrame` evaluates active `loop1` (`ubLoopActive`), active motion block (`ubMotionActive`), and armed deferred jump (`ubCallArmed`) in that priority order.

- **Control Opcodes (`mog` `S_4` fight-script bytecode):**
  - `0x80 <facing:u8>` (2 bytes): Set `ubFlags = facing` (1 = right, 3 = left), or toggle bit 1 (`ubFlags ^= 2`) when `facing == 0xFF`. Also updates owner `+10` (`ubFacing`).
  - `0x84 <mode:u8> <target:BE32>` (6 bytes): If `mode == 3`, jump immediately (`ulScript = target`); otherwise arm deferred continuation (`ulCall = target`, `ubCallArmed = 1`, `ulScript += 6`) taken at the next `$FF` marker.
  - `0x88 <count:u8>` (2 bytes): Begin `loop1` (`ubLoopActive = 1`, `ulLoop = pc + 2`). If `count == 0`, pick a random count in `1..31` from the engine PRNG.
  - `0x8C <param:u8> <count:u8> <flags:u8> <vy:u8> <vy_lim:u8> <vx:u8> <vx_lim:u8>` (8 bytes): Arm 16-bit integer motion block (`ubMotionActive = 1`, `ulReturn = pc + 8`). Stepped at each `$FF` marker for `count` steps: `flags` bit 0 rises (`uwY -= vy`), bit 1 falls (`uwY += vy`, clamped to `0`), bit 4 moves in facing direction, bit 2 moves opposite, bit 5/7 lock `vy`/`vx` acceleration, bit 6 keeps current script instead of returning to `ulReturn`.
  - `0x94 <count:u8>` (2 bytes): Begin `loop2` (`ubLoop2Count = count`, `ubLoop2Active = 1`, `ulLoop2 = pc + 2`).
  - `0x98 <pad:u8> <target:BE32>` (6 bytes): Jump to `target` if Gore-off / demo flag is non-zero; else `pc += 6`.
  - `0xA0 <flags:u8> <dx:BE16> <dy:BE16> <dz:BE16>` (8 bytes): Move job position. If `flags & 0x40`, set absolute `(uwX, uwY, uwZ) = (dx, dy, dz)`. Otherwise add/subtract `dx` (respecting `ubFlags == 3` facing and `flags & 1`), `dy` (`flags & 8` subtracts), and `dz` (`flags & 0x20` subtracts).
  - `0xA4 <sound_id:u8>` (2 bytes): Trigger synth sound sequence `sound_id`.
  - `0xA8 <size_flags:u8> <offset:s16> <value:BE32>` (8 bytes): Write low byte (`size_flags & 1`), word (`size_flags & 2`), or long (`else`) to owner record at signed byte offset `offset`.
  - `0xAC <armed:u8> <target:BE32>` (6 bytes): Arm (`armed != 0`) or disarm secondary pass script (`ulSpawn = target`, `ubSpawnArmed`), executed on a scratch copy of the job (`uwY = 0`) before the main pass.
  - `0xB0 <pad:u8> <routine_id:BE32>` (6 bytes): Invoke engine callback routine (e.g. `fightCreatureDied`).
  - `0xB4 <pad:u8> <target:BE32>` (6 bytes): If owner `swHp <= 0`, jump to `target` and reset `CombatWork` (preserving `ubSpawnArmed`/`ulSpawn`); else `pc += 6`.
  - `0xB8 <pad:u8> <target:BE32>` (6 bytes): Spawn child object job running script `target`.
  - `0xBC <pad:u8>` (2 bytes): Kill job and clear owner `ulActive = 0` (`ubActive = 0`, `ubRunning = 0`).
  - `0xC0 <set_id:u8>` (2 bytes): Select active frame-table list `set_id - 1` (`1` = knights `KN1..KN4`, `2` = creature `0..4`, `3` = map, `4` = effects).
  - `0xC4 <pad:u8> <target:BE32>` (6 bytes): Jump to `target` if current knight's job has the same `ubFlags` facing; else `pc += 6`.
  - `0xC8 <size_flags:u8> <offset:s16> <target:BE32>` (8 bytes): Jump to `target` if owner field at `offset` is zero.
  - `0xCC <size_flags:u8> <offset:s16> <target:BE32>` (8 bytes): Jump to `target` if owner field at `offset` is non-zero.
  - `0xD0 <pad:u8>` (2 bytes): Reset `CombatWork` scratch block (preserving `ubSpawnArmed` and `ulSpawn`).
  - `0xFD` (1 byte): Jump immediately to `ulReturn` (`$8C` continuation).
  - `0xFE` (1 byte): Jump immediately to `ulLoop2` (`$94` loop start).

- **Script Pointer Tables (`mog` `S_0` / `S_1` / `S_4`):**
  - `kActionRight` (`S_4[4130]`) and `kActionLeft` (`S_4[4152]`) are 11-entry `BE16` tables mapping joystick direction codes to byte offsets `(0, 4, 8, ..., 32)` into `ulActionScripts` — they are **not** bytecode PCs.
  - At startup and arena initialization, `mog` `S_0` populates 9-entry `BE32` pointer tables (`ulActionScripts`, `ulHurtScripts`, `ulDamageTable`, `ulDefenseTable`) and 24-entry walk-script tables (`ulWalkScripts`: 3 directional blocks of 4 phases + terminator/padding) in BSS (`S_1`) with `HUNK_RELOC32`-relocated `S_4` bytecode offsets, and assigns per-actor `ulIdleScript` / `ulScript26` pointers directly to `Knight` / creature records.

### 7.3 Core Simulation Struct Layouts (`mog`)
Superseded by §9, which holds the verified actor, inventory, lair, map-node and job layouts.
Some of the offsets this subsection used to list were wrong (§9.9).

### 7.4 Starting Cinematic & `program` Scene Engine (`S_2` / `intro.stile`)
The `program` executable drives the starting cinematic (`introRun`) and ending cinematic (`endingRun`)
using data tables in Hunk 2 (`S_2`, `12,288` bytes) and the `.stile` vertical scroll map:

- **Vertical Panorama Tilemap (`intro.stile`, `960` bytes = `480` `BE16` words):**
  - Defines a `10 × 48` grid of `32 × 25` pixel blocks (`320 × 1200` pixels total) assembled from
    three `320 × 200` `.piv` tile-atlas images (`[bg1a.piv, bg1c.piv, bg1b.piv]`, each holding
    $10 \times 8 = 80$ blocks).
  - For each word `code`: `picture_index = code // 80` (`0..2`), `tile_index = code % 80`,
    `src_row = tile_index // 10`, `src_col = tile_index % 10`.
  - `progress` (`0..1000`) is the vertical scroll offset $Y \in [0, 1000]$ into the `320 × 1200`
    panorama: `progress = 0` shows the full moon in the starry sky (`y = 0..199`), scrolling down
    through the treetop canopy (`bg1c.piv` blocks) to `progress = 1000` (`bg1b.piv` forest floor,
    `y = 1000..1199`).
- **Calligraphy Font (`bold.f`) & `S_2` Text Records:**
  - Character lookup table at `S_2[11744:11848]` (`104` bytes) maps `ord(ch) - 0x20` to a glyph
    frame index in `bold.f`. Frame `0x49` (`73`, `305 × 54`) is the `"MOONSTONE: A Hard Days Knight"`
    title banner blitted at top-left `(9, 60)`.
  - Each glyph advances $X$ by `frame.width - 2` (`advanceBias = -2`).
  - Text linked-list nodes in `S_2` (`14` bytes, `>IHHHI`):
    `(string_offset, x, y, style, next_node_offset)`. When `style & 1`, the line is horizontally
    centered on `320` px (`x = ((320 - line_width) & 0xFFFF) >> 1`).
  - Credit pages on the `progress = 0` moon screen (`S_2` offsets `11884`, `11912`, `11954`,
    `11996`, `12066`, `12024`) animate palette indices `(5, 9, 10, 11, 12)` (`0x0000`, `0x0FED`,
    `0x0DC9`, `0x0B95`, `0x0842`) while keeping the moon backdrop visible.
- **`program` `S_2` Animation Bytecode (`animRun`):**
  - Uses 5 cel slots (`[au1.cel, li1.cel, ha1.cel, dw1.cel, da1.cel]`).
  - `AnimKindA` jobs spawn at `(x=160, y=0, z=100, flags=1)` (unmirrored); `AnimKindB` jobs spawn
    at `(x=120, y=0, z=100, flags=3)` (horizontally mirrored, `flags & 2`).
  - Draw opcodes `0x00..0x1F` (`6` bytes `[op, frame, dy_s8, d3, dx_be16]`):
    `slot = (op & 0x1F) >> 2`. Top-left screen position is `sx = x - dx - width` when mirrored
    (`flags & 2`), else `sx = x + dx`; `sy = y + z + dy`. When `d3 & 0x10`, the cel is also stamped
    permanently into the scene's `shown` background buffer. Multiple draw opcodes execute within a
    single frame until the 2-byte `0xFF <next>` marker (`0xFF 0x00` = end of frame, `0xFF 0xFF` =
    end of script).
  - Control opcodes: `0x80 <u8>` sets/toggles `flags`; `0x84 <u8 mode> <BE32 target>` jumps (`mode=3`)
    or defers jump until end of frame; `0x88 <u8 count>` starts a frame loop; `0xB4 0x00 <BE32 fn>`
    triggers lightning palette flashes (`S_0[3918:3982]` `kFlashA` and `S_0[3982:4046]` `kFlashB`).
  - Foreground occlusion cel `ov1.cel`: frames `0` (`0, 56`) and `3` (`51, 134`) (plus `1` at
    `157, 104` and `2` at `297, 82` in phase 1) are blitted over the playfield after job draws.
- **Story Sequence Order (`introRun`):**
  1. `mindscape` logo (`Mindscape` screen).
  2. Moon backdrop (`intro.stile` at `progress = 0`) with `bold.f[0x49]` title logo and 6 credit pages.
  3. `intro.stile` vertical camera pan from `progress = 0` (moon) to `1000` (`bg1b.piv` forest floor).
  4. Scene `001B` (`bg1b.piv`, speed `8`): 5 staggered druids carrying torches (`S_2[5636]`, `12` frames apart).
  5. Scene `001C` (`bg2.piv` + `ov1.cel` phase 1, speed `8`): 5 staggered druids approaching Stonehenge (`S_2[5056]`, `8` frames apart).
  6. Scene `001A` (`bg3.piv`, speed `6`): Stonehenge circle (`S_2[6996, 7034, 6818, 6594]`), `PAIRS` (`S_2[7078, 7592]`), and central altar (`S_2[8312]`).
  7. Scene `002C` (`bg4.piv` + `S_2[584]`, then `bg5a.piv` + `S_2[2804]`, speed `6`): High Druid raising crescent amulet and invoking the cloud vortex with lightning flashes.
  8. Scene `002D` (`bg2a.piv` + `S_2[4932]` + `ov1.cel` phase 2, speed `6`): Knight drawing broadsword before Stonehenge.
  9. Scene `002F` (`bg3.piv` + `S_2[6996, 7034, 6958, 7554, 7942, 8568, 8582]`, speed `8`): Knight entering the Stonehenge circle.
  10. Scene `002E` (`bg5a.piv` + `S_2[1022]`, speed `6`): High Druid dubbing the kneeling knight.
  11. Caption `00AA` (`message.piv` + `S_2[120]` text list): a one-line caption telling the player that the druids have summoned their finest knights to Stonehenge to be dubbed for the quest.

---

## 8. Palette Architecture (`T2.3`, `T2.8`)

### 8.1 32-Colour Combat Playfield Layout (`mog`)
During combat, the 32-entry 5-bitplane palette (`0..31`) is assembled dynamically by `sceneColors`
and `sceneTail`:
- **Index `0`:** Transparent / background black (`0x000`).
- **Indices `1..5`:** HUD / shared metallic & stone greys from `test` picture 0.
- **Indices `6..8` (Knight Class Tunic/Plume Colours):**
  - Red Knight (Siragner): `6..8` = Crimson ramp.
  - Blue Knight (Galahad): `6..8` = Cobalt blue ramp.
  - Green Knight (Beowulf): `6..8` = Emerald green ramp.
  - Yellow Knight (Sudrey): `6..8` = Gold/amber ramp.
- **Indices `9..14`:** Creature-specific palette slots (loaded per monster type from `mog` tables).
- **Index `15`:** Blood crimson (`0xA00` / `0xC00`), shared by `blo.cel` and wound pixels.
- **Indices `16..28`:** Region scenery colours (`fo` Forest greens/browns, `gl` Glen stones/heather,
  `sw` Swamp murk/teals, `wa` Wasteland ochres/bones), loaded from the region's `test` picture (`1..4`).
- **Indices `29..31`:** HUD text & status bar highlight colours.

### 8.2 VBL Palette Effects (`palVblUpdate`)
Executed every 20 ms (50 Hz VBL):
- **Fade-in / Fade-out (`palFadeIn` / `palFadeOut`):** Steps each 4-bit `R, G, B` nibble toward the
  target colour (`auwCell`) or black (`0x000`) by `±1` per tick over 16 ticks (`320 ms`).
- **Colour Cycling (`palCycle`):** Rotates a contiguous sub-range of palette entries every $N$ VBL
  ticks (used on water, lava, magic auras, and Moonstone glow).
- **Lightning / Hit Flash (`display_fx`):** Temporarily overrides background/sky registers for `1..4`
  VBLs and restores from `auwCell`.

---

## 9. Game State Records (`T11.2`)

Everything the rules read or write lives in a handful of fixed-layout records in `mog`'s BSS and
heap. Offsets are bytes from the start of the record, big-endian, 2-byte alignment. `u8/u16/u32`
are unsigned, `s16` signed. In `sim/` each record is a mutable dataclass with one named integer
field per row below; the offsets matter because fight scripts poke owner fields by offset (§7.2,
opcodes `$A8`, `$C8`, `$CC`) and several tables in `mog` are indexed by them.

Source for this section: the record headers of the reference project, which cite the routine that
proves each offset. Where it disagrees with the earlier §7.3 text of this document, this section
wins (ADR-002); the differences are listed in §9.9.

### 9.1 Pools

| Pool | Count | Record | Notes |
|---|---|---|---|
| Persistent actors | 5 | Actor (132 B) | Slots 0..3 are the four knights, slot 4 is the dragon. All five take part in the daily upkeep. |
| Inventories | 5 + 24 | Inventory (24 B) | One per persistent actor, one loot inventory per lair. |
| Fight pool | 20 | Actor (132 B) | Creatures, thrown daggers and other fight objects. A slot is in use while its first long is non-zero. |
| Jobs | 10 | Job (50 B) | One running script for one actor (§9.6). |
| Lairs | 24 | Lair (20 B) | Overworld encounters (§9.5). |
| Map nodes | 9 | MapNode (6 B) | Fixed places (§9.5). |

### 9.2 Actor record (132 bytes)

One layout serves knights, the dragon, creatures and fight objects.

| Off | Type | Field | Meaning |
|---|---|---|---|
| +0 | u32 | `active` | Non-zero while the slot is in use. Cleared when its job is killed. |
| +4 | u16 | `x` | Fight position, horizontal. |
| +6 | u16 | `height` | Height above the ground (the job's altitude). Knights 0. |
| +8 | u16 | `y` | Fight position, depth. |
| +10 | u8 | `facing` | 1 right, 3 left. Bit 1 mirrors horizontal motion. |
| +11 | u8 | `input_port` | 1 = port 0, 2 = port 1, 4 = no controller (AI). |
| +12 | u8 | `anim_phase` | Walk-cycle phase 0..3. |
| +13 | u8 | `anim_timer` | Countdown reloaded by the fighter step. |
| +14 | u32 | `hit_target` | Actor this one hit this frame (set by the contact pass). |
| +18 | u32 | `attacker` | Actor that hit this one this frame. |
| +22 | u32 | `idle_script` | Script taken when nothing else is chosen. |
| +26 | u32 | `after_hit_script` | Alternate script, used after a successful hit. |
| +30 | u32 | `hurt_scripts` | Table of 9 scripts indexed by the **attacker's** action. |
| +34 | u32 | `action_scripts` | Table of 9 scripts indexed by the actor's own action. |
| +38 | u32 | `frame_list` | The 5-entry cel list handed to the job. |
| +42 | u32 | `damage_table` | Base damage per own action, read when this actor hits. |
| +46 | u32 | `walk_scripts` | Indexed by `anim_phase * 4 + direction block` (0 sideways, 32 up, 64 down). |
| +50 | u32 | `defence_table` | Per attacker action: the own action that defends against it. |
| +54 | u32 | `kind` | 0..3 the four knights, 4 computer-controlled knight, 5 dragon. |
| +58 | u16 | `box_min_x` | Union screen box of this frame's draws (§4.2), left edge. Zero means the actor drew nothing that blocks. |
| +60 | u16 | `box_max_x` | Right edge. |
| +62 | u16 | `input` | Low byte bits: 0 right, 1 left, 2 down, 3 up, 4 fire. |
| +64 | u16 | `action` | Current action as a table byte offset: 0, 4, 8, ... 32 (§9.7). |
| +66 | u16 | `tile_x` | Map tile column (§11). |
| +68 | u16 | `tile_y` | Map tile row. |
| +70 | u8 | `strength` | Added to melee damage. Starts at 1. |
| +71 | u8 | `constitution` | Feeds maximum hit points. Starts at 1. |
| +72 | u8 | `endurance` | Feeds the move budget. Starts at 1. |
| +73 | u8 | `lives` | Starts at 5. One is lost per lost fight. |
| +74 | u16 | `gold` | Starts at 10. Compared as a signed word by the shops. |
| +76 | u8 | `daggers` | Starts at 10. |
| +77 | u8 | `type` | Behaviour type, a multiple of 4 that indexes the handler tables (§9.8). |
| +78 | u16 | `progress` | Points earned by victories, spent on stats at the temple. |
| +80 | s16 | `hp` | Current hit points. Starts at 20. `$FFFF` marks dead. |
| +82 | u8 | `frog_days` | Curse countdown; the knight's turn is skipped while non-zero. |
| +83 | u8 | `recency` | Wizard-visit recency, minus 10 per day down to 0. `$FF` is the value a fresh or computer knight carries and is never decayed. |
| +84 | s16 | `hp_max` | Derived (§9.3). |
| +86 | u8 | `derived_endurance` | Derived (§9.3). The move budget is this value shifted left by 4. |
| +88 | u32 | `sword` | Item code `$16`..`$19` (§9.7). Starts at `$16`. |
| +92 | u32 | `armour` | Item code `$1B`..`$1E`. Starts at `$1B`. |
| +96 | u32 | `inventory` | Reference to this actor's Inventory. |
| +100 | u32 | `engaged_with` | The actor this one is after (dragon's target, a computer knight's opponent). Cleared at turn change. |
| +104 | u8 | `behaviour_flags` | Bit 7 is the block latch; other bits belong to the script handlers. |
| +105 | u8 | `behaviour_flags2` | Bits 0 and 2 belong to the script handlers. |
| +106 | u16 | `cooldown` | AI action cooldown. Knight steps use the high byte, creature steps the whole word. |
| +108 | u32 | `name` | Reference to the NUL-terminated name. |
| +112 | u16 | `box_min_y` | Union screen box, top edge. |
| +114 | u16 | `box_max_y` | Bottom edge. |
| +116 | u16 | `reach_x` | AI: stops and attacks within this horizontal distance. |
| +118 | u16 | `too_close_x` | AI: backs away when nearer than this. |
| +120 | u16 | `depth_reach` | AI: depth tolerance for "same lane". |
| +122 | u16 | `hit_x` | Contact point of the last hit. |
| +124 | u16 | `hit_y` | Contact point of the last hit. |
| +126 | u16 | `map_x` | Overworld position, one pixel per step. |
| +128 | u16 | `map_y` | Overworld position. |
| +130 | u8 | `life_loss` | Non-zero makes the next day turnover take a life. |

Bytes +87 and +131 are never used by the original.

### 9.3 Derived values

All arithmetic wraps at the stated width.

1. **Maximum hit points** (16-bit): `constitution * 10 + 20 * count(slot $06) + armour bonus + 10`,
   with armour bonus 0 / 10 / 20 / 30 for codes `$1B`..`$1E`. `hp` is then clamped down to it
   (signed compare). The same routine forces `sword` to `$19` when inventory slot `$04` is non-zero.
2. **Derived endurance** (8-bit): `endurance * 2 + armour bonus + 4`, with armour bonus 2 for
   `$1C` and `$1E`, otherwise 0.
3. **Overnight** (per actor, all five): unless `recency` is `$FF`, `recency` drops by 10 (floor 0)
   and `frog_days` by 1; then, if `hp < hp_max`, `hp += ((hp_max - hp) >> 2) | 1`, clamped to
   `hp_max`.
4. **Defeat**: a fighter whose `hp <= 0` after a fight gets `hp = hp_max` and loses one life.

### 9.4 Inventory (24 bytes)

Twelve 2-byte slots. The count is the byte at the even offset; the odd byte is unused. Slots
`$14` and `$16` are bit masks, every other slot is a count that wraps at 256.

| Slot | Role | Evidence |
|---|---|---|
| `$00` | Rest item: clears `life_loss`, restores `hp` to `hp_max`; at full health it adds a life instead (cap 5) | item-use handler |
| `$02` | Marks the knight's map position for a later return | item-use handler |
| `$04` | Sword of sharpness: forces `sword = $19` | stat recalculation |
| `$06` | Protection item: +20 `hp_max` per piece | stat recalculation |
| `$08` | Each piece shifts incoming damage right by one bit | damage routine |
| `$0A` | Gamble on the turn: doubles the move budget on a roll above 10 (roll range 0..100), else halves it and sets the bad-luck flag | item-use handler |
| `$0C` | Teleport gamble (good on a roll above 15) | item-use handler |
| `$0E` | Opens the exchange sheet 8 | item-use handler |
| `$10` | Opens the hand-over sheet 11 | item-use handler |
| `$12` | Scroll that lets the second knight avoid a duel; a luck gamble (good on a roll above 10) | duel prologue |
| `$14` | Keys, bits 0..3; `$0F` is all four | valley test |
| `$16` | Moonstones, bits 0..3, matched against the moon frame | damage and Stonehenge tests |

The display names of all slots are given in §12.6.

**Transfer order.** After a fight the winner takes from the loser's inventory in this slot order:
`$16, $14, $04, $06, $0E, $12, $08, $0C, $10, $00, $02, $0A`. If the loser still has lives, only
the first slot he owns moves (one piece, or the whole mask for `$16` / `$14`); if nothing moved,
the loser's gold is halved and the winner gets the other half. If the loser has no lives left,
everything moves. A winner of type `$14` takes the gold first.

### 9.5 Lair (20 bytes) and MapNode (6 bytes)

**Lair** (24 records, each with its own loot inventory):

| Off | Type | Field | Meaning |
|---|---|---|---|
| +0 | u32 | `loot` | Reference to the loot Inventory. |
| +4 | u16 | `handler_offset` | Byte offset into the arena set-up table: which creature fight this lair starts (§10). |
| +6 | u16 | `creatures_left` | Written back after a fight: creatures still to come. |
| +8 | u16 | `gold` | The lair's gold. Cleared at set-up, filled by gold rolls, emptied on the loot sheet; non-zero keeps the lair on the map (§12.24 corrects the earlier name `occupied`). |
| +10 | s16 | `map_x` | Drawn position. Negative hides the lair. |
| +12 | s16 | `map_y` | Drawn position. |
| +14 | u16 | `param14` | Per-lair arena parameter, copied into the fight set-up. |
| +16 | u32 | `param16` | Per-lair arena parameter, copied into the fight set-up. |

Four lairs receive one key each (masks 8, 4, 2, 1) at campaign start.

**MapNode**: `s16 id, u16 x, u16 y`; the table of nine ends with a negative id.

| Id | Place |
|---|---|
| `$15`..`$18` | Home village of knight kind 0..3; each counts only for its own knight |
| `$19` | Highwood (town A) |
| `$1A` | Waterdeep (town B) |
| `$1B` | Stonehenge |
| `$1C` | Valley of the Gods |
| `$1E` | The wizard's tower |

The arrival list (§11) also uses kind 1 for a living knight, `$21` for a knight out of lives and
2 for a lair.

### 9.6 Job (50 bytes) and the active-fight block (22 bytes)

**Job**: one script running for one actor.

| Off | Type | Field | Meaning |
|---|---|---|---|
| +0 | u8 | `active` | Slot taken. |
| +1 | u8 | `running` | Script running. |
| +2 | u32 | `script` | Current script position. |
| +6 | u16 | `x` | Copied from the owner's `x` at spawn. |
| +8 | u16 | `height` | Copied from the owner's `height`. |
| +10 | u16 | `y` | Copied from the owner's `y`. |
| +12 | 10 B | `draw_state` | Screen position and size of the last draw. |
| +22 | u8 | `facing` | Copied from the owner's `facing`. |
| +24 | u32 | `owner` | The actor. |
| +28 | u32 | `frame_list` | The owner's cel list. |
| +32 | u8 | `type` | The owner's `type`. |
| +36 | u32 | `work` | 36-byte scratch block (loops, motion, armed jumps, second script). |
| +40 | u32 | `attack_list` | 80-byte list of this frame's attack draws. |
| +44 | u32 | `hurt_list` | 80-byte list of this frame's hurt draws. |
| +48 | u16 | `paused` | Toggled by the pause routine. |

**Active-fight block**:

| Off | Type | Field | Meaning |
|---|---|---|---|
| +0 | u32 | `current` | The acting knight / first fighter. |
| +4 | u32 | `opponent` | The second fighter; absent in a creature fight. |
| +8 | u8 | `fight_active` | 1 while the fight loop runs. |
| +14 | u16 | `player_count` | Number of human players. |
| +16 | u8 | `end_timer` | Post-fight delay in fight frames, decremented as a byte. |
| +18 | u16 | `moon_frame` | 45..49, the picture frame of the moon. |
| +20 | u16 | `moon_subcount` | 0..3; one step per round, wrapping advances the day. |

The moon frame for lunar index 0..7 is read from an 8-byte table in `mog` `S_4`; the sequence it
holds is 45, 47, 46, 48, 49, 48, 46, 47. Frame 45 pairs with moonstone bit 1, frame 46 with bits 0
and 3, frame 49 with bit 2.

### 9.7 Actions and equipment codes

`action` values (table byte offsets): `$00` idle; `$04`, `$08`, `$14`, `$18` strikes; `$0C` dagger
throw; `$10` defend; `$1C` block; `$20` heavy strike (damage doubled).

Swords: `$16` long (+0 damage), `$17` broad (+2), `$18` claymore (+3), `$19` sharpness (+5).
Armour: `$1B` padded, `$1C` mail, `$1D` plate, `$1E` battle.

### 9.8 Behaviour types (`type`, +77)

| Value | Actor |
|---|---|
| `$00` | Flyer ("Be") |
| `$04` | Mudmen |
| `$08` | Guardian demon |
| `$0C` | Knight in a fight |
| `$10` | Knight on the map |
| `$14` | Dragon / black-knight fight type |
| `$18`, `$1C` | Trogg with axe (two variants) |
| `$20` | Trogg with spear |
| `$24` | Ratmen |
| `$28` | Dragon in flight on the map |
| `$2C` | Object of the dragon arena |
| `$30` | Balok |
| `$34` | Thrown dagger |
| `$40` | Troll |

### 9.9 Corrections to earlier text

- §7.3 gave `hp_max` at +82, `action` at +118 and `type` at +130. The verified layout has
  `hp_max` at +84, `action` at +64 and `type` at +77. Its bounding box offsets (+58, +60, +112,
  +114) were right and are kept.
- §7.3 gave the job's facing at +12, its type at +13 and its work block at +28. The verified
  layout has them at +22, +32 and +36, with the frame list at +28.
- The creature-type keys that `assets/tables.py` reports (24, 28, 32, 36, ...) are the decimal
  values of §9.8. The archetype mapping in `app/combat_bridge.py` assigns several of them to the
  wrong creature: it treats 24 as the spear Trogg (it is an axe Trogg), 32 as the Ratmen (it is
  the spear Trogg), 36 as the Troll (it is the Ratmen), 0 as Balok (it is the flyer), 44 as the
  flyer (it is a dragon-arena object) and 64 as the Guardian (it is the Troll). T11.7 replaces it.

---

## 10. Fights (`T11.3`)

A fight is a loop over at most ten jobs (§9.6). Each job runs one fight script (§7.2) for one
actor (§9.2). The script produces the actor's sprites, its attack and hurt lists and its motion;
a per-type **handler** chooses the next script. There is no other fighter state: no timers
outside the record, no separate animation table. `sim/` implements this section; `app/` only
reads the draw lists.

Sources: the reference project's fight loop, arena set-up, script engine, contact and damage
code. Where this section names a number that the original holds as data or as an immediate in
`mog` code (hit points, reach, colours, spawn lists, step sizes, the wave cut table), the
implementation reads it from the user's `mog` (§10.15) and the number is given here only to pin
the behaviour.

### 10.1 Ways into a fight

| Entry | Trigger | Set-up | After the fight |
|---|---|---|---|
| Creature lair | Node menu on a lair | Arena of the lair's `handler_offset` (§10.3) | Lost: temple sheet. Won: +1 progress, loot sheet. `creatures_left` is written back to the lair. |
| Knight meets knight | Node menu on another knight, or a computer knight's arrival | Both knights get type `$0C`; the first uses port 1 (`input_port` 2), the second port 0 (`input_port` 1); a computer knight keeps its own type | Meeting sheet for the winner, temple sheet when both lost (§12) |
| Dragon | The dragon touches the current knight on the map | Dragon arena | Hoard sheet on a win; the turn is used up |
| Valley guardian | The valley with all four keys | Guardian arena (§10.3, last row) | §12 |
| Practice | Title menu | Knight 0 on port 1 against knight 1 on port 0, player count forced to 2 | Back to the title |

With the travel mark set (§11) a lair is looted without a fight.

### 10.2 Common set-up

Every creature arena starts the same way, in this order:

1. Show the next line of the loading-text cycle on `message.piv`.
2. Build the arena picture for the current **region code** (0 glen, 4 forest, 8 swamp, `$C`
   wasteland; §4.3 composes it, where the arena file prefixes `gl`, `fo`, `sw`, `wa` are its
   region indices 1, 0, 2, 3) and its obstacle table. The **low edge** is the largest `y` of
   the obstacle records, starting from 30.
3. Free all 20 fight-pool actors, all jobs and all dagger trajectories, and clear the brawler's
   gloat latch (§10.20).
4. Place the attacker: the current knight becomes the first fighter at `x = 250`, `height = 0`,
   `y = 100`, facing left (3); its script tables are set to the knight tables (§10.9) and a job
   starts with the knight's **enter** script.

### 10.3 The creature arenas

Nine set-up routines, reached through a table that a lair's `handler_offset` indexes. Each one
sets, in this order: optional region override, common set-up (§10.2), creature cel set and sound
bank, pokes into the knight's script tables, the wave cells, wave scaling (§10.6), the first
wave (§10.5), the fight palette for its scene code (§10.13).

| Arena | Creature type | Cel files (creature frame list) | Alive at once / in all | First wave | Next creature | Scene code | Notes |
|---|---|---|---|---|---|---|---|
| Flyer | `$00` | `be1.c`, `be2.c` | 1 / 3 | whole list | alternating | `$00` | Three pokes: knight action 28 and hurt entries 28 and 32 |
| Mudmen | `$04` | `Mudmen1.CEL`, `Mudmen2.CEL` | 1 / 2 | one entry | alternating, one at a time | `$04` | Forces region code 8 |
| Trogg, axe | `$18` | `TROGGAxe1.CEL`, `TROGGAxe2.CEL` | 1 / 3 | whole list | alternating | `$18` | |
| Trogg, axe (second kind) | `$1C` | same | 1 / 3 | whole list | alternating | `$18` | Shorter reach |
| Trogg, spear | `$20` | `TROGGSpear1.CEL`, `TROGGSpear2.CEL` | 1 / 3 | whole list | alternating | `$20` | Pokes knight actions 16 and 28 |
| Ratmen | `$24` | `RATMEN1.CEL`, `RATMEN2.CEL` | 2 / 2 | whole list | alternating | `$24` | One extra creature object is on the field from the start; pokes knight actions 16 and 28 |
| Balok | `$30` | `Balok1.CEL`, `Balok3.CEL`, `Balok2.CEL`, plus `KN5.ob` | 1 / 2 | whole list (one entry at `x = -60`, facing right) | one at a time | `$30` | Pokes the target's hurt entry 8 |
| Troll | `$40` | `TROLL1.CEL`, `TROLL2.CEL`, plus `KN5.ob` | 1 / 1 | one entry | alternating | `$40` | Never more than two alive; pokes the target's hurt entry 8 |
| Guardian | `$08` | `Demon1.CEL`..`Demon4.CEL` | 1 / 1 | the guardian itself | none | `$08` | Own routine: default obstacle table, pokes the knight's hurt entry 32, reserves a second record |

The dragon fight loads `DRAGON1.CEL`, `DRAGON2.CEL`, `DRAGON5.CEL` plus `KN5.ob` and uses scene
code `$14`. Knight duels load `HE1.ob`..`HE3.ob` and use scene code `$0C`.

A creature cel set is tagged with its type so a repeat fight skips the load; Mudmen, dragon and
guardian always reload. The knights' frame list holds `KN1.ob`, `KN2.ob`, `KN3.ob` in slots 0..2
and `KN4.ob` (the weapon set, the one with a hit set) in slot 3; slot 4 takes `KN5.ob` in the
fights that load it. T11.7 confirms slot order per creature from the loader when it decodes the
definitions.

A **poke** replaces one entry of the knight's action or hurt script table for this fight, which
is how a knight gets creature-specific reactions (being grabbed, being carried).

### 10.4 Creature record initialisation

When a creature is spawned, its type's initialiser fills the new actor: `action_scripts`,
`hurt_scripts`, `damage_table`, `walk_scripts`, `frame_list` (the creature list), `type`,
`input_port = 4`, `idle_script`, `after_hit_script`, `reach_x`, `too_close_x`, `depth_reach`,
`hp` and `hp_max`. A creature whose AI plays fixed scripts by itself leaves `action_scripts`
unset.

Examples that pin the model: the first axe Trogg has reach 100, too-close 90, depth 5 and 20 hit
points; the second kind reach 70 and too-close 65. The Ratmen have 5 hit points, reach 40,
too-close 30, and their initialiser also rewrites two entries of their damage table; at moon
frame 45 they get 7 hit points and stronger entries, at frame 49 they get 12.

### 10.5 Spawning

- **Spawn lists** are records of four words `{x, height, y, facing}` in `mog` data. Spawning one
  creature takes the next free fight-pool actor, copies the four words, runs the initialiser and
  starts a job with the actor's idle script. `alive_now` goes up by one.
- **Entry lane.** Starting a job overrides the actor's `y` with one of three depth lanes. A
  counter steps 1, 2, 3, 1, ... (increment, mask with 3, skip zero). With `E` the arena's low
  edge and `g = 200 - E` (16-bit):
  - counter 3: `E + (g >> 1) - 47`
  - counter 2: `E + (g >> 2) - 47`
  - counter 1: `E + (g >> 1) + (g >> 2) - 47`
- **First wave.** "Whole list" spawns `max_alive` consecutive entries. "One entry" uses the
  alternating routine once.
- **Alternating** flips a toggle and spawns the first or the second entry of its list, so
  creatures enter from alternate sides.
- The Ratmen arena additionally spawns one object of type `$28` at the first lane before the
  wave, running a fixed script.

### 10.6 Wave size

The arena sets `max_alive` and `total`; scaling then adjusts them for the current knight:

1. If `strength > 3`: `max_alive += 1`.
2. If `hp_max >= 30`: `total += 1`. If `hp_max >= 60`: `max_alive += 1`. If `hp_max >= 90`:
   `total += 1`.
3. In a lair fight `total` becomes the lair's `creatures_left`.
4. Arenas that spawn one at a time force `max_alive = 1`; the Troll caps it at 2.
5. If `total > 0`: compute the knight's damage for action 8 (§10.11) as a class 0..7 and look up
   a signed cut in an 8 x 8 table (creature row by damage class) in `mog` data. `total - cut` is
   stored only if it is positive.

### 10.7 The fight frame

One fight frame lasts 6 VBLs (§7.1). Before the first frame: action latch, warning jobs and key
state are cleared, `fight_active = 1`, and the creature jobs' pause flags are toggled. Each
frame, in this order:

1. **Job pass** (§10.8): handlers choose scripts.
2. **Script tick**: every running job executes its script up to the next frame marker (§7.2),
   in ascending depth order, filling draw, attack and hurt lists and the actors' bounding boxes.
3. Flip the screens.
4. **Contact pass** (§10.10): links attackers and victims for the next job pass.
5. Draw pass.
6. Low-health warning (§10.14).
7. End test: while `fight_active`, a first fighter with negative `hp` clears `fight_active` and
   sets `end_timer = 50`. The pause key waits for another key.
8. Wait for the frame budget.

The loop runs while `fight_active` is set, then keeps running frames while `end_timer` counts
down to zero (a byte decrement, so a timer of 0 would run 256 frames). Afterwards: defeats are
settled (§9.3), the turn's move budget is marked spent, the bad-luck flag is cleared, all jobs
are freed, and a lair fight writes `total` back to the lair's `creatures_left`.

### 10.8 Job pass

For every active job: if its owner has neither a `hit_target` nor an `attacker` link **and** its
script is still running, nothing happens. Otherwise the handler for the job's type is called
with the owner and returns one of:

- **keep**: leave the job alone;
- **end**: free the job and clear the owner's `active`;
- **a script** plus position and facing: the job restarts on that script at that position.

So a handler runs when a blow landed or was taken, or when a script reached its end marker.
Every animation is one complete move.

Handlers by type: knight `$0C`; flyer `$00`; brawler `$18`, `$1C`, `$20`; caster `$24`; dragon
`$14`; dragon-arena object `$2C`; drake (Balok) `$30`; dagger `$34`; idle object `$28`; snatcher
(Mudmen) `$04`; guardian `$08`; stalker (Troll) `$40`; computer knight `$10`.

### 10.9 The knight handler

Default next script: `idle_script`. Then, first match wins:

1. `attacker` set: the hurt reaction for the attacker's type (§10.11).
2. `hit_target` set: the hit reaction (carry on, stop, or be hurt back by a dragon in flight).
3. Read the joystick word of the knight's port into `input`; `action = 0`. No input: idle.
4. If this knight is the confused one (a failed scroll gamble), swap up with down and left with
   right.
5. **Fire held**: look up the direction bits (fire bit removed) in the action table for the
   knight's facing (`kActionRight` / `kActionLeft`, §7.2); the result is the new `action`, and
   the next script is `action_scripts[action]`.
6. **Otherwise walk.** `anim_phase = (anim_phase + 1) & 3`. Step sizes come from three 4-entry
   tables in `mog` indexed by the phase: up moves `y` by minus the up step, down by the down
   step; right sets facing 1 and moves `x` by the side step, left sets facing 3 and moves by its
   negative. Then, in order: the movement probe (§10.10) clears blocked direction bits, the arena
   clip clears more, the obstacle probe clears more. The remaining bits are applied (up, down,
   left, right; the direction block is 32 for up, 64 for down, and 0 as soon as a sideways bit
   moved). If nothing moved, the phase is restored and the idle script plays. Otherwise the block
   latch (bit 7 of `behaviour_flags`) is cleared and the next script is
   `walk_scripts[direction block + phase * 4]`.

The knight tables set at arena entry are the tables of §7.2 (idle, after-hit, nine actions, nine
hurt reactions, walk blocks, damage and defence tables).

### 10.10 Contact, movement probe, clipping

**Contact pass.** Clear every actor's `hit_target` and `attacker`. For each active job, for each
entry of its attack list, test against each hurt entry of every *other* active job whose depth is
within 10 (§4.1), using the contact test of §4.2. The first hit stores the victim in the
attacker's `hit_target`, the attacker in the victim's `attacker` and the contact point in the
victim's `hit_x` / `hit_y`; the scan then moves to the next job (its remaining attack entries are
not tested). Finally both lists of every job are emptied. An attacker cel without a registered
hit set never hits.

**Movement probe.** For a mover with horizontal step `s` and facing `f`, start with all four
directions allowed. For every other active job's owner with a non-zero `box_min_x` and
`hp > 0`:

- If the two depths are within 10, and the mover is not beyond the other on its facing side (the
  mover's `x` is not greater when facing right, not smaller when facing left): if the mover's
  x-box shifted by `s` overlaps the other's x-box and the y-boxes overlap, the facing direction
  is blocked.
- If the depth difference is at most 20 and both the unshifted x-boxes and the y-boxes overlap:
  down is blocked when the mover is behind (smaller depth), up when it is in front.

Interval overlap is the original's interval test (§10.17, contact test step 2).

**Arena clip.** Left is cleared when `x + 25` (facing right) or `x - 25` (facing left) is below
10, right when it is above 320, down when `y + 9 > 155`, up when `y < 30`.

**Obstacle probe.** Every wall segment (§4.3) the mover would touch clears the matching
direction bit; the exact test is §10.19.

### 10.11 Damage and blocking

**Damage dealt** by an attacker's current action: the entry of its `damage_table` for `action`,
plus `strength`, plus the sword bonus (§9.7); doubled for action `$20`; doubled once more when
the attacker holds a moonstone matching the moon frame (bits 0 or 3 with frame 46, bit 1 with 45,
bit 2 with 49). 16-bit arithmetic.

**Protection:** the result is shifted right by the defender's count in inventory slot `$08`
(shift count modulo 64; 16 or more gives 0).

**Blocking:** the defender's `defence_table` gives, per attacker action, the action the defender
must be in to stop it. A defender in the `$1C` stance blocks once and sets the block latch; the
latch is released by walking. Two fighters facing each other in the required action parry, with
the clang sound.

**Hurt reaction of a knight.** A table with one row per attacker type decides whether the block
check runs, how hit points are taken (the attacker's computed damage, or a flat number for the
flyer, drake, spear Trogg, stalker, guardian and dragon), side effects, and which script plays:
normally `hurt_scripts[attacker's action]`, for some creatures a fixed script. The rows are
specified with their handlers in §10.16.

**Death.** Nothing outside the scripts kills an actor. Every hurt script contains the
"jump if owner dead" opcode, which branches to a death sequence that calls the death-accounting
routine (§10.12) once and ends with the kill opcode. A hurt-table entry of zero makes the handler
return **end**: the actor is freed without death accounting.

### 10.12 Death accounting and the end of a fight

Scripts call four fight-level routines through the engine-call opcode:

- **A fighter died.** If the first fighter's `hp <= 0`, end the fight. Otherwise `alive_now -= 1`
  and `total -= 1`; if `total <= 0` and `alive_now == 0`, end the fight; else, while
  `alive_now != max_alive` and `total > 0`, call the arena's next-creature routine.
- **End the fight.** If still active: `end_timer = 35`, `fight_active = 0`.
- **Mark the first fighter dead:** `hp = $FFFF`.
- **Pause all** creature jobs (used by grab sequences).

A second fighter's death is therefore signalled only through scripts; the end test of §10.7
looks only at the first fighter.

### 10.13 Fight palette

Built when an arena finishes its set-up, from the 32 colours of the region's object sheet
(§4.3: `test` picture 0 for glen and forest, 2 for swamp, 3 for wasteland):

1. Copy the sheet's palette.
2. Entries 9..15 take the seven colours of the **scene code** (§10.3). Scene `$0C` (a duel)
   instead puts the second fighter's three class colours at 9..11. Scenes `$18` and `$20` pick
   one of three sets by region code (`$C`, 0, others). Scene `$14` (dragon) also sets 29..31.
   Scene `$40` sets six colours. Scene `$08` (guardian) takes 23 colours from a table.
3. Fade out, copy the clean background to both screens.
4. Entries 6..8 take the first fighter's class colours, by `kind`: 0 blue, 1 orange, 2 green,
   3 red, anything else the dark black-knight set.
5. Unless the scene is `$08`: region codes 8 and `$C` set entries 1..4 to a fixed four-colour
   set; then entries 16..28 take the 13 colours of the region's table (one table each for 0, 4,
   8, `$C`).
6. Entry 0 is black. Entry 15 is blood red unless the scene is `$14`.
7. Fade in to the result.

Entry 5 and, outside the dragon scene, 29..31 always stay the sheet's own. The four region
tables hold the same 13 colours as entries 16..28 of the region backdrop pictures (`test`
pictures 7, 4, 6, 5 for codes 0, 4, 8, `$C`), which is how the code-to-region order above was
established and is what the real-disk test checks.

### 10.14 On-screen feedback

The fight has no bars, numbers or score. When the first fighter's `hp` is 10 or less, three
palette jobs start pulsing entries 6, 7 and 8 between their colour and a warning colour of the
knight's class. In a duel (arena kinds `$0C` and `$10`) the opponent's low health pulses entries
9, 10 and 11 the same way. Each pair of warnings starts once per fight.

### 10.15 Reading the fight data from `mog`

Script tables, spawn lists, step tables, region palette tables and the wave cut table are data
in `mog` and are read by offset. Hit points, reach, alive / total counts, scene colours and
class colours are **immediates inside 68k code** in hunk 0. They are read as operands at known
positions:

- The decoder identifies `mog` by the CRC-32 of its code hunk (hunk 0) and holds, per known
  version, the hunk offsets of each routine's operands (positions, not values). The first such
  decoder is `assets/fight_palette.py`.
- Each operand read checks the instruction word in front of it; a mismatch raises an error
  naming the routine, rather than returning a guess.
- Relocated longs (script addresses) are resolved through the hunk's relocation table to an
  offset in the target hunk.

An unknown CRC is an error with a message that names the supported versions.

**Two ways of reading code (T11.7).** `assets/mog_code.py` provides both: a single operand at a
known position with its instruction word compared, and a **store run**, which executes a bounded
range of a table-filling routine. A store run understands only `LEA abs.l,A0/A1/A4`,
`MOVE.L #imm,d16(A0/A1/A4)`, `MOVE.W` / `MOVE.B #imm,d16(A1)`, `MOVE.L #imm,(A0)+` and `(A0)`,
`ADDA.L #imm,A0`, `MOVEQ` into D0 / D7 with `DBRA` on the same register, and
`MOVE.L (A1)+,(A0)+`. Any other instruction is an error. The word at the end of the range must be
the expected terminator. A creature initialiser runs with A1 pointing at an empty record, and
its writes are the record fields of §10.4.

**Table extents.** The original fills every BSS table through its own `LEA table,A0`, and some
tables are shorter than their nominal size: the Mudmen's and the Troll's walk tables have one
block of four phases, not three. A table therefore ends at the nominal count (9 for the action,
hurt, damage and defence tables, 24 for walk tables) or at the next `LEA` target, whichever comes
first. A walk table is three blocks of eight longs: four phase scripts, a zero, and three unused
longs.

**What the decoder found** (`assets/fight_defs.py`, version `$FA43E5B2`):

| Arena | Creature list slots 0..4 (`*` = with hit set) | Knight slot 4 | Sound bank |
|---|---|---|---|
| Flyer | `be1.c`*, `be2.c` | | `Be.a` |
| Mudmen | `Mudmen1.cel`*, `Mudmen2.cel` | | `Mu.a` |
| Trogg, axe (both kinds) | `TroggAxe1.cel`, `TroggAxe2.cel`* | | `Tr.a` |
| Trogg, spear | `TroggSpear1.cel`, `TroggSpear2.cel`* in slots 1..4 | | `Tr.a` |
| Ratmen | `Ratmen1.cel`*, `Ratmen2.cel` | | `Ra.a` |
| Balok | `Balok1.cel`*, `Balok3.cel`, `Balok2.cel` | `Kn5.ob` | `Ba.a` |
| Troll | `Troll1.cel`*, `Troll2.cel`* | `Kn5.ob` | `To.a` |
| Guardian | `Demon2.cel`*, (unchanged), `Demon3.cel`*, `Demon4.cel`, `Demon1.cel` | | `Gu.a` |
| Dragon | `Dragon1.cel`*, `Dragon2.cel`*, (unchanged), (unchanged), `Dragon5.cel`* | `Kn5.ob` | `Dr.a` |
| Duel (type tag `$0C`) | `He1.ob`, `He2.ob`, `He3.ob`, `kn4.ob`* | | |

The knight loader fills the knight list with `kn1.ob`..`kn4.ob` (`kn4.ob` with its hit set), all
five effect slots with `blo.cel`, and loads `kn.a`. Handler offsets 12, 16 and 56 of the arena
table lead to the duel. The frame-set lists of `$C0` are, in order, the knight, creature, map and
effect lists.

Quirks of the original that are decoded as they are:

- The Troll's hurt table is filled with eight copies of the table's own address, not with
  scripts. The decoder reports these entries as "not a script" (−1). The Troll's handler
  (T11.10e) must not start them.
- Balok's initialiser writes `facing = 1` and no `input_port`, no script tables and no walk
  table. The Guardian's writes `hp` but not `hp_max`. Both handlers play fixed scripts.
- The dragon arena pokes the knight's hurt entries 4, 8, 20 and 32. Its two extra actors are
  dragon-initialised records of type `$2C` at (5, 0, 80) and (5, 0, 120).
- The Ratmen's idle object takes the creature list through `LEA list,A2` in front of the spawn
  call.
- `$AC` with a zero "armed" byte disarms the second script, and its address field is zero and
  not relocated. A script walker must not follow it.
- The Balok arena's whole-list first wave can only reach its single entry, because Balok always
  spawns one at a time. The entries after it in `mog` are other data.

Acceptance check: `tests/adf/test_fight_defs_adf.py` walks every script reachable from each
creature, arena object, knight table and poke, following every branch of §7.2, and asserts that
every draw names a loaded slot and a frame inside that cel. On the supported version none fails,
so no slot or frame needs remapping.

### 10.16 Per-handler specifications

The knight's hurt-rule rows and each creature handler are specified in this subsection by the
task that implements them (T11.9, T11.10a to T11.10i), before the code:

| Handler | Types | One-line behaviour | Task |
|---|---|---|---|
| Knight hurt rules | victim `$0C` | One row per attacker type (§10.11) | T11.9 |
| Brawler | `$18`, `$1C`, `$20` | Walks up to the target and picks one of three attacks by distance; the spear type thrusts (§10.20) | T11.10a |
| Caster | `$24` | Keeps its distance, throws daggers, grabs the first fighter and drops or slams it | T11.10b |
| Snatcher | `$04` | Appears beside the first fighter, walks up, grabs and carries it off | T11.10c |
| Flyer | `$00` | Crosses the arena between `x = -50` and `380` at the target's depth | T11.10d |
| Stalker | `$40` | Follows the first fighter and strikes when in reach | T11.10e |
| Drake | `$30` | Pounces in a dagger-style arc, grabs, flings | T11.10f |
| Guardian | `$08` | Lunge, strike or jump by distance, then a grab | T11.10g |
| Dragon and its object | `$14`, `$2C` | Walk, then melee or breath, or a flight arc, in two phases | T11.10h |
| Computer knight | `$10` | Chooses between guarding and attacking by distance, last action and odds | T11.10i |
| Dagger | `$34` | Runs its script along a trajectory until it leaves the screen or hits | T11.9 |
| Idle object | `$28` | Clears its links and keeps its script | T11.8 |

The knight's rows, the knight handler in exact form, the dagger and the hop are §10.19; the
brawler and the approach helpers are §10.20.

### 10.17 The script engine (`T11.8`)

This subsection is the exact model `sim/` implements. Where it differs from §7.2, this text wins
(the differences are listed at the end). Every number is 16-bit and wraps unless stated.

**Coordinates.** A job keeps three words: `x`, `height` and `depth`. They mirror the owner's `x`
(+4), `height` (+6) and `y` (+8, the depth). `height` is 0 on the ground and **negative in the
air**. A draw lands on screen row `depth + height + dy`. The sort key is `depth`.

**Job.** Besides the §9.6 fields, a job carries its own work block, its attack list and its hurt
list. These belong to the job record and move with it when the table is sorted. The ten job
records stay in a fixed table whose order is the order of every pass and of every owner lookup.

**Work block.** Loop 1 (count, active, return position), loop 2 (count, active, return position),
a deferred jump (target, armed), the second script (armed, script), the motion block (flags,
active, steps left, vertical speed and limit, horizontal speed and limit, return position), and
an unused parameter byte. Resetting it clears everything except the second script's armed flag
and script.

**One engine tick**, in this order:

1. **Sort**: bubble the ten records by `depth`, an unsigned compare; two neighbours swap while the
   later one is smaller; repeat until a sweep swaps nothing. Inactive records take part.
2. **Second-script pass**: for each record in table order that is active, not paused and has its
   second script armed, run a *copy* of the job whose `height` is 0, whose script is the second
   script and whose work block is the single scratch work block shared by all jobs. The scratch
   block is never cleared, between jobs or between ticks. Changes to the copy are thrown away;
   writes to the owner (pokes, kill, write-back) are real. The list cursors and the bounding box
   cells are **not** reset for this pass: the copy appends to wherever the cursors stand and grows
   whatever box the cells hold (normally the state left by the last job of the previous main
   pass).
3. **Main pass**: for each record in table order that is active and not paused (a job whose
   script has ended still runs), point the two list cursors at the job's own lists, clear the box
   cells (armed flag and the four edges to 0) and run the job.

**Running a job** executes instructions from the job's script position until a frame marker. A
script position of 0 means "nothing": the run stops at once without a write-back. Instructions:

- **Draw** (`$00`..`$7F`, 6 bytes). The cel is entry `(op & $1F) / 4` of the job's frame list
  (entries are longs, so `op & $1F` is a byte offset; `op` bits 5 and 6 are ignored). The frame's
  width `w` and height `h` come from its directory entry. The frame's current state stands for
  the number 1 when it is in its stored orientation (flag byte 1, §3.2) and 3 when mirrored; if
  that number differs from the job's facing byte, the frame is mirrored in memory first (the
  state toggles; it is per frame and shared by every job that draws it). A facing other than 1
  or 3 therefore toggles the frame at every draw. Screen
  `x = job.x + dx` when facing bit 1 is clear, otherwise `job.x - dx - w`; screen
  `y = dy + height + depth`. The job remembers `x`, `y`, `w`, `h` and the frame. Unless flag bit 6
  is set the box grows (below). If flag bit 7 is set and the gore-off flag is on, nothing else
  happens. Otherwise: the text flag takes flag bit 5; with bit 4 the cel is stamped into the
  background picture, without it the draw is entered in the restore list; with bit 1 an attack
  entry `{cel, frame, x, y}` is written at the attack cursor, with bit 0 a hurt entry at the hurt
  cursor; and the cel is drawn on the screen. The run continues with the next instruction.
- **Box growth.** If the box is not armed, all four edges take `x` / `y` and it becomes armed.
  Then, with signed compares: `min_x = x` unless `x > min_x`; `max_x = x + w` unless that is
  smaller than `max_x`; the same for `y` with `h`.
- **`$FF` frame marker** (2 bytes `$FF next`), checked in this order, the first that applies ends
  the run:
  1. Loop 1 active: count down; if not zero, go to the loop-1 position. If zero, loop 1 is cleared.
  2. Motion active: count down the steps (byte); if the result is not negative (signed byte),
     take one motion step. Otherwise fall through *without* clearing the active flag.
  3. Deferred jump armed: disarm it and go to its target.
  4. `next = $FF`: if loop 2 is active, count down; if not zero, go to the loop-2 position.
     Otherwise loop 2 is cleared and the script has **ended** (`running = 0`). The script position
     stays on the marker, so a job that is run again meets the same marker.
  5. `next = $FE`: as in 4, but if loop 2 does not repeat the script moves on by 2.
  6. Anything else: the script moves on by 2.

  Every case ends with the **write-back**: owner `x`, `height`, `y`, `facing` take the job's
  values and owner +58 / +60 / +112 / +114 take the box cells.
- **`$FD`** goes to the motion return position, **`$FE`** to the loop-2 position, and the run
  continues there (both 1 byte). A return position of 0 stops the run without a write-back.
- **`$80 f`**: facing becomes `f`, or facing bit 1 toggles when `f = $FF`. The owner's facing is
  written at once.
- **`$84 m target`**: `m = 3` jumps now; any other `m` arms the deferred jump.
- **`$88 n`**: loop 1 starts after this instruction with count `n`. With `n = 0` the count is the
  low five bits of the free-running VBL counter, and 1 if those are 0. (Not the random number
  generator.)
- **`$8C p n f vy vyl vx vxl`**: motion active with `n` steps, flags `f` and the speeds and
  limits; its return position is the next instruction, where the run continues.
- **`$94 n`**: loop 2 starts after this instruction with count `n`.
- **`$98 _ target`**: jump if the gore-off flag is on.
- **`$A0 f dx dh dd`**: with `f` bit 6, `x`, `height`, `depth` are set to the three words.
  Otherwise `x` moves by `dx`: added when (`f` bit 0 set and facing is not 3) or (`f` bit 0 clear
  and facing is 3), subtracted otherwise; `height` moves by `dh`, subtracted with `f` bit 3;
  `depth` by `dd`, subtracted with `f` bit 5. No clipping.
- **`$A4 id`**: sound effect `id` (§6.2).
- **`$A8 s off value`**: write the low byte (`s` bit 0), the low word (`s` bit 1) or the long of
  `value` into the owner at signed offset `off`. Offsets address the §9.2 bytes, so byte 63 is the
  low byte of the word at +62. A long written at a script field holds a script address.
- **`$AC a target`**: the second script becomes `target`, armed when `a` is not 0.
- **`$B0 _ routine`**: call an engine routine (§10.18) with the owner, the job's frame list and
  its position and facing; the script moves on by 6 *after* the call returns (as for `$B8`).
- **`$B4 _ target`**: if the owner's `hp` (signed) is 0 or less, jump and reset the work block.
- **`$B8 _ target`**: spawn an object running `target` (§10.18).
- **`$BC`**: clear the owner's `active` long, clear the job's `active` and `running`, move on by
  2 and continue (the run goes on until a marker).
- **`$C0 n`**: the job's frame list becomes frame-set list `n - 1` (§10.15, 1 = knight, 2 =
  creature, 3 = map, 4 = effect); the owner's `frame_list` (+38) too.
- **`$C4 _ target`**: find the first record in the table, active or not, whose owner is the
  acting knight (active-fight block +0); jump if there is one and its facing equals this job's.
- **`$C8 s off target`** / **`$CC s off target`**: read a byte, word or long of the owner as for
  `$A8`; jump if it is zero / non-zero.
- **`$D0`**: reset the work block.
- **`$90`, `$9C` and `$D4`..`$FC`**: the original has no handler (it hangs). The run stops without
  a write-back. No shipped script contains them.

**Motion step.** `v` starts as the vertical speed (zero-extended to 16 bits).

- Flag bit 1 (fall): `height += v`. If `height` is now 0 or more (signed), it becomes 0 and bit 1
  is cleared. Otherwise `v` takes the new `height` as a word, and unless flag bit 5 is set, if the
  low byte of `v` is below the limit (signed bytes) it is doubled (low byte only) and stored as
  the new vertical speed.
- Otherwise flag bit 0 (rise): `height -= v`; unless bit 5, if the low byte of `v` is above the
  limit (signed bytes) it is halved (unsigned shift of the low byte) and stored.
- The low byte of `v` is replaced by the horizontal speed; the high byte stays (so after a fall
  that did not land it is `$FF`, and a horizontal move adds `$FFxx`). Flag bit 4 moves `x` by `v`
  in the facing direction (subtract when facing bit 1 is set), flag bit 2 opposite to it; after
  each move, unless bit 7, if the low byte of `v` is above the limit (signed) it is halved and
  stored as the new horizontal speed.
- Unless flag bit 6, the script goes to the motion return position.
- If none of the moves happened, motion is no longer active.

**Contact pass** (§10.10, exact form). Clear the links (§10.18). For each active job in table
order, for each entry of its attack list until an empty entry: for each *other* active job whose
`depth` is within 10 (`|a - b|` as a 16-bit difference, then a signed compare with 10) and each of
its hurt entries until an empty entry: if the attacking cel has a hit set (§4.2), run the contact
test; on a hit write the links and contact point and continue with the next attacking job. Then
every list is emptied. The **contact test** of attacking cel `A`, frame `fa` at `(xa, ya)` against
hurt cel `D`, frame `fd` at `(xd, yd)`:

1. `m` is `A`'s width for `fa` if that frame is currently mirrored, else 0. The hit record of
   `fa` (§4.2) has `count`, `max_dx`, `max_dy` and the points. A count of 0 never hits.
2. Coarse x: the interval `[xd, xd + width_d]` against `[x0, x0 + max_dx]`, where `x0 = xa`, or
   `xa + m - max_dx` when `m` is not 0. Coarse y: `[yd, yd + height_d]` against
   `[ya, ya + max_dy]`, 16-bit values compared as unsigned. The interval test of `[a0, a1]`
   (defender) and `[b0, b1]` is the original's: if `b0 < a0` it passes when `b1 >= a0`; else if
   `b0 < a1` it passes; else if `b1 < a0` it fails; else it passes when `b1 < a1`. So `b` ending
   on `a0` counts and `b` starting on `a1` does not. Either failing: no hit.
3. For each point `(px, py)`: the gate x is `xa + px`, or `xa + m - px` when `m` is not 0; the
   point is skipped unless `xd <= gate x < xd + width_d` and `yd <= ya + py < yd + height_d`
   (signed). Then the **raw** position `cx = xa + px`, `cy = ya + py` is used (mirror ignored, a
   quirk of the original). With `xw = cx - xd`, `yw = cy - yd` the word at byte offset
   `(xw >> 4) * 2 + yw * row_bytes` (a signed word) of each stored plane of `D`'s frame, as it is
   stored now (mirrored or not, §3.2), is read and bit `15 - (xw & 15)` tested; any set bit is a
   hit with contact point `(cx, cy)`. A column past the row therefore reads the next row.

   Deliberate differences: the original counts planes through a 16-entry table indexed by the
   whole plane mask, so a 5-plane frame (mask `$10` or more) reads past the table and tests a
   meaningless number of planes; the implementation tests every stored plane. A word outside the
   frame's plane reads nothing (the original reads neighbouring memory).

**Lists.** The attack lists of all ten jobs are one buffer of 80 entries, eight per job, and so
are the hurt lists. A job's list starts at its own eight entries and ends at the first empty
entry; the cursor of a pass can run on into the next job's entries, and the contact pass reads
through them the same way. Writing past the 80th entry is outside the buffer; the
implementation raises an error there (no shipped script does it).

**Corrections to §4.1 and §7.2.** Screen y is `depth + height + dy` with `height` negative in the
air (§4.1 had `depth - height`). The `$88` random count comes from the VBL counter, not the PRNG.
Motion flag bit 0 lowers `height` (rises) and bit 1 raises it to 0 (falls); §7.2 had them
swapped and clamped the rise. A jump-if-dead reset and the `$D0` reset keep the second script.
`$C0` also writes the owner's frame list. A draw's flag bit 2 is not a mirror bit: mirroring
follows the job's facing alone. The motion count is not reset when it runs out.

### 10.18 Job plumbing and engine routines (`T11.8`)

**Record pool.** Allocating a fight actor takes the first of the 20 pool records whose `active`
long is 0 and sets it to 1. When all 20 are taken the original returns the address after the last
record without marking it; the implementation raises an error.

**Starting a job.** The first record in table order whose `active` byte is 0 gets: a cleared work
block, the script, the owner, the frame list, `x`, `height`, `depth`, facing and type;
`active = running = 1`. With no free record nothing happens (the original prints a debug message
through a routine that does nothing).

**Spawning an actor** (used by `$B8` with type `$28`, and by the arena set-ups): allocate a record;
its `x`, `height`, `y`, `facing` and `frame_list` take the given values, both links are cleared,
`active = 1`, `type` is set; then start a job for it.

**Owner lookup.** The first record in table order whose owner is the actor, active or not. The
pause toggle flips the found job's `paused` word; the kill clears its `active` and `running` and
the owner's `active` long; the restart clears its work block and sets script and `running`.

**Clearing links.** Both links of the dragon record, of 21 pool records (one more than the pool;
the implementation's pool has a spare 21st record for this) and of the four knights are cleared.

**Job pass** (§10.8, exact form). For each active job in table order: skip it if its owner has
neither link and its script is still running. Otherwise call the handler for the job's type
byte. *Keep* leaves the job alone; *end* clears the job's `active` and `running` and the owner's
`active` long; a script restarts the job on it with the returned `x`, `height`, `depth` and facing
and `running = 1`. The work block is **not** reset on a restart.

**Idle object** (`$28`): clear both links of the owner and keep.

**Engine routines.** A `$B0` operand is the address of a routine in `mog` code. The scripts of the
supported version call at least nineteen; the decoder identifies them by hunk and offset and checks
the instruction word at each entry. T11.8 executes the four fight-level ones (§10.12), and T11.10a
adds the gloat latch. The scripts also hold `$B0` calls to about twenty more routines (sound
pickers among them) that the walker of T11.7 does not reach. Each creature task adds those its
scripts actually call; an unlisted routine stops the engine with an error:

| Routine | Effect |
|---|---|
| A fighter died | §10.12 |
| End the fight | §10.12 |
| Mark the first fighter dead | `hp = $FFFF` of the actor in the active-fight block +0 |
| Pause all | Toggle the pause word of the job of every pool record except the "kept" record (the one whose job keeps running during a grab), then of the dragon record |
| Set the gloat latch | The brawler's gloat latch becomes 1 (§10.20). The knight's "down hard" script calls it, so a knight that goes down hard is never gloated over. Added by T11.10a: the walker of T11.7 does not reach that fixed script |

The others belong to the tasks that own their subject: the sound pickers and other sound-only routines (§10.32, T11.23a), the
dagger throw, the hop and its step (T11.9, §10.19), the counter (T11.10f / h), and three guardian
routines (T11.10g). Until a routine has an implementation the engine reports each call with its name,
owner and arguments and carries on, so the caller can see it was skipped.

"A fighter died" calls the arena's next-creature routine while `alive_now != max_alive` and
`total > 0`. A routine that does not raise `alive_now` would make the original loop for ever; the
implementation raises an error instead.

### 10.19 Knight control, daggers and the hop (`T11.9`)

This subsection is the exact model of the knight handler (type `$0C`), the knight's reactions to
being hit and to hitting, the thrown dagger (type `$34`), and the engine routines `dagger_throw`,
`hop` and `hop_step`. It refines §10.9 to §10.11; where they differ, this text wins. Words are
16-bit and wrap; "signed" compares read them as two's complement.

**Fight cells used here** (besides §10.12 / §10.18): the joystick word of each port; the demo
flag (nothing in the original's attract mode dies by these rules); the confused knight and its
on-flag (§11, a failed scroll gamble); the moon frame (§9.6); the arena's wall table (§4.3); the
hop facing byte, the hop table and the hop counter.

**Tables.** A record's script and number tables (`hurt_scripts`, `action_scripts`, `walk_scripts`,
`damage_table`, `defence_table`) are tables of longs addressed by a byte offset: an action is its
own offset (§9.7), a walk entry is `block + phase * 4`. The knights share one set of tables, so a
poke (§10.3) reaches both knights of a duel. An offset that is not a multiple of 4 or lies past
the table is an address error or a read of other memory in the original; the implementation
raises.

**Knight handler.** The next script starts as `idle_script`; the exit hands the job the record's
`x`, `height`, `y` and `facing`. In order:

1. `attacker` set: the **hurt rule** of the attacker's type (below).
2. `hit_target` set: the **hit rule** of the target's type (below).
3. `action = 0`; `input` takes the joystick word of the record's port (port 0 when `input_port`
   is 1, port 1 otherwise). A zero word: idle.
4. The confused knight, while the on-flag is set: if either vertical bit is set both vertical bits
   are flipped, then the same for the two horizontal bits.
5. Fire (bit 4): the index `input` without bit 4 picks a word from the action table of the
   facing (the left table when `facing` is 3, else the right table; 16 words each, and the right
   table's words 11..15 are the left table's first five, as they lie in memory). That word is the
   new `action` and the next script is `action_scripts[action]`.
6. Otherwise walk. The phase is saved, then `phase = (phase + 1) & 3`. With the step tables of
   §10.9 indexed by the new phase: up gives `dy = -up[phase]`, else down gives `dy = down[phase]`;
   right sets `facing = 1` and `dx = side[phase]`, else left sets `facing = 3` and
   `dx = -side[phase]`. The input is masked by the movement probe (with `dx` and the new facing),
   then cleared further by the arena clip and the obstacle probe. Then each remaining bit is applied
   in the order up, down, left, right: up and down add `dy` to `y` and set the block to 32 / 64,
   left and right add `dx` to `x` and set the block to 0. If no bit remained, the saved phase is
   restored and the script is idle. Otherwise bit 7 of `behaviour_flags` is cleared and the next
   script is `walk_scripts[block + phase * 4]`.

A joystick cannot press left and right together; the action tables give `$16` and `$40` for such
words, which the original cannot use (above).

**Movement probe** (exact form of §10.10). The mask starts as `$1F`; `f = facing & 3`. For each
active job in table order whose owner is not the mover, has a non-zero `box_min_x` and `hp > 0`
(signed): if the depths are close (§10.17), the side test runs: with `f = 1` it applies only when
the mover's `x` is not greater than the other's (signed) and clears bit 0; otherwise only when it is
not smaller, clearing bit 1; it clears the bit when the interval test (§10.17) passes for
`[box_min_x + dx, box_max_x + dx]` against the other's x box and for the two y boxes (+112 /
+114). Then the vertical test: `d = y - other.y`; when `d >= 0` (signed) the bit is 3 (up), else
bit 2 (down) and `d = -d`; when `d` is not above the probe distance (20, signed) and the two
unshifted x boxes and the two y boxes pass the interval test, the bit is cleared.

**Arena clip.** `e = x + 25`, or `x - 25` when facing bit 1 is set; `f = y + 9` (signed words).
`e < 10` clears left, `e > 320` clears right, `f > 155` clears down, `f < 30` clears up.

**Obstacle probe.** `lo = box_min_x + dx`, `hi = box_max_x + dx`, `limit = y + dy + 47`. For each
wall `{x1, x2, y_w}` of the arena table: if `[x1, x2]` against `[lo, hi]` passes the interval
test, and `[30, y_w]` against `[box_max_y, box_max_y + 1]` passes too, the facing direction is
cleared (left when facing bit 1 is set, unless `hi < box_min_x`; right otherwise, unless
`lo > box_max_x`; signed compares). Independently of the second test, but only for walls that
passed the first, `y_w >= limit` (signed) clears up. A table with no walls tests nothing (the
original's loop counter would run 65536 times over memory).

**Rule dispatch.** Two tables in `mog`, both indexed by a type byte and filled by one routine at
start-up, name the rule of each type: one for the type of the attacker, one for the type of the
target. A type with no entry makes the original jump to address 0; the implementation raises. The
supported version fills them like this:

| Types (hurt table) | Rule | Types (hit table) | Rule |
|---|---|---|---|
| `$00` | flyer | all listed hurt types except `$28` | carry on |
| `$04`, `$34` | contact | `$28` | hurt by the target as by a dragon in flight |
| `$08` | guardian | | |
| `$0C`, `$10` | knight | | |
| `$14` | dragon | | |
| `$18`, `$1C` | axe | | |
| `$20` | spear | | |
| `$24` | Ratmen | | |
| `$28` | dragon in flight | | |
| `$2C` | dragon object | | |
| `$30` | drake | | |
| `$40` | stalker | | |

**The common ending** of a rule ("hurt script"): the next script is `hurt_scripts[attacker's
action]`. **Taking hit points** subtracts a word from `hp`. "Face away" sets `facing` to the
attacker's facing with bit 1 flipped. "Restart the attacker on S" gives the attacker's job a
cleared work block, script `S` and `running = 1` (§10.18); the knight's own result is then 0
(**end**: the knight's job is freed, §10.8).

**Block check** (returns blocked or not). `need = defence_table[attacker's action]` (low word).
If `need` differs from the knight's `action`: not blocked. If the knight's `action` is `$1C`: blocked
only when bit 7 of `behaviour_flags` is clear, and then that bit is set. Otherwise: not blocked
when both face the same way; else blocked, with the clang sound (`$11`).

**Hurt rules** (amounts are the supported version's; all are read from `mog`):

- **Contact.** `hp > 0`: take the attacker's contact damage (below), then the hurt script.
  Otherwise the next script is the "down" script, or the "down hard" script when the attacker's
  action is 8.
- **Knight.** `hp <= 0`: the "down hard" script. Otherwise the block check; blocked:
  `action_scripts[own action]`; else as contact.
- **Axe.** `hp > 0`: the block check; blocked: `action_scripts[own action]`; else take the raw
  `damage_table[attacker's action]` of the attacker (no strength, no sword, no moonstone), then the
  hurt script. `hp <= 0`: "down hard" when the attacker's type is `$18`, else "down" (the axe
  rule's own pair of script operands).
- **Spear.** The block check always runs; blocked: the spear-parry script. Else take 3. If the
  knight has no `hit_target`, `hp <= 0` afterwards and the demo flag is off, restart the attacker
  on the spear-kill script. Otherwise the hurt script.
- **Ratmen.** Attacker action 4: take the low word of the attacker's `damage_table` entry 1, set
  `life_loss = 1`, next the curse script. Action 8: take entry 2, next the Ratmen-strike script.
  Any other action: as contact.
- **Flyer.** Take 5. If the knight has no `hit_target`, `hp <= 0` afterwards and the demo flag is
  off: restart the attacker on its kill script (one for facing the same way as the knight, one for
  facing it). Otherwise the next script is the flyer-hurt script of the same pair of cases.
- **Guardian.** Attacker action `$20`: take 10 and face away. Action 4: take 8 and face away.
  Otherwise take 10. Then the hurt script.
- **Stalker.** Take 7. If the attacker's action is `$20` and `hp <= 0` afterwards: the flung
  script. Otherwise the hurt script.
- **Drake.** Attacker action 8: face away. Take 5, then the hurt script.
- **Dragon.** `y` becomes the attacker's `y` minus 1. Attacker action 4: the amount is 20.
  Otherwise the amount is 30 and the **attacker's** `action` becomes `$20`. Take the amount after
  protection, then the hurt script (so a flight blow plays the heavy-blow reaction).
- **Dragon in flight.** As the dragon's second case, without the depth change.
- **Dragon object.** The amount is the attacker's type byte minus 10 (34 for type `$2C`; the
  original subtracts from the register that still holds the type). Take it after protection; the
  hop facing becomes 1 and the hop table the dragon object's; `facing = 3`; next the object-hop
  script.

**Hit rules.** *Carry on*: if the target's type is `$0C` or `$10`: with the target's `hp > 0`, a
target in action `$1C` gives **keep**; with `hp <= 0`, the knight's own action 8 gives keep unless
the demo flag is on. Then, for every type: a target in action `$18` gives keep; otherwise the next
script is `after_hit_script`. *Hurt by the target as by a dragon in flight*: the dragon-in-flight
hurt rule with the target as the attacker.

**Contact damage** (exact form of §10.11): the low word of `damage_table[action]`, plus
`strength`, plus the sword bonus (each `(code, bonus)` pair of the original is tested in turn
against `sword`: `$17` +2, `$18` +3, `$19` +5); doubled for action `$20`; doubled once more for
the first moonstone pair that matches: bit 0 with frame 46, bit 3 with 46, bit 1 with 45, bit 2
with 49, against the attacker's inventory slot `$16` and the moon frame. A record without an
inventory (a dagger) has no moonstones; a record without a damage table (the spear Trogg) counts
its entry as 0 (§10.20). **Protection**: the amount shifted right by the knight's
count in inventory slot `$08` taken modulo 64; 16 or more gives 0.

**Dagger throw** (`dagger_throw`, called from a knight's action-`$0C` script): the thrower's
`daggers` byte drops by 1 (wrapping); a record of type `$34` is spawned (§10.18) at the calling
job's position and facing with its frame list, running the dagger script; it gets the dagger
damage table (in `mog` code; 3 per entry) and `action = $0C`.

**Dagger handler** (type `$34`). With a `hit_target`: **end**. Facing 1: while `x < 330` (signed)
the next script is the dagger flight script, else end. Other facings: while `x > 0` the flight
script; at `-10 <= x <= 0` end; below `-10` the flight script again (a quirk: a dagger that steps
past the margin keeps flying).

**Hop.** `hop` sets the hop counter to `$FFFF`. `hop_step`, called by the owner's script each
frame of a throw: the counter goes up by 1; the owner's `input` becomes 0 and its `facing` the hop
facing. With hop facing 1, bit 1 (left) is set, the arena clip runs, and if bit 1 survived the
owner's job `x` drops by the word at `counter * 2` of the hop table; with any other hop facing,
bit 0 and `x` rises by that word. The hop table lies in the script hunk.

**Read from `mog`** (`assets/fighter_rules.py`, version `$FA43E5B2`): the step tables and action
tables through their relocated `LEA`s; the clip, probe and obstacle numbers, every amount, the
clang sound, the sword pairs, the moonstone bit / frame pairs and the dagger bounds as operands
with their instruction words checked; every fixed script, the hop table and the dagger damage
table as relocated operands; the rule tables by running their fill routine (§10.15 store run) and
naming each rule by its entry offset, checked by its first instruction word. Action codes and
type bytes are §9.7 / §9.8 constants.

Deliberate differences: the address errors and table overruns above raise; a missing rule
raises; an empty wall table tests nothing.

### 10.20 The brawler: Troggs (`T11.10a`)

This subsection is the exact model of the handler for types `$18`, `$1C` (axe Troggs) and `$20`
(spear Trogg), and of the **approach helpers** it shares with the dragon (T11.10h). Words are
16-bit and wrap. "Signed" compares read them as two's complement. `|v|` is the 68000 absolute
value of a word (negate when bit 15 is set, so `|$8000| = $8000`, which is negative). Numbers in
the text are the supported version's. All of them are read from `mog` (end of this subsection).
Action codes and type bytes are §9.7 / §9.8 constants.

**Fight cells used here.** The **target** is the first fighter (§10.12). The **gloat latch**
(a word) is cleared by the common set-up (§10.2). This handler sets it, and so does the engine
routine "set the gloat latch" (§10.18), which the knight's "down hard" script calls. The **approach cells**
are scratch words the helpers pass between each other: `dx`, `dy`, `dy_override`, `near`,
`block` and `vertical`. The **random generator** is the one of §11.2, shared with the map. The
**cool-down** is the high byte of the record's word at +106. Only that byte is read or written.

**Handler result.** The handler builds a **next script**, which starts as `idle_script`. Its exit
turns the value into the job-pass result of §10.18: `$FFFFFFFF` is **keep**, 0 is **end**, and any
other value is that script at the record's `x`, `height`, `y` and `facing`.

**Handler.** In order:

1. `attacker` set: the **hurt branch**.
2. `hit_target` set: the **hit branch**.
3. Otherwise **think**, clearing the brawler's own `input` and `action`.

**Hurt branch.** The cool-down becomes 0. The attacker's type picks the amount:
- `$0C` or `$20`: the attacker's contact damage (§10.19), with no protection.
- `$34`: 3.
- Any other type: nothing happens and the result is the idle script.

The amount is subtracted from `hp`. The next script is `hurt_scripts[attacker's action]`, where a
zero entry ends the job (§10.11). The handler never tests `hp`: death comes from the hurt
script's "jump if owner dead" branch.

**Hit branch.** The next script is `after_hit_script` and the cool-down becomes 10. Then the
target's type decides:
- **Not `$0C`:** think, clearing the **target's** `input` and `action`. This is a quirk of the
  original, which leaves the brawler's own `input` and `action` as they were. The next script
  stays `after_hit_script` unless think changes it.
- **`$0C`, with no `hit_target` of its own and `hp <= 0` (signed):** the next script becomes
  `$FFFFFFFF`. A spear Trogg while the demo flag is off then kills the target's job (§10.18) and
  plays the spear-finish script. Nothing further applies.
- **Then, for a spear Trogg:** the next script becomes `idle_script`. An axe Trogg keeps the
  next script as it stands, so it repeats `after_hit_script` while the knight stands, and keeps
  its job (`$FFFFFFFF`) once the knight is down.

**Think.** The chosen record's `input` and `action` become 0. Then:
1. Call **approach**, which returns a flag and a distance `d`.
2. Flag set: if `near` is 0, **walk**. Otherwise `d = |x - target.x|` and **decide**.
3. Flag clear: decide with the returned `d`.

**Decide** (`d` and the record's numbers are compared signed). In order:
1. `d <= too_close_x`: walk.
2. Target `hp <= 0`. With the demo flag on, the next script stands (no change). Then
   `d > 100`: walk. The gloat latch is set: stand. The cool-down is not 0: subtract 1 from it
   and stand. Otherwise set the gloat latch and **strike**.
3. The cool-down is not 0: subtract 1 from it and stand.
4. Spear Trogg: `d > reach_x` walks. Otherwise the cool-down becomes 20, `action` becomes 4,
   and the next script is the **thrust** script (a fixed script; the spear Trogg has no action
   table).
5. Axe Trogg with `d <= 100`: one percent draw (§11.2). A draw above 30 strikes. Otherwise the
   first fighter's `action` decides: `$10` (defend) goes to the heavy test, anything else
   strikes.
6. **Heavy test**: `d > 120` walks. Otherwise the cool-down becomes 10, `action` becomes `$20`,
   and the next script is `action_scripts[$20]`.

**Strike** sets the cool-down to 10 and `action` to 8, and the next script is
`action_scripts[8]`. "Stand" leaves the next script as it is.

**Approach** (also used by the dragon). It works on the record and the target:
1. `dx`, `dy`, `near` and `dy_override` become 0.
2. `facing` becomes 1 when `x < target.x` (signed), else 3.
3. `vertical` becomes 0.
4. `|target.y - y| <= depth_reach` (signed) sets `near = 1`. Otherwise one input bit corrects
   the depth and sets `vertical = 1`: down (bit 2) when `y <= target.y` (signed), up (bit 3)
   when the record is further down.
5. With `e = target.x - x` and `a = |e|`:
   - `a <= too_close_x` (signed): left (bit 1) when `e >= 0` (signed), else right (bit 0). The
     record backs away. Return flag 1 (the distance returned is `e`, which think never reads).
   - Otherwise `a <= reach_x`: return flag `vertical` with distance `a`.
   - Otherwise right when `x < target.x` (signed), else left. Return flag 1 with distance
     `reach_x`.

The input bits are ORed into the record's `input`. Think cleared it first, except in the hit
branch's quirk, where stale bits of the last move stay set.

**Walk.** If the low byte of `input` is 0, the next script stands. Otherwise the **step
helpers** run for the bits that are set, in the order up (bit 3), down (bit 2), right (bit 0),
left (bit 1). Each helper reads the walk phase `p` and the entry of its step table at byte offset
`p * 4`, an `{x, y}` word pair. The tables are three 16-byte runs of `{x, y}` pairs in `mog`'s code
hunk: side, up, down. They lie back to back, so an entry past a table's end reads the next table,
or the code after the last one. Each helper sets a **block** (0, 32 or 64) and a phase step `s`:

| Helper | `dx`, `dy` | Also | Block | `s` |
|---|---|---|---|---|
| Up | up table `x`, minus up table `y` | `dy_override = -5` | 32 | +1 |
| Down | down table `x`, down table `y` | `dy_override = 5` | 64 | +1 |
| Right | side table `x`, side table `y` | `dy = dy_override` when that is not 0 | 0 | see below |
| Left | minus side table `x`, side table `y` | `dy = dy_override` when that is not 0 | 0 | see below |

For the two side helpers, `s` is +1 when the bit of the facing direction is set (right for
facing 1, left otherwise), so the record walks forwards. It is −1 otherwise: the record backs
away and the walk cycle runs backwards. A later helper replaces the block and `s` of an
earlier one. Then the **step** runs:

1. Phase advance: `p = (p + s) & 7`, repeated until `walk_scripts[block + p * 4]` is not 0. The
   axe Troggs' sideways block has three phase scripts, so phase 3 is skipped. The original loops
   for ever on a block with no script. The implementation raises after eight tries.
2. The block is stored. `input` is masked by the movement probe of §10.19, using `dx` and the
   record's facing. No arena clip and no obstacle probe run.
3. Nothing left in `input`: the phase becomes 0 and the next script is `idle_script`.
4. Otherwise `dx` becomes 0 when neither side bit survived, and `dy` becomes 0 when neither
   vertical bit survived. The next script is `walk_scripts[block + p * 4]`. Then `x += dx` and
   `y += dy`.

**What a Trogg fight looks like.** A Trogg turns to face the knight. It walks along the depth
axis until it is within `depth_reach` of the knight, and sideways until it is within
`reach_x`. It backs away when the knight is closer than `too_close_x`. Within reach, it either
waits out its cool-down on the idle script or attacks. An axe Trogg mostly strikes (action 8);
it swings the heavy blow (`$20`) only when the knight defends or the knight is out of strike
range but within 120. A spear Trogg always thrusts. After a knight is down, an axe Trogg strikes
it once (the gloat) and then stands. A spear Trogg whose blow lands on a downed knight kills the
knight's job and plays its finishing script.

**Read from `mog`** (`assets/creature_rules.py`, version `$FA43E5B2`):
- The handler's entry, by the handler-table entries of the three types (all three must name it).
- Every number above, as operands with their instruction words checked: the two 100s, the 120,
  the 30, the four cool-downs, the three action codes, the defend action and the 3.
- The thrust and spear-finish scripts, as relocated operands.
- The step tables, through their relocated `LEA`s (the two side `LEA`s must agree).
- From the helper hunk (40): the helper entries, checked by their first instruction word, and
  the two `dy_override` operands.

**Deliberate differences.**
- A phase advance that finds no script raises instead of looping for ever.
- A record without a damage table counts its table entry as 0 in contact damage. The spear Trogg
  has no damage table, so its thrust on another Trogg made the original read the 68000 vector
  table.

---

### 10.21 Presenting the engine output (`T11.8b1`)

The app consumes the `Fight.step` output once per fight frame. It sends the draws to the
presenter in their emitted order: the second-script pass followed by the depth-sorted main
pass (§10.17). Screen Y is already projected and is not a new sort key. Cel names and frame
indices must resolve exactly; missing files and out-of-range frames are errors.

The presenter retains two indexed 320 by 200 buffers: the background with permanent stamps,
and the most recent displayed frame. At the start of a new frame it copies the background
to the displayed frame. It draws each cel onto the displayed frame, and also onto the
background when `stamp` is set. In particular, a later stamp must remain above an earlier
ordinary draw on the current frame. Ordinary draws disappear on the next frame unless
redrawn; stamps remain. Transparency is index 0 and drawing is clipped to the game viewport.

Draw positions are unsigned 16-bit words; the presenter interprets them as signed coordinates
for clipping. The `mirrored` flag is the orientation captured at that individual draw, not
the frame's final shared orientation after the engine tick. Unmirrored decoded pixels use
the disk header's horizontal shift (§3.2). Reversing the decoded visible pixel width gives
the same mirrored visible position as reversing the aligned row and subtracting the
mirrored header's padding shift. Decoded cels are never modified by presentation.

Reading or displaying the retained frame does not advance jobs, handlers, PRNG, stamps or
palette effects. The app owns frame pacing, entry and outcomes; those remain T11.8b2 /
T11.11. The audit's single-opponent fixtures exercise the real knight and brawler handlers
with a deterministic joystick bot; their positions, knight equipment and health are test
setup, not campaign defaults or wave rules.

### 10.22 Campaign host boundary (`T11.8b2`)

The host loads a fight's immutable executable definitions once, resolves lair handler offsets
through the decoded arena table, and passes the session's four persistent Actor records,
dragon record, inventories and original-generator instance into each new Fight. Job and
actor changes during combat belong only to that Fight. Input is the original five-bit
right/left/down/up/fire word; a normalized horizontal or vertical axis cannot set both
opposing direction bits. Scene rendering reads the retained frame of §10.21.

During the remaining map/sheet migration the old location screens need a separate data-only
record. It has no combat timers, controller, scripts or damage logic. At fight entry its
health, equipment, stats, daggers, lives and inventory masks are translated to the Actor.
At fight return the resulting health, daggers and lives are copied back for those screens.
Legacy sword tiers 1..4 correspond to codes $16..$19 and armour levels 0..3 to $1B..$1E;
the legacy screens' extra tier is clamped at the last supported code. Stamina, score and
secondary-weapon selection from those screens never affect combat. This adapter is removed
with T11.15 to T11.21, rather than retaining a second combat controller.

The existing map still uses its earlier generator until T11.12; the original fight generator
is session-owned, seeded once and reused across fights. No fight is reseeded on entry. T11.12
joins the map to that instance. Unsupported arena handlers are rejected before any fight is
activated, with an explicit unavailable status. They are not replaced with another creature.
Without original assets there is no synthetic gameplay fallback; tests inject synthetic
script engines and records explicitly.

The minimal host bridge samples the two ports each PAL tick and consumes a fight frame every
six ticks. After the engine clears fight_active it continues frames while the decoded end
timer counts down as a byte. The bridge preserves the decoded spawn lists, initial base wave
counts and alternating spawn cursor. T11.11 still completes the enter script, spawn lanes,
wave scaling, negative-health end-test variant, low-health palette jobs, lair write-back and
loot/temple screens. Migrating the host does not mark those rules implemented.

Fight replay snapshots contain all actor records, job positions and work blocks, shared
scratch/box/mirror state, list buffers and cursors, mutable tables, inventories, ports, PRNG,
fight cells, spawn cursor and PAL-frame phase. Snapshots contain values rather than callbacks
or asset bytes; static data compatibility and complete campaign snapshots remain campaign
verification work (T11.24).

Human duels start the first fighter on the knight frame list and the second on the decoded
duel/creature frame list (`He1.ob`..`He3.ob` and shared `kn4.ob`, §10.15), with ports as in
§10.1. Practice forces the blue and orange classes and returns to the title after either
fighter's death script and the post-fight delay. Replay metadata stores the roster at the
start of recording; menu changes to the roster are reproduced by replaying input. The
headless CLI loads the resolved disk directory, so disk-backed gameplay can be replayed
without a window. Omitting the optional disk directory from the Python helper retains the
asset-free menu/map test mode, with fights explicitly unavailable.

### 10.23 Ratmen caster and shared flight slots (`T11.10b`)

This specifies the original, single-target handler of type $24. Source: the Amiga reference's
behaviour descriptions, checked against the user's CRC-identified `mog` code at $5EC2..$6574,
and its flight helpers $70E6..$71C4 / $7388..$74D6. No co-op extensions from the reference are
adopted. All amounts, distances, waits and fixed scripts below are decoded at runtime with
instruction words and relocation targets checked. Action/type codes and flag meanings are
structural constants. Arithmetic wraps at the record width unless a comparison says otherwise.

**Shared state.** The fight owns a caster flag byte (bits 2 = initial throw underway, 3 = slam,
5 = victim held), a word wait between attacks, aim distance/steps/arc, a reusable flight request
and six trajectory slots. The active-fight second record is needed for the initial throw.
These are shared across Ratmen, never private per-handler state, and are cleared on fight
construction. A slot is keyed by actor handle; the request has owner, start and destination
`x/depth/height`, steps and arc. Ratmen move their own actor along these trajectories; these
are separate from the knight's type-$34 dagger objects.

**Handler priority.** Start with the actor's idle script. An attacker link takes precedence
over a hit link; otherwise think. Think clears the selected record's action (normally self;
an unrecognized attacker or another Ratman hit by self is the selected record instead, a
preserved quirk). State flags are tested in this order: self +104 bit 7 or bit 0 => flight;
bit 5 => grabbed; +105 bit 0 => release; +104 bit 4 => throw victim; bit 3 => hold;
+105 bit 2 => slam; otherwise choose a new action.

**Choosing an action.** Face the first fighter. Within the actor's depth reach, stand while
shared bit 5 or 3 is set or the first fighter has non-positive HP; otherwise a nonzero shared
wait is decremented and self stands. With horizontal distance at most 40 choose action 8 and
its fixed strike script; at most 50 choose action 4 and its strike script. Otherwise, or
outside depth reach, begin a throw. There are no PRNG draws in the caster handler.

**Starting and aiming.** Clear self's action and phase. If shared bit 2 is already set, aim at
the first fighter. Otherwise set shared bit 2 and self bit 2, set the whole cooldown word to
30, and request a flight from self at height -20 to the second record's job's x, depth + 3
and height, for 14 steps with arc 0. Missing job coordinates are treated as zero. Start the
flight, set self bit 0 and choose the flight script.

Aim chooses destination x as target.x minus reach when the wrapped x difference is nonnegative,
otherwise target.x plus reach; other destination coordinates are the target's. It writes
self.map_x to destination x and the chosen stray record's map_y to target depth. Distance is
the signed maximum of the word absolute x/depth differences. Aim arc is distance >> 1 (minimum
3 when not above 2), steps distance >> 3 (at most 4 forces steps 4 and arc 10), and self's
whole cooldown becomes the aimed step count. For the flight request only, halve steps with a
minimum of 8. At aimed distance <= 40 set self bit 7, replace arc by 2, start the flight and
advance the upward walk block's phase; otherwise start it and select the flight script.

**Flight and hold.** One trajectory step writes self's position. A missing slot returns the
old coordinates and counts as completed. On completion, if self bit 2 is clear, zero height
and the first flags byte and return idle. If bit 2 is set, replace that byte with bit 3 and
immediately run hold. In flight advance the upward walk block when height <= -10, the sideways
block otherwise. Hold decrements the whole cooldown word. At zero clear self's first flags
byte and shared bit 2 and aim again; otherwise use the near hold script below distance 60,
the far hold script at 60 or more. Phase advance uses §10.20 without movement probes.

**Hurt.** Only an attacker of type $0C directly wounds self; other types enter think and clear
the attacker's action. Without self bit 0, attacker action $10 or $1C clears self's first flags
byte and height, sets HP to -1 and selects the first death script. Any other action subtracts
the **first fighter's**, not the attacker's, computed contact damage and selects the hurt
table entry indexed by that first fighter's action. With bit 0 set, attacker action $18 causes
the second death script and the same resets; other actions subtract the first fighter's
damage, clear first flags/height and choose the airborne hurt script. Death accounting is
performed by the script, never directly by the handler.

**Hit.** Another Ratman enters think, clearing the victim's action. With self bit 0, shared
bit 5 means continue flight; otherwise replace self's first flags byte with bit 5, set shared
bit 5, set cooldown to sign-extended byte(victim.endurance + 6), zero height, pause the
victim's job and select the grab script. With self bit 3, clear both self flag bytes, set
second-byte bit 2 and shared bit 3, pause the victim and choose the slam-start script.
Otherwise keep the current job by default, clear both self flag bytes and set shared wait 15.
When facings match flip the **first fighter's** record and job. Action 8 or 4 replaces keep
with its matching counter script. The Ratman-type check precedes flight/grab checks.

**Grab/release/throw/slam.** The escape/counter input is always joystick port 1's fire + down,
independent of the first fighter's input-port byte. In grab, that input clears the first
flags byte, sets second-byte bit 0 and chooses escape; otherwise decrement cooldown, choosing
grab while nonzero and replacing the first flags byte by bit 4 with the wind-up script at 0.
Release toggles the first fighter's pause, clears its links, restarts its after-hit script,
clears shared bit 5 and ends self's job. Throw clears shared bit 5 and sets cooldown 17;
request a flight from self.height - 90 to x + 150, same depth and original height, 17 steps,
arc 120. Clear both flag bytes, set bit 0, phase 0 and displayed height -70. Set the first
fighter's job facing to self's, clear that fighter's links, subtract 5 HP and restart its
idle script if the true signed subtraction is positive, its fall script otherwise; toggle
its pause and use the thrown script. The job facing write does not immediately change its
record. In slam, port 1 fire + down subtracts the first fighter's contact damage from self
and uses the struggle script while the true signed difference is positive. Otherwise unpause
the first fighter, clear its links, restart its released script, clear shared bit 3 and use
the drop script. Without that input squeeze takes 1 HP from the first fighter per handler
call and chooses the finishing script when the true signed subtraction is non-positive.

**Trajectory arithmetic.** Slots hold owner, remaining steps, vertical speed and acceleration,
x/depth deltas and x/depth/height positions. All stored words wrap. x/depth use 10.6 and
height 8.8 fixed point. Choose the first slot whose owner is the requested owner **or zero**
(a free slot preceding a matching one wins). Let N be steps, D the signed word difference
start height minus destination height. If |D| <= 5, let H = (N+1)>>1, V = word(2*DIVU(
word(arc<<8), H)); acceleration = low word DIVU(sign-extended V, H-1). Otherwise if D >= 0,
V = word(2*DIVS(D<<8,N)), acceleration = word(DIVS(sign-extended V,N-1)); for D < 0,
V=0 and acceleration is minus DIVS(sign-extended word(2*DIVS(D<<8,N)),N-1).
x/depth deltas divide sign-extended word((destination-start)<<6) by N with DIVS.
Initialize positions to word(x<<6), word(depth<<6), word(height<<8). Each step subtracts
acceleration from velocity, subtracts the **old** velocity from height and adds horizontal
deltas. Return arithmetic shifts of the stored words (6/6/8), then decrement remaining steps;
zero frees the slot and signals completion. This intentionally preserves word-overflow quirks.

**Arena wiring and limits.** Ratmen's decoded idle object is created before the creatures,
on its decoded frame set and script, at x 160, height E-200, depth E, facing 1, with E the
largest wall depth (at least the knight clip's top edge). It becomes the fight's second
record. Both x and the height offset are checked operand reads. Moon-frame variants of
§10.4 replace the spawn HP and shared damage table; the caller supplies the original moon
frame, with calendar wiring still assigned to T11.12/13. Existing base waves remain until
T11.11 adds full scaling/lanes. Snapshots include caster cells, second handle, flight request
and slots. A full slot pool, division by zero or quotient overflow raises instead of using
undefined machine state. Trajectory setup's writes of arbitrary low-memory words to map
coordinates are omitted; no meaningful game values live there. A walk block without a
usable phase raises (§10.20). These are bounded-failure differences, not alternate AI.

The Ratmen scripts additionally call two sound-picker entries at code $7234 and $724C.
They select from a two-sound and four-sound group respectively using the original generator.
Their checked entry identities are registered now; their draws and playback remain deferred
with the other sound pickers to T11.23. Thus the handler itself makes no PRNG draws, but a
fully wired original script's sound selection will do so when that task is implemented.

### 10.24 Host setup, warnings and settlement (`T11.11`)

Checked against the original executable's setup at $3EDA/$4A30, spawn helpers at
$4A6E..$4B48, scaling at $40A4..$41D6, warning routines at $089E..$0A28 and loop at
$073C..$089C. Reference extensions for cooperative parties and mod scaling are excluded.
This section clarifies §10.2/5/6/7/14 and supersedes the anchor placement sentence of §10.23.

**Order and lanes.** Copy the current caller's knight stats before arena setup. The knight
enters with the checked relocated script at $4A64, not its idle script. It consumes the
first lane; creature jobs consume the next lanes, including replacements. The lane counter
is session-owned (the original common setup does not reset it), wraps through 1,2,3 and is
included in replay state. Each lane calculation wraps word operations. Ratmen's fixed anchor
uses lane 3's computed depth, and height depth minus the checked offset; it does not advance
the counter. The first-wave list has enough decoded entries for the scaled maximum. A
single-entry arena calls its alternating routine once. Before the first frame toggle only
pool/dragon job pauses; the knight's enter script later calls pause-all to release them.
Duel fighters both use enter scripts and lanes; the second starts at decoded x 30, facing 1.
Fresh fights clear pool/job/trajectory state. Loading-screen transitions belong to scene
presentation; this task does not introduce a loading delay into deterministic simulation.

**Scaling clarification.** Strength is a signed byte, maximum HP a signed word. Apply §10.6
threshold bonuses, optional lair count override and arena concurrency limits in that order.
With positive total, force the knight's action to 8 (a preserved side effect) and compute
contact damage. Add the logical word `hp_max >> 2`, wrapping; logically shift the sum right
one; subtract decoded base 6, wrapping; clamp a negative result to zero and values at least
16 to 15; logically shift right one to obtain column 0..7. Apply the arena's decoded signed
cut only if the true signed difference is positive, then store the wrapped word. No PRNG
draws are involved. Original death-accounting refill behavior in §10.12 remains unchanged.

**Timing and warnings.** Sample ports each VBL and advance one frame each decoded budget
(6 VBLs). After that frame's script/contact/draw work, register warnings, then test the first
fighter's signed HP: negative while active starts the decoded death delay (50); zero alone
does not trigger this end test. If inactive after the frame, decrement the timer immediately,
including the frame that ended the fight; zero wraps to 255. Script-driven end uses its
separately decoded delay (35). Retain the final frame until the caller returns.

Warnings start once for HP <= decoded threshold (10). They persist even if health recovers.
The first fighter owns entries 6..8; a duel's second owns 9..11. Targets are checked immediate
colour runs by kind (0,1,2,3,other), independent of arena/region colours. Each VBL after
registration steps every RGB nibble by one toward its target (decoded period 1); arrival
swaps target and saved starting colour. Only these six entries can change. Warning jobs
are palette state, separate from the ten creature/script jobs, and appear in snapshots.
The host's pause command suspends fight and warning advancement; no game-time catch-up
occurs on resume. Frame palette updates remain independent of render-call count.

**Caller boundary.** On completion settle both participating knight records once: signed
HP <= 0 restores HP to maximum and decrements the byte life count. Preserve a defeat mask
(first bit 0, second bit 1) before restoring health. A lair writes total back even on loss;
a first-fighter lair win increments its progress word. Spend the turn and clear bad luck,
then free script jobs and warning ramps. Practice returns to title for either loser.
The existing overworld caller receives the outcome until T11.15 supplies the original loot,
meeting and temple sheets; this task does not substitute invented loot or inventory rewards.
Lair remaining counts and the lane cursor persist in the session and its hash, ready for
the original overworld state of T11.12. Unsupported creature handlers remain unavailable.

### 10.25 Mudmen snatcher (`T11.10c`)

Source: the original `mog` hunk 40, offsets `$0000..$0408`, checked against the
classic (single-target) behavior described by moonstone-ace's snatcher handler. Its
later cooperative-player extensions are excluded. Values below describe operands;
the implementation reads them and all script addresses from the user's executable.

The target is always the first fighter. Each call starts with the idle script and
clears input. An attacker takes priority: subtract its contact damage, set animation
timer to 20, and choose the fixed hurt script. A hit target instead has its job's
pause bit toggled; select the carry script, set shared and local carry bit 0, timer 40.

Shared flags are tested in order: gone (bit 2), carrying (0), freed (1), released (6).
Gone returns idle. Carrying affects only a Mudman with its own carry bit: decrement
the byte timer; while nonzero, port 1 fire plus down selects release and replaces
carry with release in both flags, otherwise repeat carry. On expiry clear shared
carry, set shared/local freed, mark the first fighter dead, and fall into freed.
Freed selects the swallow script, kills the target job, and clears both freed bits
only when the local freed bit is set. Released clears both release bits, toggles
the target pause, subtracts one HP from the Mudman and selects hurt. The original
attempts to restart the target with owner/script registers reversed; it finds no
job. Preserve that ineffective restart rather than inventing a recovery script.

With none of those shared flags, an unseen Mudman decrements its byte timer. Zero
returns idle; any other result copies target x/depth, appears 75 pixels to its
right facing 3 when target x is below 160 (signed), otherwise 75 to its left facing
1. Choose appear, set local shown (bit 3) and grab-ready (bit 4). Grab-ready tests
depth and the inclusive x distance 20..80: toggle target pause, mark it dead, choose
the sink script and set shared gone. Otherwise clear grab-ready and choose emerge.

Normal approach uses §10.20's approach helper. With its flag set and near clear,
walk; with near set recompute absolute x distance. Distances at most 50 walk;
below 75 choose strike and replace local flags with shown/grab-ready; below 100
choose reach only if depth is near; all other cases walk. Depth-near is the signed
absolute depth difference compared with the actor's depth reach (§10.19).

Walking advances phase first through block 0 (+1, §10.20); right/left reads the
eight-word x-step table via the relocated LEA in hunk 40, up/down chooses -2/+2.
Use the shared movement scratch (including an untouched component), apply the
movement mask, clear components with no surviving direction bit, add wrapped
word coordinates and select the phase's walk script. If both components are zero,
reset phase and select idle. No arena clipping is added.

The decoder checks the handler-table entry for type `$04` points at hunk 40 offset
0, each operand's instruction/displacement, script relocations to hunk 4, both
step-table pointers and helper call destinations. Shared snatch flags belong to
the fight and its replay snapshot, initially zero for each encounter.

The appearance script reaches two additional checked sound routines in hunk 40:
`$0428` picks one of four sound pairs with a PRNG draw, and `$04BC` advances a
modulo-four sound counter. They join the existing `$049E` sound picker and remain
deferred, including draws and counter state, to T11.23. The handler itself draws
no random numbers; its movement and script choices are fully deterministic.

### 10.26 Flyer (`T11.10d`)

Source: the supported `mog` code hunk, `$5A76..$5C1C`, checked against
moonstone-ace's classic Flyer behavior (later party-target selection is excluded).
The four step words follow the alternation word addressed by `EORI.W` at `$5AE6`.
All tunables, step words and fixed scripts are decoded from the user's executable.

The target is the first fighter. Every call starts with idle as next script,
input cleared and action set to `$20` (heavy). An attacker whose type is not `$00`
takes priority: subtract its contact damage with word wrapping, then use its
action as a byte offset into this Flyer's hurt table. Another Flyer is ignored,
falling through to the hit branch. A hit on a non-Flyer selects `after_hit_script`
unless the victim has no hit target, has signed HP <= 0 and demo is off. In that
case kill the **first fighter's** job, mark the first fighter dead, end the fight,
and choose one of two fixed finishing scripts according to whether this Flyer
and the hit victim have equal facing bytes. Hits on Flyers fall through to motion.

Motion compares x as a signed word. Facing 3 turns only at x < 0, places x at
-50 and sets facing 1. All other facings turn at x >= 340, place x at 380 and set
facing 3. A turn XORs the shared alternation word with 1; nonzero copies the
target's depth, zero adds `(beam_word & 7) << 2` to it (word wrapping). Then draw
the original PRNG exactly once: store `(draw & 15) | 5` as a byte timer and
decrement it once. A nonzero result returns idle. The timer is **not** counted
down on subsequent calls away from an edge, so this is one idle script, not a
multi-frame wait. There is no random draw or lane update away from a turn.

Otherwise increment animation phase as a byte; reset it only when it equals 4.
Sign-extend phase to a word to select the walk script at phase * 4 and the step
word at phase * 2. Negate the step when facing bit 1 is set, add it to x with word
wrapping and return the selected walk script. No approach, movement probe or
arena clipping is applied. Invalid step phases raise instead of reading unrelated
code bytes; valid actors only use the four decoded phases.

**Hardware approximation:** `$DFF006` is a raster-position register; its horizontal
low bits depend on CPU instruction timing that this 50 Hz simulation does not
emulate. `Fight.step(vbl)` supplies `beam_word = u16(vbl)` before the handler pass.
Only the Flyer's lane variation reads this deterministic surrogate. It consumes no
extra PRNG draw, performs no I/O and cannot depend on rendering or wall time. This
explicit deviation follows the integer timing contract (§7 / ADR-005).

The alternation word is persistent executable state, not reset by arena setup.
`FightLoader` retains one mutable `FlyerCells` initialized from the relocated
word; its fights share it. Both the runtime snapshot and the host hash (even
between encounters) include the word; runtime snapshots also include beam_word.
The decoder verifies type `$00` in the handler table, helper destinations and
entry instructions, the raster read, operand instruction/displacement pairs,
script relocations, step-table extent and adjacency to the alternation word.

### 10.27 Troll stalker (`T11.10e`)

Source: the supported `mog` hunk 40 at `$04E0..$065E`, with its hit-spark
helper at `$1298`, checked against moonstone-ace's classic stalker behavior.
The target is always the first fighter; later cooperative-target changes are
excluded. The decoder reads operands, relocated scripts and step tables from
the user's executable, not from copied reference tables.

Each handler call clears input and defaults to idle; action is retained. A
nonzero hit target takes priority and selects `after_hit_script` without taking
damage or spawning a spark. Otherwise an attacker subtracts contact damage
(§10.19, wrapped word), spawns a hit spark and selects the fixed hurt script.
The invalid hurt table described in §10.15 is never indexed. Its fixed script
tests HP and selects the death branch; the handler does not substitute a death.
With neither link, a first fighter with signed HP <= 0 makes the Troll idle.

Otherwise run the approach helper (§10.20). Flag clear means attack; recompute
absolute x distance. Distances below 100 or at least 150 choose action 8 and
the fixed strike script. In the interval [100, 150), choose heavy action `$20`
and its fixed script, unless the previous action was already `$20`, in which
case strike. Strike also writes the shared hop facing and relocated hop-table
offset. Heavy does not alter those cells. No PRNG draws occur in the handler.

With the approach flag set, walk by block 0. Right has priority over left:
read the `{dx, dy}` word pair for the **current** phase (not the new phase),
negating dx for left. Up has priority over down and replaces dy with -5 or +5.
Each selected directional helper sets the phase increment to +1 if the input
contains the bit of the actor's facing direction (right for facing 1, left
otherwise), or -1 if not. Then use the existing move-apply helper (§10.20):
advance phase with that increment until there is a script, apply the movement
probe, clear blocked components, add wrapped coordinates and select the new
phase's walk script. All-zero input resets phase and returns idle. There is
no arena clipping. The step table is read as sixteen words: the original has
only four pairs, so phases 4..7 read the following sound-id and instruction
bytes. Those words stay runtime data, including their unusual signed values.

**Hit spark** (shared by later creature handlers): restore the decoded idle
object type's handler to the idle handler, then spawn its fixed script with
the decoded effects frame list, x = the hurt actor's hit_x, height = 0,
depth = hit_y and facing = the first fighter's facing. It is an ordinary pool
record and job; no special render-only effect or creature-wave accounting is
added. The spark script frees it in the normal way.

The decoder checks type `$40` against hunk 40 offset `$04E0`, all immediate
instruction/displacement pairs, both step-table LEAs, helper call/branch
destinations and entry words, the spark script and idle-handler identity, and
the spark frame-list address against the already decoded frame-list table.
The fixed strike, heavy, hurt, hop and spark addresses must relocate into the
script hunk. Hop, approach and jobs reuse their existing state and snapshot
representation; no new persistent cells are needed for this handler.

The Troll's decoded first-wave list is empty: setup calls the alternating
next-creature routine once instead of a list-spawn loop. The host treats this
as a single initial spawn without forcing the wave limit to one; the decoded
cap of two still applies to later refills (§10.5 / §10.6).

**Attack screen shake.** The heavy script calls code `$90B8`: install a VBL
effect with initial wait/reload 3 and a table selected by the relocated operand
at `$90E0`. Each VBL decrements the wait word. At zero, read a table word:
`$FFFF` restores the window and ends the effect; otherwise add the word to
both DIW window registers, advance the pointer, reload the wait. The words'
signed high bytes are vertical pixel displacements; their low bytes are zero.
Read the period and table at runtime. A new call restarts the sequence while
keeping the current displacement until the next table entry. Consolidate
overlapping starts into one hook (the original can install duplicate hooks
against the same mutable cells). This explicit deviation avoids orphaned hooks.
The fight retains the effect state and last effect VBL in its snapshot. The
runtime advances it every VBL, independently of rendering; direct audit frame
advances consume the elapsed integer VBLs once. Each host runtime call consumes
exactly one effect tick, so pause/resume does not catch up through paused time.
The presenter offsets the whole
retained image within the 320×256 canvas, including its borders, without moving
collision coordinates, sprites, permanent stamps or the palette.

The heavy script also reaches hunk-40 `$0682`, a two-iteration random sound
picker. It is identified explicitly, but its two draws and playback remain
deferred with the other sound pickers to T11.23.

### 10.28 Balok drake (`T11.10f`)

Source: supported `mog` hunk 0 `$6AD6..$6EC6`, counter `$50FC..$5114`,
and the shared trajectory/spark helpers (§10.23 / §10.27). Classic target
selection is always the first fighter. All thresholds, scripts, sounds and
table pointers below are read from checked executable instructions at runtime.

Default next script is idle; do not clear input or action on entry. Priorities:
shared flying bit 1, attacker, hit target, grabbed bit 0, recovery bit 5, then
normal decisions. An attacker subtracts contact damage, spawns the shared hit
spark and selects fixed hurt. A heavy (`$20`) hit toggles the victim's pause,
selects grab and sets shared grabbed. An action-8 hit selects after-strike;
other hits select the actor's after-hit script. Grabbed selects carry, clears
bit 0 and sets bit 5. During recovery, first-fighter HP <= 0 XORs the shared
finisher toggle with 1 and chooses one of two finish scripts; flags become
bit 6 only. Otherwise clear the first fighter's links, toggle its pause,
restart its idle job, move that job's x to Balok x plus 75 (minus when facing
bit 1 is set), clear flags, and select after-hit. This changes the job, not
the knight's actor position, until script writeback. No escape input is added.

Normal decision idles if the target's signed HP <= 0. Save target x/depth in
shared words and face it (1 for signed self x < target x, else 3). If depth
is within the actor's depth reach: clear flags and use absolute x distance.
At most 70 clears behaviour_flags2 and goes to jump aiming. At most 80
selects action 8 / hop-strike unless action was 8, in which case heavy. Up
to 120 selects action `$20` / heavy. Hop-strike writes hop facing and table.
At most 180 with the first fighter out of daggers idles; all other distances,
or a depth mismatch, go to aiming.

Aim through §10.23's helper, supplying an explicit reach offset of 80 rather
than mutating the actor's reach field. Reuse the existing shared aim words
(currently held in CasterCells). Aim steps <= 3 (signed) idles. Otherwise a
nonzero behaviour_flags2 byte is decremented; still nonzero idles. Clear
action, cap **request** steps at 20 (signed), save that as remaining steps,
retain the original uncapped aim-step and arc words, start the shared flight,
replace flags with bit 1 and select takeoff/landing.

In flight clear behaviour_flags2, step the keyed trajectory (an absent slot
keeps coordinates), then decrement the shared remaining word regardless of
the trajectory helper's completion result. Zero lands normally. Otherwise
select flight; when `(aim_steps >> 1) >= remaining` (signed), height >= -40
and absolute x distance <= 10, copy target x/depth, restart the target on
the fixed victim-landing script, mark the first fighter dead, and land.
Landing sets behaviour_flags2 to 5, height to zero and flags to zero and
selects takeoff/landing. Arc < 8 emits sound `$2F`; otherwise start the
existing screen shake and emit `$2D`, `$2E`. The handler itself draws no PRNG.

**Counter engine call:** decrement the owning actor's animation timer byte.
The BGE test uses the signed subtraction before wrapping: if negative, make
one original PRNG draw, mask its low byte with 31 (discarded), then replace
the timer with the decoded fixed 30. For example, old timer `$80` reloads,
even though the wrapped decrement is `$7F`; old timer `$81` also reloads.
This routine is shared with later creature tasks; it is not a no-op or a
random reload. Its state already belongs to the actor and PRNG snapshots.

Balok's single-entry next-creature list always spawns that fixed entry; it
does not use or flip the two-entry arena toggle. Its one-at-a-time wave
rule remains in force. All job and script execution remains in Fight.
Shared drake cells (flags, remaining, saved target, finisher toggle) belong
to the loader's executable state and are retained across its fights, as
with FlyerCells; runtime and inactive-host hashes include them. The existing
trajectory, aim, hop, spark and shake state is reused, without another
animation model. Decoder checks identify the handler-table entry, helper
destinations/entry words, operands/displacements and relocated scripts.

The grab sound-pair engine call at `$6D3C` emits its decoded synth IDs in
order; hard landing emits its pair directly. Existing sound pickers and
sample-bank wiring remain deferred to T11.23.

### 10.29 Guardian (T11.10g)

Source: supported mog hunk 40 $06AC..$0C90 and hunk 0 $4908..$49EE;
classic single-player rules, excluding the reference port's later party targeting.
All amounts, fixed scripts, hop tables and sound IDs are decoded from checked
instructions. Type $08 uses a main actor and a reserved second body record.
Setup uses the decoded main position (100,5,100), facing 1 and creature frame
list, with one alive and one total; it does not run ordinary creature waves
or scaling. Reserve the body immediately after the main record. Encounter flags
start at zero. The target cell starts as the first fighter and is refreshed only
after hurt/hit checks, preserving the previous-target read on hurt.

On every handler entry select idle, clear input and copy main x/depth into the
body job, if it exists. Attacker wins over hit-target and all staged flags:
clear both shared flag bytes, subtract contact damage, select fixed hurt, and
if signed absolute x distance to the previous target <=60 snap to target x
plus 60 facing 3, minus 60 otherwise. Sync body again. A hit-target sets the
HIGH byte at actor +106 to 5, clears both flag bytes, syncs body and KEEPS the
current job/script (the -1 next-script sentinel). It does not clear action.

Otherwise target the first fighter and handle shared flags in order 0,3,1,4,5.
Bit 0 becomes bit 4 and selects $69E2; bit 3 clears and selects $6AB4. In either
case bit 6 releases the first fighter: hop facing comes from the main actor,
toggle its pause, restart fixed $19B2, then clear bit 6. The original then writes
3 or 5 into SCRIPT +106 through the wrong register; this corrupts unrelated
script data and is deliberately omitted, without setting the Guardian cooldown.
Bit 1 just clears. Bit 4 becomes bit 3, selects $6A7A, and grabs at inclusive
x distance 130..150; bit 5 becomes bit 0, selects $69AE, grabs at 120..140.
A grab toggles first-fighter pause, sets bit 6, selects the respective $6B52 /
$6B9E script and writes cooldown byte 5 / 4 at +106. All these exits sync body.

Ordinary decisions idle when target signed HP <=0. Run the shared approach
helper (§10.20). When it requests movement with depth not near, walk immediately;
when depth is near recompute absolute x distance. Otherwise use its distance
result. Decrement the byte at +106 when nonzero; still nonzero walks. At distance
<=100 choose heavy/action $20, $6536, first hop table, cooldown 6 and return
without a second body sync. <=130 chooses action 8, bit 1, $6640, cooldown 6 and
syncs. <=140 chooses action 4, bit 5, $68B4, second hop table, cooldown 5 and
then walks. Farther walks. Walking uses fixed five-pixel steps, right before
left and up before down, wraps words, has no clipping/probe, and syncs body.

Engine routines reached by these fixed scripts:
- $0A0E (guardian_2) creates a body job on decoded $6404, type 8, using the
  main job x/height/facing and, deliberately, x AGAIN as the initial depth.
  The reserved record receives those coordinates; frame list is the call's.
- $0A52 copies main JOB facing to body JOB; $0A76 frees body job/record.
- $0BA8 subtracts decoded 10 HP from first fighter and toggles its pause.
- $0BD2 toggles first-fighter pause and repositions its job: facing main
  actor facing XOR2, depth main depth, x main x minus 137 for new facing 1,
  plus 137 otherwise, word wrapped then signed-clamped (>=320 ->319, <0 ->1).
  Restart idle when alive; otherwise request the original fade with palette
  entries 2..7 targeted to zero and restart decoded $1720. Fade is advanced
  by the host on PAL ticks, independent of render calls.
- $0A84 emits four decoded fixed synth IDs in order. $0AAE's random sound
  pairs and draws remain deferred to T11.23, consistently with other pickers.

Missing jobs leave no writes (the original writes through zero in some helpers).
Guardian cells (main/body handles, flags, previous target, death-fade request)
are encounter-owned and included in snapshots. Existing trajectory/hop machinery,
persistent script jobs, contact damage, stamps and end-fight routines are reused.
The hurt script branches on HP and ends the fight itself; the boss is not
settled with the ordinary fighter_died wave decrement. Acceptance separately
records that death branch and the resulting fight termination.

### 10.30 Dragon and linked objects (T11.10h)

Source: supported mog hunk 0 $6574..$6AD4 (handlers and helpers),
$44C4..$469C (arena), scripts in hunk 4, and shared helpers of §10.20,
§10.23 and §10.27. Use classic first-fighter targeting; later party changes
in the reference port are excluded. All tunables, fixed scripts, table
pointers and sound IDs below come from checked executable instructions.

**Setup:** use persistent actor handle 5, not an ordinary pool monster.
Apply the decoded dragon record, position (80,-40,100), facing 1 and creature
frame list. Clear its links, action, input and encounter flags. Start idle.
Allocate the two decoded type-$2C objects in order, each with its own record
and shared dragon tables: positions (5,0,80) and (5,0,120). The common
initializer overwrites their earlier HP=50 with 120; retain that order.
One alive/one total, no wave scaling/refills. The first knight uses its
normal entry script; pause the pool and dragon until that script releases
them. Dragon HP is reinitialized by this arena's common initializer.
The linked handles, flag bytes, saved reach/too-close words and distance
are encounter cells and participate in runtime snapshots.

**Main handler:** default next script is idle. Attacker precedes hit-target.
A type-$0C attacker sets flag $80, subtracts contact damage, selects the
hurt table by attacker's action and creates the shared hit spark at the
DRAGON's contact coordinates. A dagger ($34) sets $80, selects hurt offset
12 and subtracts 3, then the same spark. Other attackers keep the current
script if the dragon job is running; otherwise think, retaining the
original job-pointer alias described below. On hit-target, action !=4
keeps the job; action 4 frees the target's job/record and selects the fixed
bite/finish script. Classic code does not mark HP immediately; that script
calls mark-first-dead. With no links clear action before thinking.

**Ground step** (also script callback $6882): clear dragon input; if flag
$40 is set save reach/too-close and replace them with 2/1. Run approach
§10.20 against first fighter, then force facing 1. Apply right before left
in separate tests: x+5 only if signed result <100, x-5 only if signed result
>30. Apply up then down separately (five pixels, word wrap). Copy actor
x/depth into its job. The original restores the saved reach/too-close
through the JOB pointer: actor values stay 2/1; words at job+116/+118
become the saved values (width/frame-height of the job two slots later).
Preserve writes within the modeled ten-record table; writes beyond it
are omitted as invalid-memory corruption. Save words remain in snapshots.

After ground step save signed absolute x distance. Below 140, flag $20
set picks an attack, otherwise start the near flight. At or above 140,
flag $20 clear picks an attack, otherwise start the far flight. This is
the assembly's actual branch order; the reference rule's introductory
near/far prose states it backwards.

**Start flight:** set flag $10, near also sets $20, far clears $20. Reset
phase. Select walk table offset32 (near) or64 (far), first entry. Set
actor cooldown WORD to 13/9. Fill the existing trajectory request owned
by handle5 from current dragon coordinates to x100, first-fighter depth,
height -70/-30, steps13/9, arc80/120 respectively, and start it. Sync links.

**Flight handler:** decrement cooldown WORD, clear $10 at zero. Move actor
depth +5 when signed depth <target depth, else -5 (including equality).
Step the shared trajectory, accepting only x/height; its depth result is
discarded, so depth remains the tracker result. Increment phase BYTE;
if signed byte >=8 use7 for lookup, otherwise use the unsigned byte.
Select walk offset32 when $20 is set,64 otherwise, plus phase*4. A wrapped
negative phase can address beyond the table; bounds errors raise under
ADR-011. Sync links. Hurt does not cancel a flight.

**Attack pick:** clear $40 first. Target HP<=0 or approach near=0 leaves
idle. With $20 clear set action8 and fixed strike script. With $20 set,
distance<=70 and $80 clear selects action4/bite; otherwise select action
$20/breath and set $40. Both latter choices clear $80. Breath also spawns
an ordinary idle-object job (type$28, creature frame list) at dragon x+55,
height0, depth+5, facing1 on the decoded fire script.

**Job alias on an ignored hit:** when the dragon job has ended, the
original enters think with A1 pointing to its JOB. Ground thinking restores
the actor pointer and is normal. In-flight thinking instead decrements
the word at job+106 (x of the job two slots later) and adjusts job+8 (its
height) as if it were actor depth. A job pointer cannot match a trajectory
owned by an actor, so dragon x/height remain unchanged. Phase advancement
and link sync still operate on the dragon. A missing two-slots-later record
raises instead of reading arbitrary memory. The normal handler result then
replaces its job coordinates from the actor, as with every restart.

**Linked records:** normal sync writes actor depth dragon+10 into link A,
and dragon-20 into link B; it does not reposition their jobs. The separate
$6A82 routine differs: BOTH jobs get dragon+10 (a subtraction uses the wrong
register), then BOTH actors get the first job's depth. Preserve this typo;
missing jobs cause no zero-address writes.

**Object handler (type$2C):** default idle, first target. Attacker returns
idle; hit-target returns idle, and unless the target is type$14 sets hop
facing1 and the ordinary hop table. With neither link: dragon signed HP<=0
selects fixed object-death. Otherwise target HP>0, depth within the object's
reach and signed target x<=100 selects action **$14** (decimal20, not $20)
and fixed attack. Otherwise normal linked-record sync then idle. No clipping,
wave death or independent HP reaction is added.

**Callbacks:** $6D28/$6D32 emit their decoded fixed sound IDs in order;
$6882 runs the ground step; $6A82 synchronizes jobs as above. The newly
reached random two-draw sound picker $71EC and hunk16 channel-release calls
$40/$52 (which release a reserved synth voice before requesting sound $A7)
are identified but deferred, including their audio state/draws, to T11.23.
The existing counter, hop, shake and end-fight machinery is reused. Death
comes from the hurt script's HP branch and ends the fight directly.

Host Dragon intercepts use this runtime. Original map/calendar transitions
remain T11.12/13; hoard/reward sheet and settlement remain T11.21. Real-disk
acceptance uses ordinary recorded joystick inputs, checks natural hurt-script
death/win and idle loss, full-state replay, independently read setup operands,
and all reachable fixed-script draws. The audit knight explicitly has strength5,
sharpness sword, battle armour, 80HP, one protection item and moonstone bit1
at moon frame45; its controller closes and uses action $18. Pose previews
are labelled separately from natural traces and include the complete dragon
and linked objects.

### 10.31 Computer knight (T11.10i)

Sources: classic Amiga handler in mog hunk40 $0CAA..$112A, hunk41
step data, and moonstone-ace fight_creatures/fighters and ai_fight_knight rules.
Later cooperative targeting and difficulty adjustments in the reference port are excluded.

The type-$10 handler clears input and defaults to idle. Its target is the opponent
when it is the first fighter, otherwise the first fighter. Attacker takes priority
over hit target. Hurt always uses the knight-versus-knight rule of §10.19, regardless
of attacker type: block while alive, contact damage without protection, hurt table;
when already down select the fixed knight down-hard script. On a landed hit, KEEP
when the target is action $1C and either target type is $0C or own action is $20/$04;
otherwise use own after-hit script. This differs from the human hit handler.

Normal thinking saves the previous action in a shared word (hunk41 +52), then
runs approach (§10.20) against the selected target. Movement requested with depth
not near walks immediately; otherwise recompute absolute x distance when movement
was requested, and decide. Own action is not cleared on idle or walk.

A down target clears only the high cooldown byte (+106); at signed distance above
90 walk, else strike $08 unless the shared gloat latch is set, in which case idle.
Against a living target, with block latch clear, consume one percent draw. When
its signed byte is at least the signed odds byte and facings differ: opponent
action $08 within signed x distance120 selects defend $10; action $20 or $04
within120 selects block $1C. A failed range test leaves distance equal to the
x-difference sign (1 for opponent left, 0 otherwise), preserving the original bug.
The redundant second facing comparison has no additional effect.

Then consume a percent draw. A signed byte greater than odds attacks; otherwise
consume one discarded full draw (the debug taunt), then walk. Attack tests in order:
signed distance <=90 and last action !=$08 selects $08; <=95 and last !=$20 selects
$20; <=100 and (no daggers or last !=$04) selects $04; otherwise with daggers select
$0C, else walk. Every selected action uses own action script table. Odds are indexed
by the original zero-based day word, not a new difficulty setting. The decoder
retains bytes following the relocated odds pointer, including adjacent data when
a late day reads past the nominal table; an index outside the loaded hunk raises.
Calendar wiring remains T11.12/13; independent duels accept the original day index.

Walking clears the block latch. Use §10.20 step helpers in up/down/right/left order,
but their table pointers are hunk41 +20/+36/+0. Eight pairs per pointer are read
from the user's executable: these overlap subsequent tables, the mutable last-action
word at +52, and text. Substitute the current shared action word at +52 before a
down step reads it. Up/down overrides are -5/+5 from checked operands. The old
phase supplies movement; phase advancement and probes follow §10.20. No arena
clip or obstacle probe is added. The original leaves stale high D0 bits when
a down opponent is at least256 pixels away and can index far memory: use the
bounded phase index instead (ADR-011), without emulating arbitrary memory reads.

The decoder checks the type table entry ($10 -> hunk40 $0CAA), helper destinations,
operand instructions, paired ranges, step pointers and odds pointers. Distances,
overrides and odds bytes are runtime data. Fight snapshots include day and last
action. The existing duel loader accepts explicit computer handles, sets their
kind4/type$10/port4 before palette composition and job creation, and assigns human
ports2 then1 skipping computers. Both computer fighters are rejected. Both use
the ordinary knight tables/reach and separate knight/creature frame lists. The
campaign caller identifies computers from its roster. The existing temporary legacy
sheet boundary retains all four records on construction/reset, hashes all four,
and synchronizes only existing map records at settlement. Computer records do not
write joystick ports. Original map initialization remains T11.12/13; meeting sheets and loot
remain T11.21. A seeded equipped audit uses ordinary joystick inputs for wins and
idle input for losses, alongside separately labelled pose previews.

### 10.32 Fight sound requests and sound pickers (T11.23a)

This subsection models every fight-script engine routine (§10.18) whose only job is sound, and
the form in which the fight hands sounds to the presentation. It refines the sound notes of
§10.26 to §10.30; where they differ, this text wins.

**The front end.** `mog` hunk 16 holds the sound front end that every effect goes through. It has
three kinds of entry, and the fight reports each call as one *sound request*, in execution order:

- *play* (hunk 16 `$0088`): start a sequence on the next free channel. Channel choice is
  presentation (T11.23b) and never feeds back into the simulation.
- *fixed* (hunk 16 `$0000`, `$0010`, `$0020`, `$0030`, channels 0 to 3): mark the channel busy
  and start a sequence on it. The entry address names the channel.
- *release* (hunk 16 `$0040`, `$0052`, channels 0 and 1): `ANDI.B #mask` on the busy byte, then a
  `BSR` to the play entry with a fixed silence sequence. The mask clears the channel's own bit
  and bits 4 to 7. The request carries the channel, the mask and the silence id.

Script opcode `$A4` (§10.17) is a *play* request, and so are the handler sounds of §10.19 to
§10.31 (the clang, the drake landing and grab pairs). Inside one fight frame, the requests of the
job pass, the engine tick and the contact pass form one ordered list: handlers, script opcodes and
routines append to the same list as they run.

**Random draws.** Every picker draw is one full draw of the shared generator (§11.2, the hunk-0
routine at `$A2B0`), masked from the low end. The pickers are part of the PRNG order: until
T11.23a the fight skipped them, so fights that reached one made fewer draws than the original.

**Hunk-0 pickers** (`$71D2` to `$72FB`). Each one starts with a `JSR` to the generator, unless
noted, and ends with a play request:

- *Masked add.* `ANDI.L #mask,D0`, an optional fold, then an `ADDI.W` or `ADDI.L` of a base. The
  fold (`BEQ.S +2`, `SUBQ.W #1,D0`) leaves 0 alone and lowers every other value by one, so
  values 0 and 1 give the same sound. `$71D2`, `$721C`, `$7234`, `$724C`, `$7264` and `$727C` are
  single masked adds. `$71EC` is two of them, each with its own draw: a folded one and a plain one.
- *Plain cycle* (`$7296`). Add one to a code-hunk word; when the signed result is not below a
  limit (`CMPI.W`, `BLT`), store zero. The id is the word plus a base. This takes no draw.
- *List cycle* (`$72C0`, `$72C8`). Each loads its own list address (`LEA`, then `BRA` to a shared
  tail for the first). The shared tail adds two to one shared offset word and reads the list word
  at that byte offset. If the word is the terminator (`CMPI.W #$FFFF`), the offset becomes zero and
  the first list word is used. This takes no draw. The two lists sit back to back in the code
  hunk and share the offset word, so one list can leave the offset at a position where the other
  list reads its terminator early. Starting from offset 0, no sequence of calls reaches past the
  second list's terminator. The decoder therefore keeps the words from the first list to that
  terminator, and a read outside them is an error.

The plain-cycle word and the list offset are code-hunk cells. The original never resets them
between fights, so they persist for the whole session, like the generator. They start at the
values stored in the executable.

**Hunk-40 routines** (the Guardian and Wizard hunk):

- `$041C`: one fixed request (`MOVE.W #id,D0`, then `JSR` to a fixed entry).
- `$0428` and `$0AAE`: one draw, `ANDI.W #3`, then a branch on the value. Each branch target plays
  one pair of ids (two play requests). The comparisons (`BEQ`, then `CMP #1` and `CMP #2` with `BEQ`)
  are decoded, so the value-to-pair mapping is read, not assumed. `$0AAE` sends value 0 to the
  same pair as value 3.
- `$049E`: one draw, `ANDI.W #3`, then a byte-table read with sign extension (`EXT.W`). The table
  sits right after the routine at `$04DA`.
- `$04BC`: add one to a hunk-40 word, `ANDI.W #3` it in place, and play one id only when the
  result is zero. This takes no draw. The word persists like the hunk-0 cells.
- `$0682`: `MOVEQ #n,D7` and a `DBRA` loop, so n + 1 iterations. Each iteration takes one draw and
  `ANDI.W #7`, reads a byte table at `$0674` and zero-extends it (`ANDI.W #$FF`).
- `$0A84`: four fixed requests in order, one per channel.

**Other hunk-0 routines.** `$6D28` and `$6D32` (the Dragon's) are fixed requests on channels 0 and
1. `$6D3C` (the Balok grab) is two play requests and stays with §10.28.

**Decoding.** For the supported `mog` version, every instruction word, every relocated call
target (the generator, and each front-end entry with its hunk) and every immediate above is
checked at its position. Ids, masks, bases, limits, list words, table bytes, loop counts and the
initial cell values are all read from the user's file.

## 11. Overworld and Turns (`T11.4`)

The overworld is one screen: the map picture with small sprites on it. Knights take turns. The
knight whose turn it is moves one pixel per frame under joystick (or computer) control until a
**move budget** is used up; a **proximity scan** lists what the knight is standing next to, and
the fire button acts on that list. There are no paths, no nodes to hop between and no random
encounters: a fight starts only from the node menu, from a computer knight's arrival, or from
the dragon's touch. `sim/` implements this section; `app/` draws it and feeds the input word.

Sources: the reference project's map loop, proximity scan, turn scheduler, lunar clock, dragon
flight and computer-knight rules. Numbers that the original holds as data or as immediates in
`mog` are read from the user's `mog` (§11.16); they appear here only to pin the behaviour.
Comparisons are signed 16-bit unless stated; arithmetic wraps at 16 bits.

### 11.1 State

Besides the records of §9, the map keeps these cells (all words unless noted). In `sim/` they
are fields of one overworld state object.

| Cell | Meaning |
|---|---|
| `turn` | Index 0..3 of the knight record whose turn it is. |
| `current` | The record of that knight (also the active-fight block's `current`). |
| `spent`, `budget` | Move budget accounting (§11.6). |
| `move` | The move word of this frame, same bits as the actor's `input` (§9.2): 0 right, 1 left, 2 down, 3 up, 4 fire. |
| `terrain_counter`, `blocked` | Terrain slow-down (§11.6). |
| `travel`, `ambush`, `saved_x`, `saved_y` | Map modes (§11.9). |
| `arrivals` | Up to 10 rows `{key, kind}` built by the scan (§11.7). |
| `dragon_touch` | Up to four knights whose box touches the dragon. |
| `dragon_active`, `dragon_timer`, `dragon_speed_x`, `dragon_speed_y` | Dragon flight (§11.10). |
| `skipped` | Knights passed over in a row by the scheduler (§11.11). |
| `day`, `moon_index` | Clock (§11.12); `moon_frame` and `moon_subcount` live in the active-fight block (§9.6). |
| `region_code` | Region of the tile the current knight stands on (§11.5); the arena set-up reads it (§10.2). |
| `bad_luck` | Set by a failed gamble item, cleared after a fight (§10.7). |
| Computer-knight cells | Walk line, roam list and target, shop wish and town target (§11.13). |

Three more groups of words are written at every turn set-up but nothing reads them: the
quarter and three-quarter marks of the budget, and three latches. They are not modelled.

### 11.2 Random numbers

The current `sim/prng.py` is a linear congruential generator and is **not** the original's.
Every rule that draws needs the original generator, because the order and number of draws are
part of the behaviour:

- **State:** one 32-bit word.
- **Draw:** repeat eight times: let `t` be the state rotated right by 3 bits, XORed with the
  state; shift the state right by one bit and put bit 1 of `t` into bit 31. The new state is
  the result of the draw; callers mask the bits they want from the low end.
- **Percent draw:** one draw, low 7 bits; a value of 100 or more has 27 subtracted. The range
  is 0..100.
- **Seed:** the original picks one of four 32-bit seeds stored in `mog` data by the low two
  bits of the video beam position at start-up. Here the index 0..3 comes from the replay seed.

T11.12 replaces the generator (it is the first rule task that draws on the map); the fight
engine uses the same one.

### 11.3 New game

In this order (the draws are consecutive):

1. Scheduler cells cleared: `skipped`, `bad_luck`, `spent`, `turn`, `day` are 0;
   `moon_frame = 45`, `moon_subcount = 0`.
2. All four knight records become **computer knights**: `kind = 4`, `type = $10`,
   `input_port = 4`, one of four fixed names, and map positions (15, 100), (300, 100),
   (160, 20), (160, 180) for records 0..3.
3. The dragon (record 4) gets one life, its creature initialisation, `type = $14`, facing 1,
   `kind = 5`, and its hoard: four item rolls and one gold roll (§12) into its inventory.
4. The market stock is cleared and filled by six item rolls; the count of slot `$08` then gets
   2 added (§12).
5. The 24 lairs and their loot inventories are cleared; each lair's `loot` is linked and
   its `gold` cleared.
6. **Keys.** The lairs form four groups of six (records 0..5, 6..11, 12..17, 18..23). One index
   0..5 is drawn (low 3 bits of a draw, redrawn while above 5). The lair at that index in group
   0 gets key mask 8, in group 1 mask 4, in group 2 mask 2, in group 3 mask 1.
7. **Loot.** For each lair in order: a percent draw is compared against a four-band table in
   `mog` (`{limit, kind}` pairs; the first band whose limit is not below the roll). Kind 1 is
   one gold roll, kind 2 three item rolls, kind 3 one gold roll and two item rolls (§12).
8. **Lair definitions** from four parallel 24-entry tables in `mog`: a long whose high word is
   `handler_offset` and whose low word is the initial `creatures_left`; the map position
   (`x`, `y` words); `param14` (word); `param16` (long).

Knight selection (§12 owns the screen) then gives the first `player_count` records a human
`kind` 0..3. A human knight gets its name and starting position by kind: kind 0 at (10, 10),
kind 1 at (300, 5), kind 2 at (26, 180), kind 3 at (300, 185). All four records then get their
inventory, the record defaults of §9.2 and the knight fight tables, and `tile_x = map_x >> 3`,
`tile_y = map_y >> 3`.

The map scene set-up that follows frees all jobs, stops the map colour jobs, sets up the turn
(§11.11), recalculates all four knights' derived values (§9.3) and clears the key state.

### 11.4 The map screen and its frame

One map frame lasts **2 VBLs** (a fight frame lasts 6, §10.7).

**Screen entry** (at the start of the game and after every menu action, place, fight, status
sheet or turn change):

1. Clear the key state.
2. Build the background: the map picture, then the lair markers (§11.15), then the other
   knights. Copy it to both screens.
3. Set up the turn (§11.11). This recomputes `budget` but leaves `spent` alone.
4. Run the move step (§11.5) with whatever the `move` word still holds.
5. Draw the current knight and run the map jobs (the dragon), flip.
6. Start the map colours if they are not running: fade to the map palette, one colour ramp and
   one colour cycle (§11.15). Then, if the dragon is not active, try to spawn it (§11.10).

Quirk kept: step 4 uses the stale move word, so a direction held when fire opened the menu
moves the knight one more pixel on re-entry.

**Each frame**, in this order:

1. Stamp the frame start.
2. **Decide the move word.**
   - Human knight: run the terrain test (§11.6); if not blocked, read the joystick, else the
     word is 0.
   - Computer knight (`kind == 4`): the decision chain of §11.13, the terrain test,
     `spent += 1`, and if not blocked one step of the walk line.
3. **Charge the step** (human only): outside travel and ambush mode, if the word has any of
   the four direction bits, `spent += 1`. The charge is made before clipping, so pushing
   against the map edge costs budget; a frame blocked by terrain costs nothing.
4. **Keys.** For a human knight: space opens the status sheet (sheet 9, §12; the map colours
   are stopped first and the screen is re-entered afterwards); `E` sets `spent = budget`, which
   ends the turn. For any knight: `Q` leaves for the farewell screen and the title.
5. **Fire.** If the word has the fire bit: in ambush mode the map modes are cleared first; then
   the node menu runs (§11.8). A non-zero result re-enters the screen.
6. **Dragon.** If the dragon attacks the current knight (§11.10), the map colours stop and the
   dragon encounter runs.
7. **Budget.** If `spent < budget`: run the move step, draw the current knight and the map
   jobs, flip, restore the background on the hidden screen, wait for the frame time, next
   frame. Otherwise run the turn scheduler (§11.11): it either re-enters the screen for the
   next knight or ends the game.

### 11.5 Move step, clipping, tile

The move step does nothing to the position when the move word is zero. Otherwise:

1. **Clip** the word against the position before the step: at `x <= 0` clear left, at
   `x >= 310` clear right, at `y <= 0` clear up, at `y >= 190` clear down. The clipped word is
   written back.
2. **Step** one pixel per remaining direction bit: right `x + 1`, left `x - 1`, up `y - 1`,
   down `y + 1`. Opposite bits cancel. A diagonal moves one pixel on both axes for one unit of
   budget.

Then, always: recompute the tile and run the proximity scan (§11.7).

**Tile.** With `w`, `h` the size of frame 0 of the map sprite set (§11.15):
`tile_x = (map_x + (w >> 1)) >> 3`, `tile_y = (map_y + h) >> 3` (unsigned shifts). The tile is
the 8 x 8 cell under the middle of the sprite's bottom edge. The **tile index** is
`tile_y * 40 + tile_x`, used as a signed word index into two byte tables of `mog` data, one byte
per cell of the 40 x 25 grid:

- the **terrain mask** table (§11.6);
- the **region** table: the region code (0, 4, 8, `$C`; §10.2) of the cell. It is read into
  `region_code` at every turn set-up, so a fight uses the region of the tile the knight stood
  on when the turn was last set up (screen entry, or the prologue of a place visit).

### 11.6 Terrain and the move budget

**Terrain test** (once per frame, before the move word is decided): `blocked` is cleared. In
travel or ambush mode nothing else happens. Otherwise the tile is recomputed and the mask byte
of its cell is read; a mask of 0 leaves the knight free. For a non-zero mask,
`terrain_counter += 1` and the frame is blocked when `terrain_counter & mask` (mask
sign-extended to a word) is non-zero. A mask of 1 lets every second frame through, a mask of 3
every fourth. The counter is never reset.

**Budget.** `budget = derived_endurance << 4` (§9.3), set by the turn set-up. With the starting
values that is 96 units. A human turn ends when `spent >= budget`; each walking frame costs one
unit, standing still costs nothing, and a turn has no time limit. A computer knight pays one
unit per frame whatever it does. Places, fights and items end a turn by setting
`spent = budget` (§10.7, §12).

Quirk kept: because every screen entry recomputes `budget`, a budget doubled or halved by an
item (§9.4, slot `$0A`) lasts only until the next screen entry.

### 11.7 Proximity scan

Run after every move step for the current knight at `(cx, cy)`. It rebuilds the arrival list
and draws a highlight sprite over everything it lists.

**Box test.** Two boxes touch when their x intervals and their y intervals both pass this
test: with A = `[a0, a1]` and B = `[b0, b1]`, they pass when `b0 < a1` and `b1 >= a0` (B
starting exactly at A's far edge does not count, B ending exactly at A's near edge does). An
interval runs from the position to the position plus the width (or height) of a frame of the
map sprite set.

**Order.** The first five rows of the list are cleared (rows beyond stay as they were; quirk
kept), then rows are appended:

1. **Places** (skipped in travel mode): each map node (§9.5) in table order. A = the node at
   its position with the size of the frame whose number is the node id; B = the knight with
   the size of frame 0. A home village (`$15`..`$18`) counts only for the knight of kind
   0..3 respectively. A hit draws frame `id` at the node and appends
   `{key = x << 16 | y, kind = id}`.
2. **Other knights** (skipped in travel mode): records 0..3 except the current one, in
   order. A = the current knight, B = the other, both with frame 0's size. A hit draws the
   other knight's highlight (frame `kind + 5`, or frame `$2A` when it has no lives) and appends
   `{record, 1}`, or `{record, $21}` when it has no lives.
3. **Dragon touch** (skipped in travel mode): the touch list is cleared; if the dragon is
   active, each of the four knights (the current one included) whose box touches the dragon's
   is put on the touch list and highlighted. A = the dragon with frame `$14`'s size at its
   `x - 10`, `y`; B = the knight with frame 0's size. The dragon keeps its map position in the
   fight fields `x` and `y` (§9.2), not in `map_x` / `map_y`.
4. **Lairs** (also in travel mode): each of the 24 lairs with `map_x >= 0`. A = the lair with
   frame 31's size, B = the knight. A hit draws frame 31 at the lair and appends `{lair, 2}`.

A row whose key is zero ends the list.

### 11.8 Node menu and dispatch

Fire on the map runs the menu routine on the arrival list:

- **No row:** nothing happens, result 0.
- **One row:** it is dispatched at once.
- **Travel mode:** the first row of kind 2 (a lair) is dispatched; if there is none, result 0.
  (The search tests the kind before the end marker, so a stale kind 2 in the terminating row
  counts; quirk kept.)
- **Otherwise the menu:** the key state is cleared and a panel is drawn at (50, 100): frame
  `$20` of the map sprite set as the panel, the knight's own frame (`kind`) at (55, 105), the
  headline (the knight's name followed by the " may ..." string) at (65, 105), then one line
  per row starting at y = 115 and 6 pixels apart at x = 55: the digit `1`, `2`, ..., a space,
  and the row text. The text is the lair string for kind 2, the battle string plus the other
  knight's name for kind 1, and for a place the entry `kind - $15` of a 14-entry string table.
  All strings come from `mog` (§11.16). The menu then waits for a key `1`..`9` whose row
  exists; other keys are ignored. There is no way to leave the menu without choosing.

**Dispatch by the row's kind:**

| Kind | Action |
|---|---|
| 1 or `$21` | Knight meets knight (§10.1, §12) with the current knight and the row's record. |
| 2 | Lair entry with the row's lair (below). |
| anything else | Place visit with the kind as the place id (§12). |

The action's result goes back to the frame; non-zero re-enters the map screen.

**Lair entry.** Without travel mode: the creature fight for the lair's `handler_offset`
(§10.3). A lost fight shows the temple sheet and returns. A won fight gives `progress + 1` and
shows the loot sheet (sheet 2, §12). In travel mode there is no fight: the loot sheet opens
directly, but only to look (nothing can be taken there, §12.5). After the loot sheet the lair is tidied (§11.9) and, in travel mode, the knight is
put back on the saved position and the modes are cleared.

### 11.9 Lair visibility and map modes

**Visibility.** A lair is on the map while its `map_x` is not negative. After a lair's loot
sheet (not in travel mode) the lair is hidden, both coordinates set to -1, when its `gold`
word is zero and all 24 bytes of its loot inventory are zero. A lair that still holds loot
stays visible and can be entered again; `creatures_left` (§10.7) says how many creatures the
next fight has. Hidden lairs are skipped by drawing and by the scan; the computer knights'
roam list gives them the largest distance (§11.13).

**Modes.** Two flags change how the current knight moves. Both are cleared at every turn end.

| Mode | Set by | Effect |
|---|---|---|
| **Travel** | The position-mark item (§9.4 slot `$02`): flag set, position saved | Terrain never blocks, walking costs no budget, the scan lists only lairs, fire shows the loot of the first listed lair without a fight and without taking anything (§12.5), and afterwards the knight is back on the saved position. Drawn with frame `kind + 10`. |
| **Ambush** | A good roll of the teleport item (slot `$0C`): flag set, position saved | Terrain never blocks and walking costs no budget. Fire clears the modes before the menu runs, so the knight walks anywhere for free and then acts normally from there. Drawn with frame `kind + 15`. |

A bad roll of the teleport item instead moves the knight to a random spot:
`map_x = (draw & $FF) + 32`, `map_y = (draw & $7F) + 36`, x drawn first.

### 11.10 The dragon

**Spawn.** Tried at every map screen entry while `dragon_active` is 0. It happens when
`day >= 2` and the dragon's `lives` byte is not negative (a slain dragon has `lives = $FF`).
On spawn: all jobs are freed; the dragon record gets `x = 10`, `height = 0`, `y = 100`,
facing 3, `type = $28`, `anim_phase = 0`; its frame list is the map sprite set in all five
slots; a job starts with the first of the 16 flight scripts; `dragon_speed_x = 2`,
`dragon_timer = 100`, `dragon_active = 1`. Then a target is drawn: the low 2 bits of a draw
pick a record 0..3, redrawn until that knight has lives (the original loops for ever with
nobody alive; here the loop is bounded and no target is set). The target goes into the
dragon's `engaged_with`. `dragon_speed_y` is 1.

**Flight** (the type `$28` handler, once per map frame while the map jobs run):

1. `dragon_timer -= 1`; below zero it restarts at 100.
2. While the timer is 60 or less the dragon **hovers**: `x += dragon_speed_x`, the hover
   script plays, nothing else changes (no edge test).
3. Otherwise it **flies**: `x += dragon_speed_x`. If the target's `map_y` differs from the
   dragon's `y`, `y` moves by `dragon_speed_y` towards it. If `x > 350`: `x = 345`, bit 1 of
   `facing` flips, the x speed negates. If `x <= -20`: `x = -10`, flip, negate. `y > 200`
   becomes 0, `y < 0` becomes 200. `anim_phase` advances modulo 16 and selects the flight
   script.

With no target the target `map_y` reads as 0.

**Attack.** Tested every frame (§11.4 step 6): the dragon's `lives` is not negative, it is
active, its `engaged_with` is the current knight, and the current knight is on the touch list.
Then the dragon encounter runs:

- a computer knight loses one life and loses the encounter at once;
- a knight that is frogged or has no lives loses at once;
- otherwise the dragon fight (§10.1) decides.

A lost encounter transfers loot to the dragon (§9.4 transfer order). A won fight marks the
dragon slain (`lives = $FF`), gives the progress award and shows the hoard sheet (§12). Either
way `spent = budget`.

`dragon_active` is cleared by the arena reset that precedes every fight and by the prologue of
every place visit (§12). The next screen entry then spawns the dragon again at its start
position with a newly drawn target.

### 11.11 Turn scheduler

**Turn set-up** (every screen entry, every place visit, and inside the scheduler) for record
`turn`: `input_port = 2`; `type = $0C` unless `kind == 4`; `budget = derived_endurance << 4`;
`current` and the active-fight block's `current` take the record; the encounter kind is
cleared; the tile is recomputed and `region_code` read (§11.5).

**Turn change** (when `spent >= budget`): `skipped = 0`, then repeat this pass until it says
play or game over:

1. Clear the map modes (§11.9).
2. `turn = (turn + 1) & 3`, `spent = 0`.
3. If `turn` wrapped to 0, a **round** is over: run the clock (§11.12), then show the
   "Next Day" screen.
4. Turn set-up for the new record. Its `engaged_with` is cleared, and the computer-knight
   cells for the town target, the walk line and the roam list are reset.
5. Decide:
   - `frog_days > 0` (signed byte): the knight is passed over; next pass.
   - `lives > 0` (signed byte): **play**; the map screen is entered for this knight.
   - `kind == 4`: a dead computer knight is passed over; next pass.
   - otherwise (a human knight out of lives): `skipped += 1`; if `skipped == player_count`
     the game is **over** (farewell screen, then the title), else next pass.

A frogged knight does not count towards `skipped`. With every human frogged and no computer
knight able to play, the passes run through rounds (each with its "Next Day" screen) until a
curse runs out.

### 11.12 Clock, moon and the "Next Day" screen

The clock runs once per round (four turns), in this order:

1. `moon_subcount += 1`. If it is now above 3: `moon_subcount = 0`, `day += 1`,
   `moon_index = (moon_index + 1) & 7`, the **day turnover** runs, and `moon_frame` takes the
   entry `moon_index` of the 8-byte moon table (§9.6).
2. The **overnight** rule of §9.3 runs for all five records (curse and recency decay, then
   regeneration).

So the "Next Day" screen appears after every round and knights heal every round, while the
counter called `day` here (the one the dragon's spawn tests) and the moon step once per four
rounds. `moon_index` starts at 0 and `moon_frame` at 45; the cycle of eight moon steps takes
32 rounds, and the dragon first appears after 8 rounds.

**Day turnover**, for records 0..3 in order:

- A computer knight (`kind == 4`): `recency = $FF`; if it has lives, a negative `gold` is
  reset to 0 and it receives the wizard's gift roll (§12) as if it had visited him. Because its
  `recency` is `$FF`, the curse outcome is rerolled, so the gift is always an item, a stat
  point or gold.
- Any knight whose `life_loss` is non-zero loses one life.

**"Next Day" screen:** the map colours are stopped (fade out); the `ch.piv` picture is shown
with the "Next Day" text from `mog` and the moon as frame `moon_frame` of `ki.cel` at
(119, 12); the game waits for a fire press and release; the palette goes to black.

The palette faded in (36-tick fade-to) is the shared "last picture" palette. Decoding a picture
writes its palette words into that buffer, but `ch.piv` has 4 planes and writes only words
0..15. So the screen shows `ch.piv`'s own colours 0..15 (the night sky and trees) over the map
picture's colours 16..31, which the moon cel and the bold text use. Showing the whole map
palette instead gives orange trees on olive green: the Audit v4 "inverted colours" defect.

### 11.13 Computer-controlled knights

A knight of `kind == 4` runs this chain every frame of its turn, in this order. Every draw
named here happens whether or not its result is used.

1. **Roam list** (once per turn): for each of the 24 lairs, the Manhattan distance from the
   knight (`|dx| + |dy|` on map positions, 16-bit), or `$FFFF` for a hidden lair. The list is
   sorted by a bubble sort of 23 passes (pass *k* compares rows 0..22-*k* with their
   successor) in which a row stays only if it is strictly smaller (unsigned) than the next
   and its top bit is clear; equal rows swap.
2. **Roam target** (every frame): the low 2 bits of a draw, redrawn while zero, pick row 1, 2
   or 3 of the list (the second to fourth nearest). That lair is the roam target; a flag
   records whether it is hidden.
3. **Stat purchase:** with at least 3 `progress` points the knight buys one point of a picked
   stat (the stat pick and its draws are specified in §12).
4. **Rest item:** if `lives <= 3`, or `hp` is below a quarter of `hp_max`, and the knight owns
   a piece of slot `$00`: one is used and its rest effect applies (§9.4).
5. **Opponent:** the other three knights are sorted by Manhattan distance, nearest first
   (stable). If the knight has no `engaged_with` yet, they are tried in that order, followed
   by one empty fourth entry (quirk kept: it costs one more draw and engages nobody). A
   candidate that is itself a computer knight is skipped without a draw. If the roam target
   is hidden the first candidate is engaged at once; otherwise one draw decides: low 7 bits
   of 25 or less engage, anything else moves on. Computer knights never pick each other;
   knights out of lives are not excluded.
6. **Scroll of Acquisition:** with an opponent engaged and a count in slot `$0E`, one piece is
   used, the good jingle plays and the meeting is settled without a
   fight, the opponent as the loser (§9.4 transfer order).
7. **Speed item:** with a count in slot `$0A`, an opponent engaged, and that opponent at a
   Manhattan distance of at least `derived_endurance << 4`: the jingle plays, one piece is
   used and `budget` doubles.
8. **Shop wish** (the town target is cleared first). No wish with `gold <= 10`. Otherwise the
   first of these that applies:
   - a life (cost 25) when `gold > 25` and `lives <= 2`;
   - armour, best first, at the smith's prices (battle 75, plate 50, mail 30): the first one
     the gold pays for decides. It is wanted if the worn armour is worse (battle: if the worn
     one is not exactly battle armour); if it is not wanted, the armour step ends without a
     wish;
   - a sword. With gold for the claymore (25): a knight that wears the claymore goes on to
     the daggers; any other gets cost, kind and item set to the claymore, but the step does
     not end there (quirk kept). Then, with gold for the broad sword (10) and a sword below
     the broad sword, the cost becomes 10 and the wish stands, the item still naming the
     claymore when the first half ran. So a knight with the starting sword and 25 or more
     gold gets the claymore for 10, and a knight with the broad sword never gets one;
   - daggers (cost 2) when it carries 5 or fewer.

   When a wish stands, the **town target** is chosen: from the knight's tile, the Manhattan
   distances to tiles (12, 7) and (37, 20); the second town if strictly nearer, with walk
   target (297, 157), else the first with (94, 47).
9. **Arrival**, tested against the arrival list of the last scan (at most its first seven
   rows):
   - With a town target: when a row of kind `$19` or `$1A` is listed, the turn ends
     (`spent = budget`), the purchase is made and the target cleared. Nothing else is tested
     while a town target is set.
   - Else with an opponent engaged: if it is not listed, nothing more happens this frame. If
     it is listed, the knights meet (§10.1) and the turn ends.
   - Then (also with no opponent): if the roam target lair is listed, the turn ends. The
     computer knight does not enter the lair.
10. Terrain test (§11.6), `spent += 1`, and if not blocked one step of the **walk line**.

Steps 5 to 8 are skipped while a gate word is set; in the original nothing sets it, so they
always run.

**Walk line.** Set up on the first walk step of a turn towards, in this priority: the engaged
opponent's map position, the town target, the roam target lair's position. With `dx`, `dy` the
differences target minus knight: the x bit is right for `dx >= 0`, else left; the y bit is
down for `dy >= 0`, else up; the major axis is y when `|dy| > |dx|`, else x; the error starts
at the major length. Each step moves along the major axis and subtracts the minor length from
the error; when the error goes negative the major length is added back and the step also
moves along the minor axis. The line is not recomputed during the turn, even if the target
moves or changes, and the walker does not stop at the target: it keeps stepping until the
budget runs out, an arrival ends the turn, or the map edge clips it. Standing exactly on the
target it keeps stepping along x.

**Purchase** (on arrival in town): `gold -= cost`, then by kind: a sword or an armour replaces
the worn one (armour also recalculates `hp_max` and derived endurance); a life sets
`hp = hp_max` and `lives += 1`; daggers run the loop below.

Dagger loop (quirks kept): add one dagger; stop if `gold < 2`; stop if the 16-bit word made of
the `daggers` byte and the `type` byte equals 10 (in practice never); else subtract 2 from the
**high byte** of `gold` (512 gold) and repeat. The loop ends once the gold word is negative,
usually after two daggers; the day turnover later resets negative gold to 0.

A human knight's turn has none of this: the joystick word is the only input.

### 11.14 Order of draws on the map

For a deterministic replay the draws must happen in exactly this order. Per frame of a
computer knight's turn: the roam target (one or more draws), the stat purchase (only when it
has the points), the opponent draws (zero to four), then whatever the arrival triggers (a
meeting draws, a purchase does not). Per screen entry: the dragon's target draws, only when it
spawns. Per round: the wizard's gift rolls of the day turnover, only on a day change, for
records 0..3 in order. A human knight's frame draws nothing by itself.

### 11.15 Drawing

- **Picture:** the map is picture 8 of `test` (§3.1), shown with its own palette.
- **Sprites:** all map sprites are frames of `mi.c` (§3.2), drawn top-left at the given map
  position. The node panel text uses the small font.
- **Background layer** (rebuilt at screen entry): a marker, frame `$14`, at every visible
  lair; then every knight except the current one at its map position: frame `kind` (0..4),
  frame `kind + $2B` when frogged and alive, frame `$21` when out of lives.
- **Per frame:** the current knight with frame `kind + 5`, plus 5 in travel mode, plus 10 in
  ambush mode; the highlights the scan drew (§11.7); the dragon through its job's script.
- **Colour jobs:** a ramp with parameters (`$1F`, `$FF`, step 1) and a cycle over entries
  `$15..$17` (step 1, period `$18`). They run while the map is shown and are freed, with a
  fade out, before any other screen. T11.12 specifies the ramp from the palette-job routine
  (§8.2) before using it.

Frame numbers used: 0..4 knights, 5..9 current or highlighted knight, 10..14 travel, 15..19
ambush, `$14` (20) lair marker and dragon box, `$15`..`$1E` place highlights, 31 lair
highlight, `$20` menu panel, `$21` dead knight, `$2A` dead knight highlighted, `$2B`..`$2F`
frogged knights. Frame `$14` falls in the ambush range for kind 4; a computer knight never
enters ambush mode, so the two uses do not meet. T11.12 checks every frame number against the
real `mi.c` on a contact sheet.

### 11.16 Reading the map data from `mog`

Read as data by offset (found through the relocations of the code that loads them, §10.15):
the map node table, the terrain mask and region tables (1000 bytes each), the four lair
definition tables, the lair loot odds table, the moon table, the PRNG seed table, the 16
dragon flight scripts and the hover script, the menu strings and the 14 place names, the
"Next Day" text, the computer knights' names.

Read as code operands with the instruction word checked (§10.15): the knights' starting
positions, the smith prices the computer knight shops with, the town tiles and walk targets,
the stat price, the dragon's spawn day.

Rule constants that describe the screen or an algorithm stay in code: the clip limits, the
frame time, the menu layout, the masks and thresholds of the draws.

### 11.17 Notes and conflicts

- The reference project's summary comment for the map loop names Esc as the end-turn key; its
  code compares the translated key with the character codes of `E`, `Q` and space, and the
  menu compares with digit characters, so the translation yields characters. This section
  follows the code. T11.12 confirms it from the key translation table in `mog`.
- The computer knight's scroll rule (§11.13 step 6) reads slot `$0E`, the Scroll of
  Acquisition (§12.6): it takes loot from the engaged opponent without a fight.
- A lock cell is tested in the frame's charge step and cleared after the valley fight; nothing
  sets it. It is not modelled.
- The plan's acceptance line for T11.13 ("a four-knight round visits each knight once per
  day") is read as: each knight plays once per **round**, the "Next Day" screen follows every
  round, and the day counter and the moon step every fourth round.

---

### 11.18 Map implementation boundary and palette jobs (T11.12)

The pixel map replaces the graph completely. Actor.map_x/map_y and tile_x/tile_y
are authoritative; the map and Fight share the same Rng object and actor records.
Locations still awaiting their sheet replacement access resources through views
of those records/inventories, never through an independent map combat record.
Without decoded map data, headless title/menu/fight tests may construct a session,
but entering or stepping the map reports missing original assets, not a synthetic
road network. Data for headless rule tests is supplied explicitly.

Read the nodes through hunk0 $0E7E; terrain through hunk36 $08BA and region through
$13FE (1000 bytes each); the four seeds through hunk0 $A302. Read the default
record stores at hunk0 $5092..$50E6 with the existing checked store-run reader.
Human start/name instructions are $4FC6/$4FD4, $4FEE/$4FFC, $5014/$5022,
$4F9E/$4FAC in kind order; computer positions/names come from $4BAC..$4C58.
Lair records use the already checked four-table decoder (§10.15). Frame sizes
come from the actual mi.c, without modulo or substitute frames. It contains
47 frames (0..46); a frogged kind-4 computer requests missing frame47 in the
original. That invalid draw raises here, rather than selecting another frame. Menu strings
come from the hunk36 LEAs at $12E8, $13AC, $13C0, $13DE; character translation
uses hunk34 $07AE. The lookup has a leading zero before the raw-code table;
raw Space/$40, E/$12, Q/$10 and 1..9/$01..$09 use index raw+1. The host
serializes the translated character tested by the map/menu instructions.

Small.font actually contains72 variable-width glyphs (typically5 by6), not
the ASCII strip claimed by §3.3. Read the character map through hunk0 $9252;
character minus32 (byte wrapped) selects a glyph index. Ordinary map-menu text
uses each glyph width as its advance, without a gap, and retains indexed colors.

The map color ramp is the original palette job, not a tick-based grey pulse.
It starts with index31, target word $00FF, countdown/reload1, saved original
color, repetitions0 (infinite). Each PAL tick it calls the component step:
blue and green are compared nibble by nibble, red compares the full high byte.
On reaching target, target and saved color swap. The cycle reload is24; at
zero rotate entries21..23 right by one (direction byte1). Fade and ramp/cycle
state belong to simulation/host updates, never to render calls. Stop/restart
on screen transitions discards jobs and starts from the map palette again.

Rows retain raw keys: handles1..4 for knights; lair keys are disjoint handles
starting at6; place keys pack x/y. Only the first five arrival rows are cleared;
a full ten-row list and table reads outside loaded data raise rather than
corrupting adjoining memory. Travel skips places, knights and dragon touches
and leaves the old touch list unchanged. The scan's interval test is performed
on zero-extended wrapped word endpoints, preserving its asymmetric edges.
Menu key input is serialized in the input frame; digits select rows directly.
A menu freezes map movement until a valid digit arrives; no cancel is added.
The map frame decides status/end-turn/fire before moving, and charges before
edge clipping. Screen re-entry applies stale move without charging again.

T11.12 owns movement, terrain, scanning, menus, visibility and the turn-budget
exhausted event. The original scheduler/clock/dragon jobs consume that event
in T11.13; a host awaiting them shows TURN COMPLETE without inventing a day or
advancing a human-only scheduler. Original computer map decisions remain T11.14.
The default actor setup and lair definition allocation are present here;
new-game market/dragon/lair gift rolls remain with the original shared inventory
rules and campaign initialization tasks. A lair is hidden only by explicit loot
settlement when both its gold and all twelve counts are empty, never on a fight
win alone. Original place/loot sheets and their effects remain T11.15..T11.21;
this task dispatches existing callers and reports unavailable unscheduled ones.


### 11.19 Scheduler, clock and map-dragon integration (T11.13)

The scheduler cursor visits all four persistent records in record order, including
computer knights; the human roster is only the controller/player-count mapping.
Existing KnightId+1 handles stay stable; turn_order maps original record indices
to human selection order followed by the remaining computers. Scans, gifts and
dragon target draws use that same order. A scheduler invocation clears skipped
once, then advances records until play,
farewell, or a round boundary. At a round boundary it suspends before setting up
record0, runs upkeep once, and resumes only after the Next Day press/release.
Skipping continues with the same skipped count across that suspension. Setup
recomputes tile/region/budget without moving; screen entry applies the stale move.
The active record's engagement is cleared on turn changes. AI goal/walk/roam cells
are reset by the same boundary when T11.14 installs them. Until that task, a
computer turn is exposed as unavailable, without consuming invented moves/draws.
All-human rule/host tests exercise complete rounds independently of map AI.

**Overnight correction to §9.3:** recency subtraction is byte-wrapped; a negative
byte result becomes zero. Recency FF skips both recency and frog decay. A nonzero
frog byte decrements once. Regeneration always computes a wrapped word difference
(max minus hp), logically shifts it right2 and ORs1 when nonzero, adds it to hp,
then caps with a signed comparison. This also preserves above-maximum/overflow
cases; it is not guarded by hp < hp_max. Day turnover runs before overnight.
Computer gifts use the shared rules of §12.9/12, including the shared last-item
memory and message cycles. Retries in random selection are bounded at4096 and
raise when exhausted. Gold halves use logical word shifts: both winner's increment
and loser's replacement are floor(gold/2), so odd gold loses one. Zero lives
(exactly byte0) transfers all counts; other byte values transfer one owned slot.
The all-transfer sword typo and the dragon-type14 gold-first double-halving path
are retained. Recalculate the four knights after transfer, not the dragon.

**Checked executable reads:** moon frames through hunk0 LEA $0664; Next Day text
list through $2BA8. Its14-byte linked records hold text pointer, x/y, style word,
next pointer; loops/out-of-bounds raise. The font is bold.f, selected by the
original loader. It automatically adds narrow style8 (advance = glyph width-3);
style1 centers within the loaded span (width minus margin, read through $91F0
and $91F6). Moon x/y are operands $2BD0/$2BD4. No extra day/phase labels are drawn.
Next Day waits for an initial release, a press, then release, so held fire from
the map cannot dismiss it. The scheduler and palette fade clocks advance in host
updates, never during rendering. Farewell uses the original message text, with
its dedicated ending sequence still T11.22.

Read dragon spawn threshold at hunk36 $0692; record stores $06EA..$0710;
flight table through relocated store $0716 (16 pointers into hunk4); hover through
$07DA. The handler identity stored at $06B8 points to $07B8. Timer, speed and edge
operands are checked at $074A, $0752, $07C6, $07CE, $0820/$0828,
$083A/$0842, $0854/$085C/$0868; y speed is the data word referenced by $07FE.
Spawning clears map jobs and starts a real job in the existing Fight/ScriptEngine
with all five frame-list slots naming mi.c. The original takes that job's initial
x/height/depth from words at first-script+4/+6/+8, despite having just set the
record to (10,0,100); preserve these initial job words and its first write-back.
The subsequent type28 handler selects a flight/hover script and restarts at the
record position/facing. Retain the actual script draw offsets and mirroring.
A map entry runs existing jobs before trying to spawn, as §11.4 specifies.
Rendering consumes retained draws, never runs a job or a flight step.

The four candidates for dragon targeting are sampled by the original Rng until
a living one is found. With no living knight, no target/job is retained; invalid
endless selection is bounded as above. An attack tests the previous scan's touch
list after menu/key processing and before budget/movement. Computer targets lose
a life immediately; frogged or byte-zero-lives humans lose without an arena.
Loss transfers loot to the persistent dragon; a fought win marks lives FF and
awards the decoded progress operand. Spent becomes budget in all cases. The
existing real Dragon fight is used for other humans. Arena/place entry clears
map jobs/dragon_active; the next entry can spawn again unless lives is negative.
Clock day and loaded moon frame are passed into duel/creature runtimes. Hoard
sheet presentation remains T11.15/21; gift rolls at campaign initialization remain
the initialization follow-up recorded by T11.12.


### 11.20 Computer map integration (T11.14)

The decision order and arithmetic of §11.13/14 apply to two-VBL map frames.
Mutable AI cells belong to the campaign: sorted lair rows, chosen lair and hidden
flag, neighbor distances/handles, gate, town distances/target, cost/kind/item,
and the initialized walk line (axis bits, lengths, error). The scheduler clears
the town target, roam-built and walk-initialized flags and the engaged handle
when classifying each new turn, including record0 after the round screen.
Screen re-entry does not clear the line. All cells participate in replay hashes.

Distances subtract, take absolute values, add and wrap as words. Opponent distances
are sign-extended to unsigned longs for their stable comparison. Roam sorting uses
unsigned words but always swaps a leading negative word. Retry loops raise after
4096 failed draws rather than hanging on an invalid seed. Town distance comparisons,
walk-axis comparisons and the speed threshold use signed words. Shopping compares
signed gold and signed equipment longs. The sword's lost absolute store does not
update the item cell; the dagger store clears only that cell's high word. Dagger
debits subtract from gold's high byte, preserving its low byte.

Rest clears life_loss, restores HP, and when HP was already full increments lives;
a signed result of six or more is forced to five. Stat purchase changes the byte
and progress without recalculating derived values. Acquisition uses the existing
original loot-transfer order and recalculates the four knight records; it neither
grants progress nor ends the turn. It retains the engaged handle. The item jingles
remain with sound wiring T11.23, as do other noncombat original synth triggers.

Arrival reads the previous scan, stopping at its first zero key and testing no
more than seven rows. A town target suppresses both opponent and lair tests.
Arrival can set spent=budget, but the terrain test, one-frame charge and walk-line
error update still happen before the budget check; the map position then stays
unchanged. A meeting suspends the AI frame at arrival while the existing duel runs;
on return, only that frame's remaining terrain/charge/walk and budget tail runs,
without repeating its random decisions. Its resumed frame sets spent to the
recomputed budget before the final charge. A Dragon return resumes only the budget
tail, without another decision, charge or walk-line update. Frogged or dead
opponents settle through the existing loot rule without a fight. Full human
meeting/loot sheets stay T11.21.
The existing duel runtime handles live human opponents in either fighter position;
computer winners receive one progress and the original transfer after defeat
settlement. The human winner's loot sheet remains that later task.

`assets/ai_map_rules.py` reads hunk36 checked operands at $0F6A..$11AE for
prices, equipment IDs, caps and town tiles/targets, $0AD2 for opponent odds,
$0E36 for rest threshold; the stat-price load at $0F3C points into hunk4.
Rest caps come from checked hunk0 operands $BEA6/$BEAE. Code-hunk CRC checking
precedes reads, every immediate instruction form is checked, long immediates
are checked before reading, and relocated data pointers require their target hunk.
Rule records are frozen and contain no I/O. No prices or equipment tables are
copied from original data into the implementation. Multi-day verification uses
the real tables and original RNG, with an independent per-frame draw-order oracle
and explicit checks for sorted ties, ignored candidates, terrain and shopping quirks.

## 12. Places and Sheets (`T11.5`)

Everything that is neither the map (§11) nor a fight (§10) is one of two kinds of screen:

- a **sheet**: the knight's status panel with icons for stats, equipment and items, optionally
  beside a second panel (another knight, a lair's loot, the market stock, the smith's offer).
  One loop and one click handler serve all sheets; the sheet number selects the second panel
  and which clicks do something;
- a **picture screen**: a backdrop with a few buttons or a short animation (the two towns, the
  healer, the mystic, the dice house, the wizard, the Stonehenge ritual, text messages).

Sources: the reference project's place visit, sheet loop, click handler, item, shop, healing,
dice and ritual rules. Texts, prices, odds tables and layouts that the original holds in `mog`
are read from the user's `mog` (§12.22); numbers given here pin the behaviour. `sim/` holds the
rules and the sheet state; `app/` draws and feeds the pointer.

### 12.1 Sheet numbers

| No. | Sheet | Second panel | Opened by |
|---|---|---|---|
| 1 | Meeting | The other knight | After a knight fight, or a meeting without one (§12.18) |
| 2 | Lair loot | The lair's loot and gold | A won lair fight, or a look into a lair in travel mode (§11.8) |
| 3 | Offer | none (wide layout) | Stonehenge without the right moonstone (§12.16) |
| 5 | Smith | The smith's fixed offer | Town button |
| 6 | Market | The market stock | Town button (Highwood) |
| 8 | Acquisition | Another knight, selectable | Using the Scroll of Acquisition |
| 9 | Status / temple | none (wide layout) | Space on the map or in Highwood, home village, after a lost fight, after the wizard, after Stonehenge |
| 10 | Dragon hoard | The dragon's inventory and gold | A won dragon fight |
| 11 | Wyrm | Another knight, selectable | Using the Scroll of the Wyrm |

### 12.2 The sheet loop

**Open** with a sheet number: the change counter and the done flag are cleared, the screen
fades out, the pointer is switched on, the button text tables are built (§12.4), the sheet is
drawn (below).

**Loop**, until the done flag is set: hit-test the pointer against the region list. A region
under the pointer has its text line shown. If a region is under the pointer and the pointer is
not locked, the click handler runs (§12.5). Then flip and redraw the moving parts.

**Pointer.** A hardware-sprite pointer moved by the joystick of the knight the sheet belongs
to, 2 pixels per VBL, clamped to x 0..314 and y 0..195. It is "locked" except while fire is
held, so a click is: fire down while over a region. There is no debounce in the loop itself;
every handler that changes something redraws the sheet, which takes long enough to act as one.

**Close:** fade out, pointer off. If the hand-over flag is set (sheet 11), the dragon's
`engaged_with` takes the knight shown in the second panel and the flag is cleared.

**Draw** (also after every change):

1. Clear the region list. Draw the panel frames from a fixed draw list (§12.3); the frame
   piece with number `$1A` also registers the **done** region at its position (25 x 117).
2. Pick the two knights of the sheet and fill the two button arrays (§12.4).
3. Left panel: the sheet's knight (§12.3), with the "use" array. Sheets 9 and 3 use the wide
   layout: everything shifted right by 74 pixels, one panel only.
4. Right panel, shifted by 150 pixels, with the "take" array:
   - sheet 2: the lair's loot (§12.8);
   - sheet 1: if the other knight has no lives the change counter is cleared; if the change
     counter is non-zero the sheet number becomes 9 and drawing starts again (so one take from
     a living knight ends the meeting, a dead knight can be emptied); else the other knight's
     panel;
   - sheet 11, and sheet 8 while nothing has changed: the other knight's panel and the
     **next knight** button (frame `$2C` at (146, 71), 20 x 20);
   - sheet 8 after a change: the change counter is cleared, the sheet number returns to the
     sheet the scroll was used from, drawing starts again;
   - sheet 5: the smith's icons; sheet 6: the market stock as an inventory; sheet 10: the
     dragon's inventory and gold.
5. Set the four colours of the panels: two for the sheet's knight and, on sheets 1, 8 and 11,
   two for the other knight, by `kind` (the class colour pairs come from `mog`, §12.22).
6. Flip and show.

### 12.3 Panel layout

All icons are frames of `ki.cel` (§3.2); positions are for the left panel before any shift.
Every icon drawn through the icon routine also registers a region of the icon's size carrying
`{button index, type, mask, field}` (§12.4).

**Knight panel** (derived values are recalculated first, §9.3):

| Element | Frame | Position | Step | Count | Button | Type | Field |
|---|---|---|---|---|---|---|---|
| Name (text) | – | (60, 24) | – | – | – | – | – |
| Strength row | `$26` | (41, 35) | 0 | `strength` | 0 | 3 | `$46` |
| Constitution row | `$27` | (41, 42) | 0 | `constitution` | 2 | 3 | `$47` |
| Endurance row | `$28` | (41, 49) | 0 | `endurance` | 1 | 3 | `$48` |
| Gold icon | `$2A` | (89, 42) | 0 | 1 | 4 | 2 | `$4A` |
| Stat bars (draw list) | `$29`, `$2A`, `$2B` | (89, 35), (89, 42), (89, 49) | – | – | – | – | – |
| Numbers: strength, constitution, endurance | – | x 63, y 35 / 42 / 49 | – | – | – | – | – |
| Numbers: progress, gold | – | (112, 35), (112, 42) | – | – | – | – | – |
| "hp/max" text | – | (112, 49) | – | – | – | – | – |
| Lives | `$11` (second panel `$12`), +2 while frogged | (41, 59) | 19 | `lives` (a byte with bit 7 set shows 5) | 3 | 3 | `$49` |
| Daggers | `$15` | (38, 78) | 10 | `daggers` | 5 | 3 | `$4C` |
| Sword | the sword code (capped at `$19`) | (43, 95) | – | 1 | 10 + (code - `$16`) | 2 | `$58` |
| Armour | the armour code | (29, 112) | – | 1 | 6 + (code - `$1B`) | 2 | `$5C` |

A stat row with step 0 draws all its icons on one spot; the number beside it carries the value.

**Inventory icons** (same routine for a knight, a lair's loot, the market stock, the hoard):

| Slot (§12.6) | Frame | Position | Step | Shown at most | Button | Type |
|---|---|---|---|---|---|---|
| `$02` | 9 | (87, 164) | 16 | 4 | 19 | 5 |
| `$06` | 3 | (33, 149) | 12 | 3 | 20 | 1 |
| `$08` | `$0A` | (69, 133) | 20 | 4 | 21 | 1 |
| `$14` keys, bits 0..3 | 5, 6, 7, 8 | x 76, 94, 112, 130, y 111 | – | 1 each | 17 | 1, mask = the bit |
| `$16` moonstones, bits 0..3 | 2, 1, 0, 0 | from (103, 111), see below | – | 1 each | 14, 15, 16, 14 | 1, mask = the bit |
| `$00` | 4 | (31, 165) | 13 | 4 | 18 | 5 |
| `$0A`..`$12` (five scrolls) | `$0B`..`$0F` | columns from x 30, 25 apart, y 183 | – | 2 each (the second as frame `$10`, 4 right) | 22..26 | 5 |

Quirk kept: the moonstone icons set their x once and advance by whatever step the previously
drawn row left behind, so their spacing depends on what else the inventory holds.

**Lair loot panel** (sheet 2): three decoration frames (`$1F` at (58, 46), `$20` at (76, 33),
`$21` at (58, 60)); if the lair's gold word is non-zero, the gold icon `$25` at (87, 64)
(button 4, type 2, field `$4A`) with the text "*n* gp." at (92, 91); the sword-of-sharpness
icon `$19` at (43, 95) when slot `$04` is non-zero (button 13, type 1, field 4); then the
inventory icons of the loot. The **hoard** (sheet 10) is the same without the decoration,
using the dragon's inventory and `gold`. The **market** panel is the stock's inventory icons
plus the sword-of-sharpness icon.

**Smith panel** (sheet 5): mail `$1C` at (23, 145), plate `$1D` at (64, 145), battle armour
`$1E` at (106, 144), type `$A` with masks 1, 2, 4, field `$5C`; broad sword `$17` at
(50, 107) and claymore `$18` at (47, 125), type `$A`, masks 1 and 2, field `$58`; thirteen
dagger icons `$15` from (32, 89), 9 apart, type `$A`, field `$4C`.

T11.15 checks every frame and position against the real `ki.cel` on a contact sheet.

### 12.4 Regions and button text tables

A **region** is a rectangle with: the button cell it belongs to, an id long (7 marks the done
region), a word whose low nibble is the **type** and whose next nibble is the **mask**, and a
**field** word (an inventory slot offset `$00`..`$16`, or an actor field offset: `$46`..`$48`
stats, `$49` lives, `$4A` gold, `$4C` daggers, `$58` sword, `$5C` armour).

A **button cell** holds a text and a flags word. There are two arrays of 27 cells, "use" for
the left panel and "take" for the right one; a region's button index selects the cell. Index
order: 0 strength, 1 endurance, 2 constitution, 3 lives, 4 gold, 5 daggers, 6..9 the four
armours, 10..13 the four swords, 14..16 moonstones, 17 key, 18..26 the items in the order of
§12.6.

Seven text tables of 27 entries exist; the sheet number picks one per array:

| Table | Texts | Array and sheets | Flags |
|---|---|---|---|
| Plain | names only | default for both arrays | `$03` |
| Temple | names; "drink" / "use" / "cast" wording for items | use, sheet 9 | `$13` |
| Offer | blank except moonstones, key and "offer ..." for items | use, sheet 3 | `$13` |
| Sell | "sell ..." | use, sheet 6 | `$13` |
| Take | "take ..." | take, sheets 1, 8, 10, and 2 unless in travel mode | `$23` |
| Buy | "buy ..." | take, sheets 5 and 6 | `$23` |
| Stat | "increase ..." for the three stats | see below | – |

Bit 4 (`$10`) of the flags means "may be used", bit 5 (`$20`) "may be taken", bit 6 (`$40`)
"stat may be bought".

**Stat buttons.** After the use array is filled, if the sheet's knight has at least 3
`progress` points, each of the first three use cells whose stat is not exactly 5 gets the
"increase" text and has bit 6 toggled. Quirk kept: this happens on every sheet, not only on
sheet 9.

### 12.5 Click dispatch

1. The **done** region sets the done flag.
2. The **next knight** button advances the second panel to the next of the four knights that
   is not the sheet's knight (a counter stepping modulo 4), and redraws.
3. Otherwise by region type, with the flags of the region's button cell:

| Type | Action |
|---|---|
| 5 | On sheet 6: the market click (§12.11). Else, without flag bit 4: the take action if flag bit 5 is set, else nothing. With bit 4: **use the item** (§12.7). |
| 1 | On sheet 6: the market click. Else the **take** action if flag bit 5 is set (§12.8). |
| 3 | With flag bit 6: **buy the stat** of the field (§12.9), redraw. Then, with flag bit 5 and field `$4C`: the dagger top-up (§12.8). |
| `$A` | The smith click (§12.10). |
| `$C` | **Give up** one piece of the field's slot (§12.16), redraw. |
| other | With flag bit 5, by field: `$4A` take gold, `$5C` take armour, `$58` take sword (§12.8); redraw. |

On sheet 3 every icon drawn through the sheet-aware routine has its type forced to `$C`; the
keys and moonstones are drawn through the plain routine and keep type 1, so they cannot be
offered.

Consequences that the implementation must reproduce: items can be **used** only on sheet 9
(the only sheet whose use array has bit 4); things can be **taken** only from the right panel
of sheets 1, 2, 8 and 10; in travel mode sheet 2 shows the loot with the plain table, so
nothing can be taken (the Gem of Seeing only looks).

### 12.6 Items

The display names come from the text tables in `mog`; they complete §9.4.

| Slot | Name | Kind |
|---|---|---|
| `$00` | Potion of Healing | used (rest) |
| `$02` | Gem of Seeing | used (travel mode, §11.9) |
| `$04` | Sword of Sharpness | worn while held |
| `$06` | Ring of Protection | passive (+20 `hp_max` each) |
| `$08` | Talisman of the Wyrm | passive (halves incoming damage per piece, §10.11) |
| `$0A` | Scroll of Haste | used (gamble on the move budget) |
| `$0C` | Scroll of the Hawk | used (ambush mode or a random spot, §11.9) |
| `$0E` | Scroll of Acquisition | used (sheet 8) |
| `$10` | Scroll of the Wyrm | used (sheet 11: sends the dragon after a knight) |
| `$12` | Scroll of Protection | used when challenged (§12.18) |
| `$14` | Keys to the Valley | four flag bits |
| `$16` | Moonstones | four flag bits |

Moonstone texts by bit: bit 0 "new moon", bit 1 "full", bit 2 "half"; bit 3 has no text or
icon of its own and shows as bit 0's text with bit 2's icon.

### 12.7 Using an item

The count of the slot drops by one (unchecked) and the slot is remembered as the **last item
used** (the callers of §12.16 and §12.18 read it). Then by slot:

| Slot | Effect |
|---|---|
| `$00` | **Rest:** `life_loss` cleared; if `hp == hp_max` the knight gains a life (a result of 6 or more becomes 5); `hp = hp_max`. Click sound. |
| `$0A` | Percent draw. Above 10: good jingle, `budget` doubled. Else: bad jingle, `bad_luck = 1`, `budget` halved. |
| `$0E` | Good jingle; the current sheet number is remembered, the second panel starts at the first other knight, and sheet 8 opens in place of this one. |
| `$02` | Good jingle; travel mode on with the position saved (§11.9); the sheet ends. |
| `$0C` | Percent draw. Above 15: good jingle, ambush mode on; else bad jingle and the random spot (§11.9). The sheet ends either way. |
| `$10` | Good jingle; the hand-over flag is set, the second panel starts at the first other knight, and sheet 11 opens in place of this one. |
| `$12` | Percent draw. Above 10: the sheet ends, good jingle. Else: the sheet ends, bad jingle, `bad_luck = 1`, bad jingle again. |
| others | Only the decrement. |

The draw is made only for slots `$0A`, `$0C` and `$12`. The sheet is redrawn unless another
sheet opened. On sheet 8 the player takes one thing from the shown knight with the take
actions; the sheet then returns to the one the scroll was used from. On sheet 11 nothing can
be taken; the knight shown when the sheet is closed becomes the dragon's target.

### 12.8 Taking

Each successful take adds one to the change counter and redraws.

**Take an item** (type 1 or 5 region of the right panel, click sound first):

- Keys and moonstones: the whole mask byte moves (OR into mine, source cleared).
- Slot `$04`: the source loses one; my `sword = $19`; except on sheet 2, my count gains one
  and the other knight's `sword` falls back to `$16`.
- Any other slot: one piece moves. For slot `$06` my `hp += 20` first.
- Then my `hp_max` and derived endurance are recalculated.

No check is made that the source holds a piece; counts wrap in the byte (the sheet only shows
regions for what is there).

**Take gold** (field `$4A`): gold moves one piece at a time from the source (the other
knight's `gold`, or on sheet 2 the lair's gold word) until the source is empty or my gold is
**exactly** 150; a knight already above 150 takes everything. A coin sound plays when the
source ran empty.

**Take armour** (field `$5C`): if my armour code is lower than the other's, I take it and the
other falls back to `$1B`. A hit-point amount also moves from the other's `hp` to mine; the
original looks it up by **my previous** armour with a byte offset into a word table, which
yields 0 when I wore padded armour or mail and 10 when I wore plate (quirk kept).

**Take sword** (field `$58`): if my sword is `$19`: the other's slot `$04` loses one, and
except on sheet 2 my slot `$04` gains one and the other's sword becomes `$16`; then derived
values are recalculated. Otherwise, if my sword code is lower than the other's, the two codes
swap.

**Dagger top-up** (type 3, field `$4C`, flag bit 5): daggers move one at a time from the
other knight until I have exactly 10 or the other has none.

### 12.9 Buying a stat

With flag bit 6 on the clicked stat row: the stat byte of the field gains one; constitution
also gives `hp += 10`; `progress -= 3`; derived values are recalculated. The price (3) is a
word in `mog`. Because of the stat-button rule (§12.4) a stat at exactly 5 cannot be bought,
one above 5 can.

**Stat pick** (used by the wizard, the mystic, the valley and the computer knights): if all
three stats are 5 or more, no stat is picked. Otherwise: one draw, low 4 bits, a value above
8 has 7 subtracted; the result 0..8 indexes a nine-row table in `mog` that names a stat per
row (the original order is strength, endurance, constitution, endurance, strength, endurance,
constitution, constitution, strength); redraw while the picked stat is exactly 5.

The computer knight's purchase (§11.13 step 3): with at least 3 `progress`, a stat pick; if a
stat was picked it gains one and `progress -= 3`. No recalculation is done there.

### 12.10 The smith (sheet 5)

Fixed prices, read from `mog`; a purchase needs `gold >= price` (signed).

- **Armour** (the lowest set mask bit): mail 30, plate 50, battle armour 75. The armour is
  replaced without comparing with the worn one (a cheaper one replaces a better one); `hp`
  gains 10 / 20 / 30; `hp_max` is recalculated.
- **Sword:** broad sword 10, claymore 25; refused when the worn sword's code is the same or
  higher.
- **Dagger:** 2 gold each while the knight carries fewer than 10.

### 12.11 The market (sheet 6)

The stock is an inventory filled at the start of the game (§11.3) and changed only by trade.
The price list has one word per slot (index = slot / 2), set up from `mog` when the text
tables are built. The pointer's x decides: at x >= 160 (the stock panel) a click **buys**,
left of it a click **sells**.

- **Buy:** refused when the price exceeds the gold. Else `gold -= price`; keys and moonstones
  move by mask (OR into the knight, XOR out of the stock) with no recalculation; any other
  slot moves one piece; slot `$06` gives `hp += 20` and recalculates `hp_max`. For the other
  counted slots the original runs the recalculation on the wrong record (the inventory
  address instead of the knight), which writes into unrelated memory. That write is **not**
  reproduced here (a deliberate deviation); the knight's own values are not recalculated on
  this path, exactly as in the original. The buy sound plays before the price test.
- **Sell:** the piece (or mask) moves to the stock; `gold += price >> 1`, then gold above 150
  is cut to 150; selling slot `$04` puts the knight's sword back to `$16`; `hp_max` is
  recalculated. Then redraw and the sell sound.

### 12.12 Gift rolls

Three routines fill inventories at random; the new game (§11.3), the wizard and the valley
use them.

- **Gold roll:** one draw, low 5 bits; above 21 subtract 10; add 10. The result 10..31 is
  added to the knight's `gold` (no cap) or to a lair's gold word.
- **Item roll** into a target inventory: a percent draw is matched against a ten-band table in
  `mog` (limits 25, 35, 45, 55, 65, 75, 80, 85, 94, 100 for slots `$00`, `$02`, `$04`, `$06`,
  `$08`, `$0A`, `$0E`, `$0C`, `$10`, `$12`). The roll is repeated when the slot equals the
  previous gift (one shared memory, initially none), or when it is slot `$04` and the target
  already holds one. The memory is updated before the second test (so a refused sword also
  blocks a sword on the next try). The slot's count gains one. When the target is a knight's
  own inventory, slot `$04` sets `sword = $19` and slot `$06` gives `hp += 20` and a
  recalculation.
- **Wizard's gift:** one percent draw plus the knight's `recency` (byte addition, wrapping):
  up to 30 an item roll into the knight's inventory; up to 70 a stat point by the stat pick
  (no stat pickable: the whole gift is rolled again; constitution recalculates `hp_max` and
  then adds 10 `hp`; endurance recalculates derived endurance); up to 90 a gold roll; above
  that the frog curse, `frog_days = 3`, except that a knight with `recency == $FF` rolls the
  whole gift again. Which of several message variants is shown cycles per kind (four item
  messages, three gold messages).

### 12.13 The visit: prologue, classification, exits

Choosing a place in the node menu (§11.8) runs, before anything else: map colours off (fade
out), turn set-up (§11.11), the arena reset (all jobs and fight objects freed, fighter script
tables rebuilt, fade out, key state cleared), and `dragon_active = 0`.

| Id | Place | Section |
|---|---|---|
| `$15`..`$18` | Home village | §12.14 |
| `$19` | Highwood | §12.15 |
| `$1A` | Waterdeep | §12.15 |
| `$1B` | Stonehenge | §12.16 |
| `$1C` | Valley of the Gods | §12.17 |
| `$1E` | The wizard's tower | §12.20 |
| other | Nothing happens; the id is returned | – |

**Exits.** Three shared tails, each falling into the next:

1. **Temple exit:** sheet 9, then
2. **Spent exit:** `spent = budget` (the turn is over), then
3. **Set-up exit:** the map scene set-up of §11.3 (jobs freed, colours stopped, turn set-up,
   all knights recalculated, keys cleared) and a non-zero result, which re-enters the map
   screen.

### 12.14 Home village

The knight gains a life if it has fewer than 3 (signed byte). Then the temple exit: the status
sheet, where progress can be spent and items used, and the turn ends.

### 12.15 The towns

**Loading:** the loading-text screen is shown with the town's text; the town picture
(`HighWood.PIV` or `WaterDeep.PIV`, §12.22) is loaded with its palette.

**Screen:** the pointer starts at (290, 100) in Highwood and (30, 100) in Waterdeep. Five
button regions 64 wide are registered, ids 1..5 from top to bottom:

| | x | y of buttons 1..5 | Heights |
|---|---|---|---|
| Highwood | 256 | 30, 66, 106, 140, 183 | 16, 16, 16, 26, 12 |
| Waterdeep | 0 | 26, 63, 101, 134, 182 | 16, 16, 16, 31, 12 |

The picture fades in. The loop then polls: if the pointer is over a button and fire is
pressed, the button's action runs and the screen is drawn again; otherwise the keyboard is
read.

| Id | Highwood | Waterdeep |
|---|---|---|
| 1 | Smith (sheet 5) | Smith (sheet 5) |
| 2 | Dice house (§12.21) | Dice house |
| 3 | Healer (§12.19) | Healer |
| 4 | Market (sheet 6) | Mythral the mystic (§12.19) |
| 5 | Exit: pointer off, spent exit | Exit |

**Space key:** in Highwood it opens the status sheet (sheet 9). In Waterdeep the original
does not load a sheet number and opens the number the key translation left behind, the space
character's code 32. The sheet loop treats an unknown number as a sheet with the left panel
only, the narrow layout and the plain tables: the knight's panel can be looked at, nothing
can be used. The sheet loop therefore accepts any number, and Waterdeep's space key passes 32
(quirk kept).

A town visit always ends the turn (the only way out is the exit button).

### 12.16 Stonehenge

**Match test.** With the knight's moonstone mask and the current `moon_frame`: bit 2 or bit 3
with frame 46, bit 1 with frame 45, bit 0 with frame 49.

**No match: the offer.** A recoloured message screen with the offer text; wait for fire; fade
out. The last-item memory is set to "none" and sheet 3 opens. There every item icon is a
give-up region: clicking one drops one piece of that slot (slot `$04` also puts the sword
back to `$16`), records the slot as the last item, recalculates and ends the sheet. If an
item was given: the **ritual screen** plays (below) and the knight is blessed: one life more
unless it has exactly 5, `hp_max` recalculated, `hp = hp_max`, `life_loss` cleared. Then fade
out and the temple exit.

**Ritual screen:** all jobs freed; the recoloured message screen with the ritual text;
`Hen1.p` loaded with its palette, `Hen1.c` as the cel set, the `he.a` sound bank; two jobs
(type `$28`) run the two ritual scripts at (160, 0, 100); the knight's four class shades are
written into the palette from entry 8 on; three sounds start; the palette fades towards the
picture's with period 4. The loop runs the jobs every 6 VBLs until the first script calls the
type handler, which ends the screen; fade.

**Match: the ending.** The **ending code** is built: bit 0 for frame 46, bit 2 for frame 45,
bit 1 for frame 49; bit 3 for knight kind 3, bit 4 for kind 0, bit 5 for kind 1, bit 6 for
kind 2; bit 7 is always added. The quest-complete message is shown, 20 frames pass, fire is
awaited, and the game hands over to the `program` executable with the code (§12.23). It does
not return.

### 12.17 The Valley of the Gods

Without all four keys (`$0F`): a message, wait for fire, and the set-up exit; the turn is
**kept**.

With them: turn set-up, the guardian arena (§10.3), the fight.

- **Lost:** turn set-up; a stat pick, then two lives are lost and the picked stat drops by
  one unless it is 1. (With no stat pickable the original decrements an unrelated byte of the
  record; here nothing is lowered.) Then the temple exit.
- **Won:** `progress += 3`, the keys are taken away; a recoloured message; wait for fire, then
  10 frames; key state cleared, turn set-up, and a **moonstone**: the bit numbered by the low
  2 bits of a draw is set in the knight's moonstone mask. The original returns the result of
  that draw to the map, so a draw of 0 leaves the map screen not re-entered; here the screen
  is always re-entered. The turn is kept.

### 12.18 Knight meets knight

Started from the node menu on another knight, or by a computer knight's arrival (§11.13), with
a first knight (the one whose turn it is) and a second.

1. Map colours off. The pair becomes `current` and `opponent` of the active-fight block.
2. If the second knight is frogged or has no lives, there is no fight: continue at step 6 with
   the first knight as the winner.
3. **Scroll of Protection.** If the second knight is human and holds one: a message screen
   naming it, wait for fire; then sheet 9 opens **for the second knight** (it becomes
   `current` for the length of the sheet). If the last item used there is slot `$12`: with
   `bad_luck` clear the fight is avoided and the map returns; with `bad_luck` set the fight
   goes ahead.
4. Two computer knights never fight: back to the map.
5. **Fight** (§10.1, duel arena). Both lost: a human first knight sees sheet 9; back to the
   map. If the first lost, the pair is swapped so that the winner is first.
6. **Settlement.** A computer winner gets `progress + 1` and the loot transfer of §9.4, and
   the map returns. A human winner gets sheet 1 with the loser in the second panel; the pair
   is swapped back afterwards.

### 12.19 Donation screen, healer and mystic

Both screens load their picture (`HEA.piv` / `MYS.piv`) and the cel set `mys.cel`, put the
pointer at (160, 170), show a greeting text, start four synth voices and wait for fire. Then
the **donation screen**:

- the knight's gold becomes the **purse**, the donation starts at 0;
- drawn: the portrait (frame 0 mystic, 1 healer) at (2, 162) and (270, 162); button frames 4
  ("-") at (144, 169) and 5 ("+") at (162, 169), 3 (accept) at (131, 185), 2 (leave) at
  (173, 185); the purse at (20, 175) and the donation at (288, 175) with their labels;
- regions: "-" 14 x 8, "+" 14 x 8, accept 20 x 8, leave 32 x 8;
- "+" moves one gold from purse to donation, "-" one back, each followed by a redraw and a
  2-frame wait; **leave** clears both amounts and returns; **accept** writes the purse back
  as the knight's `gold` and returns.

**Leave** never touches the knight's `gold`; **accept** pays the donation whatever the service
then gives.

**Healer.** Not accepted: the refusal text. Accepted with a donation of 0: the "nothing given"
text. Otherwise, while at least 10 remains of the donation: clear `life_loss` if set; if
`hp != hp_max`, set `hp = hp_max` for 10 and go round again; else, unless `lives` is exactly
5, add a life for 15 and go round again; else stop. (The life step does not test that 15
remain; the donation word may go negative, which ends the loop.) Whatever remains of the
donation is lost. The reply text depends on whether less than 10 was given and whether a
curse was lifted.

**Mystic.** No donation: the "nothing given" text. Not accepted: the refusal text. Otherwise
the gamble: a stat pick (§12.9), then a percent draw plus a bonus by donation size (up to 9
gold +20, up to 19 +10, up to 29 0, up to 39 -10, up to 49 -20, up to 250 -30; table in
`mog`; above 250 the original reads past the table, here the bonus is 0). A sum of 50 or more
**wins**: the picked stat gains one (constitution also `hp += 10`), derived values are
recalculated; with no stat pickable the "nothing to raise" text. Otherwise it **loses**: a
stat of 1 stays ("unchanged" text); else it drops by one (constitution also `hp -= 10`, not
below 1) and derived values are recalculated. Larger donations lower the odds; the original
table is kept as it is.

Both screens end with the reply shown, a 50-frame wait, wait for fire, fade.

### 12.20 The wizard's tower

1. `WI2.P` and `WI1.P` are loaded with their palettes and the cel set `Wi1.C` (§12.22); the
   knight's five class shades are written into the palette from entry 6 on.
2. **Arrival:** a job (type `$28`, at (160, 0, 100)) runs the tower script; the greeting is
   printed at (150, 5); four synth voices start; the palette fades in; the loop runs every 5
   VBLs until fire.
3. **Gift:** the wizard's gift is rolled (§12.12). The second picture is shown with the gift
   text at (50, 130) and two jobs running the gift scripts; loop every 6 VBLs until fire.
4. **Farewell:** the first picture again with a speech chosen by the kind of gift (item, stat,
   gold, curse, or an omen text otherwise); loop every 4 VBLs until fire.
5. `recency = 70`. Fade out.

Then sheet 9 and the spent exit: the visit uses up the turn. A recent visit shifts the next
gift roll towards gold and the curse, and `recency` falls by 10 per round (§9.3).

### 12.21 The dice house

Not entered with `gold <= 0`. `tav.piv` is the room, `dice.piv` the table shown for a roll,
`dice.cel` the dice and the thrower. Regions: five bet buttons at x 268, y 38, 66, 94, 121,
147 (43 x 24) for bets of 1..5 gold, and a leave button at (268, 181) (45 x 16). The knight's
five class shades go into the palette from entry 6 on; three sounds start.

**Round:** leave at once if `gold <= 0`. Show the room, the gold at (282, 14), and start the
thrower's job (type `$44`) on its idle script. Its handler, once per job frame:

- fire not pressed, or pressed over nothing: stay idle;
- fire over a bet button: if the bet exceeds the gold, stay idle; else `gold -= bet` and the
  throw script starts;
- fire over leave: the screen ends;
- when the throw script ends: the roll.

**Roll:** three dice, each the low 3 bits of a draw, redrawn while above 5. They are drawn on
the table picture as frames of `dice.cel` at (115, 15), (49, 38), (75, 88) in the order
rolled, then sorted ascending and looked up in the payout table in `mog`: rows of three dice
values and a multiplier (the original has eleven rows with multipliers 4, 5, 6, 8, 10, 12,
14, 16, 18, 20, 30). A listed combination pays `bet * multiplier` into `gold` (no cap) with
the winning text; any other loses the stake. After 20 frames and a fire press the next round
starts.

### 12.22 Reading the place data from `mog`

Read as data by offset (through the relocations of the code that uses them, §10.15): all
texts (button texts, item names, greetings, replies, messages, speeches), the file name
strings of the pictures and cel sets (which is also how the exact spelling and case of
`HighWood.PIV`, `WaterDeep.PIV`, `HEA.piv`, `MYS.piv`, `mys.cel`, `tav.piv`, `dice.piv`,
`dice.cel`, `Hen1.p`, `Hen1.c`, `he.a`, `WI1.P`, `WI2.P`, `Wi1.C` is confirmed), the item
band table, the lair loot odds, the stat row table, the mystic's bonus table, the dice payout
table, the panel draw lists, the scripts of the dice thrower, the ritual and the wizard, the
loading-text cycle.

Read as code operands with the instruction word checked (§10.15): the smith's prices, the
market price list, the stat price, the healer's prices and caps, the gold cap, the village
life cap, the wizard's roll limits, the class colour pairs and shades, the town button
layouts.

Rule constants that stay in code: region types and flag bits, the icon positions of §12.3
(they are immediates spread over one long routine; T11.15 may move them to operand reads if
the contact sheet shows a mismatch), the order of tests in every rule.

### 12.23 Ending, farewell and message screens

- **Message screen:** `message.piv` with a list of text lines from `mog`, in the picture's
  palette. The **recoloured** variant overwrites palette entries 1..6 with a dark red ramp.
  The **loading-text** variant shows the next of 14 texts in a cycle before fights (§10.2).
  Towns use the separate fixed welcome list with a substituted town name (§12.33).
- **Farewell** (quit key, or no human knight left, §11.11): the farewell text on a message
  screen; wait for a fire press and release; map colours off; back to the title.
- **Ending:** Stonehenge with the right moonstone stores the ending code (§12.16) where
  `program` reads its start flags and starts `program` again; with bit 7 set, `program` plays
  the ending sequence with `vmusic.cmp` (§7.4), choosing the moon and knight colours from the
  other bits. T11.22 specifies that sequence from `program`.

### 12.24 Corrections to earlier sections

- **§9.5, lair `occupied` (+8):** the word is the lair's **gold**. Gold rolls at the start of
  the game add to it, the loot sheet's gold icon shows and empties it, and the lair stays on
  the map while it or the loot inventory is non-zero (§11.9).
- **§9.4 names:** given by §12.6. Slot `$0E` is the Scroll of Acquisition and slot `$10` the
  Scroll of the Wyrm; "opens the exchange sheet 8 / hand-over sheet 11" described their
  mechanics correctly.
- **§9.6, moonstone pairing:** the damage rule (§10.11) pairs bits 0 and 3 with frame 46, bit
  1 with frame 45 and bit 2 with frame 49. Stonehenge (§12.16) pairs bits **2** and 3 with
  frame 46, bit 1 with frame 45 and bit **0** with frame 49. The two rules differ on bits 0
  and 2 in the original; both are kept as they are.
- **§11.8 / §11.9, travel mode:** entering a lair in travel mode opens the loot sheet with
  the plain table, so the Gem of Seeing shows a lair's contents and nothing can be taken.
- **§11.13 step 6 and §11.17:** the scroll the computer knight uses against an engaged
  opponent is the Scroll of Acquisition (slot `$0E`); there is no conflict with §9.4.
- **§10.1:** "temple sheet" and "status sheet" are the same sheet 9.

### 12.25 Sheet data initialization (T11.15a)

The button text tables occupy seven consecutive BSS arrays, each with 27 long
cells. They are populated at runtime, rather than stored as pointer arrays on
disk. For the supported code CRC, execute the bounded store run in hunk0 from
$C6E0 up to $CBF4, checking the terminating LEA instruction. The existing
StoreRun decoder handles its relocated loads and stores and its one counted
fill loop. Sort the seven loaded BSS table starts by offset. Their meanings in
that order are stat, temple, take, buy, offer, sell and plain.

Each populated cell is a relocated long into hunk4 naming an ASCII string.
An unwritten cell is the original zero-initialized BSS value, represented as
None. An explicit pointer to an empty string remains an empty string. This
distinction preserves sparse smith and stat tables. Reject unexpected table
counts, overlapping arrays and pointers into other hunks. Never fill missing
entries with invented labels.

The panel renderer loads the narrow and wide draw lists through checked LEAs
at hunk0 $AF32 and $AF4A. Each entry contains four big-endian words, frame,
signed x, signed y and orientation. A negative frame word terminates the list.
Decode all four words and retain list order. Preserve the last word unchanged
for the presenter. Reject unterminated lists. Frame sizes and the visual meaning of each
orientation are checked against ki.cel by the presenter subtask.

This data-only boundary uses the existing asset-to-simulation layering. It
introduces no second sheet state machine. T11.15b consumes these immutable
records for the sheet loop, rendering and campaign callers of §12.2–9.
The base 32-word sheet palette is loaded through the checked LEA at $AE56.
Class colour pairs are checked MOVE.W-to-postincrement operands at $AE88,
$AE9C, $AEC4, $AEB0 and $AECE, each followed four bytes later by its shade.
This order produces class rows 0..4. The presenter substitutes the first pair
at palette entries 28..29 and, for a second knight, the second pair at 30..31.

### 12.26 Shared sheet implementation boundary (T11.15b)

The app owns decoded cels, palette and immutable sheet rules. Simulation owns
the sheet number, owner/opponent handles, pointer position, last item, change
counter, done/hand-over flags and ordered icon, text and hit-region lists.
Opening and every action rebuild those lists and recalculate displayed actors.
Rendering consumes them without changing simulation or drawing random numbers.
Joystick movement is two pixels per PAL tick with the bounds of §12.2. Held
fire dispatches each tick while over a live region. A rebuild immediately
removes regions for exhausted items. No extra repeat delay is introduced.

The common status scene hosts sheets. Space opens sheet9. A lair win opens
sheet2 before returning to the map. Travel inspection opens sheet2 read-only
and restores the saved position on closing. Empty cleared lairs open their loot
without another fight, using the engine's signed nonpositive exhaustion test.
The stored count remains the original raw word, including underflow on simultaneous
last deaths. Closing a normal loot sheet tidies that lair. Unknown
sheet numbers use the original plain, narrow left panel. Sheets5/6 receive
their transaction handlers in T11.17, and their callers arrive with T11.16.
Protection-dialog and place-specific callers remain their scheduled tasks.

Scrolls8/11 reuse the loop and cycle opponents in original record order.
The previous sheet is retained for Acquisition, and closing Wyrm writes its
selected opponent into the dragon's target. All mutable sheet cells and outputs
are included in the host hash. The existing original PRNG and map-mode helpers
implement item effects. Sheet sound events are retained in order for T11.23.
Sound playback does not drive rules or consume simulation randomness here.

Additional checked operands are read from hunk0 for the exact gold cap ($C264),
gamble thresholds ($BEC8/$BF4C/$BFB6) and rest caps ($BEA6/$BEAE). The armour
transfer's byte-indexed word table is reached through the checked LEA at $C19A.
The stat price uses the existing checked map-AI decoder. Out-of-range sprite,
button and armour-table accesses raise instead of substituting assets or reading
unmodeled memory. No game tables are copied into source.

### 12.27 Runtime town screen data (T11.16a)

For the supported mog code CRC, read town geometry from the two button-building
routines in hunk0 ($153C..$15FC, Highwood; $15FE..$16BE, Waterdeep).
These are instruction operands, not on-disk arrays. Both routines clear the
button list, then rewrite one 24-byte scratch record before registering each
of its five snapshots. Check every relocated scratch-record LEA and registration
call. All five loads must name the same record in hunk11; its allocation must
contain 24 bytes. The clear and register calls must name hunk0 $94BA and $9480.
Each immediate write is checked for its instruction and destination displacement.

The first snapshot supplies x (+12), y (+14), width (+4), height (+6), id (+16),
type (+20) and field (+22). The second and third change y/id only. The fourth
rewrites y, width, height and id. The fifth changes y, height and id, retaining
the fourth width. Preserve all inherited values in the decoded records. Require
ids 1..5 in registration order, positive extents contained in the 320x200
playfield, and the checked RTS at the end. The unused long at +8 is cleared.
This produces five immutable records per town, including Highwood's market.
No button geometry or original strings are embedded in Python source.

Pointer starts are the checked absolute word stores at hunk0 $13F4/$13FC for
Highwood and $12AE/$12B6 for Waterdeep. Their relocated destinations must name
adjacent words in hunk4; both towns must use the same cells. The pointer cel
filename is the NUL-terminated string ten bytes after the x cell (the cell's
surrounding cursor block). Picture names come from the checked LEAs at $2DC2
and $2DFE. Town names come from the relocated immediates at $2D96 and $2DD2,
whose store destinations must also match in the two loaders. Require bounded,
nonempty ASCII strings within hunk4.

Both towns compare the translated key with Space. Highwood then loads the
sheet number as a checked long immediate at $14C2 before the sheet call at
$14C8. Waterdeep calls that same sheet entry at $137A without loading D0;
its decoded Space sheet is the key operand at $1366. Check the intervening
fade/pointer calls, the relocated sheet entry and the instruction forms, so
this difference remains an explicit decoded property. Button action meanings
remain the control-flow rules of ?12.15, rather than strings inferred from the
painted pictures.

Frozen records in sim/town_types.py form the data boundary for T11.16b.
assets/town_data.py performs the checked reads without adding an alternate
screen loop. T11.16a does not replace host menus or implement shop/service
transactions. The parent task remains open until T11.16b removes the old
location model, wires these records, and reviews real-disk contact sheets.


### 12.28 Town presenter and host continuations (T11.16b1)

The town uses the same VBL joystick pointer as sheets (§12.2). Poll a live
button before the Space key; held fire over a registered rectangle dispatches
its id. Rectangle right and bottom edges are excluded. A child call suspends town polling.
On return, rebuild the town and reset the pointer to its decoded start (§12.15),
without repeating visit setup or spending the turn. The host retains the town
owner, town identity, pointer and pending request in replay hashes, including
while a shared sheet is active. The owning player port follows the campaign's
existing controller assignment.

The original cursor builder (mog LAB_0572, rt/combat_ui.cpp) loads frame zero
of the decoded pointer cel and builds hardware sprite zero. The two stored
planes give values 0..3: zero is transparent; visible pixels address palette
entries 17..19. Software composition must add 16 to nonzero pointer pixels,
leaving zero transparent. Do not recolour the town picture or substitute a
palette. The original town palettes already contain those sprite colours.

Opening a town runs turn setup, clears map jobs and dragon-active, stops map
colours and clears the host's buffered map key. Shared sheets 5, 6 and the
decoded Space number use the existing SheetScreen. Closing them returns to
the retained town. Only the exit button sets spent to budget and takes the
map setup exit. Escape and unrelated keys do not leave a town.

The presenter/caller cutover is T11.16b1. Service requests are explicitly
unavailable until T11.18, and transactions remain T11.17; their original
callers are retained without applying the deleted model's effects. The
loading-text prologue and final every-button-to-screen acceptance are
T11.16b2, after those children exist. No invented service screens replace them.
Stonehenge no longer calls the deleted all-moonstones ritual model; it remains
explicitly unavailable until its original offer/ritual caller lands in T11.19.


### 12.29 Smith/market data and startup inventory boundary (T11.17)

Read human smith prices from hunk0 checked compare/debit pairs: armour
$C2C4/$C2CE, $C2F8/$C302, $C32C/$C336; swords $C360/$C376,
$C394/$C3AA; daggers $C3C0/$C3D0. Each pair must agree and address
actor gold (+74). Armour stores at $C2D4/$C308/$C33C address +92;
the HP gains at $C2DC/$C310/$C344 address +80. Sword compare/store
pairs $C36A/$C37C and $C39E/$C3B0 address +88 and must agree.
The dagger cap is the checked byte compare at $C3C8. Dagger count comparisons
are signed bytes, sword comparisons signed longs, gold comparisons signed words;
updates wrap to the stored width. Armour selection uses the lowest set mask bit.
Do not apply the reference port's configurable zero-price refusal to original
operands: an affordable zero-price operand remains a purchase.

The market initializer is the checked relocated LEA at $CBF4, followed by
12 immediate word writes to displacements 0,2,...,22. Preserve all prices,
including key/moonstone slots. The market click LEA at $C3F6 must name that
same allocation. The stock address at $4DBC/$4DDC and $9AEE must agree.
The signed pointer split is the checked compare at $C3FC; the sell right shift
is the checked LSR at $C48C. Read the sell cap and clamp from $C492/$C49A
and require agreement. Buy sounds precede affordability; sell sounds follow
redraw. Neither purchase nor sale increments the shared take-change counter.
Stat purchases continue on sheets 5/6, because dispatch is by region type.

The smith panel uses §12.3 positions shifted by 150 for its right panel.
Button indices for mail/plate/battle are 7/8/9, swords 11/12, dagger 5.
The market right panel draws stock inventory plus the sharp-sword icon at
(193,95). The left worn-sword icon remains type 2, field +88; do not invent a sell
region for it. The slot-4 sale handler still preserves its documented fallback
when invoked, although the ordinary worn-sword region does not call it.
Counted stock is finite. The pointer x, rather than the region side, selects
buy/sell. Mask buys OR into the owner and XOR out of stock, sales reverse
those operations. Gold addition wraps before the signed sell-cap test.
For counted buys other than rings, omit the original invalid inventory-address
HP write (§12.11); the ensuing sheet redraw still recalculates the displayed
knight normally. Buying a sharp-sword count therefore equips it on redraw.

Campaign startup now completes the inventory setup of §11.3, once after
installing decoded defaults and before any gameplay draw. It uses the same
Rng and GiftState as turns and sheets. First four hoard item calls ($4D9C
through $4DAE), then a hoard gold call ($4DB6); six market item calls from
the checked MOVEQ/DBRA loop at $4DCA..$4DD8, then the extra count at
$4DE2 (slot displacement checked). Inventory-only gifts update last-gift
memory and reject duplicate sharp swords, without actor side effects.
A single rejection-sampled key draw ($4E48 mask, $4E4E limit) supplies
one shared index for all four six-lair groups, with masks from the stores at
$4E62/$4E72/$4E82/$4E92. Read the four lair loot bands via LEA $4F1A;
run their gold/item choices in lair order (kind 4 gives no loot).
Hoard item calls use the actor-target path, including ring HP and sharp-sword
side effects; stock and lair calls use the inventory-only path. All stock, loot and last-gift state
enter the existing world hash. Reset reconstructs them; opening a market never
rolls or replenishes stock. This closes the initialization follow-up recorded
by T11.12/13, and preserves the PRNG order before market and later gameplay.


### 11.21 Bottom-edge map grid reads (T11.17 verification correction)

The corrected startup random sequence reaches a legal computer position
(305,190), whose frame-0 bottom edge yields tile index 1038. The original
signed-index byte read has no 1000-cell bounds test. Keep the nominal 40x25
terrain and region arrays, and also load their immediately following bytes
from the same hunk. Bound this extension by the largest tile reachable at
(310,190) using the actual decoded frame-0 size (for the supported image,
one additional 40-cell row). Lookup first addresses the nominal grid, then
this retained suffix; negative indices or reads beyond the suffix still raise.
Do not clamp the knight, tile or table values and do not invent padding.
This corrects the earlier §11.18 model of a 1000-byte allocation without
changing movement, clipping, PRNG calls or map scheduling. Synthetic rules may
omit the suffix and continue to reject accesses outside their supplied data.

### 12.30 Service rule boundary (T11.18a)

The service rules operate on the existing Actor, Inventory and shared Rng. They
do not own screens or advance jobs. Donation state retains unsigned word purse
and donation cells. Moving one gold tests the source for exact zero, then wraps
both words. Leaving clears these cells without writing actor gold; accepting
writes the purse. Screen input and two-frame repeat waits belong to T11.18b.

For the supported mog CRC, the healer's minimum and HP debit are checked word
operands at hunk0 $9ED4 and $9EFA and must agree. The life debit is $9F16,
the byte cap comparison $9F08 (actor +73), and the small-donation reply threshold
$9F24. Both debits must be positive signed words so the bounded loop progresses.
Preserve the equality-only life cap, byte wrapping and signed donation test.
Return remaining donation and the original change bits (curse, HP, life).

The mystic's bonus table is reached by the checked LEA at $9D92, in hunk0.
The MOVEQ at $9D98 gives its row count minus one. Each row is two signed words,
upper donation bound and bonus; require strictly increasing bounds. Check the
four-byte advance at $9D9E and DBRA at $9DA0. The win comparison is $9DAA.
Constitution HP addition/subtraction at $9CA8/$9CE8 must agree; read the HP
floor from $9CF0 and the byte stat floor from $9CCC. Pick the stat before the
percent draw, even when none is pickable. Compare the wrapped signed sum with
the win threshold. On a win, add to the stat, add constitution HP, then
recalculate HP and endurance; on a loss at the floor, return unchanged, otherwise
subtract, clamp constitution HP by signed word comparison, then recalculate
endurance and HP. A no-stat loss returns unchanged and performs those latter
recalculations. Do not reproduce the stale A1 write into unmodeled memory.
Beyond the last donation band use zero bonus, as specified in §12.19.

The dice payout LEA is at $A7CA, into hunk42. The checked MOVEQ at $A7D0
contains 11: read that many six-byte rows (three face bytes, zero key pad,
multiplier byte, unused byte). The original DBRA compares one extra row beyond
the table; omit that read, which cannot change a valid dice result (§12.21).
Check the six-byte table advance at $A7DA and DBRA at $A7E0. Read the random
mask at $A730 and accepted face maximum at $A734. Reject unsorted, duplicate or
unreachable table keys, truncated tables and zero multipliers. Roll three dice
in order, retaining their unsorted display order; lookup uses a sorted copy.
Stake affordability uses signed words; debit and payout wrap actor gold to a
word, while the returned product retains the unsigned word multiplication.
Bound rejection loops and raise if supplied data cannot terminate.

These immutable decoded rule records and pure transactions are T11.18a.
T11.18b supplies original assets/text/scripts, screen phases, host snapshots,
town returns and real-disk contact-sheet acceptance. No service is exposed as
playable until those callers are implemented.

### 12.31 Donation screens: healer and mystic (T11.18b1)

This subsection is the exact form of §12.19 for the supported `mog` version. It covers the two
screens and their shared donation screen. Rules come from §12.30; this section owns assets,
text, phases, timing and the host caller. The dice house is T11.18b2.

**Opening** (mystic at hunk0 `$9B66`, healer at `$9DD0`). The routine selects font table entry 0
(`Small.font`; entry 4 is the bold menu font, the only one with narrow spacing, §11.19). It loads
the screen's picture (a checked `LEA` of its filename, then the picture loader) and the shared cel
file through the `BSR` to `$A23A` (a checked `LEA` of `mys.cel`). It fades out, places the pointer
with two `MOVE.W #imm` stores to the pointer cells (160, 170), switches the pointer on and
shows the picture. It then draws the greeting text list (a checked `LEA` followed by `JSR` to
the list printer at `$9196`) and starts four synth sequences directly. Each start is
`MOVE.W #seq,D0`, `MOVE.W #channel,D1` and a `JSR` to the synth start in hunk 44. These bypass
the front end of §10.32: no busy marking. The routine fades to the picture's palette and waits
for fire, meaning a press and then a release.

The pointer is the hardware sprite the town built (§12.28). The screens never rebuild it. It moves
2 pixels per VBL within x 0..314 and y 0..195 in every phase, as in the town, and is shown
throughout.

**Text.** Text lists are the 14-byte linked records of §11.19: text pointer, x, y, style, next
pointer. Style 1 centres within the loaded span. Labels and numbers are single strings drawn at
an immediate x, y and style (`MOVE.W` to D0, D1 and D2, or `EOR.L D2,D2` for style 0, then `JSR`
to `$917C`). A number is formatted into a 4-byte field: the value modulo 1000 as decimal digits,
left-aligned, without leading zeros, padded with spaces to three characters (`$93CA`).

**Donation screen** (`$9F4E`, `D0` = portrait frame: the mystic passes 0 and the healer 1 through
`MOVEQ`). The purse becomes the knight's gold and the donation starts at 0 (§12.30). Four regions
are registered from `MOVE.W #imm,disp(A0)` stores into the region scratch record: x at +12, y at
+14, width at +4, height at +6, and the button code at +20. A later region inherits every field
it does not store. The codes are 4 ("-"), 5 ("+"), 3 (accept) and 2 (leave). The redraw (`$A100`)
blits the picture, then draws:

- the portrait frame twice and the four button frames, each as `MOVE.W` frame (or the portrait
  cell), x and y, then `JSR` to the cel drawer in hunk 28. The frame is drawn at x minus the
  frame's hot-spot x, as on the sheets;
- the donation label (style 4: right-aligned to the span) and the purse label;
- the purse and the donation numbers at their immediate positions.

**Loop** (`$A046`). Each VBL: if a region is under the pointer while fire is held (the pointer is
locked otherwise, §12.2), its code acts. Leave (2) and accept (3) end the screen (§12.30). "-" (4)
and "+" (5) move one gold. When the move succeeds, the screen redraws and waits for the
`MOVEQ #2` frames before polling again. When it fails, polling continues on the next VBL with no
wait. So a held button moves one gold every three VBLs.

**Reply.** The screen blits the picture again and chooses a text list:

- *mystic*: a donation of 0 gives *nothing given*, even after accept. Leave clears the
  donation first, so the mystic answers a leave with *nothing given*, and its *refused* branch
  (a screen not accepted with a donation left) cannot be reached. Otherwise the gamble runs (§12.30, its draws at this moment). A win with no
  pickable stat gives the *nothing to raise* list. *Unchanged* gives its list. A raise or a lower
  searches the raise or lower table: the `MOVEQ #2` count gives three rows, each an 8-byte
  `{stat long, text-list pointer}`. The table pointer is stored by `MOVE.L #table,cell`. A stat
  missing from the table falls through to the unchanged list.
- *healer*: not accepted gives *refused*, and a donation of 0 gives *nothing given*. Otherwise the
  healing runs (§12.30). A donation not above the reply limit (signed) gives the *small donation*
  list. A larger one gives *healed* when the curse bit was set, and *plain* otherwise.

The refused, nothing-given and unchanged lists are shared: the healer branches into the mystic's
tail. **Close** (`$9D5C`): wait for the `MOVE.L #imm,D0` frames (50), wait for fire, fade out,
pointer off. The host then resumes the town (§12.28) without spending the turn.

**Host.** The service screen is a child of the town, like the shared sheets. The town keeps its
state, and the replay hash holds the screen's phase, pointer, wait counters, fire stage, donation
cells and the displayed list. The simulation advances only while no palette fade runs, but the
pointer keeps moving. Sound starts are reported as *start* requests (channel, sequence) for the
presentation; until T11.23b the host passes only the ids.

### 12.32 The dice house (T11.18b2)

This subsection is the exact form of §12.21 for the supported `mog` version (hunk0 `$A318` to
`$A8E3`). Rules come from §12.30.

**Entry.** With the knight's gold not above 0 (signed word), the routine returns at once and no
screen appears. Otherwise it selects font table entry 0 (`Small.font`) and loads the tavern
picture and the table picture, each through a checked `LEA` of a filename in hunk 42 and the
picture loader. It copies each picture's palette: the tavern palette becomes the round palette
and the table palette the roll palette. It loads the dice cel file through a checked `LEA`. The
knight becomes the player. Six regions are registered from the same scratch stores as §12.31:
x +12, y +14, width +4, height +6, a long kind at +16 and the code at +20, with unstored fields
inherited. Five bet regions have kind 1 and codes 1 to 5 (the stake). The leave region has kind
2. The routine fades out and clears the "done" word. The colour ramp routine (`$AADE`) writes the
player's class shades into the round palette from the byte offset given by `MOVE.L #imm,D0`. It
finds the row by `CMPI.L #kind,54(A1)` and writes five `MOVE.W #imm,(A0)+` words; a kind without
a row writes nothing. Three fixed-channel requests follow (§10.32).

**Round** (`$A50A`). The pointer x is set by a `MOVE.W #imm` store; y keeps its value from the
town. The pointer is shown. With the player's gold not above 0 (signed) the screen ends. Otherwise
fight jobs are cleared, the tavern is shown in the round palette, the throw state becomes 0, and
the thrower is spawned. The spawn is a pool record and a job of type `$44` at (160, 0, 100)
facing 1. Its script is the idle script, its frame list holds only the dice cels, and the frame
wait becomes 5 (all operands checked at `$A60C`).

**Loop.** Each pass runs the job pass, the engine tick and the draw pass (§10.17 and §10.18),
then prints the player's gold at an immediate position in style 2. The value is the signed word
widened to a long, formatted as in §12.31. When the throw state is 2, the roll follows.
Otherwise the routine waits for the frame-wait word's number of VBLs, and the screen ends once
"done" is set. As in fights, one pass takes the wait's VBLs.

**Thrower handler** (`$A63A`, type `$44`). It runs whenever the job pass calls it (§10.18). It
returns through the common exit (`$6EC8`): the script cell is the result, with 0 meaning end,
and the record's position and facing are returned:

- throw state not 0 (the throw script has ended): the state becomes 2 and the job ends;
- fire not held on the player's port: the state becomes 0, the job restarts on the idle script
  and the frame wait becomes 6;
- fire held: the routine hit-tests the pointer. With no region, the job goes idle as above. A
  kind-2 region sets "done" and goes idle. A kind-1 region takes its code as the bet. A bet
  greater than the gold (signed words) goes idle. Otherwise the bet comes off the gold (§12.30),
  the state becomes 1, the job restarts on the throw script and the frame wait becomes 3.

The scripts are fight-engine scripts in hunk 42, so engine routine operands there are named like
those of the fight data hunk. The throw script calls `$A8C2` (a sound-only routine, §10.32): one
draw masked by `ANDI.W #3`, then `MOVEQ #0,D0` overwrites the value, so the byte table's first
entry is always played on fixed channel 3. The draw still counts.

**Roll** (`$A722`). Three dice are rolled (§12.30). The table picture is shown with the pointer off,
and the dice are drawn as dice-cel frames (the face is the frame) at three immediate positions,
in roll order. They are then sorted and looked up. *Lost*: the bet and the gold go into the lost
text list. *Won*: the payout is added (§12.30), and the winnings and the gold go into the won
list. Each value is formatted into a number field inside a text-list string, then followed by
the suffix that `MOVE.B #imm,(A2)+` stores write at `$A828` (" gp."). A string therefore reads
up to its field, then the digits, then the suffix. The list is shown in the roll palette, and
the routine waits `MOVEQ #20` VBLs and for fire (press, then release). If the gold is now
negative (signed), "done" is set. A new round starts.

**Exit.** Pointer off, fade, and the player becomes the current knight again. The host returns to
the town without spending the turn, as in §12.31.

**Host.** The dice screen is a child of the town. The replay hash holds its phase, throw state,
frame wait, bet, "done", the pointer, the roll, and the thrower's job and pool records. The fight
engine instance uses the shared generator. Draws come from the engine tick (§10.17) over the
tavern picture.

### 12.33 Town welcome prologue and final caller acceptance (T11.16b2)

The town loaders call the fixed message routine, not the fourteen-entry rotating
fight-loading routine. Highwood at hunk0 $2D96 and Waterdeep at $2DD2 store a
relocated string pointer into the same hunk4 text-record cell. Each loads the
same linked welcome list through its following LEA and calls hunk0 $2FB4.
Decode both lists with the respective text-pointer substitution, retaining all
coordinates, styles and order; do not modify the executable or embed its text.
Require the substitution cell to be an actual record in the list. The list and
all text pointers must remain in hunk4. Decode the message picture filename
through LEA $2BFA and the font filename through LEA $2C36. The font slot store
at $2C32 and selection at $2FCA must agree on slot 4 (bold font). The shared
message path calls the picture-unpack helper $3044, draws the list at $2FD6,
and installs the picture palette at $2FE2. Check those targets and forms.
The town picture loader calls the common fade-out at $2E22 before copying the
loaded town picture. The welcome itself has no fire wait, delay or synth start.

The host represents the blocking visit as a retained town continuation. Stop
map colour jobs, run the existing turn/job/key setup once, and fade the retained
map to black. Then show the decoded welcome in the message picture's own palette
and fade it to black. Finally show and fade in the town. These are PAL palette
steps; disk decoding time does not add a simulation wait. A phase transition
occurs after that VBL's palette step, without requiring a render call or an
extra empty tick between fades. All phase and fade cells enter the existing hash.
No town button or translated key is polled during the prologue or a town fade.
The pointer moves on the owner's port during the town fade, as the original VBL
pointer does. The welcome has no pointer. Town input starts when the fade ends.

A return from a sheet, healer, mystic or dice house redraws the retained town
and resets its pointer; it never reruns the welcome or turn setup. The only
spent exit is town button 5. Shared sheets 5/6 and Space (9 or 32), both dice
callers, both healers and Waterdeep's mystic are checked through their real
screens and returns. The real-disk town contact sheet includes both welcomes,
each of the ten button outcomes, Space sheets and service returns. Loading
screens for fights and other places remain their own callers.

### 12.34 Stonehenge implementation boundary (T11.19)

Read the four bit/frame comparisons at hunk0 $177E/$178C/$179A/$17A8,
phase ending-bit rows at $1822/$182C/$1836 and class ending-bit rows at
$1844/$1850/$185C/$1868. Check instruction words and their branch forms.
Read the final flag at $189A, the blessing cap/gain at $17F8/$1800, and
quest wait at $1882. The offer and quest lists are reached through checked
LEAs $17B6/$1876. The program filename is the relocated LEA at $18A4;
the encoded hand-over is retained until T11.22 consumes it. It never returns
to the map or reuses the wizard picture as an ending.

The ritual entry at $A92A frees jobs and shows the recoloured list reached
through $A93E (hunk42). Read filenames at $A95E/$A996, and the sound bank
through the checked call at $A9A2 into hunk16 $1E8 and that entry's relocated
LEA into its filename hunk. Retain the bank as decoded sample data. Actual
sample address/sequence wiring remains T11.23c. Both spawn blocks at $A9B6
and $A9DA supply hunk42 scripts, position, facing and type; retain this order.
The handler installed at $A9AE must name $AAC0 and its cleared script and
stop-word stores must be checked. It returns END for the invoking job and
sets the ritual's done latch.

Class shade rows are the checked compare/push runs at $AB74 through the
RTS at $ABE8: three postincrement words followed by one final word, preserving
class order and values. The palette byte offset at $AA2E is divided by two;
the target period comes from $AA5E. Three fixed-channel sound requests are
read at $AA3A/$AA44/$AA4E. The main frame wait is $AA96 and must agree with
the frame-mode store at $AA6E. No original colours, sounds or scripts are
embedded in source. The script callback at $AAA8 takes one percent draw,
compares the threshold at $AAAE and emits the play id at $AAB4 only when
not greater. Identify its relocated script operands in the script image;
all other unknown callbacks raise.

Ritual jobs use the existing Fight/ScriptEngine and ordered FightCanvas.
There is one initial engine pass before palette setup, followed immediately
by the loop's first pass; thereafter pass intervals use the decoded wait.
Jobs continue while the four-VBL target fade runs. Stop as soon as the handler
sets done, without inventing an input skip. The original final silent fade
is an OUT fade: both $8B26 and $8B46 use the black target. Decode their period
at $8B2E/$8B4E and wait at $8B36/$8B5E, and check agreement with the message
fade period/wait at $8B7C/$8B86. These routines wait 36 PAL ticks even when
the palette reaches its target sooner. This task's blocking phases retain
that minimum wait as well as the palette clock; no render call advances them.

The campaign retains the Stonehenge owner, phase, fire-wait cell, wait counter,
ending request and ritual job state in hashes. An unmatched visit displays
the offer, waits for press/release, fades out, resets last-item and opens
sheet3. Closing sheet3 first fades its retained screen; an offered item runs
the ritual and then the original blessing, otherwise no reward is given.
Both paths open sheet9 and spend the turn only when it closes. A matching
visit instead shows the quest text, waits the decoded 20 ticks after its
message fade, waits for press/release and emits the encoded program request.
The terminal hand-over retains that message until the ending consumer lands.

Offer-region detail: only existing inventory regions whose field is below
$14 are converted to type12. Keys, moonstones and worn equipment remain
unchanged. The normal worn sharp-sword icon addresses actor field $58, so it
does not expose slot4 to the offer UI. Preserve the slot4 drop handler without
inventing a selectable region for it, just as for the market's sale fallback.

### 12.35 Wizard gift result and dialogue boundary (T11.20a)

The visit's reward and the computer knight's daily reward call the same routine
at hunk0 $9884. They must share one implementation and the campaign's GiftState.
Return an immutable result as well as updating the actor/inventory: kind 1 item,
2 gold, 3 stat, 4 curse; item slot or amount; stat offset and the selected stat-row
index; and the updated item/gold message-cycle index. There is no additional draw
for dialogue. Preserve the row, rather than searching for its stat offset after
the roll: several rows raise the same stat but point at different speeches.
The existing rejection loops, wrapped recency addition and derived-stat effects
of §12.12 are unchanged. The item cycle increments before indexing and masks
with 3 ($9930/$9938); the gold cycle increments before indexing and wraps at 3
($98F6/$98FE/$9908). The curse branch leaves both cycles and last-item memory
alone. Stat selection's rejected candidates consume their draws before the final
row is returned. A saturated stat roll restarts the whole reward roll.

**Counted speeches.** These are not linked TextLine records. The printer at
$A262 reads an unsigned byte count, followed by that many NUL-terminated ASCII
strings. It adds the immediate word at $A2A2 to y after each string (10 pixels
on the supported file). Decode within hunk4, rejecting an empty count, pointers
outside the data, unterminated strings and non-ASCII text. Empty lines are valid.

Read the greeting through the checked LEA at $9602. The gift's item-pointer
table is reached through LEA $9948 and has four relocated entries (mask operand
$9938 plus one); the gold-pointer table through LEA $9918 has three (compare
operand $98FE). Check the increment and reset instructions/cells too. The nine
stat rows reached through LEA $99E4 are `{stat long, speech pointer}`. The
long compare operand at $99F6 gives the last row index (8); each stat must be actor offset
70, 71 or 72. The curse speech comes from the immediate relocated pointer in
the `MOVE.L #pointer,cell` at $98E2; check that its destination agrees with
the indexed message stores at $991E/$994E/$9968. No text or pointer table is
embedded in source.

**Farewells.** The initial relocated `MOVEA.L #pointer,A0` at $97D0 supplies
the omen default. The four compare/branch/pointer runs at $97D6, $97E6,
$97F6 and $9806 override it by kind. Preserve their order and decoded keys;
unknown kinds keep the omen. The item and gold farewells contain writable
fields. Find the item field through LEA $9B52, the gold field through both
LEAs $9A4C/$9A58 (which must agree). Retain the line index and character
offset of each field in a frozen speech record. Rendering substitutes into a
copy, truncating the original line at the field because the original write
includes a terminator. Never mutate executable bytes. Each field must be inside
exactly one counted line of the corresponding farewell. Item names and numeric
formatting remain the animated caller's responsibility in T11.20b.

The animated caller remains T11.20b. Its fire loops sample held fire after each
5/6/4-VBL frame wait, as checked at $9680/$9764/$985C; they are not
press-then-release waits. The first stage has an engine pass before the palette
target and another at loop entry. The gift-to-farewell change has no fade-out:
it replaces the picture and directly installs the class palette. The final
recency store is the byte operand at $9876 (70). These details refine §12.20;
T11.20a adds no host route or alternative screen loop.

### 12.36 Animated wizard caller (T11.20b)

The wizard loader ($2E42) selects scene 3 and calls the rotating loading message
at $2F5A. Retain a session loading-text cursor, starting at 0. Decode its fourteen
linked lists through LEA $2F74 and the compare operand at $2F94; the cursor is
incremented and reset after selecting the list. Its four direct synth starts are
the checked immediate pairs at $2F20/$2F2E/$2F3C/$2F4A. Message/font filenames
come from $2BFA/$2C36, and the wizard's Small.font from $2C70; the selected
font slot is 0 at $9598 and $9890. Stop map colour jobs, run turn setup, free
map jobs and clear keys once on entry. Retain map fade-out, loading-message
fade-in and fade-out as blocking phases, using the common decoded two-VBL
palette period and 36-VBL minimum wait (§12.34). Loading takes no additional
simulation time. The cursor and phase enter replay hashes. This gives the wizard
its original loading path; other callers of the rotating message can share the
cursor when their loading presentations are completed.

Read tower picture/cel/gift picture filenames through relocated LEAs
$2E5C/$2F04/$2E8C, checking the picture/cel loader calls and palette copies.
All five creature frame slots receive the same wizard cel allocation; preserve
this aliasing, rather than remapping a script slot to zero. The bank load at
$2F10 names hunk16 $250. Its LEA at +0 names Wz.a in hunk17; decode the bank
without embedding any samples. Exact instrument relocation remains T11.23c.

Four spawn blocks at $95CE/$96EE/$9716/$97A8 contain a relocated immediate
script pointer (hunk4), the shared cel-list LEA, three position words, MOVEQ
facing and type, and a spawn call. D4's long immediate is unused by spawn (the
second gift block passes 2); check its form without turning it into a slot.
The fourth block must equal the first. Type $28 uses the existing idle-object
handler; jobs keep their script/work state and end naturally. The three stages
use fresh pools: one tower job, then two ordered gift jobs, then the tower job.
Their frames use the existing ordered FightCanvas and its stamps. Unknown
callbacks fail; there is no catch-and-restart animation fallback.

The five-shade class ramp is the shared routine at $AADE (§12.32). The byte
palette offset is the long at $95C2 (12), divided by two. Retain the checked
class order and five RGB12 words; do not embed colours. The tower and farewell
use that patched tower palette; the gift uses its own unpatched picture palette.
The greeting/farewell coordinates and style come from $9608/$9816 (x long,
y/style MOVEQ); gift coordinates/style are three words at $96D6. Speeches are
background text, composed before animated jobs, so later draws may overlap them.
The gold farewell uses a three-character decimal field (§12.31), followed by
the decoded byte-store suffix at $9A6A through its NUL. Item names are read
from the ten `{slot word, string pointer}` rows reached through LEA $9B3C.
Substitution is done on a copy as §12.35 specifies.

At tower entry run one engine pass, emit the four START requests read at $9630
and its three following 12-byte blocks, set the palette target (period from
$9666), then immediately run the first loop pass. Run jobs during this target
fade. A loop pass is followed by its entire 5/6/4-VBL wait, decoded from
$9680/$9764/$985C, then sample held fire on the visit owner's logical port.
An early press/release is not buffered. If fire is absent, run the next pass
on that same tick. On arrival fire, pause jobs for the common blocking fade;
only after it ends roll the shared reward once and start the two gift scripts.
The gift's first loop pass is immediate, with a black-to-picture target fade
(period $974A). Gift fire directly replaces the picture/pool and installs the
class palette; farewell's first pass is immediate, without a blocking fade.
Farewell fire stores the decoded recency byte, then runs the silent OUT fade.

The host retains the visit while sheet9 is open. Closing that sheet fades its
retained display, then sets spent to budget and uses the existing map re-entry.
No input skips a blocking fade; render calls cannot advance jobs, waits or RNG.
Hash the owner, class, phase, waits, gift result, job/pool records, engine work,
lists and counters. Scene reset clears the visit; a new game resets the loading
cursor. The wizard pictures are removed from the legacy victory display; actual
ending playback remains the program hand-over of T11.22.

### 12.37 Home-village caller (T11.21a)

The common place dispatcher sends kind bytes 21 through 24 to the same village
routine; the active knight owns the visit and the temple sheet, independently
of the village's colour. There is no separate village picture or dialogue.
This extends the caller summary in §12.13 from the supported mog instructions,
checked against the Amiga reference's village and common place-return paths.

At entry, retain the map palette, stop its colour jobs, run turn setup, clear
map jobs and input, and clear dragon-active through the existing map reset.
Keep displaying the map during the blocking OUT fade (the shared §12.34 silent
fade: period 2, minimum 36 PAL ticks). Input cannot skip this wait. Then apply
the village reward once and open shared sheet 9. At code $18B6, CMP.I.B compares
actor byte +73 with the runtime immediate; the $18BC BGE is **signed**. If the
actor's signed lives byte is less than that cap, ADD.I.B at $18BE adds the
runtime gain, wrapping to eight bits. Thus the supported cap of three is not
an equality-only cap: four lives stay four, while $FF becomes zero. Do not heal
HP, recalculate stats, change inventory or draw RNG as part of this reward.

The MOVEQ at $18C4 supplies sheet 9 to the shared-sheet call at $18C6. Decode
and validate these operands and call identities from the user's mog, along
with the cap/gain field displacements and signed branch. The common return at
$18CC copies budget into spent and $18D6 calls map setup in hunk 36, offset 0.
On temple completion, retain its display through the same blocking OUT fade,
then remember its last item, clear the sheet/visit, set spent equal to budget,
and re-enter the map. Sheet actions use the existing inventory and actor records.

Retain owner, phase and remaining wait in session hashes, including while the
temple is open; rendering never advances them. Reset clears the visit. Preserve
the fade palette even after its last interpolation step until the blocking wait
expires. Missing original disks leave the map unchanged with an availability
message, as for other original place callers. Valley setup/rewards and meetings
remain T11.21b/c; they must not inherit this village reward or spent-exit policy.

### 12.38 Valley caller, Guardian background and rewards (T11.21b)

This completes §12.17 from the supported mog caller at hunk 0 $16C0..$1768,
the Guardian loader $4908 and hunk 36 $065E..$0686. Read operands and text from
the user's executable after checking instruction forms, relocations and helper
identities. The keys comparison at $16CA tests byte +20 for exact equality to
the runtime mask, not containment. The missing-key text is the relocated list
at $16D2; the victory list at $1748 uses the shared recoloured message palette
of §12.34. Both use message.piv, the shared message loader's bold.f, and the
existing map character map. The writer's font check at $933A detects this bold
font and ORs style bit 3 into each record ($9350): subtract three pixels from
each glyph's advance for both alignment and drawing. Apply this to ordinary
and victory messages too, not only the rotating loading text; otherwise the
longer centred lines clip at both edges. Decode/check that style instruction.

Retain the active owner through all waits, combat and optional temple. Entry
uses the same map OUT fade/reset as §12.37. Missing keys show the normal message,
wait through its blocking IN fade, then wait for a fresh fire press and release;
OUT and re-enter the map without spending any remaining budget. No RNG is drawn
by this branch (apart from the usual later map dragon spawn, when applicable).

Complete keys select the Guardian scene (MOVEQ at $4908), show the shared rotating
loading message ($4910), then load the original arena and run the existing fixed
Guardian fight (§10.29), with the campaign's actor/inventory/RNG and spawn lane.
Loading shares the campaign cursor, voices and blocking IN/OUT waits of §12.36.
The loader at $3214 copies the Test picture whose runtime pointer is slot 8
(displacement 32 at $3226). In the pack's format mapping (§3.1), this is picture
index 5, without a .t compositor overlay. It is not map picture index 8. Region
does not replace the Guardian palette (§10.13). The default obstacle reset is
a relocated call to **hunk 12**, $03DA, not hunk 0: five MOVE.W immediates supply
one segment count and its x1, x2, y, unused fields. Its y is also written as the
floor edge at hunk14 +78. Validate that equality. Decode these values and pass
them through the existing arena/fight boundary; do not invent a regional .t.

On fight completion clear bad luck and settle the normal knight defeat first
(restore HP, lose one life, §10.24). Preserve the last combat frame during OUT.
On loss run turn setup and pick_stat using the existing gift table/RNG **before**
subtracting the extra life amount at $171A. If the pick is nonzero, compare its
byte with the floor at $1720 and subtract the amount at $1728 only when unequal;
keep §12.17's omission of the unrelated-record write when the pick is zero.
Bytes wrap, including a zero stat decrement to $FF. Open shared sheet9, retain
its OUT fade, then spend the budget and re-enter the map. Sheet redraw performs
the existing derived-stat recalculation. The total supported life loss is three.
The spent tail copies the pre-reentry budget; if endurance dropped, the next
map setup can reduce budget below spent. Preserve that order rather than forcing
spent to equal the newly computed budget.

On win add the decoded word at $1738 to progress with 16-bit wrap, and clear keys
using the byte store at $1742, before showing the recoloured victory message.
After its blocking IN fade, wait for press and release, then exactly the MOVEQ
count at $175A (10 PAL ticks). Run the shared OUT fade before replacing the
message, clear map input and run turn setup, then make one campaign RNG draw.
The AND.I.L at hunk36 $0670 supplies the moonstone-bit mask; BSET at $0680
addresses inventory byte +22. OR that bit with existing moonstones and re-enter
the map, including when the draw is zero, without spending the turn. No reward
roll occurs during rendering or while fire is still held.

Serialize owner, keys-match decision, phase, wait, press latch and combat result;
the active encounter already serializes every Guardian/job/RNG cell. Reset clears
the caller. Keep palette effects installed through black blocking-wait tails.
The dedicated background extends the existing fight loader; it does not create
another combat engine. Exact sound-bank instrument wiring remains T11.23.

### 12.39 The ending pass of `program` (T11.22)

Stonehenge with the matching moonstones stores the ending code (§12.16) in the boot flags word
and starts `program` again (§12.34). `program` copies that word into its own flags cell
(hunk0 $0190, from $3E0 at $001E) and tests one bit at $0064..$0072: `MOVE.W flags,D0 ;
ANDI.W #bit ; CMP.W #bit ; BEQ.W $0120`. The bit is read from those operands ($80). When it is
set, the ending below runs instead of the intro. It never returns to the map and always ends by
loading `mog` again, which starts at the title with a fresh campaign. `assets/ending_data.py`
decodes everything in this section into `sim/ending_types.EndingPlan`; the player is T11.22b.

**12.39.1 Top level ($0120..$0172).** Each line is checked by instruction word and call target.

1. Caption screen (§12.39.7) with the loading text list (hunk2 $0004, `LEA` at $0120, call
   $11CA).
2. The ending loader, hunk8 $0500 (§12.39.3).
3. `gap = 2` (long store at $0132).
4. The four scene bodies at hunk0 $0938, $0ABA, $0BD8 and $0D2E, called in that order.
5. Caption screen with the closing list (hunk0 $0176, one centred line); wait 25 ticks ($0160);
   the standard fade out (hunk10 $0C50).
6. `BRA.W` back to $0114: `LEA` of the next program's name (`Mog`) and `JMP` to the loader.

**12.39.2 Buffers and palette tables.** The scenes name ten picture buffers by long cells hunk2
$0212 + 4n (n = 0 is the *shown* buffer the scene frames draw from, 1..9 are stored pictures
P1..P9) and two screens: hunk30 $0000 (the displayed screen at scene start) and $0004 (the
work screen). *Show Pn* copies 40000 bytes (one 5-plane 320×200 picture) from Pn into the shown
buffer (hunk10 $0D6A); *Select Pn* only replaces the shown pointer. *Prepare* (hunk10 $0CC4)
copies the shown buffer to both screens. Eight 32-colour palette tables live at hunk9
$0006 + $40·k (k = 0..7); the stage palette and *PaletteTo* name them by k.

**12.39.3 Loader (hunk8 $0500).** One block per picture: draw target = buffer, load the picture
file into it, copy its palette into table k, then the optional tweaks. Decoded order:

| File | Buffer | Table | Tweaks |
|---|---|---|---|
| `bg5.piv` | P5 | 3 | class |
| `bg5a.piv` | P6 | 4 | class |
| `bg3.piv` | P7 | 5 | class |
| `bg2a.piv` | P9 | 7 | moon, then class |
| (clear P2) | | | |
| `bg7.piv` | P2 | 0 | |
| `bg8.piv` | P3 | 1 | |

Cels are placed one behind another in the cel area and their base addresses are written to a
six-slot frame table (hunk11 $00DE); draw opcodes select a slot as `(op & $1F) >> 2`. Load order
and slots: `dg1` 0, `li1` 1, `ha1` 3, `co1` 2, `da1` 4, then `klift1` 5, which is loaded into
buffer P4 (hunk2 $0222) instead of the cel area. `ov1.cel` is loaded into buffer P8 (hunk2
$0232), whose address becomes the overlay cel pointer (hunk3 $0092). Then `co.stile` (1000
bytes, the block map of the reverse wipe, same format as `intro.stile` in §7.4) and `vmusic.cmp`
(61344 packed bytes, RNC-unpacked in place). The decoder follows these placements symbolically
and refuses any other instruction.

*Moon tweak* (hunk8 $0938, before the class tweak of table 7): on the flags word, the first set
bit among the blocks `BTST #1`, `#0`, `#2` (in that order) writes three colours into table 7
(entries 12, 15 and 23, from `MOVE.W #c,d(A0)` with entry = d/2) and three *ramp colours* into
the hunk9 words $0000/$0002/$0004 (zero by default). Each block is `BTST ; BEQ.S next ; stores ;
BRA.W end`; the decoder checks that every skip lands on the next block.

*Class tweak* (hunk8 $09DE): `ADDA.L #16,A0` (first entry 8), then blocks for bits 4, 5, 3 and
6 (in that order), each writing four colours to entries 8..11. Bits 3..6 are the knight kinds of
the ending code (§12.16) and bits 0..2 the moon frames.

**12.39.4 Scene primitives.** Scene bodies are runs of these forms only:

| Form | Step |
|---|---|
| `JSR` hunk10 $0264 / $0BF8 / $0C50 / $0C92 / $0CC4 | jobs reset / blackout (all black now) / fade out (black table, period 2, then 36 ticks) / flip / prepare |
| `JSR` hunk1 $0000 | start the music (once) |
| `JSR` hunk0 $01BA / $0292 | frame loop / stage palette now |
| `JSR` hunk31 $068A / $071C | reverse wipe, set-up half / running half |
| `JSR` hunk0 $0862 | sub-scene, inlined (five left then five right spawns) |
| `MOVE.W #v,cell` / `MOVE.L #v,cell` | store to a scene cell (below) |
| `MOVE.L #table,hunk3 $0084` | stage palette = table k |
| `MOVE.L Pn,shown` | select |
| `MOVEA.L Pn,A0 ; MOVEA.L shown,A1 ; JSR $0D6A` | show |
| `MOVEA.L #script,A0 ; JSR $02E4 / $030A` | spawn left / right; the script is a hunk2 offset |
| `MOVEA.L buf,A0 ; JSR hunk29 $05E0` | clear a buffer |
| `MOVE.L slot,D0 ; JSR hunk31 $005A` | release colour ramp 0..2 (hunk0 $092C + 4i) |
| `MOVE.L buf,D0 ; JSR hunk10 $0D9A` | draw target |
| `MOVE.L #n,D0` or `MOVEQ #n,D0 ; JSR hunk29 $0658` | wait n ticks |
| `LEA table,A0 ; JSR hunk10 $0C70` | PaletteTo k: fade at the shared period (hunk10 $0C8C, 2), then wait period·16 ticks |
| `LEA list,A0 ; JSR hunk12 $0132` | print a text list on the draw target (`bold.f`, §7.4) |
| `LEA black,A0 ; MOVEQ #p,D0 ; JSR hunk31 $001A` | fade to black with period p, no wait |
| pairs set-up + `BSR $0508` | pairs (§12.39.5) |
| `MOVEQ #n,D0` + inline loop + `DBF` | n+1 bare ticks (§12.39.5) |

Scene cells: credits overlay on (hunk2 $0246), overlay phase (hunk0 $0EC2; 2 skips the last two
overlay cels), stage mode (hunk3 $0088), staged (hunk3 $008A), stop (hunk3 $008E), speed (long,
hunk2 $023E), gap (long, hunk3 $009A), left/right depth (hunk2 $2DC6/$2DC8).

**12.39.5 Frame loop, spawns, pairs and bare ticks.**

*Frame loop* (hunk0 $01BA), repeated until it returns: stamp the tick counter; run the job
handlers of finished scripts; if `stop` is set, return; run every job's script one frame (§12.39.9). `stop` is set by
handler 0: the handler pass calls it for every active job whose script has ended, and the entry
code installs it at $0052 as hunk0 $02CC (`MOVE.W #1,stop ; A0 = 0 ; RTS`, so the job is also
freed). A loop therefore ends one frame after the first of its jobs ends. If `credits` is set,
draw the overlay cels (§12.39.10); flip; restore the dirty rectangles; if `staged` is clear, set it and apply
the stage palette by `stage mode` (2: fade to the stage table at period 2; 3: fade to black;
5: blackout; otherwise write it at once); wait until `speed + gap` ticks have passed since the
stamp (never a negative wait).

*Spawns* read their start record from the helpers: left x 160, y 0, z 100 + left depth, flags 1;
right x 120, y 0, z 100 + right depth, flags 3 (mirrored). Both use the shared frame table and
handler 0 (operands at hunk0 $02E4 and $030A).

*Pairs* (hunk0 $0508): set-up stores index 0, the table pointer, the count (5) and a spare word
(cleared, not read by this routine). For each entry it toggles the side word (hunk0 $0600, never
reset, initially 0), spawns the entry on the left when the toggle becomes 1 and on the right when
it becomes 0, then runs the counted frame loop (hunk0 $0206: the frame loop without the stop
test, until its counter at $0604 reaches the operand at $0248, 16). The ending's table
(hunk0 $0D06) repeats one script.

*Bare ticks*: n+1 passes of stamp, handlers, scripts, flip, restore and the pacing wait; there is
no stop test, overlay or stage palette.

**12.39.6 Script callbacks the ending uses.** A scan of every script reachable from the
ending's spawns finds only selector-0 `$B4` calls, to five routines. Each is decoded:

| Routine | Effect |
|---|---|
| hunk0 $08DC | create three colour ramps (hunk31 $0060: entry, the ramp colour of §12.39.3, rate 5, repeat 0) and store their slots at $092C/$0930/$0934; the first scene releases them |
| hunk0 $0EC4 | flash: write the 32 colours of the table it names (hunk0 $0F4E) to the colour registers, wait 8 ticks, write the live palette back |
| hunk0 $0EE6 | flash series: six times wait (20, 2, 20, 5, 10, 5 ticks) then a flash of 2 ticks with the table at $0F8E (the `BSR.S` target); then wait 50 |
| hunk31 $0788 | `SUBI.W #1` on the reverse wipe's step word (hunk31 $0850): the wipe slows down |
| hunk31 $077E | set the wipe's stop word (hunk33 $0F5A): its running half ends |

The waits run inside the job pass, so the whole frame stalls while the palette keeps ticking.
A table call (selector non-zero) or any other routine raises.

**12.39.7 Caption screen (hunk0 $11CA).** Blackout; clear and target the displayed screen; copy
the raw `message.piv` image to the work screen and decode it; print the list; overwrite palette
entries 1..5 with the decoded words at $122E (a dark red ramp; the first entry comes from
`ADDA.L #2`); PaletteTo that palette (period 2, wait 32 ticks).

**12.39.8 Decoded sequence.** Loading caption; loader; gap 2.
- Scene 1: credits on, phase 2, jobs reset, fade out, flip, show P9 (`bg2a`), prepare, stage
  table 7 by fade, speed 6, one left script, music, frames. Then release the three ramps, fade
  out, credits off, gap 4, show P7 (`bg3`), stage table 5, speed 8, the ten-spawn sub-scene plus
  one left script, frames, speed 6.
- Scene 2: gap 0, show P5 (`bg5`), stage table 3 set at once, two left scripts, speed 6, depths
  5/15, five pairs, 41 bare ticks, depths 0, speed 8, jobs reset, two left scripts, frames.
- Scene 3: P6, P5 and P6 in turn (stage tables 4, 3, 4) with one, two and one scripts and a
  frame loop each; then clear the shown buffer and the work screen.
- Scene 4: blackout, jobs reset, select P1, wipe set-up, stage table 0 at once, wait 15, gap 0,
  speed 6, one left script, wipe run, frames, fade out, jobs reset, select P3 (`bg8`), prepare,
  PaletteTo 1, wait 100, target the work screen, print the five-line closing text, flip, wait
  500, fade to black at period 6, wait 90.

Then the closing caption, wait 25, fade, and `mog`.

**12.39.9 Job pool and script interpreter.** The pool has 40 slots (`MOVEQ #39,D7` of the
allocator, hunk10 $0198). Spawning takes the first inactive slot and clears its loop state; jobs
reset clears the pool. Before every script pass the pool is bubble-sorted by depth (neighbours
swap while the next one has the smaller z), so draw order follows depth. Each active job's
script runs until an `$FF` marker. Draws: `op & $1F` is a byte offset into the frame table (only
multiples of 4), then frame, signed dy, flags d3, dx. Not mirrored: x = job x + dx; mirrored (job
flags bit 1): x = job x − dx − frame width; y = dy + y + z, all 16-bit. d3 bit 4 also stamps the
cel into the shown picture buffer; bit 5 only selects the CPU masked copy (no visible
difference). Control opcodes used by the ending: `$84` (mode 3 jumps now, otherwise at the end of
the frame), `$88` (loop; count 0 takes the tick counter's low 5 bits, at least 1), `$94` with
`$FE` (second loop) and `$B4`. Also implemented because their effect is fully known: `$80`,
`$8C`, `$A0` (move: bit 6 absolute; otherwise x adds when bit 0 is set, inverted for flags 3;
y subtracts with bit 3; z with bit 5), `$A4`, `$B8`/`$BC`/`$C8` (skips), `$C0` (free the job; the
pass goes on), `$D4` (clear loop state). `$C4`, `$CC`, `$D0` and `$FD` read memory the ending
never sets up and raise. End of frame: a running first loop jumps back while its count lasts;
then a deferred jump; then `$FF $FE` repeats the second loop, `$FF $00` continues, and `$FF $FF`
ends the script (unless the second loop still runs).

**12.39.10 Player model (T11.22b).** `sim/ending.EndingRun` executes the plan. One
`advance()` is one VBL: the palette ticks, then the tick counter advances. Every wait, pacing gap
and callback wait yields once per VBL. Instant steps take no time, and that includes the loader:
the original's disk time is not emulated, so the loading caption shows only for its own fade
(32 ticks) and the fade-out that follows.

*Palette* (`sim/ending_palette`): a live palette, the 32 colour registers, one fade (target,
period, counter starting at the period; a step moves each component by one toward the target
when the counter reaches 0; with no change the fade ends) and six ramp slots (colour, target,
period, counter, the colour to return to, repeat; on arrival target and return colour swap, and
a non-zero repeat frees the slot when it runs out). A tick writes the live palette to the
registers only when something moved, so a flash lasts until the next fade or ramp step or until
it is written back. Blackout zeroes both; stage-now writes the table to the registers and the
live palette. Pictures load their palette words into one shared cell (hunk26 $0146, initially
zero), with the rule checked in both of program's picture decoders (hunk20 $015C and $01C6): a
4-plane picture fills 16 words and any other 32; a word with bit 15 set keeps its low 15 bits,
otherwise it is shifted left once. Words a 16-colour picture does not fill keep the previous
picture's. Tables are copies of that cell after each load, with the tweaks applied.

*Screens* (`app/ending_screen.EndingPixels`): ten 320×200 picture buffers and two screens.
Buffer 10 is the displayed screen and 11 the work screen; flip swaps them and makes the work
screen the draw target. The dirty-rectangle restore after a frame-loop flip is modelled as
copying the shown buffer to the new work screen. That gives the same image, because only job
draws differ from the shown buffer on a work screen inside a loop, and the overlay is redrawn on
top every frame. The overlay draws the `ov1` frames at the positions decoded from hunk0 $0E1C:
four draws whose y is a byte sum and x a word sum of two immediates, with the last two skipped
while the overlay phase equals the `CMPI` operand (2). Text uses the program font (`bold.f`,
loaded by $1176 together with `message.piv`) and the character map at hunk2 $2DE0; each glyph
advances by width − 2, and style 1 centres the line on 320 (operands at hunk12 $0184, $01B0,
$021A).

*Reverse wipe* (`LOADING_A` / `LOADING_B`, hunk31 $068A / $071C). Set-up: flip, clear the
displayed screen, take the shown buffer as the wipe picture, store speed 2 and gap 0, progress
1000, step 9 (operands at $06C4..$0700), draw the wipe picture for progress 1000, copy it to the
work screen and flip. Running half, while the stop word is clear: stamp; progress −= step
(clamped at 0); redraw the wipe picture; copy it to the work screen; the handler pass; the job
pass; flip; pacing; leave once progress is below the floor (`CMPI #200` at $074E). Afterwards the
displayed screen is copied to the work screen and to the shown buffer. The wipe picture for a
progress is the 200-line window at that offset into the block panorama that the 960-byte
`co.stile` builds over the pictures in P2..P4 (§7.4 format). Only `bg7` (P2) is referenced; a map
naming P4, which holds `klift1.cel`, raises. The original scrolls incrementally (shift by the step,
then redraw the two top rows); the window is the image that scrolling produces.

*Stage palette*: the mode chain at $0262..$028E is checked (2 fade at the period from $02B2,
4 write now, 5 blackout, 3 fade to black at the period from hunk10 $0C22; any other value writes
now).

*Hand-over.* A matching Stonehenge visit releases fire on the quest text, emits the
`EndingRequest`, and the session switches to the ending scene. The ending runs with
`request.flags` as the boot flags; input is ignored (the intro's skip is not armed in the ending
pass). When the run ends the campaign is reset and the session goes to the title, as `mog`
restarts from its entry. The music step starts `vmusic.cmp` through the existing module player
(its exact effect subset is T11.23c). The ending scene's snapshot (step, tick, cells, jobs,
palette, picture cell, wipe and ramp state) is part of the session hash; rendering never
advances it. The invented VICTORY screen and the overworld `winner` field are removed: nothing
in the original reaches them.

**Version gate.** `program` hunk 0 must have CRC-32 $3064C4E2. Every operand above is still read
and checked, so a different build fails loudly instead of playing a wrong ending.

### 12.40 Meeting and dragon-hoard callers (T11.21c)

Complete §12.18 using supported mog hunk0 $0AB4..$0CA2, the dragon caller
$11CC..$1250, and the existing sheet rules (§12.2/7). Read/check helper targets,
field displacements, sheet numbers, computer kind and progress immediate; no
dialogue or reward tables are embedded. The protection text list is relocated
by $0C3C; $0C28 identifies its writable name string. Find that string's row in
the linked list and substitute the defender's name in a copied TextLine. Use
the normal shared message picture/font and condensed bold-font spacing from
§12.38, with the recolour at $301E..$3032. Do not mutate decoded text.

Keep original turn owner separate from the sheet owner and duel pair. Never
change the map turn index just to display a different knight. A map arrival
of kind 1 or 33 opens this caller, including computer-initiated arrivals.
Retain map OUT, message IN/OUT and sheet OUT fades with the shared period/minimum
wait. Clear map input/jobs and dragon-active at entry; human meetings do not
spend remaining movement. A computer arrival already exhausted its budget in
§11.20 and keeps its pending continuation so the decision is not repeated.

The defender's frog byte nonzero or lives byte **exactly zero** skips the fight
and settles for the first knight. Otherwise a human defender (kind != runtime
computer kind at $0C0A) with inventory byte +18 nonzero sees the named protection
message. Wait for press/release, then open sheet9 for that defender, clearing
last-item memory to $FFFF. The defender's input controls the message and sheet;
the attacker cannot spend its items. Closing sheet9, or a scroll's automatic
close, returns control to the caller after its OUT fade. Avoid only when last
item equals the decoded slot at $0C82 and global bad_luck is clear. A failed
protection use marks the defender as the fight's confused knight and enables
the existing direction inversion (§10.19). Closing without using it continues
normally. Two computer knights return without fighting.

A duel uses the existing persistent FightRuntime, authoritative records, shared
RNG, regional backdrop and original human/computer ports. Settle both defeats
once (HP restored/life lost), clear bad luck and retain the last frame through
OUT. Mask 3 opens sheet9 for a human first knight, otherwise returns. With only
the first defeated, use the second as winner and first as loser. A computer
winner gains the decoded word at $0BF2 and uses the existing ordered loot
transfer. A human winner opens sheet1 with winner on the left and loser on the
right, even when the winner was the defender. Sheet1 allows one take from a
loser with signed lives > 0, then becomes sheet9. The signed BGT at $AD62 clears
changes for any nonpositive signed lives byte, allowing that loser to be emptied.
This refines §12.2's "no lives" wording; the earlier no-fight decision still tests
exact zero. Its close returns to the original turn. There is no automatic human
progress reward in this caller. Direct fight/practice entry still has its own
existing continuation; it must not create an unrelated meeting caller.
Campaign kind bytes determine computer fighters; direct fights created before
map rules are installed retain their roster-based computer setup.

Acquisition and Wyrm scrolls keep using the existing in-place sheet8/11 loop:
next-knight selection, one acquisition then return to the remembered sheet,
and Wyrm target committed on sheet close. These also work inside meeting,
protection and hoard sheets; retain the caller until the shared loop ends.
Both scrolls re-enter the shared sheet initializer ($ABEC, changes cleared at
$AC02). Clear changes on entering sheet8/11, including when the parent is a
meeting that just changed to sheet9; otherwise acquisition immediately bounces
back without allowing a take. Decode/check this reset with the meeting rules.

On a natural dragon win mark its lives $FF and grant the existing decoded
dragon progress award once, then show sheet10 (MOVEQ at $1246). Use the existing
dragon inventory/gold as its source. Preserve the combat frame through OUT;
on hoard close fade OUT, then copy budget to spent and re-enter the map. Defer
that spent assignment until the hoard is closed. Loss still transfers loot,
spends and returns immediately, including automatic computer/frog/no-life
losses. Do not make a second reward roll or replace hoard stock on opening.

Hash the retained caller's original pair, phase, waits, input latch, defeat mask,
confused handle and hoard mode alongside existing sheet/fight state. Reset clears
it. Keep palette effects during blocking black tails, but allow combat's own
warning/death palette to update while the fight is running. Exact synth wiring
remains T11.23. The bounded explicit pair replaces dependence on stale original
swap-register state on a no-fight return; no unrelated record is swapped.


### 12.41 Campaign acceptance replay (T11.24)

Verification drives `GameSession.step_tick` exclusively with serializable `InputFrame`s.
Start at the title with seed `$2468ACE1` and four human ports; enter knight selection,
pick all four portraits in their displayed order, walk the first knight to lair 19
(zero-based), and use the existing ordinary fight audit controller through all natural
waves and settlement. No actor, inventory, position, pool, outcome or pointer is patched.
Take gold and an item through the loot regions, close the sheet, and continue the round.
On the next first-knight turn walk to Highwood, select its market and purchase a healing
potion at the decoded price through the original pointer regions. Close both callers.
End turns through the first actual day/moon change (four round screens, §11.12), then
acknowledge that screen with release/press/release and return to the map.

Every wait and navigation loop is bounded and fails on an unexpected scene. Record all
inputs with the existing replay format. Replaying the serialized recording in a fresh
session must match checkpoint hashes, including the final state; rendering each checkpoint
must leave its hash unchanged. The command writes inputs, hashes and a contact sheet only
under ignored `build/`. Source contains route choices and inputs, never decoded game tables
or disk-derived fixtures. This is one campaign integration route; it does not claim an
exhaustive full-game or original-hardware equivalence test. Other places, all creature
handlers, the ending and audio retain their separate acceptance tests.


Campaign verification also closes the older-caller spacing follow-up in §12.38:
town welcome and Stonehenge message lists use the same bold writer as Valley and
meetings. Their decoded immutable TextLine copies now OR the checked `$9350`
operand into their style, preserving the raw executable data. The town welcome
pixel oracle independently centres and advances glyphs by width minus three.
Menu palette rendering is read-only; the return-to-menu fade is initialized on
the simulation tick. Selection, roster and input-edge latches participate in the
session hash, so a rendered and an unrendered replay have identical checkpoints.

### 12.42 The intro pass of `program` and the `mog` title & knight-select screens (T11.25)

When `program` tests the ending flag bit at hunk0 `$0064..$0072` (§12.39) and the bit is clear
(initial boot flags `$3E0` are `0`), execution falls through to `$0076` and runs the intro pass
before jumping to `$0114` to load `Mog`. Like the ending pass (§12.39), the intro pass is
straight-line 68k code using the same buffers (`SHOWN`, `P1..P9`, `SCREEN`, `WORK`), eight palette
tables (`hunk9 $0006 + $40·k`), shared picture-palette cell (`hunk26 $0146`), 40-slot job pool,
script interpreter, `ProgramPalette`, credit overlay (`ov1.cel`), caption routine (`hunk0 $11CA`),
and `$B4` script callbacks. `assets/intro_data.py` decodes both the `program` intro pass and the
`mog` title-menu and knight-select screen layouts from the user's executables at runtime; no scene
timeline, script offset, palette word or hunk offset is embedded in Python source (ADR-001,
ADR-044).

**12.42.1 Intro top level (hunk0 `$0076..$0114`).** Checked instruction by instruction:

1. Clear intro music state (`MOVE.L #0,hunk1 $0088` at `$0076`), `gap = 0` (`MOVE.L #0,hunk3 $009A`
   at `$0080`), `speed = 2` (`MOVE.L #2,hunk2 $023E` at `$008A`).
2. `JSR hunk10 $0C50` (`Call.FADE_OUT`).
3. `JSR hunk8 $0136` (the intro loader, §12.42.2).
4. Abort test (`TST.W hunk33 $0F60 ; BNE.W $0114` at `$00A0..$00A8`): if the user skipped during
   loading, jump straight to the `Mog` loader.
5. `JSR hunk31 $05AA` (the forward progress wipe, §12.42.4).
6. `gap = 4` (`MOVE.L #4,hunk3 $009A` at `$00B0`).
7. Seven scene bodies and one mid-sequence picture setup called in order at `$00BA..$00E4`:
   - `JSR hunk0 $03F0` (Scene 1: five torch-bearing druids marching on `bg1b.piv`),
   - `JSR hunk0 $0438` (Scene 2: five druids approaching Stonehenge on `bg2.piv` with `ov1.cel`),
   - `JSR hunk8 $008E` (picture setup, §12.42.5),
   - `JSR hunk0 $0366` (Scene 3: Stonehenge circle with sub-scene `$0818`, four `Pairs` and altar),
   - `JSR hunk0 $0610` (Scene 4: High Druid on `bg4.piv`, then cloud vortex on `bg5a.piv`),
   - `JSR hunk0 $06BE` (Scene 5: Knight drawing broadsword on `bg2a.piv` with `ov1.cel` phase 2),
   - `JSR hunk0 $0792` (Scene 6: Knight entering Stonehenge circle on `bg3.piv` with sub-scene `$0862`),
   - `JSR hunk0 $0738` (Scene 7: High Druid dubbing the kneeling knight on `bg5a.piv`).
8. `JSR hunk10 $0C50` (`Call.FADE_OUT`), `Caption` with the relocated text list at `$00F2`
   (`hunk2 $0078`), `Wait` for the long immediate at `$00FE` (`$01A4` = 420 VBLs),
   `JSR hunk10 $0C50` (`Call.FADE_OUT`), `JSR hunk1 $0034` (unhook VBL music), and fall through to
   `$0114` (`Mog`).

**12.42.2 Intro loader (hunk8 `$0136..$04F4`).** Unlike the ending loader (which loads silently
under one caption), the intro loader interleaves file loads with four progress routines in hunk 31
(`$0328`, `$03B0`, `$042E`, `$04AE`, §12.42.3) that display the Mindscape logo, the moon backdrop,
the Moonstone title banner and the six credit pages:

1. `JSR hunk10 $0BF8` (`BLACKOUT`), `Target(SCREEN)` (`$013C`), load `"mindscape"` (`$0148`) into
   `SCREEN` using `P3` as scratch (`$014E`), then `LEA hunk26 $0146,A0 ; JSR hunk10 $0C70`
   (`PaletteTo` the picture-palette cell: fades in the Mindscape logo at `palette_period = 2` and
   waits 32 ticks).
2. Load `"bg1a.piv"` into `P2` and copy its palette to table 0 (`$0166..$018A`), read
   `"intro.stile"` (1000 bytes into `hunk33 $0000`, `$0190..$01AE`), then call progress step 0
   (`JSR hunk31 $0328` at `$01B4`).
3. Load `"bg1c.piv"` into `P3` (`$01BA..$01D2`), call progress step 1 (`JSR hunk31 $03B0` at
   `$01D8`), followed by the abort branch (`TST.W hunk33 $0F60 ; BNE.W $04F4`).
4. Load `"bg1b.piv"` into `P4` (`$01E8..$0200`), call progress step 2 (`JSR hunk31 $042E` at
   `$0206`), followed by the abort branch.
5. Five story pictures (`$0216..$0366`): each block sets the draw target (`P5..P9`), loads the
   picture (`bg4.piv`, `bg5a.piv`, `bg3.piv`, `bg2.piv`, `bg2a.piv`), copies its palette to table
   `3..7`, optionally calls the class tweak (`JSR hunk8 $09DE` for tables `4..7`), calls progress
   step 3 (`JSR hunk31 $04AE`), and tests the abort word.
6. Five animation cels (`$0368..$04A6`): placed into the shared frame table (`hunk11 $00DE`) in load
   order `au1.cel` (slot 0), `li1.cel` (slot 1), `da1.cel` (slot 4, offset `$10`), `ha1.cel`
   (slot 2, offset `$08`), and `dw1.cel` (slot 3, offset `$0C`, loaded at `hunk0 $10B8`). Each of
   the first four cel loads is followed by `JSR hunk31 $04AE` (progress step 3) and the abort test.
7. `ov1.cel` (`$04A8..$04C0`) is loaded after `P9` (`hunk2 $0236 + 40000`) and stored in the
   overlay cel pointer (`hunk3 $0092`); `"music.cmp"` (`$04C6..$04F4`, `$159D9` packed bytes) is
   read into `hunk3 $009E` and RNC-unpacked (`BRA.W $07C2`).

**12.42.3 Progress screens (hunk31 `$0328..$05A8`).** All four progress steps share the tail at
`$0550` (`$0556` without the leading `FLIP`): test `hunk16 $0000` (set `hunk33 $0F60 = 1` on a key),
set `palette_period = 2` (`MOVE.L #2,hunk10 $0C8C` at `$0566`), overwrite five entries of palette
table 0 (`hunk9 $0006`, displacements `10, 18, 20, 22, 24` -> entries `5, 9, 10, 11, 12`) with the
gold ramp words decoded at `$0576..$0592` (`$0000, $0FED, $0DC9, $0B95, $0842`), start a fade to
table 0 at period 2 (`MOVEQ #2,D0 ; JSR hunk31 $001A` at `$059A..$059C`), and restore dirty
rectangles (`JSR hunk10 $0A18`). Because the original overlapped that period-2 fade with the next
floppy file read and our model does not emulate disk reads, the tail waits `period << 4` (32 VBLs,
the same shift `hunk10 $0C82` uses for `PaletteTo`) when the screen shows a new stage (step 0 moon,
step 1 title banner, or a credit page in step 3), so every fade-to-gold completes before the next
step flashes to white.

- *Step 0 (`$0328`):* reset keys, `Call.FADE_OUT` (fades out the Mindscape logo over 36 VBLs),
  `FLIP`, clear `SCREEN`, `WORK` and `SHOWN`, point the wipe sources at `P2, P3, P4` (`bg1a, bg1c,
  bg1b`) and base at `SHOWN`, set `progress = 0`, draw the top 8 block rows (`wipe(SHOWN, 0)`),
  `PREPARE`, then run the tail with `flip = True` (32 VBLs: the moon fades in).
- *Step 1 (`$03B0`):* overwrite the five entries of table 0 with `$0000` (`$03B6..$03D2`), target
  `WORK`, draw cel frame `$49` of the text font (`hunk3 $0010 + 16`) at `(x=9, y=60)` decoded from
  `$0400..$040C`, `FLIP`, wait 8 VBLs (`MOVEQ #8,D0 ; JSR hunk29 $0658` at `$0422`), then run the
  tail with `flip = False` (32 VBLs: the Moonstone title banner fades in from black to gold).
- *Step 2 (`$042E`):* overwrite the five entries of table 0 with `$0FFF` (`$0434..$0450`), set
  `palette_period = 1` (`$0452`), `PaletteTo(table 0)` (`16` VBLs: flashes the title banner to
  white), wait 2 VBLs (`$0468`), overwrite the five entries with `$0000`, `FLIP`, `PREPARE`
  (restores the clean moon to `SCREEN` and `WORK`), and fall straight through into step 3 (`$04AE`).
- *Step 3 (`$04AE`):* overwrite the five entries of table 0 with `$0FFF`, set `palette_period = 1`,
  `PaletteTo(table 0)` (`16` VBLs: fades the current credit page to white), target `WORK`, compare
  the page counter (`hunk31 $079C`, initially 0) against the `CMPI.W` operand at `$04F4` (`6`). While
  `page < 6`, print the credit text list at `hunk31 $079E + 4·page`, increment `page`, and overwrite
  the five entries of table 0 with `$0000`. Then run the tail with `flip = True` (and restore the
  new `WORK` screen from `SHOWN` so the next credit page draws on a clean moon). For the three extra
  calls after `page` reaches 6 (during the remaining cel loads), no new text is drawn and the clean
  moon already on `SCREEN` stays at its target palette.

**12.42.4 Forward progress wipe (hunk31 `$05AA..$0688`).** Overwrites entries `9, 10, 11, 12` of
palette table 0 with the four words decoded at `$05B6..$05CC` (`$0A00, $0600, $0300, $0FC6`) and
writes table 0 immediately to the colour registers (`JSR hunk29 $0788`). Then loops while
`s16(progress) < $03E8` (1000, checked at `$062E`):

1. Scan the 11 signed threshold words at `hunk31 $065E` (`LEA` at `$05DA`) for the first index
   `0..10` with `s16(threshold[i]) >= s16(progress)`, and set `wipe_step` (`hunk31 $0850`) to the
   matching word in the 11-entry step table at `hunk31 $0674` (`LEA` at `$05E0`).
2. Stamp the tick counter (`JSR hunk0 $0330`), advance `progress = min(1000, progress + wipe_step)`
   (`JSR hunk31 $07B6` with bit 2 set), redraw the 200-line window of the `intro.stile` panorama
   into `SCREEN`, and `FLIP`.
3. If `wipe_step` equals the `CMPI.W` operand at `$0618` (`4`), call `JSR hunk1 $0000`
   (`Call.MUSIC`, starting `music.cmp`).
4. Wait until `speed + gap` ticks have elapsed since the stamp (`JSR hunk0 $033C`).

When `progress` reaches 1000, copy `SCREEN` to `WORK` and `SHOWN` (`$0638..$0656`).

**12.42.5 Mid-intro picture setup (hunk8 `$008E..$00FA`) and `Series` (hunk0 `$04C8..$0506`).**

- *Picture setup (`$008E`):* three 24-byte blocks (`$008E`, `$00B2`, `$00D6`), each copying a picture
  buffer (`JSR hunk10 $0D6A`) and its 32-word palette table (`JSR hunk8 $092E`):
  `(P5, table 3) -> (P2, table 0)`, `(P6, table 4) -> (P3, table 1)`, and
  `(P7, table 5) -> (P4, table 2)`.
- *`Series` (`$04C8`):* set up by writing `_PAIR_INDEX = 0` (`$0602`), `_PAIR_TABLE` (`$060A`),
  `_PAIR_COUNT` (`$0608`) and `_PAIR_SPARE = first_frame` (`$060E`), then `BSR.W $04C8`. For each of
  the `count` script pointers in the table, copy `first_frame` into the frame counter (`$0604`),
  spawn the script on the left (`BSR.W $02E4`), and run the counted frame loop (`JSR $0206`) from
  `first_frame` up to `pair_frames` (`16`), i.e. `16 - first_frame` passes.
- *Scene primitives added for the intro scene bodies:* in addition to §12.39.4, a scene body may
  call sub-scene `$0818` (`BSR.W $0818`, inlined like `$0862`), spawn a left job via `LEA script,A0 ;
  BSR.W $02E4` or `MOVEA.L #script,A0 ; BSR.W $02E4`, or invoke `Series` via `BSR.W $04C8`. Notice
  that Scene 1 (`$03F0`) sets `_PAIR_SPARE = 4` before setting the rest of the `Series` cells; the
  decoder accepts the four cell stores in any order before `BSR.W $04C8` / `$0508`.

**12.42.6 `mog` title menu and knight-select screen layouts (hunk0 `$18DE..$1FBC`).** Decoded from
`mog` with checked instruction words and relocations:

- *Title menu (`$18FA..$1B42`):*
  - Backdrop picture `"ch.piv"` (loaded at boot into `hunk1 $0124`, palette at `hunk31 $0146`),
    font `"bold.f"`, and sprite sheet `"sel.cel"`.
  - Cursor X is the word immediate at `$18FA` (`MOVE.W #$32,hunk1 $0172` -> `50`); the four cursor Y
    positions are the 4 `BE16` words at the relocated `LEA` `$1A9C` (`hunk4 $027C`: `85, 110, 148,
    168`).
  - Title banner draw (`$1AC4..$1AD4`): frame `$49` of `bold.f` at `(x=5, y=10)`; cursor arrow draw
    (`$1ADC..$1AF0`): frame `0` of `sel.cel` at `(cursor_x, cursor_y[cursor])`.
  - Main menu text list at the relocated `LEA` `$1B28` (`hunk4 $0008`), with the player-count number
    buffer at `LEA` `$1B00` (`hunk4 $00B8`, formatted by `JSR $93CA`) and the gore `"Off"` / `"On"`
    string pointers at `$1B0C` (`hunk4 $00C7`) and `$1B1E` (`hunk4 $00C4`) replacing the text pointer
    of the record at `hunk4 $004E`. As in §12.38/41, `mog`'s bold writer ORs style bit `8` into each
    record (`width - 3` advance, centred on `span = 319` when `style & 1`).
- *Knight-select screen (`$1CDC..$1FBC`):*
  - 32-word `$0RGB` palette at the relocated `LEA` `$1D2C` (`hunk4 $0476`).
  - Heading text list at the relocated `LEA` `$1E4E` (`hunk4 $0304`, `"Select a Knight"`).
  - Four portrait X positions (`4` `BE16` words) at the relocated `LEA` `$1E68` (`hunk4 $04BE`:
    `12, 88, 164, 240`), portrait Y at `$1E7C` (`$50` = 80), portrait frames `2 + index` (`ADDQ.W #2,D0`
    at `$1E7A`), and highlight frame `1` at `(portrait_x[sel], 80)` (`$1EAA..$1EB0`).
  - Selected knight's name position `(x=50, y=50)` decoded from `$1EC6..$1ECA`, and the four
    22-byte space-padded knight name buffers by portrait index `0..3` decoded from the four
    `CMP.W #n,D0 ; BNE ; MOVE.L #name,hunk4 $005C` blocks at `$1EFE, $1F2E, $1F5E, $1F8E`
    (`hunk4 $0076, $0060, $008C, $00A2`: portrait 0 Blue is `"SIR_GODBER"`, portrait 1 Yellow is
    `"SIR_RICHARD"`, portrait 2 Green is `"SIR_JEFFREY"`, portrait 3 Red is `"SIR_EDWARD"`). This
    corrects the earlier swapped `(96, 118, 140, 162)` tuple in `app/intro_player.py`.

## 13. Host Presentation (not original; Phase 13, ADR-053)

Nothing in this section is original behaviour. Presentation starts from the RGB image produced
by the palette lookup of the 320×256 indexed canvas, and ends at the window. It never writes to
the canvas, the palette, any `sim/` state or the recorded input, so replay hashes are identical
under every setting.

### 13.1 Source image

- The canvas is 320×256 (a PAL low-resolution frame). The game's 320×200 playfield sits at
  rows 28..227 (`PLAYFIELD_Y_OFFSET`); the rows above and below are border (normally colour 0).
- **Crop** selects the source rectangle: `full` (320×256) or `playfield` (320×200 at y = 28).
- **Pixel aspect** selects the shape of one source pixel: `square` (1:1), or `pal`, where the
  full 320×256 frame fills a 4:3 display, so one pixel is (4/3) / (320/256) = 16/15 as wide as it
  is tall. The pixel shape does not depend on the crop.

### 13.2 Destination rectangle

For a window of W×H output pixels, a source of w×h pixels and a pixel aspect a (horizontal
scale per unit of vertical scale: 1 or 16/15):

- **fit:** vertical scale `s = min(H / h, W / (w·a))`, horizontal scale `s·a`.
- **integer:** vertical scale `k = max(1, floor(min(H / h, W / (w·a))))`. With square pixels
  the horizontal scale is also k. With `pal` pixels the destination width is `round(k·a·w)`,
  drawn with the sharp-bilinear rule below on that axis only, because `k·16/15` is not a whole
  number for k < 15.
- **sharp-bilinear:** the fit size, sampled as if the source were first enlarged by the whole
  part of the scale with nearest-neighbour, then filtered bilinearly to the final size. Each
  source pixel then covers the same width to within one blended output pixel at its edge, and
  no source column is skipped or doubled.
- The destination size is rounded to whole output pixels and centred. The remainder of the
  window is black (or the surround of §13.5).

### 13.3 Scanlines

Scanlines are computed in output space. For an output pixel row y inside the destination,
the source-row phase is `fract((y + 0.5 − dest_y) / s_y)`, where `s_y` is the vertical
scale. Rows in the lower half of a source row (phase ≥ 0.5) are darkened by a fixed factor
(0.82). With an integer vertical scale every source row produces the same pattern, so there is
no banding. The current shader computes the phase from texture coordinates instead, which
bands at non-integer scales (fixed by T13.2).

### 13.4 Filter chain

Fixed order: indexed canvas → palette lookup → RGB canvas → crop → preset passes → scaling
to the destination of §13.2 → window. A preset is an ordered list of passes; each pass
(`PostPassSpec` in `render/postfx.py`) names its integer output scale `(scale_x, scale_y)`
relative to its input, its input-texture sampling mode (`nearest` or `linear`), its GLSL 330
core shader pair, and optional custom uniforms. Pass 0 crops the 320×256 RGB canvas to
`SourceRect` (`u_in_origin`, `u_in_size`); subsequent passes read the full intermediate texture
`(0, 0, prev_w, prev_h)` produced by the preceding pass. `faithful` has no passes (even
pixels, no effects). `scanlines` adds §13.3 in the final viewport presentation step. `crt`
(§13.4.1, T13.6) runs a two-pass separable source-resolution bloom in the post-process chain
followed by a native-resolution CRT presentation pass. Later preset: `smooth` (T13.7).
Switching presets or pass lists releases the intermediate framebuffers, textures, vertex arrays
and shader programs of the previous chain.
Passes run on RGB values, so palette effects (fades, colour cycling, recoloured messages,
pulses) need no change. In the game window and inspector, `F9` (`Shift+F9` to cycle backwards)
cycles the active `FilterPreset` at runtime; `WindowController` consumes the press and release
before `InputRouter`, so preset switches never affect simulation state or replay recordings.

#### 13.4.1 CRT preset (`render/shaders/crt.*`, T13.6)

The `crt` preset models four physical properties of a PAL RGB CRT monitor in linear light
($\gamma = 2.2$), implemented clean-room from published descriptions of CRT optics:

1. **Light phosphor bloom (intermediate chain passes):** two separable 9-tap Gaussian passes
   (`crt_bloom_h.frag` and `crt_bloom_v.frag`, $\sigma \approx 2.0$ source pixels) run in
   `PostProcessChain` at 1× cropped source resolution (`320×256` or `320×200`). Pass 0 converts
   cropped canvas pixels to linear light and extracts highlights through a smooth luminance knee
   (`[0.12, 0.65]`). Intermediate values are square-root encoded in RGBA8 to prevent 8-bit
   banding in dark tails. When `crt_bloom == 0.0`, no bloom passes are executed.
2. **Optional barrel curvature and rounded bezel mask:** in the final destination-resolution pass
   (`crt.vert` / `crt.frag`), centred viewport coordinates $c \in [-1, 1]^2$ undergo an
   axis-coupled barrel warp $c' = c + c \cdot (c_y^2, c_x^2) \cdot k$ where $k$ is
   `crt_curvature` (`0.0..0.3`). Edge midpoints remain flush with the letterboxed viewport while
   corners curve inward; an aspect-corrected rounded-rectangle signed distance field smoothly
   feathers the glass bezel over ~1.5 output pixels. Setting `crt_curvature = 0.0` disables both
   warp and corner masking.
3. **Intensity-dependent scanline beam profile:** horizontal sampling uses analytic
   sharp-bilinear reconstruction to keep 1-pixel art crisp under curvature. Vertically, the two
   nearest source rows are combined in linear light using a Gaussian spot weight whose sharpness
   narrows for dark pixels and widens for bright pixels (`crt_scanlines` in `0.0..1.0`; `0.0`
   reduces to flat linear interpolation). Evaluating the beam from continuous source-row offsets
   eliminates moiré banding at arbitrary output resolutions and under barrel warp.
4. **Phosphor mask:** applied in output pixel coordinates (`gl_FragCoord.xy`) with gentle
   average-luminance compensation:
   - `aperture`: 3-pixel vertical RGB phosphor stripes repeating every 3 output columns.
   - `shadow`: 3-column × 2-row staggered RGB phosphor triads with subtle horizontal slot bridges.
   - `none`: no phosphor mask.

### 13.5 Surround (optional, T13.8)

When the window is wider than the destination, the side bars may show a strongly blurred and
darkened copy of the current frame instead of black. The game image itself is never cropped or
stretched for this.

### 13.6 Refresh pacing (T13.4)

The simulation advances one tick per 20 ms of host time (§7.1); presentation never changes
that. A displayed frame shows the latest completed tick. On a display refreshing at a
multiple of 50 Hz (n × 50 Hz), each tick should be shown for exactly n refreshes. In exclusive
fullscreen, a 50 Hz mode may be requested when the display lists one. With vsync off or a
variable-refresh display, present once per tick. On 60 Hz, ticks are shown for one or two
refreshes (unavoidable without blending). Blending or interpolating between ticks is not
allowed (ADR-053).

### 13.7 Settings

The `[display]` table of `config.toml` holds the settings. A CLI flag beats the file, which
beats the default. An unknown key or a wrong type or value is a `ConfigError`, so a typo never
silently falls back to a default. Each key is added by the task that implements it:

| Key | Type / values | Default | CLI | Task |
|---|---|---|---|---|
| `scale` | integer 1..8: initial window size, in canvas pixels | 3 | `--scale` | T13.1 |
| `filter` | `faithful`, `scanlines`, `crt` | `faithful` | `--filter` | T13.1, T13.6 |
| `scale_mode` | `integer`, `sharp-bilinear`, `fit` | `integer` | `--scale-mode` | T13.2 |
| `aspect` | `square`, `pal` | `square` | `--aspect` | T13.2 |
| `crop` | `full`, `playfield` | `full` | `--crop` | T13.2 |
| `fullscreen` | `off`, `desktop`, `exclusive` | `off` | `--fullscreen` | T13.3 |
| `vsync` | boolean | true | `--vsync` / `--no-vsync` | T13.4 |
| `crt_mask` | `aperture`, `shadow`, `none` | `aperture` | — | T13.6 |
| `crt_mask_strength` | float 0.0..1.0 | 0.25 | — | T13.6 |
| `crt_scanlines` | float 0.0..1.0 | 0.65 | — | T13.6 |
| `crt_curvature` | float 0.0..0.3 | 0.08 | — | T13.6 |
| `crt_bloom` | float 0.0..1.0 | 0.22 | — | T13.6 |
