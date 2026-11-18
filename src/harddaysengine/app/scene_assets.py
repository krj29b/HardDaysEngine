"""Runtime asset bundle loader for playable Moonstone scenes (T8.3+)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from harddaysengine.app.intro_player import IntroCinematicPlayer
from harddaysengine.assets.ai_map_rules import parse_ai_map_rules
from harddaysengine.assets.arena import DecodedArena, composite_arena_background, decode_arena
from harddaysengine.assets.audio import DecodedModule, decode_cmp_module
from harddaysengine.assets.cel import DecodedCel, decode_cel
from harddaysengine.assets.fight_palette import FightPaletteTables, parse_fight_palette_tables
from harddaysengine.assets.hit import HitSet, parse_collide_hit
from harddaysengine.assets.hunk import HunkFormatError, parse_hunk_executable
from harddaysengine.assets.map_rules import parse_map_rules
from harddaysengine.assets.mog_code import MogCode
from harddaysengine.assets.palette import RGBColor, default_combat_palette
from harddaysengine.assets.picture import DecodedPicture, decode_picture, decode_picture_pack
from harddaysengine.assets.synth_data import parse_front_end, parse_synth
from harddaysengine.assets.tables import (
    MogRuntimeTables,
    ProgramRuntimeTables,
    load_mog_tables,
    load_program_tables,
)
from harddaysengine.assets.turn_rules import parse_turn_rules
from harddaysengine.render.renderer import CANVAS_WIDTH, PLAYFIELD_HEIGHT
from harddaysengine.render.scene import SceneSprite

if TYPE_CHECKING:
    import numpy as np
    import numpy.typing as npt

    from harddaysengine.app.audio import AudioEngine
    from harddaysengine.assets.adf import DiskSet
    from harddaysengine.sim.ai_map_state import MapAiRules
    from harddaysengine.sim.map_rules import MapRules
    from harddaysengine.sim.turn_rules import TurnRules


_LAIR_ARENAS = ("fol1.t", "gll1.t", "swl1.t", "wal1.t")


@dataclass(slots=True)
class GameAssetBundle:
    """Lazy cache of decoded Amiga pictures, arenas, cels, and audio from ``DiskSet``."""

    disk_set: DiskSet
    pictures: dict[str, DecodedPicture] = field(default_factory=dict)
    arenas: dict[str, DecodedArena] = field(default_factory=dict)
    arena_pictures: dict[str, DecodedPicture] = field(default_factory=dict)
    cels: dict[str, DecodedCel] = field(default_factory=dict)
    _test_pack: tuple[DecodedPicture, ...] | None = None
    _failed_names: set[str] = field(default_factory=set)
    _intro_player: IntroCinematicPlayer | None = None
    _intro_checked: bool = False
    _program_tables: ProgramRuntimeTables | None = None
    _program_tables_checked: bool = False
    _mog_tables: MogRuntimeTables | None = None
    _mog_tables_checked: bool = False
    _mog_s4_data: bytes | None = None
    _fight_palette_checked: bool = False
    _fight_palette_tables: FightPaletteTables | None = None
    _hit_sets: dict[str, HitSet] | None = None
    _hit_sets_checked: bool = False

    def get_intro_player(self) -> IntroCinematicPlayer | None:
        """Lazily build and cache the ``program`` starting cinematic player (§7.4)."""
        if not self._intro_checked:
            self._intro_checked = True
            self._intro_player = IntroCinematicPlayer.try_build(self)
        return self._intro_player

    def _get_test_pack(self) -> tuple[DecodedPicture, ...]:
        if self._test_pack is None:
            if self.disk_set.has_file("test"):
                self._test_pack = tuple(decode_picture_pack(self.disk_set.read_file("test")))
            else:
                self._test_pack = ()
        return self._test_pack

    def get_test_picture(self, index: int) -> DecodedPicture | None:
        """Return picture ``index`` from the ``test`` pack if present."""
        pack = self._get_test_pack()
        if 0 <= index < len(pack):
            return pack[index]
        return None

    def get_picture(self, *names: str) -> DecodedPicture | None:
        """Load and cache the first matching picture from ``names``."""
        for name in names:
            key = name.casefold()
            if key in self.pictures:
                return self.pictures[key]
            if key in self._failed_names:
                continue
            if not self.disk_set.has_file(name):
                self._failed_names.add(key)
                continue
            pic = decode_picture(self.disk_set.read_file(name))
            self.pictures[key] = pic
            return pic
        return None

    def get_cel(self, *names: str) -> DecodedCel | None:
        """Load and cache the first matching cel from ``names``."""
        for name in names:
            key = name.casefold()
            if key in self.cels:
                return self.cels[key]
            if key in self._failed_names:
                continue
            if not self.disk_set.has_file(name):
                self._failed_names.add(key)
                continue
            cel = decode_cel(self.disk_set.read_file(name))
            self.cels[key] = cel
            return cel
        return None

    def get_arena(self, name: str) -> tuple[DecodedArena, DecodedPicture] | None:
        """Load, composite, and cache a ``.t`` arena background."""
        key = name.casefold()
        if key in self.arenas and key in self.arena_pictures:
            return self.arenas[key], self.arena_pictures[key]
        if key in self._failed_names:
            return None
        if not self.disk_set.has_file(name):
            self._failed_names.add(key)
            return None
        test_pack = self._get_test_pack()
        if not test_pack:
            self._failed_names.add(key)
            return None
        entry = self.disk_set.get_entry(name)
        arena = decode_arena(entry.name, self.disk_set.read_file(name))
        bg_pic = composite_arena_background(arena, test_pack)
        self.arenas[key] = arena
        self.arena_pictures[key] = bg_pic
        return arena, bg_pic

    def get_combat_arena(
        self, lair_index: int
    ) -> tuple[DecodedArena, npt.NDArray[np.uint8], tuple[RGBColor, ...]] | None:
        """Return ``(arena, background_pixels, palette)`` for ``lair_index``."""
        preferred = _LAIR_ARENAS[lair_index % len(_LAIR_ARENAS)]
        candidates = (preferred, *_LAIR_ARENAS)
        for candidate in candidates:
            loaded = self.get_arena(candidate)
            if loaded is not None:
                arena, bg_pic = loaded
                return arena, bg_pic.pixels[:PLAYFIELD_HEIGHT, :CANVAS_WIDTH], tuple(bg_pic.palette)
        return None

    def attach_synth(self, audio: AudioEngine) -> None:
        """Enter ``mog`` on ``audio``: its synth tables and front end (ARCHITECTURE.md §6.2)."""
        if not self.disk_set.has_file("mog"):
            return
        mog = MogCode(parse_hunk_executable("mog", self.disk_set.read_file("mog")))
        audio.attach_synth(parse_synth(mog), parse_front_end(mog))

    def load_title_music(self) -> DecodedModule | None:
        """Return unpacked ProTracker ``music.cmp`` module if present on disk."""
        if not self.disk_set.has_file("music.cmp"):
            return None
        return decode_cmp_module("music.cmp", self.disk_set.read_file("music.cmp"))

    def load_victory_music(self) -> DecodedModule | None:
        """Return unpacked ProTracker ``vmusic.cmp`` module if present on disk."""
        if not self.disk_set.has_file("vmusic.cmp"):
            return None
        return decode_cmp_module("vmusic.cmp", self.disk_set.read_file("vmusic.cmp"))

    def get_program_tables(self) -> ProgramRuntimeTables | None:
        """Load and cache runtime tables from ``program`` if present (ARCHITECTURE.md §7)."""
        if not self._program_tables_checked:
            self._program_tables_checked = True
            if self.disk_set.has_file("program"):
                try:
                    exe = parse_hunk_executable("program", self.disk_set.read_file("program"))
                    self._program_tables = load_program_tables(exe)
                except HunkFormatError:
                    self._program_tables = None
        return self._program_tables

    def get_mog_tables(self) -> MogRuntimeTables | None:
        """Load and cache runtime tables from ``mog`` if present (ARCHITECTURE.md §5, §6.2)."""
        if not self._mog_tables_checked:
            self._mog_tables_checked = True
            if self.disk_set.has_file("mog"):
                try:
                    exe = parse_hunk_executable("mog", self.disk_set.read_file("mog"))
                    self._mog_tables = load_mog_tables(exe)
                    if len(exe.sections) > 4:
                        self._mog_s4_data = exe.sections[4].data
                except HunkFormatError:
                    self._mog_tables = None
                    self._mog_s4_data = None
        return self._mog_tables

    def get_fight_palette_tables(self) -> FightPaletteTables | None:
        """Load and cache the fight colour rows from ``mog`` (ARCHITECTURE.md §10.15).

        Returns ``None`` when the disk set has no ``mog``; an unsupported ``mog`` raises.
        """
        if not self._fight_palette_checked:
            self._fight_palette_checked = True
            if self.disk_set.has_file("mog"):
                exe = parse_hunk_executable("mog", self.disk_set.read_file("mog"))
                self._fight_palette_tables = parse_fight_palette_tables(exe)
        return self._fight_palette_tables

    def get_mog_s4_bytes(self) -> bytes | None:
        """Return cached raw `mog` `S_4` data hunk bytes if `mog` is present."""
        self.get_mog_tables()
        return self._mog_s4_data

    def get_hit_sets(self) -> dict[str, HitSet] | None:
        """Load and cache weapon hitboxes from ``collide.hit`` if present (ARCHITECTURE.md §4.2)."""
        if not self._hit_sets_checked:
            self._hit_sets_checked = True
            if self.disk_set.has_file("collide.hit"):
                self._hit_sets = parse_collide_hit(self.disk_set.read_file("collide.hit"))
        return self._hit_sets

    def get_blood_cel(self) -> DecodedCel | None:
        """Load and cache the ``blo.cel`` blood/gore sprite sheet if present."""
        return self.get_cel("blo.cel")

    def get_turn_rules(self) -> TurnRules:
        """Decode calendar, gift and flight tables from the original mog (§11.19)."""
        return parse_turn_rules(parse_hunk_executable("mog", self.disk_set.read_file("mog")))

    def get_ai_map_rules(self) -> MapAiRules:
        """Decode the original computer-map decision operands (§11.20)."""
        return parse_ai_map_rules(parse_hunk_executable("mog", self.disk_set.read_file("mog")))

    def get_map_rules(self) -> MapRules:
        """Load original map records, grids and exact mi.c dimensions (§11.18)."""
        cel = self.get_cel("mi.c")
        if cel is None:
            raise ValueError("original mi.c is required for the map")
        executable = parse_hunk_executable("mog", self.disk_set.read_file("mog"))
        return parse_map_rules(executable, tuple((f.meta.width, f.meta.height) for f in cel.frames))


def make_sprite(
    cel: DecodedCel,
    frame_index: int,
    *,
    x: int,
    y: int,
    z: int = 0,
    dx: int = 0,
    dy: int = 0,
    flipped: bool = False,
    layer: int = 2,
    order: int = 0,
    top_left: bool = False,
) -> SceneSprite | None:
    """Build a :class:`SceneSprite` from ``cel`` at ``frame_index``."""
    if not cel.frames:
        return None
    idx = frame_index % len(cel.frames)
    frame = cel.frames[idx]
    clamped_x = x if top_left else max(0, min(CANVAS_WIDTH - 1, x))
    clamped_y = y if top_left else max(0, min(PLAYFIELD_HEIGHT - 1, y))
    return SceneSprite(
        frame=frame,
        x=clamped_x,
        y=clamped_y,
        z=z,
        dx=dx,
        dy=dy,
        layer=layer,
        order=order,
        mirrored=flipped,
        top_left=top_left,
    )


def _fade_palette(
    palette: tuple[RGBColor, ...],
    local_tick: int,
    phase_len: int,
    fade_ticks: int = 16,
) -> tuple[RGBColor, ...]:
    if local_tick < fade_ticks:
        level = local_tick
    elif local_tick >= phase_len - fade_ticks:
        level = max(0, phase_len - 1 - local_tick)
    else:
        return palette
    return tuple(
        ((r * level) // fade_ticks, (g * level) // fade_ticks, (b * level) // fade_ticks)
        for r, g, b in palette
    )


def _append_cel_sprite(
    sprites: list[SceneSprite],
    bundle: GameAssetBundle,
    cel_name: str,
    frame_idx: int,
    *,
    x: int,
    y: int,
    order: int,
) -> None:
    cel = bundle.get_cel(cel_name)
    if cel is None:
        return
    sp = make_sprite(cel, frame_idx, x=x, y=y, layer=2, order=order)
    if sp is not None:
        sprites.append(sp)


def build_intro_cinematic_frame(
    bundle: GameAssetBundle,
    tick_count: int,
) -> tuple[npt.NDArray[np.uint8] | None, tuple[RGBColor, ...], list[SceneSprite]]:
    """Build background, faded palette, and animated sprites for the 50 Hz start cinematic."""
    player = bundle.get_intro_player()
    if player is not None:
        bg, pal = player.render_tick(bundle, tick_count)
        return bg, pal, []

    cycle_tick = tick_count % 1200
    sprites: list[SceneSprite] = []
    anim_step = tick_count // 5

    if cycle_tick < 160:
        pic = bundle.get_picture("mindscape", "bg1a.PIV")
        if pic is None:
            return None, default_combat_palette(), sprites
        return (
            pic.pixels[:PLAYFIELD_HEIGHT, :CANVAS_WIDTH],
            _fade_palette(pic.palette, cycle_tick, 160),
            sprites,
        )

    local = cycle_tick - 160
    pic = bundle.get_picture("bg1a.PIV", "bg1b.PIV", "bg1c.PIV")
    if pic is None:
        return None, default_combat_palette(), sprites
    _append_cel_sprite(sprites, bundle, "au1.cel", anim_step, x=160, y=148, order=0)
    _append_cel_sprite(sprites, bundle, "li1.cel", anim_step, x=160, y=116, order=1)
    return (
        pic.pixels[:PLAYFIELD_HEIGHT, :CANVAS_WIDTH],
        _fade_palette(pic.palette, local, 1040),
        sprites,
    )
