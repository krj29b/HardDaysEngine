"""Audible headless scene ownership, banks, requests and PAL fades (§6.3)."""

from __future__ import annotations

import hashlib
import struct
from typing import TYPE_CHECKING

import numpy as np
import pytest

from harddaysengine.app.audio import AudioEngine
from harddaysengine.app.game import GameScene, GameSession
from harddaysengine.app.input import InputFrame, PlayerInput
from harddaysengine.app.paula import address
from harddaysengine.app.scene_assets import GameAssetBundle
from harddaysengine.app.wizard_audit import wizard_until
from harddaysengine.assets.adf import DiskSet
from harddaysengine.assets.hunk import parse_hunk_executable
from harddaysengine.sim.overworld import EncounterTrigger, TriggerKind
from harddaysengine.sim.rewards import GiftKind
from harddaysengine.sim.stonehenge import StonePhase
from harddaysengine.sim.types import KnightId
from harddaysengine.sim.wizard import WizardPhase
from tests.adf.test_stonehenge_adf import _offer
from tests.adf.test_wizard_visit_adf import _session as wizard_session

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.adf


@pytest.mark.parametrize("scene", (GameScene.TITLE, GameScene.OVERWORLD))
def test_initial_scene_selects_its_original_audio_system(adf_dir: Path, scene: GameScene) -> None:
    engine = AudioEngine(headless=True)
    session = GameSession(disk_set=DiskSet.from_directory(adf_dir), audio=engine, start_scene=scene)
    assert engine.is_music_playing == (scene == GameScene.TITLE)
    if scene == GameScene.OVERWORLD:
        session.step_tick(InputFrame())
        assert engine.synth is not None
        assert all(voice.sequence for voice in engine.synth.voices)


def _audio(session: GameSession) -> AudioEngine:
    assert session.asset_bundle is not None
    engine = AudioEngine(headless=True)
    session.audio = engine
    session.asset_bundle.attach_synth(engine)
    return engine


def _bank(session: GameSession, engine: AudioEngine, name: str) -> None:
    assert session.asset_bundle is not None and engine.banks is not None
    bank = engine.banks.named(name)
    raw = session.asset_bundle.disk_set.read_file(name)[32:]
    assert engine.memory.read(address(bank.slot + 1, 0), len(raw)).tobytes() == raw


@pytest.mark.parametrize("ending", (False, True))
def test_original_modules_use_runtime_tables_and_replay_audible_pcm(
    adf_dir: Path, ending: bool
) -> None:
    bundle = GameAssetBundle(DiskSet.from_directory(adf_dir))
    traces = []
    for _ in range(2):
        engine = AudioEngine(headless=True)
        bundle.attach_synth(engine)
        module = bundle.load_victory_music() if ending else bundle.load_title_music()
        assert module is not None and engine.synth is not None and engine.music_data is not None
        code = (
            parse_hunk_executable("program", bundle.disk_set.read_file("program")).sections[1].data
        )
        assert engine.music_data.periods == struct.unpack_from(">37H", code, 0x652)
        synth_before = [voice.position for voice in engine.synth.voices]
        assert engine.play_music(module)
        digest, peak = hashlib.sha256(), 0
        for _ in range(12000):
            pcm = engine.step_vbl()
            digest.update(pcm.tobytes())
            peak = max(peak, int(np.abs(pcm.astype(np.int32)).max()))
        assert peak > 0
        assert [voice.position for voice in engine.synth.voices] == synth_before
        traces.append(digest.hexdigest())
    assert traces[0] == traces[1]


@pytest.mark.parametrize("kind", (0, 4, 8, 0x14, 0x18, 0x20, 0x24, 0x30, 0x40))
def test_fight_caller_installs_its_decoded_creature_and_knight_banks(
    adf_dir: Path, kind: int
) -> None:
    engine = AudioEngine(headless=True)
    session = GameSession(disk_set=DiskSet.from_directory(adf_dir), audio=engine)
    session._start_combat(
        EncounterTrigger(TriggerKind.WANDERING_MONSTER, KnightId.RED, -1, 1, 0, creature_type=kind),
        KnightId.RED,
    )
    assert session.scene == GameScene.COMBAT and session.fight_loader is not None
    assert engine.module_player is None
    _bank(session, engine, "kn.a")
    name = session.fight_loader.arena_for(kind).cels.sound_bank
    assert name is not None
    _bank(session, engine, name)
    for _ in range(12):
        session.step_tick(InputFrame())


def test_map_restarts_after_the_full_silent_wait_without_being_stopped_again(adf_dir: Path) -> None:
    session = GameSession(disk_set=DiskSet.from_directory(adf_dir))
    engine = _audio(session)
    session._reenter_map()
    session.step_tick(InputFrame())
    session._open_sheet(9)
    session._begin_sound_return("sheet")
    rng = session.overworld.prng.state
    assert session.asset_bundle is not None
    data = session.asset_bundle.get_scene_sounds()
    for _ in range(data.fade_wait - 1):
        session.step_tick(InputFrame(players={KnightId.RED: PlayerInput(fire=True)}))
        assert session.scene == GameScene.STATUS and session.sheet_screen is not None
    assert engine.synth is not None
    assert all(voice.fade == 16 for voice in engine.synth.voices)
    session.step_tick(InputFrame())
    assert session.scene == GameScene.OVERWORLD and session.sheet_screen is None
    assert session.overworld.prng.state == rng
    synth = engine.synth
    for request in data.map_music:
        expected = struct.unpack_from(
            ">I", synth.data.memory, synth.data.sequence_table + 4 * request.sound
        )[0]
        assert synth.voices[request.channel].sequence == expected
        assert synth.voices[request.channel].fade == 0


def test_natural_wizard_installs_its_bank_before_the_arrival_sequences(adf_dir: Path) -> None:
    session = wizard_session(adf_dir, GiftKind.ITEM)
    engine = _audio(session)
    wizard_until(session, WizardPhase.ARRIVAL)
    screen = session.wizard_screen
    assert screen is not None
    _bank(session, engine, screen.rules.bank)
    assert any(np.any(engine.step_vbl()) for _ in range(80))


def test_ritual_installs_its_bank_before_its_fixed_channel_starts(adf_dir: Path) -> None:
    session = _offer(adf_dir)
    engine = _audio(session)
    screen = session.stone_screen
    assert screen is not None
    screen.state.phase = StonePhase.RITUAL_MESSAGE_OUT
    session._stone_transition(screen)
    _bank(session, engine, screen.rules.ritual.bank)
    assert any(np.any(engine.step_vbl()) for _ in range(80))


def test_sheet_mapping_preserves_multi_voice_order_and_does_not_invent_shop_sounds(
    adf_dir: Path,
) -> None:
    bundle = GameAssetBundle(DiskSet.from_directory(adf_dir))
    data = bundle.get_scene_sounds()
    assert data.sheet(["bad", "click", "good", "bad"]) == (
        *data.bad,
        *data.click,
        *data.good,
        *data.bad,
    )
    assert data.sheet(["buy", "sell", "coin"]) == ()
