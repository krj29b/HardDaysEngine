"""Checked original healer, mystic and dice data (ARCHITECTURE.md §12.30)."""

from __future__ import annotations

import struct
from itertools import pairwise
from typing import TYPE_CHECKING

from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.map_rules import _bytes
from harddaysengine.assets.mog_code import MogCode, Pointer
from harddaysengine.sim.m68k import s16
from harddaysengine.sim.service_types import DiceRules, HealerRules, MysticRules

if TYPE_CHECKING:
    from harddaysengine.assets.hunk import DecodedHunkExecutable


def _healer(mog: MogCode) -> HealerRules:
    heal = mog.operand(0x9ED4, 0xB07C, "healer minimum")
    if heal != mog.operand(0x9EFA, 0x0479, "healer HP debit"):
        raise MogVersionError("mog: healer minimum and HP debit disagree")
    if mog.pointer(0x9EFE, "healer HP donation") != mog.pointer(0x9F1A, "healer life donation"):
        raise MogVersionError("mog: healer debits name different donation cells")
    life = mog.operand(0x9F16, 0x0479, "healer life debit")
    cap = mog.operand(0x9F08, 0x0C28, "healer life cap", displacement=73)
    if min(s16(heal), s16(life)) <= 0 or cap > 255:
        raise MogVersionError("mog: nonprogressing healer prices or invalid byte cap")
    return HealerRules(heal, life, cap, mog.operand(0x9F24, 0xB27C, "healer reply limit"))


def _mystic(mog: MogCode) -> MysticRules:
    target = mog.lea(0x9D92, 0x41F9, "mystic bonus table")
    mog.expect(0x9D98, (0x7405,), "mystic bonus row count")
    mog.expect(0x9D9E, (0x5888,), "mystic table advance")
    mog.expect(0x9DA0, (0x51CA,), "mystic table DBRA")
    mog.expect(0x9DA2, (0xFFF8,), "mystic table back edge")
    if target.hunk != 0:
        raise MogVersionError("mog: mystic bonus table outside code hunk")
    count = (mog.word(0x9D98) & 255) + 1
    bands = tuple(struct.iter_unpack(">hh", _bytes(mog, target, count * 4)))
    if any(left[0] >= right[0] for left, right in pairwise(bands)):
        raise MogVersionError("mog: mystic donation bounds are not increasing")
    hp = mog.operand(0x9CA8, 0x0669, "mystic HP gain", displacement=80)
    if hp != mog.operand(0x9CE8, 0x0468, "mystic HP loss", displacement=80):
        raise MogVersionError("mog: mystic HP gain and loss disagree")
    floor = mog.operand(0x9CCC, 0x0C31, "mystic stat floor", displacement=0x1800)
    if floor > 255:
        raise MogVersionError("mog: invalid mystic byte floor")
    return MysticRules(
        bands,
        s16(mog.operand(0x9DAA, 0xB07C, "mystic win threshold")),
        hp,
        mog.operand(0x9CF0, 0x317C, "mystic HP floor", displacement=80),
        floor,
    )


def _dice_rows(
    mog: MogCode, target: Pointer, maximum: int, mask: int
) -> tuple[tuple[int, int, int, int], ...]:
    rows = []
    keys = set()
    for a, b, c, pad, multiplier, _ in struct.iter_unpack(
        ">6B", _bytes(mog, target, (mog.word(0xA7D0) & 255) * 6)
    ):
        key = (a, b, c)
        if pad or not multiplier or not 0 <= a <= b <= c <= maximum:
            raise MogVersionError("mog: invalid dice payout row")
        if key in keys or any(face & mask != face for face in key):
            raise MogVersionError("mog: duplicate or unreachable dice payout key")
        keys.add(key)
        rows.append((*key, multiplier))
    return tuple(rows)


def _dice(mog: MogCode) -> DiceRules:
    target = mog.lea(0xA7CA, 0x43F9, "dice payouts")
    mog.expect(0xA7D0, (0x7E0B,), "dice payout row count")
    mog.expect(0xA7DA, (0xD3FC,), "dice row advance")
    if mog.long(0xA7DC) != 6:
        raise MogVersionError("mog: unexpected dice payout stride")
    mog.expect(0xA7E0, (0x51CF,), "dice payout DBRA")
    mog.expect(0xA7E2, (0xFFF0,), "dice payout back edge")
    if target.hunk != 42:
        raise MogVersionError("mog: dice payouts outside their data hunk")
    mask = mog.operand(0xA730, 0x0240, "dice random mask")
    maximum = mog.operand(0xA734, 0xB03C, "dice face maximum")
    if not 0 <= maximum <= min(mask, 127):
        raise MogVersionError("mog: invalid dice face range")
    return DiceRules(mask, maximum, _dice_rows(mog, target, maximum, mask))


def parse_service_rules(
    executable: DecodedHunkExecutable,
) -> tuple[HealerRules, MysticRules, DiceRules]:
    """Read checked service operands and tables from the user's mog (§12.30)."""
    mog = MogCode(executable)
    return _healer(mog), _mystic(mog), _dice(mog)
