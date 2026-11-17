"""Real-disk, single-opponent fight presentation audit (T11.8b1, §10.17 to §10.20).

This is an audit fixture, not campaign arena setup: one Trogg is placed on the same depth as
an equipped knight. Wave scaling, lanes, campaign entry and settlement belong to T11.11.
All scripts, creature values, control rules and palette rows come from the user's disks.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from pymoonstone.app.fight_scene import FightScene
from pymoonstone.app.input import InputFrame, PlayerInput
from pymoonstone.assets.creature_rules import parse_brawler_rules
from pymoonstone.assets.fight_defs import ArenaDef, parse_fight_defs
from pymoonstone.assets.fight_palette import REGION_FOREST, assemble_fight_palette
from pymoonstone.assets.fight_program import (
    CREATURE_LIST,
    KNIGHT_LIST,
    cel_files,
    cel_shapes,
    fight_constants,
    frame_lists,
    script_image,
    script_tables,
)
from pymoonstone.assets.fighter_rules import parse_knight_rules
from pymoonstone.assets.hunk import parse_hunk_executable
from pymoonstone.assets.mog_code import MogCode
from pymoonstone.render.scene import palette_fx_to_rgb
from pymoonstone.sim.actor import Actor
from pymoonstone.sim.fight import Fight
from pymoonstone.sim.fight_ai.brawler import BRAWLER_TYPES, install_brawler
from pymoonstone.sim.fight_ai.caster import CASTER_TYPE
from pymoonstone.sim.fight_moves import DOWN, FIRE, LEFT, RIGHT, UP
from pymoonstone.sim.fight_routines import DAGGER_TYPE
from pymoonstone.sim.fighters import KNIGHT_TYPE, install_knight_control
from pymoonstone.sim.inventory import Inventory
from pymoonstone.sim.m68k import s16
from pymoonstone.sim.palette_fx import pack_rgb12
from pymoonstone.sim.rng import Rng

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from pymoonstone.app.game import GameSession
    from pymoonstone.app.scene_assets import GameAssetBundle
    from pymoonstone.assets.cel import DecodedCel
    from pymoonstone.assets.fight_defs import CelFile
    from pymoonstone.sim.fight import TableKeys
    from pymoonstone.sim.fighter_rules import KnightRules

TROGG_ARENAS = ("trogg_axe", "trogg_axe_2", "trogg_spear")
_SAMPLE_FRAMES = frozenset({0, 20, 40, 80, 160, 320})
_MAX_FRAMES = 6000


def _load_cels(bundle: GameAssetBundle, files: Mapping[str, CelFile]) -> dict[str, DecodedCel]:
    cels = {}
    for name in files:
        cel = bundle.get_cel(name)
        if cel is None:
            raise ValueError(f"fight audit: missing cel {name}")
        cels[name] = cel
    return cels


@dataclass(slots=True)
class FightAudit:
    """One knight and Trogg controlled by the real handlers, with a deterministic input bot."""

    scene: FightScene
    rules: KnightRules
    opponent: int
    first: int = 1

    def input_word(self) -> int:
        """Audit-only joystick bot: align, face, strike or throw one dagger at a time."""
        fight = self.scene.fight
        knight, trogg = fight.actor(self.first), fight.actor(self.opponent)
        if trogg.type == CASTER_TYPE:
            return _caster_input(self)
        dy = s16(trogg.y) - s16(knight.y)
        if abs(dy) > 3:
            return DOWN if dy > 0 else UP
        dx = s16(trogg.x) - s16(knight.x)
        if (knight.facing == 1) != (dx > 0):
            return RIGHT if dx > 0 else LEFT
        return _attack_word(fight, self.rules, abs(dx), self.first)


def _caster_input(audit: FightAudit) -> int:
    """Audit melee bot; Ratmen reject ordinary dagger damage (§10.23)."""
    fight = audit.scene.fight
    knight, target = fight.actor(audit.first), fight.actor(audit.opponent)
    dx, dy = s16(target.x) - s16(knight.x), s16(target.y) - s16(knight.y)
    if abs(dy) > 6:
        return DOWN if dy > 0 else UP
    if (knight.facing == 1) != (dx > 0):
        return RIGHT if dx > 0 else LEFT
    if abs(dx) > 50:
        return RIGHT if dx > 0 else LEFT
    table = audit.rules.actions_right if knight.facing == 1 else audit.rules.actions_left
    action = 0x04
    return FIRE | table.index(action)


def _attack_word(fight: Fight, rules: KnightRules, distance: int, first: int) -> int:
    knight = fight.actor(first)
    table = rules.actions_right if knight.facing == 1 else rules.actions_left
    if distance <= 50:
        return FIRE | table.index(0x08)
    daggers = any(j.active and j.type == DAGGER_TYPE for j in fight.engine.jobs)
    if distance <= 150 and not daggers:
        return FIRE | table.index(0x0C)
    return _centre(s16(knight.x))


def _centre(x: int) -> int:
    if x < 110:
        return RIGHT
    return LEFT if x > 210 else 0


def _actors(fight: Fight, arena: ArenaDef) -> int:
    """Place the audit's single opponent; campaign spawning is deliberately separate."""
    knight = fight.actor(1)
    knight.x, knight.y, knight.facing, knight.input_port = 250, 100, 3, 1
    knight.hp = knight.hp_max = 200
    knight.strength, knight.sword, knight.daggers = 3, 0x19, 99
    knight.type, knight.active, knight.inventory = KNIGHT_TYPE, 1, 1
    fight.inventories[1] = Inventory()
    fight.start_job(knight.idle_script, 1, KNIGHT_LIST, (250, 0, 100), 3, KNIGHT_TYPE)
    handle = fight.alloc_record()
    actor = fight.actor(handle)
    init = arena.creature
    if init.actor_type is None or init.hp is None:
        raise ValueError(f"fight audit: incomplete creature initialiser for {arena.name}")
    fight.register_tables(script_tables(init)).assign(actor)
    actor.x, actor.y, actor.facing = 80, 100, 1
    actor.type, actor.input_port = init.actor_type, init.input_port or 0
    actor.hp, actor.hp_max = init.hp, init.hp_max or 0
    actor.reach_x, actor.too_close_x = init.reach_x or 0, init.too_close_x or 0
    actor.depth_reach = init.depth_reach or 0
    fight.start_job(init.idle_script, handle, CREATURE_LIST, (80, 0, 100), 1, init.actor_type)
    fight.first, fight.fight_active = 1, True
    fight.total = fight.max_alive = fight.alive_now = 1
    return handle


def _poke_tables(fight: Fight, keys: TableKeys, arena: ArenaDef) -> None:
    for poke in arena.knight_pokes:
        key = keys.action_scripts if poke.table == "action" else keys.hurt_scripts
        fight.tables[key][poke.offset // 4] = poke.script


def build_fight_audit(bundle: GameAssetBundle, name: str) -> FightAudit:
    """Build a single-Trogg preview entirely on the persistent engine (§10.17 to §10.20)."""
    if name not in TROGG_ARENAS:
        raise ValueError(f"fight audit: unsupported arena {name}")
    executable = parse_hunk_executable("mog", bundle.disk_set.read_file("mog"))
    defs = parse_fight_defs(executable)
    arena = next(a for a in defs.arenas if a.name == name)
    loaded = bundle.get_arena("fo1.t")
    hits, palette_tables = bundle.get_hit_sets(), bundle.get_fight_palette_tables()
    if loaded is None or hits is None or palette_tables is None:
        raise ValueError("fight audit: missing arena, hit sets or palette tables")
    files = cel_files(defs.knight_cels, arena.cels)
    cels = _load_cels(bundle, files)
    mog = MogCode(executable)
    fight = Fight(
        script_image(mog),
        cel_shapes(files, cels, hits),
        frame_lists(defs.knight_cels, arena.cels),
        [Actor() for _ in range(4)],
        Actor(),
        fight_constants(mog),
        rng=Rng(0x2468ACE1),
    )
    rules = parse_knight_rules(executable)
    install_knight_control(fight, rules)
    install_brawler(fight, parse_brawler_rules(executable), rules)
    knight_tables = fight.register_tables(script_tables(defs.knight))
    knight_tables.assign(fight.actor(1))
    _poke_tables(fight, knight_tables, arena)
    opponent = _actors(fight, arena)
    picture = loaded[1]
    words = [pack_rgb12(r >> 4, g >> 4, b >> 4) for r, g, b in picture.palette]
    palette = assemble_fight_palette(
        words, palette_tables, scene=arena.scene, region=REGION_FOREST, first_kind=0
    )
    scene = FightScene(fight, picture.pixels, cels, palette_fx_to_rgb(palette))
    return FightAudit(scene, rules, opponent)


def build_caster_audit(bundle: GameAssetBundle, moon_frame: int = 0) -> FightAudit:
    """One Ratman with its original flight anchor; campaign waves belong to T11.11."""
    from pymoonstone.app.fight_runtime import FightLoader

    loader = FightLoader(bundle)
    arena = next(a for a in loader.defs.arenas if a.name == "ratmen")
    arena = replace(arena, total=1, max_alive=1, first_wave=arena.first_wave[:1])
    knights = [Actor() for _ in range(4)]
    knight = knights[0]
    # Size this explicit one-opponent fixture as a starting knight, then equip its test HP.
    knight.hp = knight.hp_max = 20
    knight.strength, knight.sword, knight.inventory = 3, 0x19, 1
    runtime = loader.creature(
        arena, "fo1.t", knights, Actor(), {1: Inventory()}, Rng(0x2468ACE1), 1, moon_frame
    )
    knight.hp = knight.hp_max = 200
    opponent = next(j.owner for j in runtime.fight.engine.jobs if j.type == CASTER_TYPE)
    return FightAudit(runtime.scene, loader.knight_rules, opponent)


def caster_pose_frames(bundle: GameAssetBundle) -> Iterator[tuple[str, FightScene]]:
    """Decoded script previews for visual review, separate from the seeded outcome trace."""
    from pymoonstone.app.fight_runtime import FightLoader

    loader = FightLoader(bundle)
    arena = next(a for a in loader.defs.arenas if a.name == "ratmen")
    scripts = loader.caster_rules.scripts
    poses = (
        ("idle", arena.creature.idle_script),
        ("walk", next(s for s in arena.creature.walk_scripts if s)),
        ("attack", scripts.strike8),
        ("hurt", arena.creature.hurt_scripts[8 // 4]),
        ("death", scripts.death),
    )
    for name, script in poses:
        yield from _pose_frames(build_caster_audit(bundle), name, script)


def _pose_frames(audit: FightAudit, name: str, script: int) -> Iterator[tuple[str, FightScene]]:
    fight = audit.scene.fight
    me = fight.actor(audit.opponent)
    me.hp = 0xFFFF if name == "death" else me.hp
    job = fight.find_job(audit.opponent)
    if job is None:
        raise ValueError("Ratmen pose preview has no creature job")
    job.paused = 0
    job.x, job.height, job.depth = 150, 0, 120
    me.x, me.height, me.y = 150, 0, 120
    fight.restart_job(audit.opponent, script)
    # Step the script engine directly to retain the selected pose through its end.
    for frame in range(12):
        fight.engine.vbl = frame * 6
        output = fight.engine.tick()
        audit.scene.canvas.accept(output.draws)
        if frame in (0, 3, 7) and any(d.cel.startswith("Ratmen") for d in output.draws):
            yield f"{name}_frame{frame + 1}", audit.scene


def fight_audit_frames(audit: FightAudit) -> Iterator[tuple[str, FightScene]]:
    """Sample a real-handler fight through its death branch, retaining the last frame."""
    fight = audit.scene.fight
    for frame in range(_MAX_FRAMES):
        fight.ports[0] = audit.input_word() if fight.fight_active else 0
        audit.scene.advance((frame + 1) * 6)
        if frame in _SAMPLE_FRAMES:
            yield f"frame{frame + 1}", audit.scene
        if not fight.fight_active:
            yield f"outcome_frame{frame + 1}", audit.scene
            return
    raise RuntimeError("fight audit did not reach an outcome")


def fight_host_input(session: GameSession) -> InputFrame:
    """Audit-only bot inputs for the campaign host, including replacement Trogg waves."""
    active, loader = session.active_encounter, session.fight_loader
    if active is None or loader is None:
        raise ValueError("fight host audit requires an active, loaded fight")
    runtime = active.runtime
    targets = [
        j.owner
        for j in runtime.fight.engine.jobs
        if j.active
        and j.type in (*BRAWLER_TYPES, CASTER_TYPE)
        and s16(runtime.fight.actor(j.owner).hp) > 0
    ]
    word = 0
    if targets:
        first = runtime.fight.actor(runtime.first)
        target = min(
            targets, key=lambda handle: abs(s16(runtime.fight.actor(handle).x) - s16(first.x))
        )
        audit = FightAudit(runtime.scene, loader.knight_rules, target, runtime.first)
        word = audit.input_word()
    inp = PlayerInput(
        dx=int(bool(word & RIGHT)) - int(bool(word & LEFT)),
        dy=int(bool(word & DOWN)) - int(bool(word & UP)),
        fire=bool(word & FIRE),
    )
    # The first keyboard/gamepad slot is RED in the input API, regardless of chosen class.
    from pymoonstone.sim.types import KnightId

    return InputFrame(players={KnightId.RED: inp})
