"""Synthetic checked service operands, relocation failures and malformed tables (§12.30)."""

import struct
import zlib

import pytest

from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.hunk import HUNK_CODE, DecodedHunkExecutable, HunkSection
from harddaysengine.assets.mog_code import MogCode
from harddaysengine.assets.service_data import _dice, _healer, _mystic, parse_service_rules


def _image() -> tuple[bytearray, bytearray, dict[int, tuple[int, ...]]]:
    code, table = bytearray(0xA800), bytearray(68)
    for pos, words in (
        (0x9ED4, (0xB07C, 7)),
        (0x9EFA, (0x0479, 7, 0, 4)),
        (0x9F16, (0x0479, 11, 0, 4)),
        (0x9F08, (0x0C28, 4, 73)),
        (0x9F24, (0xB27C, 6)),
        (0x9D92, (0x41F9, 0, 0x600, 0x7405, 0, 0, 0x5888, 0x51CA, 0xFFF8)),
        (0x9DAA, (0xB07C, 60)),
        (0x9CA8, (0x0669, 6, 80)),
        (0x9CE8, (0x0468, 6, 80)),
        (0x9CCC, (0x0C31, 1, 0x1800)),
        (0x9CF0, (0x317C, 2, 80)),
        (0xA7CA, (0x43F9, 0, 0, 0x7E0B)),
        (0xA7DA, (0xD3FC, 0, 6, 0x51CF, 0xFFF0)),
        (0xA730, (0x0240, 7)),
        (0xA734, (0xB03C, 5)),
    ):
        struct.pack_into(">" + "H" * len(words), code, pos, *words)
    for index in range(6):
        struct.pack_into(">hh", code, 0x600 + index * 4, index * 9, 17 - index * 8)
    for index in range(11):
        face = index if index < 6 else index - 5
        struct.pack_into(">6B", table, index * 6, 0, index // 6, face, 0, index + 2, 0)
    return code, table, {0: (0x9D94,), 1: (0x9EFE, 0x9F1A), 42: (0xA7CC,)}


def _mog(code: bytearray, table: bytearray, relocs: dict[int, tuple[int, ...]]) -> MogCode:
    sections = tuple(
        HunkSection(
            index=i,
            hunk_type=HUNK_CODE,
            alloc_size=len(code) if i == 0 else len(table),
            data=bytes(code) if i == 0 else bytes(table) if i == 42 else b"",
            relocs=relocs if i == 0 else {},
        )
        for i in range(43)
    )
    return MogCode(DecodedHunkExecutable("synthetic", sections), supported=(zlib.crc32(code),))


def test_synthetic_service_rules_use_altered_prices_bands_and_payouts() -> None:
    mog = _mog(*_image())
    healer, mystic, dice = _healer(mog), _mystic(mog), _dice(mog)
    assert (healer.heal_price, healer.life_price, healer.life_cap, healer.reply_limit) == (
        7,
        11,
        4,
        6,
    )
    assert mystic.bands == tuple((i * 9, 17 - i * 8) for i in range(6))
    assert (mystic.win_at, mystic.hp_step, mystic.hp_floor, mystic.stat_floor) == (60, 6, 2, 1)
    assert dice.rows[-1] == (0, 1, 5, 12)
    assert (dice.mask, dice.face_max) == (7, 5)
    with pytest.raises(MogVersionError, match="unsupported version"):
        parse_service_rules(mog.executable)


@pytest.mark.parametrize(
    ("pos", "value", "message"),
    (
        (0x9ED4, 0, "expected"),
        (0x9EFC, 8, "disagree"),
        (0x9F0C, 74, "displacement"),
        (0x9F18, 0, "nonprogressing"),
        (0x9F1C, 1, "different donation"),
    ),
)
def test_healer_rejects_malformed_operands(pos: int, value: int, message: str) -> None:
    code, table, relocs = _image()
    struct.pack_into(">H", code, pos, value)
    with pytest.raises(MogVersionError, match=message):
        _healer(_mog(code, table, relocs))


@pytest.mark.parametrize(
    ("pos", "value", "message"),
    (
        (0x9D98, 0, "row count"),
        (0x9D9E, 0, "advance"),
        (0x9DA2, 0, "back edge"),
        (0x604, 0, "not increasing"),
        (0x9CEA, 9, "disagree"),
        (0x9CCE, 256, "byte floor"),
    ),
)
def test_mystic_rejects_malformed_operands_and_bands(pos: int, value: int, message: str) -> None:
    code, table, relocs = _image()
    struct.pack_into(">H", code, pos, value)
    with pytest.raises(MogVersionError, match=message):
        _mystic(_mog(code, table, relocs))


@pytest.mark.parametrize(
    ("pos", "value", "message"),
    ((0xA7DE, 8, "stride"), (0xA7E2, 0, "back edge"), (0xA736, 128, "face range")),
)
def test_dice_rejects_malformed_operands(pos: int, value: int, message: str) -> None:
    code, table, relocs = _image()
    struct.pack_into(">H", code, pos, value)
    with pytest.raises(MogVersionError, match=message):
        _dice(_mog(code, table, relocs))


@pytest.mark.parametrize("kind", ("pad", "zero", "unsorted", "duplicate", "truncated"))
def test_dice_rejects_invalid_rows(kind: str) -> None:
    code, table, relocs = _image()
    if kind == "pad":
        table[3] = 1
    elif kind == "zero":
        table[4] = 0
    elif kind == "unsorted":
        table[0] = 5
    elif kind == "duplicate":
        table[6:12] = table[:6]
    else:
        table = table[:65]
    with pytest.raises(MogVersionError):
        _dice(_mog(code, table, relocs))


def test_table_relocations_must_name_the_expected_hunks() -> None:
    code, table, relocs = _image()
    relocs[4] = relocs.pop(0)
    with pytest.raises(MogVersionError, match="outside code"):
        _mystic(_mog(code, table, relocs))
    relocs[4] = relocs.pop(42)
    with pytest.raises(MogVersionError, match="outside their data"):
        _dice(_mog(code, table, relocs))
