"""The synth and front end on the user's ``mog`` (ARCHITECTURE.md §6.2)."""

from __future__ import annotations

import hashlib
import struct
from typing import TYPE_CHECKING

import pytest

from harddaysengine.app.audio import AudioEngine
from harddaysengine.assets.adf import DiskSet
from harddaysengine.assets.hunk import parse_hunk_executable
from harddaysengine.assets.mog_code import MogCode
from harddaysengine.assets.sound_rules import parse_sound_rules
from harddaysengine.assets.synth_data import (
    FrontEndData,
    SynthData,
    parse_front_end,
    parse_synth,
)
from harddaysengine.sim.sound import Emit, SoundKind, SoundRequest

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.adf

# Bytes that are commands in the jump table (§6.2.6), with their argument counts; `$98` counts
# its own argument plus that many bytes.
_ARGUMENTS = {
    0x80: 1, 0x84: 1, 0x88: 0, 0x8C: 1, 0x90: 0, 0x94: 1, 0x9C: 1, 0xA0: 1, 0xA4: 1, 0xA8: 1,
    0xAC: 0, 0xB0: 1, 0xB4: 0, 0xB8: 1, 0xBC: 1, 0xC0: 1, 0xC4: 0, 0xC8: 1, 0xCC: 0, 0xD0: 1,
    0xD4: 1,
}  # fmt: skip
_ENDS = (0x88, 0xAC, 0xD4)


@pytest.fixture(scope="module")
def mog(adf_dir: Path) -> MogCode:
    disks = DiskSet.from_directory(adf_dir)
    return MogCode(parse_hunk_executable("mog", disks.read_file("mog")))


@pytest.fixture(scope="module")
def synth(mog: MogCode) -> SynthData:
    return parse_synth(mog)


@pytest.fixture(scope="module")
def front(mog: MogCode) -> FrontEndData:
    return parse_front_end(mog)


def test_tables_match_the_spec(synth: SynthData) -> None:
    assert synth.sequence_count == 168
    assert (synth.vibrato_row, synth.envelope_row, synth.tempo_dividend) == (15, 8, 750)
    assert (synth.attenuation, synth.rest, synth.park, synth.start_period) == (16, 168, 0, 256)
    assert [b - a for a, b in zip(synth.stack_tops, synth.stack_tops[1:], strict=False)] == [
        128
    ] * 3
    assert synth.wipe_start + synth.wipe_bytes == synth.stack_tops[-1] + 4
    assert len(synth.instruments) == 131
    waves = synth.instruments[:9]
    assert all(row.wave and row.loop < 0 and row.loop_offset == 0 for row in waves)
    banks = synth.instruments[9:]
    assert not any(row.wave for row in banks)
    looping = [row for row in banks if row.loop < 0]
    assert len(looping) == 10
    assert sum(1 for row in looping if row.loop_offset) == 7


def test_front_end_matches_the_releases_the_fight_routines_call(
    mog: MogCode, front: FrontEndData
) -> None:
    assert (front.busy, front.cursor, front.full, front.silence) == (0, 0, 0x0F, 0xA7)
    assert front.fixed_bits == (1, 2, 4, 8)
    assert front.release_masks == (0x0E, 0x0D, 0x0B, 0x07)
    rules = parse_sound_rules(mog.executable)
    releases = [
        step.request
        for _, steps in rules.routines
        for step in steps
        if isinstance(step, Emit) and step.request.kind == SoundKind.RELEASE
    ]
    assert releases
    for request in releases:
        assert request.mask == front.release_masks[request.channel]
        assert request.sound == front.silence


def _walk(memory: bytes, start: int) -> set[int]:
    """Command bytes of one sequence, read independently of the synth up to its end marker."""
    found: set[int] = set()
    position = start
    while True:
        byte = memory[position]
        position += 1
        if byte < 0x80:
            continue
        found.add(byte)
        if byte in _ENDS:
            return found
        position += _ARGUMENTS[byte] if byte != 0x98 else memory[position] + 1


def test_every_sequence_uses_only_implemented_commands(synth: SynthData) -> None:
    used: set[int] = set()
    for index in range(1, synth.sequence_count):
        (start,) = struct.unpack_from(">I", synth.memory, synth.sequence_table + 4 * index)
        used |= _walk(synth.memory, start)
    assert used <= set(_ARGUMENTS)
    assert synth.sequence_count > 0xA7


def _run(synth: SynthData, front: FrontEndData, requests: list[SoundRequest]) -> str:
    engine = AudioEngine(headless=True)
    engine.attach_synth(synth, front)
    digest = hashlib.sha256()
    for request in requests:
        engine.handle_requests([request])
        for _ in range(40):
            digest.update(engine.step_vbl().tobytes())
    return digest.hexdigest()


def test_every_sequence_plays_on_every_channel_and_replays_identically(
    synth: SynthData, front: FrontEndData
) -> None:
    requests = [
        SoundRequest(SoundKind.START, sequence, sequence % 4)
        for sequence in range(1, synth.sequence_count)
    ]
    requests.append(SoundRequest(SoundKind.RELEASE, front.silence, 1, front.release_masks[1]))
    assert _run(synth, front, requests) == _run(synth, front, requests)


def test_waveform_sequences_are_audible_before_any_bank_is_loaded(
    synth: SynthData, front: FrontEndData
) -> None:
    engine = AudioEngine(headless=True)
    engine.attach_synth(synth, front)
    audible = 0
    for sequence in range(1, synth.sequence_count):
        engine.handle_requests([SoundRequest(SoundKind.START, sequence, 0)])
        peak = max(int(abs(engine.step_vbl()).max()) for _ in range(60))
        audible += peak > 0
    assert audible > 0
