"""Original module timing, unsupported effects and Paula output (§6.3.1)."""

from __future__ import annotations

import struct

import numpy as np
import pytest

from harddaysengine.app.audio import AudioEngine
from harddaysengine.app.module_player import ModulePlayer
from harddaysengine.app.paula import Paula, SampleMemory
from harddaysengine.assets.audio import DecodedModule, parse_protracker_mod
from harddaysengine.assets.music_data import MusicData

RULES = MusicData(tuple(900 - 20 * i for i in range(37)), bytes(range(32)), 6)


class PeriodPaula(Paula):
    """Record period writes while still rendering the real sample buffers."""

    def __init__(self) -> None:
        super().__init__(SampleMemory(), 22050)
        self.periods = [0] * 4

    def period(self, channel: int, value: int) -> None:
        self.periods[channel] = value
        super().period(channel, value)


def _module(events: tuple[tuple[int, int, int, int, int], ...]) -> DecodedModule:
    data = bytearray(1084 + 1024 + 64)
    struct.pack_into(">HBBHH", data, 42, 32, 7, 48, 2, 2)
    data[950] = 1
    data[1080:1084] = b"M.K."
    for row, channel, note, effect, arg in events:
        sample = 1 if row == 0 else 0
        start = 1084 + row * 16 + channel * 4
        data[start : start + 4] = bytes(
            ((sample & 0xF0) | (note >> 8), note & 255, ((sample & 15) << 4) | effect, arg)
        )
    data[-64:] = bytes((100, 156)) * 32
    return parse_protracker_mod("invented.mod", bytes(data))


def _player(events: tuple[tuple[int, int, int, int, int], ...]) -> ModulePlayer:
    return ModulePlayer(_module(events), RULES, Paula(SampleMemory(), 22050))


def _ticks(player: ModulePlayer, count: int) -> np.ndarray[tuple[int, int], np.dtype[np.int16]]:
    out = np.zeros((0, 2), dtype=np.int16)
    for _ in range(count):
        player.tick()
        player.paula.commit()
        out = player.paula.render_frame()
    return out


def test_first_row_waits_six_pal_ticks_and_sample_start_is_zeroed() -> None:
    player = _player(((0, 0, 500, 0, 0),))
    assert not np.any(_ticks(player, 5))
    assert player.row == 0
    assert np.any(_ticks(player, 1))
    assert player.row == 1
    voice = player.voices[0]
    assert player.paula.memory.read(voice.sample, 6).tolist() == [0, 0, 0, 0, 100, -100]


@pytest.mark.parametrize(("effect", "arg", "expected"), ((1, 100, 113), (2, 255, 856)))
def test_slides_clamp_and_do_not_remember_a_zero_argument(
    effect: int, arg: int, expected: int
) -> None:
    player = _player(((0, 0, 500, effect, arg), (1, 0, 0, effect, 0)))
    _ticks(player, 11)
    assert player.voices[0].period == expected
    _ticks(player, 7)
    assert player.voices[0].period == expected


def test_tone_slide_remembers_speed_and_does_not_restart_the_sample() -> None:
    player = _player(((0, 0, 500, 0, 0), (1, 0, 450, 3, 20), (2, 0, 400, 3, 0)))
    _ticks(player, 18)
    assert (player.voices[0].period, player.voices[0].target) == (450, 400)
    _ticks(player, 3)
    assert (player.voices[0].period, player.voices[0].target) == (400, 0)


def test_volume_immediate_does_not_replace_the_stored_sample_volume() -> None:
    player = _player(((0, 0, 500, 0xC, 10), (1, 0, 0, 0xA, 2)))
    _ticks(player, 6)
    assert player.voices[0].volume == 48
    _ticks(player, 7)
    assert player.voices[0].volume == 46


def test_f_uses_low_five_bits_as_speed_and_d_ignores_break_row() -> None:
    player = _player(((0, 0, 500, 0xF, 0x25), (1, 0, 0, 0xD, 0x30)))
    _ticks(player, 6)
    assert player.speed == 5
    _ticks(player, 5)
    assert (player.position, player.row) == (0, 0)


def test_two_break_commands_cancel_and_unsupported_extended_effect_does_nothing() -> None:
    player = _player(((0, 0, 500, 0xD, 0), (0, 1, 500, 0xD, 0), (1, 0, 0, 0xE, 0xC0)))
    _ticks(player, 12)
    assert player.row == 2
    assert player.voices[0].volume == 48
    assert np.any(_ticks(player, 1))


def test_headless_music_runs_and_stop_keeps_the_last_dma_loop() -> None:
    engine = AudioEngine(headless=True)
    engine.music_data = RULES
    module = _module(((0, 0, 500, 0, 0),))
    assert engine.play_music(module)
    assert engine.is_music_playing
    assert any(np.any(engine.step_vbl()) for _ in range(8))
    player = engine.module_player
    assert player is not None
    row = player.row
    engine.stop_music()
    assert not engine.is_music_playing
    for _ in range(10):
        engine.step_vbl()
    assert player.row == row


@pytest.mark.parametrize(
    ("effect", "argument", "expected"),
    ((0, 0x21, [460, 480, 500, 460]), (4, 0xFF, [500, 503, 507, 497])),
)
def test_arpeggio_and_signed_vibrato_use_the_decoded_tables(
    effect: int, argument: int, expected: list[int]
) -> None:
    paula = PeriodPaula()
    player = ModulePlayer(_module(((0, 0, 500, effect, argument),)), RULES, paula)
    _ticks(player, 6)
    actual = []
    for _ in range(4):
        _ticks(player, 1)
        actual.append(paula.periods[0])
    assert actual == expected


def test_portamento_clamps_cleanly_to_protracker_bounds_without_underflow() -> None:
    paula = PeriodPaula()
    player = ModulePlayer(_module(((0, 0, 500, 1, 255),)), RULES, paula)
    _ticks(player, 11)
    assert player.voices[0].period == 113
    assert paula.periods[0] == 113
    paula_down = PeriodPaula()
    player_down = ModulePlayer(_module(((0, 0, 800, 2, 255),)), RULES, paula_down)
    _ticks(player_down, 11)
    assert player_down.voices[0].period == 856
    assert paula_down.periods[0] == 856
