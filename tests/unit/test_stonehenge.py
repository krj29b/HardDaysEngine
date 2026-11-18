"""Synthetic Stonehenge mappings, blessing boundaries and persistent ritual timing (§12.34)."""

import struct
from dataclasses import replace

import pytest

from harddaysengine.sim.actor import Actor
from harddaysengine.sim.fight import FightConstants
from harddaysengine.sim.fight_script import CelShape, FrameShape, ScriptImage
from harddaysengine.sim.inventory import Inventory
from harddaysengine.sim.rng import Rng
from harddaysengine.sim.sound import SoundKind, SoundRequest
from harddaysengine.sim.stonehenge import (
    RitualEngine,
    RitualRules,
    RitualSpawn,
    StoneRules,
    StoneState,
    bless,
    ending_code,
    matches,
    open_ritual,
)

RITUAL = RitualRules(
    "p",
    "c",
    "a",
    (RitualSpawn(2, (50, 0, 80), 1, 40),),
    (),
    8,
    3,
    5,
    (SoundRequest(SoundKind.FIXED, 90, 2),),
    60,
    77,
)
RULES = StoneRules(
    ((3, 71), (0, 72)),
    ((71, 2), (72, 0)),
    ((7, 4), (9, 1)),
    128,
    4,
    2,
    6,
    2,
    9,
    "m",
    "f",
    (),
    (),
    (),
    (),
    "program",
    RITUAL,
)


def _engine(sound: str = "ritual_sound") -> RitualEngine:
    draw = struct.pack(">BBbBh", 0, 0, 0, 0, 0)
    # Three script frames separated by markers, then the ordinary handler marker.
    image = bytes(2) + draw + b"\xff\x00" + struct.pack(">BBI", 0xB0, 0, 0)
    image += draw + b"\xff\x00" + draw + b"\xff\xff"
    shape = CelShape("c", (FrameShape(4, 4, (0xF000,) * 4),), None)
    return RitualEngine(ScriptImage(image, {12: sound}), {"c": shape}, FightConstants(1, 65535))


@pytest.mark.parametrize(
    "moon,stones,result",
    ((71, 8, True), (71, 1, False), (72, 1, True), (72, 8, False), (70, 255, False), (71, 9, True)),
)
def test_match_uses_decoded_bits_and_phase(moon: int, stones: int, result: bool) -> None:
    assert matches(RULES, moon, stones) == result


def test_ending_code_combines_only_the_respective_phase_and_class_rows() -> None:
    assert ending_code(RULES, 71, 7) == 148
    assert ending_code(RULES, 72, 9) == 131
    assert ending_code(RULES, 9, 71) == 128  # crossing domains must not set either row


@pytest.mark.parametrize("lives,after", ((3, 5), (4, 4), (5, 7), (255, 1)))
def test_blessing_exact_cap_byte_wrap_and_equipment_hp(lives: int, after: int) -> None:
    actor = Actor(lives=lives, constitution=2, hp=1, hp_max=1, life_loss=1, frog_days=3)
    inv = Inventory()
    inv.counts[3] = 1
    bless(actor, inv, RULES)
    assert (actor.lives, actor.hp, actor.hp_max, actor.life_loss, actor.frog_days) == (
        after,
        50,
        50,
        0,
        3,
    )


def test_fire_wait_requires_press_then_release() -> None:
    state = StoneState(1, False, 0)
    assert not state.released(False)
    assert not state.released(True) and not state.released(True)
    assert state.released(False) and not state.released(False)


def test_ritual_has_two_initial_passes_then_decoded_intervals_and_exact_sound_draw() -> None:
    rng = Rng(0x12345678)
    oracle = Rng(rng.state)
    expected = oracle.percent() <= RITUAL.sound_limit
    run = open_ritual(RITUAL, _engine(), rng)
    run.advance(100)
    assert len(run.batches) == 2 and run.started and not run.done
    assert run.sounds[0] == RITUAL.sounds[0]
    assert run.sounds[1:] == ([SoundRequest(SoundKind.PLAY, 77)] if expected else [])
    assert rng.state == oracle.state
    script = run.fight.engine.jobs[0].script
    for tick in range(101, 105):
        run.advance(tick)
        assert not run.batches and run.fight.engine.jobs[0].script == script
    run.advance(105)
    assert len(run.batches) == 1 and not run.done
    before = run.fight.engine.vbl
    for tick in range(106, 111):
        run.advance(tick)
    assert run.done and not run.batches
    assert run.fight.engine.vbl == before  # stop handler prevents the final engine tick
    snapshot = run.snapshot()
    run.advance(200)
    assert run.snapshot() == snapshot


def test_ritual_rejects_unknown_callbacks_and_snapshots_persistent_work() -> None:
    run = open_ritual(RITUAL, _engine("unknown"), Rng(12))
    with pytest.raises(ValueError, match="unimplemented"):
        run.advance(0)
    run = open_ritual(replace(RITUAL, sound_limit=-1), _engine(), Rng(12))
    run.advance(0)
    assert run.sounds == list(RITUAL.sounds)
    snapshot = run.snapshot()
    run.fight.engine.jobs[0].work.loop_count = 17
    assert run.snapshot() != snapshot
