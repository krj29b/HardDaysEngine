"""Invented meeting caller and name-substitution records; no original text (§12.40)."""

import zlib
from dataclasses import replace

from harddaysengine.assets.mog_code import MogCode
from tests.unit.test_sound_rules_decoder import _Hunk
from tests.unit.valley_setup import synthetic_valley


def synthetic_meeting() -> MogCode:
    executable = synthetic_valley().executable
    sections = list(executable.sections)
    code, data = (
        _Hunk(
            bytearray(sections[i].data), relocs={k: list(v) for k, v in sections[i].relocs.items()}
        )
        for i in (0, 4)
    )
    code.data.extend(bytes(0x200))
    for pos, hunk, target in (
        (0xB40, 0, 0x3C12),
        (0xB46, 0, 0x73C),
        (0xBA2, 0, 0xABEC),
        (0xBC4, 36, 0),
        (0xBE8, 0, 0xABEC),
        (0xC42, 0, 0x2FEA),
        (0xC48, 0, 0x1FD0),
        (0xC72, 0, 0xABEC),
        (0x1248, 0, 0xABEC),
    ):
        code.at(pos).w(0x4EB9).ref(hunk, target)
    code.at(0xC0A).w(0x0CA9, 0, 7, 54)
    code.at(0xC20).w(0x4A28, 16)
    code.at(0xC82).w(0x0C79, 16).ref(4, 0x100)
    code.at(0xB9C).w(0x203C, 0, 1)
    code.at(0xBE2).w(0x203C, 0, 9)
    code.at(0xC70).w(0x7009)
    code.at(0x1246).w(0x700A)
    code.at(0xBF2).w(0x0668, 7, 78)
    code.at(0xAC02).w(0x33FC, 0).ref(1, 0x1C16)
    code.at(0xAD5E).w(0x4A28, 73, 0x6E08)
    code.at(0xC3C).w(0x41F9).ref(4, 0xD80)
    code.at(0xC28).w(0x47F9).ref(4, 0xE00)
    data.at(0xD80).ref(4, 0xE40).w(0, 40, 1).ref(4, 0xDA0)
    data.at(0xDA0).ref(4, 0xE00).w(0, 60, 1, 0, 0)
    data.data[0xE40:0xE53] = b"Invented protection"
    data.data[0xE00:0xE0B] = b"Placeholder"
    for index, hunk in ((0, code), (4, data)):
        sections[index] = replace(
            sections[index],
            data=bytes(hunk.data),
            alloc_size=len(hunk.data),
            relocs={k: tuple(v) for k, v in hunk.relocs.items()},
        )
    return MogCode(
        replace(executable, sections=tuple(sections)), supported=(zlib.crc32(code.data),)
    )
