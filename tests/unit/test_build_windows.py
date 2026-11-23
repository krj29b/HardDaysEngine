"""Unit tests for Windows release legal-notice bundling in ``scripts/build_windows.py``."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Protocol, cast
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = ROOT / "scripts" / "build_windows.py"


class _BuildWindowsModule(Protocol):
    def build_third_party_licenses(self, *, bundle: Path | None = None) -> str: ...
    def verify_archive_contents(self, archive: Path) -> None: ...


def _load_build_windows() -> _BuildWindowsModule:
    spec = importlib.util.spec_from_file_location("build_windows_under_test", BUILD_SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return cast("_BuildWindowsModule", mod)


def _make_minimal_valid_zip(dest: Path, third_party: str) -> None:
    shaders_dir = ROOT / "src" / "harddaysengine" / "render" / "shaders"
    with ZipFile(dest, "w") as zipped:
        zipped.writestr("disks/", "")
        zipped.write(ROOT / "LICENSE", "LICENSE.txt")
        zipped.write(ROOT / "NOTICE", "NOTICE.txt")
        zipped.writestr("THIRD_PARTY_LICENSES.txt", third_party)
        for shader in sorted(shaders_dir.glob("*")):
            if shader.is_file():
                zipped.write(shader, f"_internal/harddaysengine/render/shaders/{shader.name}")


def test_build_third_party_licenses_includes_python_and_all_runtime_deps(tmp_path: Path) -> None:
    mod = _load_build_windows()
    dll_dir = tmp_path / "_internal" / "pygame"
    dll_dir.mkdir(parents=True)
    (dll_dir / "SDL2.dll").write_bytes(b"")
    (dll_dir / "libscipy_openblas64.dll").write_bytes(b"")

    text = mod.build_third_party_licenses(bundle=tmp_path)
    lower = text.lower()
    assert "python " in lower
    assert "psf" in lower or "python software foundation" in lower
    for pkg in ("pygame-ce", "moderngl", "glcontext", "numpy", "pillow", "pydantic"):
        assert f"package: {pkg}" in lower
    assert "package: amitools" not in lower
    # pygame-ce ships its full LGPL text under pygame/docs/generated/LGPL.txt
    assert "gnu lesser general public license" in lower
    assert "bundled native libraries" in lower
    assert "SDL2.dll" in text
    assert "libscipy_openblas64.dll" in text


def test_verify_archive_contents_checks_license_notice_and_third_party(tmp_path: Path) -> None:
    mod = _load_build_windows()
    third_party = mod.build_third_party_licenses()

    good_zip = tmp_path / "good.zip"
    _make_minimal_valid_zip(good_zip, third_party)
    mod.verify_archive_contents(good_zip)

    missing_notice = tmp_path / "missing_notice.zip"
    with ZipFile(missing_notice, "w") as zipped:
        zipped.writestr("disks/", "")
        zipped.write(ROOT / "LICENSE", "LICENSE.txt")
        zipped.writestr("THIRD_PARTY_LICENSES.txt", third_party)
    with pytest.raises(ValueError, match=r"NOTICE\.txt"):
        mod.verify_archive_contents(missing_notice)

    missing_tp = tmp_path / "missing_tp.zip"
    with ZipFile(missing_tp, "w") as zipped:
        zipped.writestr("disks/", "")
        zipped.write(ROOT / "LICENSE", "LICENSE.txt")
        zipped.write(ROOT / "NOTICE", "NOTICE.txt")
    with pytest.raises(ValueError, match=r"THIRD_PARTY_LICENSES\.txt"):
        mod.verify_archive_contents(missing_tp)

    incomplete = tmp_path / "incomplete.zip"
    _make_minimal_valid_zip(incomplete, "Python 3.12 only\n")
    with pytest.raises(ValueError, match="missing entry"):
        mod.verify_archive_contents(incomplete)
