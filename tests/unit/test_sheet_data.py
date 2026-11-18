"""Synthetic signed panel lists and invalid relocation handling (§12.25)."""

import struct
import zlib

import pytest

from harddaysengine.assets.fight_palette import MogVersionError
from harddaysengine.assets.hunk import HUNK_CODE, DecodedHunkExecutable, HunkSection
from harddaysengine.assets.mog_code import MogCode
from harddaysengine.assets.sheet_data import PanelPiece, _panel


def _mog(*, target: int = 0, terminated: bool = True) -> MogCode:
    code = bytearray(20 if terminated else 16)
    struct.pack_into(">HI", code, 0, 0x43F9, 8)
    struct.pack_into(">HhhH", code, 8, 0, -7, 13, 1)
    if terminated:
        struct.pack_into(">H", code, 16, 65535)
    sections = tuple(
        HunkSection(
            index=i,
            hunk_type=HUNK_CODE,
            alloc_size=len(code),
            data=bytes(code) if i == 0 else bytes(len(code)),
            relocs={target: (2,)} if i == 0 else {},
        )
        for i in range(5)
    )
    return MogCode(
        DecodedHunkExecutable(name="synthetic", sections=sections), supported=(zlib.crc32(code),)
    )


def test_panel_preserves_signed_positions_and_orientation() -> None:
    assert _panel(_mog(), 0) == (PanelPiece(0, -7, 13, 1),)


def test_panel_rejects_wrong_hunk_and_missing_terminator() -> None:
    with pytest.raises(MogVersionError, match="outside the code"):
        _panel(_mog(target=4), 0)
    with pytest.raises(MogVersionError, match="unterminated"):
        _panel(_mog(terminated=False), 0)
