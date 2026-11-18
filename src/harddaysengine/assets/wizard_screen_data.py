"""Checked animated wizard operands and loading data (ARCHITECTURE.md §12.36)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harddaysengine.assets.dice_house_data import class_ramps
from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.mog_code import MogCode, Pointer
from harddaysengine.assets.stonehenge_data import _fade
from harddaysengine.assets.turn_rules import text_list
from harddaysengine.sim.m68k import s8
from harddaysengine.sim.sound import SoundKind, SoundRequest
from harddaysengine.sim.wizard_types import WizardLoading, WizardRules, WizardSpawn

if TYPE_CHECKING:
    from harddaysengine.assets.hunk import DecodedHunkExecutable


def _at(mog: MogCode, pos: int, expected: Pointer, hunk: int = 0) -> None:
    if mog.pointer(pos, "wizard target", hunk) != expected:
        raise MogVersionError(f"mog: unexpected wizard target at hunk {hunk} offset {pos}")


def _call(mog: MogCode, pos: int, expected: Pointer) -> None:
    if mog.call_target(pos, "wizard call") != expected:
        raise MogVersionError(f"mog: unexpected wizard call at {pos}")


def _string(mog: MogCode, pointer: Pointer) -> str:
    if not 0 <= pointer.offset < len(mog.hunk_data(pointer.hunk)):
        raise MogVersionError("mog: wizard string outside allocation")
    value = mog.cstring(pointer, "wizard string")
    if not value:
        raise MogVersionError("mog: empty wizard string")
    return value


def _moveq(mog: MogCode, pos: int, register: int = 0) -> int:
    opcode = mog.word(pos)
    if opcode & 0xFF00 != 0x7000 | register << 9:
        raise MogVersionError(f"mog: expected wizard MOVEQ at {pos}")
    return s8(opcode & 255)


def _file(mog: MogCode, pos: int, loader: Pointer, call_offset: int = 12) -> str:
    pointer = mog.lea(pos, 0x41F9, "wizard file")
    if pointer.hunk != 4:
        raise MogVersionError("mog: wizard filename outside data hunk")
    if call_offset == 12:
        mog.expect(pos + 6, (0x2279,), "wizard picture destination")
    _call(mog, pos + call_offset, loader)
    return _string(mog, pointer)


def _spawn(mog: MogCode, pos: int) -> WizardSpawn:
    script = mog.lea(pos, 0x207C, "wizard script")
    if script.hunk != 4 or not 0 < script.offset < len(mog.hunk_data(4)):
        raise MogVersionError("mog: wizard script outside data hunk")
    if mog.lea(pos + 6, 0x45F9, "wizard frame list") != Pointer(1, 0x180):
        raise MogVersionError("mog: wizard spawn uses another frame list")
    coordinates = tuple(
        mog.operand(pos + 12 + 4 * i, opcode, "wizard spawn position")
        for i, opcode in enumerate((0x303C, 0x323C, 0x343C))
    )
    facing = _moveq(mog, pos + 24, 3)
    mog.expect(pos + 26, (0x283C,), "wizard unused D4 immediate")
    kind = _moveq(mog, pos + 32, 5)
    if kind != 40:
        raise MogVersionError("mog: wizard spawn does not use the idle-object handler")
    _call(mog, pos + 34, Pointer(0, 0x7084))
    return WizardSpawn(
        script.offset, (coordinates[0], coordinates[1], coordinates[2]), facing, kind
    )


def _sounds(
    mog: MogCode, starts: tuple[int, ...], *, word_channel: bool
) -> tuple[SoundRequest, ...]:
    result = []
    for pos in starts:
        seq = mog.operand(pos, 0x303C, "wizard synth sequence")
        channel = (
            mog.operand(pos + 4, 0x323C, "wizard synth channel")
            if word_channel
            else _moveq(mog, pos + 4, 1)
        )
        _call(mog, pos + (8 if word_channel else 6), Pointer(44, 0x698))
        if not 0 <= channel < 4:
            raise MogVersionError("mog: wizard synth channel outside four voices")
        result.append(SoundRequest(SoundKind.START, seq, channel))
    return tuple(result)


def _loading(mog: MogCode) -> WizardLoading:
    table = mog.lea(0x2F74, 0x41F9, "wizard loading texts")
    count = mog.operand(0x2F94, 0x0C79, "loading text count")
    if table.hunk != 4 or count != 14:
        raise MogVersionError("mog: unsupported loading message table")
    mog.expect(0x2F7A, (0x3039,), "loading cursor read")
    for pos in (0x2F8C, 0x2F94):
        if mog.operand(pos, 0x0679 if pos == 0x2F8C else 0x0C79, "loading cursor") < 1:
            raise MogVersionError("mog: invalid loading cursor step")
        _at(mog, pos + 4, mog.pointer(0x2F7C, "loading cursor"))
    if mog.word(0x2F8E) != 1 or mog.operand(0x2F9E, 0x33FC, "loading cursor reset") != 0:
        raise MogVersionError("mog: unexpected loading cursor update")
    _at(mog, 0x2FA2, mog.pointer(0x2F7C, "loading cursor"))
    mog.expect(0x2F6E, (0x2169,), "loading font selection")
    mog.expect(0x2F70, (16,), "loading font slot")
    _call(mog, 0x2F86, Pointer(0, 0x9196))
    _call(mog, 0x2FAC, Pointer(0, 0x8B7C))
    messages = tuple(
        text_list(mog, mog.pointer(table.offset + i * 4, "loading text", 4), hunk=4)
        for i in range(count)
    )
    return WizardLoading(
        _string(mog, mog.lea(0x2BFA, 0x41F9, "message picture")),
        _string(mog, mog.lea(0x2C36, 0x41F9, "message font")),
        messages,
        _sounds(mog, (0x2F20, 0x2F2E, 0x2F3C, 0x2F4A), word_channel=True),
    )


def _items(mog: MogCode) -> tuple[tuple[int, str], ...]:
    table = mog.lea(0x9B3C, 0x49F9, "wizard item names")
    if table.hunk != 4 or not 0 <= table.offset <= len(mog.hunk_data(4)) - 60:
        raise MogVersionError("mog: wizard item names outside data hunk")
    rows = tuple(
        (
            mog.hunk_word(4, table.offset + i * 6),
            _string(mog, mog.pointer(table.offset + i * 6 + 2, "wizard item name", 4)),
        )
        for i in range(10)
    )
    if {slot for slot, _ in rows} != set(range(0, 20, 2)):
        raise MogVersionError("mog: incomplete wizard item names")
    return rows


def _suffix(mog: MogCode) -> str:
    chars: list[str] = []
    for pos in range(0x9A6A, 0x9A82, 4):
        value = mog.operand(pos, 0x14FC, "wizard gold suffix")
        if value > 127:
            raise MogVersionError("mog: non-ASCII wizard gold suffix")
        if value == 0:
            return "".join(chars)
        chars.append(chr(value))
    raise MogVersionError("mog: unterminated wizard gold suffix")


def _stage_data(mog: MogCode) -> tuple[tuple[int, int, int], ...]:
    at = []
    for pos in (0x9608, 0x9816):
        mog.expect(pos, (0x203C,), "wizard text x")
        _call(mog, pos + 10, Pointer(0, 0xA262))
        at.append((mog.long(pos + 2), _moveq(mog, pos + 6, 1), _moveq(mog, pos + 8, 2)))
    gift = tuple(
        mog.operand(0x96D6 + i * 4, op, "wizard gift text")
        for i, op in enumerate((0x303C, 0x323C, 0x343C))
    )
    _call(mog, 0x96E2, Pointer(0, 0xA262))
    return (at[0], (gift[0], gift[1], gift[2]), at[1])


def _assets(mog: MogCode) -> tuple[str, str, str, str, str]:
    tower = _file(mog, 0x2E5C, Pointer(25, 0x186))
    gift = _file(mog, 0x2E8C, Pointer(25, 0x186))
    cel = _file(mog, 0x2F04, Pointer(26, 0x6FC), 6)
    _call(mog, 0x2F10, Pointer(16, 0x250))
    mog.operand(0x250, 0x41F9, "wizard bank filename", hunk=16)
    bank = mog.pointer(0x252, "wizard bank filename", 16)
    if bank.hunk != 17:
        raise MogVersionError("mog: wizard bank filename outside filename hunk")
    for i, pos in enumerate(range(0x2ED6, 0x2EFE, 8)):
        mog.expect(pos, (0x2379,), "wizard aliased cel slot")
        mog.expect(pos + 6, (i * 4,), "wizard cel slot offset")
        _at(mog, pos + 2, Pointer(1, 0xCE0))
    for pos in (0x9598, 0x9890):
        mog.expect(pos, (0x2169,), "wizard font selection")
        mog.expect(pos + 2, (0,), "wizard font slot")
        mog.expect(pos + 4, (10,), "wizard active font field")
    return (
        tower,
        gift,
        cel,
        _string(mog, bank),
        _string(mog, mog.lea(0x2C70, 0x41F9, "wizard font")),
    )


def _read_rules(mog: MogCode) -> WizardRules:
    _checks(mog)
    tower, gift, cel, bank, font = _assets(mog)
    spawns = tuple(_spawn(mog, pos) for pos in (0x95CE, 0x96EE, 0x9716, 0x97A8))
    if spawns[0] != spawns[3]:
        raise MogVersionError("mog: wizard farewell spawn differs from arrival")
    entry, ramps = _palette_data(mog)
    period, waits = _timing(mog)
    fade_period, fade_wait = _fade(mog)
    return WizardRules(
        tower,
        gift,
        cel,
        bank,
        font,
        spawns[:3],
        ramps,
        entry,
        period,
        waits,
        _stage_data(mog),
        fade_period,
        fade_wait,
        _sounds(mog, (0x9630, 0x963C, 0x9648, 0x9654), word_channel=False),
        _items(mog),
        _suffix(mog),
        _loading(mog),
    )


def _palette_data(mog: MogCode) -> tuple[int, tuple[tuple[int, tuple[int, ...]], ...]]:
    mog.expect(0x95C2, (0x203C,), "wizard class palette offset")
    entry = mog.long(0x95C4)
    if entry % 2 or not 0 <= entry <= 54:
        raise MogVersionError("mog: wizard class ramp outside palette")
    ramps = class_ramps(mog)
    if any(word > 4095 for _, words in ramps for word in words):
        raise MogVersionError("mog: wizard class ramp outside RGB12")
    return entry // 2, ramps


def _timing(mog: MogCode) -> tuple[int, tuple[int, int, int]]:
    period = _moveq(mog, 0x9666)
    waits = tuple(_moveq(mog, pos) for pos in (0x9680, 0x9764, 0x985C))
    if period < 1 or min(waits) < 1 or period != _moveq(mog, 0x974A):
        raise MogVersionError("mog: invalid wizard palette or frame timing")
    return period, (waits[0], waits[1], waits[2])


def _checks(mog: MogCode) -> None:
    for pos, target in (
        (0x9580, 0x2E42),
        (0x9586, 0x8B26),
        (0x9624, 0x7972),
        (0x9694, 0x8B26),
        (0x96AC, 0x9884),
        (0x987C, 0x8B46),
        (0x95C8, 0xAADE),
    ):
        _call(mog, pos, Pointer(0, target))
    for pos in (0x966E, 0x9752, 0x984A):
        for offset, target in ((0, 0x7972), (6, 0x8F88), (12, 0x83C4), (26, 0x1FE6)):
            _call(mog, pos + offset, Pointer(0, target))
        _call(mog, pos + 20, Pointer(34, 0x658))
        mog.expect(pos + 32, (0x0801,), "wizard fire port")
        mog.expect(pos + 34, (4,), "wizard fire bit")
        mog.expect(pos + 36, (0x67DA,), "wizard frame-loop branch")
    for pos in (0x9668, 0x974C):
        _call(mog, pos, Pointer(36, 0x142E))
    for pos, cell in ((0x2E74, 0x1E6), (0x2EA4, 0x226)):
        if mog.lea(pos, 0x43F9, "wizard picture palette") != Pointer(1, cell):
            raise MogVersionError("mog: wizard picture palette copies disagree")
        _call(mog, pos + 6, Pointer(0, 0x907C))


def parse_wizard_rules(executable: DecodedHunkExecutable) -> WizardRules:
    """Read original assets, scripts, palettes and timing without game-data literals (§12.36)."""
    return _read_rules(MogCode(executable))
