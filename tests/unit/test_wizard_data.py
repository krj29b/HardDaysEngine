"""Counted wizard text, table identity and immutable substitutions (§12.35)."""

import struct
from unittest.mock import patch

import pytest

from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.mog_code import Pointer
from harddaysengine.assets.wizard_data import _read_dialogue, _speech, parse_wizard_dialogue
from harddaysengine.sim.rewards import GiftKind, GiftResult
from tests.unit.wizard_setup import changed, synthetic_mog


def test_runtime_tables_keep_row_order_and_substitute_without_mutating_data() -> None:
    mog = synthetic_mog()
    with patch("harddaysengine.assets.wizard_data.MogCode", return_value=mog):
        dialogue = parse_wizard_dialogue(mog.executable)
    assert dialogue.line_step == 12 and dialogue.recency == 55
    assert dialogue.greeting.lines[0] == "Speech 1600"
    assert dialogue.gift(GiftResult(GiftKind.ITEM, 3)) == dialogue.items[3]
    assert dialogue.gift(GiftResult(GiftKind.GOLD, 2)) == dialogue.gold[2]
    assert dialogue.gift(GiftResult(GiftKind.CURSE)) == dialogue.curse
    for i, (stat, speech) in enumerate(dialogue.stats):
        assert dialogue.gift(GiftResult(GiftKind.STAT, i, stat=stat)) == speech
    before = mog.hunk_data(4)
    for key in (1, 2, 3, 4, 99):
        speech = dialogue.farewell(key)
        if key in (1, 2):
            assert speech.substitute("Sample") == (f"Farewell {key}", "Field: Sample")
            assert speech.substitute("")[1] == "Field: "
        else:
            assert speech.substitute("Ignored") == speech.lines
    assert dialogue.farewell(99) == dialogue.omen
    assert mog.hunk_data(4) == before


@pytest.mark.parametrize("message", [-1, 4])
def test_message_indices_and_stat_identity_are_checked(message: int) -> None:
    dialogue = _read_dialogue(synthetic_mog())
    with pytest.raises(ValueError, match="outside decoded table"):
        dialogue.gift(GiftResult(GiftKind.ITEM, message))
    with pytest.raises(ValueError, match="outside decoded table"):
        dialogue.gift(GiftResult(GiftKind.STAT, -1))
    with pytest.raises(ValueError, match="disagree"):
        dialogue.gift(GiftResult(GiftKind.STAT, 0, stat=72))


@pytest.mark.parametrize(
    ("hunk", "pos", "value", "error"),
    [
        (0, 0x993A, b"\0\x07", "cycle extents"),
        (0, 0x98F8, b"\0\x02", "cycle increment"),
        (0, 0x98FA, struct.pack(">I", 24), "cycle cells"),
        (0, 0x990A, b"\0\x01", "reset to zero"),
        (0, 0x990C, struct.pack(">I", 24), "reset uses another"),
        (0, 0x99F8, struct.pack(">I", 9), "stat table extent"),
        (4, 128, struct.pack(">I", 69), "not a knight stat"),
        (0, 0x9922, struct.pack(">I", 48), "stores disagree"),
        (0, 0x9A5A, struct.pack(">I", 2000), "field pointers disagree"),
        (0, 0x97EA, struct.pack(">I", 48), "kind cells disagree"),
        (0, 0x9808, b"\0\x05", "kinds are incomplete"),
        (0, 0x9602, b"\0\0", "wizard greeting"),
    ],
)
def test_malformed_instruction_pairs_and_tables_raise(
    hunk: int, pos: int, value: bytes, error: str
) -> None:
    with pytest.raises(MogVersionError, match=error):
        _read_dialogue(changed(synthetic_mog(), hunk, pos, value))


def test_counted_speech_rejects_bad_pointers_count_encoding_and_missing_fields() -> None:
    mog = synthetic_mog()
    for pointer in (Pointer(0, 1600), Pointer(4, -1), Pointer(4, 4096)):
        with pytest.raises(MogVersionError, match="outside data hunk"):
            _speech(mog, pointer)
    with pytest.raises(MogVersionError, match=r"empty.*count"):
        _speech(changed(mog, 4, 1600, b"\0"), Pointer(4, 1600))
    with pytest.raises(MogVersionError, match="non-ASCII"):
        _speech(changed(mog, 4, 1601, b"\xff"), Pointer(4, 1600))
    with pytest.raises(MogVersionError, match="unterminated"):
        _speech(changed(mog, 4, 4094, b"\1X"), Pointer(4, 4094))
    with pytest.raises(MogVersionError, match="field outside"):
        _speech(mog, Pointer(4, 1600), Pointer(4, 1700))
    empty = changed(mog, 4, 4093, b"\2\0\0")
    assert _speech(empty, Pointer(4, 4093)).lines == ("", "")
