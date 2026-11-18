"""Bank offsets, short reads and audible sequencer output (§6.2.10)."""

from __future__ import annotations

import numpy as np
import pytest

from harddaysengine.app.audio import AudioEngine
from harddaysengine.app.paula import address
from harddaysengine.assets.bank_data import BankData, BankLoad
from harddaysengine.sim.sound import SoundKind, SoundRequest
from tests.unit.synth_setup import synth_data
from tests.unit.test_sound_front import FRONT


def _engine() -> tuple[AudioEngine, BankLoad]:
    engine = AudioEngine(headless=True)
    engine.attach_synth(
        synth_data({1: bytes((0x94, 10, 0x8C, 1, 0x80, 50, 0xD0, 1, 5, 0x88))}), FRONT
    )
    bank = BankLoad("invented.a", 2, 264)
    engine.attach_banks(BankData(bank, (bank,), ((0, 8), (2, 64), (0, 16)), 32))
    return engine, bank


def test_bank_sound_is_silent_until_its_slot_is_loaded() -> None:
    engine, bank = _engine()
    request = SoundRequest(SoundKind.START, 1, 0)
    engine.handle_requests([request])
    assert not any(np.any(engine.step_vbl()) for _ in range(4))
    engine.load_bank(bank, bytes(32 + 64) + bytes((90, 166)) * 100)
    engine.handle_requests([request])
    assert any(np.any(engine.step_vbl()) for _ in range(4))
    assert engine.synth is not None
    assert engine.synth.voices[0].sample == address(3, 64)


def test_bank_replacement_retains_short_read_tail_and_never_adds_another_base() -> None:
    engine, bank = _engine()
    assert engine.synth is not None
    samples = engine.synth.samples[:]
    engine.load_bank(bank, bytes(32) + bytes((17,)) * 264)
    engine.load_bank(bank, bytes(32) + bytes((38,)) * 4)
    actual = engine.memory.read(address(3, 0), 264)
    assert actual[:4].tolist() == [38] * 4
    assert actual[4:].tolist() == [17] * 260
    assert engine.synth.samples == samples
    assert engine.banks is not None
    engine.attach_banks(engine.banks)
    assert engine.synth.samples == samples


def test_bank_load_refuses_missing_header_or_relocation() -> None:
    engine, bank = _engine()
    with pytest.raises(ValueError, match="header"):
        engine.load_bank(bank, bytes(31))
    with pytest.raises(ValueError, match="instrument"):
        engine.attach_banks(BankData(bank, (), (), 32))
