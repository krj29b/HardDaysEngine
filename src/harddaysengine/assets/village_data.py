"""Checked village reward and shared temple operands (ARCHITECTURE.md §12.37)."""

from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.hunk import DecodedHunkExecutable
from harddaysengine.assets.mog_code import MogCode, Pointer
from harddaysengine.assets.stonehenge_data import _fade
from harddaysengine.assets.town_data import _call
from harddaysengine.sim.village import VillageRules


def _read_rules(mog: MogCode) -> VillageRules:
    cap = mog.operand(0x18B6, 0x0C28, "village life cap", displacement=73)
    mog.expect(0x18BC, (0x6C06,), "village signed cap branch")
    gain = mog.operand(0x18BE, 0x0628, "village life gain", displacement=73)
    sheet = mog.expect(0x18C4, (0x7009,), "village temple sheet") & 255
    _call(mog, 0x18C6, 0xABEC)
    mog.expect(0x18CC, (0x33F9,), "village spent assignment")
    if mog.pointer(0x18CE, "village budget") != Pointer(1, 0x1B1C):
        raise MogVersionError("mog: unexpected village budget cell")
    if mog.pointer(0x18D2, "village spent") != Pointer(1, 0x1AF0):
        raise MogVersionError("mog: unexpected village spent cell")
    if mog.call_target(0x18D6, "village map setup") != Pointer(36, 0):
        raise MogVersionError("mog: unexpected village map setup")
    mog.expect(0x18DC, (0x4E75,), "village return")
    if cap > 255 or gain > 255:
        raise MogVersionError("mog: village life immediate outside byte")
    period, wait = _fade(mog)
    return VillageRules(cap, gain, sheet, period, wait)


def parse_village_rules(executable: DecodedHunkExecutable) -> VillageRules:
    """Read the supported executable instead of embedding its reward (§12.37)."""
    return _read_rules(MogCode(executable))
