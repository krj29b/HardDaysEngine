"""Audio engine: the ``mog`` synth on a Paula model, and ``program`` music (ARCHITECTURE.md §6).

Each host VBL runs the synth tick, then the sound requests reported during that tick, then
renders one fiftieth of a second (§6.2.9). Without synth data the channels stay silent.

Rendered PCM goes into a ring that an SDL audio callback drains at the device rate, so VBL
bursts from the host loop never drop or starve audio (ADR-051).
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

import pygame
from pygame._sdl2 import audio as sdl_audio

from harddaysengine.app.audio_stream import PcmRing
from harddaysengine.app.module_player import ModulePlayer
from harddaysengine.app.paula import Paula, SampleMemory, address
from harddaysengine.app.sound_front import SoundFront
from harddaysengine.app.synth import WAVE_REGION, Synth

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    import numpy as np
    from numpy.typing import NDArray

    from harddaysengine.assets.audio import DecodedModule
    from harddaysengine.assets.bank_data import BankData, BankLoad
    from harddaysengine.assets.music_data import MusicData
    from harddaysengine.assets.synth_data import FrontEndData, SynthData
    from harddaysengine.sim.sound import SoundRequest

DEFAULT_MIX_RATE = 22050
DEVICE_CHUNK = 512
_BYTES_PER_FRAME = 4


class AudioEngine:
    """The synth, its front end and Paula, with ``pygame.mixer`` output and module music."""

    def __init__(self, sample_rate: int = DEFAULT_MIX_RATE, *, headless: bool = False) -> None:
        """Render at ``sample_rate``; ``headless`` never opens an audio device."""
        self.memory = SampleMemory()
        self.paula = Paula(self.memory, sample_rate)
        self.synth: Synth | None = None
        self.front: SoundFront | None = None
        self.banks: BankData | None = None
        self.music_data: MusicData | None = None
        self.module_player: ModulePlayer | None = None
        self._pending: list[Callable[[], None]] = []
        self._headless = headless
        self._mixer_initialized = False
        self._device: sdl_audio.AudioDevice | None = None
        self._ring = PcmRing.for_device(sample_rate, DEVICE_CHUNK)
        self._current_music_title: str | None = None
        self._is_music_playing = False
        if not headless:
            self._init_pygame_mixer(sample_rate)

    def _init_pygame_mixer(self, sample_rate: int) -> None:
        """Bring up SDL audio, then open a callback device at exactly ``sample_rate``."""
        try:
            if pygame.mixer.get_init() is None:
                pygame.mixer.init(frequency=sample_rate, size=-16, channels=2, buffer=DEVICE_CHUNK)
            self._device = sdl_audio.AudioDevice(
                devicename=None,
                iscapture=False,
                frequency=sample_rate,
                audioformat=sdl_audio.AUDIO_S16,
                numchannels=2,
                chunksize=DEVICE_CHUNK,
                allowed_changes=0,
                callback=self._fill_device,
            )
            self._device.pause(0)
            self._mixer_initialized = True
        except pygame.error:
            self._headless = True
            self._mixer_initialized = False
            self._device = None

    def _fill_device(self, _device: sdl_audio.AudioDevice, stream: memoryview) -> None:
        """SDL audio thread: copy the next queued frames, or silence, into ``stream``."""
        stream[:] = self._ring.read(len(stream) // _BYTES_PER_FRAME).tobytes()

    @property
    def headless(self) -> bool:
        """Whether the engine is running without an active `pygame.mixer` device."""
        return self._headless

    @property
    def mixer_initialized(self) -> bool:
        """Whether `pygame.mixer` was successfully initialized."""
        return self._mixer_initialized

    @property
    def current_music_title(self) -> str | None:
        """Title of the currently loaded `.cmp` ProTracker module, if any."""
        return self._current_music_title

    @property
    def is_music_playing(self) -> bool:
        """Whether ProTracker module playback is active."""
        return self._is_music_playing

    def attach_synth(self, synth: SynthData, front: FrontEndData) -> None:
        """Enter ``mog``: load the waveform hunk, then run the synth init and front-end set-up."""
        self.memory.clear()
        self.banks = None
        self.memory.load(WAVE_REGION, synth.waves)
        self.synth = Synth(synth, self.paula)
        self.paula.handler = self.synth.interrupt
        self.front = SoundFront(front, self._start)
        self.module_player = None
        self._is_music_playing = False
        self._pending.clear()
        self.synth.init()
        self.paula.commit()
        self.front.load()
        self.front.clear_busy()

    def _start(self, sequence: int, channel: int) -> None:
        if self.synth is None:
            raise ValueError("synth start without synth data")
        self.synth.start(sequence, channel)
        self.paula.commit()

    def attach_banks(self, banks: BankData) -> None:
        """Resolve the original instrument addresses once (§6.2.10)."""
        if self.synth is None:
            raise ValueError("bank relocation requires synth data")
        if len(banks.samples) != len(self.synth.samples):
            raise ValueError("bank relocation does not match the instrument table")
        self.banks = banks
        self.synth.samples[:] = [
            address(slot + WAVE_REGION, offset) for slot, offset in banks.samples
        ]

    def load_bank(self, bank: BankLoad, data: bytes) -> None:
        """Copy the exact original file slice into its persistent slot (§6.2.10)."""
        if self.banks is None:
            raise ValueError("bank load requires relocation data")
        start = self.banks.header_bytes
        if len(data) < start:
            raise ValueError(f"truncated sound bank header {bank.name!r}")
        self.memory.write(bank.slot + WAVE_REGION, data[start : start + bank.size], bank.size)

    def handle_requests(self, requests: Sequence[SoundRequest]) -> None:
        """Queue requests reported by ``sim``; they run after the next synth tick (§6.2.9)."""
        front = self.front
        if front is not None:
            self._pending.extend(partial(front.request, request) for request in requests)

    def stop_all(self) -> None:
        """Queue the front end's stop all (§6.2.1)."""
        front = self.front
        if front is not None:
            self._pending.append(front.stop_all)

    def fade_step(self, flag: bool) -> None:
        """One palette fade step: the synth's fade attenuation (§6.2.8)."""
        if self.synth is not None and self.module_player is None:
            self.synth.fade(flag)

    def step_vbl(self) -> NDArray[np.int16]:
        """Advance one PAL VBL: tick, queued requests, then render and queue the PCM."""
        if self.module_player is not None:
            self.module_player.tick()
            self.paula.commit()
        elif self.synth is not None:
            self.synth.tick()
            self.paula.commit()
        pending, self._pending = self._pending, []
        for action in pending:
            action()
        pcm = self.paula.render_frame()
        if self._device is not None:
            self._ring.write(pcm)
        return pcm

    def play_music(self, module: DecodedModule, *, loops: int = -1) -> bool:
        """Start the original 50 Hz program player, including in headless mode (§6.3.1)."""
        if loops != -1:
            raise ValueError("the original module player repeats its arrangement")
        if self.music_data is None:
            raise ValueError("module playback requires the original program player tables")
        self._pending.clear()
        self.paula.handler = None
        self.module_player = ModulePlayer(module, self.music_data, self.paula)
        self._current_music_title = module.title
        self._is_music_playing = True
        return True

    def stop_music(self) -> None:
        """Remove the program music tick job, retaining Paula's last buffers (§6.3.1)."""
        self._is_music_playing = False
        if self.module_player is not None:
            self.module_player.running = False

    def close(self) -> None:
        """Stop music and shut down `pygame.mixer` if initialized."""
        self.stop_music()
        if self._device is not None:
            self._device.close()
            self._device = None
        if self._mixer_initialized:
            self._mixer_initialized = False
            try:
                pygame.mixer.quit()
            except pygame.error:
                return
