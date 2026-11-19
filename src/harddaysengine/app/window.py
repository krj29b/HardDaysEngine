"""Host window and fullscreen mode controller (T13.3, ARCHITECTURE.md §13.7).

Manages resizable windowed, borderless desktop fullscreen, and exclusive fullscreen modes via
``pygame.display.set_mode`` without destroying the OpenGL 3.3 context or textures. Consumes
``F11`` and ``Alt+Enter`` (including their matching ``KEYUP`` events) before ``InputRouter``
sees them so fullscreen toggles never confirm menus and never enter replay recordings.
"""

from __future__ import annotations

from typing import Protocol

import pygame

from harddaysengine.render.presentation import FullscreenMode

_BASE_GL_FLAGS = pygame.OPENGL | pygame.DOUBLEBUF
_WINDOWED_FLAGS = _BASE_GL_FLAGS | pygame.RESIZABLE
_FULLSCREEN_FLAGS = _BASE_GL_FLAGS | pygame.FULLSCREEN
_ENTER_KEYS = frozenset({pygame.K_RETURN, pygame.K_KP_ENTER})


class WindowModeBackend(Protocol):
    """Minimal display-mode interface used by :class:`WindowController`."""

    def set_windowed(self, size: tuple[int, int]) -> None:
        """Enter resizable OpenGL windowed mode at ``size``."""

    def set_desktop_fullscreen(self) -> None:
        """Enter borderless desktop fullscreen OpenGL mode."""

    def set_exclusive_fullscreen(self) -> None:
        """Enter exclusive fullscreen OpenGL mode."""

    def get_window_size(self) -> tuple[int, int]:
        """Return the current window ``(width, height)`` in pixels."""


class PygameWindowBackend:
    """``pygame-ce`` OpenGL 3.3 display-mode backend preserving the active GL context."""

    def set_windowed(self, size: tuple[int, int]) -> None:
        """Enter resizable OpenGL windowed mode at ``size``."""
        pygame.display.set_mode(size, _WINDOWED_FLAGS)

    def set_desktop_fullscreen(self) -> None:
        """Enter borderless desktop fullscreen (SDL_WINDOW_FULLSCREEN_DESKTOP)."""
        pygame.display.set_mode((0, 0), _FULLSCREEN_FLAGS)

    def set_exclusive_fullscreen(self) -> None:
        """Enter exclusive fullscreen at the primary desktop resolution."""
        sizes = pygame.display.get_desktop_sizes()
        target = sizes[0] if sizes else (0, 0)
        pygame.display.set_mode(target, _FULLSCREEN_FLAGS)

    def get_window_size(self) -> tuple[int, int]:
        """Return the current window ``(width, height)`` in pixels."""
        width, height = pygame.display.get_window_size()
        return (int(width), int(height))


def _is_fullscreen_toggle_keydown(event: pygame.event.Event) -> bool:
    key = int(event.key)
    if key == pygame.K_F11:
        return True
    mod = int(getattr(event, "mod", 0))
    return key in _ENTER_KEYS and (mod & pygame.KMOD_ALT) != 0


class WindowController:
    """Stateful windowed/fullscreen controller and pre-router hotkey filter (T13.3)."""

    def __init__(
        self,
        initial_size: tuple[int, int],
        initial_mode: FullscreenMode = FullscreenMode.OFF,
        *,
        backend: WindowModeBackend | None = None,
    ) -> None:
        self._windowed_size = (max(1, initial_size[0]), max(1, initial_size[1]))
        self._mode = initial_mode
        self._fullscreen_target = (
            initial_mode if initial_mode is not FullscreenMode.OFF else FullscreenMode.DESKTOP
        )
        self._backend: WindowModeBackend = backend if backend is not None else PygameWindowBackend()
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
