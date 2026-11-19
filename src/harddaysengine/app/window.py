"""Host window and fullscreen mode controller (T13.3, T13.4, ARCHITECTURE.md §13.6-§13.7).

Manages resizable windowed, borderless desktop fullscreen, and exclusive fullscreen modes via
``pygame.display.set_mode`` without destroying the OpenGL 3.3 context or textures. Requests a
50 Hz display mode in exclusive fullscreen when the display lists one, and consumes ``F11`` and
``Alt+Enter`` (including their matching ``KEYUP`` events) before ``InputRouter`` sees them so
fullscreen toggles never confirm menus and never enter replay recordings.
"""

from __future__ import annotations

import ctypes
import ctypes.util
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import pygame

from harddaysengine.app.loop import PAL_HZ
from harddaysengine.render.presentation import FullscreenMode

if TYPE_CHECKING:
    from collections.abc import Sequence

_BASE_GL_FLAGS = pygame.OPENGL | pygame.DOUBLEBUF
_WINDOWED_FLAGS = _BASE_GL_FLAGS | pygame.RESIZABLE
_FULLSCREEN_FLAGS = _BASE_GL_FLAGS | pygame.FULLSCREEN
_ENTER_KEYS = frozenset({pygame.K_RETURN, pygame.K_KP_ENTER})


class _SdlDisplayMode(ctypes.Structure):
    _fields_ = [
        ("format", ctypes.c_uint32),
        ("w", ctypes.c_int),
        ("h", ctypes.c_int),
        ("refresh_rate", ctypes.c_int),
        ("driverdata", ctypes.c_void_p),
    ]


def _load_sdl2() -> ctypes.CDLL | None:
    pygame_dir = Path(pygame.__file__).parent
    for pattern in ("SDL2.dll", "libSDL2*.so*", "libSDL2*.dylib"):
        matches = sorted(pygame_dir.glob(pattern))
        if matches:
            try:
                return ctypes.CDLL(str(matches[0]))
            except OSError:
                return None
    found = ctypes.util.find_library("SDL2")
    if not found:
        return None
    try:
        return ctypes.CDLL(found)
    except OSError:
        return None


def _list_sdl_display_modes(sdl: ctypes.CDLL, display_index: int = 0) -> list[tuple[int, int, int]]:
    count = int(sdl.SDL_GetNumDisplayModes(display_index))
    modes: list[tuple[int, int, int]] = []
    for idx in range(max(0, count)):
        mode = _SdlDisplayMode()
        if int(sdl.SDL_GetDisplayMode(display_index, idx, ctypes.byref(mode))) == 0:
            modes.append((int(mode.w), int(mode.h), int(mode.refresh_rate)))
    return modes


def select_50hz_display_mode(
    modes: Sequence[tuple[int, int, int]],
    preferred_size: tuple[int, int],
) -> tuple[int, int, int] | None:
    """Choose a 50 Hz display mode when available, preferring ``preferred_size`` (§13.6)."""
    candidates = [m for m in modes if m[2] == PAL_HZ]
    if not candidates:
        return None
    for mode in candidates:
        if (mode[0], mode[1]) == preferred_size:
            return mode
    return max(candidates, key=lambda m: (m[0] * m[1], m[0], m[1]))


def _try_apply_sdl_50hz_mode(preferred_size: tuple[int, int], display_index: int = 0) -> None:
    sdl = _load_sdl2()
    if sdl is None:
        return
    modes = _list_sdl_display_modes(sdl, display_index)
    chosen = select_50hz_display_mode(modes, preferred_size)
    if chosen is None:
        return
    sdl.SDL_GL_GetCurrentWindow.restype = ctypes.c_void_p
    win_ptr = sdl.SDL_GL_GetCurrentWindow()
    if not win_ptr:
        return
    desired = _SdlDisplayMode(format=0, w=chosen[0], h=chosen[1], refresh_rate=chosen[2])
    closest = _SdlDisplayMode()
    sdl.SDL_GetClosestDisplayMode.restype = ctypes.c_void_p
    if sdl.SDL_GetClosestDisplayMode(display_index, ctypes.byref(desired), ctypes.byref(closest)):
        sdl.SDL_SetWindowDisplayMode(ctypes.c_void_p(win_ptr), ctypes.byref(closest))


class WindowModeBackend(Protocol):
    """Minimal display-mode interface used by :class:`WindowController`."""

    def set_windowed(self, size: tuple[int, int]) -> None:
        """Enter resizable OpenGL windowed mode at ``size``."""

    def set_desktop_fullscreen(self) -> None:
        """Enter borderless desktop fullscreen OpenGL mode."""

    def set_exclusive_fullscreen(self) -> None:
        """Enter exclusive fullscreen OpenGL mode (requesting 50 Hz when listed)."""

    def get_window_size(self) -> tuple[int, int]:
        """Return the current window ``(width, height)`` in pixels."""

    def get_refresh_rate(self) -> int:
        """Return the active display refresh rate in Hz, or ``0`` if unknown."""


class PygameWindowBackend:
    """``pygame-ce`` OpenGL 3.3 display-mode backend preserving the active GL context."""

    def __init__(self, *, vsync: bool = True) -> None:
        self._vsync = vsync

    def _set_mode(self, size: tuple[int, int], flags: int) -> None:
        vsync_flag = 1 if self._vsync else 0
        try:
            pygame.display.set_mode(size, flags, vsync=vsync_flag)
        except pygame.error:
            pygame.display.set_mode(size, flags, vsync=0)

    def set_windowed(self, size: tuple[int, int]) -> None:
        """Enter resizable OpenGL windowed mode at ``size``."""
        self._set_mode(size, _WINDOWED_FLAGS)

    def set_desktop_fullscreen(self) -> None:
        """Enter borderless desktop fullscreen (SDL_WINDOW_FULLSCREEN_DESKTOP)."""
        self._set_mode((0, 0), _FULLSCREEN_FLAGS)

    def set_exclusive_fullscreen(self) -> None:
        """Enter exclusive fullscreen, requesting a 50 Hz mode if the display lists one."""
        sizes = pygame.display.get_desktop_sizes()
        target = sizes[0] if sizes else (0, 0)
        self._set_mode(target, _FULLSCREEN_FLAGS)
        if target != (0, 0):
            _try_apply_sdl_50hz_mode(target)

    def get_window_size(self) -> tuple[int, int]:
        """Return the current window ``(width, height)`` in pixels."""
        width, height = pygame.display.get_window_size()
        return (int(width), int(height))

    def get_refresh_rate(self) -> int:
        """Return the active display refresh rate in Hz, or ``0`` if unknown."""
        try:
            return max(0, int(pygame.display.get_current_refresh_rate()))
        except pygame.error:
            return 0


def _is_fullscreen_toggle_keydown(event: pygame.event.Event) -> bool:
    key = int(event.key)
    if key == pygame.K_F11:
        return True
    mod = int(getattr(event, "mod", 0))
    return key in _ENTER_KEYS and (mod & pygame.KMOD_ALT) != 0


class WindowController:
    """Stateful windowed/fullscreen controller and pre-router hotkey filter (T13.3, T13.4)."""

    def __init__(
        self,
        initial_size: tuple[int, int],
        initial_mode: FullscreenMode = FullscreenMode.OFF,
        *,
        vsync: bool = True,
        backend: WindowModeBackend | None = None,
    ) -> None:
        self._windowed_size = (max(1, initial_size[0]), max(1, initial_size[1]))
        self._mode = initial_mode
        self._fullscreen_target = (
            initial_mode if initial_mode is not FullscreenMode.OFF else FullscreenMode.DESKTOP
        )
        self._backend: WindowModeBackend = (
            backend if backend is not None else PygameWindowBackend(vsync=vsync)
        )
        self._suppressed_keyups: set[int] = set()

    @property
    def mode(self) -> FullscreenMode:
        """Active window mode."""
        return self._mode

    @property
    def windowed_size(self) -> tuple[int, int]:
        """Last known resizable windowed ``(width, height)``."""
        return self._windowed_size

    def open(self) -> None:
        """Apply the initial display mode."""
        self._apply_mode(self._mode)

    def get_refresh_rate(self) -> int:
        """Return the active display refresh rate in Hz, or ``0`` if unknown."""
        return self._backend.get_refresh_rate()

    def toggle_fullscreen(self) -> FullscreenMode:
        """Toggle between windowed mode and the active fullscreen target."""
        if self._mode is FullscreenMode.OFF:
            cur_w, cur_h = self._backend.get_window_size()
            if cur_w > 0 and cur_h > 0:
                self._windowed_size = (cur_w, cur_h)
            self._mode = self._fullscreen_target
        else:
            self._mode = FullscreenMode.OFF
        self._apply_mode(self._mode)
        return self._mode

    def consume_event(self, event: pygame.event.Event) -> bool:
        """Handle resize and fullscreen hotkeys; return ``True`` if consumed before the router."""
        etype = event.type
        if etype == pygame.KEYDOWN and _is_fullscreen_toggle_keydown(event):
            key = int(event.key)
            self._suppressed_keyups.add(key)
            if not getattr(event, "repeat", 0):
                self.toggle_fullscreen()
            return True
        if etype == pygame.KEYUP:
            key = int(event.key)
            if key in self._suppressed_keyups:
                self._suppressed_keyups.discard(key)
                return True
            return False
        if (
            etype in (pygame.VIDEORESIZE, pygame.WINDOWSIZECHANGED)
            and self._mode is FullscreenMode.OFF
        ):
            cur_w, cur_h = self._backend.get_window_size()
            if cur_w > 0 and cur_h > 0:
                self._windowed_size = (cur_w, cur_h)
        return False

    def _apply_mode(self, mode: FullscreenMode) -> None:
        if mode is FullscreenMode.DESKTOP:
            self._backend.set_desktop_fullscreen()
        elif mode is FullscreenMode.EXCLUSIVE:
            self._backend.set_exclusive_fullscreen()
        else:
            self._backend.set_windowed(self._windowed_size)
