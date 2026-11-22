"""Keyboard controls for the opt-in campaign panel (§13.11)."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

import pygame

if TYPE_CHECKING:
    from pathlib import Path


class SaveAction(StrEnum):
    SAVE = "Save game"
    LOAD = "Load game"


class CampaignMenu:
    """Host-only modal state and action requests."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.actions: tuple[SaveAction, ...] = ()
        self.selected = 0
        self.status = ""
        self.request: SaveAction | None = None
        self._suppressed: set[int] = set()

    @property
    def is_open(self) -> bool:
        return bool(self.actions)

    def close(self) -> None:
        """Dismiss the panel while retaining key-release suppression."""
        self.actions = ()

    def rows(self) -> tuple[tuple[str, str], ...]:
        """Show the currently available actions for the selected scene."""
        return tuple((action.value, "") for action in self.actions)

    def consume_event(self, event: pygame.event.Event, *, available: bool, can_save: bool) -> bool:
        """Route panel presses and their releases before gameplay sees them."""
        if self.path is None or event.type not in (pygame.KEYDOWN, pygame.KEYUP):
            return False
        key = int(event.key)
        if event.type == pygame.KEYUP:
            captured = key in self._suppressed
            self._suppressed.discard(key)
            return captured
        return self._consume_keydown(event, key, available=available, can_save=can_save)

    def _consume_keydown(
        self, event: pygame.event.Event, key: int, *, available: bool, can_save: bool
    ) -> bool:
        repeat = bool(getattr(event, "repeat", False))
        if repeat and key in self._suppressed:
            return True
        if key == pygame.K_F8 and available:
            if not repeat:
                self._suppressed.add(key)
                self._toggle(can_save)
            return True
        if not self.is_open or self._window_shortcut(event, key):
            return False
        if not repeat:
            self._suppressed.add(key)
            self._navigate(key)
        return True

    def _toggle(self, can_save: bool) -> None:
        if self.is_open:
            self.close()
            return
        self.actions = (SaveAction.SAVE, SaveAction.LOAD) if can_save else (SaveAction.LOAD,)
        self.selected, self.status = 0, ""

    @staticmethod
    def _window_shortcut(event: pygame.event.Event, key: int) -> bool:
        return key == pygame.K_F11 or (
            key in (pygame.K_RETURN, pygame.K_KP_ENTER)
            and bool(int(getattr(event, "mod", 0)) & pygame.KMOD_ALT)
        )

    def _navigate(self, key: int) -> None:
        if key == pygame.K_ESCAPE:
            self.close()
        elif key in (pygame.K_UP, pygame.K_DOWN):
            step = -1 if key == pygame.K_UP else 1
            self.selected = (self.selected + step) % len(self.actions)
        elif key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self.request = self.actions[self.selected]
