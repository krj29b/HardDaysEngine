"""Versioned campaign checkpoint data and atomic local storage (§13.11)."""

from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from harddaysengine.sim.actor import Actor
from harddaysengine.sim.ai_map_state import MapAiCells
from harddaysengine.sim.fight import DrakeCells, FlyerCells
from harddaysengine.sim.fight_script import BoxCells, Draw, Job, ListEntry, Work
from harddaysengine.sim.inventory import Inventory
from harddaysengine.sim.location_state import KnightState
from harddaysengine.sim.map_dragon import DragonFlight
from harddaysengine.sim.map_rules import MapColors
from harddaysengine.sim.overworld import Arrival, Lair, MapSprite
from harddaysengine.sim.palette_fx import CycleRange, FlashOverride, PaletteFxState
from harddaysengine.sim.rewards import GiftState
from harddaysengine.sim.sound import SoundCells
from harddaysengine.sim.turns import TurnCells
from harddaysengine.sim.types import KnightId

if TYPE_CHECKING:
    from harddaysengine.app.fight_runtime import FightLoader
    from harddaysengine.app.map_runtime import MapRuntime
    from harddaysengine.assets.adf import DiskSet
    from harddaysengine.sim.overworld import OverworldState

_Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
_Tick = Annotated[int, Field(ge=0)]


class _SaveModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorldCheckpoint(_SaveModel):
    """Mutable map records. Static rules and aliases come from the fresh session."""

    ai: MapAiCells
    turns: TurnCells
    gifts: GiftState
    market: Inventory
    lairs: list[Lair]
    arrivals: Annotated[list[Arrival], Field(min_length=10, max_length=10)]
    highlights: list[MapSprite]
    dragon_touch: list[int]
    colors: MapColors
    turn_index: Annotated[int, Field(ge=0, le=3)]
    spent: int
    budget: int
    move: int
    terrain_counter: int
    blocked: bool
    travel: bool
    ambush: bool
    saved_x: int
    saved_y: int
    region_code: int
    phase: Literal[0, 1]
    menu: bool
    turn_over: bool
    dragon_encounter: bool
    dragon_active: bool
    flight: DragonFlight
    day: int
    moon_index: Annotated[int, Field(ge=0, le=7)]
    moon_frame: int
    moon_subcount: int
    total_turns: int
    claimed_moonstones_mask: int
    game_over: bool
    names: Annotated[list[str], Field(min_length=4, max_length=4)]

    @classmethod
    def capture(cls, world: "OverworldState") -> "WorldCheckpoint":
        """Copy the versioned map fields into a record."""
        return cls.model_validate({name: getattr(world, name) for name in cls.model_fields})

    def restore(self, world: "OverworldState") -> None:
        """Preserve the fresh map's actor, inventory, PRNG and rule references."""
        for name in type(self).model_fields:
            setattr(world, name, getattr(self, name))


class _Cycle(_SaveModel):
    spec: CycleRange
    tick_counter: _Tick


class PaletteCheckpoint(_SaveModel):
    base: list[int]
    target: list[int]
    divisor: Annotated[int, Field(ge=1)]
    tick: _Tick
    fading: bool
    cycles: list[_Cycle]
    flash: FlashOverride | None
    vbl: _Tick

    def restore(self) -> PaletteFxState:
        """Reconstruct the palette without stepping its effects."""
        return PaletteFxState.from_checkpoint(
            self.base,
            self.target,
            fade=(self.divisor, self.tick, self.fading),
            cycles=[(cycle.spec, cycle.tick_counter) for cycle in self.cycles],
            flash=self.flash,
            vbl=self.vbl,
        )


class MapCheckpoint(_SaveModel):
    jobs: Annotated[list[Job], Field(min_length=10, max_length=10)]
    box: BoxCells
    scratch: Work
    mirrored: set[tuple[str, int]]
    vbl: _Tick
    attack: Annotated[list[ListEntry | None], Field(min_length=80, max_length=80)]
    hurt: Annotated[list[ListEntry | None], Field(min_length=80, max_length=80)]
    cursors: tuple[int | None, int | None]
    effect_vbl: int | None
    stamps: list[Draw]
    draws: list[Draw]

    @classmethod
    def capture(cls, runtime: "MapRuntime") -> "MapCheckpoint":
        """Capture executable map jobs and asset-relative draw commands."""
        return cls.model_validate(
            runtime.snapshot() | {"stamps": runtime.stamps, "draws": runtime.draws}
        )

    def restore(self, runtime: "MapRuntime") -> None:
        """Restore mutable interpreter state and recompose the retained frame."""
        fight = runtime.scene.fight
        engine = fight.engine
        engine.jobs, engine.box, engine.scratch_work = self.jobs, self.box, self.scratch
        engine.mirrored, engine.vbl = self.mirrored, self.vbl
        engine.attack.entries, engine.hurt.entries = self.attack, self.hurt
        engine.attack.cursor, engine.hurt.cursor = self.cursors
        fight.effect_vbl = self.effect_vbl
        runtime.restore_draws(self.stamps, self.draws)


class FightCheckpoint(_SaveModel):
    flyer: FlyerCells
    drake: DrakeCells
    sound: SoundCells

    @classmethod
    def capture(cls, loader: "FightLoader") -> "FightCheckpoint":
        """Save the mutable cells shared across fights."""
        return cls(flyer=loader.flyer, drake=loader.drake, sound=loader.sound_cells)

    def restore(self, loader: "FightLoader") -> None:
        """Restore shared cells in a freshly decoded fight loader."""
        loader.flyer, loader.drake, loader.sound_cells = self.flyer, self.drake, self.sound


class CampaignCheckpoint(_SaveModel):
    """The finite set of records needed to resume an overworld tick."""

    version: Literal[1]
    data_hash: _Hash
    state_hash: _Hash
    seed: int
    tick: _Tick
    players: Annotated[tuple[KnightId, ...], Field(min_length=1, max_length=4)]
    actors: Annotated[list[Actor], Field(min_length=4, max_length=4)]
    dragon: Actor
    inventories: dict[int, Inventory]
    rng: int
    world: WorldCheckpoint
    palette: PaletteCheckpoint
    map_jobs: MapCheckpoint | None
    fights: FightCheckpoint | None
    legacy: dict[KnightId, KnightState]
    paused: bool
    selected_players: Annotated[int, Field(ge=1, le=4)]
    menu_cursor: int
    gore: bool
    pick_slot: int
    picked_mask: int
    picked: list[KnightId]
    menu_latches: tuple[int, int, bool, bool]
    map_key: int
    calendar_phase: int
    loading_cycle: int
    last_sheet_item: int
    last_status: str
    lanes: int
    lair_counts: dict[int, int]
    bad_luck: int
    defeats: int


def disk_signature(disks: "DiskSet") -> str:
    """Fingerprint the files that define campaign rules, without embedding game data."""
    digest = sha256()
    for name in ("program", "mog"):
        digest.update(sha256(disks.read_file(name)).digest())
    return digest.hexdigest()


def write_checkpoint(path: Path, checkpoint: CampaignCheckpoint) -> None:
    """Replace the previous save only after the new file is completely written."""
    with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(checkpoint.model_dump_json().encode("utf-8"))
            stream.close()
            temporary.replace(path)
        finally:
            stream.close()
            temporary.unlink(missing_ok=True)


def read_checkpoint(path: Path) -> CampaignCheckpoint:
    """Read the strict, versioned save schema."""
    return CampaignCheckpoint.model_validate_json(path.read_bytes(), strict=True)
