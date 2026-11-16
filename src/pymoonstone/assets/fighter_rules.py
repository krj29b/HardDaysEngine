"""Knight control numbers, scripts and rule tables read from ``mog`` (ARCHITECTURE.md §10.19).

Like :mod:`pymoonstone.assets.fight_defs` this module holds only *positions* in the supported
``mog`` version. Every value is read from the user's file: an operand after its instruction word
(compared), a relocated long after its instruction word, a table through its relocated ``LEA``,
and the two rule tables by running their fill routine (§10.15). A rule is named by the entry
offset of its code, whose first instruction word is compared too.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

from pymoonstone.assets.fight_palette import MogVersionError
from pymoonstone.assets.mog_code import (
    BSS_HUNK,
    CODE_HUNK,
    DATA_HUNK,
    OP_LEA_A0,
    OP_LEA_A2,
    MogCode,
    Pointer,
    StoreRun,
)
from pymoonstone.sim.fighter_rules import (
    ArenaClip,
    DaggerRules,
    DamageRules,
    HitRule,
    HurtAmounts,
    HurtRule,
    HurtScripts,
    KnightRules,
    Probes,
    WalkSteps,
)

if TYPE_CHECKING:
    from pymoonstone.assets.hunk import DecodedHunkExecutable

OBSTACLE_HUNK = 12
_STEP_ENTRIES = 4
_ACTION_ENTRIES = 16
_DAGGER_TABLE_ENTRIES = 9

# Instruction words.
_SUBI_W_D16_A1 = 0x0469
_SUBI_W_D0 = 0x0440
_MOVE_W_D0 = 0x303C
_MOVE_W_D1 = 0x323C
_CMP_W_D0 = 0xB07C
_CMP_W_D1 = 0xB27C
_CMP_W_D2 = 0xB47C
_CMPI_W_D16_A0 = 0x0C68
_CMPI_L_D16_A0 = 0x0CA8
_ADDI_W_D0 = 0x0640
_ADDI_W_ABS = 0x0679
_BTST_D1 = 0x0801
_MOVE_L_ABS = 0x23FC
_MOVEA_L_A0 = 0x207C
_MOVE_L_D16_A1 = 0x237C
_MOVE_W_D16_A1 = 0x337C

# (position, instruction word, displacement or None) of an immediate word.
Operand = tuple[int, int, int | None]

_STEP_LEAS = (0x52E0, 0x5304, 0x5320)
_ACTION_LEAS = (0x5346, 0x5352)
_CLIP: tuple[Operand, ...] = (
    (0x595C, _MOVE_W_D0, None),
    (0x5976, _CMP_W_D0, None),
    (0x5982, _CMP_W_D0, None),
    (0x596E, _MOVE_W_D1, None),
    (0x598E, _CMP_W_D1, None),
    (0x599A, _CMP_W_D1, None),
)
_VERTICAL_REACH: Operand = (0x862A, _CMP_W_D2, None)
_OBSTACLE_REACH: Operand = (0x0478, _ADDI_W_ABS, None)
_WALL_TOP: Operand = (0x04DE, _MOVE_W_D0, None)
_CLANG: Operand = (0x5446, _MOVE_W_D0, None)
_SWORD_COMPARES = (0x59C0, 0x59CE, 0x59DC)
_SWORD_ADDS = (0x59CA, 0x59D8, 0x59E6)
_SWORD_FIELD = 0x58
_MOON_TESTS = (0x5A06, 0x5A14, 0x5A22, 0x5A30)
_MOON_COMPARES = (0x5A0C, 0x5A1A, 0x5A28, 0x5A36)
_HP = 0x50
_Y = 0x08
_AMOUNTS: tuple[Operand, ...] = (
    (0x559A, _SUBI_W_D16_A1, _HP),
    (0x56F4, _SUBI_W_D16_A1, _HP),
    (0x55E6, _SUBI_W_D16_A1, _HP),
    (0x55F6, _SUBI_W_D16_A1, _HP),
    (0x55FE, _SUBI_W_D16_A1, _HP),
    (0x561C, _SUBI_W_D16_A1, _HP),
    (0x54A6, _SUBI_W_D16_A1, _HP),
    (0x5658, _SUBI_W_D16_A1, _Y),
    (0x5666, _MOVE_W_D0, None),
    (0x566C, _MOVE_W_D0, None),
    (0x5682, _SUBI_W_D0, None),
)
# (position, instruction word) of each fixed script, in HurtScripts field order.
_SCRIPTS: tuple[tuple[int, int], ...] = (
    (0x5778, _MOVE_L_ABS),
    (0x578A, _MOVE_L_ABS),
    (0x5554, _MOVE_L_ABS),
    (0x5564, _MOVE_L_ABS),
    (0x558A, _MOVE_L_ABS),
    (0x55BE, _MOVEA_L_A0),
    (0x54C4, _MOVE_L_ABS),
    (0x54EE, _MOVE_L_ABS),
    (0x5724, _MOVEA_L_A0),
    (0x571C, _MOVEA_L_A0),
    (0x5762, _MOVE_L_ABS),
    (0x5752, _MOVE_L_ABS),
    (0x5642, _MOVE_L_ABS),
    (0x569E, _MOVE_L_ABS),
)
_HOP_TABLE = 0x5694
_DAGGER_SCRIPT = 0x6FFC
_DAGGER_DAMAGE = 0x700E
_DAGGER_DAMAGE_FIELD = 0x2A
_DAGGER_ACTION: Operand = (0x7016, _MOVE_W_D16_A1, 0x40)
_DAGGER_FLIGHT = 0x702A
_DAGGER_RIGHT: Operand = (0x7044, _CMPI_W_D16_A0, 0x04)
_DAGGER_LEFT: Operand = (0x7062, _CMP_W_D0, None)

# The rule tables: the fill routine and the two dispatchers that read them.
_RULE_FILL = (0x57BA, 0x58B6)
_HURT_FILL_LEA = 0x57BA
_HIT_FILL_LEA = 0x5838
_HURT_DISPATCH_LEA = 0x5486
_HIT_DISPATCH_LEA = 0x537C
_TYPE_RANGE = 0x100
_HURT_ENTRIES: dict[int, tuple[int, HurtRule]] = {
    0x5772: (0x4A69, HurtRule.CONTACT),
    0x56C2: (0x4A69, HurtRule.KNIGHT),
    0x5504: (0x4A69, HurtRule.AXE),
    0x557E: (0x6100, HurtRule.SPEAR),
    0x54B0: (0x0C68, HurtRule.RATMEN),
    0x56F4: (0x0469, HurtRule.FLYER),
    0x55DE: (0x0C68, HurtRule.GUARDIAN),
    0x561C: (0x0469, HurtRule.STALKER),
    0x5492: (0x0C68, HurtRule.DRAKE),
    0x5652: (0x3368, HurtRule.DRAGON),
    0x566C: (0x303C, HurtRule.DRAGON_FLIGHT),
    0x5682: (0x0440, HurtRule.DRAGON_OBJECT),
}
_HIT_ENTRIES: dict[int, tuple[int, HitRule]] = {
    0x5388: (0x0C28, HitRule.CARRY_ON),
    0x566C: (0x303C, HitRule.HURT_BY_FLIGHT),
}


def _hunk_word(mog: MogCode, hunk: int, pos: int) -> int:
    value: int = struct.unpack_from(">H", mog.hunk_data(hunk), pos)[0]
    return value


def _operand(mog: MogCode, operand: Operand, what: str, hunk: int = CODE_HUNK) -> int:
    """The immediate word after a checked instruction word (and displacement, if any)."""
    pos, opcode, displacement = operand
    found = _hunk_word(mog, hunk, pos)
    if found != opcode:
        msg = f"mog: {what}: expected ${opcode:04X} at hunk {hunk} offset {pos}, found ${found:04X}"
        raise MogVersionError(msg)
    if displacement is not None and _hunk_word(mog, hunk, pos + 4) != displacement:
        raise MogVersionError(f"mog: {what}: unexpected displacement at code offset {pos + 4}")
    return _hunk_word(mog, hunk, pos + 2)


def _signed(word: int) -> int:
    return word - 0x10000 if word & 0x8000 else word


def _relocated(mog: MogCode, pos: int, opcode: int, hunk: int, what: str) -> int:
    """The relocated long operand of the instruction at ``pos``; it must point into ``hunk``."""
    mog.expect(pos, (opcode,), what)
    pointer = mog.pointer(pos + 2, what)
    if pointer.hunk != hunk:
        raise MogVersionError(f"mog: {what}: the operand at {pos} does not point into hunk {hunk}")
    return pointer.offset


def _words(mog: MogCode, lea_pos: int, count: int, what: str) -> tuple[int, ...]:
    pointer = mog.lea(lea_pos, OP_LEA_A2, what)
    if pointer.hunk != DATA_HUNK:
        raise MogVersionError(f"mog: {what}: the table is not in the data hunk")
    return tuple(_hunk_word(mog, DATA_HUNK, pointer.offset + 2 * i) for i in range(count))


def _clip(mog: MogCode) -> ArenaClip:
    values = [_signed(_operand(mog, operand, "arena clip")) for operand in _CLIP]
    return ArenaClip(*values)


def _probes(mog: MogCode) -> Probes:
    return Probes(
        vertical_reach=_operand(mog, _VERTICAL_REACH, "movement probe"),
        obstacle_reach=_operand(mog, _OBSTACLE_REACH, "obstacle probe", OBSTACLE_HUNK),
        wall_top=_operand(mog, _WALL_TOP, "obstacle probe", OBSTACLE_HUNK),
    )


def _damage(mog: MogCode) -> DamageRules:
    swords = []
    for compare, add in zip(_SWORD_COMPARES, _SWORD_ADDS, strict=True):
        mog.expect(compare, (_CMPI_L_D16_A0,), "sword bonus")
        if mog.word(compare + 6) != _SWORD_FIELD:
            raise MogVersionError(f"mog: sword bonus: unexpected field at code offset {compare}")
        swords.append((mog.long(compare + 2), _operand(mog, (add, _ADDI_W_D0, None), "sword")))
    moon = [
        (
            _operand(mog, (test, _BTST_D1, None), "moonstone bit"),
            _operand(mog, (compare, _CMP_W_D2, None), "moon frame"),
        )
        for test, compare in zip(_MOON_TESTS, _MOON_COMPARES, strict=True)
    ]
    return DamageRules(tuple(swords), tuple(moon))


def _amounts(mog: MogCode) -> HurtAmounts:
    return HurtAmounts(*(_operand(mog, operand, "hurt amount") for operand in _AMOUNTS))


def _scripts(mog: MogCode) -> HurtScripts:
    values = [_relocated(mog, pos, op, DATA_HUNK, "hurt script") for pos, op in _SCRIPTS]
    return HurtScripts(*values)


def _dagger(mog: MogCode) -> DaggerRules:
    what = "dagger"
    table = _relocated(mog, _DAGGER_DAMAGE, _MOVE_L_D16_A1, CODE_HUNK, what)
    if mog.word(_DAGGER_DAMAGE + 6) != _DAGGER_DAMAGE_FIELD:
        raise MogVersionError("mog: dagger: the damage table goes to another field")
    return DaggerRules(
        script=_relocated(mog, _DAGGER_SCRIPT, _MOVEA_L_A0, DATA_HUNK, what),
        flight_script=_relocated(mog, _DAGGER_FLIGHT, _MOVE_L_ABS, DATA_HUNK, what),
        damage=tuple(mog.long(table + 4 * i) for i in range(_DAGGER_TABLE_ENTRIES)),
        action=_operand(mog, _DAGGER_ACTION, what),
        right_edge=_signed(_operand(mog, _DAGGER_RIGHT, what)),
        left_margin=_signed(_operand(mog, _DAGGER_LEFT, what)),
    )


def _rule_of[R](mog: MogCode, routine: int, entries: dict[int, tuple[int, R]], what: str) -> R:
    known = entries.get(routine)
    if known is None:
        raise MogVersionError(f"mog: {what}: unknown rule routine at code offset {routine}")
    mog.expect(routine, (known[0],), what)
    return known[1]


def _rule_table[R](
    mog: MogCode,
    run: StoreRun,
    lea: tuple[int, int],
    span: int,
    entries: dict[int, tuple[int, R]],
    what: str,
) -> dict[int, R]:
    """Type byte to rule, from the cells the fill wrote; the dispatcher must read the same."""
    base = mog.lea(lea[0], OP_LEA_A0, what)
    if mog.lea(lea[1], OP_LEA_A2, what) != base or base.hunk != BSS_HUNK:
        raise MogVersionError(f"mog: {what}: the dispatcher reads another table")
    rules: dict[int, R] = {}
    for kind in range(0, span, 4):
        cell = run.memory.get(Pointer(BSS_HUNK, base.offset + kind))
        if cell is None:
            continue
        if cell.target != CODE_HUNK:
            raise MogVersionError(f"mog: {what}: type ${kind:02X} names no routine")
        rules[kind] = _rule_of(mog, cell.value, entries, what)
    return rules


def parse_knight_rules(mog_exec: DecodedHunkExecutable) -> KnightRules:
    """Read everything §10.19 needs from the user's ``mog``."""
    mog = MogCode(mog_exec)
    run = StoreRun(mog)
    run.run(*_RULE_FILL, "knight rule tables")
    hurt_base = mog.lea(_HURT_FILL_LEA, OP_LEA_A0, "hurt rules")
    hit_base = mog.lea(_HIT_FILL_LEA, OP_LEA_A0, "hit rules")
    hurt_span = hit_base.offset - hurt_base.offset
    side, up, down = (_words(mog, pos, _STEP_ENTRIES, "walk steps") for pos in _STEP_LEAS)
    right, left = (_words(mog, pos, _ACTION_ENTRIES, "action table") for pos in _ACTION_LEAS)
    return KnightRules(
        steps=WalkSteps(side, up, down),
        actions_right=right,
        actions_left=left,
        clip=_clip(mog),
        probes=_probes(mog),
        clang_sound=_operand(mog, _CLANG, "clang sound"),
        damage=_damage(mog),
        amounts=_amounts(mog),
        scripts=_scripts(mog),
        dagger=_dagger(mog),
        hop_table=_relocated(mog, _HOP_TABLE, _MOVE_L_ABS, DATA_HUNK, "hop table"),
        hurt_rules=_rule_table(
            mog, run, (_HURT_FILL_LEA, _HURT_DISPATCH_LEA), hurt_span, _HURT_ENTRIES, "hurt rules"
        ),
        hit_rules=_rule_table(
            mog, run, (_HIT_FILL_LEA, _HIT_DISPATCH_LEA), _TYPE_RANGE, _HIT_ENTRIES, "hit rules"
        ),
    )
