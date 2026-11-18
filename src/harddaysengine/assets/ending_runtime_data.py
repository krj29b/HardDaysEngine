"""Checked runtime operands of the ``program`` ending (ARCHITECTURE.md §12.39.5-12.39.9).

Script callbacks, the credit overlay, the text routine, the job pool size and the job handler that
ends a frame loop. Each routine is read instruction by instruction; anything unexpected raises.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.program_code import BEQ, LEA_A0, RTS, key
from harddaysengine.sim.ending_types import (
    Callback,
    Flash,
    FlashSeries,
    Overlay,
    OverlayDraw,
    RampSetup,
    ScriptHunk,
    TextFont,
    WipeSlow,
    WipeSpec,
    WipeStop,
)

if TYPE_CHECKING:
    from harddaysengine.assets.program_code import ProgramCode

_CODE, _WIPE, _SCRIPTS = 0, 31, 2
_PAL_WRITE, _WAIT, _LIVE = (29, 0x788), (29, 0x658), (32, 0xA)
_CPU_MASK, _OVERLAY_CEL, _DRAW_CEL, _PHASE = (24, 0x18), (3, 0x92), (23, 0x22C), (0, 0xEC2)
_STOP, _HANDLERS = (3, 0x8E), (3, 0x28)
_WIPE_STEP, _WIPE_STOP = (31, 0x850), (33, 0xF5A)
_MOVE_W_ABS, _BSR_S = 0x33FC, 0x6100


def _quick(prog: ProgramCode, hunk: int, pos: int, what: str) -> int:
    word = prog.word(hunk, pos)
    if word & 0xFF00 != 0x7000:
        raise MogVersionError(f"program: {what}: expected MOVEQ #n,D0 at {pos:#x}")
    return word & 0xFF


def _wait(prog: ProgramCode, pos: int, what: str) -> int:
    ticks = _quick(prog, _CODE, pos, what)
    if prog.call(_CODE, pos + 2, what) != _WAIT:
        raise MogVersionError(f"program: {what} calls another routine")
    return ticks


def flash(prog: ProgramCode, pos: int) -> Flash:
    """``LEA table ; write ; MOVEQ #n ; wait ; MOVEA.L live ; write ; RTS`` (§12.39.6)."""
    prog.expect(_CODE, pos, LEA_A0, "flash table")
    table = prog.pointer(_CODE, pos + 2, "flash table")
    if prog.call(_CODE, pos + 6, "flash write") != _PAL_WRITE:
        raise MogVersionError("program: flash writes through another routine")
    ticks = _wait(prog, pos + 12, "flash wait")
    prog.expect(_CODE, pos + 20, 0x2079, "flash restore")
    if key(prog.pointer(_CODE, pos + 22, "live palette")) != _LIVE:
        raise MogVersionError("program: flash restores another palette")
    if prog.call(_CODE, pos + 26, "flash restore") != _PAL_WRITE:
        raise MogVersionError("program: flash restores through another routine")
    prog.expect(_CODE, pos + 32, RTS, "flash end")
    data = prog.code.hunk_data(table.hunk)
    if table.hunk != _CODE or table.offset + 64 > len(data):
        raise MogVersionError("program: flash palette outside the code hunk")
    colours = struct.unpack_from(">32H", data, table.offset)
    if any(colour > 0xFFF for colour in colours):
        raise MogVersionError("program: flash palette outside RGB12")
    return Flash(colours, ticks)


def flash_series(prog: ProgramCode, pos: int) -> FlashSeries:
    """``(MOVEQ ; wait ; BSR.S flash)*n ; MOVEQ ; wait ; RTS`` with one flash target."""
    waits: list[int] = []
    targets: set[int] = set()
    while True:
        ticks = _wait(prog, pos, "flash series wait")
        if prog.word(_CODE, pos + 8) == RTS:
            break
        word = prog.word(_CODE, pos + 8)
        if word & 0xFF00 != _BSR_S or word & 0xFF in (0, 0xFF):
            raise MogVersionError(f"program: flash series step at {pos:#x} is not understood")
        waits.append(ticks)
        targets.add(pos + 10 + (word & 0xFF))
        pos += 10
    if len(targets) != 1 or not waits:
        raise MogVersionError("program: flash series calls more than one flash")
    return FlashSeries(tuple(waits), flash(prog, targets.pop()), ticks)


def _wipe_slow(prog: ProgramCode, pos: int) -> WipeSlow:
    prog.expect(_WIPE, pos, 0x0479, "wipe slow")
    amount = prog.word(_WIPE, pos + 2)
    if key(prog.pointer(_WIPE, pos + 4, "wipe step")) != _WIPE_STEP:
        raise MogVersionError("program: wipe slow changes another cell")
    prog.expect(_WIPE, pos + 8, RTS, "wipe slow end")
    return WipeSlow(amount)


def _wipe_stop(prog: ProgramCode, pos: int) -> WipeStop:
    prog.expect(_WIPE, pos, _MOVE_W_ABS, "wipe stop")
    if prog.word(_WIPE, pos + 2) != 1 or key(prog.pointer(_WIPE, pos + 4, "stop")) != _WIPE_STOP:
        raise MogVersionError("program: wipe stop sets another cell")
    prog.expect(_WIPE, pos + 8, RTS, "wipe stop end")
    return WipeStop()


def callbacks(prog: ProgramCode) -> tuple[tuple[tuple[int, int], Callback], ...]:
    """The routines ending scripts reach through ``$B4`` (§12.39.6)."""
    return (
        ((0, 0x8DC), RampSetup()),
        ((0, 0xEC4), flash(prog, 0xEC4)),
        ((0, 0xEE6), flash_series(prog, 0xEE6)),
        ((31, 0x788), _wipe_slow(prog, 0x788)),
        ((31, 0x77E), _wipe_stop(prog, 0x77E)),
    )


def _overlay_draw(prog: ProgramCode, pos: int) -> OverlayDraw:
    if key(prog.pointer(_CODE, pos + 2, "overlay cel")) != _OVERLAY_CEL:
        raise MogVersionError("program: overlay draws another cel")
    forms = ((6, 0x303C), (10, 0x7400), (12, 0x143C), (16, 0x0602), (20, 0x323C), (24, 0x0641))
    for offset, opcode in forms:
        prog.expect(_CODE, pos + offset, opcode, "overlay draw")
    if prog.call(_CODE, pos + 28, "overlay draw") != _DRAW_CEL:
        raise MogVersionError("program: overlay draws through another routine")
    y = (prog.word(_CODE, pos + 14) + prog.word(_CODE, pos + 18)) & 0xFF
    x = (prog.word(_CODE, pos + 22) + prog.word(_CODE, pos + 26)) & 0xFFFF
    return OverlayDraw(prog.word(_CODE, pos + 8), x - 0x10000 if x & 0x8000 else x, y)


def overlay(prog: ProgramCode) -> Overlay:
    """The credit overlay at $0E1C: draws, then a phase test that skips the tail (§12.39.10)."""
    prog.expect(_CODE, 0xE1C, _MOVE_W_ABS, "overlay mask on")
    if key(prog.pointer(_CODE, 0xE20, "overlay mask")) != _CPU_MASK:
        raise MogVersionError("program: overlay sets another flag")
    draws: list[OverlayDraw] = []
    kept, phase, skip, pos = -1, -1, -1, 0xE24
    while prog.word(_CODE, pos) != _MOVE_W_ABS:
        if prog.word(_CODE, pos) == 0x2079:
            draws.append(_overlay_draw(prog, pos))
            pos += 34
            continue
        prog.expect(_CODE, pos, 0x0C79, "overlay phase test")
        if kept >= 0 or key(prog.pointer(_CODE, pos + 4, "overlay phase")) != _PHASE:
            raise MogVersionError("program: overlay phase test is not understood")
        kept, phase = len(draws), prog.word(_CODE, pos + 2)
        skip = prog.branch(_CODE, pos + 8, BEQ, "overlay skip")
        pos += 12
    if skip != pos or prog.word(_CODE, pos + 2) != 0:
        raise MogVersionError("program: overlay skip does not reach its end")
    if key(prog.pointer(_CODE, pos + 4, "overlay mask")) != _CPU_MASK:
        raise MogVersionError("program: overlay clears another flag")
    prog.expect(_CODE, pos + 8, RTS, "overlay end")
    return Overlay(tuple(draws), kept, phase)


def message_files(prog: ProgramCode) -> tuple[str, str]:
    """The font and message picture the entry code loads at $1176 before the ending test."""
    if prog.call(_CODE, 0x5E, "message loader") != (0, 0x1176):
        raise MogVersionError("program: the message loader is called elsewhere")
    font_file = prog.name(_CODE, 0x1180, "text font")
    if prog.call(_CODE, 0x1186, "font load") != (21, 0x6FC):
        raise MogVersionError("program: the text font is loaded by another routine")
    message = prog.name(_CODE, 0x118C, "message picture")
    if prog.call(_CODE, 0x1192, "message open") != (18, 0x8C):
        raise MogVersionError("program: the message picture is opened by another routine")
    return font_file, message


def font(prog: ProgramCode) -> TextFont:
    """Text routine operands at hunk12 $0184 / $01B0 / $021A (§12.39.7)."""
    prog.expect(12, 0x184, 0x323C, "text width")
    prog.expect(12, 0x1B0, 0x43F9, "text charmap")
    charmap = prog.pointer(12, 0x1B2, "text charmap")
    prog.expect(12, 0x21A, 0x0441, "text advance")
    if charmap.hunk != _SCRIPTS:
        raise MogVersionError("program: text charmap outside the script hunk")
    file = message_files(prog)[0]
    return TextFont(file, charmap.offset, prog.word(12, 0x21C), prog.word(12, 0x186))


def job_slots(prog: ProgramCode) -> int:
    """``MOVEQ #n-1,D7`` of the job allocator at hunk10 $0198."""
    count = _quick_any(prog, 10, 0x198, 0x7E00) + 1
    if prog.branch(10, 0x1AE, 0x51CF, "job scan") != 0x1A0:
        raise MogVersionError("program: job scan loops elsewhere")
    return count


def _quick_any(prog: ProgramCode, hunk: int, pos: int, opcode: int) -> int:
    word = prog.word(hunk, pos)
    if word & 0xFF00 != opcode:
        raise MogVersionError(f"program: expected MOVEQ ${opcode:04X} at {pos:#x}")
    return word & 0xFF


def check_finish_handler(prog: ProgramCode) -> None:
    """Handler 0, installed at $0052, sets the frame loop's stop cell and frees the job."""
    prog.expect(_CODE, 0x52, LEA_A0, "handler table")
    if key(prog.pointer(_CODE, 0x54, "handler table")) != _HANDLERS:
        raise MogVersionError("program: handler 0 is installed elsewhere")
    prog.expect(_CODE, 0x58, 0x20BC, "handler 0")
    handler = prog.pointer(_CODE, 0x5A, "handler 0")
    pos = handler.offset
    prog.expect(_CODE, pos, _MOVE_W_ABS, "handler stop")
    if handler.hunk != _CODE or prog.word(_CODE, pos + 2) != 1:
        raise MogVersionError("program: handler 0 does not set the stop cell")
    if key(prog.pointer(_CODE, pos + 4, "handler stop")) != _STOP:
        raise MogVersionError("program: handler 0 sets another cell")
    prog.expect(_CODE, pos + 8, 0x207C, "handler result")
    if prog.long(_CODE, pos + 10) or prog.code.reloc(pos + 10, _CODE) is not None:
        raise MogVersionError("program: handler 0 keeps the job")
    prog.expect(_CODE, pos + 14, RTS, "handler end")


def scripts(prog: ProgramCode) -> ScriptHunk:
    """The script hunk with its relocations (§12.39.5)."""
    relocs = sorted(prog.code.relocations(_SCRIPTS).items())
    return ScriptHunk(prog.code.hunk_data(_SCRIPTS), tuple(relocs))


def _word_store(prog: ProgramCode, hunk: int, pos: int, cell: tuple[int, int], what: str) -> int:
    prog.expect(hunk, pos, _MOVE_W_ABS, what)
    if key(prog.pointer(hunk, pos + 4, what)) != cell:
        raise MogVersionError(f"program: {what} stores another cell")
    return prog.word(hunk, pos + 2)


def _long_store(prog: ProgramCode, pos: int, cell: tuple[int, int], what: str) -> int:
    prog.expect(_WIPE, pos, 0x23FC, what)
    if key(prog.pointer(_WIPE, pos + 6, what)) != cell or prog.code.reloc(pos + 2, _WIPE):
        raise MogVersionError(f"program: {what} stores another cell")
    return prog.long(_WIPE, pos + 2)


def wipe(prog: ProgramCode) -> WipeSpec:
    """Set-up half at hunk31 $068A and the floor test of the running half (§12.39.8)."""
    speed = _long_store(prog, 0x6C4, (2, 0x23E), "wipe speed")
    gap = _long_store(prog, 0x6CE, (3, 0x9A), "wipe gap")
    start = _word_store(prog, _WIPE, 0x6D8, (31, 0x84E), "wipe progress")
    _word_store(prog, _WIPE, 0x6E0, _WIPE_STEP, "wipe first step")
    if _word_store(prog, _WIPE, 0x6F8, _WIPE_STOP, "wipe stop clear"):
        raise MogVersionError("program: wipe starts stopped")
    step = _word_store(prog, _WIPE, 0x700, _WIPE_STEP, "wipe step")
    prog.expect(_WIPE, 0x74E, 0x0C79, "wipe floor")
    if key(prog.pointer(_WIPE, 0x752, "wipe floor")) != (31, 0x84E):
        raise MogVersionError("program: wipe floor tests another cell")
    loop = prog.word(_WIPE, 0x756)
    displacement = (loop & 0xFF) - 0x100 if loop & 0x80 else loop & 0xFF
    if loop & 0xFF00 != 0x6C00 or 0x758 + displacement != 0x71C:
        raise MogVersionError("program: wipe loop is not understood")
    return WipeSpec(start, step, speed, gap, prog.word(_WIPE, 0x750))


def stage_period(prog: ProgramCode) -> int:
    """Fade period of stage mode 2 ($02AC) and mode 3 (hunk10 $0C1C); they must agree."""
    for pos, mode, target in (
        (0x262, 2, 0x2AC),
        (0x26E, 4, 0x292),
        (0x27A, 5, 0x2BC),
        (0x286, 3, 0x2C4),
    ):
        prog.expect(_CODE, pos, 0x0C79, "stage mode test")
        if prog.word(_CODE, pos + 2) != mode or key(prog.pointer(_CODE, pos + 4, "mode")) != (
            3,
            0x88,
        ):
            raise MogVersionError("program: stage mode chain is not understood")
        if prog.branch(_CODE, pos + 8, BEQ, "stage mode") != target:
            raise MogVersionError("program: stage mode branches elsewhere")
    first = _quick(prog, _CODE, 0x2B2, "stage fade period")
    second = _quick_any(prog, 10, 0xC22, 0x7000)
    if first != second:
        raise MogVersionError("program: stage fades disagree")
    return first


def palette_cell(prog: ProgramCode) -> tuple[int, ...]:
    """Initial picture-palette cell (hunk26 $0146) after checking both decode loops (§12.39.3).

    A 4-plane picture fills 16 words, any other 32. A word with bit 15 set keeps its low 15 bits,
    any other word is shifted left once; entries a picture does not fill keep their old value.
    """
    loops = ((0x13A, 0x0C50, 0x15C, 0x3011), (0x1AA, 0x0C51, 0x1C6, 0x3010))
    for test, compare, loop, register in loops:
        prog.expect(20, test, compare, "palette plane test")
        if prog.word(20, test + 2) != 4:
            raise MogVersionError("program: palette word count is chosen by another test")
        if key(prog.pointer(20, loop + 2, "palette cell")) != (26, 0x146):
            raise MogVersionError("program: picture palettes go to another cell")
        rule = (register, 0x0880, 0x000F, 0x6602, 0xE348)
        if tuple(prog.word(20, loop + 6 + 2 * i) for i in range(5)) != rule:
            raise MogVersionError("program: picture palette words are decoded differently")
    return struct.unpack_from(">32H", prog.code.hunk_data(26), 0x146)
