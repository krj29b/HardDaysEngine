"""Sound requests and picker steps with invented rules (ARCHITECTURE.md §10.32)."""

from __future__ import annotations

import struct

import pytest

from harddaysengine.sim.actor import Actor
from harddaysengine.sim.fight import KEEP, Fight, FightConstants, HandlerResult
from harddaysengine.sim.fight_script import RoutineCall, ScriptImage
from harddaysengine.sim.rng import Rng
from harddaysengine.sim.sound import (
    CounterStep,
    CycleStep,
    DrawPick,
    Emit,
    ListStep,
    SoundCells,
    SoundError,
    SoundKind,
    SoundRequest,
    SoundRules,
    SoundStep,
    install_sounds,
    play,
    run_steps,
)

FIXED = SoundRequest(SoundKind.FIXED, 9, 2)
RELEASE = SoundRequest(SoundKind.RELEASE, 200, 1, 0x0D)
# Two lists back to back, sharing one offset: A = 11, 12, end, end; B = 21, 22, 23, end.
WINDOW = (11, 12, 0xFFFF, 0xFFFF, 21, 22, 23, 0xFFFF)
LIST_A = ListStep(0, WINDOW, 2, 0xFFFF, 0)
LIST_B = ListStep(8, WINDOW, 2, 0xFFFF, 0)


def _run(
    steps: tuple[SoundStep, ...], cells: SoundCells, seed: int = 7
) -> tuple[list[SoundRequest], Rng]:
    rng, out = Rng(seed), list[SoundRequest]()
    run_steps(steps, rng, cells, out.append)
    return out, rng


def _draws(seed: int, count: int) -> list[int]:
    rng = Rng(seed)
    return [rng.draw() for _ in range(count)]


def test_play_requests_wrap_to_a_word() -> None:
    assert play(0x1_0005) == SoundRequest(SoundKind.PLAY, 5)


def test_constant_requests_keep_their_kind_order_and_take_no_draw() -> None:
    out, rng = _run((Emit(FIXED), Emit(RELEASE)), SoundCells())
    assert out == [FIXED, RELEASE] and rng.state == 7


def test_each_draw_pick_takes_one_draw_and_plays_its_choices_in_order() -> None:
    choices = ((30, 31), (40, 41), (50, 51), (60, 61))
    out, rng = _run((DrawPick(3, choices), DrawPick(3, choices)), SoundCells())
    first, second = _draws(7, 2)
    expected = [*choices[first & 3], *choices[second & 3]]
    assert out == [play(s) for s in expected] and rng.state == second


def test_cycle_wraps_at_a_signed_limit_without_drawing() -> None:
    cells = SoundCells(plain=2)
    step = CycleStep(step=1, limit=4, reset=0, base=50)
    ids = [_run((step,), cells)[0][0].sound for _ in range(4)]
    assert ids == [53, 50, 51, 52]
    negative = SoundCells(plain=0xFFFE)
    assert _run((step,), negative)[0] == [play(49)] and negative.plain == 0xFFFF


def test_list_cycles_restart_at_the_terminator() -> None:
    cells = SoundCells()
    ids = [_run((LIST_B,), cells)[0][0].sound for _ in range(4)]
    assert ids == [22, 23, 21, 22] and cells.list_offset == 2


def test_lists_share_one_offset_so_a_long_list_cuts_the_short_one() -> None:
    cells = SoundCells()
    for _ in range(2):
        _run((LIST_B,), cells)
    assert cells.list_offset == 4
    out, _ = _run((LIST_A,), cells)
    assert out == [play(11)] and cells.list_offset == 0


def test_a_list_read_outside_the_decoded_words_is_an_error() -> None:
    with pytest.raises(SoundError, match="outside"):
        _run((LIST_B,), SoundCells(list_offset=6))
    with pytest.raises(SoundError, match="outside"):
        _run((LIST_A,), SoundCells(list_offset=0xFFFC))


def test_counter_plays_only_when_the_masked_word_returns_to_zero() -> None:
    cells = SoundCells(counter=1)
    step = CounterStep(step=1, mask=3, sound=90)
    outs = [_run((step,), cells)[0] for _ in range(4)]
    assert outs == [[], [], [play(90)], []] and cells.counter == 1


def _fight(code: bytes, routines: dict[int, str]) -> Fight:
    knights = [Actor(hp=20, active=1) for _ in range(4)]
    return Fight(
        image=ScriptImage(bytes(2) + code, routines),
        cels={},
        frame_lists={1: (None,)},
        knights=knights,
        dragon=Actor(),
        constants=FightConstants(end_delay=35, dead_hp=0xFFFF),
    )


def test_a_frame_reports_handler_script_and_routine_sounds_in_execution_order() -> None:
    code = struct.pack(">BB", 0xA4, 5) + struct.pack(">BBI", 0xB0, 0, 0)
    code += struct.pack(">BB", 0xA4, 6) + b"\xff\xff"
    fight = _fight(code, {2 + 4: "sound_x"})
    rules = SoundRules((("sound_x", (Emit(FIXED), DrawPick(0, ((70,),)))),), SoundCells())
    install_sounds(fight, rules)

    def handler(f: Fight, _handle: int) -> HandlerResult:
        f.play_sound(1)
        return KEEP

    fight.handlers[0x0C] = handler
    fight.actor(1).hit_target = 2
    fight.first = 1
    fight.start_job(2, 1, 1, (40, 0, 90), 1, 0x0C)
    out = fight.step(vbl=0)
    assert out.sounds == [play(1), play(5), FIXED, play(70), play(6)]
    assert out.tick.sounds == [] and out.skipped_calls == []


def test_installed_routines_share_the_fight_cells_and_generator() -> None:
    fight = _fight(b"\xff\xff", {})
    cells = SoundCells(plain=3)
    fight.sound_cells = cells
    install_sounds(fight, SoundRules((("cycle", (CycleStep(1, 5, 0, 4),)),), SoundCells()))
    call = RoutineCall("cycle", 1, 1, 0, 0, 0, 1)
    fight.routines["cycle"](fight, call)
    fight.routines["cycle"](fight, call)
    assert fight.sounds == [play(8), play(4)] and cells.plain == 0


def test_rules_hand_out_a_fresh_copy_of_the_executable_cells() -> None:
    rules = SoundRules((), SoundCells(1, 2, 3))
    first = rules.initial_cells()
    first.plain = 9
    assert rules.initial_cells() == SoundCells(1, 2, 3)
