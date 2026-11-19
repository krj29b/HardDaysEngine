"""Checked decoders of the ``program`` intro pass and ``mog`` menu/knight-select screens (§12.42).

Nothing is guessed and no original scene table, script offset, palette word or hunk offset is
embedded in Python source (ADR-001, ADR-044). Every instruction word and relocated operand is
checked against the supported executables; anything unexpected raises :class:`MogVersionError`.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from harddaysengine.assets import ending_runtime_data as runtime
from harddaysengine.assets.ending_data import (
    _CAPTION,
    _CODE,
    _FADE,
    _JSR,
    _LEA_A0,
    _LOADER,
    _LONG_CELLS,
    _MOVE_L_ABS,
    _MOVE_L_ABS_D0,
    _MOVE_L_IMM_D0,
    _MOVEA_A0,
    _MOVEA_A1,
    _PALETTE_TO,
    _RTS,
    _SCREENS,
    _TARGET,
    _TEXT,
    _UNPACK,
    _WAIT,
    _buffer,
    _caption_entries,
    _class_tweaks,
    _data_file,
    _fade,
    _flag_bit,
    _hand_back,
    _pair_frames,
    _palette,
    _Scene,
    _spawn_side,
)
from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.mog_code import MogCode, Pointer
from harddaysengine.assets.program_code import ProgramCode, key as _key
from harddaysengine.assets.turn_rules import bold_lines, text_list
from harddaysengine.sim.ending_types import (
    Buffer,
    Call,
    Caption,
    Cell,
    Invoke,
    Load,
    PictureLoad,
    Store,
    Wait,
)
from harddaysengine.sim.intro_types import (
    ForwardWipe,
    IntroLoad,
    IntroPlan,
    IntroStep,
    KnightSelectLayout,
    MogMenuData,
    PictureCopy,
    PictureSetup,
    ProgressSpec,
    SpriteDraw,
    TitleMenuLayout,
)

if TYPE_CHECKING:
    from harddaysengine.assets.hunk import DecodedHunkExecutable

_WIPE = 31
_ABORT_FLAG = (33, 0xF60)
_INTRO_LOADER = (8, 0x136)
_PICTURE_SETUP = (8, 0x8E)
_FORWARD_WIPE = (31, 0x5AA)
_MUSIC_STOP = (1, 0x34)
_MUSIC_STATE = (1, 0x88)
_LOAD_STEP_0, _LOAD_STEP_1, _LOAD_STEP_2, _LOAD_STEP_3 = (
    (31, 0x328),
    (31, 0x3B0),
    (31, 0x42E),
    (31, 0x4AE),
)
_INTRO_SCENES = (0x3F0, 0x438, 0x366, 0x610, 0x6BE, 0x792, 0x738)


def _palette_pokes(
    prog: ProgramCode, hunk: int, pos: int, count: int, what: str
) -> tuple[tuple[tuple[int, int], ...], int]:
    """Decode ``LEA table 0,A0 ; (MOVE.W #word,d16(A0))*count`` (§12.42.3-4)."""
    prog.expect(hunk, pos, _LEA_A0, what)
    if _palette(prog.pointer(hunk, pos + 2, what), what) != 0:
        raise MogVersionError(f"program: {what} targets another palette table")
    pos += 6
    entries: list[tuple[int, int]] = []
    for _ in range(count):
        prog.expect(hunk, pos, 0x317C, what)
        word = prog.word(hunk, pos + 2)
        disp = prog.word(hunk, pos + 4)
        entry, rest = divmod(disp, 2)
        if rest or not 0 <= entry < 32 or word > 0xFFF:
            raise MogVersionError(f"program: {what} poke is out of range")
        entries.append((entry, word))
        pos += 6
    return tuple(entries), pos


def _check_uniform_pokes(
    prog: ProgramCode,
    hunk: int,
    pos: int,
    expected_indices: tuple[int, ...],
    what: str,
) -> tuple[int, int]:
    entries, pos = _palette_pokes(prog, hunk, pos, len(expected_indices), what)
    words = {w for _, w in entries}
    if tuple(idx for idx, _ in entries) != expected_indices or len(words) != 1:
        raise MogVersionError(f"program: {what} does not write one colour to the expected entries")
    return words.pop(), pos


def _progress_tail(prog: ProgramCode) -> tuple[tuple[tuple[int, int], ...], int]:
    if prog.call(_WIPE, 0x32E, "step 0 fade") != (10, 0xC50):
        raise MogVersionError("program: progress step 0 does not fade out")
    if prog.branch(_WIPE, 0x3AA, 0x6000, "step 0 tail") != 0x550:
        raise MogVersionError("program: progress step 0 does not branch to tail")
    gold_entries, pos = _palette_pokes(prog, _WIPE, 0x570, 5, "progress gold entries")
    prog.expect(_WIPE, 0x566, _MOVE_L_ABS, "tail palette period")
    tail_period = prog.long(_WIPE, 0x568)
    prog.expect(_WIPE, pos, _LEA_A0, "tail fade table")
    tail_quick = prog.word(_WIPE, pos + 6)
    if (
        _key(prog.pointer(_WIPE, 0x56C, "tail period cell")) != (_SCREENS, 0xC8C)
        or tail_quick != (0x7000 | tail_period)
        or prog.call(_WIPE, pos + 8, "tail fade") != _FADE
    ):
        raise MogVersionError("program: progress tail fade is not understood")
    return gold_entries, tail_period


def _progress_logo(
    prog: ProgramCode,
    gold_indices: tuple[int, ...],
) -> tuple[int, int, int, int, int]:
    black_1, _ = _check_uniform_pokes(prog, _WIPE, 0x3B0, gold_indices, "step 1 black")
    prog.expect(_WIPE, 0x400, _MOVE_L_IMM_D0, "step 1 logo frame")
    logo_frame = prog.long(_WIPE, 0x402)
    prog.expect(_WIPE, 0x406, 0x323C, "step 1 logo x")
    logo_x = prog.word(_WIPE, 0x408)
    prog.expect(_WIPE, 0x40A, 0x343C, "step 1 logo y")
    logo_y = prog.word(_WIPE, 0x40C)
    if prog.call(_WIPE, 0x40E, "step 1 logo draw") != (23, 0x22C):
        raise MogVersionError("program: step 1 logo draw calls another routine")
    logo_wait = prog.word(_WIPE, 0x422) & 0xFF
    if (
        prog.word(_WIPE, 0x422) & 0xFF00 != 0x7000
        or prog.call(_WIPE, 0x424, "step 1 wait") != _WAIT
        or prog.branch(_WIPE, 0x42A, 0x6000, "step 1 tail") != 0x556
    ):
        raise MogVersionError("program: step 1 wait or tail branch is not understood")
    return black_1, logo_frame, logo_x, logo_y, logo_wait


def _progress_step_2(
    prog: ProgramCode,
    gold_indices: tuple[int, ...],
    black_word: int,
) -> tuple[int, int, int]:
    white_2, pos = _check_uniform_pokes(prog, _WIPE, 0x42E, gold_indices, "step 2 white")
    prog.expect(_WIPE, pos, _MOVE_L_ABS, "step 2 flash period")
    flash_period = prog.long(_WIPE, pos + 2)
    if prog.call(_WIPE, pos + 16, "step 2 palette to") != _PALETTE_TO:
        raise MogVersionError("program: step 2 flash does not call PaletteTo")
    flash_wait = prog.word(_WIPE, pos + 22) & 0xFF
    if (
        prog.word(_WIPE, pos + 22) & 0xFF00 != 0x7000
        or prog.call(_WIPE, pos + 24, "step 2 wait") != _WAIT
    ):
        raise MogVersionError("program: step 2 wait is not understood")
    black_2, _ = _check_uniform_pokes(prog, _WIPE, pos + 30, gold_indices, "step 2 black")
    if black_2 != black_word:
        raise MogVersionError("program: step 1 and step 2 black pokes disagree")
    return white_2, flash_period, flash_wait


def _progress_spec(prog: ProgramCode) -> ProgressSpec:
    """Decode the four progress routines at hunk 31 ``$0328..$05A8`` (§12.42.3)."""
    gold_entries, tail_period = _progress_tail(prog)
    gold_indices = tuple(idx for idx, _ in gold_entries)
    black_1, logo_frame, logo_x, logo_y, logo_wait = _progress_logo(prog, gold_indices)
    white_2, flash_period, flash_wait = _progress_step_2(prog, gold_indices, black_1)

    white_3, pos = _check_uniform_pokes(prog, _WIPE, 0x4AE, gold_indices, "step 3 white")
    prog.expect(_WIPE, pos, _MOVE_L_ABS, "step 3 flash period")
    if prog.long(_WIPE, pos + 2) != flash_period or white_2 != white_3:
        raise MogVersionError("program: step 2 and step 3 palette operands disagree")
    prog.expect(_WIPE, 0x4F4, 0x0C79, "step 3 credit count")
    credit_count = prog.word(_WIPE, 0x4F6)
    if _key(prog.pointer(_WIPE, 0x4F8, "credit page cell")) != (_WIPE, 0x79C):
        raise MogVersionError("program: step 3 tests another page cell")
    prog.expect(_WIPE, 0x500, _LEA_A0, "step 3 credit table")
    table = prog.pointer(_WIPE, 0x502, "step 3 credit table")
    if table.hunk != _WIPE or prog.call(_WIPE, 0x512, "step 3 text") != _TEXT:
        raise MogVersionError("program: step 3 credit table or text call moved")
    black_3, _ = _check_uniform_pokes(prog, _WIPE, 0x51E, gold_indices, "step 3 black")
    if black_3 != black_1 or not 0 < credit_count <= 16:
        raise MogVersionError("program: step 3 black poke or credit count is invalid")
    credits = tuple(
        prog.lines(prog.pointer(_WIPE, table.offset + 4 * i, "credit list"), "credit list")
        for i in range(credit_count)
    )
    return ProgressSpec(
        gold_entries=gold_entries,
        white_word=white_2,
        black_word=black_1,
        tail_period=tail_period,
        flash_period=flash_period,
        logo_frame=logo_frame,
        logo_x=logo_x,
        logo_y=logo_y,
        logo_wait=logo_wait,
        flash_wait=flash_wait,
        credits=credits,
    )


def _wipe_tables(prog: ProgramCode) -> tuple[tuple[int, ...], tuple[int, ...]]:
    prog.expect(_WIPE, 0x5DA, _LEA_A0, "wipe thresholds")
    thresh_ptr = prog.pointer(_WIPE, 0x5DC, "wipe thresholds")
    prog.expect(_WIPE, 0x5E0, 0x43F9, "wipe steps")
    steps_ptr = prog.pointer(_WIPE, 0x5E2, "wipe steps")
    if thresh_ptr.hunk != _WIPE or steps_ptr.hunk != _WIPE or steps_ptr.offset <= thresh_ptr.offset:
        raise MogVersionError("program: forward wipe tables are outside hunk 31")
    count, rest = divmod(steps_ptr.offset - thresh_ptr.offset, 2)
    if rest or not 0 < count <= 32:
        raise MogVersionError("program: forward wipe threshold table size is invalid")
    data = prog.code.hunk_data(_WIPE)
    thresholds = struct.unpack_from(f">{count}H", data, thresh_ptr.offset)
    steps = struct.unpack_from(f">{count}H", data, steps_ptr.offset)
    return thresholds, steps


def _forward_wipe(prog: ProgramCode) -> ForwardWipe:
    """Decode the forward progress wipe at hunk 31 ``$05AA..$0688`` (§12.42.4)."""
    if prog.call(_WIPE, 0x5AA, "forward wipe list clear") != (10, 0xB46):
        raise MogVersionError("program: forward wipe does not clear draw lists")
    entries, pos = _palette_pokes(prog, _WIPE, 0x5B0, 4, "forward wipe palette")
    prog.expect(_WIPE, pos, _LEA_A0, "forward wipe palette write")
    if _palette(
        prog.pointer(_WIPE, pos + 2, "forward wipe palette"), "forward wipe"
    ) != 0 or prog.call(_WIPE, pos + 6, "forward wipe write") != (29, 0x788):
        raise MogVersionError("program: forward wipe does not write palette table 0")
    thresholds, steps = _wipe_tables(prog)
    prog.expect(_WIPE, 0x618, 0x0C79, "wipe music step")
    music_step = prog.word(_WIPE, 0x61A)
    if _key(prog.pointer(_WIPE, 0x61C, "wipe step cell")) != (_WIPE, 0x850) or prog.call(
        _WIPE, 0x622, "wipe music call"
    ) != (1, 0x000):
        raise MogVersionError("program: forward wipe music trigger is not understood")
    prog.expect(_WIPE, 0x62E, 0x0C79, "wipe limit")
    limit = prog.word(_WIPE, 0x630)
    if _key(prog.pointer(_WIPE, 0x632, "wipe progress cell")) != (_WIPE, 0x84E):
        raise MogVersionError("program: forward wipe limit tests another cell")
    return ForwardWipe(entries, thresholds, steps, music_step, limit)


def _picture_setup(prog: ProgramCode) -> PictureSetup:
    """Decode the three picture and palette copies at hunk 8 ``$008E..$00FA`` (§12.42.5)."""
    pos = _PICTURE_SETUP[1]
    copies: list[PictureCopy] = []
    while prog.word(_LOADER, pos) != _RTS:
        prog.expect(_LOADER, pos, _MOVEA_A0, "setup picture source")
        src_buf = _buffer(prog.pointer(_LOADER, pos + 2, "setup src"), "setup src")
        prog.expect(_LOADER, pos + 6, _MOVEA_A1, "setup picture dst")
        dst_buf = _buffer(prog.pointer(_LOADER, pos + 8, "setup dst"), "setup dst")
        if prog.call(_LOADER, pos + 12, "setup unpack") != _UNPACK:
            raise MogVersionError("program: picture setup unpack calls another routine")
        prog.expect(_LOADER, pos + 18, _LEA_A0, "setup palette src")
        src_pal = _palette(prog.pointer(_LOADER, pos + 20, "setup pal src"), "setup pal src")
        prog.expect(_LOADER, pos + 24, 0x43F9, "setup palette dst")
        dst_pal = _palette(prog.pointer(_LOADER, pos + 26, "setup pal dst"), "setup pal dst")
        if prog.call(_LOADER, pos + 30, "setup palette copy") != (8, 0x92E):
            raise MogVersionError("program: picture setup palette copy calls another routine")
        copies.append(PictureCopy(src_buf, dst_buf, src_pal, dst_pal))
        pos += 36
    if not copies:
        raise MogVersionError("program: picture setup has no copies")
    return PictureSetup(tuple(copies))


def _check_abort_branch(prog: ProgramCode, hunk: int, pos: int, target: int, what: str) -> int:
    prog.expect(hunk, pos, 0x4A79, what)
    if _key(prog.pointer(hunk, pos + 2, what)) != _ABORT_FLAG:
        raise MogVersionError(f"program: {what} tests another flag")
    if prog.branch(hunk, pos + 6, 0x6600, what) != target:
        raise MogVersionError(f"program: {what} branches elsewhere")
    return pos + 10


def _intro_picture_raw(prog: ProgramCode, pos: int) -> tuple[Buffer, str, Buffer, int]:
    prog.expect(_LOADER, pos, _MOVE_L_ABS_D0, "intro loader target")
    buffer = _buffer(prog.pointer(_LOADER, pos + 2, "intro loader buffer"), "intro loader buffer")
    if prog.call(_LOADER, pos + 6, "intro loader target") != _TARGET:
        raise MogVersionError("program: intro loader target calls another routine")
    name = prog.name(_LOADER, pos + 12, "intro loader picture")
    prog.expect(_LOADER, pos + 18, _MOVEA_A1, "intro loader scratch")
    scratch_ptr = prog.pointer(_LOADER, pos + 20, "intro loader scratch")
    scratch = Buffer.SHOWN if _key(scratch_ptr) == (2, 0x20A) else _buffer(scratch_ptr, "scratch")
    if prog.call(_LOADER, pos + 24, "intro picture file") != (20, 0x186):
        raise MogVersionError("program: intro loader picture uses another routine")
    return buffer, name, scratch, pos + 30


def _intro_story_picture(prog: ProgramCode, pos: int) -> tuple[PictureLoad, int]:
    buffer, name, _, pos = _intro_picture_raw(prog, pos)
    prog.expect(_LOADER, pos, _LEA_A0, "intro loader palette")
    palette = _palette(prog.pointer(_LOADER, pos + 2, "palette"), "intro loader palette")
    if prog.call(_LOADER, pos + 6, "palette copy") != (10, 0xC2C):
        raise MogVersionError("program: intro loader palette copy uses another routine")
    pos += 12
    tweaked = prog.word(_LOADER, pos) == _LEA_A0 and prog.word(_LOADER, pos + 6) == _JSR
    if tweaked and prog.call(_LOADER, pos + 6, "class tweak") == (8, 0x9DE):
        if _palette(prog.pointer(_LOADER, pos + 2, "tweak"), "class tweak") != palette:
            raise MogVersionError("program: intro class tweak applies to another palette")
        pos += 12
    else:
        tweaked = False
    if prog.call(_LOADER, pos, "intro picture progress") != _LOAD_STEP_3:
        raise MogVersionError("program: intro story picture does not call progress step 3")
    pos = _check_abort_branch(prog, _LOADER, pos + 6, 0x4F4, "intro picture abort")
    return PictureLoad(name, buffer, palette, False, tweaked), pos


@dataclass(slots=True)
class _IntroCelWalk:
    """Symbolic walk of the intro loader's cel placement at hunk 8 ``$0368..$04C6`` (§12.42.2)."""

    prog: ProgramCode
    table: dict[int, object] = field(default_factory=dict)
    a0: object = None
    a1: object = None
    overlay: object = None
    size_of: str = ""
    loads: list[tuple[object, str]] = field(default_factory=list)
    progress_calls: int = 0

    def run(self, pos: int) -> int:
        while not self._opens_file(pos):
            pos = self._step(pos)
        return pos

    def _opens_file(self, pos: int) -> bool:
        if self.prog.word(_LOADER, pos) != _LEA_A0 or self.prog.word(_LOADER, pos + 6) != _JSR:
            return False
        return _key(self.prog.pointer(_LOADER, pos + 8, "loader call")) == (18, 0x8C)

    def _step(self, pos: int) -> int:
        word = self.prog.word(_LOADER, pos)
        if word == 0x2F09:
            return self._progress_step(pos)
        if word == 0xD3FC:
            self.a1 = ("offset", self.a1, self.prog.long(_LOADER, pos + 2))
            return pos + 6
        if word == 0xD3C0:
            self.a1 = ("after", self.size_of)
            return pos + 2
        if word in (_LEA_A0, _MOVEA_A1, 0x20B9, 0x23C9, _JSR):
            return self._absolute(pos, word)
        return self._table_move(pos, word)

    def _progress_step(self, pos: int) -> int:
        if (
            self.prog.call(_LOADER, pos + 2, "cel progress") != _LOAD_STEP_3
            or self.prog.word(_LOADER, pos + 8) != 0x225F
        ):
            raise MogVersionError("program: intro cel progress block is malformed")
        self.progress_calls += 1
        return _check_abort_branch(self.prog, _LOADER, pos + 10, 0x4F4, "cel progress abort")

    def _table_move(self, pos: int, word: int) -> int:
        offset = self.prog.word(_LOADER, pos + 2)
        if word == 0x2149 and self.a0 == "table":
            self.table[offset // 4] = self.a1
        elif word == 0x2268 and self.a0 == "table":
            self.a1 = self.table[offset // 4]
        else:
            raise MogVersionError(f"program: intro cel walk: ${word:04X} at {pos:#x}")
        return pos + 4

    def _absolute(self, pos: int, word: int) -> int:
        operand = self.prog.pointer(_LOADER, pos + 2, "intro cel operand")
        if word == _LEA_A0:
            self.a0 = "table" if operand.hunk == 11 else self.prog.code.cstring(operand, "cel name")
        elif word == _MOVEA_A1:
            self.a1 = _key(operand)
        elif word == 0x20B9 and self.a0 == "table":
            self.table[0] = _key(operand)
        elif word == 0x23C9:
            self.overlay = self.a1
        elif word == _JSR:
            self._call(_key(operand))
        else:
            raise MogVersionError(f"program: intro cel walk: ${word:04X} at {pos:#x}")
        return pos + 6

    def _call(self, target: tuple[int, int]) -> None:
        if not isinstance(self.a0, str):
            raise MogVersionError("program: intro cel call without a file name")
        if target == (21, 0x6FC):
            self.loads.append((self.a1, self.a0))
        elif target == (21, 0x4FE):
            self.size_of = self.a0
        else:
            raise MogVersionError(f"program: intro cel walk calls {target}")

    def cels(self) -> tuple[tuple[tuple[int, str], ...], str]:
        slots: list[tuple[int, str]] = []
        overlay = ""
        for place, name in self.loads:
            owners = [slot for slot, value in self.table.items() if value == place]
            if place == self.overlay and not owners:
                overlay = name
            elif len(owners) == 1:
                slots.append((owners[0], name))
            else:
                raise MogVersionError(f"program: intro cel {name} has no single table slot")
        if not overlay or len({slot for slot, _ in slots}) != len(slots):
            raise MogVersionError("program: intro cel table is incomplete")
        return tuple(slots), overlay


def _intro_mindscape(prog: ProgramCode, pos: int) -> tuple[str, Buffer, int]:
    if prog.call(_LOADER, pos, "intro blackout") != (10, 0xBF8):
        raise MogVersionError("program: intro loader does not start with blackout")
    target, mindscape, scratch, pos = _intro_picture_raw(prog, pos + 6)
    if target != Buffer.SCREEN:
        raise MogVersionError("program: mindscape is not loaded into SCREEN")
    prog.expect(_LOADER, pos, _LEA_A0, "mindscape palette cell")
    if (
        _key(prog.pointer(_LOADER, pos + 2, "mindscape palette cell")) != (26, 0x146)
        or prog.call(_LOADER, pos + 6, "mindscape palette to") != _PALETTE_TO
    ):
        raise MogVersionError("program: mindscape does not fade in from the palette cell")
    return mindscape, scratch, pos + 12


def _intro_loader(prog: ProgramCode) -> IntroLoad:
    """Decode the intro loader at hunk 8 ``$0136..$04F4`` (§12.42.2)."""
    mindscape, scratch, pos = _intro_mindscape(prog, _INTRO_LOADER[1])

    buf_a, name_a, _, pos = _intro_picture_raw(prog, pos)
    prog.expect(_LOADER, pos, _LEA_A0, "bg1a palette")
    pal_a = _palette(prog.pointer(_LOADER, pos + 2, "bg1a palette"), "bg1a palette")
    if prog.call(_LOADER, pos + 6, "bg1a palette copy") != (10, 0xC2C):
        raise MogVersionError("program: bg1a palette copy calls another routine")
    stile, stile_bytes, pos = _data_file(prog, pos + 12, "intro stile")
    if prog.call(_LOADER, pos, "progress step 0") != _LOAD_STEP_0:
        raise MogVersionError("program: intro loader does not call progress step 0")
    pos += 6

    buf_c, name_c, _, pos = _intro_picture_raw(prog, pos)
    if prog.call(_LOADER, pos, "progress step 1") != _LOAD_STEP_1:
        raise MogVersionError("program: intro loader does not call progress step 1")
    pos = _check_abort_branch(prog, _LOADER, pos + 6, 0x4F4, "step 1 abort")

    buf_b, name_b, _, pos = _intro_picture_raw(prog, pos)
    if prog.call(_LOADER, pos, "progress step 2") != _LOAD_STEP_2:
        raise MogVersionError("program: intro loader does not call progress step 2")
    pos = _check_abort_branch(prog, _LOADER, pos + 6, 0x4F4, "step 2 abort")

    pictures: list[PictureLoad] = [
        PictureLoad(name_a, buf_a, pal_a, False, False),
        PictureLoad(name_c, buf_c, -1, False, False),
        PictureLoad(name_b, buf_b, -1, False, False),
    ]
    while prog.word(_LOADER, pos) == _MOVE_L_ABS_D0:
        pic, pos = _intro_story_picture(prog, pos)
        pictures.append(pic)

    walk = _IntroCelWalk(prog)
    pos = walk.run(pos)
    cels, overlay = walk.cels()
    music, music_bytes, pos = _data_file(prog, pos, "intro music")
    prog.expect(_LOADER, pos, _MOVEA_A0, "intro music rnc buffer")
    if prog.branch(_LOADER, pos + 6, 0x6000, "intro music rnc") != 0x7C2:
        raise MogVersionError("program: intro music does not branch to RNC unpack")
    prog.expect(_LOADER, pos + 10, _RTS, "intro loader end")
    return IntroLoad(
        mindscape=mindscape,
        mindscape_scratch=scratch,
        pictures=tuple(pictures),
        cels=cels,
        cels_with_progress=walk.progress_calls,
        overlay=overlay,
        stile=stile,
        stile_bytes=stile_bytes,
        music=music,
        music_bytes=music_bytes,
        progress=_progress_spec(prog),
    )


def _intro_scenes(prog: ProgramCode) -> list[IntroStep]:
    steps: list[IntroStep] = []
    expected_calls = (
        (0, _INTRO_SCENES[0]),
        (0, _INTRO_SCENES[1]),
        _PICTURE_SETUP,
        *((0, sc) for sc in _INTRO_SCENES[2:]),
    )
    for index, target in enumerate(expected_calls):
        if prog.call(_CODE, 0xBA + 6 * index, "intro scene call") != target:
            raise MogVersionError("program: an intro scene call moved")
        if target == _PICTURE_SETUP:
            steps.append(_picture_setup(prog))
        else:
            body = _Scene(prog)
            body.run(target[1])
            steps.extend(body.steps)
    return steps


def _intro_epilogue(prog: ProgramCode, steps: list[IntroStep]) -> str:
    if prog.call(_CODE, 0xEA, "intro pre-caption fade") != (10, 0xC50):
        raise MogVersionError("program: intro pre-caption fade moved")
    steps.append(Invoke(Call.FADE_OUT))
    prog.expect(_CODE, 0xF0, _LEA_A0, "intro closing caption")
    steps.append(Caption(prog.lines(prog.pointer(_CODE, 0xF2, "caption"), "intro caption")))
    if prog.call(_CODE, 0xF6, "intro caption call") != _CAPTION:
        raise MogVersionError("program: intro caption calls another routine")
    prog.expect(_CODE, 0xFC, _MOVE_L_IMM_D0, "intro caption wait")
    if prog.call(_CODE, 0x102, "intro caption wait") != _WAIT:
        raise MogVersionError("program: intro caption wait calls another routine")
    steps.append(Wait(prog.long(_CODE, 0xFE)))
    if prog.call(_CODE, 0x108, "intro final fade") != (10, 0xC50):
        raise MogVersionError("program: intro final fade calls another routine")
    steps.append(Invoke(Call.FADE_OUT))
    if prog.call(_CODE, 0x10E, "intro music unhook") != _MUSIC_STOP:
        raise MogVersionError("program: intro music unhook calls another routine")
    prog.expect(_CODE, 0x114, _LEA_A0, "next program")
    name = prog.name(_CODE, 0x114, "next program")
    prog.expect(_CODE, 0x11A, 0x4EF9, "program loader jump")
    if name != _hand_back(prog):
        raise MogVersionError("program: intro and ending hand-back disagree")
    return name


def _intro_top_level(prog: ProgramCode) -> tuple[tuple[IntroStep, ...], str]:
    """Decode the intro top level at hunk 0 ``$0076..$0114`` (§12.42.1)."""
    prog.expect(_CODE, 0x76, _MOVE_L_ABS, "intro music state")
    if (
        prog.long(_CODE, 0x78) != 0
        or _key(prog.pointer(_CODE, 0x7C, "music state")) != _MUSIC_STATE
    ):
        raise MogVersionError("program: intro music state clear moved")
    steps: list[IntroStep] = []
    for pos in (0x80, 0x8A):
        prog.expect(_CODE, pos, _MOVE_L_ABS, "intro init cell")
        cell = _LONG_CELLS.get(_key(prog.pointer(_CODE, pos + 6, "intro init cell")))
        if cell is None:
            raise MogVersionError("program: intro init stores an unknown cell")
        steps.append(Store(cell, prog.long(_CODE, pos + 2)))
    if prog.call(_CODE, 0x94, "intro fade out") != (10, 0xC50):
        raise MogVersionError("program: intro initial fade out moved")
    steps.append(Invoke(Call.FADE_OUT))
    if prog.call(_CODE, 0x9A, "intro loader") != _INTRO_LOADER:
        raise MogVersionError("program: intro loader call moved")
    steps.append(Load())
    _check_abort_branch(prog, _CODE, 0xA0, 0x114, "intro top abort")
    if prog.call(_CODE, 0xAA, "forward wipe") != _FORWARD_WIPE:
        raise MogVersionError("program: forward wipe call moved")
    steps.append(_forward_wipe(prog))
    prog.expect(_CODE, 0xB0, _MOVE_L_ABS, "intro scene gap")
    if _key(prog.pointer(_CODE, 0xB6, "intro gap cell")) != (3, 0x9A):
        raise MogVersionError("program: intro scene gap writes another cell")
    steps.append(Store(Cell.GAP, prog.long(_CODE, 0xB2)))
    steps.extend(_intro_scenes(prog))
    name = _intro_epilogue(prog, steps)
    return tuple(steps), name


def parse_intro_plan(executable: DecodedHunkExecutable) -> IntroPlan:
    """Decode the whole intro pass of ``program`` (§12.42)."""
    prog = ProgramCode(executable)
    runtime.check_finish_handler(prog)
    steps, next_program = _intro_top_level(prog)
    class_tweaks, class_first = _class_tweaks(prog)
    fade_period, fade_wait, palette_period = _fade(prog)
    return IntroPlan(
        flag_bit=_flag_bit(prog),
        steps=steps,
        load=_intro_loader(prog),
        class_tweaks=class_tweaks,
        class_first=class_first,
        spawns=(_spawn_side(prog, 0x2E4), _spawn_side(prog, 0x30A)),
        pair_frames=_pair_frames(prog),
        caption_entries=_caption_entries(prog),
        fade_period=fade_period,
        fade_wait=fade_wait,
        palette_period=palette_period,
        next_program=next_program,
        overlay=runtime.overlay(prog),
        callbacks=runtime.callbacks(prog),
        scripts=runtime.scripts(prog),
        font=runtime.font(prog),
        job_slots=runtime.job_slots(prog),
        stage_period=runtime.stage_period(prog),
        message=runtime.message_files(prog)[1],
        palette_cell=runtime.palette_cell(prog),
    )


def _mog_span(mog: MogCode) -> int:
    mog.expect(0x91F0, (0x3239,), "text span")
    span_ptr = mog.pointer(0x91F2, "text span", 0)
    mog.expect(0x91F6, (0x9279,), "text margin")
    margin_ptr = mog.pointer(0x91F8, "text margin", 0)
    if span_ptr.hunk != 4 or margin_ptr.hunk != 4:
        raise MogVersionError("mog: text span outside hunk 4")
    h4 = mog.hunk_data(4)
    span: int = struct.unpack_from(">H", h4, span_ptr.offset)[0]
    margin: int = struct.unpack_from(">H", h4, margin_ptr.offset)[0]
    return span - margin


def _find_menu_line_indices(
    mog: MogCode,
    list_ptr: Pointer,
    count: int,
    players_buf: Pointer,
    gore_rec: Pointer,
) -> tuple[int, int]:
    cur = list_ptr
    players_idx = gore_idx = -1
    for idx in range(count):
        if mog.pointer(cur.offset, "menu record text", 4) == players_buf:
            players_idx = idx
        if cur.offset == gore_rec.offset:
            gore_idx = idx
        if idx + 1 < count:
            cur = mog.pointer(cur.offset + 10, "next menu record", 4)
    if players_idx < 0 or gore_idx < 0:
        raise MogVersionError("mog: menu text list is missing players or gore record")
    return players_idx, gore_idx


def _title_menu_layout(mog: MogCode, span: int) -> TitleMenuLayout:
    """Decode the ``mog`` title menu at hunk 0 ``$18FA..$1B42`` (§12.42.6)."""
    cursor_x = mog.operand(0x18FA, 0x33FC, "menu cursor x")
    if mog.pointer(0x18FE, "menu cursor x cell", 0) != Pointer(1, 0x172):
        raise MogVersionError("mog: menu cursor x stored in another cell")
    mog.expect(0x1A9C, (0x41F9,), "menu cursor y table")
    y_ptr = mog.pointer(0x1A9E, "menu cursor y table", 0)
    h4 = mog.hunk_data(4)
    if y_ptr.hunk != 4 or y_ptr.offset + 8 > len(h4):
        raise MogVersionError("mog: menu cursor y table outside hunk 4")
    cursor_y = struct.unpack_from(">4H", h4, y_ptr.offset)

    banner = SpriteDraw(
        frame=mog.operand(0x1AC4, 0x303C, "menu banner frame"),
        x=mog.operand(0x1AC8, 0x323C, "menu banner x"),
        y=mog.operand(0x1ACC, 0x343C, "menu banner y"),
    )
    cursor_frame = mog.operand(0x1ADC, 0x303C, "menu cursor frame")
    mog.expect(0x1B00, (0x45F9,), "menu players number buffer")
    players_buf = mog.pointer(0x1B02, "menu players number buffer", 0)
    mog.expect(0x1B0C, (0x23FC,), "menu gore off string")
    gore_off_ptr = mog.pointer(0x1B0E, "menu gore off string", 0)
    gore_rec_off = mog.pointer(0x1B12, "menu gore record", 0)
    mog.expect(0x1B1E, (0x23FC,), "menu gore on string")
    gore_on_ptr = mog.pointer(0x1B20, "menu gore on string", 0)
    gore_rec_on = mog.pointer(0x1B24, "menu gore record", 0)
    if gore_rec_off != gore_rec_on or gore_rec_on.hunk != 4 or players_buf.hunk != 4:
        raise MogVersionError("mog: menu gore or players record outside hunk 4")

    mog.expect(0x1B28, (0x41F9,), "menu text list")
    list_ptr = mog.pointer(0x1B2A, "menu text list", 0)
    lines = bold_lines(mog, text_list(mog, list_ptr, "title menu text", hunk=4))
    players_idx, gore_idx = _find_menu_line_indices(
        mog, list_ptr, len(lines), players_buf, gore_rec_on
    )
    return TitleMenuLayout(
        banner=banner,
        cursor_frame=cursor_frame,
        cursor_x=cursor_x,
        cursor_y=(cursor_y[0], cursor_y[1], cursor_y[2], cursor_y[3]),
        lines=lines,
        players_line_index=players_idx,
        gore_line_index=gore_idx,
        gore_on_text=mog.cstring(gore_on_ptr, "gore on"),
        gore_off_text=mog.cstring(gore_off_ptr, "gore off"),
        span=span,
    )


def _knight_names(mog: MogCode) -> tuple[str, str, str, str]:
    names: list[str] = []
    for index, pos in enumerate((0x1EFE, 0x1F2E, 0x1F5E, 0x1F8E)):
        if mog.operand(pos, 0xB07C, "portrait index") != index:
            raise MogVersionError("mog: portrait index compare mismatch")
        mog.expect(pos + 6, (0x23FC,), "portrait name pointer")
        name_ptr = mog.pointer(pos + 8, "portrait name pointer", 0)
        if mog.pointer(pos + 12, "active name cell", 0) != Pointer(4, 0x5C) or name_ptr.hunk != 4:
            raise MogVersionError("mog: portrait name store targets another cell")
        mog.expect(pos + 32, (0x237C,), "portrait actor kind")
        kind = mog.long(pos + 34)
        if kind != index or mog.hunk_word(0, pos + 38) != 0x36:
            raise MogVersionError("mog: portrait actor kind mismatch")
        raw_name = mog.cstring(name_ptr, "knight name").strip().replace("_", " ")
        if not raw_name:
            raise MogVersionError("mog: empty knight name")
        names.append(raw_name)
    return (names[0], names[1], names[2], names[3])


def _knight_select_tables(
    mog: MogCode,
) -> tuple[tuple[int, ...], tuple[int, int, int, int], Pointer]:
    mog.expect(0x1D2C, (0x41F9,), "knight select palette")
    pal_ptr = mog.pointer(0x1D2E, "knight select palette", 0)
    mog.expect(0x1E4E, (0x41F9,), "knight select text list")
    list_ptr = mog.pointer(0x1E50, "knight select text list", 0)
    mog.expect(0x1E68, (0x41F9,), "portrait x table")
    px_ptr = mog.pointer(0x1E6A, "portrait x table", 0)
    h4 = mog.hunk_data(4)
    if (
        pal_ptr.hunk != 4
        or list_ptr.hunk != 4
        or px_ptr.hunk != 4
        or pal_ptr.offset + 64 > len(h4)
        or px_ptr.offset + 8 > len(h4)
    ):
        raise MogVersionError("mog: knight select tables outside hunk 4")
    palette = struct.unpack_from(">32H", h4, pal_ptr.offset)
    if any(w > 0xFFF for w in palette):
        raise MogVersionError("mog: knight select palette outside RGB12")
    px = struct.unpack_from(">4H", h4, px_ptr.offset)
    return palette, (px[0], px[1], px[2], px[3]), list_ptr


def _knight_select_layout(mog: MogCode, span: int) -> KnightSelectLayout:
    """Decode the ``mog`` knight-select screen at hunk 0 ``$1CDC..$1FBC`` (§12.42.6)."""
    palette, portrait_x, list_ptr = _knight_select_tables(mog)
    addq = mog.hunk_word(0, 0x1E7A)
    if addq & 0xF1FF != 0x5040:
        raise MogVersionError("mog: portrait frame ADDQ.W is not understood")
    first_frame = ((addq >> 9) & 7) or 8
    portrait_y = mog.operand(0x1E7C, 0x343C, "portrait y")
    highlight_frame = mog.operand(0x1EAA, 0x303C, "highlight frame")
    if mog.operand(0x1EAE, 0x343C, "highlight y") != portrait_y:
        raise MogVersionError("mog: portrait and highlight y disagree")
    name_x = mog.operand(0x1EC6, 0x303C, "knight name x")
    name_y = mog.operand(0x1ECA, 0x323C, "knight name y")
    mog.expect(0x1ECE, (0xB582,), "knight name style clear")

    lines = bold_lines(mog, text_list(mog, list_ptr, "knight select text", hunk=4))
    name_style = lines[0].style & 8
    return KnightSelectLayout(
        lines=lines,
        portrait_x=portrait_x,
        portrait_y=portrait_y,
        portrait_first_frame=first_frame,
        highlight_frame=highlight_frame,
        name_x=name_x,
        name_y=name_y,
        name_style=name_style,
        knight_names=_knight_names(mog),
        palette=palette,
        span=span,
    )


def _mog_char_map(mog: MogCode) -> bytes:
    mog.expect(0x9252, (0x43F9,), "text char map")
    ptr = mog.pointer(0x9254, "text char map", 0)
    if ptr.hunk != 4:
        raise MogVersionError("mog: text char map outside hunk 4")
    h4 = mog.hunk_data(4)
    if not 0 <= ptr.offset <= len(h4) - 104:
        raise MogVersionError("mog: text char map truncated")
    return h4[ptr.offset : ptr.offset + 104]


def parse_menu_data(executable: DecodedHunkExecutable) -> MogMenuData:
    """Decode the ``mog`` title menu and knight-select screen layouts (§12.42.6)."""
    mog = MogCode(executable)
    span = _mog_span(mog)
    return MogMenuData(
        title=_title_menu_layout(mog, span),
        knight_select=_knight_select_layout(mog, span),
        char_map=_mog_char_map(mog),
    )
