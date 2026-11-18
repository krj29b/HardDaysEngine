"""Checked Valley operands, original call identities and synthetic data (§12.38)."""

import struct
from unittest.mock import patch

import pytest

from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.valley_data import _read_data, parse_valley_data, valley_arena
from tests.unit.valley_setup import synthetic_valley
from tests.unit.wizard_setup import changed


def test_decodes_invented_rules_texts_palette_and_default_wall() -> None:
    mog = synthetic_valley()
    with patch("harddaysengine.assets.valley_data.MogCode", return_value=mog):
        data = parse_valley_data(mog.executable)
    rules = data.rules
    assert (rules.keys, rules.life_loss, rules.stat_floor, rules.stat_loss) == (31, 4, 2, 3)
    assert (rules.progress, rules.moon_mask, rules.fade_wait) == (9, 7, 13)
    assert data.missing[0].text == "Invented keys notice"
    assert data.victory[0].text == "Invented victory notice"
    assert data.recolor == tuple(range(10, 16)) and data.style == 8
    assert len(data.loading.messages) == 14 and data.loading.font == "bold"
    stage = valley_arena(mog)
    assert stage.picture == 5
    assert [(w.x1, w.x2, w.y, w.unused) for w in stage.obstacles] == [(-5, 280, 80, 0)]


@pytest.mark.parametrize(
    "hunk,pos,value,error",
    [
        (0, 0x16CA, b"\x0c\x68", "Valley keys"),
        (0, 0x16CE, b"\0\x16", "displacement"),
        (0, 0x16D0, b"\x66\x14", "exact key match"),
        (0, 0x16D4, struct.pack(">I", 0xFFFF), "outside its data hunk"),
        (0, 0x16EE, struct.pack(">I", 0), "Valley call"),
        (0, 0x171C, b"\1\0", "outside byte"),
        (0, 0x1726, b"\x66\x06", "floor branch"),
        (0, 0x173C, b"\0\x49", "displacement"),
        (0, 0x175A, b"\x70\x09", "reward wait"),
        (0, 0x9352, b"\0\4", "bold-font style"),
        (0, 0x3020, b"\x10\0", "RGB12"),
        (36, 0x672, b"\0\1", "moonstone mask"),
        (36, 0x674, b"\0\x08", "moonstone mask"),
        (36, 0x682, b"\0\x14", "moonstone field"),
        (36, 0x66C, struct.pack(">I", 0), "Valley call"),
        (36, 0x65E, b"\0\0", "call instruction"),
        (36, 0x684, b"\0\0", "reward return"),
        (0, 0x16E4, b"\0\0", "continuation target"),
    ],
)
def test_malformed_caller_data_is_rejected(hunk: int, pos: int, value: bytes, error: str) -> None:
    with pytest.raises(MogVersionError, match=error):
        _read_data(changed(synthetic_valley(), hunk, pos, value))


@pytest.mark.parametrize(
    "hunk,pos,value,error",
    [
        (0, 0x3228, b"\0\x24", "packed picture slot"),
        (0, 0x4930, struct.pack(">I", 0), "Valley call"),
        (12, 0x3DC, struct.pack(">I", 2), "obstacle table"),
        (12, 0x3E2, b"\0\2", "obstacle count"),
        (12, 0x3E8, b"\0\0", "default obstacle"),
        (12, 0x3F6, b"\0\x51", "floor disagree"),
        (12, 0x3F8, struct.pack(">I", 80), "floor disagree"),
    ],
)
def test_unknown_stage_setup_is_rejected(hunk: int, pos: int, value: bytes, error: str) -> None:
    with pytest.raises(MogVersionError, match=error):
        valley_arena(changed(synthetic_valley(), hunk, pos, value))
