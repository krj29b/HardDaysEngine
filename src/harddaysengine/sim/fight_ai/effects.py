"""Ordinary job-backed creature hit effects (ARCHITECTURE.md §10.27)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from harddaysengine.sim.fight import idle_object

if TYPE_CHECKING:
    from harddaysengine.sim.actor import Actor
    from harddaysengine.sim.fight import Fight


@dataclass(slots=True, frozen=True)
class HitSparkRules:
    """Decoded script, frame list and type of the shared hit spark (§10.27)."""

    script: int
    frame_list: int
    kind: int


def hit_spark(fight: Fight, source: Actor, rules: HitSparkRules) -> None:
    """Spawn the original pool object at the contact point, facing like the first knight."""
    fight.handlers[rules.kind] = idle_object
    fight.spawn_actor(
        rules.script,
        rules.frame_list,
        (source.hit_x, 0, source.hit_y),
        fight.actor(fight.first).facing,
        rules.kind,
    )
