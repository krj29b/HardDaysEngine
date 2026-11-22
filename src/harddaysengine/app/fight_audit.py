"""Real-disk, single-opponent fight presentation audit (T11.8b1, §10.17 to §10.20).

This is an audit fixture, not campaign arena setup: one Trogg is placed on the same depth as
an equipped knight. Wave scaling, lanes, campaign entry and settlement belong to T11.11.
All scripts, creature values, control rules and palette rows come from the user's disks.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from harddaysengine.app.fight_scene import FightScene
from harddaysengine.app.input import InputFrame, PlayerInput
from harddaysengine.assets.creature_rules import parse_brawler_rules
from harddaysengine.assets.fight_defs import ArenaDef, parse_fight_defs
from harddaysengine.assets.fight_palette import REGION_FOREST, assemble_fight_palette
from harddaysengine.assets.fight_program import (
    CREATURE_LIST,
    KNIGHT_LIST,
    cel_files,
    cel_shapes,
    fight_constants,
    frame_lists,
    script_image,
    script_tables,
)
from harddaysengine.assets.fighter_rules import parse_knight_rules
from harddaysengine.assets.hunk import parse_hunk_executable
from harddaysengine.assets.mog_code import MogCode
from harddaysengine.render.scene import palette_fx_to_rgb
from harddaysengine.sim.actor import Actor
from harddaysengine.sim.fight import Fight
from harddaysengine.sim.fight_ai.brawler import BRAWLER_TYPES, install_brawler
from harddaysengine.sim.fight_ai.caster import CASTER_TYPE
from harddaysengine.sim.fight_ai.dragon import DRAGON_TYPE
from harddaysengine.sim.fight_ai.drake import DRAKE_TYPE, FLYING
from harddaysengine.sim.fight_ai.guardian import GUARDIAN_TYPE
from harddaysengine.sim.fight_ai.knight import COMPUTER_KNIGHT_TYPE
from harddaysengine.sim.fight_ai.snatcher import SNATCHER_TYPE
from harddaysengine.sim.fight_moves import DOWN, FIRE, LEFT, RIGHT, UP
from harddaysengine.sim.fight_routines import DAGGER_TYPE
from harddaysengine.sim.fighters import KNIGHT_TYPE, install_knight_control
from harddaysengine.sim.inventory import Inventory
from harddaysengine.sim.m68k import s16
from harddaysengine.sim.palette_fx import pack_rgb12
from harddaysengine.sim.rng import Rng

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from harddaysengine.app.game import GameSession
    from harddaysengine.app.scene_assets import GameAssetBundle
    from harddaysengine.assets.cel import DecodedCel
    from harddaysengine.assets.fight_defs import CelFile
    from harddaysengine.sim.fight import TableKeys
    from harddaysengine.sim.fighter_rules import KnightRules

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
    input_frames: int = 0

    def input_word(self) -> int:
        """Audit-only joystick bot: align, face, strike or throw one dagger at a time."""
        controller = {
            SNATCHER_TYPE: _snatcher_input,
            CASTER_TYPE: _caster_input,
            DRAKE_TYPE: _drake_input,
            GUARDIAN_TYPE: _guardian_input,
            COMPUTER_KNIGHT_TYPE: _guardian_input,
            DRAGON_TYPE: _dragon_input,
        }.get(self.scene.fight.actor(self.opponent).type, _standard_input)
        return controller(self)


def _snatcher_input(audit: FightAudit) -> int:
    audit.input_frames += 1
    return UP if audit.input_frames <= 20 else FIRE | LEFT | DOWN


def _standard_input(audit: FightAudit) -> int:
    fight = audit.scene.fight
    knight, target = fight.actor(audit.first), fight.actor(audit.opponent)
    dy = s16(target.y) - s16(knight.y)
    if abs(dy) > 3:
        return DOWN if dy > 0 else UP
    dx = s16(target.x) - s16(knight.x)
    if (knight.facing == 1) != (dx > 0):
        return RIGHT if dx > 0 else LEFT
    return _attack_word(fight, audit.rules, abs(dx), audit.first)


def _dragon_input(audit: FightAudit) -> int:
    """Audit-only sword policy: close beneath the Dragon and use action $18."""
    fight = audit.scene.fight
    knight, dragon = fight.actor(audit.first), fight.actor(5)
    dx, dy = s16(dragon.x - knight.x), s16(dragon.y - knight.y)
    if abs(dy) > 3:
        return DOWN if dy > 0 else UP
    if (knight.facing == 1) != (dx > 0):
        return RIGHT if dx > 0 else LEFT
    if abs(dx) > 40:
        return RIGHT if dx > 0 else LEFT
    table = audit.rules.actions_right if knight.facing == 1 else audit.rules.actions_left
    return FIRE | table.index(0x18)


def _guardian_input(audit: FightAudit) -> int:
    """Audit bot aligns, faces and uses the heavy sword move against the Guardian."""
    f = audit.scene.fight
    knight, target = f.actor(audit.first), f.actor(audit.opponent)
    dy, dx = s16(target.y - knight.y), s16(target.x - knight.x)
    if abs(dy) > 3:
        return DOWN if dy > 0 else UP
    if (knight.facing == 1) != (dx > 0):
        return RIGHT if dx > 0 else LEFT
    table = audit.rules.actions_right if knight.facing == 1 else audit.rules.actions_left
    if abs(dx) > 100:
        return RIGHT if dx > 0 else LEFT
    return FIRE | table.index(0x20)


def _drake_input(audit: FightAudit) -> int:
    """Audit controller: sidestep a pounce, then close and thrust at a landed Balok."""
    knight = audit.scene.fight.actor(audit.first)
    if audit.scene.fight.drake.flags & FLYING:
        if knight.y > 95:
            return UP
        return RIGHT if knight.x < 250 else LEFT
    return _caster_input(audit, reach=110)


def _caster_input(audit: FightAudit, *, reach: int = 50) -> int:
    """Audit melee policy for Ratmen and grounded Balok (§10.23 / §10.28)."""
    fight = audit.scene.fight
    knight, target = fight.actor(audit.first), fight.actor(audit.opponent)
    dx, dy = s16(target.x) - s16(knight.x), s16(target.y) - s16(knight.y)
    if abs(dy) > 6:
        return DOWN if dy > 0 else UP
    if (knight.facing == 1) != (dx > 0):
        return RIGHT if dx > 0 else LEFT
    if abs(dx) > reach:
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
    from harddaysengine.app.fight_runtime import FightLoader

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
    from harddaysengine.app.fight_runtime import FightLoader

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


def _pose_frames(
    audit: FightAudit,
    name: str,
    script: int,
    samples: tuple[int, ...] = (0, 3, 7),
) -> Iterator[tuple[str, FightScene]]:
    fight = audit.scene.fight
    me = fight.actor(audit.opponent)
    me.hp = 0xFFFF if name == "death" else me.hp
    job = fight.find_job(audit.opponent)
    if job is None:
        raise ValueError("pose preview has no creature job")
    job.paused = 0
    job.x, job.height, job.depth = 150, 0, 120
    me.x, me.height, me.y = 150, 0, 120
    fight.restart_job(audit.opponent, script)
    creature_cels = frozenset(name for name in fight.frame_lists[CREATURE_LIST] if name is not None)
    # Step the script engine directly to retain the selected pose through its end.
    for frame in range(max(12, max(samples) + 1)):
        fight.engine.vbl = frame * 6
        output = fight.engine.tick()
        audit.scene.canvas.accept(output.draws)
        if frame in samples and any(d.cel in creature_cels for d in output.draws):
            yield f"{name}_frame{frame + 1}", audit.scene


def build_snatcher_audit(bundle: GameAssetBundle) -> FightAudit:
    """One original Mudman, with an explicit equipped-knight audit fixture (§10.25)."""
    from harddaysengine.app.fight_runtime import FightLoader

    loader = FightLoader(bundle)
    arena = next(a for a in loader.defs.arenas if a.name == "mudmen")
    arena = replace(arena, total=1, max_alive=1, first_wave=arena.first_wave[:1])
    knights = [Actor() for _ in range(4)]
    knight = knights[0]
    knight.hp = knight.hp_max = 20
    knight.strength, knight.sword, knight.inventory = 3, 0x19, 1
    runtime = loader.creature(
        arena, "sw1.t", knights, Actor(), {1: Inventory()}, Rng(0x2468ACE1), 1
    )
    knight.hp = knight.hp_max = 200
    opponent = next(j.owner for j in runtime.fight.engine.jobs if j.type == SNATCHER_TYPE)
    return FightAudit(runtime.scene, loader.knight_rules, opponent)


def snatcher_pose_frames(bundle: GameAssetBundle) -> Iterator[tuple[str, FightScene]]:
    """Mudmen script previews, separately labelled from natural outcome frames (§10.25)."""
    from harddaysengine.app.fight_runtime import FightLoader

    loader = FightLoader(bundle)
    arena = next(a for a in loader.defs.arenas if a.name == "mudmen")
    scripts = loader.snatcher_rules.scripts
    for name, script in (
        ("idle", arena.creature.idle_script),
        ("walk", next(s for s in arena.creature.walk_scripts if s)),
        ("attack", scripts.reach),
        ("hurt", scripts.hurt),
        ("death", scripts.hurt),
    ):
        yield from _pose_frames(build_snatcher_audit(bundle), name, script)


def build_flyer_audit(bundle: GameAssetBundle) -> FightAudit:
    """Single Flyer with original setup/pokes and explicit audit equipment (§10.26)."""
    from harddaysengine.app.fight_runtime import FightLoader

    loader = FightLoader(bundle)
    arena = next(a for a in loader.defs.arenas if a.name == "flyer")
    arena = replace(arena, total=1, max_alive=1, first_wave=arena.first_wave[:1])
    knights = [Actor() for _ in range(4)]
    knight = knights[0]
    knight.hp = knight.hp_max = 20
    knight.strength, knight.sword, knight.inventory, knight.daggers = 3, 0x19, 1, 99
    runtime = loader.creature(
        arena, "fo1.t", knights, Actor(), {1: Inventory()}, Rng(0x2468ACE1), 1
    )
    knight.hp = knight.hp_max = 200
    opponent = next(j.owner for j in runtime.fight.engine.jobs if j.type == 0)
    return FightAudit(runtime.scene, loader.knight_rules, opponent)


def flyer_pose_frames(bundle: GameAssetBundle) -> Iterator[tuple[str, FightScene]]:
    """Flyer script previews, distinct from natural handler execution (§10.26)."""
    from harddaysengine.app.fight_runtime import FightLoader

    loader = FightLoader(bundle)
    arena = next(a for a in loader.defs.arenas if a.name == "flyer")
    hurt = arena.creature.hurt_scripts[8 // 4]
    for name, script in (
        ("idle", arena.creature.idle_script),
        ("walk", next(s for s in arena.creature.walk_scripts if s)),
        ("attack", arena.creature.after_hit_script),
        ("hurt", hurt),
        ("death", hurt),
    ):
        samples = (0, 8, 10, 12) if name == "death" else (0, 3, 7)
        yield from _pose_frames(build_flyer_audit(bundle), name, script, samples)


def build_stalker_audit(bundle: GameAssetBundle) -> FightAudit:
    """Single Troll using original setup/pokes with explicit audit equipment (§10.27)."""
    from harddaysengine.app.fight_runtime import FightLoader

    loader = FightLoader(bundle)
    arena = next(a for a in loader.defs.arenas if a.name == "troll")
    arena = replace(arena, total=1, max_alive=1, first_wave=arena.first_wave[:1])
    knights = [Actor() for _ in range(4)]
    knight = knights[0]
    knight.hp = knight.hp_max = 20
    knight.strength, knight.sword, knight.inventory, knight.daggers = 3, 0x19, 1, 99
    runtime = loader.creature(
        arena, "fo1.t", knights, Actor(), {1: Inventory()}, Rng(0x2468ACE1), 1
    )
    knight.hp = knight.hp_max = 200
    opponent = next(j.owner for j in runtime.fight.engine.jobs if j.type == 0x40)
    # Explicit single-opponent fixture, independent of campaign entry lanes.
    for owner, x in ((1, 80), (opponent, 250)):
        actor, job = runtime.fight.actor(owner), runtime.fight.find_job(owner)
        if job is None:
            raise ValueError("Troll audit fixture has no fighter job")
        actor.x, actor.y = x, 100
        job.x, job.depth, job.paused = x, 100, 0
        runtime.fight.restart_job(owner, actor.idle_script)
    return FightAudit(runtime.scene, loader.knight_rules, opponent)


def stalker_pose_frames(bundle: GameAssetBundle) -> Iterator[tuple[str, FightScene]]:
    """Troll pose scripts use the fixed hurt operand, never its unusable hurt table (§10.27)."""
    from harddaysengine.app.fight_runtime import FightLoader

    loader = FightLoader(bundle)
    arena = next(a for a in loader.defs.arenas if a.name == "troll")
    rules = loader.stalker_rules
    for name, script in (
        ("idle", arena.creature.idle_script),
        ("walk", next(s for s in arena.creature.walk_scripts if s)),
        ("attack", rules.strike_script),
        ("heavy", rules.heavy_script),
        ("hurt", rules.hurt_script),
        ("death", rules.hurt_script),
    ):
        yield from _pose_frames(build_stalker_audit(bundle), name, script)


def build_drake_audit(bundle: GameAssetBundle) -> FightAudit:
    """One Balok using original entry, trajectories and explicit audit equipment (§10.28)."""
    from harddaysengine.app.fight_runtime import FightLoader

    loader = FightLoader(bundle)
    arena = next(a for a in loader.defs.arenas if a.name == "balok")
    arena = replace(arena, total=1, max_alive=1)
    knights = [Actor() for _ in range(4)]
    knight = knights[0]
    knight.hp = knight.hp_max = 20
    knight.strength, knight.sword, knight.inventory, knight.daggers = 3, 0x19, 1, 99
    runtime = loader.creature(
        arena, "fo1.t", knights, Actor(), {1: Inventory()}, Rng(0x2468ACE1), 1
    )
    knight.hp = knight.hp_max = 200
    opponent = next(j.owner for j in runtime.fight.engine.jobs if j.type == 0x30)
    return FightAudit(runtime.scene, loader.knight_rules, opponent)


def drake_pose_frames(bundle: GameAssetBundle) -> Iterator[tuple[str, FightScene]]:
    """Balok's fixed script previews, labelled apart from natural fight frames (§10.28)."""
    from harddaysengine.app.fight_runtime import FightLoader

    loader = FightLoader(bundle)
    arena = next(a for a in loader.defs.arenas if a.name == "balok")
    scripts = loader.drake_rules.scripts
    for name, script in (
        ("idle", arena.creature.idle_script),
        ("walk_flight", scripts.flight),
        ("attack", scripts.strike),
        ("heavy", scripts.heavy),
        ("grab", scripts.grab),
        ("hurt", scripts.hurt),
        ("death", scripts.hurt),
    ):
        yield from _pose_frames(build_drake_audit(bundle), name, script)


def build_guardian_audit(bundle: GameAssetBundle) -> FightAudit:
    """One original Guardian and its reserved body, with explicit audit equipment (§10.29)."""
    from harddaysengine.app.fight_runtime import FightLoader

    loader = FightLoader(bundle)
    arena = next(a for a in loader.defs.arenas if a.name == "guardian")
    knights = [Actor() for _ in range(4)]
    knight = knights[0]
    knight.hp = knight.hp_max = 200
    knight.strength, knight.sword, knight.inventory, knight.daggers = 3, 0x19, 1, 99
    runtime = loader.creature(
        arena, "fo1.t", knights, Actor(), {1: Inventory()}, Rng(0x2468ACE1), 1
    )
    return FightAudit(runtime.scene, loader.knight_rules, runtime.fight.guardian.main)


def build_dragon_audit(bundle: GameAssetBundle) -> FightAudit:
    """Persistent Dragon and its two original objects with explicit audit equipment (§10.30)."""
    from harddaysengine.app.fight_runtime import FightLoader

    loader = FightLoader(bundle)
    arena = next(a for a in loader.defs.arenas if a.name == "dragon")
    knights = [Actor() for _ in range(4)]
    knight = knights[0]
    knight.hp = knight.hp_max = 80
    knight.strength, knight.sword, knight.inventory, knight.daggers = 5, 0x19, 1, 10
    knight.armour = 0x1E
    inventory = Inventory()
    inventory.counts[4], inventory.counts[11] = 1, 2
    runtime = loader.creature(
        arena, "fo1.t", knights, Actor(), {1: inventory}, Rng(0x2468ACE1), 1, 45
    )
    return FightAudit(runtime.scene, loader.knight_rules, 5)


def dragon_pose_frames(bundle: GameAssetBundle) -> Iterator[tuple[str, FightScene]]:
    """Dragon and both objects, labelled separately from natural fight outcomes (§10.30)."""
    from harddaysengine.app.fight_runtime import FightLoader

    loader = FightLoader(bundle)
    arena = next(a for a in loader.defs.arenas if a.name == "dragon")
    scripts = loader.dragon_rules.scripts
    for name, script in (
        ("idle", arena.creature.idle_script),
        ("walk_flight", arena.creature.walk_scripts[loader.dragon_rules.near.block // 4]),
        ("attack", scripts.strike8),
        ("breath", scripts.breath),
        ("bite", scripts.bite),
        ("hurt", arena.creature.hurt_scripts[2]),
        ("death", arena.creature.hurt_scripts[2]),
    ):
        audit = build_dragon_audit(bundle)
        fight = audit.scene.fight
        # Preview positions shift the whole multipart fixture into view; gameplay uses decoded ones.
        for handle in (fight.dragon_cells.link_a, fight.dragon_cells.link_b):
            actor, job = fight.actor(handle), fight.find_job(handle)
            actor.x, actor.y = actor.x + 70, actor.y + 20
            if job is not None:
                job.x, job.depth, job.paused = actor.x, actor.y, 0
        samples = {
            "death": (0, 4, 9),
            "attack": (0, 3, 7),
            "breath": (0, 3, 7),
            "bite": (0, 3),
        }.get(name, (0,))
        yield from _pose_frames(audit, name, script, samples)


def guardian_pose_frames(bundle: GameAssetBundle) -> Iterator[tuple[str, FightScene]]:
    """Guardian multipart script previews, separate from ordinary-input fights (§10.29)."""
    from harddaysengine.app.fight_runtime import FightLoader

    loader = FightLoader(bundle)
    arena = next(a for a in loader.defs.arenas if a.name == "guardian")
    scripts = loader.guardian_rules.scripts
    for name, script in (
        ("idle", arena.creature.idle_script),
        ("walk", arena.creature.idle_script),
        ("attack", scripts.lunge),
        ("strike8", scripts.strike8),
        ("grab", scripts.grab7),
        ("hurt", scripts.hurt),
        ("death", scripts.hurt),
    ):
        audit = build_guardian_audit(bundle)
        # Complete the original tornado entrance so pose previews include the following body.
        for frame in range(32):
            audit.scene.advance(frame * 6)
        fight = audit.scene.fight
        body = fight.find_job(fight.guardian.body)
        if body is not None:
            body.x, body.depth = 150, 120
        if name in ("idle", "walk"):
            script = fight.actor(audit.opponent).idle_script
        samples = {"attack": (0, 3), "strike8": (0, 3, 7, 14), "death": (0, 3, 7)}
        yield from _pose_frames(audit, name, script, samples.get(name, (0,)))


def fight_audit_frames(audit: FightAudit) -> Iterator[tuple[str, FightScene]]:
    """Sample a real-handler fight through its death branch, retaining the last frame."""
    fight = audit.scene.fight
    port = 0 if fight.actor(audit.first).input_port == 1 else 1
    for frame in range(_MAX_FRAMES):
        fight.ports[port] = audit.input_word() if fight.fight_active else 0
        audit.scene.advance((frame + 1) * 6)
        if frame in _SAMPLE_FRAMES:
            yield f"frame{frame + 1}", audit.scene
        if not fight.fight_active:
            yield f"outcome_frame{frame + 1}", audit.scene
            return
    raise RuntimeError("fight audit did not reach an outcome")


def fight_host_input(session: GameSession) -> InputFrame:
    """Audit-only bot inputs for the campaign host, including replacement Trogg waves."""
    return _host_input(session, melee=False)


def fight_host_melee_input(session: GameSession) -> InputFrame:
    """Ordinary heavy-sword audit inputs for a naturally spawned lair wave."""
    return _host_input(session, melee=True)


def _host_input(session: GameSession, *, melee: bool) -> InputFrame:
    active, loader = session.active_encounter, session.fight_loader
    if active is None or loader is None:
        raise ValueError("fight host audit requires an active, loaded fight")
    runtime = active.runtime
    targets = [
        j.owner
        for j in runtime.fight.engine.jobs
        if j.active
        and j.type in (*BRAWLER_TYPES, CASTER_TYPE, GUARDIAN_TYPE)
        and s16(runtime.fight.actor(j.owner).hp) > 0
    ]
    word = 0
    if targets:
        first = runtime.fight.actor(runtime.first)
        target = min(
            targets, key=lambda handle: abs(s16(runtime.fight.actor(handle).x) - s16(first.x))
        )
        audit = FightAudit(runtime.scene, loader.knight_rules, target, runtime.first)
        word = _guardian_input(audit) if melee else audit.input_word()
    inp = PlayerInput(
        dx=int(bool(word & RIGHT)) - int(bool(word & LEFT)),
        dy=int(bool(word & DOWN)) - int(bool(word & UP)),
        fire=bool(word & FIRE),
    )
    # The first keyboard/gamepad slot is RED in the input API, regardless of chosen class.
    from harddaysengine.sim.types import KnightId

    return InputFrame(players={KnightId.RED: inp})


def build_computer_knight_audit(bundle: GameAssetBundle) -> FightAudit:
    """Human against black knight, with explicit audit equipment (§10.31)."""
    from harddaysengine.app.fight_runtime import FightLoader

    loader = FightLoader(bundle)
    knights = [
        Actor(kind=i, hp=20, hp_max=20, strength=3, sword=0x16, daggers=10, inventory=i + 1)
        for i in range(4)
    ]
    knights[0].hp = knights[0].hp_max = 200
    knights[0].sword = 0x19
    runtime = loader.duel(
        "fo1.t",
        knights,
        Actor(),
        {i: Inventory() for i in range(1, 5)},
        Rng(0x2468ACE1),
        1,
        2,
        computers=frozenset({2}),
    )
    # Audit input controller uses port0; campaign duels retain their original port order.
    knights[0].input_port = 1
    return FightAudit(runtime.scene, loader.knight_rules, 2)


def computer_knight_pose_frames(bundle: GameAssetBundle) -> Iterator[tuple[str, FightScene]]:
    """Knight script previews, distinct from natural AI outcomes (§10.31)."""
    from harddaysengine.app.fight_runtime import FightLoader

    init = FightLoader(bundle).defs.knight
    for name, script in (
        ("idle", init.idle_script),
        ("walk", init.walk_scripts[1]),
        ("attack", init.action_scripts[2]),
        ("guard", init.action_scripts[7]),
        ("hurt", init.hurt_scripts[2]),
        ("death", init.hurt_scripts[2]),
    ):
        samples = {"attack": (0, 3, 7), "hurt": (0, 3), "death": (0, 3, 12)}.get(name, (0,))
        yield from _pose_frames(build_computer_knight_audit(bundle), name, script, samples)
