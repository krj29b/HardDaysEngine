"""Checked map music and sheet requests (ARCHITECTURE.md §6.3.2)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.mog_code import Pointer
from harddaysengine.sim.sound import SoundKind, SoundRequest

if TYPE_CHECKING:
    from harddaysengine.assets.mog_code import MogCode


@dataclass(slots=True, frozen=True)
class SceneSounds:
    """Runtime sound requests in original call order."""

    map_music: tuple[SoundRequest, ...]
    click: tuple[SoundRequest, ...]
    good: tuple[SoundRequest, ...]
    bad: tuple[SoundRequest, ...]
    fade_period: int
    fade_wait: int

    def sheet(self, events: list[str]) -> tuple[SoundRequest, ...]:
        """Translate sound events; buy/sell/coin are printed messages (§6.3.2)."""
        mapping = {
            "click": self.click,
            "good": self.good,
            "bad": self.bad,
            "buy": (),
            "sell": (),
            "coin": (),
        }
        return tuple(request for event in events for request in mapping[event])


def _play(mog: MogCode, pos: int) -> SoundRequest:
    value = mog.operand(pos, 0x303C, "scene sound sequence")
    if mog.call_target(pos + 4, "scene sound call") != Pointer(16, 0x88):
        raise MogVersionError("mog: scene sound does not call PLAY")
    return SoundRequest(SoundKind.PLAY, value)


def parse_scene_sounds(mog: MogCode) -> SceneSounds:
    """Decode the existing map and sheet sound operands (§6.3.2)."""
    music = []
    for pos in (0x2F20, 0x2F2E, 0x2F3C, 0x2F4A):
        sequence = mog.operand(pos, 0x303C, "map sequence")
        channel = mog.operand(pos + 4, 0x323C, "map channel")
        if mog.call_target(pos + 8, "map music") != Pointer(44, 0x698):
            raise MogVersionError("mog: map music is not a direct synth start")
        music.append(SoundRequest(SoundKind.START, sequence, channel))
    click = _play(mog, 0xC03A)
    if any(_play(mog, p) != click for p in (0xBD6E, 0xBE4C, 0xBE7E)):
        raise MogVersionError("mog: sheet click sounds disagree")
    mog.expect(0x8B4E, (0x7002,), "silent fade period MOVEQ")
    mog.expect(0x8B5E, (0x203C,), "silent fade wait")
    return SceneSounds(
        tuple(music),
        (click,),
        tuple(_play(mog, p) for p in (0xCEB4, 0xCEBE, 0xCEC8, 0xCED2)),
        tuple(_play(mog, p) for p in (0xCE8E, 0xCE98)),
        mog.word(0x8B4E) & 255,
        mog.long(0x8B60),
    )
