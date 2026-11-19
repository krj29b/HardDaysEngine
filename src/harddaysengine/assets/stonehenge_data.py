"""Checked Stonehenge operands, messages and hunk-42 scripts (§12.34)."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.fight_program import fight_constants, script_image
from harddaysengine.assets.mog_code import MogCode, Pointer
from harddaysengine.assets.town_data import _call
from harddaysengine.assets.turn_rules import bold_lines, text_list
from harddaysengine.sim.sound import SoundKind, SoundRequest
from harddaysengine.sim.stonehenge import RitualEngine, RitualRules, RitualSpawn, StoneRules

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harddaysengine.assets.hunk import DecodedHunkExecutable
    from harddaysengine.sim.fight_script import CelShape


def _file(mog: MogCode, pointer: Pointer) -> str:
    data = mog.hunk_data(pointer.hunk)
    if not 0 <= pointer.offset < len(data):
        raise MogVersionError("mog: Stonehenge filename outside allocation")
    value = mog.cstring(pointer, "Stonehenge filename")
    if not value:
        raise MogVersionError("mog: empty Stonehenge filename")
    return value


def _match_rows(mog: MogCode) -> tuple[tuple[int, int], ...]:
    rows = []
    for pos in (0x177E, 0x178C, 0x179A, 0x17A8):
        bit = mog.operand(pos, 0x0801, "Stonehenge stone bit")
        mog.expect(pos + 4, (0x6708,), "Stonehenge absent bit branch")
        frame = mog.operand(pos + 6, 0xB07C, "Stonehenge moon frame")
        if mog.branch_target(pos + 10, 0x6700, "Stonehenge match branch") != 0x1820:
            raise MogVersionError("mog: unexpected Stonehenge match target")
        if bit > 7:
            raise MogVersionError("mog: Stonehenge bit outside inventory byte")
        rows.append((bit, frame))
    return tuple(rows)


def _ending_rows(mog: MogCode, *, classes: bool) -> tuple[tuple[int, int], ...]:
    rows = []
    starts = (0x1844, 0x1850, 0x185C, 0x1868) if classes else (0x1822, 0x182C, 0x1836)
    for pos in starts:
        if classes:
            mog.expect(pos, (0xB0BC,), "Stonehenge ending class")
            key, branch = mog.long(pos + 2), pos + 6
        else:
            key, branch = mog.operand(pos, 0xB07C, "Stonehenge ending moon"), pos + 4
        mog.expect(branch, (0x6604,), "Stonehenge ending branch")
        bit = mog.operand(branch + 2, 0x08C7, "Stonehenge ending bit")
        if bit > 7:
            raise MogVersionError("mog: Stonehenge ending bit outside boot flags")
        rows.append((key, bit))
    return tuple(rows)


def _fade(mog: MogCode) -> tuple[int, int]:
    periods, waits = [], []
    for pos, wait in ((0x8B2E, 0x8B36), (0x8B4E, 0x8B5E), (0x8B7C, 0x8B86)):
        opcode = mog.expect(pos, (0x7002,), "Stonehenge fade period")
        periods.append(opcode & 255)
        mog.expect(wait, (0x203C,), "Stonehenge fade wait")
        waits.append(mog.long(wait + 2))
    if len(set(periods)) != 1 or len(set(waits)) != 1:
        raise MogVersionError("mog: Stonehenge blocking fades disagree")
    return periods[0], waits[0]


def _ramps(mog: MogCode) -> tuple[tuple[int, tuple[int, ...]], ...]:
    rows = []
    for pos in (0xAB74, 0xAB92, 0xABB0, 0xABCE):
        mog.expect(pos, (0x0CA9,), "ritual class compare")
        mog.expect(pos + 6, (54,), "ritual class field")
        mog.expect(pos + 8, (0x6614, 0x6610), "ritual class branch")
        values = tuple(mog.operand(pos + 10 + i * 4, 0x30FC, "ritual shade") for i in range(3))
        last = mog.operand(pos + 22, 0x30BC, "ritual final shade")
        if any(word > 4095 for word in (*values, last)):
            raise MogVersionError("mog: ritual palette word outside RGB12")
        rows.append((mog.long(pos + 2), (*values, last)))
    mog.expect(0xABE8, (0x4E75,), "ritual ramp return")
    return tuple(rows)


def _spawn(mog: MogCode, pos: int) -> RitualSpawn:
    script = mog.lea(pos, 0x41F9, "ritual script")
    if script.hunk != 42 or not 0 <= script.offset < len(mog.hunk_data(42)):
        raise MogVersionError("mog: ritual script outside hunk42")
    if mog.lea(pos + 6, 0x45F9, "ritual cel table") != Pointer(42, 0x3DE):
        raise MogVersionError("mog: ritual spawn uses another cel table")
    position = tuple(
        mog.operand(pos + 12 + i * 4, op, "ritual position")
        for i, op in enumerate((0x303C, 0x323C, 0x343C))
    )
    facing = mog.operand(pos + 24, 0x163C, "ritual facing")
    kind = mog.word(pos + 28)
    if kind & 0xFF80 != 0x7A00:
        raise MogVersionError("mog: invalid ritual actor type instruction")
    _call(mog, pos + 30, 0x7084)
    return RitualSpawn(script.offset, (position[0], position[1], position[2]), facing, kind & 127)


def _handler(mog: MogCode) -> None:
    mog.expect(0xA9AE, (0x217C,), "ritual handler installation")
    if mog.pointer(0xA9B0, "ritual handler") != Pointer(0, 0xAAC0):
        raise MogVersionError("mog: unexpected ritual handler")
    mog.expect(0xA9B4, (40,), "ritual handler table slot")
    mog.expect(0xAAC6, (0x23FC,), "ritual script clear")
    if mog.long(0xAAC8) or mog.reloc(0xAAC8) is not None:
        raise MogVersionError("mog: ritual handler does not clear its script")
    if mog.pointer(0xAACC, "ritual script cell") != Pointer(1, 0xC0C):
        raise MogVersionError("mog: unexpected ritual script cell")
    if not mog.operand(0xAAD0, 0x33FC, "ritual stop latch"):
        raise MogVersionError("mog: ritual handler does not set stop")
    if mog.pointer(0xAAD4, "ritual stop latch") != mog.pointer(0xAA6A, "ritual stop clear"):
        raise MogVersionError("mog: ritual stop stores disagree")


def _ritual(mog: MogCode) -> RitualRules:
    _handler(mog)
    mog.expect(0xA9A2, (0x4EB9,), "ritual bank call")
    if mog.pointer(0xA9A4, "ritual bank") != Pointer(16, 0x1E8):
        raise MogVersionError("mog: unexpected ritual bank loader")
    mog.operand(0x1E8, 0x41F9, "ritual bank LEA", hunk=16)
    bank_pointer = mog.pointer(0x1EA, "ritual bank filename", 16)
    sounds = []
    for pos in (0xAA3A, 0xAA44, 0xAA4E):
        sound = mog.operand(pos, 0x303C, "ritual fixed sound")
        target = mog.call_target(pos + 4, "ritual fixed channel")
        if target.hunk != 16 or target.offset not in (0, 16, 32, 48):
            raise MogVersionError("mog: unexpected ritual sound channel")
        sounds.append(SoundRequest(SoundKind.FIXED, sound, target.offset // 16))
    wait = mog.operand(0xAA6E, 0x33FC, "ritual frame wait")
    if mog.expect(0xAA96, (0x7006,), "ritual loop wait") & 255 != wait:
        raise MogVersionError("mog: ritual frame waits disagree")
    mog.expect(0xAA2E, (0x203C,), "ritual palette offset")
    entry = mog.long(0xAA30)
    if entry % 2 or not 0 <= entry <= 56:
        raise MogVersionError("mog: ritual shade offset outside palette")
    mog.expect(0xAA5E, (0x7004,), "ritual palette period")
    _call(mog, 0xAAA8, 0xA2D6)
    mog.expect(0xAAB2, (0x6E0A,), "ritual sound odds branch")
    if mog.call_target(0xAAB8, "ritual picked sound") != Pointer(16, 0x88):
        raise MogVersionError("mog: ritual sound does not use free-channel play")
    return RitualRules(
        _file(mog, mog.lea(0xA95E, 0x41F9, "ritual picture")),
        _file(mog, mog.lea(0xA996, 0x41F9, "ritual cels")),
        _file(mog, bank_pointer),
        (_spawn(mog, 0xA9B6), _spawn(mog, 0xA9DA)),
        _ramps(mog),
        entry // 2,
        mog.word(0xAA5E) & 255,
        wait,
        tuple(sounds),
        mog.operand(0xAAAE, 0xB07C, "ritual sound threshold"),
        mog.operand(0xAAB4, 0x303C, "ritual picked sound"),
    )


def _read_rules(mog: MogCode) -> StoneRules:
    period, wait = _fade(mog)
    offer = mog.lea(0x17B6, 0x41F9, "Stonehenge offer")
    quest = mog.lea(0x1876, 0x41F9, "Stonehenge quest complete")
    ritual = mog.lea(0xA93E, 0x41F9, "ritual message")
    _call(mog, 0x17BC, 0x2FEA)
    _call(mog, 0x187C, 0x2FB4)
    _call(mog, 0xA944, 0x2FEA)
    recolor = tuple(
        mog.operand(pos, 0x30FC, "message recolour") for pos in range(0x301E, 0x3032, 4)
    )
    recolor += (mog.operand(0x3032, 0x30BC, "message final recolour"),)
    return StoneRules(
        _match_rows(mog),
        _ending_rows(mog, classes=False),
        _ending_rows(mog, classes=True),
        mog.operand(0x189A, 0x0047, "ending boot flag"),
        mog.operand(0x17F8, 0x0C28, "Danu life cap", displacement=73),
        mog.operand(0x1800, 0x0628, "Danu life gain", displacement=73),
        mog.expect(0x1882, (0x7014,), "quest wait") & 255,
        period,
        wait,
        _file(mog, mog.lea(0x2BFA, 0x41F9, "message picture")),
        _file(mog, mog.lea(0x2C36, 0x41F9, "message font")),
        recolor,
        bold_lines(mog, text_list(mog, offer, "Stonehenge offer", hunk=4)),
        bold_lines(mog, text_list(mog, quest, "Stonehenge quest complete", hunk=4)),
        bold_lines(mog, text_list(mog, ritual, "ritual message", hunk=42)),
        _file(mog, mog.lea(0x18A4, 0x41F9, "ending executable")),
        _ritual(mog),
    )


def parse_stone_rules(executable: DecodedHunkExecutable) -> StoneRules:
    """Decode the supported mog, never embedding original tables or texts (§12.34)."""
    return _read_rules(MogCode(executable))


def ritual_engine(mog: MogCode, cels: Mapping[str, CelShape]) -> RitualEngine:
    image = script_image(mog, 42)
    routines = dict(image.routines)
    for pos, hunk in mog.relocations(42).items():
        if hunk == 0 and mog.hunk_data(42)[pos : pos + 4] == (0xAAA8).to_bytes(4, "big"):
            routines[pos] = "ritual_sound"
    return RitualEngine(replace(image, routines=routines), cels, fight_constants(mog))
