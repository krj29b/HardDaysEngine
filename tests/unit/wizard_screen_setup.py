"""Invented wizard screen code, palettes and assets; no original game data (§12.36)."""

import zlib

from harddaysengine.assets.hunk import HUNK_CODE, DecodedHunkExecutable, HunkSection
from harddaysengine.assets.mog_code import MogCode
from tests.unit.test_dice_house_data import _ramp
from tests.unit.test_sound_rules_decoder import _Hunk


def _assets(code: _Hunk, data: _Hunk) -> None:
    for pos, name_at, name in ((0x2E5C, 16, "tower.p"), (0x2E8C, 32, "gift.p")):
        data.data[name_at : name_at + len(name)] = name.encode()
        code.at(pos).w(0x41F9).ref(4, name_at).w(0x2279).ref(35, 4).w(0x4EB9).ref(25, 0x186)
    data.data[48:53] = b"c.cel"
    code.at(0x2F04).w(0x41F9).ref(4, 48).w(0x4EB9).ref(26, 0x6FC)
    code.at(0x2F10).w(0x4EB9).ref(16, 0x250)
    for i, pos in enumerate(range(0x2ED6, 0x2EFE, 8)):
        code.at(pos).w(0x2379).ref(1, 0xCE0).w(i * 4)
    for pos in (0x9598, 0x9890):
        code.at(pos).w(0x2169, 0, 10)
    for pos, text, offset in ((0x2C70, "small", 64), (0x2BFA, "message", 80), (0x2C36, "bold", 96)):
        code.at(pos).w(0x41F9).ref(4, offset)
        data.data[offset : offset + len(text)] = text.encode()
    for pos, cell in ((0x2E74, 0x1E6), (0x2EA4, 0x226)):
        code.at(pos).w(0x43F9).ref(1, cell).w(0x4EB9).ref(0, 0x907C)


def _stages(code: _Hunk) -> None:
    for pos, script in ((0x95CE, 200), (0x96EE, 400), (0x9716, 600), (0x97A8, 200)):
        code.at(pos).w(0x207C).ref(4, script).w(0x45F9).ref(1, 0x180)
        code.w(0x303C, 90, 0x323C, 0, 0x343C, 70, 0x7603, 0x283C, 0, 19, 0x7A28, 0x4EB9).ref(
            0, 0x7084
        )
    for pos, target in (
        (0x9580, 0x2E42),
        (0x9586, 0x8B26),
        (0x9624, 0x7972),
        (0x9694, 0x8B26),
        (0x96AC, 0x9884),
        (0x987C, 0x8B46),
        (0x95C8, 0xAADE),
    ):
        code.at(pos).w(0x4EB9).ref(0, target)
    for pos, wait in ((0x966E, 7), (0x9752, 2), (0x984A, 9)):
        for target in (0x7972, 0x8F88, 0x83C4):
            code.at(pos + (0, 6, 12)[(0x7972, 0x8F88, 0x83C4).index(target)]).w(0x4EB9).ref(
                0, target
            )
        code.at(pos + 18).w(0x7000 | wait, 0x4EB9).ref(34, 0x658).w(0x4EB9).ref(0, 0x1FE6)
        code.w(0x0801, 4, 0x67DA)
    for pos in (0x9666, 0x974A):
        code.at(pos).w(0x7003, 0x4EB9).ref(36, 0x142E)
    for pos in (0x9608, 0x9816):
        code.at(pos).w(0x203C, 0, 130, 0x7209, 0x7400, 0x4EB9).ref(0, 0xA262)
    code.at(0x96D6).w(0x303C, 40, 0x323C, 120, 0x343C, 1, 0x4EB9).ref(0, 0xA262)
    code.at(0x95C2).w(0x203C, 0, 16)
    for pos, wait in ((0x8B2E, 0x8B36), (0x8B4E, 0x8B5E), (0x8B7C, 0x8B86)):
        code.at(pos).w(0x7002)
        code.at(wait).w(0x203C, 0, 13)
    _ramp(code)


def _texts(code: _Hunk, data: _Hunk) -> None:
    code.at(0x9B3C).w(0x49F9).ref(4, 0x400)
    for i in range(10):
        at = 0x500 + 20 * i
        label = f"Item {i}".encode()
        data.data[at : at + len(label)] = label
        data.at(0x400 + 6 * i).w(2 * i).ref(4, at)
    for i, char in enumerate(b" bits\0"):
        code.at(0x9A6A + 4 * i).w(0x14FC, char)
    code.at(0x2F74).w(0x41F9).ref(4, 0x700)
    for i in range(14):
        at = 0x800 + 14 * i
        data.at(0x700 + 4 * i).ref(4, at)
        data.at(at).ref(4, 0x500).w(i + 1, i + 2, 0, 0, 0)
    code.at(0x2F7A).w(0x3039).ref(4, 0x120)
    for pos, op, value in ((0x2F8C, 0x0679, 1), (0x2F94, 0x0C79, 14), (0x2F9E, 0x33FC, 0)):
        code.at(pos).w(op, value).ref(4, 0x120)
    code.at(0x2F6E).w(0x2169, 16)
    for pos, target in ((0x2F86, 0x9196), (0x2FAC, 0x8B7C)):
        code.at(pos).w(0x4EB9).ref(0, target)


def synthetic_screen() -> MogCode:
    code, data, front, names = (_Hunk(bytearray(size)) for size in (0xAC00, 4096, 0x300, 64))
    _assets(code, data)
    _stages(code)
    _texts(code, data)
    for pos, word_channel in ((0x9630, False), (0x2F20, True)):
        for i in range(4):
            code.at(pos + i * (14 if word_channel else 12)).w(0x303C, 30 + i)
            if word_channel:
                code.w(0x323C, i)
            else:
                code.w(0x7200 | i)
            code.w(0x4EB9).ref(44, 0x698)
    front.at(0x250).w(0x41F9).ref(17, 0)
    names.data[:4] = b"bank"
    hunks = {0: code, 4: data, 16: front, 17: names}
    sections = tuple(
        HunkSection(
            i, HUNK_CODE, len(h.data), bytes(h.data), {k: tuple(v) for k, v in h.relocs.items()}
        )
        for i in range(45)
        for h in (hunks.get(i, _Hunk(bytearray())),)
    )
    return MogCode(
        DecodedHunkExecutable("synthetic wizard screen", sections),
        supported=(zlib.crc32(code.data),),
    )
