"""Audio engine: the ``mog`` synth on a Paula model, and ``program`` music (ARCHITECTURE.md §6).

Each host VBL runs the synth tick, then the sound requests reported during that tick, then
renders one fiftieth of a second (§6.2.9). Without synth data the channels stay silent.
"""

from __future__ import annotations

import io
from functools import partial
from typing import TYPE_CHECKING

import pygame

from harddaysengine.app.paula import Paula, SampleMemory
from harddaysengine.app.sound_front import SoundFront
from harddaysengine.app.synth import WAVE_REGION, Synth

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    import numpy as np
    from numpy.typing import NDArray

    from harddaysengine.assets.audio import DecodedModule
    from harddaysengine.assets.synth_data import FrontEndData, SynthData
    from harddaysengine.sim.sound import SoundRequest

DEFAULT_MIX_RATE = 22050


class AudioEngine:
    """The synth, its front end and Paula, with ``pygame.mixer`` output and module music."""

    def __init__(self, sample_rate: int = DEFAULT_MIX_RATE, *, headless: bool = False) -> None:
        """Render at ``sample_rate``; ``headless`` never opens an audio device."""
        self.memory = SampleMemory()
        self.paula = Paula(self.memory, sample_rate)
        self.synth: Synth | None = None
        self.front: SoundFront | None = None
        self._pending: list[Callable[[], None]] = []
        self._headless = headless
        self._mixer_initialized = False
        self._output_channel: pygame.mixer.Channel | None = None
        self._current_music_title: str | None = None
        self._is_music_playing = False
        if not headless:
            self._init_pygame_mixer(sample_rate)

    def _init_pygame_mixer(self, sample_rate: int) -> None:
        try:
            if pygame.mixer.get_init() is None:
                pygame.mixer.init(frequency=sample_rate, size=-16, channels=2, buffer=1024)
            self._output_channel = pygame.mixer.Channel(0)
            self._mixer_initialized = True
        except pygame.error:
            self._headless = True
            self._mixer_initialized = False
            self._output_channel = None

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
        self.memory.load(WAVE_REGION, synth.waves)
        self.synth = Synth(synth, self.paula)
        self.paula.handler = self.synth.interrupt
        self.front = SoundFront(front, self._start)
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
        if self.synth is not None:
            self.synth.fade(flag)

    def step_vbl(self) -> NDArray[np.int16]:
        """Advance one PAL VBL: tick, queued requests, then render and queue the PCM."""
        if self.synth is not None:
            self.synth.tick()
            self.paula.commit()
        pending, self._pending = self._pending, []
        for action in pending:
            action()
        pcm = self.paula.render_frame()
        if self._mixer_initialized and self._output_channel is not None and len(pcm) > 0:
            try:
                sound = pygame.sndarray.make_sound(pcm)
                if not self._output_channel.get_busy():
                    self._output_channel.play(sound)
                else:
                    self._output_channel.queue(sound)
            except pygame.error:
                self._mixer_initialized = False
                self._headless = True
        return pcm

    def play_music(self, module: DecodedModule, *, loops: int = -1) -> bool:
        """Load and play a decompressed `.cmp` ProTracker module via `pygame.mixer.music`."""
        self._current_music_title = module.title
        if not self._mixer_initialized:
            self._is_music_playing = False
            return False
        try:
            pygame.mixer.music.load(io.BytesIO(module.raw_mod))
            pygame.mixer.music.play(loops=loops)
        except pygame.error:
            self._is_music_playing = False
            return False
        self._is_music_playing = True
        return True

    def stop_music(self) -> None:
        """Stop ProTracker module playback if active."""
        self._is_music_playing = False
        if self._mixer_initialized:
            try:
                pygame.mixer.music.stop()
            except pygame.error:
                return

    def close(self) -> None:
        """Stop music and shut down `pygame.mixer` if initialized."""
        self.stop_music()
        if self._mixer_initialized:
            try:
                pygame.mixer.quit()
            except pygame.error:
                self._mixer_initialized = False
                self._output_channel = None
                return
            self._mixer_initialized = False
            self._output_channel = None
