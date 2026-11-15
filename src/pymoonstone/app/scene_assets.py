"""Runtime asset bundle loader for playable Moonstone scenes (T8.3+)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pymoonstone.app.intro_player import IntroCinematicPlayer
from pymoonstone.assets.arena import DecodedArena, composite_arena_background, decode_arena
from pymoonstone.assets.audio import DecodedModule, decode_cmp_module, decode_sample_bank
from pymoonstone.assets.cel import DecodedCel, decode_cel
from pymoonstone.assets.palette import RGBColor, assemble_combat_palette, default_combat_palette
from pymoonstone.assets.picture import DecodedPicture, decode_picture, decode_picture_pack
from pymoonstone.render.renderer import CANVAS_WIDTH, PLAYFIELD_HEIGHT
from pymoonstone.render.scene import SceneSprite
from pymoonstone.sim.knight_combat import KnightAction
from pymoonstone.sim.monster_ai import MonsterArchetype

if TYPE_CHECKING:
    import numpy as np
    import numpy.typing as npt

    from pymoonstone.app.audio import AudioEngine
    from pymoonstone.assets.adf import DiskSet

_ARCHETYPE_CELS: dict[MonsterArchetype, tuple[str, ...]] = {
    MonsterArchetype.TROGG_SPEAR: ("TROGGSpear1.CEL", "TROGGSpear2.CEL"),
    MonsterArchetype.TROGG_AXE: ("TROGGAxe1.CEL", "TROGGAxe2.CEL"),
    MonsterArchetype.RATMAN: ("RATMEN1.CEL", "RATMEN2.CEL"),
    MonsterArchetype.MUDMAN: ("Mudmen1.CEL", "Mudmen2.CEL"),
    MonsterArchetype.BEASTMAN: ("be1.c", "be2.c"),
    MonsterArchetype.DEMON: ("Demon1.CEL", "Demon2.CEL"),
    MonsterArchetype.TROLL: ("TROLL1.CEL", "TROLL2.CEL"),
    MonsterArchetype.BALOK: ("Balok1.CEL", "Balok2.CEL"),
    MonsterArchetype.DRAGON: ("DRAGON1.CEL", "DRAGON2.CEL"),
    MonsterArchetype.GUARDIAN: ("HE1.ob", "HE2.ob"),
}

_LAIR_ARENAS: tuple[str, ...] = ("fol1.t", "fo1.t", "gl1.t", "fol2.t")

_ACTION_FRAME_OFFSET: dict[KnightAction, int] = {
    KnightAction.IDLE: 0,
    KnightAction.WALK: 1,
    KnightAction.RUN: 2,
    KnightAction.ROLL: 5,
    KnightAction.BLOCK: 6,
    KnightAction.OVERHEAD_SLASH: 7,
    KnightAction.THRUST: 9,
    KnightAction.LOW_SWEEP: 11,
    KnightAction.THROW_SECONDARY: 13,
    KnightAction.HIT_STUN: 14,
    KnightAction.KNOCKDOWN: 15,
    KnightAction.DEATH: 16,
}


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
                pal = assemble_combat_palette(default_combat_palette(), bg_pic.palette)
                return arena, bg_pic.pixels[:PLAYFIELD_HEIGHT, :CANVAS_WIDTH], pal
        return None

    def get_knight_cel(self, knight_index: int) -> DecodedCel | None:
        """Return the knight cel for player ``knight_index`` (0..3)."""
        preferred = f"KN{(knight_index % 4) + 1}.ob"
        return self.get_cel(preferred, "KN4.ob", "KN1.ob")

    def get_monster_cel(self, archetype: MonsterArchetype) -> DecodedCel | None:
        """Return the primary cel for ``archetype``."""
        candidates = _ARCHETYPE_CELS.get(archetype, ("TROLL1.CEL",))
        return self.get_cel(*candidates, "TROLL1.CEL")

    def preload_audio(self, audio: AudioEngine) -> None:
        """Load available ``.a`` sample banks into ``audio.mixer``."""
        for entry in self.disk_set.list_entries():
            if not entry.name.casefold().endswith(".a"):
                continue
            try:
                bank = decode_sample_bank(entry.name, self.disk_set.read_file(entry.name))
            except ValueError:
                continue
            if len(bank.samples_pcm8) > 0:
                audio.mixer.register_sample_bank(bank)

    def load_title_music(self) -> DecodedModule | None:
        """Return unpacked ProTracker ``music.cmp`` module if present on disk."""
        if not self.disk_set.has_file("music.cmp"):
            return None
        return decode_cmp_module("music.cmp", self.disk_set.read_file("music.cmp"))


def select_knight_frame(cel: DecodedCel, action: KnightAction, tick_count: int) -> int:
    """Pick a valid cel frame index for ``action`` and ``tick_count``."""
    if not cel.frames:
        return 0
    base = _ACTION_FRAME_OFFSET.get(action, 0)
    anim = (tick_count // 6) % 3 if action in (KnightAction.WALK, KnightAction.RUN) else 0
    return (base + anim) % len(cel.frames)


def select_monster_frame(cel: DecodedCel, tick_count: int, attacking: bool) -> int:
    """Pick a valid monster cel frame index."""
    if not cel.frames:
        return 0
    step = (tick_count // 6) % min(len(cel.frames), 4)
    offset = min(len(cel.frames) - 1, 2) if attacking else 0
    return (offset + step) % len(cel.frames)


def make_sprite(
    cel: DecodedCel,
    frame_index: int,
    *,
    x: int,
    y: int,
    z: int = 0,
    flipped: bool = False,
    layer: int = 2,
    order: int = 0,
) -> SceneSprite | None:
    """Build a :class:`SceneSprite` from ``cel`` at ``frame_index``."""
    if not cel.frames:
        return None
    idx = frame_index % len(cel.frames)
    frame = cel.frames[idx]
    clamped_x = max(0, min(CANVAS_WIDTH - 1, x))
    clamped_y = max(0, min(PLAYFIELD_HEIGHT - 1, y))
    return SceneSprite(
        frame=frame,
        x=clamped_x,
        y=clamped_y,
        z=z,
        layer=layer,
        order=order,
        mirrored=flipped,
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
