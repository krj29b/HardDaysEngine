"""Dice house rounds, thrower handler and roll with invented data (ARCHITECTURE.md §12.32)."""

from __future__ import annotations

import struct

import pytest

from harddaysengine.sim.actor import Actor
from harddaysengine.sim.dice_house import (
    DiceEngine,
    DiceHouse,
    DiceLine,
    DicePhase,
    DiceRegion,
    DiceRun,
    DiceWorld,
    Thrower,
    can_enter,
    open_dice,
    roll_lines,
    step,
)
from harddaysengine.sim.fight import FightConstants
from harddaysengine.sim.fight_script import CelShape, FrameShape, ScriptImage
from harddaysengine.sim.rng import Rng
from harddaysengine.sim.service_types import DiceRules
from harddaysengine.sim.services import dice_roll
from harddaysengine.sim.sound import (
    DrawPick,
    Emit,
    SoundCells,
    SoundKind,
    SoundRequest,
    SoundRules,
)
from harddaysengine.sim.turn_rules import TextLine

IDLE, THROW = 2, 12


def _draw(frame: int) -> bytes:
    return struct.pack(">BBbBh", 0, frame, 0, 0, 0)


# Idle: one frame then end. Throw: a frame, the sound routine, three more frames, end.
IMAGE = ScriptImage(
    bytes(2) + _draw(0) + b"\xff\xff" + _draw(1) + b"\xff\x00"
    + struct.pack(">BBI", 0xB0, 0, 0) + _draw(2) + b"\xff\x00" + _draw(3) + b"\xff\xff",
    {THROW + 8: "dice_sound"},
)  # fmt: skip
SOLID = FrameShape(4, 4, (0xF,) * 4)
FIXED = SoundRequest(SoundKind.FIXED, 77, 3)
ENGINE = DiceEngine(
    IMAGE,
    {"d.cel": CelShape("d.cel", (SOLID,) * 4, None)},
    FightConstants(end_delay=1, dead_hp=0xFFFF),
    SoundRules((("dice_sound", (DrawPick(1, ((), ())), Emit(FIXED))),), SoundCells()),
)
LEAVE = DiceRegion(100, 100, 10, 10, 2, 9)
HOUSE = DiceHouse(
    tavern="t",
    table="d",
    cel_file="d.cel",
    regions=(DiceRegion(0, 0, 10, 10, 1, 3), DiceRegion(20, 0, 10, 10, 1, 50), LEAVE),
    ramp_entry=6,
    ramps=((3, (1, 2, 3, 4, 5)),),
    sounds=((60, 0), (61, 2)),
    pointer_x=280,
    thrower=Thrower(160, 0, 100, 1, 0x44, IDLE, THROW, 2, 3, 4, 1, 2),
    gold_at=(1, 2, 2),
    dice_at=((0, 0), (1, 1), (2, 2)),
    lost=(DiceLine("lost ", 0, 0, 0, "bet"), DiceLine("now ", 0, 9, 0, "gold")),
    won=(
        DiceLine("won ", 0, 0, 0, "won"),
        DiceLine("now ", 0, 9, 0, "gold"),
        DiceLine("go", 0, 18, 0),
    ),
    suffix=" gp.",
    roll_wait=4,
)  # fmt: skip
RULES = DiceRules(7, 5, ((0, 0, 0, 2), (1, 2, 3, 9)))


def _visit(gold: int = 10, seed: int = 5) -> tuple[DiceRun, DiceWorld]:
    world = DiceWorld(Actor(gold=gold, kind=3), Rng(seed), RULES)
    return open_dice(HOUSE, ENGINE, 1, 77), world


def _at(run: DiceRun, region: DiceRegion) -> None:
    run.state.pointer_x, run.state.pointer_y = region.x, region.y


def _tick(run: DiceRun, world: DiceWorld, fire: bool = False) -> None:
    step(run, world, (0, 0, fire))


def test_entry_needs_positive_signed_gold_and_starts_fixed_sounds() -> None:
    assert not can_enter(Actor(gold=0)) and not can_enter(Actor(gold=0x8000))
    run, _ = _visit()
    assert run.state.sounds == [
        SoundRequest(SoundKind.FIXED, 60, 0),
        SoundRequest(SoundKind.FIXED, 61, 2),
    ]
    assert (run.state.pointer_x, run.state.pointer_y) == (280, 77) and HOUSE.ramp(9) == ()


def test_a_round_spawns_the_thrower_and_passes_follow_the_frame_wait() -> None:
    run, world = _visit()
    _tick(run, world)
    assert run.state.phase == DicePhase.PLAY and run.fight is not None
    job = run.fight.find_job(6)
    assert job is not None and job.type == 0x44 and (job.x, job.depth) == (160, 100)
    assert run.state.frame_wait == 2 and run.draws
    passes = []
    for _ in range(12):
        before = run.draws
        _tick(run, world)
        passes.append(run.draws is not before)
    # Spawn wait 2, then the idle wait 3 once the handler restarted the idle script.
    assert passes[:2] == [False, True] and passes[2:5] == [False, False, True]


def test_unaffordable_bets_idle_and_leave_ends_the_visit() -> None:
    run, world = _visit(gold=10)
    _tick(run, world)
    _at(run, HOUSE.regions[1])
    for _ in range(8):
        _tick(run, world, fire=True)
    assert run.state.throw == 0 and run.state.bet == 50 and world.player.gold == 10
    _at(run, LEAVE)
    for _ in range(8):
        _tick(run, world, fire=True)
    assert run.state.phase == DicePhase.DONE and world.player.gold == 10


@pytest.mark.parametrize("seed", [(i * 0x9E3779B1) & 0xFFFFFFFF for i in range(1, 30)])
def test_a_bet_throws_draws_in_order_and_rolls(seed: int) -> None:
    run, world = _visit(gold=10, seed=seed)
    _tick(run, world)
    _at(run, HOUSE.regions[0])
    sounds = []
    while run.state.throw == 0:
        _tick(run, world, fire=True)
        sounds.extend(run.state.sounds)
    assert world.player.gold == 7 and run.state.frame_wait == 4
    while run.state.phase == DicePhase.PLAY:
        _tick(run, world)
        sounds.extend(run.state.sounds)
    assert FIXED in sounds and run.fight is None and len(run.state.dice) == 3
    expected_gold = world.player.gold
    assert run.state.lines[1] == TextLine(f"now {expected_gold} gp.", 0, 9, 0)


def test_the_roll_matches_the_rules_after_the_script_draws() -> None:
    run, world = _visit(gold=10, seed=0x1234)
    _tick(run, world)
    _at(run, HOUSE.regions[0])
    while run.state.throw == 0:
        _tick(run, world, fire=True)
    while run.state.throw == 1:
        snapshot = (Actor(gold=world.player.gold), Rng(world.rng.state))
        _tick(run, world)
    twin, rng = snapshot
    expected = dice_roll(twin, 3, rng, RULES)
    assert run.state.dice == expected.dice and world.player.gold == twin.gold


def test_roll_lines_write_digits_and_suffix_after_each_prefix() -> None:
    lines = roll_lines(HOUSE, HOUSE.won, {"won": 1234, "gold": 5})
    assert [line.text for line in lines] == ["won 234 gp.", "now 5 gp.", "go"]


def test_after_the_roll_wait_a_fresh_press_starts_the_next_round() -> None:
    run, world = _visit(gold=10)
    _tick(run, world)
    _at(run, HOUSE.regions[0])
    while run.state.phase != DicePhase.ROLL_WAIT:
        _tick(run, world, fire=True)
    for _ in range(4):
        _tick(run, world, fire=True)
    assert run.state.phase == DicePhase.ROLL_FIRE
    _tick(run, world)
    _tick(run, world, fire=True)
    _tick(run, world)
    assert run.state.phase == DicePhase.ROUND
    _tick(run, world)
    assert run.state.phase == (DicePhase.PLAY if world.player.gold else DicePhase.DONE)


def test_a_fade_freezes_the_routine_but_moves_the_pointer() -> None:
    run, world = _visit()
    step(run, world, (1, 1, True), fading=True)
    assert run.state.phase == DicePhase.ROUND and (run.state.pointer_x, run.state.pointer_y) == (
        282,
        79,
    )


def test_unimplemented_script_routines_stop_the_visit() -> None:
    engine = DiceEngine(IMAGE, ENGINE.cels, ENGINE.constants, SoundRules((), SoundCells()))
    run = open_dice(HOUSE, engine, 1, 77)
    world = DiceWorld(Actor(gold=10), Rng(5), RULES)
    _tick(run, world)
    _at(run, HOUSE.regions[0])
    with pytest.raises(ValueError, match="dice_sound"):
        for _ in range(60):
            _tick(run, world, fire=True)


def test_snapshot_holds_state_and_thrower_records() -> None:
    run, world = _visit()
    _tick(run, world)
    snap = run.snapshot()
    assert set(snap) == {"state", "vbl", "jobs", "actors"} and snap["vbl"] == 1
