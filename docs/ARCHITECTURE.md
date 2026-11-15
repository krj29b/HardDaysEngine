# Architecture & Binary Format Specification

This document is the clean-room specification for **pymoonstone** (ADR-001). Every loader,
decompressor, simulation rule, and renderer component in `src/pymoonstone/` is implemented from
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
     - Used by `arenaObstacles` (`LAB_0A71`): blocks vertical movement across `y` when the actor's
       $X$ lies in `[x1, x2]`, and blocks horizontal movement into `[x1, x2]` when the actor's depth
       is within `|depth - y| <= 6`.
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
  - Disk B: `he.a` (shared ambient/UI bank), `kn.a` (knight combat bank), `Ra.a` (Ratmen),
    `re.a` (Trolls), `to.a` (Trogg Spear), `tr.a` (Trogg Axe), `wn.a` (Tavern / Wind).
  - Disk C: `ba.a` (Balok), `be.a` (Beast), `dr.a` (Dragon), `Gu.a` (Guardian), `mu.a` (Mudmen),
    `wz.a` (Demon / Wizard).
- **Container:** 12 of the 13 `.a` banks are wrapped in a 32-byte single-section Amiga Hunk header
  (`HUNK_HEADER` + 1 `HUNK_CODE` section + 4-byte `HUNK_END` footer); `wn.a` is a direct IFF
  `FORM....8SVX` container.
- **Decompressed Payload:** Raw concatenated **8-bit signed PCM** (`int8`, `-128..+127`) waveforms
  (often containing embedded IFF `FORM....8SVX` headers) for the Amiga Paula DACs.
- **Instrument Addressing (`mog` Hunk `S_45` at `+168`, `131` records × `14` bytes):**
  - Each 14-byte `SynthInst` record has:
    - `+0`: `BE16` `loop_flag` (`int16`: `< 0` means one-shot, `>= 0` means looping).
    - `+2`: `BE16` `loop_offset` (byte offset from sample start for loop point).
    - `+4`: `BE16` `length_words` (sample length in 16-bit words; byte length = `length_words * 2`).
    - `+6`: `BE32` `sample_offset` (for instruments `0..8`, relocated offset in `S_45` `0..167`;
      for instruments `9..130`, byte offset inside one of the 5 runtime bank slots `LAB_05C7..05CB`).
    - `+10`: `BE32` `pitch_offset` (relocated offset of the 60-entry `BE16` Paula period table).
  - **Paula Sampling Frequency:** Given a Paula period word `P` (typically `124..856`), the PAL
    sample rate is $f_s = \lfloor 3,546,895 / P \rfloor\text{ Hz}$ (for standard C-2 period `214`,
    $f_s \approx 16,574\text{ Hz}$; for period `428`, $f_s \approx 8,287\text{ Hz}$).

### 6.2 4-Voice VBL Synth Sequencer (`mog` Hunk `S_44`)
- Triggered by `sfxPlay(sound_id)` (`sound_id` in `1..168`), which looks up `BE32` offset
  `S_44[(sound_id - 1) * 4]`.
- A sound effect header begins with `uint8 voice_count_minus_1` (`0..3`), followed by
  `voice_count_minus_1 + 1` 5-byte voice descriptors: `[channel_idx:u8, stream_offset:BE32]`.
- Evaluated at 50 Hz (inside `vblTail` every VBL):
  - Bytes `0x00..0x7F`: Note trigger (`note` followed by `duration` ticks; `0x00` = rest).
  - Bytes `0x80..0x91`: Control opcodes (`0x80` end/loop, `0x81` select instrument, `0x82` vibrato,
    `0x83` portamento, `0x84` volume slide, `0x85` set volume, `0x86` arpeggio, `0x88` transpose,
    `0x8B` release envelope, etc.).

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
on up to **10 concurrent job slots**:

- **Draw Instruction (`opcode 0x00..0x1F`, 6 bytes):**
  - `+0`: `uint8` `cel_slot` (`0..31`, indexing `pCelTables[cel_slot]`).
  - `+1`: `uint8` `frame_index` (`0..frame_count-1`).
  - `+2`: `int8` `dy` (signed vertical offset added to job screen $Y$).
  - `+3`: `uint8` `draw_flags`:
    - Bit 0 (`0x01`): Register cel in job's **Hurt List** (`ulHurtList`).
    - Bit 1 (`0x02`): Register cel in job's **Attack List** (`ulAttackList`).
    - Bit 2 (`0x04`): XOR horizontal flip with the job's facing flag (`(job.ubFlags >> 1) & 1`).
    - Bit 5 (`0x20`): Stamp directly into the static background buffer (`SCREEN_B`) instead of the
      per-frame restore list (used for blood splats and fallen corpses).
    - Bit 6 (`0x40`): Exclude this cel from expanding the actor's union body bounding box.
    - Bit 7 (`0x80`): Foreground-masked draw (masks against saved background plane 3 `fgPlaneSave`).
  - `+4`: `BE16` `dx` (`int16` signed horizontal offset; negated when the job faces left).
- **End-of-Frame Marker (`0xFF`, 1 byte):**
  - Ends the current script frame; `ulScript` advances past `0xFF` and waits for the next tick.
- **Control Opcodes (`0x80..0xD4` in `mog` `combat_script.hpp`):**
  - `0x80`: End script (`ubRunning = 0`, triggers AI/handler dispatch `creatureDispatch` on next tick).
  - `0x81 <BE32 target>`: Unconditional jump (`ulScript = target`).
  - `0x82`: Free job and owner record (`ubActive = 0`, `ubRunning = 0`, `owner.ulActive = 0`).
  - `0x83..0x89`: Loop counters, relative branches, random branches, and subroutine calls using the
    36-byte `CombatWork` scratch block.
  - `0x8A <u16 dx> <u16 dz>`: Move job/owner position by `(±dx, dz)` with arena & obstacle clipping.
  - `0x8E <u16 sound_id>`: Trigger synth sound effect `sfxPlay(sound_id)`.
  - `0x90..0xD4`: Combat-specific hooks (spawn projectile/dagger, blood spray, screen shake, flash,
    grab/throw victim alignment, dragon flight/breath, knock-out timer).

### 7.3 Core Simulation Struct Layouts (`mog`)
- **`Knight` / Creature Actor Record (`132` bytes):**
  - `+0`: `u32 ulActive` (`0` = free, `1` = active).
  - `+4`: `u16 uwX`, `+6`: `u16 uwHeight`, `+8`: `u16 uwY` (depth $Z$).
  - `+10`: `u8 ubFacing` (`1` = right, `3` = left; bit 1 is the left-facing bit).
  - `+11`: `u8 ubInputPort` (`1` = Joy0, `0` = Joy1).
  - `+14`: `u32 ulHitTarget` (record address hit by this actor this frame).
  - `+18`: `u32 ulAttacker` (record address that hit this actor this frame).
  - `+22..+54`: Script table pointers (`ulIdleScript`, `ulScript26`, `ulWalkScripts`,
    `ulActionScripts`, `ulHurtScripts`, `ulDamageTable`, `ulDefenseTable`).
  - `+58`: `u16 uwBoxMinX`, `+60`: `u16 uwBoxMaxX`, `+112`: `u16 uwBoxMinY`, `+114`: `u16 uwBoxMaxY`
    (per-frame union screen bounding box).
  - `+62`: `u16 uwInput` (joystick bitmask: bit 0 right, bit 1 left, bit 2 down, bit 3 up, bit 4 fire).
  - `+76`: `u8 ubDaggers`, `+80`: `i16 swHp`, `+82`: `i16 swMaxHp`.
  - `+118`: `u16 uwAction`, `+120`: `u16 uwDepthReach`, `+122`: `u16 uwHitX`, `+124`: `u16 uwHitY`,
    `+130`: `u8 ubType`.
- **`CombatJob` Slot (`50` bytes × `10` slots):**
  - `+0`: `u8 ubActive`, `+1`: `u8 ubRunning`, `+2`: `u32 ulScript`, `+6`: `u16 uwX`,
    `+8`: `u16 uwY` (altitude), `+10`: `u16 uwZ` (depth), `+12`: `u8 ubFlags` (facing),
    `+13`: `u8 ubType`, `+24`: `u32 ulOwner`, `+28`: `u32 ulWork` (`36`-byte `CombatWork`),
    `+40`: `u32 ulAttackList` (`60` bytes = 6 × 10-byte hit entries), `+44`: `u32 ulHurtList`
    (`60` bytes = 6 × 10-byte hurt entries), `+48`: `u16 uwPaused`.

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
  11. Caption `00AA` (`message.piv` + `S_2[120]` text list): *"The druids sent their best knights to Stonehenge so they may be dubbed into the Quest for the MOONSTONE"*.

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
