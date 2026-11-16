"""Creature, arena and lair definitions read from ``mog`` (ARCHITECTURE.md §10.3-§10.6, §10.15).

Nothing here is a game value. The module holds *where* in ``mog`` hunk 0 the original keeps each
value (routine entries and operand positions of the supported version) and reads the values from
the user's file through the checked readers of :mod:`pymoonstone.assets.mog_code`.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from pymoonstone.assets.fight_palette import MogVersionError
from pymoonstone.assets.mog_code import (
    BSS_HUNK,
    CODE_HUNK,
    DATA_HUNK,
    OP_BRA_W,
    OP_BSR_W,
    OP_JSR,
    OP_LEA_A0,
    OP_LEA_A1,
    OP_LEA_A2,
    OP_LEA_A3,
    OP_LEA_A4,
    OP_RTS,
    Cell,
    MogCode,
    Pointer,
    StoreRun,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pymoonstone.assets.hunk import DecodedHunkExecutable

CEL_SLOTS = 5
SCRIPT_TABLE_ENTRIES = 9
WALK_TABLE_ENTRIES = 24
# A script-table entry that is relocated, but not into the script hunk.
NOT_A_SCRIPT = -1

# Record offsets written by the initialisers (§9.2).
_F_X, _F_HEIGHT, _F_Y, _F_FACING, _F_PORT = 4, 6, 8, 10, 11
_F_IDLE, _F_AFTER_HIT, _F_HURT, _F_ACTION, _F_FRAMES = 22, 26, 30, 34, 38
_F_DAMAGE, _F_WALK, _F_DEFENCE, _F_TYPE = 42, 46, 50, 77
_F_HP, _F_HP_MAX, _F_REACH, _F_TOO_CLOSE, _F_DEPTH = 80, 84, 116, 118, 120

# Hunk-1 cells the arena routines fill (§10.3).
_CELL_TOTAL = 760
_CELL_MAX_ALIVE = 762
_CELL_TOGGLE = 766
_CELL_NEXT = 768
_CELL_INIT = 772
_CELL_KIND_TAG = 382

# Routine entries in hunk 0.
_LOAD_CEL = Pointer(26, 1788)
_LOAD_CEL_WITH_HITS = Pointer(CODE_HUNK, 34812)
_PALETTE_ROUTINE = Pointer(CODE_HUNK, 35738)
_SPAWN_WHOLE_LIST = 16070
_SPAWN_ONE = 16286
_SOUND_HUNK = 16

_KNIGHT_FILL = (13380, 13754)
_CREATURE_FILL = (13756, 15192)
_ARENA_TABLE_FILL = (19716, 19826)
_FRAME_SET_FILL = (30068, 30104)
_KNIGHT_INIT = (15678, 15760)
_DRAGON_INIT = (17980, 18076)
_DRAGON_DAMAGE = (17660, 17698)
_DRAGON_PLACE = (17698, 17728)
_DRAGON_OBJECTS = (((17758, 17788), (17792, 17820)), ((17838, 17868), (17872, 17900)))
_GUARDIAN_INIT = (18776, 18860)
_RATMEN_VARIANTS = ((17528, 17536, 17564, 0x0C68), (17564, 17572, 17600, 0x205F))
_RATMEN_OBJECT = (17306, 17312, 17340)
_REGION_CELL = Pointer(DATA_HUNK, 28350)
_SINGLE_ALIVE_COMPARES = (16672, 16692)
_ALIVE_CAP_COMPARE = (16712, 16724)
_CUT_ROWS_LEA = 16800
_CUT_TABLE_INDEX = 16832
_CUT_SIZE = 8
_LAIR_LEAS = (20166, 20172, 20178, 20184)
_LAIR_COUNT_POS = 20190
_OP_PUSH_A0 = 0x2F08
_OP_CMPI_L_ABS = 0x0CB9
_OP_CMPI_W_ABS = 0x0C79
_OP_CMPI_W_A0 = 0x0C68
_OP_MOVE_B_ABS = 0x13FC
_OP_MOVE_L_ABS = 0x23FC
_OP_MOVE_L_D0 = 0x203C
_OP_MOVE_B_D5 = 0x1A3C
_OP_EORI_W_ABS = 0x0A79
_OP_INDEXED_BYTE = 0x103B
_POKE_LOADS = (0x2069, 0x2269)
_POKE_STORES = (0x217C, 0x237C)
_POKE_TABLES = {_F_ACTION: "action", _F_HURT: "hurt"}


@dataclass(slots=True, frozen=True)
class CelFile:
    """A cel file a fight loads, and whether its hit set is registered (§10.3)."""

    name: str
    has_hit_set: bool


@dataclass(slots=True, frozen=True)
class CelSet:
    """What one loader puts into the five-entry frame lists (§10.3).

    ``slots`` is the creature list; ``None`` is a slot the loader leaves as it was. The other
    two tuples are the slots of the knights' and the effects list the same loader fills.
    """

    kind: int | None
    slots: tuple[CelFile | None, ...]
    knight_slots: tuple[CelFile | None, ...]
    effect_slots: tuple[CelFile | None, ...]
    sound_bank: str | None


@dataclass(slots=True, frozen=True)
class SpawnEntry:
    """One record of a spawn list (§10.5)."""

    x: int
    height: int
    y: int
    facing: int


@dataclass(slots=True, frozen=True)
class ActorInit:
    """What an initialiser writes into a new actor (§10.4). ``None`` / ``()`` means not written.

    Script values are offsets into ``mog`` hunk 4; 0 is an empty entry.
    """

    actor_type: int | None
    input_port: int | None
    facing: int | None
    position: tuple[int | None, int | None, int | None]
    frame_set: int | None
    idle_script: int
    after_hit_script: int
    hurt_scripts: tuple[int, ...]
    action_scripts: tuple[int, ...]
    walk_scripts: tuple[int, ...]
    damage_table: tuple[int, ...]
    defence_table: tuple[int, ...]
    hp: int | None
    hp_max: int | None
    reach_x: int | None
    too_close_x: int | None
    depth_reach: int | None


@dataclass(slots=True, frozen=True)
class Poke:
    """One entry of the knights' action or hurt table replaced for a fight (§10.3)."""

    table: str
    offset: int
    script: int


@dataclass(slots=True, frozen=True)
class MoonVariant:
    """Hit points and damage entries a creature takes at one moon frame (§10.4)."""

    moon_frame: int
    hp: int
    damage_table: tuple[int, ...]


@dataclass(slots=True, frozen=True)
class ArenaDef:
    """One arena set-up routine (§10.3, §10.5, §10.6)."""

    name: str
    handler_offsets: tuple[int, ...]
    cels: CelSet
    scene: int
    region_override: int | None
    max_alive: int
    total: int
    first_wave: tuple[SpawnEntry, ...]
    next_creature: tuple[SpawnEntry, ...]
    one_at_a_time: bool
    alive_cap: int | None
    cut_row: tuple[int, ...]
    knight_pokes: tuple[Poke, ...]
    creature: ActorInit
    objects: tuple[ActorInit, ...]
    moon_variants: tuple[MoonVariant, ...]


@dataclass(slots=True, frozen=True)
class LairDef:
    """Start values of one lair (§9.5)."""

    handler_offset: int
    creatures_left: int
    map_x: int
    map_y: int
    param14: int
    arena_name: str


@dataclass(slots=True, frozen=True)
class FightDefs:
    """Everything §10.15 reads for fights."""

    knight: ActorInit
    knight_cels: CelSet
    duel_cels: CelSet
    arenas: tuple[ArenaDef, ...]
    arena_by_handler: Mapping[int, ArenaDef]
    duel_handler_offsets: tuple[int, ...]
    frame_set_lists: tuple[int, ...]
    lairs: tuple[LairDef, ...]


@dataclass(slots=True, frozen=True)
class _CelLoad:
    target: str
    slots: tuple[int, ...]
    lea_pos: int
    call_pos: int


@dataclass(slots=True, frozen=True)
class _Loader:
    entry: int
    tag_pos: int | None
    sound_pos: int | None
    files: tuple[_CelLoad, ...]


@dataclass(slots=True, frozen=True)
class _ArenaSpec:
    name: str
    entry: int
    loader: _Loader
    loader_call: int
    scene_pos: int
    max_alive_pos: int
    total_pos: int
    next_pos: int
    init_pos: int | None = None
    init_stop: int = 0
    wave: tuple[int | None, int] | None = None
    pokes: tuple[tuple[int, tuple[int, ...]], ...] = ()
    region_pos: int | None = None


def _c(slots: tuple[int, ...], lea_pos: int, call_pos: int) -> _CelLoad:
    return _CelLoad("creature", slots, lea_pos, call_pos)


_KN5 = ("knight", (4,))
_KNIGHT_LOADER = _Loader(
    9360,
    None,
    9568,
    (
        _CelLoad("knight", (0,), 9380, 9386),
        _CelLoad("knight", (1,), 9426, 9432),
        _CelLoad("knight", (2,), 9472, 9478),
        _CelLoad("knight", (3,), 9518, 9524),
        _CelLoad("effect", (0, 1, 2, 3, 4), 9550, 9562),
    ),
)
_DUEL_LOADER = _Loader(
    9576,
    9596,
    None,
    (_c((0,), 9624, 9630), _c((1,), 9670, 9676), _c((2,), 9716, 9722), _c((3,), 9518, 9524)),
)
_SPEAR_LOADER = _Loader(9766, 9786, 9884, (_c((0,), 9814, 9820), _c((1, 2, 3, 4), 9872, 9878)))
_AXE_LOADER = _Loader(9892, 9912, 9998, (_c((0,), 9940, 9946), _c((1,), 9986, 9992)))
_RATMEN_LOADER = _Loader(10006, 10026, 10112, (_c((0,), 10054, 10060), _c((1,), 10100, 10106)))
_MUDMEN_LOADER = _Loader(10120, 10120, 10192, (_c((0,), 10148, 10154), _c((1,), 10180, 10186)))
_BALOK_LOADER = _Loader(
    10200,
    10212,
    10342,
    (
        _c((0,), 10240, 10246),
        _c((1,), 10290, 10296),
        _c((2,), 10330, 10336),
        _CelLoad(*_KN5, 10370, 10376),
    ),
)
_DRAGON_LOADER = _Loader(
    10384,
    10384,
    10510,
    (
        _c((0,), 10412, 10418),
        _c((1,), 10458, 10464),
        _c((4,), 10498, 10504),
        _CelLoad(*_KN5, 10538, 10544),
    ),
)
_FLYER_LOADER = _Loader(10564, 10576, 10662, (_c((0,), 10604, 10610), _c((1,), 10650, 10656)))
_GUARDIAN_LOADER = _Loader(
    10668,
    10674,
    10838,
    (
        _c((3,), 10702, 10708),
        _c((4,), 10748, 10754),
        _c((0,), 10780, 10786),
        _c((2,), 10826, 10832),
    ),
)
_TROLL_LOADER = _Loader(
    10846,
    10858,
    10996,
    (_c((0,), 10894, 10906), _c((1,), 10944, 10956), _CelLoad(*_KN5, 10984, 10990)),
)
_DUEL_ENTRY = 15378

_WAVE_ARENAS = (
    _ArenaSpec(
        "flyer",
        16954,
        _FLYER_LOADER,
        16962,
        17072,
        17006,
        17014,
        17030,
        17040,
        17196,
        (17062, 17068),
        ((16974, (16978,)), (16986, (16990, 16998))),
    ),
    _ArenaSpec(
        "mudmen",
        18282,
        _MUDMEN_LOADER,
        18300,
        18366,
        18314,
        18322,
        18338,
        18348,
        18496,
        (None, 18362),
        region_pos=18284,
    ),
    _ArenaSpec(
        "trogg_axe",
        15762,
        _AXE_LOADER,
        15768,
        15840,
        15774,
        15782,
        15798,
        15808,
        15950,
        (15830, 15836),
    ),
    _ArenaSpec(
        "trogg_axe_2",
        15952,
        _AXE_LOADER,
        15958,
        16030,
        15964,
        15972,
        15988,
        15998,
        16248,
        (16020, 16026),
    ),
    _ArenaSpec(
        "trogg_spear",
        16348,
        _SPEAR_LOADER,
        16354,
        16452,
        16386,
        16394,
        16410,
        16420,
        16546,
        (16442, 16448),
        ((16366, (16370, 16378)),),
    ),
    _ArenaSpec(
        "ratmen",
        17198,
        _RATMEN_LOADER,
        17206,
        17370,
        17238,
        17246,
        17262,
        17272,
        17522,
        (17360, 17366),
        ((17218, (17222, 17230)),),
    ),
    _ArenaSpec(
        "balok",
        18078,
        _BALOK_LOADER,
        18086,
        18176,
        18110,
        18118,
        18142,
        18152,
        18272,
        (18166, 18172),
        ((18098, (18102,)),),
    ),
    _ArenaSpec(
        "troll",
        18498,
        _TROLL_LOADER,
        18506,
        18596,
        18530,
        18538,
        18554,
        18564,
        18694,
        (18586, 18592),
        ((18518, (18522,)),),
    ),
)
_GUARDIAN_ARENA = _ArenaSpec(
    "guardian",
    18696,
    _GUARDIAN_LOADER,
    18710,
    18920,
    18886,
    18894,
    18910,
    pokes=((18752, (18756,)),),
)
_DRAGON_ARENA = _ArenaSpec(
    "dragon",
    17604,
    _DRAGON_LOADER,
    17612,
    17958,
    17906,
    17914,
    17930,
    pokes=((17624, (17628, 17636, 17644, 17652)),),
)


def _fail(what: str, detail: str) -> MogVersionError:
    return MogVersionError(f"mog: {what}: {detail}")


# ---- cel sets ------------------------------------------------------------------------------


def _cel_file(mog: MogCode, load: _CelLoad, what: str) -> CelFile:
    name = mog.cstring(mog.lea(load.lea_pos, OP_LEA_A0, what), what)
    routine = mog.call_target(load.call_pos, what)
    if routine not in (_LOAD_CEL, _LOAD_CEL_WITH_HITS):
        raise _fail(what, f"the call at code offset {load.call_pos} is not a cel loader")
    return CelFile(name, routine == _LOAD_CEL_WITH_HITS)


def _kind_tag(mog: MogCode, pos: int, what: str) -> int:
    mog.expect(pos, (_OP_MOVE_B_ABS,), what)
    if mog.pointer(pos + 4, what) != Pointer(BSS_HUNK, _CELL_KIND_TAG):
        raise _fail(what, f"the instruction at code offset {pos} does not tag the cel set")
    return mog.word(pos + 2) & 0xFF


def _sound_bank(mog: MogCode, pos: int, what: str) -> str:
    routine = mog.call_target(pos, what)
    if routine.hunk != _SOUND_HUNK:
        raise _fail(what, f"the call at code offset {pos} is not a sound-bank loader")
    code = mog.hunk_data(_SOUND_HUNK)
    opcode: int = struct.unpack_from(">H", code, routine.offset)[0]
    if opcode != OP_LEA_A0:
        raise _fail(what, "the sound-bank loader does not start with LEA name,A0")
    return mog.cstring(mog.pointer(routine.offset + 2, what, _SOUND_HUNK), what)


def _cel_set(mog: MogCode, loader: _Loader, what: str) -> CelSet:
    lists: dict[str, list[CelFile | None]] = {
        name: [None] * CEL_SLOTS for name in ("creature", "knight", "effect")
    }
    for load in loader.files:
        cel = _cel_file(mog, load, what)
        for slot in load.slots:
            lists[load.target][slot] = cel
    return CelSet(
        kind=None if loader.tag_pos is None else _kind_tag(mog, loader.tag_pos, what),
        slots=tuple(lists["creature"]),
        knight_slots=tuple(lists["knight"]),
        effect_slots=tuple(lists["effect"]),
        sound_bank=None if loader.sound_pos is None else _sound_bank(mog, loader.sound_pos, what),
    )


# ---- actor records -------------------------------------------------------------------------


def _script_entry(cell: Cell | None, what: str) -> int:
    if cell is None:
        return 0
    if cell.target is None:
        if cell.value != 0:
            raise _fail(what, f"script entry ${cell.value:X} is not relocated")
        return 0
    return cell.value if cell.target == DATA_HUNK else NOT_A_SCRIPT


def _number_entry(cell: Cell | None, what: str) -> int:
    if cell is None:
        return 0
    if cell.target is not None:
        raise _fail(what, "a number table holds an address")
    return cell.value


def _table(
    fields: Mapping[int, Cell], offset: int, run: StoreRun, count: int, what: str, *, scripts: bool
) -> tuple[int, ...]:
    """The hunk-1 table a record field points at, as ``count`` entries; ``()`` when not set."""
    cell = fields.get(offset)
    if cell is None:
        return ()
    if cell.target != BSS_HUNK:
        raise _fail(what, f"record field +{offset} does not point at a table")
    convert = _script_entry if scripts else _number_entry
    return tuple(convert(entry, what) for entry in run.longs(Pointer(BSS_HUNK, cell.value), count))


def _small(fields: Mapping[int, Cell], offset: int) -> int | None:
    cell = fields.get(offset)
    return None if cell is None else cell.value


def _signed(value: int | None) -> int | None:
    if value is None:
        return None
    return value - 0x10000 if value & 0x8000 else value


def _frame_set(
    fields: Mapping[int, Cell], frame_set_lists: tuple[int, ...], what: str
) -> int | None:
    cell = fields.get(_F_FRAMES)
    if cell is None:
        return None
    if cell.target != BSS_HUNK or cell.value not in frame_set_lists:
        raise _fail(what, "record field +38 is not one of the frame lists")
    return frame_set_lists.index(cell.value) + 1


def _actor(
    fields: Mapping[int, Cell], run: StoreRun, frame_set_lists: tuple[int, ...], what: str
) -> ActorInit:
    """Build an :class:`ActorInit` from the record fields a run wrote (§10.4)."""
    return ActorInit(
        actor_type=_small(fields, _F_TYPE),
        input_port=_small(fields, _F_PORT),
        facing=_small(fields, _F_FACING),
        position=(
            _signed(_small(fields, _F_X)),
            _signed(_small(fields, _F_HEIGHT)),
            _signed(_small(fields, _F_Y)),
        ),
        frame_set=_frame_set(fields, frame_set_lists, what),
        idle_script=_script_entry(fields.get(_F_IDLE), what),
        after_hit_script=_script_entry(fields.get(_F_AFTER_HIT), what),
        hurt_scripts=_table(fields, _F_HURT, run, SCRIPT_TABLE_ENTRIES, what, scripts=True),
        action_scripts=_table(fields, _F_ACTION, run, SCRIPT_TABLE_ENTRIES, what, scripts=True),
        walk_scripts=_table(fields, _F_WALK, run, WALK_TABLE_ENTRIES, what, scripts=True),
        damage_table=_table(fields, _F_DAMAGE, run, SCRIPT_TABLE_ENTRIES, what, scripts=False),
        defence_table=_table(fields, _F_DEFENCE, run, SCRIPT_TABLE_ENTRIES, what, scripts=False),
        hp=_small(fields, _F_HP),
        hp_max=_small(fields, _F_HP_MAX),
        reach_x=_small(fields, _F_REACH),
        too_close_x=_small(fields, _F_TOO_CLOSE),
        depth_reach=_small(fields, _F_DEPTH),
    )


class _Reader:
    """Shared state of one parse: the code, the filled tables and the frame lists."""

    def __init__(self, mog: MogCode) -> None:
        self.mog = mog
        self.tables = StoreRun(mog)
        self.tables.run(*_KNIGHT_FILL, "knight script tables")
        self.tables.run(*_CREATURE_FILL, "creature script tables")
        self.frame_set_lists = self._frame_set_lists()

    def _frame_set_lists(self) -> tuple[int, ...]:
        run = StoreRun(self.mog)
        run.run(*_FRAME_SET_FILL, "frame-set list", OP_LEA_A0)
        base = self.mog.lea(_FRAME_SET_FILL[0], OP_LEA_A0, "frame-set list")
        lists: list[int] = []
        for cell in run.longs(base, CEL_SLOTS):
            if cell is None or cell.target != BSS_HUNK:
                break
            lists.append(cell.value)
        return tuple(lists)

    def record_run(self) -> StoreRun:
        """A run that sees the filled tables and keeps its own writes to itself."""
        return StoreRun(self.mog, memory=dict(self.tables.memory), starts=set(self.tables.starts))

    def actor(self, run: StoreRun, what: str) -> ActorInit:
        return _actor(run.record_fields(), run, self.frame_set_lists, what)

    def init_run(self, start: int, stop: int, what: str) -> StoreRun:
        """Run a creature initialiser; a leading register save is stepped over."""
        run = self.record_run()
        if self.mog.word(start) == _OP_PUSH_A0:
            start += 2
        terminator = OP_RTS if self.mog.word(stop) == OP_RTS else OP_LEA_A0
        run.run_record(start, stop, what, terminator)
        return run


# ---- arena rows ----------------------------------------------------------------------------


def _scene(mog: MogCode, pos: int, what: str) -> int:
    opcode = mog.word(pos)
    if opcode & 0xFF00 == 0x7000:
        scene, call_pos = opcode & 0xFF, pos + 2
    else:
        mog.expect(pos, (_OP_MOVE_L_D0,), what)
        scene, call_pos = mog.long(pos + 2), pos + 6
    if mog.call_target(call_pos, what) != _PALETTE_ROUTINE:
        raise _fail(what, f"the call at code offset {call_pos} is not the palette routine")
    return scene


def _spawn_list(mog: MogCode, pointer: Pointer, count: int) -> tuple[SpawnEntry, ...]:
    data = mog.hunk_data(pointer.hunk)
    return tuple(
        SpawnEntry(*struct.unpack_from(">4h", data, pointer.offset + index * 8))
        for index in range(count)
    )


def _next_creature(mog: MogCode, routine: int, what: str) -> tuple[SpawnEntry, ...]:
    """Entries the next-creature routine spawns from: none, one, or two taken in turn (§10.5)."""
    if mog.word(routine) == OP_RTS:
        return ()
    spawn_list = mog.lea(routine, OP_LEA_A0, what)
    if mog.word(routine + 6) == _OP_EORI_W_ABS:
        if mog.pointer(routine + 10, what) != Pointer(BSS_HUNK, _CELL_TOGGLE):
            raise _fail(what, "the next-creature routine flips another cell")
        return _spawn_list(mog, spawn_list, 2)
    if mog.branch_target(routine + 6, OP_BRA_W, what) != _SPAWN_ONE:
        raise _fail(what, "the next-creature routine does not end in the spawn routine")
    return _spawn_list(mog, spawn_list, 1)


def _first_wave(
    mog: MogCode, spec: _ArenaSpec, reach: int, next_routine: int, what: str
) -> tuple[SpawnEntry, ...]:
    """The list a whole-list first wave walks; ``()`` when the next-creature routine is used.

    ``reach`` is the largest alive count wave scaling can produce (§10.6).
    """
    if spec.wave is None:
        return ()
    lea_pos, call_pos = spec.wave
    target = mog.branch_target(call_pos, OP_BSR_W, what)
    if target != _SPAWN_WHOLE_LIST:
        if target != next_routine:
            _require_next_routine(mog, target, what)
        return ()
    if lea_pos is None:
        raise _fail(what, "a whole-list wave without a list")
    return _spawn_list(mog, mog.lea(lea_pos, OP_LEA_A0, what), reach)


def _wave_reach(max_alive: int, *, one_at_a_time: bool, alive_cap: int | None) -> int:
    """Largest ``max_alive`` after scaling: up to two more, then the forced limits (§10.6)."""
    if one_at_a_time:
        return 1
    scaled = max_alive + 2
    return scaled if alive_cap is None else min(scaled, alive_cap)


def _require_next_routine(mog: MogCode, target: int, what: str) -> None:
    if not _next_creature(mog, target, what):
        raise _fail(what, f"the first wave calls an unknown routine at code offset {target}")


def _pokes(mog: MogCode, spec: _ArenaSpec, what: str) -> tuple[Poke, ...]:
    pokes: list[Poke] = []
    for load_pos, store_positions in spec.pokes:
        mog.expect(load_pos, _POKE_LOADS, what)
        table = _POKE_TABLES.get(mog.word(load_pos + 2))
        if table is None:
            raise _fail(what, f"the load at code offset {load_pos} is not a script table")
        for pos in store_positions:
            mog.expect(pos, _POKE_STORES, what)
            script = mog.pointer(pos + 2, what)
            if script.hunk != DATA_HUNK:
                raise _fail(what, f"the poke at code offset {pos} is not a script")
            pokes.append(Poke(table, mog.word(pos + 6), script.offset))
    return tuple(pokes)


def _region_override(mog: MogCode, spec: _ArenaSpec, what: str) -> int | None:
    if spec.region_pos is None:
        return None
    mog.expect(spec.region_pos, (_OP_MOVE_L_ABS,), what)
    if mog.pointer(spec.region_pos + 6, what) != _REGION_CELL:
        raise _fail(what, "the region override writes another cell")
    return mog.long(spec.region_pos + 2)


@dataclass(slots=True, frozen=True)
class _WaveRules:
    single_alive_routines: tuple[int, ...]
    cap_init: int
    cap: int
    cut_inits: tuple[int, ...]
    cut_table: tuple[tuple[int, ...], ...]


def _compare_operand(mog: MogCode, pos: int, cell: int, what: str) -> int:
    mog.expect(pos, (_OP_CMPI_L_ABS,), what)
    if mog.pointer(pos + 6, what) != Pointer(BSS_HUNK, cell):
        raise _fail(what, f"the compare at code offset {pos} reads another cell")
    return mog.pointer(pos + 2, what).offset


def _wave_rules(mog: MogCode) -> _WaveRules:
    """Which routines force one creature at a time or cap the count, and the cut table (§10.6)."""
    what = "wave scaling"
    cap_pos, cap_value_pos = _ALIVE_CAP_COMPARE
    mog.expect(cap_value_pos, (_OP_CMPI_W_ABS,), what)
    rows = mog.lea(_CUT_ROWS_LEA, OP_LEA_A0, what)
    mog.expect(_CUT_TABLE_INDEX, (_OP_INDEXED_BYTE,), what)
    base = _CUT_TABLE_INDEX + 2 + (mog.word(_CUT_TABLE_INDEX + 2) & 0xFF)
    return _WaveRules(
        single_alive_routines=tuple(
            _compare_operand(mog, pos, _CELL_NEXT, what) for pos in _SINGLE_ALIVE_COMPARES
        ),
        cap_init=_compare_operand(mog, cap_pos, _CELL_INIT, what),
        cap=mog.word(cap_value_pos + 2),
        cut_inits=tuple(
            mog.pointer(rows.offset + index * 4, what).offset for index in range(_CUT_SIZE)
        ),
        cut_table=tuple(
            struct.unpack_from(f">{_CUT_SIZE}b", mog.code, base + row * _CUT_SIZE)
            for row in range(_CUT_SIZE)
        ),
    )


def _arena(
    reader: _Reader,
    spec: _ArenaSpec,
    handlers: Mapping[int, int],
    creature: ActorInit,
    init_routine: int,
    rules: _WaveRules,
) -> ArenaDef:
    """Read the cells one arena routine sets (§10.3); the creature record is read by the caller."""
    mog, what = reader.mog, f"{spec.name} arena"
    if mog.call_target(spec.loader_call, what) != Pointer(CODE_HUNK, spec.loader.entry):
        raise _fail(what, "the arena does not call its cel loader")
    max_alive = mog.cell_word(spec.max_alive_pos, _CELL_MAX_ALIVE, what)
    next_routine = mog.cell_routine(spec.next_pos, _CELL_NEXT, what)
    cut_row = rules.cut_inits.index(init_routine) if init_routine in rules.cut_inits else None
    one_at_a_time = next_routine in rules.single_alive_routines
    alive_cap = rules.cap if init_routine == rules.cap_init else None
    reach = _wave_reach(max_alive, one_at_a_time=one_at_a_time, alive_cap=alive_cap)
    return ArenaDef(
        name=spec.name,
        handler_offsets=tuple(sorted(o for o, entry in handlers.items() if entry == spec.entry)),
        cels=_cel_set(mog, spec.loader, what),
        scene=_scene(mog, spec.scene_pos, what),
        region_override=_region_override(mog, spec, what),
        max_alive=max_alive,
        total=mog.cell_word(spec.total_pos, _CELL_TOTAL, what),
        first_wave=_first_wave(mog, spec, reach, next_routine, what),
        next_creature=_next_creature(mog, next_routine, what),
        one_at_a_time=one_at_a_time,
        alive_cap=alive_cap,
        cut_row=() if cut_row is None else rules.cut_table[cut_row],
        knight_pokes=_pokes(mog, spec, what),
        creature=creature,
        objects=(),
        moon_variants=(),
    )


def _wave_arena(
    reader: _Reader, spec: _ArenaSpec, handlers: Mapping[int, int], rules: _WaveRules
) -> ArenaDef:
    what = f"{spec.name} creature"
    if spec.init_pos is None:
        raise _fail(what, "no initialiser position")
    init_routine = reader.mog.cell_routine(spec.init_pos, _CELL_INIT, what)
    run = reader.init_run(init_routine, spec.init_stop, what)
    return _arena(reader, spec, handlers, reader.actor(run, what), init_routine, rules)


def _ratmen_extras(reader: _Reader, arena: ArenaDef) -> ArenaDef:
    """The Ratmen's idle object and their two moon-frame variants (§10.4, §10.5)."""
    mog, what = reader.mog, "ratmen extras"
    variants: list[MoonVariant] = []
    damage_base = Pointer(BSS_HUNK, _ratmen_damage_table(reader))
    for compare_pos, start, stop, terminator in _RATMEN_VARIANTS:
        mog.expect(compare_pos, (_OP_CMPI_W_A0,), what)
        run = reader.record_run()
        run.registers["a4"] = damage_base
        run.run_record(start, stop, what, terminator)
        hp = run.record_fields()[_F_HP].value
        damage = tuple(_number_entry(c, what) for c in run.longs(damage_base, SCRIPT_TABLE_ENTRIES))
        variants.append(MoonVariant(mog.word(compare_pos + 2), hp, damage))
    script_pos, frames_pos, type_pos = _RATMEN_OBJECT
    script = mog.lea(script_pos, OP_LEA_A0, what)
    frames = mog.lea(frames_pos, OP_LEA_A2, what)
    if frames.hunk != BSS_HUNK or frames.offset not in reader.frame_set_lists:
        raise _fail(what, "the idle object's frame list is not one of the frame lists")
    mog.expect(type_pos, (_OP_MOVE_B_D5,), what)
    idle_object = replace(
        _actor({}, reader.tables, reader.frame_set_lists, what),
        actor_type=mog.word(type_pos + 2) & 0xFF,
        frame_set=reader.frame_set_lists.index(frames.offset) + 1,
        idle_script=script.offset,
        after_hit_script=script.offset,
    )
    return replace(arena, objects=(idle_object,), moon_variants=tuple(variants))


def _ratmen_damage_table(reader: _Reader) -> int:
    spec = next(spec for spec in _WAVE_ARENAS if spec.name == "ratmen")
    if spec.init_pos is None:
        raise _fail("ratmen extras", "no initialiser position")
    init_routine = reader.mog.cell_routine(spec.init_pos, _CELL_INIT, "ratmen extras")
    run = reader.init_run(init_routine, spec.init_stop, "ratmen extras")
    return run.record_fields()[_F_DAMAGE].value


def _guardian_arena(reader: _Reader, handlers: Mapping[int, int], rules: _WaveRules) -> ArenaDef:
    run = reader.record_run()
    run.run_record(*_GUARDIAN_INIT, "guardian record", OP_JSR)
    creature = reader.actor(run, "guardian record")
    return _arena(reader, _GUARDIAN_ARENA, handlers, creature, 0, rules)


def _dragon_arena(reader: _Reader, handlers: Mapping[int, int], rules: _WaveRules) -> ArenaDef:
    """The dragon, its damage entries and the two objects of its arena (§10.3)."""
    mog, what = reader.mog, "dragon arena"
    base = reader.record_run()
    base.run(*_DRAGON_DAMAGE, what, OP_LEA_A1)
    base.run_record(*_DRAGON_INIT, what)
    place = StoreRun(mog)
    place.run(*_DRAGON_PLACE, what, OP_BSR_W)
    record = mog.lea(_DRAGON_PLACE[0], OP_LEA_A1, what)
    fields = dict(base.record_fields())
    for pointer, cell in place.memory.items():
        fields[pointer.offset - record.offset] = cell
    dragon = _actor(fields, base, reader.frame_set_lists, what)
    objects: list[ActorInit] = []
    for (first_start, first_stop), (second_start, second_stop) in _DRAGON_OBJECTS:
        if mog.branch_target(first_stop, OP_BSR_W, what) != _DRAGON_INIT[0]:
            raise _fail(what, "an arena object is not set up by the dragon initialiser")
        run = StoreRun(mog, memory=dict(base.memory), starts=set(base.starts))
        run.run_record(first_start, first_stop, what, OP_BSR_W)
        run.run_record(*_DRAGON_INIT, what)
        run.run_record(second_start, second_stop, what, OP_JSR)
        objects.append(_actor(run.record_fields(), run, reader.frame_set_lists, what))
    arena = _arena(reader, _DRAGON_ARENA, handlers, dragon, _DRAGON_INIT[0], rules)
    return replace(arena, objects=tuple(objects))


# ---- lairs, top level ----------------------------------------------------------------------


def _lairs(mog: MogCode) -> tuple[LairDef, ...]:
    """The 24 lair start records, from the four tables the set-up loop copies (§9.5)."""
    what = "lair tables"
    opcodes = (OP_LEA_A1, OP_LEA_A2, OP_LEA_A3, OP_LEA_A4)
    tables = [mog.lea(pos, op, what) for pos, op in zip(_LAIR_LEAS, opcodes, strict=True)]
    if any(table.hunk != DATA_HUNK for table in tables):
        raise _fail(what, "a lair table is outside the data hunk")
    count_op = mog.word(_LAIR_COUNT_POS)
    if count_op & 0xFF00 != 0x7000:
        raise _fail(what, "no MOVEQ loop count in front of the copy loop")
    data = mog.hunk_data(DATA_HUNK)
    kinds, places, params, names = (table.offset for table in tables)
    lairs: list[LairDef] = []
    for index in range((count_op & 0xFF) + 1):
        handler_offset, creatures_left = struct.unpack_from(">HH", data, kinds + index * 4)
        map_x, map_y = struct.unpack_from(">hh", data, places + index * 4)
        (param14,) = struct.unpack_from(">H", data, params + index * 2)
        name_pos = names + index * 4
        relocated = mog.reloc(name_pos, DATA_HUNK) is not None
        name = mog.cstring(mog.pointer(name_pos, what, DATA_HUNK), what) if relocated else ""
        lairs.append(LairDef(handler_offset, creatures_left, map_x, map_y, param14, name))
    return tuple(lairs)


def _arena_handlers(mog: MogCode) -> dict[int, int]:
    """Handler offset to arena routine, from the code that fills the table (§10.3)."""
    what = "arena table"
    run = StoreRun(mog)
    run.run(*_ARENA_TABLE_FILL, what, OP_LEA_A0)
    base = mog.lea(_ARENA_TABLE_FILL[0], OP_LEA_A0, what)
    handlers: dict[int, int] = {}
    for pointer, cell in run.memory.items():
        if cell.target != CODE_HUNK:
            raise _fail(what, "an entry is not a routine")
        handlers[pointer.offset - base.offset] = cell.value
    return handlers


def parse_fight_defs(mog_exec: DecodedHunkExecutable) -> FightDefs:
    """Read the knight, creature, arena and lair definitions from the user's ``mog`` (§10.15)."""
    mog = MogCode(mog_exec)
    reader = _Reader(mog)
    handlers = _arena_handlers(mog)
    rules = _wave_rules(mog)
    knight_run = reader.record_run()
    knight_run.run_record(*_KNIGHT_INIT, "knight record")
    arenas = [_wave_arena(reader, spec, handlers, rules) for spec in _WAVE_ARENAS]
    arenas = [_ratmen_extras(reader, a) if a.name == "ratmen" else a for a in arenas]
    arenas.append(_guardian_arena(reader, handlers, rules))
    arenas.append(_dragon_arena(reader, handlers, rules))
    return FightDefs(
        knight=reader.actor(knight_run, "knight record"),
        knight_cels=_cel_set(mog, _KNIGHT_LOADER, "knight cels"),
        duel_cels=_cel_set(mog, _DUEL_LOADER, "duel cels"),
        arenas=tuple(arenas),
        arena_by_handler={offset: arena for arena in arenas for offset in arena.handler_offsets},
        duel_handler_offsets=tuple(
            sorted(offset for offset, entry in handlers.items() if entry == _DUEL_ENTRY)
        ),
        frame_set_lists=reader.frame_set_lists,
        lairs=_lairs(mog),
    )
