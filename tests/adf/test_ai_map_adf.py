"""Independent original AI operands, draw trace and multi-day replay (§11.20)."""

from __future__ import annotations

import struct
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from pymoonstone.app.game import GameScene, GameSession
from pymoonstone.app.input import InputFrame, PlayerInput
from pymoonstone.assets.adf import DiskSet
from pymoonstone.assets.ai_map_rules import parse_ai_map_rules
from pymoonstone.assets.fight_palette import MogVersionError
from pymoonstone.assets.hunk import parse_hunk_executable
from pymoonstone.sim.ai_map import computer_frame
from pymoonstone.sim.ai_shop import DAGGERS, SWORD, purchase, shop_wish
from pymoonstone.sim.m68k import s16
from pymoonstone.sim.overworld import scan
from pymoonstone.sim.turns import TurnResult, advance
from pymoonstone.sim.types import KnightId

if TYPE_CHECKING:
    from pathlib import Path

    from pymoonstone.sim.ai_map_state import MapAiRules

pytestmark = pytest.mark.adf


def _session(adf_dir: Path) -> GameSession:
    return GameSession(
        disk_set=DiskSet.from_directory(adf_dir),
        start_scene=GameScene.OVERWORLD,
        players=(KnightId.RED,),
    )


def _rules(session: GameSession) -> MapAiRules:
    rules = session.overworld.ai_rules
    assert rules is not None
    return rules


def test_rules_match_independent_executable_words_and_longs(adf_dir: Path) -> None:
    session = _session(adf_dir)
    rules = _rules(session)
    executable = parse_hunk_executable("mog", DiskSet.from_directory(adf_dir).read_file("mog"))
    data = executable.sections[36].data

    def word(offset: int) -> int:
        return int.from_bytes(data[offset : offset + 2], "big")

    def long(offset: int) -> int:
        return int.from_bytes(data[offset : offset + 4], "big")

    assert rules.armour == tuple(
        (word(price), long(item))
        for price, item in ((0xFC2, 0xFD2), (0xFF4, 0x1004), (0x1026, 0x1036))
    )
    assert rules.claymore == (word(0x1058), long(0x1068))
    assert rules.broad == (word(0x1084), long(0x107A))
    assert rules.life_price == word(0xF9A) == word(0xF88)
    assert rules.dagger_price == word(0x10AA) == word(0x118A) == word(0x119A)
    assert rules.engage == word(0xAD4)
    assert rules.dagger_cap == word(0x1192)
    stat_pointer = long(0xF3E)
    assert (
        rules.stat_price == struct.unpack_from(">H", executable.sections[4].data, stat_pointer)[0]
    )
    assert rules.towns == tuple(
        (word(x), word(y), word(target), word(target + 2))
        for x, y, target in ((0x10D6, 0x10DA, 0x110A), (0x10F0, 0x10F4, 0x1116))
    )
    code = executable.sections[0].data
    assert rules.rest_cap == int.from_bytes(code[0xBEB0:0xBEB2], "big")


@pytest.mark.parametrize("offset", [0xAD2, 0xF3C, 0xFC0, 0x1066, 0x10D4, 0x1114, 0x1190])
def test_changed_ai_instruction_is_rejected(adf_dir: Path, offset: int) -> None:
    executable = parse_hunk_executable("mog", DiskSet.from_directory(adf_dir).read_file("mog"))
    sections = list(executable.sections)
    data = bytearray(sections[36].data)
    data[offset : offset + 2] = bytes(2)
    sections[36] = replace(sections[36], data=bytes(data))
    with pytest.raises(MogVersionError, match=r"instruction|expected"):
        parse_ai_map_rules(replace(executable, sections=tuple(sections)))


def _draw(state: int) -> int:
    """Independent bit recurrence, avoiding the simulation PRNG implementation."""
    for _ in range(8):
        feedback = ((state >> 4) ^ (state >> 1)) & 1
        state = (state >> 1) | (feedback << 31)
    return state


def test_original_frame_draw_order_matches_independent_oracle_across_turns(adf_dir: Path) -> None:
    session = _session(adf_dir)
    world = session.overworld
    assert world.turn_rules is not None
    count = 0
    for _ in range(16):
        result = advance(world, world.turn_rules)
        if result == TurnResult.NEXT_DAY:
            advance(world, world.turn_rules)
        if world.current_actor.kind != 4:
            continue
        world.enter()
        for _ in range(50):
            actor = world.current_actor
            state = world.prng.state
            state = _draw(state)
            while not state & 3:
                state = _draw(state)
            # No progress is earned in this direct map-only fixture.
            assert actor.progress < _rules(session).stat_price
            if not actor.engaged_with:
                # Three peers, only the one human may draw; then the null row.
                state = _draw(state)
                if state & 127 > _rules(session).engage:
                    state = _draw(state)
            computer_frame(world, _rules(session))
            assert world.prng.state == state
            count += 1
            if world.turn_over or world.ai.pending:
                break
    assert count > 200 and world.day >= 1


def test_real_shopping_preserves_sword_fallthrough_and_high_byte_debit(adf_dir: Path) -> None:
    session = _session(adf_dir)
    world, rules = session.overworld, _rules(session)
    actor = world.actors[1]
    actor.gold, actor.lives, actor.armour, actor.sword, actor.daggers = 25, 4, 30, 22, 9
    assert shop_wish(actor, world.ai.wish, rules)
    assert world.ai.wish.kind == SWORD and world.ai.wish.cost == 10
    purchase(actor, world.inventories[actor.inventory], world.ai.wish, rules)
    assert actor.gold == 15 and actor.sword == 24
    actor.sword, actor.gold, actor.daggers = 24, 30, 5
    assert shop_wish(actor, world.ai.wish, rules)
    assert world.ai.wish.kind == DAGGERS
    purchase(actor, world.inventories[actor.inventory], world.ai.wish, rules)
    assert actor.daggers == 7 and s16(actor.gold) == -484


def _multi_day(adf_dir: Path) -> tuple[list[str], set[int], int]:
    session = _session(adf_dir)
    world = session.overworld
    # Keep the stationary human alive through natural computer/Dragon encounters.
    world.actors[0].lives = 60
    session._sync_legacy_actor(KnightId.RED)
    hashes, computers, rounds = [], set(), 0
    previous = session.scene
    for tick in range(20000):
        if session.scene == GameScene.OVERWORLD and world.current_actor.kind == 4:
            computers.add(int(world.current_knight_id))
        fire = session.scene == GameScene.NEW_DAY and tick % 4 in (1, 2)
        frame = InputFrame(key=ord("E"), players={KnightId.RED: PlayerInput(fire=fire)})
        session.step_tick(frame)
        if session.scene == GameScene.NEW_DAY and previous != GameScene.NEW_DAY:
            rounds += 1
        previous = session.scene
        if tick % 50 == 0:
            hashes.append(session.state_hash())
        if world.day >= 3:
            hashes.append(session.state_hash())
            break
    assert world.day >= 3 and rounds >= 12
    return hashes, computers, world.prng.state


def test_seeded_natural_computer_turns_run_multiple_days_and_replay(adf_dir: Path) -> None:
    first = _multi_day(adf_dir)
    assert first[1] == {1, 2, 3}
    assert first == _multi_day(adf_dir)


def test_computer_arrival_enters_original_duel_and_resumes_without_redrawing(adf_dir: Path) -> None:
    session = _session(adf_dir)
    world = session.overworld
    assert world.turn_rules is not None
    advance(world, world.turn_rules)
    actor = world.current_actor
    human = world.actors[0]
    actor.map_x, actor.map_y = human.map_x, human.map_y
    actor.engaged_with, actor.gold, actor.progress = 1, 0, 3
    session._reenter_map()
    scan(world)
    session.step_tick(InputFrame())
    session.step_tick(InputFrame())
    active = session.active_encounter
    assert session.scene == GameScene.COMBAT and active is not None
    assert active.runtime.first == int(world.current_knight_id) + 1
    assert active.runtime.opponent == 1 and world.ai.pending
    assert active.runtime.fight.actor(active.runtime.first).kind == 4
    assert actor.progress == 0 and actor.strength + actor.constitution + actor.endurance == 4
    # Caller-boundary fixture: natural duel outcome coverage lives in test_computer_knight_adf.
    human.hp = 0
    gold, lives = actor.gold, human.lives
    human.gold = 30
    session._finish_combat(active)
    assert human.lives == lives - 1 and actor.progress == 1 and actor.gold == gold + 15
    rng = world.prng.state
    session.step_tick(InputFrame())
    session.step_tick(InputFrame())
    assert world.prng.state == rng and not world.ai.pending


@pytest.mark.parametrize("frog", [False, True])
def test_dead_or_frogged_opponent_settles_without_duel(adf_dir: Path, frog: bool) -> None:
    session = _session(adf_dir)
    world = session.overworld
    assert world.turn_rules is not None
    advance(world, world.turn_rules)
    actor, human = world.current_actor, world.actors[0]
    actor.map_x, actor.map_y = human.map_x, human.map_y
    actor.engaged_with, actor.gold = 1, 0
    human.frog_days, human.lives, human.gold = int(frog), int(frog), 40
    session._reenter_map()
    session.step_tick(InputFrame())
    session.step_tick(InputFrame())
    assert session.scene == GameScene.OVERWORLD and session.active_encounter is None
    assert actor.progress == 1 and actor.gold == (20 if frog else 0)
    assert world.ai.pending
