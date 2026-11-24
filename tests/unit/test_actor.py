"""Actor domain helpers and byte-addressed VM field adapter (ARCHITECTURE.md §9.2, §10.17)."""

from __future__ import annotations

import pytest

from harddaysengine.sim.actor import ALL_STATS, Actor, StatKind
from harddaysengine.sim.script_adapter import ACTOR_SIZE, RecordAccessError, ScriptFieldAdapter


def test_typed_stat_and_cooldown_helpers() -> None:
    actor = Actor(strength=1, constitution=2, endurance=3, cooldown=0x045A)
    assert [actor.get_stat(stat) for stat in ALL_STATS] == [1, 2, 3]
    actor.set_stat(StatKind.STRENGTH, 4)
    assert actor.strength == 4
    assert actor.add_stat(StatKind.CONSTITUTION, 2) == 4
    assert actor.add_stat(StatKind.ENDURANCE, -1) == 2
    assert actor.cooldown_byte() == 0x04
    actor.set_cooldown_byte(0x09)
    assert actor.cooldown == 0x095A


def test_reads_and_writes_follow_the_big_endian_layout() -> None:
    actor = Actor(hp=0x1234, idle_script=0x0102_0304, daggers=7)
    assert ScriptFieldAdapter.read(actor, 80, 2) == 0x1234
    assert ScriptFieldAdapter.read(actor, 81, 1) == 0x34
    assert ScriptFieldAdapter.read(actor, 22, 4) == 0x0102_0304
    assert ScriptFieldAdapter.read(actor, 76, 1) == 7
    ScriptFieldAdapter.write(actor, 22, 4, 18394)
    assert actor.idle_script == 18394


def test_a_byte_write_inside_a_word_changes_only_that_byte() -> None:
    actor = Actor(input=0xABCD)
    ScriptFieldAdapter.write(actor, 63, 1, 0x12)
    assert actor.input == 0xAB12
    ScriptFieldAdapter.write(actor, 62, 1, 0xFF)
    assert actor.input == 0xFF12


def test_a_word_access_can_span_two_fields() -> None:
    actor = Actor(anim_phase=0x11, anim_timer=0x22)
    assert ScriptFieldAdapter.read(actor, 12, 2) == 0x1122
    ScriptFieldAdapter.write(actor, 76, 2, 0x0A24)
    assert (actor.daggers, actor.type) == (0x0A, 0x24)


def test_a_long_write_keeps_only_the_low_bytes_of_a_short_field() -> None:
    actor = Actor()
    ScriptFieldAdapter.write(actor, 10, 4, 0x0102_0304)
    assert (actor.facing, actor.input_port, actor.anim_phase, actor.anim_timer) == (1, 2, 3, 4)


def test_padding_bytes_read_zero_and_ignore_writes() -> None:
    actor = Actor()
    for offset in (87, 105, 131):
        ScriptFieldAdapter.write(actor, offset, 1, 0xAA)
        assert ScriptFieldAdapter.read(actor, offset, 1) == 0
    ScriptFieldAdapter.write(actor, 104, 1, 0x80)
    assert actor.block_latched and ScriptFieldAdapter.read(actor, 104, 1) == 0x80
    ScriptFieldAdapter.write(actor, 104, 1, 0x7F)
    assert not actor.block_latched and ScriptFieldAdapter.read(actor, 104, 1) == 0


def test_access_outside_the_record_is_an_error() -> None:
    actor = Actor()
    with pytest.raises(RecordAccessError):
        ScriptFieldAdapter.read(actor, ACTOR_SIZE - 1, 2)
    with pytest.raises(RecordAccessError):
        ScriptFieldAdapter.write(actor, -2, 2, 0)


def test_clear_zeroes_every_field() -> None:
    actor = Actor(active=1, hp=20, map_x=5, life_loss=1)
    actor.clear()
    assert all(ScriptFieldAdapter.read(actor, offset, 1) == 0 for offset in range(ACTOR_SIZE))
