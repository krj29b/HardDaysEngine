"""Byte-addressed access to the actor record (ARCHITECTURE.md §9.2, §10.17)."""

from __future__ import annotations

import pytest

from harddaysengine.sim.actor import ACTOR_SIZE, Actor, RecordAccessError


def test_reads_and_writes_follow_the_big_endian_layout() -> None:
    actor = Actor(hp=0x1234, idle_script=0x0102_0304, daggers=7)
    assert actor.read(80, 2) == 0x1234
    assert actor.read(81, 1) == 0x34
    assert actor.read(22, 4) == 0x0102_0304
    assert actor.read(76, 1) == 7
    actor.write(22, 4, 18394)
    assert actor.idle_script == 18394


def test_a_byte_write_inside_a_word_changes_only_that_byte() -> None:
    actor = Actor(input=0xABCD)
    actor.write(63, 1, 0x12)
    assert actor.input == 0xAB12
    actor.write(62, 1, 0xFF)
    assert actor.input == 0xFF12


def test_a_word_access_can_span_two_fields() -> None:
    actor = Actor(anim_phase=0x11, anim_timer=0x22)
    assert actor.read(12, 2) == 0x1122
    actor.write(76, 2, 0x0A24)
    assert (actor.daggers, actor.type) == (0x0A, 0x24)


def test_a_long_write_keeps_only_the_low_bytes_of_a_short_field() -> None:
    actor = Actor()
    actor.write(10, 4, 0x0102_0304)
    assert (actor.facing, actor.input_port, actor.anim_phase, actor.anim_timer) == (1, 2, 3, 4)


def test_access_outside_the_record_is_an_error() -> None:
    actor = Actor()
    with pytest.raises(RecordAccessError):
        actor.read(ACTOR_SIZE - 1, 2)
    with pytest.raises(RecordAccessError):
        actor.write(-2, 2, 0)


def test_clear_zeroes_every_field() -> None:
    actor = Actor(active=1, hp=20, map_x=5, spare131=9)
    actor.clear()
    assert all(actor.read(offset, 1) == 0 for offset in range(ACTOR_SIZE))
