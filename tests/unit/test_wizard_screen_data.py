"""Checked wizard screen operands with deliberately different synthetic values (§12.36)."""

import struct
from unittest.mock import patch

import pytest

from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.wizard_screen_data import _read_rules, parse_wizard_rules
from tests.unit.wizard_screen_setup import synthetic_screen
from tests.unit.wizard_setup import changed


def test_screen_uses_runtime_assets_spawns_shades_waits_and_message_order() -> None:
    mog = synthetic_screen()
    with patch("harddaysengine.assets.wizard_screen_data.MogCode", return_value=mog):
        rules = parse_wizard_rules(mog.executable)
    assert (rules.tower, rules.gift, rules.cel, rules.bank, rules.font) == (
        "tower.p",
        "gift.p",
        "c.cel",
        "bank",
        "small",
    )
    assert rules.waits == (7, 2, 9) and rules.fade_wait == 13
    assert rules.palette_entry == 8 and rules.palette_period == 3
    assert rules.ramps == ((0, (1, 2, 3, 4, 5)), (3, (6, 7, 8, 9, 10)))
    assert [s.script for s in rules.spawns] == [200, 400, 600]
    assert all(s.position == (90, 0, 70) and s.facing == 3 and s.kind == 40 for s in rules.spawns)
    assert rules.speech_at == ((130, 9, 0), (40, 120, 1), (130, 9, 0))
    assert rules.items == tuple((i * 2, f"Item {i}") for i in range(10))
    assert rules.gold_suffix == " bits"
    assert [lines[0].x for lines in rules.loading.messages] == list(range(1, 15))
    assert [(s.sound, s.channel) for s in rules.sounds] == [(30 + i, i) for i in range(4)]


@pytest.mark.parametrize(
    "pos,value,error",
    [
        (0x968E, b"\x08\x00", "fire port"),
        (0x95D0, struct.pack(">I", 0), "script outside"),
        (0x95D6, struct.pack(">I", 42), "another frame list"),
        (0x95EE, b"\x7a\x29", "idle-object"),
        (0x97AA, struct.pack(">I", 201), "farewell spawn"),
        (0x95C4, struct.pack(">I", 15), "outside palette"),
        (0x9666, b"\x70\0", "palette or frame timing"),
        (0x9680, b"\x71\x05", "MOVEQ"),
        (0x9634, b"\x72\x04", "channel outside"),
        (0x9A7E, b"\x14\xfc\0!", "unterminated.*suffix"),
        (0x9A6C, b"\0\x80", "non-ASCII"),
        (0x2F96, b"\0\x0d", "loading message table"),
        (0x2F8E, b"\0\x02", "cursor update"),
        (0x2F8E, b"\0\0", "cursor step"),
        (0x2F7C, struct.pack(">I", 4), "unexpected wizard target"),
        (0x2E76, struct.pack(">I", 0), "palette copies"),
        (0x2F06, struct.pack(">I", 4096), "outside allocation"),
    ],
)
def test_bad_operand_forms_cells_bounds_and_timings_raise(
    pos: int, value: bytes, error: str
) -> None:
    with pytest.raises(MogVersionError, match=error):
        _read_rules(changed(synthetic_screen(), 0, pos, value))


def test_duplicate_item_slots_and_empty_filenames_are_rejected() -> None:
    with pytest.raises(MogVersionError, match="incomplete wizard item"):
        _read_rules(changed(synthetic_screen(), 4, 0x400, b"\0\x02"))
    with pytest.raises(MogVersionError, match="empty wizard string"):
        _read_rules(changed(synthetic_screen(), 4, 16, b"\0"))
