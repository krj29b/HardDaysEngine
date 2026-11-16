"""4-player keyboard/gamepad input router and replay serialization (T8.3)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pygame

from pymoonstone.sim.fight_moves import DOWN, FIRE, LEFT, RIGHT, UP
from pymoonstone.sim.types import KnightId

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

REPLAY_FORMAT_VERSION = 1
AXIS_DEADZONE = 0.5


@dataclass(slots=True, frozen=True)
class PlayerInput:
    """Normalized 1-tick input state for a single player (combat + menu/overworld)."""

    dx: int = 0
    dy: int = 0
    fire: bool = False
    secondary: bool = False
    weapon_switch: bool = False
    run: bool = False
    confirm: bool = False
    pause: bool = False
    skip_intro: bool = False

    def normalized(self) -> PlayerInput:
        """Clamp ``dx`` and ``dy`` to ``-1..1``."""
        ndx = -1 if self.dx < 0 else (1 if self.dx > 0 else 0)
        ndy = -1 if self.dy < 0 else (1 if self.dy > 0 else 0)
        return PlayerInput(
            dx=ndx,
            dy=ndy,
            fire=self.fire,
            secondary=self.secondary,
            weapon_switch=self.weapon_switch,
            run=self.run,
            confirm=self.confirm,
            pause=self.pause,
            skip_intro=self.skip_intro,
        )

    def to_joystick_word(self) -> int:
        """Original joystick word, with opposite directions excluded (ARCHITECTURE.md §10.19)."""
        horizontal = RIGHT if self.dx > 0 else (LEFT if self.dx < 0 else 0)
        vertical = DOWN if self.dy > 0 else (UP if self.dy < 0 else 0)
        return horizontal | vertical | (FIRE if self.fire else 0)

    def to_dict(self) -> dict[str, int | bool]:
        """Serialize to a JSON-safe dictionary."""
        norm = self.normalized()
        return {
            "dx": norm.dx,
            "dy": norm.dy,
            "fire": norm.fire,
            "secondary": norm.secondary,
            "weapon_switch": norm.weapon_switch,
            "run": norm.run,
            "confirm": norm.confirm,
            "pause": norm.pause,
            "skip_intro": norm.skip_intro,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PlayerInput:
        """Deserialize a :class:`PlayerInput` from a mapping."""
        return cls(
            dx=int(data.get("dx", 0)),
            dy=int(data.get("dy", 0)),
            fire=bool(data.get("fire", False)),
            secondary=bool(data.get("secondary", False)),
            weapon_switch=bool(data.get("weapon_switch", False)),
            run=bool(data.get("run", False)),
            confirm=bool(data.get("confirm", False)),
            pause=bool(data.get("pause", False)),
            skip_intro=bool(data.get("skip_intro", False)),
        ).normalized()


@dataclass(slots=True, frozen=True)
class InputFrame:
    """Complete 4-player input snapshot for one 50 Hz PAL VBL tick."""

    players: dict[KnightId, PlayerInput] = field(default_factory=dict)
    quit_requested: bool = False

    def for_player(self, knight_id: KnightId) -> PlayerInput:
        """Return the input for ``knight_id``, defaulting to neutral."""
        return self.players.get(knight_id, PlayerInput())

    def to_dict(self) -> dict[str, Any]:
        """Serialize this frame to a JSON-safe dictionary."""
        return {
            "players": {str(int(kid)): inp.to_dict() for kid, inp in sorted(self.players.items())},
            "quit_requested": self.quit_requested,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> InputFrame:
        """Deserialize an :class:`InputFrame` from a mapping."""
        raw_players = data.get("players", {})
        parsed: dict[KnightId, PlayerInput] = {}
        if isinstance(raw_players, Mapping):
            for key, value in raw_players.items():
                kid = KnightId(int(key))
                if isinstance(value, Mapping):
                    parsed[kid] = PlayerInput.from_dict(value)
        return cls(players=parsed, quit_requested=bool(data.get("quit_requested", False)))


@dataclass(slots=True, frozen=True)
class ReplayRecording:
    """Deterministic seed, roster, and per-tick :class:`InputFrame` sequence."""

    seed: int
    players: tuple[KnightId, ...]
    frames: tuple[InputFrame, ...]

    def to_dict(self) -> dict[str, Any]:
        """Serialize the recording to a JSON-safe dictionary."""
        return {
            "version": REPLAY_FORMAT_VERSION,
            "seed": self.seed,
            "players": [int(kid) for kid in self.players],
            "frames": [frame.to_dict() for frame in self.frames],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ReplayRecording:
        """Deserialize a :class:`ReplayRecording` from a mapping."""
        version = int(data.get("version", REPLAY_FORMAT_VERSION))
        if version != REPLAY_FORMAT_VERSION:
            msg = f"Unsupported replay version: {version}"
            raise ValueError(msg)
        seed = int(data["seed"])
        raw_players = data.get("players", [int(KnightId.RED)])
        players = tuple(KnightId(int(k)) for k in raw_players)
        raw_frames = data.get("frames", [])
        frames = tuple(InputFrame.from_dict(f) for f in raw_frames)
        return cls(seed=seed, players=players, frames=frames)


def save_replay(path: Path, recording: ReplayRecording) -> None:
    """Write ``recording`` to ``path`` as deterministic JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(recording.to_dict(), indent=2, sort_keys=True), encoding="utf-8")


def load_replay(path: Path) -> ReplayRecording:
    """Read and validate a :class:`ReplayRecording` from ``path``."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        msg = "Replay JSON root must be an object"
        raise ValueError(msg)
    return ReplayRecording.from_dict(data)


@dataclass(slots=True, frozen=True)
class KeyBindings:
    """Keyboard scancode bindings for a single player."""

    left: int
    right: int
    up: int
    down: int
    fire: int
    secondary: int
    weapon_switch: int
    run: int
    confirm: int
    pause: int


DEFAULT_KEY_BINDINGS: dict[KnightId, KeyBindings] = {
    KnightId.RED: KeyBindings(
        left=pygame.K_LEFT,
        right=pygame.K_RIGHT,
        up=pygame.K_UP,
        down=pygame.K_DOWN,
        fire=pygame.K_SPACE,
        secondary=pygame.K_LCTRL,
        weapon_switch=pygame.K_TAB,
        run=pygame.K_LSHIFT,
        confirm=pygame.K_RETURN,
        pause=pygame.K_p,
    ),
    KnightId.BLUE: KeyBindings(
        left=pygame.K_a,
        right=pygame.K_d,
        up=pygame.K_w,
        down=pygame.K_s,
        fire=pygame.K_f,
        secondary=pygame.K_g,
        weapon_switch=pygame.K_r,
        run=pygame.K_q,
        confirm=pygame.K_e,
        pause=pygame.K_F1,
    ),
    KnightId.GREEN: KeyBindings(
        left=pygame.K_j,
        right=pygame.K_l,
        up=pygame.K_i,
        down=pygame.K_k,
        fire=pygame.K_u,
        secondary=pygame.K_o,
        weapon_switch=pygame.K_y,
        run=pygame.K_h,
        confirm=pygame.K_n,
        pause=pygame.K_F2,
    ),
    KnightId.YELLOW: KeyBindings(
        left=pygame.K_KP4,
        right=pygame.K_KP6,
        up=pygame.K_KP8,
        down=pygame.K_KP2,
        fire=pygame.K_KP0,
        secondary=pygame.K_KP_PERIOD,
        weapon_switch=pygame.K_KP7,
        run=pygame.K_KP5,
        confirm=pygame.K_KP_ENTER,
        pause=pygame.K_F3,
    ),
}


@dataclass(slots=True)
class _PadState:
    """Mutable per-gamepad state updated from pygame joystick events."""

    axis_x: float = 0.0
    axis_y: float = 0.0
    hat_x: int = 0
    hat_y: int = 0
    buttons: set[int] = field(default_factory=set)
    edge_buttons: set[int] = field(default_factory=set)


class InputRouter:
    """4-player keyboard and ``pygame.joystick`` router producing :class:`InputFrame`."""

    def __init__(
        self,
        bindings: Mapping[KnightId, KeyBindings] | None = None,
        *,
        init_joysticks: bool = False,
    ) -> None:
        self._bindings: dict[KnightId, KeyBindings] = dict(
            bindings if bindings is not None else DEFAULT_KEY_BINDINGS
        )
        self._held_keys: set[int] = set()
        self._edge_keys: set[int] = set()
        self._pads: dict[int, _PadState] = {}
        self._injected: dict[KnightId, PlayerInput] = {}
        self._quit_requested = False

        if init_joysticks:
            self.init_joysticks()

    def init_joysticks(self) -> int:
        """Initialize ``pygame.joystick`` if available; return detected count."""
        try:
            if not pygame.joystick.get_init():
                pygame.joystick.init()
            count = pygame.joystick.get_count()
            for idx in range(min(4, count)):
                joy = pygame.joystick.Joystick(idx)
                if not joy.get_init():
                    joy.init()
                self._pads.setdefault(idx, _PadState())
            return count
        except pygame.error:
            return 0

    def set_player_input(self, knight_id: KnightId, player_input: PlayerInput) -> None:
        """Inject a deterministic :class:`PlayerInput` override for ``knight_id``."""
        self._injected[knight_id] = player_input.normalized()

    def clear_injected(self) -> None:
        """Remove all programmatic player input overrides."""
        self._injected.clear()

    def update_from_key_sequence(self, pressed: Sequence[bool] | Mapping[int, bool]) -> None:
        """Update held keyboard state from a sequence or mapping of key states."""
        self._held_keys.clear()
        if isinstance(pressed, dict):
            for key, is_down in pressed.items():
                if is_down:
                    self._held_keys.add(key)
            return
        for bind in self._bindings.values():
            for code in (
                bind.left,
                bind.right,
                bind.up,
                bind.down,
                bind.fire,
                bind.secondary,
                bind.weapon_switch,
                bind.run,
                bind.confirm,
                bind.pause,
            ):
                if 0 <= code < len(pressed) and pressed[code]:
                    self._held_keys.add(code)

    def process_pygame_event(self, event: pygame.event.Event) -> None:
        """Process a single ``pygame.event.Event`` into held and edge input state."""
        etype = event.type
        if etype == pygame.QUIT:
            self._quit_requested = True
        elif etype == pygame.KEYDOWN:
            key = int(event.key)
            self._held_keys.add(key)
            self._edge_keys.add(key)
        elif etype == pygame.KEYUP:
            self._held_keys.discard(int(event.key))
        elif etype in (
            pygame.JOYAXISMOTION,
            pygame.JOYHATMOTION,
            pygame.JOYBUTTONDOWN,
            pygame.JOYBUTTONUP,
        ):
            self._process_joystick_event(event)

    def _process_joystick_event(self, event: pygame.event.Event) -> None:
        joy_id = int(getattr(event, "joy", getattr(event, "instance_id", 0)))
        if not 0 <= joy_id < 4:
            return
        pad = self._pads.setdefault(joy_id, _PadState())
        if event.type == pygame.JOYAXISMOTION:
            if event.axis == 0:
                pad.axis_x = float(event.value)
            elif event.axis == 1:
                pad.axis_y = float(event.value)
        elif event.type == pygame.JOYHATMOTION:
            hx, hy = event.value
            pad.hat_x = int(hx)
            pad.hat_y = -int(hy)
        elif event.type == pygame.JOYBUTTONDOWN:
            btn = int(event.button)
            pad.buttons.add(btn)
            pad.edge_buttons.add(btn)
        elif event.type == pygame.JOYBUTTONUP:
            pad.buttons.discard(int(event.button))

    def _sample_keyboard(self, bind: KeyBindings) -> PlayerInput:
        dx = (1 if bind.right in self._held_keys else 0) - (
            1 if bind.left in self._held_keys else 0
        )
        dy = (1 if bind.down in self._held_keys else 0) - (1 if bind.up in self._held_keys else 0)
        skip_intro = (
            (bind.confirm in self._edge_keys)
            or (bind.fire in self._edge_keys)
            or (pygame.K_ESCAPE in self._edge_keys)
            or (pygame.K_RETURN in self._edge_keys)
            or (pygame.K_SPACE in self._edge_keys)
        )
        return PlayerInput(
            dx=dx,
            dy=dy,
            fire=bind.fire in self._held_keys,
            secondary=bind.secondary in self._held_keys,
            weapon_switch=bind.weapon_switch in self._edge_keys,
            run=bind.run in self._held_keys,
            confirm=(bind.confirm in self._edge_keys) or (bind.fire in self._edge_keys),
            pause=bind.pause in self._edge_keys,
            skip_intro=skip_intro,
        )

    def _sample_pad(self, pad: _PadState) -> PlayerInput:
        ax_dx = 1 if pad.axis_x >= AXIS_DEADZONE else (-1 if pad.axis_x <= -AXIS_DEADZONE else 0)
        ax_dy = 1 if pad.axis_y >= AXIS_DEADZONE else (-1 if pad.axis_y <= -AXIS_DEADZONE else 0)
        dx = pad.hat_x if pad.hat_x != 0 else ax_dx
        dy = pad.hat_y if pad.hat_y != 0 else ax_dy
        button_a = 0 in pad.edge_buttons
        start_btn = 7 in pad.edge_buttons
        return PlayerInput(
            dx=dx,
            dy=dy,
            fire=0 in pad.buttons,
            secondary=1 in pad.buttons,
            weapon_switch=2 in pad.edge_buttons,
            run=3 in pad.buttons,
            confirm=button_a or start_btn,
            pause=start_btn,
            skip_intro=button_a or start_btn,
        )

    def _merge_inputs(self, kb: PlayerInput, pad: PlayerInput) -> PlayerInput:
        return PlayerInput(
            dx=pad.dx if pad.dx != 0 else kb.dx,
            dy=pad.dy if pad.dy != 0 else kb.dy,
            fire=kb.fire or pad.fire,
            secondary=kb.secondary or pad.secondary,
            weapon_switch=kb.weapon_switch or pad.weapon_switch,
            run=kb.run or pad.run,
            confirm=kb.confirm or pad.confirm,
            pause=kb.pause or pad.pause,
            skip_intro=kb.skip_intro or pad.skip_intro,
        ).normalized()

    def sample_frame(self) -> InputFrame:
        """Sample all 4 player inputs for the current VBL tick and clear edge triggers."""
        players: dict[KnightId, PlayerInput] = {}
        for kid in KnightId:
            if kid in self._injected:
                players[kid] = self._injected[kid]
                continue
            kb = (
                self._sample_keyboard(self._bindings[kid])
                if kid in self._bindings
                else PlayerInput()
            )
            pad_state = self._pads.get(int(kid))
            pad = self._sample_pad(pad_state) if pad_state is not None else PlayerInput()
            players[kid] = self._merge_inputs(kb, pad)

        self._edge_keys.clear()
        for pad_state in self._pads.values():
            pad_state.edge_buttons.clear()
        quit_req = self._quit_requested
        self._quit_requested = False
        return InputFrame(players=players, quit_requested=quit_req)
