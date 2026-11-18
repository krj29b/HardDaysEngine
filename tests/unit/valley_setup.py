"""Invented Valley operands, linked texts and wall, with shared loading fixtures (§12.38)."""

import zlib
from dataclasses import replace

from harddaysengine.assets.mog_code import MogCode
from tests.unit.test_sound_rules_decoder import _Hunk
from tests.unit.wizard_screen_setup import synthetic_screen


def synthetic_valley() -> MogCode:
    sections = list(synthetic_screen().executable.sections)
    code, data = (
        _Hunk(
            bytearray(sections[i].data), relocs={k: list(v) for k, v in sections[i].relocs.items()}
        )
        for i in (0, 4)
    )
    floor, tail = _Hunk(bytearray(0x500)), _Hunk(bytearray(0x700))
    for pos, hunk, target in (
        (0x16D8, 0, 0x2FB4),
        (0x16E6, 36, 0x47A),
        (0x16EC, 0, 0x4908),
        (0x16F2, 0, 0x73C),
        (0x170E, 0, 0x99D8),
        (0x174E, 0, 0x2FEA),
        (0x1754, 0, 0x1FD0),
        (0x175C, 34, 0x658),
        (0x1762, 36, 0x65E),
        (0x4910, 0, 0x2F5A),
        (0x4916, 0, 0x29AC),
        (0x492E, 12, 0x3DA),
        (0x29AC, 0, 0x3214),
    ):
        code.at(pos).w(0x4EB9).ref(hunk, target)
    for pos, opcode, value, field in (
        (0x16CA, 0x0C28, 31, 20),
        (0x171A, 0x0428, 4, 73),
        (0x1720, 0x0C30, 2, 0x1000),
        (0x1728, 0x0430, 3, 0x1000),
        (0x1738, 0x0668, 9, 78),
        (0x1742, 0x117C, 0, 20),
        (0x9350, 0x002A, 8, 9),
    ):
        code.at(pos).w(opcode, value, field)
    code.at(0x16D0).w(0x6714)
    for pos, target in ((0x16DE, 0x1FD0), (0x16E2, 0x18D6), (0x172E, 0x18C4)):
        code.at(pos).w(0x6100 if pos == 0x16DE else 0x6000, target - pos - 2)
    code.at(0x1726).w(0x6706)
    code.at(0x175A).w(0x700A)
    code.at(0x4908).w(0x7003)
    code.at(0x3226).w(0x2068, 32)
    tail.at(0x670).w(0x0280, 0, 7)
    tail.at(0x680).w(0x01E8, 22)
    tail.at(0x684).w(0x4E75)
    for pos, hunk, target in ((0x65E, 20, 0x666), (0x664, 36, 0x47A), (0x66A, 0, 0xA2B0)):
        tail.at(pos).w(0x4EB9).ref(hunk, target)
    floor.at(0x3DA).w(0x2279).ref(14, 0)
    for index, value in enumerate((1, -5, 280, 80, 0)):
        floor.at(0x3E0 + index * 4).w(0x32FC, value)
    floor.at(0x3F4).w(0x33FC, 80).ref(14, 78)
    for pos, record, string, text in (
        (0x16D2, 0xC00, 0xD00, b"Invented keys notice"),
        (0x1748, 0xC20, 0xD40, b"Invented victory notice"),
    ):
        code.at(pos).w(0x41F9).ref(4, record)
        data.at(record).ref(4, string).w(0, 60, 1, 0, 0)
        data.data[string : string + len(text)] = text
    for index, pos in enumerate(range(0x301E, 0x3036, 4)):
        code.at(pos).w(0x30BC if pos == 0x3032 else 0x30FC, index + 10)
    for index, hunk in ((0, code), (4, data), (12, floor), (36, tail)):
        sections[index] = replace(
            sections[index],
            data=bytes(hunk.data),
            alloc_size=len(hunk.data),
            relocs={k: tuple(v) for k, v in hunk.relocs.items()},
        )
    executable = replace(synthetic_screen().executable, sections=tuple(sections))
    return MogCode(executable, supported=(zlib.crc32(code.data),))
