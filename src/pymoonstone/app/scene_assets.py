"""Runtime asset bundle loader for playable Moonstone scenes (T8.3+)."""

from __future__ import annotations

import contextlib
import struct
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pymoonstone.app.combat_bridge import compose_knight_pieces, select_knight_body_frame
from pymoonstone.app.intro_player import IntroCinematicPlayer
from pymoonstone.assets.arena import DecodedArena, composite_arena_background, decode_arena
from pymoonstone.assets.audio import (
    DEFAULT_SAMPLE_RATE,
    AudioFormatError,
    DecodedModule,
    decode_cmp_module,
    decode_iff_8svx,
    decode_sample_bank,
)
from pymoonstone.assets.cel import DecodedCel, decode_cel
from pymoonstone.assets.hit import HitSet, parse_collide_hit
from pymoonstone.assets.hunk import HunkFormatError, parse_hunk_executable
from pymoonstone.assets.palette import RGBColor, assemble_combat_palette, default_combat_palette
from pymoonstone.assets.picture import DecodedPicture, decode_picture, decode_picture_pack
from pymoonstone.assets.tables import (
    MogRuntimeTables,
    ProgramRuntimeTables,
    load_mog_tables,
    load_program_tables,
)
from pymoonstone.render.renderer import CANVAS_WIDTH, PLAYFIELD_HEIGHT
from pymoonstone.render.scene import SceneSprite
from pymoonstone.sim.monster_ai import MonsterArchetype
from pymoonstone.sim.overworld import (
    EncounterBand,
    MapSiteInput,
    NodeKind,
    OverworldGraph,
    build_overworld_graph_from_sites,
)

if TYPE_CHECKING:
    import numpy as np
    import numpy.typing as npt

    from pymoonstone.app.audio import AudioEngine
    from pymoonstone.assets.adf import DiskSet
    from pymoonstone.sim.knight_combat import KnightAction

_ARCHETYPE_CELS: dict[MonsterArchetype, tuple[str, ...]] = {
    MonsterArchetype.TROGG_SPEAR: ("TROGGSpear1.CEL", "TROGGSpear2.CEL"),
    MonsterArchetype.TROGG_AXE: ("TROGGAxe1.CEL", "TROGGAxe2.CEL"),
    MonsterArchetype.RATMAN: ("RATMEN1.CEL", "RATMEN2.CEL"),
    MonsterArchetype.MUDMAN: ("Mudmen1.CEL", "Mudmen2.CEL"),
    MonsterArchetype.BEASTMAN: ("be1.c", "be2.c"),
    MonsterArchetype.DEMON: ("Demon1.CEL", "Demon2.CEL", "Demon3.CEL", "Demon4.CEL"),
    MonsterArchetype.TROLL: ("TROLL1.CEL", "TROLL2.CEL"),
    MonsterArchetype.BALOK: ("Balok1.CEL", "Balok2.CEL", "Balok3.CEL"),
    MonsterArchetype.DRAGON: ("DRAGON1.CEL", "DRAGON2.CEL", "DRAGON5.CEL"),
    MonsterArchetype.GUARDIAN: ("HE1.ob", "HE2.ob", "HE3.ob"),
}

_CANONICAL_ROAD_SITES: tuple[tuple[int, int, int], ...] = (
    # Forest (top-left quadrant)
    (0, 48, 36),
    (1, 88, 34),
    (2, 126, 48),
    (3, 54, 72),
    (4, 92, 68),
    # Glen (top-right quadrant)
    (5, 194, 48),
    (6, 232, 34),
    (7, 272, 36),
    (8, 228, 68),
    (9, 266, 72),
    # Swamp (bottom-left quadrant)
    (10, 54, 128),
    (11, 92, 132),
    (12, 48, 164),
    (13, 88, 166),
    (14, 126, 152),
    # Wasteland (bottom-right quadrant)
    (15, 228, 132),
    (16, 266, 128),
    (17, 194, 152),
    (18, 232, 166),
    (19, 272, 164),
    # Central Valley approaches
    (20, 140, 88),
    (29, 180, 112),
)

_LAIR_ARENAS: tuple[str, ...] = ("fol1.t", "fo1.t", "gl1.t", "fol2.t")

COMBAT_SFX_KEYS: tuple[str, ...] = ("swing", "hit", "block", "hurt", "throw", "death")

_LEGACY_SFX_ALIASES: dict[str, tuple[str, ...]] = {
    "hit": ("hit.a", "splat.a", "swordhit.a"),
    "hurt": ("hurt.a",),
    "throw": ("throw.a", "swish.a", "knifethr.a"),
    "swing": ("swing.a",),
    "block": ("block.a",),
    "death": ("death.a",),
}


def _extract_8svx_chunks(pcm: bytes) -> list[tuple[bytes, int]]:
    chunks: list[tuple[bytes, int]] = []
    pos = 0
    while True:
        idx = pcm.find(b"FORM", pos)
        if idx < 0 or idx + 12 > len(pcm):
            break
        if pcm[idx + 8 : idx + 12] != b"8SVX":
            pos = idx + 4
            continue
        (form_size,) = struct.unpack_from(">I", pcm, idx + 4)
        end = min(len(pcm), idx + 8 + form_size)
        with contextlib.suppress(AudioFormatError):
            chunks.append(decode_iff_8svx(pcm[idx:end]))
        pos = max(idx + 12, end)
    return chunks


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
                pal = assemble_combat_palette(default_combat_palette(), bg_pic.palette)
                return arena, bg_pic.pixels[:PLAYFIELD_HEIGHT, :CANVAS_WIDTH], pal
        return None

    def get_knight_cel(self, knight_index: int) -> DecodedCel | None:
        """Return the knight cel for player ``knight_index`` (0..3)."""
        preferred = f"KN{(knight_index % 4) + 1}.ob"
        return self.get_cel(preferred, "KN4.ob", "KN1.ob")

    def get_monster_cels(self, archetype: MonsterArchetype) -> tuple[DecodedCel, ...]:
        """Return all available cel slots for ``archetype`` in slot order."""
        candidates = _ARCHETYPE_CELS.get(archetype, ("TROLL1.CEL", "TROLL2.CEL"))
        loaded: list[DecodedCel] = []
        for name in candidates:
            cel = self.get_cel(name)
            if cel is not None and cel.frames:
                loaded.append(cel)
        return tuple(loaded)

    def get_monster_cel(self, archetype: MonsterArchetype) -> DecodedCel | None:
        """Return the primary cel for ``archetype``."""
        cels = self.get_monster_cels(archetype)
        if cels:
            return cels[0]
        return self.get_cel("TROLL1.CEL")

    def preload_audio(self, audio: AudioEngine) -> None:
        """Load available ``.a`` sample banks and register addressable combat SFX keys."""
        bank_chunks: dict[str, list[tuple[bytes, int]]] = {}
        for entry in self.disk_set.list_entries():
            if not entry.name.casefold().endswith(".a"):
                continue
            try:
                bank = decode_sample_bank(entry.name, self.disk_set.read_file(entry.name))
            except ValueError:
                continue
            if len(bank.samples_pcm8) == 0:
                continue
            audio.mixer.register_sample_bank(bank)
            stem = entry.name.rsplit(".", 1)[0].casefold()
            chunks = _extract_8svx_chunks(bank.samples_pcm8) or [
                (bank.samples_pcm8, DEFAULT_SAMPLE_RATE)
            ]
            bank_chunks[stem] = chunks
            for idx, (pcm, rate) in enumerate(chunks):
                audio.mixer.register_sample(f"{stem}:{idx}", pcm, sample_rate=rate)

        self._register_combat_sfx_keys(audio, bank_chunks)
        mog_tables = self.get_mog_tables()
        if mog_tables is not None:
            audio.mixer.set_synth_tables(mog_tables.synth)

    def _register_combat_sfx_keys(
        self,
        audio: AudioEngine,
        bank_chunks: dict[str, list[tuple[bytes, int]]],
    ) -> None:
        primary = bank_chunks.get("kn") or bank_chunks.get("he")
        if not primary and bank_chunks:
            primary = next(iter(bank_chunks.values()))
        if not primary:
            return
        for idx, sfx_key in enumerate(COMBAT_SFX_KEYS):
            pcm, rate = primary[idx % len(primary)]
            audio.mixer.register_sample(sfx_key, pcm, sample_rate=rate)
            for alias in _LEGACY_SFX_ALIASES.get(sfx_key, ()):
                audio.mixer.register_sample(alias, pcm, sample_rate=rate)

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

    def build_overworld_graph(self) -> OverworldGraph | None:
        """Build an ``OverworldGraph`` from ``mog`` sites and lair nodes (ARCHITECTURE.md §5)."""
        mog_tables = self.get_mog_tables()
        if mog_tables is None or len(mog_tables.lair_region.map_sites) < 4:
            return None
        road_kind = NodeKind.__members__.get("ROAD", NodeKind.WILDERNESS)
        valley_kind = NodeKind.__members__.get("VALLEY_OF_THE_GODS", NodeKind.WILDERNESS)
        tower_kind = NodeKind.__members__.get("MATHS_TOWER", NodeKind.WILDERNESS)
        site_kinds: dict[int, NodeKind] = {
            **dict.fromkeys(range(21), road_kind),
            21: NodeKind.START,
            22: NodeKind.START,
            23: NodeKind.START,
            24: NodeKind.START,
            25: NodeKind.HIGHWOOD,
            26: NodeKind.WATERDEEP,
            27: NodeKind.STONEHENGE,
            28: valley_kind,
            29: road_kind,
            30: tower_kind,
            31: NodeKind.LAIR,
            32: NodeKind.LAIR,
            33: NodeKind.LAIR,
            34: NodeKind.LAIR,
        }
        site_list = [
            MapSiteInput(
                site_id=site.site_id, x=site.x, y=site.y, kind=site_kinds.get(site.site_id)
            )
            for site in mog_tables.lair_region.map_sites
        ]
        existing_ids = {site.site_id for site in site_list}
        for road_id, rx, ry in _CANONICAL_ROAD_SITES:
            if road_id not in existing_ids:
                site_list.append(MapSiteInput(site_id=road_id, x=rx, y=ry, kind=road_kind))
        sites = tuple(site_list)
        nodes = mog_tables.lair_region.creature_nodes
        lairs = tuple(
            (idx, nodes[idx * 6].map_x, nodes[idx * 6].map_y)
            for idx in range(4)
            if idx * 6 < len(nodes)
        )
        bands = tuple(
            EncounterBand(limit=band.limit, kind=band.kind)
            for band in mog_tables.lair_region.encounter_bands
        )
        return build_overworld_graph_from_sites(
            sites,
            lairs=lairs,
            encounter_bands=bands,
            moves_per_turn=3,
        )

    def compose_knight_sprites(
        self,
        knight_index: int,
        action: KnightAction,
        tick_count: int,
        *,
        x: int,
        y: int,
        z: int = 0,
        flipped: bool = False,
        layer: int = 2,
        order: int = 0,
    ) -> list[SceneSprite]:
        """Build multi-part knight :class:`SceneSprite`s using `mog` scripts and `collide.hit`."""
        kcel = self.get_knight_cel(knight_index)
        if kcel is None or not kcel.frames:
            return []
        mog_tables = self.get_mog_tables()
        hit_sets = self.get_hit_sets()
        preferred = f"kn{(knight_index % 4) + 1}"
        hset = (
            hit_sets.get(preferred) or hit_sets.get("kn4") or hit_sets.get("kn1")
            if hit_sets
            else None
        )
        pieces = compose_knight_pieces(
            kcel,
            action,
            tick_count,
            flipped=flipped,
            mog_s4=self.get_mog_s4_bytes(),
            combat_actions=mog_tables.combat_actions if mog_tables is not None else None,
            hit_set=hset,
        )
        sprites: list[SceneSprite] = []
        for idx, piece in enumerate(pieces):
            sp = make_sprite(
                kcel,
                piece.frame_index,
                x=x,
                y=y,
                z=z,
                dx=piece.dx,
                dy=piece.dy,
                flipped=piece.flipped,
                layer=layer,
                order=order + idx,
            )
            if sp is not None:
                sprites.append(sp)
        return sprites

    def compose_monster_sprites(
        self,
        archetype: MonsterArchetype,
        tick_count: int,
        *,
        attacking: bool = False,
        x: int,
        y: int,
        z: int = 0,
        flipped: bool = False,
        layer: int = 2,
        order: int = 0,
    ) -> list[SceneSprite]:
        """Build multi-part monster :class:`SceneSprite`s across all cel slots."""
        cels = self.get_monster_cels(archetype)
        if not cels:
            fallback = self.get_cel("TROLL1.CEL")
            cels = (fallback,) if fallback is not None and fallback.frames else ()
        sprites: list[SceneSprite] = []
        for slot_idx, cel in enumerate(cels):
            fidx = select_monster_frame(cel, tick_count, attacking)
            sp = make_sprite(
                cel,
                fidx,
                x=x,
                y=y,
                z=z,
                flipped=flipped,
                layer=layer,
                order=order + slot_idx,
            )
            if sp is not None:
                sprites.append(sp)
        return sprites


def select_knight_frame(
    cel: DecodedCel,
    action: KnightAction,
    tick_count: int,
    *,
    hit_set: HitSet | None = None,
) -> int:
    """Pick a body-sized cel frame index within `collide.hit` bounds for ``action``."""
    return select_knight_body_frame(cel, action, tick_count, hit_set=hit_set)


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
    dx: int = 0,
    dy: int = 0,
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
        dx=dx,
        dy=dy,
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
