"""Building the fight engine's inputs, on synthetic data (ARCHITECTURE.md §10.17, §10.18)."""

from __future__ import annotations

import struct
import zlib

import numpy as np
import pytest

from pymoonstone.assets.cel import CelFrame, CelFrameMeta, DecodedCel
from pymoonstone.assets.fight_defs import CelFile, CelSet
from pymoonstone.assets.fight_palette import MogVersionError
from pymoonstone.assets.fight_program import (
    _ROUTINES,
    CREATURE_LIST,
    EFFECT_LIST,
    GUARDIAN_HUNK,
    KNIGHT_LIST,
    MAP_LIST,
    cel_files,
    cel_shape,
    cel_shapes,
    fight_constants,
    frame_lists,
    script_image,
)
from pymoonstone.assets.hit import HitFrame, HitPoint, HitSet
from pymoonstone.assets.hunk import HUNK_CODE, HUNK_DATA, DecodedHunkExecutable, HunkSection
from pymoonstone.assets.mog_code import CODE_HUNK, DATA_HUNK, MogCode


def _put(buffer: bytearray, pos: int, *words: int) -> None:
    struct.pack_into(f">{len(words)}H", buffer, pos, *words)


def _mog(*, break_entry: bool = False) -> MogCode:
    code = bytearray(0x7400)
    guardian = bytearray(0xA20)
    for hunk, offset, opcode, _ in _ROUTINES:
        _put(code if hunk == CODE_HUNK else guardian, offset, opcode)
    _put(code, 0x38C, 0x157C, 0x0023, 0x0010)
    _put(code, 0x400, 0x317C, 0xFFFF, 0x0050)
    if break_entry:
        _put(code, 0x380, 0x4E71)
    data = bytearray(40)
    struct.pack_into(">I", data, 10, 0x035A)
    struct.pack_into(">I", data, 20, 0x041C)
    struct.pack_into(">I", data, 30, 0x1234)
    sections = []
    for index in range(GUARDIAN_HUNK + 1):
        payload, relocs = bytes(8), {}
        if index == CODE_HUNK:
            payload = bytes(code)
        elif index == DATA_HUNK:
            payload, relocs = bytes(data), {CODE_HUNK: (10, 30), GUARDIAN_HUNK: (20,)}
        elif index == GUARDIAN_HUNK:
            payload = bytes(guardian)
        kind = HUNK_CODE if index in (CODE_HUNK, GUARDIAN_HUNK) else HUNK_DATA
        sections.append(HunkSection(index, kind, len(payload), payload, relocs))
    executable = DecodedHunkExecutable(name="mog", sections=tuple(sections))
    return MogCode(executable, supported=(zlib.crc32(bytes(code)),))


def test_script_image_names_the_routines_its_longs_point_at() -> None:
    image = script_image(_mog())
    assert image.routines == {10: "fighter_died", 20: "guardian_1"}
    assert len(image.data) == 40


def test_a_routine_entry_with_the_wrong_instruction_is_refused() -> None:
    with pytest.raises(MogVersionError, match="routine end_fight"):
        script_image(_mog(break_entry=True))


def test_fight_constants_are_read_from_the_routines() -> None:
    constants = fight_constants(_mog())
    assert (constants.end_delay, constants.dead_hp) == (0x23, 0xFFFF)


def _frame(width: int, height: int, mask: np.ndarray) -> CelFrame:
    meta = CelFrameMeta(0, 0, width, height, 1, 0x0F)
    return CelFrame(meta, mask.astype(np.uint8), mask)


def test_cel_shape_packs_masks_across_the_aligned_row_and_attaches_hits() -> None:
    mask = np.zeros((2, 18), dtype=np.bool_)
    mask[0, 0] = mask[1, 17] = True
    cel = DecodedCel(2, 0, 0, (_frame(18, 2, mask), _frame(0, 0, np.zeros((0, 0), bool))), b"")
    hits = HitSet("w.cel", "w", (HitFrame(0, 1, 3, 4, (HitPoint(3, 4),)), HitFrame(1, 0, 0, 0, ())))
    shape = cel_shape("w.cel", cel, hits)
    first = shape.frames[0]
    assert (first.width, first.height, first.aligned_width) == (18, 2, 32)
    assert first.mask_rows == (1 << 31, 1 << (31 - 17))
    assert shape.frames[1].mask_rows == ()
    assert shape.hits is not None
    assert shape.hits[0] is not None
    assert (shape.hits[0].max_dx, shape.hits[0].points, shape.hits[1]) == (3, ((3, 4),), None)
    assert cel_shape("w.cel", cel, None).hits is None


KNIGHT = CelSet(
    kind=None,
    slots=(None,) * 5,
    knight_slots=(CelFile("kn1.ob", False), None, None, CelFile("kn4.ob", True), None),
    effect_slots=(CelFile("blo.cel", False),) * 5,
    sound_bank="kn.a",
)
ARENA = CelSet(
    kind=0x30,
    slots=(CelFile("Balok1.cel", True), None, None, None, None),
    knight_slots=(None, None, None, None, CelFile("Kn5.ob", False)),
    effect_slots=(None,) * 5,
    sound_bank="Ba.a",
)


def test_frame_lists_overlay_the_arena_loader_on_the_knight_loader() -> None:
    lists = frame_lists(KNIGHT, ARENA)
    assert lists[KNIGHT_LIST] == ("kn1.ob", None, None, "kn4.ob", "Kn5.ob")
    assert lists[CREATURE_LIST] == ("Balok1.cel", None, None, None, None)
    assert lists[MAP_LIST] == (None,) * 5
    assert lists[EFFECT_LIST] == ("blo.cel",) * 5
    assert frame_lists(KNIGHT, None)[CREATURE_LIST] == (None,) * 5


def test_cel_shapes_attach_hit_sets_only_where_the_loader_registers_them() -> None:
    files = cel_files(KNIGHT, ARENA)
    assert set(files) == {"kn1.ob", "kn4.ob", "blo.cel", "Balok1.cel", "Kn5.ob"}
    blank = DecodedCel(1, 0, 0, (_frame(16, 1, np.ones((1, 16), dtype=np.bool_)),), b"")
    cels = dict.fromkeys(files, blank)
    hit_set = HitSet("x", "x", (HitFrame(0, 1, 1, 1, (HitPoint(1, 1),)),))
    shapes = cel_shapes(files, cels, {"kn4": hit_set, "balok1": hit_set, "kn1": hit_set})
    assert shapes["kn4.ob"].hits is not None
    assert shapes["kn1.ob"].hits is None
    with pytest.raises(MogVersionError, match=r"no hit set for kn4\.ob"):
        cel_shapes(files, cels, {"balok1": hit_set})
