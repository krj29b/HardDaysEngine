"""The synth sequencer against hand-worked expectations from the spec (ARCHITECTURE.md §6.2)."""

from __future__ import annotations

import pytest

from harddaysengine.app.paula import SET, address
from harddaysengine.app.synth import RAW_REGION, WAVE_REGION, Synth, SynthError
from tests.unit.synth_setup import (
    ATTENUATION,
    PARK,
    REST,
    STACKS,
    START_PERIOD,
    STREAMS,
    FakePaula,
    synth_data,
)


def _synth(streams: dict[int, bytes], **kwargs: bytes) -> tuple[Synth, FakePaula]:
    paula = FakePaula()
    synth = Synth(synth_data(streams, **kwargs), paula)
    synth.init()
    paula.log.clear()
    return synth, paula


def _channel0(paula: FakePaula) -> list[tuple[object, ...]]:
    """The log without the register-pass writes of channels 1 to 3."""
    return [e for e in paula.log if not (e[0] in ("period", "volume") and e[1] != 0)]


def _retriggers(paula: FakePaula, channel: int, sample: int) -> int:
    return sum(1 for entry in paula.log if entry == ("location", channel, sample))


TEMPO_1 = bytes((0x94, 10))  # tempo = 10 / 10 = 1


def test_init_writes_the_chip_and_wipes_the_stacks() -> None:
    paula = FakePaula()
    data = synth_data({})
    synth = Synth(data, paula)
    synth.memory[0x50] = 0xAA
    synth.tempo = 9
    synth.init()
    park = address(WAVE_REGION, PARK)
    assert paula.log[:3] == [("dmacon", 0x800F), ("intena", 0x0780), ("intreq", 0x0780)]
    assert paula.log[3:7] == [("volume", ch, 0) for ch in range(4)]
    assert ("location", 3, park) in paula.log
    assert paula.log[-5:] == [*(("period", ch, 0x100) for ch in range(4)), ("dmacon", 0x000F)]
    assert synth.memory[0x40 : 0x40 + 4 + 4 * 0x20] == bytes(4 + 4 * 0x20)
    assert synth.tempo == data.tempo
    assert [(v.interrupts, v.stack, v.words, v.sample) for v in synth.voices] == [
        (2, top, 8, park) for top in STACKS
    ]


def test_start_resets_the_voice_and_switches_channel_one_off() -> None:
    synth, paula = _synth({5: TEMPO_1})
    old = synth.voices[2]
    old.interrupts, old.transpose, old.fade = 5, 3, ATTENUATION
    synth.start(5, 2)
    voice = synth.voices[2]
    assert (voice.interrupts, voice.transpose, voice.fade) == (5, 0, 0)
    assert (voice.sequence, voice.position, voice.stack) == (STREAMS, STREAMS, STACKS[2])
    assert (voice.base_period, voice.period, voice.words) == (START_PERIOD, START_PERIOD, 4)
    sample = address(WAVE_REGION, 8)
    assert paula.log == [
        ("intena", 0x200),
        ("intreq", 0x200),
        ("dmacon", 4),
        ("volume", 2, 0),
        ("location", 2, address(WAVE_REGION, PARK)),
        ("length", 2, 1),
        ("period", 2, 1),
        ("dmacon", SET | 4),
        ("location", 2, sample),
        ("length", 2, 4),
        ("period", 2, START_PERIOD),
        ("dmacon", 2),
    ]


def test_note_reads_the_pitch_table_with_transpose_and_retriggers() -> None:
    stream = bytes((0x94, 5, 0x8C, 5, 0xBC, 1, 0xD0, 1, 0x80, 30, 2))
    synth, paula = _synth({1: stream})
    synth.start(1, 0)
    paula.log.clear()
    synth.tick()
    voice = synth.voices[0]
    assert synth.tempo == 2
    assert voice.base_period == 200 + (2 * 4 + 1)
    assert (voice.length, voice.left, voice.volume) == (10, 9, 30)
    rest = address(WAVE_REGION, REST)
    silence = [
        ("dmacon", 1),
        ("intena", 0x80),
        ("intreq", 0x80),
        ("volume", 0, 0),
        ("location", 0, rest),
        ("length", 0, 1),
        ("period", 0, 1),
    ]
    assert paula.log[:15] == [*silence, ("dmacon", SET | 1), *silence]
    tail = [entry for entry in paula.log if entry[0] != "period" and entry[0] != "volume"]
    assert tail[-5:] == [
        ("location", 0, address(RAW_REGION, 0x40)),
        ("length", 0, 100),
        ("dmacon", SET | 1),
        ("intreq", 0x80),
        ("intena", SET | 0x80),
    ]
    assert voice.interrupts == 2


@pytest.mark.parametrize(
    ("instrument", "interrupts", "last"), [(0, 2, ("intena", 0x80)), (2, 1, ("intena", 0x8080))]
)
def test_looping_instruments_use_one_or_no_interrupt(
    instrument: int, interrupts: int, last: tuple[object, ...]
) -> None:
    synth, paula = _synth({1: bytes((*TEMPO_1, 0x8C, 3, 0xD0, instrument, 4))})
    synth.start(1, 0)
    synth.tick()
    assert synth.voices[0].interrupts == interrupts
    assert [entry for entry in paula.log if entry[0] == "intena"][-1] == last


def test_one_shot_takes_two_interrupts_and_loops_take_one() -> None:
    synth, paula = _synth({1: bytes((*TEMPO_1, 0x8C, 3, 0xD0, 1, 4)), 2: bytes((*TEMPO_1,))})
    synth.start(1, 1)
    synth.tick()
    paula.log.clear()
    paula.intreq(SET | 0x100)
    synth.interrupt()
    assert ("location", 1, address(WAVE_REGION, PARK)) in paula.log
    assert synth.voices[1].interrupts == 1 and paula.requests == 0
    paula.log.clear()
    paula.intreq(SET | 0x100)
    synth.interrupt()
    assert paula.log[2:5] == [("intena", 0x100), ("dmacon", 2), ("intreq", 0x100)]
    voice = synth.voices[1]
    voice.loop, voice.loop_offset, voice.sample, voice.words = -1, 8, 1000, 16
    paula.enabled |= 0x100
    paula.log.clear()
    paula.intreq(SET | 0x100)
    synth.interrupt()
    assert paula.log[2:5] == [("location", 1, 1008), ("length", 1, 12), ("intena", 0x100)]


def test_pending_requests_of_disabled_channels_are_only_acknowledged() -> None:
    synth, paula = _synth({})
    paula.intreq(SET | 0x200)
    paula.log.clear()
    synth.interrupt()
    assert paula.log == [("intreq", 0x200)]


ROW = bytes((2, 0, 1, 0, 0, 3, 0, 0xFE, 0, 0, 1, 0, 0, 0, 0))


def test_tremolo_and_vibrato_stages_follow_the_row() -> None:
    stream = bytes((*TEMPO_1, 0x8C, 20, 0x80, 20, 0x9C, 0, 3))
    synth, _ = _synth({1: stream}, rows=ROW)
    synth.start(1, 0)
    volumes, periods = [], []
    for _ in range(6):
        synth.tick()
        volumes.append(synth.voices[0].volume)
        periods.append(synth.voices[0].period)
    assert volumes == [20, 23, 23, 26, 26, 20]
    assert periods == [200 + 12 - 2] * 6


def test_repeat_flags_reload_the_counters() -> None:
    stream = bytes((*TEMPO_1, 0x8C, 20, 0x80, 20, 0x9C, 0, 0xA8, 1, 3))
    synth, _ = _synth({1: stream}, rows=ROW)
    synth.start(1, 0)
    for _ in range(6):
        synth.tick()
    voice = synth.voices[0]
    assert (voice.tremolo, voice.tremolo_steps, voice.tremolo_delays) == (0, [2, 0], [1, 0])


def test_loops_run_their_body_count_plus_one_times() -> None:
    stream = bytes((*TEMPO_1, 0x8C, 1, 0xC0, 2, 1, 0xC4, 0xAC))
    synth, paula = _synth({1: stream})
    synth.start(1, 0)
    paula.log.clear()
    for _ in range(4):
        synth.tick()
    voice = synth.voices[0]
    assert _retriggers(paula, 0, address(WAVE_REGION, 8)) == 3
    assert (voice.position, voice.sequence, voice.stack) == (0, 0, STACKS[0])


def test_call_return_restart_and_jump() -> None:
    streams = {
        1: bytes((*TEMPO_1, 0x8C, 1, 0xB0, 2, 7, 0xD4, 3)),
        2: bytes((4, 0xB4)),
        3: bytes((9, 0x88)),
    }
    synth, _ = _synth(streams)
    synth.start(1, 0)
    notes = []
    for _ in range(4):
        synth.tick()
        notes.append(synth.voices[0].note)
    assert notes == [4, 7, 9, 4]
    assert synth.voices[0].stack == STACKS[0] - 4


def test_summed_length_and_transpose_add() -> None:
    stream = bytes((*TEMPO_1, 0x98, 3, 1, 2, 3, 0xBC, 2, 0xB8, 0xFF, 0))
    synth, _ = _synth({1: stream})
    synth.start(1, 0)
    synth.tick()
    voice = synth.voices[0]
    assert (voice.length, voice.left, voice.transpose) == (6, 5, 1)
    synth2, _ = _synth({1: bytes((*TEMPO_1, 0xBC, 2, 0xB8, 0, 0))})
    synth2.start(1, 0)
    synth2.tick()
    assert synth2.voices[0].transpose == 0


def test_stop_still_runs_modulation_and_the_register_pass() -> None:
    synth, paula = _synth({1: bytes((*TEMPO_1, 0x8C, 1, 0x80, 9, 0xAC))})
    synth.start(1, 0)
    paula.log.clear()
    synth.tick()
    voice = synth.voices[0]
    assert (voice.position, voice.sequence, voice.retrigger) == (0, 0, False)
    assert _channel0(paula)[-5:] == [
        ("intena", 0x80),
        ("intreq", 0x80),
        ("dmacon", 1),
        ("period", 0, START_PERIOD),
        ("volume", 0, 9),
    ]
    paula.log.clear()
    synth.tick()
    assert _channel0(paula) == [("period", 0, START_PERIOD), ("volume", 0, 9)]


def test_rest_restarts_dma_before_reading_when_the_loop_offset_is_zero() -> None:
    synth, paula = _synth({1: bytes((*TEMPO_1, 0x8C, 1, 0xD0, 1, 5, 6))})
    synth.start(1, 0)
    synth.tick()
    paula.log.clear()
    synth.tick()
    assert paula.log[7] == ("dmacon", SET | 1)


def test_envelope_attack_decay_sustain_release() -> None:
    envelope = bytes((16, 1, 8, 1, 20, 2, 5, 1))
    stream = bytes((*TEMPO_1, 0x8C, 10, 0x80, 40, 0xC8, 0, 3, 0xAC))
    synth, _ = _synth({1: stream}, envelope=envelope)
    synth.start(1, 0)
    volumes = []
    for _ in range(10):
        synth.tick()
        volumes.append(synth.voices[0].volume)
    assert volumes == [16, 32, 40, 32, 24, 20, 20, 20, 15, 10]


def test_fade_attenuation_is_subtracted_at_the_register_pass() -> None:
    synth, paula = _synth({1: bytes((*TEMPO_1, 0x8C, 9, 0x80, 10, 3))})
    synth.start(1, 0)
    synth.fade(True)
    synth.tick()
    assert ("volume", 0, 0) in _channel0(paula)
    assert ("volume", 0, 10) not in paula.log
    synth.fade(False)
    paula.log.clear()
    synth.tick()
    assert paula.log == [
        ("period", 0, 212),
        ("volume", 0, 10),
        *(entry for ch in (1, 2, 3) for entry in (("period", ch, 0), ("volume", ch, 0))),
    ]


@pytest.mark.parametrize(
    ("stream", "match"),
    [
        (bytes((0x81,)), "not a command"),
        (bytes((0x94, 0)), "tempo divisor 0"),
        (bytes((0xD0, 3)), "instrument 3"),
        (bytes((0xD4, 200)), "outside hunk 44"),
    ],
)
def test_states_the_original_would_trap_on_raise(stream: bytes, match: str) -> None:
    synth, _ = _synth({1: stream})
    synth.start(1, 0)
    with pytest.raises(SynthError, match=match):
        synth.tick()


def test_start_rejects_a_fifth_channel() -> None:
    synth, _ = _synth({})
    with pytest.raises(SynthError, match="channel 4"):
        synth.start(1, 4)
