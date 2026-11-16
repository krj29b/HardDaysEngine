"""The contact pass: attack lists against hurt lists (ARCHITECTURE.md §10.10, §10.17)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pymoonstone.sim.m68k import s16, u16

if TYPE_CHECKING:
    from collections.abc import Callable

    from pymoonstone.sim.fight_script import FrameShape, HitShape, Job, ListEntry, ScriptEngine

DEPTH_REACH = 10


@dataclass(slots=True, frozen=True)
class Contact:
    """A hit found by the contact test: the contact point (16-bit screen words)."""

    x: int
    y: int


def overlap(a0: int, a1: int, b0: int, b1: int) -> bool:
    """The original's interval test of ``[a0, a1]`` and ``[b0, b1]`` (§10.17).

    ``b`` ending where ``a`` starts counts; ``b`` starting where ``a`` ends does not.
    """
    if b0 < a0:
        return b1 >= a0
    if b0 < a1:
        return True
    if b1 < a0:
        return False
    return b1 < a1


def depth_close(depth_a: int, depth_b: int) -> bool:
    """``|a - b| <= 10`` with a 16-bit difference and a signed compare (§10.17)."""
    diff = u16(depth_a - depth_b)
    if diff & 0x8000:
        diff = u16(-diff)
    return s16(diff) <= DEPTH_REACH


def _hit_record(engine: ScriptEngine, attack: ListEntry) -> HitShape | None:
    """The attacking frame's hit record, or ``None`` when it cannot hit (§10.17 step 1)."""
    hits = engine.cels[attack.cel].hits
    if hits is None or attack.frame >= len(hits):
        return None
    record = hits[attack.frame]
    return record if record is not None and record.points else None


def _coarse(
    attack: ListEntry, hurt: ListEntry, defender: FrameShape, record: HitShape, mirror: int
) -> bool:
    """The two interval tests of §10.17 step 2."""
    x0 = u16(attack.x + mirror - record.max_dx) if mirror else attack.x
    if not overlap(hurt.x, u16(hurt.x + defender.width), x0, u16(x0 + record.max_dx)):
        return False
    return overlap(hurt.y, u16(hurt.y + defender.height), attack.y, u16(attack.y + record.max_dy))


def contact_test(engine: ScriptEngine, attack: ListEntry, hurt: ListEntry) -> Contact | None:
    """The contact test of §10.17 for one attack entry against one hurt entry."""
    record = _hit_record(engine, attack)
    if record is None:
        return None
    defender = engine.cels[hurt.cel].frames[hurt.frame]
    mirror = 0
    if (attack.cel, attack.frame) in engine.mirrored:
        mirror = engine.cels[attack.cel].frames[attack.frame].width
    if not _coarse(attack, hurt, defender, record, mirror):
        return None
    defender_mirrored = (hurt.cel, hurt.frame) in engine.mirrored
    for px, py in record.points:
        gate_x = u16(attack.x + mirror - px) if mirror else u16(attack.x + px)
        cy = u16(attack.y + py)
        if not _inside(gate_x, hurt.x, defender.width) or not _inside(cy, hurt.y, defender.height):
            continue
        cx = u16(attack.x + px)
        if defender.opaque(u16(cx - hurt.x), u16(cy - hurt.y), mirrored=defender_mirrored):
            return Contact(cx, cy)
    return None


def _inside(value: int, start: int, size: int) -> bool:
    """``start <= value`` and ``value - start < size``, as signed words (§10.17)."""
    return s16(value) >= s16(start) and s16(u16(value - start)) < s16(size)


def _first_contact(engine: ScriptEngine, attacker: Job) -> tuple[Job, Contact] | None:
    """The first hurt entry of another job in reach that one of ``attacker``'s entries hits."""
    for attack in engine.attack.read(attacker.list_base):
        for victim in engine.jobs:
            if victim is attacker or not victim.active:
                continue
            if not depth_close(victim.depth, attacker.depth):
                continue
            for hurt in engine.hurt.read(victim.list_base):
                contact = contact_test(engine, attack, hurt)
                if contact is not None:
                    return victim, contact
    return None


def contact_scan(engine: ScriptEngine, clear_links: Callable[[], None]) -> None:
    """The contact pass: links, contact points, then empty lists (§10.10, §10.17)."""
    clear_links()
    for attacker in engine.jobs:
        if not attacker.active:
            continue
        found = _first_contact(engine, attacker)
        if found is None:
            continue
        victim, contact = found
        engine.owner(attacker).hit_target = victim.owner
        victim_actor = engine.owner(victim)
        victim_actor.attacker = attacker.owner
        victim_actor.hit_x, victim_actor.hit_y = contact.x, contact.y
    engine.attack.clear()
    engine.hurt.clear()
