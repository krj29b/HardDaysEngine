# Input Router and SDL Input Diagnostics

This document describes:

1. **The current gameplay input router** (`src/harddaysengine/app/input.py`) used by the 50 Hz
   simulation and replay system.
2. **The standalone SDL input diagnostic tool** (`harddaysengine-input-diagnostics`) introduced
   in Phase 16 to inspect attached devices and capture reproducible keyboard, mouse, raw joystick,
   and mapped SDL game-controller evidence.

Normative specifications live in `docs/ARCHITECTURE.md` (§13.8 and §13.12) and architectural
decisions in `docs/DECISIONS.md` (ADR-059 and ADR-065).

---

## 1. Gameplay Input Router (`app/input.py`)

### 1.1 Responsibilities and Boundaries

`InputRouter` translates live `pygame` events into deterministic per-tick `InputFrame` snapshots
consumed by `GameSession.step_tick` and serialized by `--record` / `--replay`.

- **Fixed-rate sampling:** Each 50 Hz PAL VBL tick calls `InputRouter.sample_frame()`, which
  produces one `InputFrame` containing up to four `PlayerInput` entries (`KnightId.RED`, `BLUE`,
  `GREEN`, `YELLOW`), a `quit_requested` flag, and a single translated map/menu `key` code.
- **Pre-router host hotkeys:** Host-only presentation and utility controls are intercepted by
  `WindowController` and overlay panels *before* `InputRouter.process_pygame_event()` so they
  never affect simulation state or replay recordings:
  - `F8` — opt-in campaign save/load panel (`[gameplay] save_load = true`, ADR-063)
  - `F9` / `Shift+F9` — cycle presentation filter preset (ADR-053)
  - `F10` — live display preview and CRT tuning panel (ADR-062)
  - `F11` / `Alt+Enter` — fullscreen toggle (T13.3)
- **Original Amiga joystick word:** `PlayerInput.to_joystick_word()` packs directional and fire
  state into the original bitmask (`RIGHT`, `LEFT`, `DOWN`, `UP`, `FIRE`) with opposite directions
  excluded (`ARCHITECTURE.md` §10.19).

### 1.2 Data Model and Replay Serialization

| Type | Module | Role |
|---|---|---|
| `PlayerInput` | `app/input.py` | Frozen per-player 1-tick state: `dx`, `dy` (`-1..1`), `fire`, `secondary`, `weapon_switch`, `run`, `confirm`, `pause`, `skip_intro`. |
| `InputFrame` | `app/input.py` | Complete 4-player snapshot for one 50 Hz VBL tick plus `quit_requested` and `key`. |
| `ReplayRecording` | `app/input.py` | Versioned JSON replay (`version: 1` seed-based; `version: 2` starting from `start_checkpoint`). |

### 1.3 Default Keyboard Bindings (`DEFAULT_KEY_BINDINGS`)

| Action | Player 1 (`RED`) | Player 2 (`BLUE`) | Player 3 (`GREEN`) | Player 4 (`YELLOW`) |
|---|---|---|---|---|
| Left / Right | `Left` / `Right` | `A` / `D` | `J` / `L` | `KP4` / `KP6` |
| Up / Down | `Up` / `Down` | `W` / `S` | `I` / `K` | `KP8` / `KP2` |
| Fire | `Space` | `F` | `U` | `KP0` |
| Secondary | `Left Ctrl` | `G` | `O` | `KP.` |
| Weapon switch | `Tab` | `R` | `Y` | `KP7` |
| Run | `Left Shift` | `Q` | `H` | `KP5` |
| Confirm | `Return` | `E` | `N` | `KP Enter` |
| Pause | `P` | `F1` | `F2` | `F3` |

In addition, `sample_frame()` extracts one edge-triggered character code for overworld/menu
shortcuts: digits `1`–`9`, `Space`, and uppercase `E` / `Q`.

### 1.4 Gamepad Device Lifecycle and Mapping (ADR-059)

- **Startup and hotplug:** `InputRouter.init_joysticks()` opens up to 4 connected `pygame.joystick`
  devices in device order. Each open `JoystickType` handle is retained in `self._joysticks` so SDL
  continues delivering input events.
- **Stable player slots:** Open handles are keyed by SDL `instance_id` (`Joystick.get_instance_id()`),
  keeping each connected controller in its assigned player slot `0..3` even if another pad is
  unplugged. `JOYDEVICEADDED` opens a newly attached pad into the first free slot;
  `JOYDEVICEREMOVED` drops the handle and clears all held and edge state for that slot.
- **Raw joystick sampling (`_sample_pad`):**
  - Left stick axes: `axis == 0` (`X`) and `axis == 1` (`Y`) with deadzone `AXIS_DEADZONE = 0.5`.
  - D-pad hat: `JOYHATMOTION` `(hx, hy)` with Y negated (`-hy`, because SDL hat `+1` is up).
    Non-zero hat input overrides the analog stick.
  - Raw buttons (hardcoded indices):
    - Button `0` → `fire` (held), `confirm` (edge), `skip_intro` (edge)
    - Button `1` → `secondary` (held)
    - Button `2` → `weapon_switch` (edge)
    - Button `3` → `run` (held)
    - Button `7` → `pause` (edge), `confirm` (edge), `skip_intro` (edge)
- **Keyboard + gamepad merge (`_merge_inputs`):** Non-zero gamepad directions override keyboard
  directions; boolean actions are OR-combined.

### 1.5 Host Control Modes (ADR-066, §13.13)

`InputRouter` supports configurable host control modes via `[input] mode` in `config.toml` or
`--control-mode` on `harddaysengine` (`CLI > config.toml > auto`):

- **`auto` (default):** Resolved dynamically from the number of currently connected gamepads.
  Selects `shared-pad` whenever exactly **1** gamepad is connected, otherwise `standard`,
  updating automatically if pads are plugged in or removed during a session.
- **`standard` (Mode A):** Static per-slot keyboard and gamepad routing across slots `0..3`.
- **`shared-pad` (Mode B):** Pass-the-pad routing driven by `GameSession.control_targets()`:
  - Outside 2-human duels (menus, overworld, towns, sheets, services, dice, Stonehenge, Wizard,
    Village, Valley, Next Day, Game Over, single-human fights, and meeting protection/loot
    sheets), the active human player is controlled by the connected gamepad merged with Player 1
    keyboard backup (`DEFAULT_KEY_BINDINGS[KnightId.RED]`) plus map shortcuts (`1`–`9`, `E`, `Q`,
    `Space`, `P`).
  - During 2-human duels (`PVP_DUEL` and `Practice`), the duel initiator (`knight_ids[0]`) is
    controlled exclusively by the gamepad and the second player (`knight_ids[1]`) is controlled
    exclusively by keyboard **`WASD` + `Space` (fire)**, with `P` / gamepad Start forwarding
    pause cleanly without input bleed.

Control-mode routing runs entirely above `InputFrame` generation; `sim/`, replay files, and
state hashes are unaffected.

### 1.6 Known Controller Compatibility Gaps in the Current Router

Because `InputRouter` currently reads only raw `pygame.joystick` events and assumes an XInput-style
USB button layout (`axis 0/1`, `hat 0`, buttons `0/1/2/3/7`), several controller standards on the
market can behave inconsistently until a mapped SDL `GameController` layer (with raw fallback) is
added:

| Controller / Standard | Typical Difference vs Current `InputRouter` |
|---|---|
| **Xbox One / Series X\|S over Bluetooth** (Windows WGI / HID, Linux `xpadneo` / `hid-generic`) | Raw button indices often shift compared to USB XInput (for example, `Menu/Start` may land on raw button `6` or `11` instead of `7`, and face buttons may shift due to extra slots or the Share button). |
| **PlayStation DualShock 4 & DualSense (PS5)** (USB & Bluetooth HID) | Raw DirectInput/HID commonly reports `Square=0, Cross=1, Circle=2, Triangle=3` and `Options/Start=9`, breaking `0/1/2/3/7` assumptions. |
| **Nintendo Switch Pro & Joy-Con** (USB & Bluetooth) | D-pad is frequently exposed as 4 separate raw buttons rather than `JOYHATMOTION` (ignored by `_sample_pad`); `Plus/Start` is rarely index `7`; face button labels differ from Xbox. |
| **DirectInput & multi-mode pads** (Logitech F310/F710 `D`-mode, 8BitDo `D`/`Switch`/`macOS` modes) | Switching modes changes GUID, raw button indices (e.g. `A=1`, `Start=9`), and whether the D-pad reports as a hat or buttons. |
| **Retro USB joysticks & Amiga DB9→USB adapters** (Competition Pro USB, Speedlink, Raphnet, 2600-daptor) | Usually unmapped by SDL `GameControllerDB` (`is_controller: false`), often have 1–2 buttons (no button `7`), and report digital directions on `axis 0/1`. Any future mapping update must keep a raw-joystick fallback for these devices. |
| **Arcade / Fightsticks** (Hori, Qanba, Mayflash, Brook) | Physical `LS / DP / RS` mode switches can route the stick to right-stick axes (`axis 2/3`), which are currently ignored; PS-mode sticks often place Start/Options on button `9`. |
| **Steam Input / Steam Deck / Apple MFi / Stadia / Luna** | Cross-platform consistency requires SDL2's mapped `GameController` API (`a`, `b`, `x`, `y`, `start`, `dpad_*`, `leftx`/`lefty`) rather than raw joystick indices. |

---

## 2. Standalone SDL Input Diagnostics (`harddaysengine-input-diagnostics`)

Phase 16 provides an opt-in diagnostic tool to collect reproducible SDL2 metadata and event logs
for investigating controller discrepancies without modifying gameplay mappings or replay files.

### 2.1 Architecture and Isolation (ADR-065)

| Module | Responsibility |
|---|---|
| `src/harddaysengine/cli/input_diagnostics.py` | Console script `harddaysengine-input-diagnostics`, argument parsing/validation, `OSError` exit handling. |
| `src/harddaysengine/app/input_diagnostics.py` | SDL subsystem setup, startup/hotplug device registry, FIFO event loop, record budget enforcement, clean shutdown. |
| `src/harddaysengine/app/input_diag_events.py` | Event normalization across all SDL input categories, JSON value sanitization, deadzone band classification, and single-line terminal formatting. |
| `src/harddaysengine/app/input_diag_report.py` | `MotionCoalescer` (high-rate motion/axis noise reduction) and `JsonlReportWriter` (line-flushed UTF-8 JSON Lines output). |

**Strict isolation guarantees:**
- Never imports or invokes `sim/`, `render/`, `assets/`, `GameSession`, `InputRouter`, `moderngl`,
  `pygame.mixer`, or `.adf` disk loaders.
- Sets `SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS=1` (via `os.environ.setdefault`) before SDL
  initialization so gamepads report events even when the terminal holds focus or no window is open.
- Uses `pygame.display.init()` (without audio or OpenGL) and opens a small plain-SDL software
  capture surface (`320×120`) by default so OS keyboard, mouse, wheel, text, and window-close
  (`QUIT`) events can be captured. Passing `--no-window` skips creating a surface; if display
  initialization fails on a headless host, the tool falls back to `SDL_VIDEODRIVER=dummy` without
  failing and records `capture_window: false` (plus `capture_window_note`) in `session_start`.

### 2.2 Dual-Layer SDL2 Device Discovery and Identity

The tool inspects **both** raw `pygame.joystick` and mapped `pygame._sdl2.controller` (when
available) plus optional `pygame._sdl2.touch` devices:

- **Authoritative key (`instance_id`):** Every gamepad is tracked strictly by
  `Joystick.get_instance_id()`. The tool never calls deprecated `Joystick.get_id()`, never uses
  `Controller.id` as an instance ID (in `pygame-ce`, `Controller.id` stores the creation-time
  `device_index`), and never calls `Controller.as_joystick()` after attachment (which raises
  `pygame.error` once an earlier device disconnects and device indices shift).
- **Paired hotplug deduplication:** SDL2 typically emits `ControllerDeviceAdded` followed by
  `JoyDeviceAdded` on attach, and `ControllerDeviceRemoved` followed by `JoyDeviceRemoved` on
  detach. The session opens both `Joystick` and `Controller.from_joystick(joy)` once under
  `instance_id`, emits a single `device_discovered` record, marks the second add event
  `already_open: true`, retains cached identity (`name`, `guid`) across both removal events, and
  emits a single `device_removed` record when the final handle closes.
- **Graceful absence of metadata:** Optional SDL queries (`get_guid`, `get_power_level`,
  `get_mapping`, `get_num*`, `get_axis/hat/button`, `pygame._sdl2.touch`) catch `pygame.error`,
  `AttributeError`, and `NotImplementedError`, omitting unavailable fields instead of failing.
- **Transport clues in `guid`:** Even though `pygame-ce` does not expose separate USB VID/PID
  getters, SDL2 encodes the bus type (typically `03` for USB, `05` for Bluetooth) and hardware
  VID/PID inside the 32-hex-character `guid` recorded on discovery.

### 2.3 CLI Usage and Options

```powershell
uv run harddaysengine-input-diagnostics [options]
```

In the **portable Windows ZIP** (`HardDaysEngine-Windows-x64.zip` / `x86.zip`), run the bundled
wrapper or invoke `HardDaysEngine.exe` directly — no Python installation required:

```powershell
# Double-click input-diagnostics.bat to write input-diagnostics.jsonl beside the exe,
# or pass any diagnostics flags from PowerShell / Command Prompt:
.\input-diagnostics.bat --no-window --output xbox_usb.jsonl
.\HardDaysEngine.exe --input-diagnostics --help
```

| Flag | Default | Description |
|---|---|---|
| `--version` | — | Print `HardDaysEngine <version>` and exit `0`. |
| `-o`, `--output <path>` | disabled | Write a line-flushed UTF-8 JSON Lines (`.jsonl`) report while keeping the live terminal view. File open/write errors print to `stderr` and exit `2`. |
| `--no-window` | off | Run console-only without opening the plain-SDL software capture surface (still captures joystick/controller and queued events). |
| `--duration <seconds>` | unlimited | Stop automatically after `<seconds>` (non-negative finite float). |
| `--max-records <N>` | `10000` | Hard upper bound on total emitted records (positive integer). When reached, stops cleanly and emits `session_end` with `truncated: true`. |
| `--coalesce-ms <ms>` | `50` | Coalescing window in milliseconds for high-rate continuous motion/axes (`>= 0`). |
| `--axis-epsilon <float>` | `0.02` | Raw joystick axis deadband delta for noise coalescing (`>= 0.0`; mapped controller epsilon scales as `round(axis_epsilon * 32768)`). |
| `--raw` | off | Disable motion/axis coalescing (`coalesce_ms=0`, `axis_epsilon=0.0`), still bounded by `--max-records`. |

Stop interactively at any time by closing the capture window (`QUIT`) or pressing `Ctrl+C`
(`SIGINT`).

### 2.4 Normalized Event Categories

Events are drained from `pygame.event.get()` in exact SDL FIFO order. Non-input housekeeping
events (`AUDIODEVICEADDED/REMOVED`, `WINDOWEXPOSED`, `WINDOWRESIZED`, etc.) are ignored.

| Category | Stable `event` names | Key normalized `fields` |
|---|---|---|
| `keyboard` | `key_down`, `key_up`, `text_input`, `text_editing`, `keymap_changed` | `key`, `key_name`, `scancode`, `mod`, `mod_names`, `unicode`, `repeat`, `text`. |
| `mouse` | `mouse_motion`, `mouse_button_down`, `mouse_button_up`, `mouse_wheel` | `pos`, `rel`, `buttons`, `button`, `button_name` (`left`/`middle`/`right`/`x1`/`x2`), `clicks`, `x`, `y`, `flipped`, `which`. |
| `joystick` | `joy_device_added`, `joy_device_removed`, `joy_axis_motion`, `joy_hat_motion`, `joy_ball_motion`, `joy_button_down`, `joy_button_up` | `axis`, `value` (`[-1.0, 1.0]`), `band`, `threshold_crossed`, `hat`, `direction` (`center`/`up`/`up_left`/...), `ball`, `rel`, `button`, `already_open`. |
| `controller` | `controller_device_added`, `controller_device_removed`, `controller_device_remapped`, `controller_axis_motion`, `controller_button_down`, `controller_button_up`, `controller_touchpad_down/motion/up`, `controller_sensor_update` | `axis`, `axis_name` (`leftx`..`triggerright`), `value` (raw s16 `-32768..32767`), `normalized`, `band`, `threshold_crossed`, `button`, `button_name` (`a`..`dpad_right`, `misc1`, `paddle1`..`paddle4`, `touchpad`), `mapping`. |
| `other_input` | `finger_down/motion/up`, `multi_gesture`, `dollar_gesture`, `dollar_record`, or `sdl_event_<type>` | JSON-safe copy of `event.dict` (primitives, lists, string-keyed maps; fallback `repr`). |
| `lifecycle` | `quit`, `window_close`, `window_focus_gained`, `window_focus_lost`, `window_enter`, `window_leave` | Optional `window` ID. |

### 2.5 Motion/Axis Noise Coalescing and Bounded Reports

To prevent analog stick jitter or high-DPI mouse motion from flooding logs while keeping every
meaningful transition:

- **Coalesced streams:** `joy_axis_motion`, `controller_axis_motion`, `mouse_motion`,
  `finger_motion`, `controller_touchpad_motion`, and `controller_sensor_update`.
- **Never coalesced:** all device add/remove/remap, keyboard, mouse button/wheel, joystick
  button/hat/ball, controller button/touchpad down/up, finger down/up, unknown input, and
  lifecycle events.
- **Threshold preservation:** Axis samples are classified into `neg` / `center` / `pos` bands
  matching `InputRouter.AXIS_DEADZONE = 0.5` (`±0.5` for raw joystick axes, `±16384` for mapped
  controller axes). Whenever a sample crosses a band boundary, any pending burst on that stream
  is flushed first and the crossing sample is emitted immediately with `threshold_crossed: true`.
- **Preserved extrema:** Coalesced bursts (`coalesced: true`) record `sample_count`,
  `first_elapsed_ms`, `last_elapsed_ms`, plus `first_value`, `last_value`, `min_value`, and
  `max_value` (or `first_pos`, `last_pos`, and summed `rel` for mouse motion).
- **Causal ordering:** All pending bursts are flushed in `(first_elapsed_ms, stream_key)` order
  before any discrete event, device discovery, or shutdown.
- **Crash-safe flushing:** When `--output` is set, `JsonlReportWriter` writes one UTF-8 LF JSON
  line (`sort_keys=True`) per record and calls `flush()` immediately, so an abrupt interrupt
  retains a complete, parseable partial report.

### 2.6 JSON Lines Schema (`schema_version: 1`) and Terminal Format

Each line in the `.jsonl` file is an independent JSON object with monotonic 1-based `seq`,
`elapsed_ms`, and `record_type`:

1. `session_start` — engine/pygame-ce/SDL/Python/OS versions, `video_driver`, `capture_window`,
   `controller_api_available`, `touch_api_available`, `sdl_env`, and active `config`.
2. `device_discovered` — `origin` (`"startup"` or `"hotplug"`), `instance_id`, `device_index`,
   `name`, `guid`, `is_controller`, `capabilities`, `power_level`, `initial_state`,
   `controller_name`, and `controller_mapping` (when available).
3. `event` — normalized event (single or coalesced burst).
4. `device_removed` — emitted once when a disconnected gamepad's handles are released, repeating
   cached `instance_id`, `name`, and `guid`.
5. `session_end` — `reason` (`quit`, `interrupt`, `duration_elapsed`, or `max_records_reached`),
   `total_raw_events`, `total_emitted_records`, `coalesced_raw_events`, and `truncated`.

Example live terminal output:

```text
HardDaysEngine 0.4.1 input diagnostics | pygame-ce 2.5.8 | SDL 2.32.10 | win32 | window=on
[+12ms] DEVICE+ (startup) id=0 "Xbox Series X Controller" guid=0300... ctrl=yes axes=6 btns=16 hats=1 power=wired
  mapping: a=b0 b=b1 back=b6 ... start=b7 x=b2 y=b3
[+1240ms] #4 JOY id=0 joy_axis_motion axis=0 value=0.610352 band=pos [threshold]
[+1240ms] #5 CTRL id=0 controller_axis_motion axis=0 axis_name=leftx value=20000 normalized=0.61037 band=pos [threshold]
[+1310ms] #6 CTRL id=0 controller_button_down button=6 button_name=start
[+1310ms] #7 JOY id=0 joy_button_down button=7
[+1500ms] #8 JOY id=0 joy_axis_motion axis=0 band=pos first_value=0.612 last_value=0.64 min_value=0.61 max_value=0.645 (x14 coalesced)
[+4200ms] SESSION END reason=quit raw=21 emitted=9 truncated=no
```

---

## 3. Manual Hardware Comparison Procedure (USB vs Bluetooth)

To diagnose a controller that behaves differently over USB and Bluetooth (for example an Xbox One
or Xbox Series X|S gamepad):

1. **Capture USB baseline:**
   - Connect the controller via USB cable (disconnect Bluetooth).
   - Run:
     ```powershell
     uv run harddaysengine-input-diagnostics --no-window --output build/xbox_usb.jsonl
     ```
   - Perform the same short test sequence slowly (about 0.5 s per action):
     1. Left stick: Left → Right → Up → Down → release.
     2. D-pad: Left → Right → Up → Down.
     3. Face buttons in order: `A`, `B`, `X`, `Y`.
     4. Menu/Start, View/Back, Left/Right bumpers, Left/Right triggers.
   - Press `Ctrl+C` to stop cleanly.

2. **Capture Bluetooth run:**
   - Unplug USB, pair/connect the same controller over Bluetooth, and run:
     ```powershell
     uv run harddaysengine-input-diagnostics --no-window --output build/xbox_bt.jsonl
     ```
   - Repeat the exact same input sequence and press `Ctrl+C`.

3. **Compare the two reports:**
   - Check `session_start`: verify `pygame_version`, `sdl_version`, `platform`, and `sdl_env`.
   - Check `device_discovered`: compare `name`, `guid` (note bus prefix `03` USB vs `05`
     Bluetooth), `is_controller`, `capabilities`, `initial_state`, and `controller_mapping`.
   - Check each button/direction pair in the `event` records: compare raw `joy_button_down` /
     `joy_hat_motion` / `joy_axis_motion` indices against mapped `controller_button_down` /
     `controller_axis_motion` names (`a`, `b`, `x`, `y`, `start`, `dpad_*`, `leftx`, `lefty`).
   - When reporting a bug, attach both `.jsonl` files (or paste their `session_start`,
     `device_discovered`, and differing `event` lines) along with your OS version and controller
     model/firmware. Never commit generated files under `build/`.
