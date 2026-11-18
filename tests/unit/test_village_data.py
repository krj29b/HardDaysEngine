"""Invented village instruction fixtures; no extracted tables or strings (§12.37)."""

import struct
from unittest.mock import patch

import pytest

from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.mog_code import MogCode
from harddaysengine.assets.village_data import _read_rules, parse_village_rules
from harddaysengine.sim.village import VillageRules
from tests.unit.wizard_setup import _mog, changed


def _synthetic() -> MogCode:
    code = bytearray(0xA000)
    struct.pack_into(">3H", code, 0x18B6, 0x0C28, 5, 73)
    struct.pack_into(">4H", code, 0x18BC, 0x6C06, 0x0628, 2, 73)
    struct.pack_into(">2HI", code, 0x18C4, 0x7009, 0x4EB9, 0xABEC)
    struct.pack_into(">HIIHIH", code, 0x18CC, 0x33F9, 0x1B1C, 0x1AF0, 0x4EB9, 0, 0x4E75)
    for pos, wait in ((0x8B2E, 0x8B36), (0x8B4E, 0x8B5E), (0x8B7C, 0x8B86)):
        struct.pack_into(">H", code, pos, 0x7002)
        struct.pack_into(">HI", code, wait, 0x203C, 19)
    return _mog(code, bytearray(4), {0: [0x18C8], 1: [0x18CE, 0x18D2], 36: [0x18D8]}, [])


def test_decodes_invented_reward_and_blocking_wait() -> None:
    mog = _synthetic()
    with patch("harddaysengine.assets.village_data.MogCode", return_value=mog):
        rules = parse_village_rules(mog.executable)
    assert rules == VillageRules(5, 2, 9, 2, 19)


@pytest.mark.parametrize(
    "pos,value,error",
    [
        (0x18B6, b"\x0c\x68", "life cap"),
        (0x18BA, b"\0H", "displacement"),
        (0x18B8, b"\1\0", "outside byte"),
        (0x18C0, b"\1\0", "outside byte"),
        (0x18BC, b"\x64\x06", "signed cap branch"),
        (0x18C4, b"\x70\x08", "temple sheet"),
        (0x18C8, struct.pack(">I", 17), "target"),
        (0x18CE, struct.pack(">I", 0), "budget cell"),
        (0x18D2, struct.pack(">I", 0), "spent cell"),
        (0x18D8, struct.pack(">I", 2), "map setup"),
        (0x18DC, b"\0\0", "return"),
        (0x8B60, struct.pack(">I", 20), "fades disagree"),
    ],
)
def test_changed_instruction_forms_fields_and_continuations_are_rejected(
    pos: int, value: bytes, error: str
) -> None:
    with pytest.raises(MogVersionError, match=error):
        _read_rules(changed(_synthetic(), 0, pos, value))
