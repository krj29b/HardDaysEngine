"""Checked caller decoding and relocated protection-name identity (§12.40)."""

import struct
from unittest.mock import patch

import pytest

from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.meeting_data import _read_data, parse_meeting_data
from tests.unit.meeting_setup import synthetic_meeting
from tests.unit.wizard_setup import changed


def test_reads_invented_kind_reward_slot_and_finds_name_in_second_row() -> None:
    mog = synthetic_meeting()
    with patch("harddaysengine.assets.meeting_data.MogCode", return_value=mog):
        data = parse_meeting_data(mog.executable)
    assert (data.rules.computer_kind, data.rules.protection_slot, data.rules.progress) == (7, 16, 7)
    assert (data.rules.loot_sheet, data.rules.temple_sheet, data.rules.hoard_sheet) == (1, 9, 10)
    assert data.name_row == 1 and data.message[0].text == "Invented protection"
    assert data.message[1].text == "Placeholder"
    assert data.recolor == tuple(range(10, 16)) and data.style == 8


@pytest.mark.parametrize(
    "pos,value,error",
    [
        (0xB42, struct.pack(">I", 0), "call target"),
        (0xC10, b"\0\x35", "computer field"),
        (0xC84, b"\0\x12", "slots disagree"),
        (0xC22, b"\0\x15", "slots disagree"),
        (0xB9E, struct.pack(">I", 2), "sheet numbers"),
        (0xC70, b"\x70\x08", "defender temple"),
        (0x1246, b"\x70\x09", "hoard sheet"),
        (0xBF6, b"\0\x4d", "displacement"),
        (0xC2A, struct.pack(">I", 0), "not in its message"),
        (0x9352, b"\0\4", "bold-font style"),
        (0x3020, b"\x10\0", "RGB12"),
        (0xAC04, b"\0\1", "not reset"),
        (0xAD62, b"\x64\x08", "signed loser-life"),
    ],
)
def test_unexpected_callers_fields_name_and_palette_are_rejected(
    pos: int, value: bytes, error: str
) -> None:
    with pytest.raises(MogVersionError, match=error):
        _read_data(changed(synthetic_meeting(), 0, pos, value))


def test_nonusable_protection_slot_is_rejected_even_when_both_operands_agree() -> None:
    mog = changed(synthetic_meeting(), 0, 0xC22, b"\0\x14")
    mog = changed(mog, 0, 0xC84, b"\0\x14")
    with pytest.raises(MogVersionError, match="outside usable"):
        _read_data(mog)
