"""Clean-room Amiga ``program`` starting cinematic interpreter (ARCHITECTURE.md §7.4)."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from pymoonstone.assets.arena import decode_stile
from pymoonstone.assets.hunk import parse_hunk_executable
from pymoonstone.assets.palette import RGBColor, default_combat_palette
from pymoonstone.render.renderer import CANVAS_WIDTH, PLAYFIELD_HEIGHT
from pymoonstone.render.scene import blit_indexed_sprite
from pymoonstone.sim.palette_fx import rgb12_to_rgb24

if TYPE_CHECKING:
    from collections.abc import Sequence

    import numpy.typing as npt

    from pymoonstone.app.scene_assets import GameAssetBundle
    from pymoonstone.assets.cel import DecodedCel

_PLAYFIELD_CLIP: tuple[int, int, int, int] = (0, 0, CANVAS_WIDTH, PLAYFIELD_HEIGHT)
_CHARMAP_OFFSET = 11744
_TITLE_LOGO_FRAME = 0x49
_CAPTION_TEXT_OFFSET = 120
_CREDIT_TEXT_OFFSETS: tuple[int, ...] = (11884, 11912, 11954, 11996, 12066, 12024)
_TEXT_PAL_INDICES: tuple[int, ...] = (5, 9, 10, 11, 12)
_TEXT_GOLD_WORDS: tuple[int, ...] = (0x0000, 0x0FED, 0x0DC9, 0x0B95, 0x0842)
_WIPE_PAL_WORDS: tuple[tuple[int, int], ...] = (
    (9, 0x0A00),
    (10, 0x0600),
    (11, 0x0300),
    (12, 0x0FC6),
)
_CAPTION_PAL_WORDS: tuple[int, ...] = (0x0800, 0x0600, 0x0400, 0x0000, 0x0200)
_KNIGHT_TWEAK_RAMP_WORDS: tuple[tuple[int, int, int, int], ...] = (
    (0x005D, 0x0028, 0x0016, 0x0003),  # Blue
    (0x0E00, 0x0900, 0x0600, 0x0300),  # Red
    (0x0FA0, 0x0B40, 0x0930, 0x0710),  # Orange/Yellow
    (0x00C5, 0x0082, 0x0061, 0x0040),  # Green
)
_WIPE_THRESHOLDS: tuple[int, ...] = (10, 18, 33, 65, 115, 900, 920, 940, 960, 980, 1200)
_WIPE_STEPS: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 5, 4, 3, 2, 1)
_FLASH_A_FN = 3780
_FLASH_B_FN = 3814
_FLASH_A_S0_OFFSET = 3918
_FLASH_B_S0_OFFSET = 3982
_INTRO_CEL_NAMES: tuple[str, ...] = ("au1.cel", "li1.cel", "ha1.cel", "dw1.cel", "da1.cel")


def _s8(val: int) -> int:
    masked = val & 0xFF
    return masked - 0x100 if masked >= 0x80 else masked


def _s16(val: int) -> int:
    masked = val & 0xFFFF
    return masked - 0x10000 if masked >= 0x8000 else masked


def _blit_playfield(
    buf: npt.NDArray[np.uint8],
    pixels: npt.NDArray[np.uint8],
    x: int,
    y: int,
    *,
    mirrored: bool = False,
) -> None:
    blit_indexed_sprite(
        buf,
        pixels,
        x,
        y,
        mirrored=mirrored,
        clip_rect=_PLAYFIELD_CLIP,
    )


def _pad_palette_32(palette: Sequence[RGBColor]) -> list[RGBColor]:
    out = list(palette[:32])
    while len(out) < 32:
        out.append((0, 0, 0))
    return out


def _scale_palette(palette: Sequence[RGBColor], num: int, den: int) -> tuple[RGBColor, ...]:
    clamped = max(0, min(den, num))
    if clamped >= den:
        return tuple(palette)
    return tuple(
        ((r * clamped) // den, (g * clamped) // den, (b * clamped) // den) for r, g, b in palette
    )


def _fade_palette_ends(
    palette: tuple[RGBColor, ...],
    local_tick: int,
    total_ticks: int,
    *,
    fade_in: int = 16,
    fade_out: int = 16,
) -> tuple[RGBColor, ...]:
    if fade_in > 0 and local_tick < fade_in:
        return _scale_palette(palette, local_tick, fade_in)
    if fade_out > 0 and local_tick >= total_ticks - fade_out:
        return _scale_palette(palette, max(0, total_ticks - 1 - local_tick), fade_out)
    return palette


def assemble_stile_panorama(
    stile_words: Sequence[int],
    pictures: tuple[npt.NDArray[np.uint8], npt.NDArray[np.uint8], npt.NDArray[np.uint8]],
) -> npt.NDArray[np.uint8]:
    """Assemble the 320x1200 vertical panorama from ``intro.stile`` and 3 ``.piv`` atlases."""
    panorama = np.zeros((1200, CANVAS_WIDTH), dtype=np.uint8)
    for idx, code in enumerate(stile_words[:480]):
        dst_r, dst_c = divmod(idx, 10)
        pic_idx, tile_idx = divmod(code, 80)
        if not (0 <= pic_idx < 3):
            continue
        src_r, src_c = divmod(tile_idx, 10)
        src_pic = pictures[pic_idx]
        y0, x0 = dst_r * 25, dst_c * 32
        sy0, sx0 = src_r * 25, src_c * 32
        panorama[y0 : y0 + 25, x0 : x0 + 32] = src_pic[sy0 : sy0 + 25, sx0 : sx0 + 32]
    return panorama


def _build_wipe_progress_curve() -> tuple[int, ...]:
    progress = 0
    positions: list[int] = [0]
    while progress < 1000:
        row = 0
        while row < 10 and _WIPE_THRESHOLDS[row] < progress:
            row += 1
        progress = min(1000, progress + _WIPE_STEPS[row])
        positions.append(progress)
    return tuple(positions)


def render_bold_text_list(
    buf: npt.NDArray[np.uint8],
    h2: bytes,
    bold_cel: DecodedCel,
    list_offset: int,
) -> None:
    """Render a 14-byte ``S_2`` text linked list using ``bold.f`` (§7.4)."""
    charmap = h2[_CHARMAP_OFFSET : _CHARMAP_OFFSET + 104]
    cur = list_offset
    while 0 < cur <= len(h2) - 14:
        t_off, tx, ty, style, nxt = struct.unpack_from(">IHHHI", h2, cur)
        end = h2.find(0, t_off)
        if end < 0:
            break
        text = h2[t_off:end].decode("ascii", errors="replace")
        glyphs = [
            bold_cel.frames[charmap[(ord(ch) - 0x20) & 0xFF] % len(bold_cel.frames)]
            for ch in text
            if 0 <= (ord(ch) - 0x20) < len(charmap)
        ]
        total_w = sum(cf.meta.width - 2 for cf in glyphs)
        cx = (((CANVAS_WIDTH - total_w) & 0xFFFF) >> 1) if (style & 1) else tx
        for cf in glyphs:
            _blit_playfield(buf, cf.pixels, cx, ty)
            cx += cf.meta.width - 2
        cur = nxt


@dataclass(slots=True)
class _IntroAnimJob:
    """Single ``program`` ``S_2`` animation bytecode job slot (ARCHITECTURE.md §7.4)."""

    p: int
    x: int
    y: int
    z: int
    flags: int
    has_script: bool = True
    loop_active: int = 0
    loop_count: int = 0
    p_loop: int = 0
    deferred: int = 0
    p_deferred: int = 0

    @classmethod
    def spawn(cls, script_offset: int, kind: str = "A") -> _IntroAnimJob:
        if kind == "B":
            return cls(p=script_offset, x=120, y=0, z=100, flags=3)
        return cls(p=script_offset, x=160, y=0, z=100, flags=1)

    def _handle_end_of_frame(self, h2: bytes) -> bool:
        if self.loop_active:
            self.loop_count = (self.loop_count - 1) & 0xFFFF
            if self.loop_count != 0:
                self.p = self.p_loop
                return True
            self.loop_active = 0
        if self.deferred:
            self.deferred = 0
            self.p = self.p_deferred
            return True
        if h2[self.p + 1] != 0xFF:
            self.p += 2
            return True
        self.has_script = False
        return True

    def _handle_control_op(self, op: int, h2: bytes) -> int:
        if op == 0x80:
            arg = h2[self.p + 1]
            self.flags = (self.flags ^ 2) if arg == 0xFF else arg
            self.p += 2
            return 0
        if op == 0x84:
            mode = h2[self.p + 1]
            target = int(struct.unpack_from(">I", h2, self.p + 2)[0])
            if mode == 3:
                self.p = target
            else:
                self.p_deferred = target
                self.deferred = 1
                self.p += 6
            return 0
        if op == 0x88:
            cnt = h2[self.p + 1]
            self.loop_count = cnt if cnt != 0 else 4
            self.loop_active = 1
            self.p += 2
            self.p_loop = self.p
            return 0
        fn_addr = int(struct.unpack_from(">I", h2, self.p + 2)[0])
        self.p += 6
        return 1 if fn_addr == _FLASH_A_FN else (2 if fn_addr == _FLASH_B_FN else 0)

    def _execute_draw_op(
        self,
        op: int,
        h2: bytes,
        cels: Sequence[DecodedCel],
        shown: npt.NDArray[np.uint8],
        work: npt.NDArray[np.uint8],
    ) -> None:
        slot = (op & 0x1F) >> 2
        fidx = h2[self.p + 1]
        dy = _s8(h2[self.p + 2])
        d3 = h2[self.p + 3]
        dx = _s16(int(struct.unpack_from(">H", h2, self.p + 4)[0]))
        if 0 <= slot < len(cels) and cels[slot].frames:
            cf = cels[slot].frames[fidx % len(cels[slot].frames)]
            mirr = bool(self.flags & 2)
            sx = _s16((self.x - dx - cf.meta.width) if mirr else (self.x + dx))
            sy = _s16(self.y + self.z + dy)
            if d3 & 0x10:
                _blit_playfield(shown, cf.pixels, sx, sy, mirrored=mirr)
            _blit_playfield(work, cf.pixels, sx, sy, mirrored=mirr)
        self.p += 6

    def step_frame(
        self,
        h2: bytes,
        cels: Sequence[DecodedCel],
        shown: npt.NDArray[np.uint8],
        work: npt.NDArray[np.uint8],
    ) -> tuple[bool, int]:
        """Execute one script frame; return ``(still_active, flash_kind)``."""
        if not self.has_script:
            return False, 0
        flash = 0
        while 0 <= self.p < len(h2):
            op = h2[self.p]
            if op == 0xFF:
                return self._handle_end_of_frame(h2), flash
            if op >= 0x80:
                flash = max(flash, self._handle_control_op(op, h2))
            else:
                self._execute_draw_op(op, h2, cels, shown, work)
        self.has_script = False
        return False, flash


@dataclass(slots=True, frozen=True)
class _SceneSpec:
    """Declarative specification for one ``program`` intro story scene (§7.4)."""

    bg_name: str
    vbls_per_frame: int
    num_frames: int
    ov1_phase: int
    tweaked_palette: bool
    spawns: tuple[tuple[int, int, str], ...]
    fade_in: int = 16
    fade_out: int = 16


_STORY_SCENE_SPECS: tuple[_SceneSpec, ...] = (
    # 1. Scene 001B: 5 druids with torches marching across bg1b.piv forest floor
    _SceneSpec(
        bg_name="bg1b.piv",
        vbls_per_frame=8,
        num_frames=68,
        ov1_phase=0,
        tweaked_palette=False,
        spawns=((0, 5636, "A"), (12, 5636, "A"), (24, 5636, "A"), (36, 5636, "A"), (48, 5636, "A")),
        fade_in=0,
        fade_out=16,
    ),
    # 2. Scene 001C: 5 druids approaching Stonehenge behind ov1.cel foliage
    _SceneSpec(
        bg_name="bg2.piv",
        vbls_per_frame=8,
        num_frames=48,
        ov1_phase=1,
        tweaked_palette=True,
        spawns=((0, 5056, "A"), (8, 5056, "A"), (16, 5056, "A"), (24, 5056, "A"), (32, 5056, "A")),
    ),
    # 3. Scene 001A: Stonehenge overhead druid circle + PAIRS + central altar
    _SceneSpec(
        bg_name="bg3.piv",
        vbls_per_frame=6,
        num_frames=102,
        ov1_phase=0,
        tweaked_palette=True,
        spawns=(
            (0, 6996, "A"),
            (0, 7034, "A"),
            (0, 6818, "A"),
            (0, 6996, "B"),
            (0, 7034, "B"),
            (0, 6594, "B"),
            (0, 7078, "A"),
            (16, 7078, "B"),
            (32, 7592, "A"),
            (48, 7592, "B"),
            (64, 8312, "A"),
        ),
    ),
    # 4. Scene 002C Part 1: High Druid raising crescent amulet & staff on bg4.piv
    _SceneSpec(
        bg_name="bg4.piv",
        vbls_per_frame=6,
        num_frames=24,
        ov1_phase=0,
        tweaked_palette=False,
        spawns=((0, 584, "A"),),
        fade_in=16,
        fade_out=0,
    ),
    # 5. Scene 002C Part 2: High Druid invoking the swirling cloud vortex on bg5a.piv
    _SceneSpec(
        bg_name="bg5a.piv",
        vbls_per_frame=6,
        num_frames=36,
        ov1_phase=0,
        tweaked_palette=True,
        spawns=((0, 2804, "A"),),
        fade_in=0,
        fade_out=16,
    ),
    # 6. Scene 002D: Knight drawing broadsword before Stonehenge on bg2a.piv + ov1 phase 2
    _SceneSpec(
        bg_name="bg2a.piv",
        vbls_per_frame=6,
        num_frames=31,
        ov1_phase=2,
        tweaked_palette=True,
        spawns=((0, 4932, "A"),),
    ),
    # 7. Scene 002F: Knight entering the Stonehenge circle on bg3.piv
    _SceneSpec(
        bg_name="bg3.piv",
        vbls_per_frame=8,
        num_frames=31,
        ov1_phase=0,
        tweaked_palette=True,
        spawns=(
            (0, 6996, "A"),
            (0, 7034, "A"),
            (0, 6958, "A"),
            (0, 7554, "A"),
            (0, 7942, "A"),
            (0, 6996, "B"),
            (0, 7034, "B"),
            (0, 6958, "B"),
            (0, 7554, "B"),
            (0, 7942, "B"),
            (0, 8568, "A"),
            (0, 8582, "A"),
        ),
    ),
    # 8. Scene 002E: High Druid dubbing the kneeling knight on bg5a.piv
    _SceneSpec(
        bg_name="bg5a.piv",
        vbls_per_frame=6,
        num_frames=27,
        ov1_phase=0,
        tweaked_palette=True,
        spawns=((0, 1022, "A"),),
    ),
)


def _draw_ov1_overlay(work: npt.NDArray[np.uint8], ov1: DecodedCel | None, phase: int) -> None:
    if ov1 is None or len(ov1.frames) < 4 or phase == 0:
        return
    _blit_playfield(work, ov1.frames[0].pixels, 0, 56)
    _blit_playfield(work, ov1.frames[3].pixels, 51, 134)
    if phase != 2:
        _blit_playfield(work, ov1.frames[1].pixels, 157, 104)
        _blit_playfield(work, ov1.frames[2].pixels, 297, 82)


def _build_caption_palette(base_palette: Sequence[RGBColor]) -> tuple[RGBColor, ...]:
    cap_pal = _pad_palette_32(base_palette)
    for idx, w12 in enumerate(_CAPTION_PAL_WORDS, start=1):
        cap_pal[idx] = rgb12_to_rgb24(w12)
    for idx, w12 in zip(_TEXT_PAL_INDICES, _TEXT_GOLD_WORDS, strict=True):
        cap_pal[idx] = rgb12_to_rgb24(w12)
    return tuple(cap_pal)


def _unpack_pal32(h0: bytes, offset: int) -> tuple[RGBColor, ...]:
    return tuple(rgb12_to_rgb24(w) for w in struct.unpack_from(">32H", h0, offset))


def _set_text_palette_colors(
    pal: list[RGBColor], colors: Sequence[RGBColor]
) -> tuple[RGBColor, ...]:
    for idx, col in zip(_TEXT_PAL_INDICES, colors, strict=True):
        pal[idx] = col
    return tuple(pal)


def _lerp_white_to_gold(gold_rgb: Sequence[RGBColor], step: int) -> list[RGBColor]:
    return [
        (
            255 - ((255 - r) * step) // 16,
            255 - ((255 - g) * step) // 16,
            255 - ((255 - b) * step) // 16,
        )
        for r, g, b in gold_rgb
    ]


def _step_active_jobs(
    jobs: list[_IntroAnimJob],
    h2: bytes,
    cels: Sequence[DecodedCel],
    shown: npt.NDArray[np.uint8],
    work: npt.NDArray[np.uint8],
) -> int:
    frame_flash = 0
    for job in list(jobs):
        active, jf = job.step_frame(h2, cels, shown, work)
        frame_flash = max(frame_flash, jf)
        if not active:
            jobs.remove(job)
    return frame_flash


def _render_credit_frames(
    moon_bg: npt.NDArray[np.uint8],
    h2: bytes,
    bold: DecodedCel,
) -> tuple[npt.NDArray[np.uint8], ...]:
    credit_frames: list[npt.NDArray[np.uint8]] = []
    for off in _CREDIT_TEXT_OFFSETS:
        cframe = moon_bg.copy()
        render_bold_text_list(cframe, h2, bold, off)
        credit_frames.append(cframe)
    return tuple(credit_frames)


@dataclass(slots=True)
class IntroCinematicPlayer:
    """Lazy, clean-room player for the full ``program`` starting cinematic (§7.4)."""

    h0: bytes
    h2: bytes
    panorama: npt.NDArray[np.uint8]
    bg1a_palette: tuple[RGBColor, ...]
    title_frame: npt.NDArray[np.uint8]
    credit_frames: tuple[npt.NDArray[np.uint8], ...]
    caption_frame: npt.NDArray[np.uint8]
    caption_palette: tuple[RGBColor, ...]
    wipe_curve: tuple[int, ...]
    flash_a_palette: tuple[RGBColor, ...]
    flash_b_palette: tuple[RGBColor, ...]
    _scene_cache: dict[int, tuple[tuple[npt.NDArray[np.uint8], ...], tuple[int, ...]]] = field(
        default_factory=dict
    )

    @classmethod
    def try_build(cls, bundle: GameAssetBundle) -> IntroCinematicPlayer | None:
        """Build an :class:`IntroCinematicPlayer` if Disk 1 intro assets exist in ``bundle``."""
        required_files = (
            "program",
            "intro.stile",
            "bg1a.piv",
            "bg1c.piv",
            "bg1b.piv",
            "message.piv",
        )
        if not all(bundle.disk_set.has_file(name) for name in required_files):
            return None
        bold = bundle.get_cel("bold.f")
        prg = parse_hunk_executable("program", bundle.disk_set.read_file("program"))
        if bold is None or len(prg.sections) < 3:
            return None
        bg1a = bundle.get_picture("bg1a.piv")
        bg1c = bundle.get_picture("bg1c.piv")
        bg1b = bundle.get_picture("bg1b.piv")
        msg_pic = bundle.get_picture("message.piv")
        assert bg1a is not None and bg1c is not None and bg1b is not None and msg_pic is not None

        h0, h2 = prg.sections[0].data, prg.sections[2].data
        stile_words = decode_stile(bundle.disk_set.read_file("intro.stile"))
        panorama = assemble_stile_panorama(stile_words, (bg1a.pixels, bg1c.pixels, bg1b.pixels))
        moon_bg = panorama[:PLAYFIELD_HEIGHT, :CANVAS_WIDTH]

        title_frame = moon_bg.copy()
        if len(bold.frames) > _TITLE_LOGO_FRAME:
            _blit_playfield(title_frame, bold.frames[_TITLE_LOGO_FRAME].pixels, 9, 60)

        caption_frame = msg_pic.pixels[:PLAYFIELD_HEIGHT, :CANVAS_WIDTH].copy()
        render_bold_text_list(caption_frame, h2, bold, _CAPTION_TEXT_OFFSET)

        return cls(
            h0=h0,
            h2=h2,
            panorama=panorama,
            bg1a_palette=tuple(_pad_palette_32(bg1a.palette)),
            title_frame=title_frame,
            credit_frames=_render_credit_frames(moon_bg, h2, bold),
            caption_frame=caption_frame,
            caption_palette=_build_caption_palette(msg_pic.palette),
            wipe_curve=_build_wipe_progress_curve(),
            flash_a_palette=_unpack_pal32(h0, _FLASH_A_S0_OFFSET),
            flash_b_palette=_unpack_pal32(h0, _FLASH_B_S0_OFFSET),
        )

    def _title_or_credit_palette(
        self,
        local_tick: int,
        total_ticks: int,
        *,
        fade_bg_in: bool,
    ) -> tuple[RGBColor, ...]:
        pal = list(self.bg1a_palette)
        gold_rgb = [rgb12_to_rgb24(w) for w in _TEXT_GOLD_WORDS]
        if fade_bg_in and local_tick < 16:
            return _scale_palette(_set_text_palette_colors(pal, gold_rgb), local_tick, 16)
        if not fade_bg_in and local_tick < 2:
            return _set_text_palette_colors(pal, [(255, 255, 255)] * 5)
        if not fade_bg_in and local_tick < 18:
            return _set_text_palette_colors(pal, _lerp_white_to_gold(gold_rgb, local_tick - 2))
        if local_tick >= total_ticks - 16:
            rem = max(0, total_ticks - 1 - local_tick)
            return _set_text_palette_colors(pal, _scale_palette(gold_rgb, rem, 16))
        return _set_text_palette_colors(pal, gold_rgb)

    def _get_story_scene_frames(
        self,
        bundle: GameAssetBundle,
        scene_index: int,
    ) -> tuple[tuple[npt.NDArray[np.uint8], ...], tuple[int, ...]]:
        if scene_index in self._scene_cache:
            return self._scene_cache[scene_index]
        spec = _STORY_SCENE_SPECS[scene_index]
        bg_pic = bundle.get_picture(spec.bg_name)
        shown = (
            bg_pic.pixels[:PLAYFIELD_HEIGHT, :CANVAS_WIDTH].copy()
            if bg_pic is not None
            else np.zeros((PLAYFIELD_HEIGHT, CANVAS_WIDTH), dtype=np.uint8)
        )
        valid_cels = [c for name in _INTRO_CEL_NAMES if (c := bundle.get_cel(name)) is not None]
        ov1 = bundle.get_cel("ov1.cel")

        jobs: list[_IntroAnimJob] = []
        frames: list[npt.NDArray[np.uint8]] = []
        flashes: list[int] = []
        for fidx in range(spec.num_frames):
            for sp_frame, script_off, kind in spec.spawns:
                if sp_frame == fidx:
                    jobs.append(_IntroAnimJob.spawn(script_off, kind))
            work = shown.copy()
            flashes.append(_step_active_jobs(jobs, self.h2, valid_cels, shown, work))
            _draw_ov1_overlay(work, ov1, spec.ov1_phase)
            frames.append(work)

        result = (tuple(frames), tuple(flashes))
        self._scene_cache[scene_index] = result
        return result

    def _story_scene_palette(
        self,
        bundle: GameAssetBundle,
        spec: _SceneSpec,
        round_index: int,
        local_tick: int,
        total_ticks: int,
        flash_flag: int,
    ) -> tuple[RGBColor, ...]:
        if flash_flag == 1 and (local_tick % spec.vbls_per_frame) < 4:
            return self.flash_a_palette
        if flash_flag == 2 and ((local_tick // 3) % 2 == 0):
            return self.flash_b_palette

        if spec.bg_name == "bg1b.piv":
            pal = list(self.bg1a_palette)
            for idx, w12 in _WIPE_PAL_WORDS:
                pal[idx] = rgb12_to_rgb24(w12)
        else:
            pic = bundle.get_picture(spec.bg_name)
            pal = _pad_palette_32(pic.palette if pic is not None else default_combat_palette())
            if spec.tweaked_palette:
                ramp = _KNIGHT_TWEAK_RAMP_WORDS[round_index & 3]
                for idx, w12 in enumerate(ramp, start=8):
                    pal[idx] = rgb12_to_rgb24(w12)
        return _fade_palette_ends(
            tuple(pal),
            local_tick,
            total_ticks,
            fade_in=spec.fade_in,
            fade_out=spec.fade_out,
        )

    def render_tick(
        self,
        bundle: GameAssetBundle,
        tick_count: int,
    ) -> tuple[npt.NDArray[np.uint8], tuple[RGBColor, ...]]:
        """Return ``(playfield_pixels, palette)`` for ``tick_count`` at 50 Hz."""
        wipe_ticks = len(self.wipe_curve) * 2
        story_ticks = sum(s.num_frames * s.vbls_per_frame for s in _STORY_SCENE_SPECS)
        # 120 Mindscape + 150 Title + 6*90 Credits + wipe_ticks + story_ticks + 220 Caption
        cycle_len = 120 + 150 + 540 + wipe_ticks + story_ticks + 220
        round_idx = max(0, tick_count) // cycle_len
        t = max(0, tick_count) % cycle_len

        if t < 120:
            ms_pic = bundle.get_picture("mindscape")
            if ms_pic is not None:
                pal = _fade_palette_ends(tuple(_pad_palette_32(ms_pic.palette)), t, 120)
                return ms_pic.pixels[:PLAYFIELD_HEIGHT, :CANVAS_WIDTH], pal
        t -= 120

        if t < 150:
            return self.title_frame, self._title_or_credit_palette(t, 150, fade_bg_in=True)
        t -= 150

        if t < 540:
            page_idx = min(len(self.credit_frames) - 1, t // 90)
            local = t % 90
            return (
                self.credit_frames[page_idx],
                self._title_or_credit_palette(local, 90, fade_bg_in=False),
            )
        t -= 540

        if t < wipe_ticks:
            step_idx = min(len(self.wipe_curve) - 1, t // 2)
            y_off = self.wipe_curve[step_idx]
            wipe_pal = list(self.bg1a_palette)
            for idx, w12 in _WIPE_PAL_WORDS:
                wipe_pal[idx] = rgb12_to_rgb24(w12)
            return self.panorama[y_off : y_off + PLAYFIELD_HEIGHT, :CANVAS_WIDTH], tuple(wipe_pal)
        t -= wipe_ticks

        for s_idx, spec in enumerate(_STORY_SCENE_SPECS):
            s_len = spec.num_frames * spec.vbls_per_frame
            if t < s_len:
                frames, flashes = self._get_story_scene_frames(bundle, s_idx)
                f_idx = min(len(frames) - 1, t // spec.vbls_per_frame)
                pal = self._story_scene_palette(bundle, spec, round_idx, t, s_len, flashes[f_idx])
                return frames[f_idx], pal
            t -= s_len

        pal = _fade_palette_ends(self.caption_palette, t, 220)
        return self.caption_frame, pal
