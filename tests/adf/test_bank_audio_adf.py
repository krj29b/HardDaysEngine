"""Independent disk reads for sample relocation and real PCM (§6.2.10)."""

from __future__ import annotations

import hashlib
import struct
from typing import TYPE_CHECKING

import numpy as np
import pytest

from harddaysengine.app.audio import AudioEngine
from harddaysengine.app.paula import address
from harddaysengine.app.scene_assets import GameAssetBundle
from harddaysengine.assets.adf import DiskSet
from harddaysengine.assets.bank_data import BankData, parse_banks
from harddaysengine.assets.hunk import parse_hunk_executable
from harddaysengine.assets.mog_code import MogCode
from harddaysengine.assets.synth_data import parse_synth
from harddaysengine.sim.sound import SoundKind, SoundRequest

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.adf


@pytest.fixture(scope="module")
def bundle(adf_dir: Path) -> GameAssetBundle:
    return GameAssetBundle(DiskSet.from_directory(adf_dir))


def test_bank_relocations_match_independent_operand_ranges(bundle: GameAssetBundle) -> None:
    mog = MogCode(parse_hunk_executable("mog", bundle.disk_set.read_file("mog")))
    synth = parse_synth(mog)
    actual = parse_banks(mog, synth)
    expected = [(0, row.sample) for row in synth.instruments]
    code = mog.hunk_data(44)
    # Read boundary operands independently; no production walker or copied sample table.
    boundaries = (
        0x1216,
        0x122C,
        0x1244,
        0x124C,
        0x1266,
        0x12EE,
        0x12F4,
        0x130E,
        0x1328,
        0x1342,
        0x135C,
    )
    base = mog.pointer(0x712, "instrument base", 44).offset
    indices = [(struct.unpack_from(">I", code, p)[0] - base) // 14 for p in boundaries]
    slots = (1, 2, 5, 2, 3, 4, 2, 3, 2)
    bounds = [*indices[:5], *indices[6:]]
    for start, end, slot in zip(bounds[:-1], bounds[1:], slots, strict=True):
        for index in range(start, end):
            expected[index] = slot, synth.instruments[index].sample
    for lea, stores in ((0x1280, (0x128E, 0x12A4)), (0x12AC, (0x12BA, 0x12D0, 0x12E6))):
        index = (struct.unpack_from(">I", code, lea)[0] - base) // 14
        for delta, operand in enumerate(stores):
            expected[index + delta] = 1, struct.unpack_from(">I", code, operand)[0]
    assert actual.samples == tuple(expected)
    assert all(slot for slot, _ in actual.samples[9:])


def test_each_loader_copies_the_original_file_slice(bundle: GameAssetBundle) -> None:
    engine = AudioEngine(headless=True)
    bundle.attach_synth(engine)
    banks = engine.banks
    assert isinstance(banks, BankData)
    for bank in (banks.startup, *banks.loaders):
        bundle.load_sound_bank(engine, bank.name)
        original = bundle.disk_set.read_file(bank.name)[
            banks.header_bytes : banks.header_bytes + bank.size
        ]
        assert engine.memory.read(address(bank.slot + 1, 0), len(original)).tobytes() == original


def _trace(bundle: GameAssetBundle) -> tuple[str, int]:
    engine = AudioEngine(headless=True)
    bundle.attach_synth(engine)
    banks = engine.banks
    assert banks is not None
    for bank in banks.loaders:
        bundle.load_sound_bank(engine, bank.name)
    digest, peak = hashlib.sha256(), 0
    for sequence in range(1, 168):
        engine.handle_requests([SoundRequest(SoundKind.START, sequence, sequence % 4)])
        for _ in range(12):
            pcm = engine.step_vbl()
            digest.update(pcm.tobytes())
            peak = max(peak, int(np.abs(pcm.astype(np.int32)).max()))
    return digest.hexdigest(), peak


def test_bank_sequences_render_audibly_and_replay_identically(bundle: GameAssetBundle) -> None:
    first = _trace(bundle)
    assert first[1] > 0
    assert first == _trace(bundle)
