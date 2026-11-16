"""Knight control on the real disks (T11.9 acceptance, ARCHITECTURE.md §10.19).

* Each of the nine actions, chosen through the joystick and the action tables read from ``mog``,
  plays its own script to its end: the draws from the frame the handler starts it until the job
  reports the end equal the draws of a job started directly on ``action_scripts[action]``, and
  the handler is not called in between.
* A blow reduces the other knight's hit points by the table value of the action plus strength
  and the sword bonus (doubled for the heavy blow), for every sword.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from typing import TYPE_CHECKING

import pytest

from pymoonstone.app.scene_assets import GameAssetBundle
from pymoonstone.assets.adf import DiskSet
from pymoonstone.assets.fight_defs import FightDefs, parse_fight_defs
from pymoonstone.assets.fight_program import (
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
from pymoonstone.sim.actor import Actor
from pymoonstone.sim.fight import POOL_FIRST, Fight, FightConstants
from pymoonstone.sim.fighter_rules import HurtRule, KnightRules
from pymoonstone.sim.fighters import KNIGHT_TYPE, install_knight_control
from pymoonstone.sim.inventory import Inventory

if TYPE_CHECKING:
    from pathlib import Path

    from pymoonstone.sim.fight_script import CelShape, Draw, ScriptImage

pytestmark = pytest.mark.adf

FIRE = 0x10
MAX_FRAMES = 120
ACTIONS = tuple(range(0, 36, 4))
# Behaviour types of §9.8 that can hit a knight.
ATTACKER_TYPES = (0x00, 0x04, 0x08, 0x0C, 0x10, 0x14, 0x18, 0x1C, 0x20, 0x24, 0x28, 0x2C, 0x30)


@dataclass(frozen=True)
class Setup:
    defs: FightDefs
    rules: KnightRules
    image: ScriptImage
    constants: FightConstants
    lists: dict[int, tuple[str | None, ...]]
    shapes: dict[str, CelShape]


@pytest.fixture(scope="module")
def setup(adf_dir: Path) -> Setup:
    bundle = GameAssetBundle(DiskSet.from_directory(adf_dir))
    executable = parse_hunk_executable("mog", bundle.disk_set.read_file("mog"))
    mog = MogCode(executable)
    defs = parse_fight_defs(executable)
    files = cel_files(defs.knight_cels, None)
    cels = {}
    for name in files:
        cel = bundle.get_cel(name)
        assert cel is not None, name
        cels[name] = cel
    hit_sets = bundle.get_hit_sets()
    assert hit_sets is not None
    return Setup(
        defs=defs,
        rules=parse_knight_rules(executable),
        image=script_image(mog),
        constants=fight_constants(mog),
        lists=frame_lists(defs.knight_cels, None),
        shapes=cel_shapes(files, cels, hit_sets),
    )


def _fight(setup: Setup, places: tuple[tuple[int, int], ...], *, sword: int = 0x16) -> Fight:
    """Knights at ``(x, facing)`` on depth 100, each running its idle script."""
    knights = [Actor() for _ in range(4)]
    fight = Fight(setup.image, setup.shapes, setup.lists, knights, Actor(), setup.constants)
    install_knight_control(fight, setup.rules)
    keys = fight.register_tables(script_tables(setup.defs.knight))
    for handle, (x, facing) in enumerate(places, start=1):
        knight = fight.actor(handle)
        keys.assign(knight)
        knight.x, knight.y, knight.facing, knight.input_port = x, 100, facing, handle
        knight.hp = knight.hp_max = 20
        knight.strength, knight.sword, knight.daggers = 1, sword, 10
        knight.type, knight.active, knight.inventory = KNIGHT_TYPE, 1, handle
        fight.inventories[handle] = Inventory()
        fight.start_job(knight.idle_script, handle, KNIGHT_LIST, (x, 0, 100), facing, KNIGHT_TYPE)
    fight.first, fight.fight_active = 1, True
    return fight


def _word_for(rules: KnightRules, action: int) -> int:
    return FIRE | rules.actions_right.index(action)


def _draws(draws: list[Draw]) -> list[tuple[str, int, int, int, bool]]:
    return [(d.cel, d.frame, d.x, d.y, d.mirrored) for d in draws]


def test_the_rules_cover_every_attacker_and_every_action(setup: Setup) -> None:
    rules = setup.rules
    assert set(ATTACKER_TYPES) <= set(rules.hurt_rules)
    assert rules.hurt_rules[0x34] is HurtRule.CONTACT
    assert set(ACTIONS) <= set(rules.actions_right)
    assert set(ACTIONS) <= set(rules.actions_left)
    assert len(rules.steps.side) == len(rules.steps.up) == len(rules.steps.down) == 4


def _played_by_handler(setup: Setup, action: int) -> tuple[int, int, list[list[Draw]]]:
    """Start ``action`` through the joystick; the start frame, the script and its draws."""
    fight = _fight(setup, ((160, 1),))
    knight, job = fight.actor(1), fight.find_job(1)
    assert job is not None
    word = _word_for(setup.rules, action)
    script = setup.defs.knight.action_scripts[action // 4]
    for frame in range(MAX_FRAMES):
        fight.ports[0] = word
        output = fight.step(frame * 6)
        if knight.action == action and (action != 0 or frame > 0):
            break
    else:
        pytest.fail(f"action {action} was never chosen")
    start = frame
    draws = [output.tick.draws]
    fight.ports[0] = 0
    while job.running:
        frame += 1
        assert frame < MAX_FRAMES, f"action {action} never ended"
        draws.append(fight.step(frame * 6).tick.draws)
    return start, script, draws


def _played_directly(setup: Setup, script: int, start: int, frames: int) -> list[list[Draw]]:
    fight = _fight(setup, ())
    knight = fight.actor(1)
    knight.x, knight.y, knight.facing, knight.type, knight.active = 160, 100, 1, KNIGHT_TYPE, 1
    knight.daggers = 10
    fight.start_job(script, 1, KNIGHT_LIST, (160, 0, 100), 1, KNIGHT_TYPE)
    return [fight.step((start + i) * 6).tick.draws for i in range(frames)]


@pytest.mark.parametrize("action", ACTIONS)
def test_each_action_plays_its_own_script_to_its_end(setup: Setup, action: int) -> None:
    start, script, draws = _played_by_handler(setup, action)
    expected = _played_directly(setup, script, start, len(draws))
    assert [_draws(d) for d in draws] == [_draws(d) for d in expected]
    assert any(draws), f"action {action} drew nothing"


def _blow(setup: Setup, action: int, distance: int, sword: int) -> int:
    fight = _fight(setup, ((150, 1), (150 + distance, 3)), sword=sword)
    word, held = _word_for(setup.rules, action), True
    for frame in range(MAX_FRAMES):
        fight.ports[0] = word if held else 0
        fight.step(frame * 6)
        held = held and fight.actor(1).action != action
    return 20 - fight.actor(2).hp


@pytest.mark.parametrize(("action", "distance"), [(4, 20), (8, 40), (20, 30), (32, 20)])
@pytest.mark.parametrize("sword", [0x16, 0x17, 0x18, 0x19])
def test_a_blow_takes_the_table_value_for_action_and_sword(
    setup: Setup, action: int, distance: int, sword: int
) -> None:
    base = setup.defs.knight.damage_table[action // 4] + 1
    base += dict(setup.rules.damage.sword_bonus).get(sword, 0)
    expected = base * 2 if action == 0x20 else base
    assert _blow(setup, action, distance, sword) == expected


def test_a_thrown_dagger_hits_with_the_dagger_table(setup: Setup) -> None:
    assert _blow(setup, 0x0C, 70, 0x19) == setup.rules.dagger.damage[3]


def test_a_dagger_flies_off_the_screen_and_is_freed(setup: Setup) -> None:
    fight = _fight(setup, ((250, 1),))
    word, held = _word_for(setup.rules, 0x0C), True
    for frame in range(MAX_FRAMES):
        fight.ports[0] = word if held else 0
        fight.step(frame * 6)
        held = held and fight.actor(1).action != 0x0C
    dagger = fight.actor(POOL_FIRST)
    assert fight.actor(1).daggers == 9
    assert dagger.active == 0
    assert dagger.x >= setup.rules.dagger.right_edge


def test_holding_right_walks_until_the_other_knight_blocks(setup: Setup) -> None:
    fight = _fight(setup, ((100, 1), (220, 3)))
    knight = fight.actor(1)
    positions = []
    for frame in range(MAX_FRAMES):
        fight.ports[0] = 0x01
        fight.step(frame * 6)
        positions.append(knight.x)
    steps = {b - a for a, b in pairwise(positions)}
    assert steps <= {0, *setup.rules.steps.side}
    assert positions[-1] > 100
    assert positions[-1] < 220
    assert positions[-1] == positions[-20]
