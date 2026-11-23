"""Unit tests for Windows release legal-notice bundling in ``scripts/build_windows.py``."""

from __future__ import annotations

import importlib.metadata as importlib_metadata
import importlib.util
import sys
from email.message import Message
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast
from zipfile import ZipFile

import pytest

if TYPE_CHECKING:
    from os import PathLike

ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = ROOT / "scripts" / "build_windows.py"


class _LibxmpInfoProto(Protocol):
    version: str | None
    is_lgpl: bool


class _BuildWindowsModule(Protocol):
    BUILD_ONLY_DISTRIBUTIONS: frozenset[str]

    def build_third_party_licenses(
        self,
        *,
        distributions: list[importlib_metadata.Distribution] | None = None,
        bundle: Path | None = None,
        libxmp_info: _LibxmpInfoProto | None = None,
        freetype_year: int | None = None,
    ) -> str: ...
    def collect_runtime_distributions(
        self,
        *,
        include_pyinstaller: bool = True,
        bundle: Path | None = None,
    ) -> list[importlib_metadata.Distribution]: ...
    def verify_archive_contents(self, archive: Path) -> None: ...
    def verify_third_party_licenses(self, third_party: str, *, archive_names: set[str]) -> None: ...
    def is_runtime_system_dll(self, filename: str) -> bool: ...
    def _is_license_file(self, rel_path: str) -> bool: ...
    def _read_dist_license_texts(
        self, dist: importlib_metadata.Distribution
    ) -> list[tuple[str, str]]: ...
    def LibxmpInfo(self, version: str | None, is_lgpl: bool) -> _LibxmpInfoProto: ...


class _FakeDist(importlib_metadata.Distribution):
    def __init__(self, root: Path, name: str, version: str, rel_files: list[str]) -> None:
        self._root = root
        self._name = name
        self._version = version
        self._rel_files = rel_files

    @property
    def metadata(self) -> importlib_metadata.PackageMetadata:
        msg = Message()
        msg["Name"] = self._name
        msg["Version"] = self._version
        msg["License"] = "MIT"
        msg["Home-page"] = f"https://example.invalid/{self._name}"
        return cast("importlib_metadata.PackageMetadata", msg)

    @property
    def files(self) -> list[importlib_metadata.PackagePath]:
        return [importlib_metadata.PackagePath(rel) for rel in self._rel_files]

    def locate_file(self, path: str | PathLike[str]) -> Path:
        return self._root / str(path)

    def read_text(self, filename: str) -> str | None:
        target = self._root / filename
        return target.read_text(encoding="utf-8") if target.is_file() else None


def _load_build_windows() -> _BuildWindowsModule:
    spec = importlib.util.spec_from_file_location("build_windows_under_test", BUILD_SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return cast("_BuildWindowsModule", mod)


def _make_valid_zip(
    dest: Path,
    third_party: str,
    *,
    extra_entries: dict[str, bytes] | None = None,
) -> None:
    shaders_dir = ROOT / "src" / "harddaysengine" / "render" / "shaders"
    with ZipFile(dest, "w") as zipped:
        zipped.writestr("disks/", "")
        zipped.write(ROOT / "LICENSE", "LICENSE.txt")
        zipped.write(ROOT / "NOTICE", "NOTICE.txt")
        zipped.writestr("THIRD_PARTY_LICENSES.txt", third_party)
        for shader in sorted(shaders_dir.glob("*")):
            if shader.is_file():
                zipped.write(shader, f"_internal/harddaysengine/render/shaders/{shader.name}")
        for name, data in (extra_entries or {}).items():
            zipped.writestr(name, data)


def test_collect_runtime_distributions_excludes_build_only_packages() -> None:
    mod = _load_build_windows()
    dists = mod.collect_runtime_distributions(include_pyinstaller=True, bundle=None)
    names = {dist.name.lower().replace("_", "-") for dist in dists}
    for required in (
        "annotated-types",
        "glcontext",
        "moderngl",
        "numpy",
        "pillow",
        "pydantic",
        "pydantic-core",
        "pygame-ce",
        "typing-extensions",
        "typing-inspection",
    ):
        assert required in names
    for build_only in mod.BUILD_ONLY_DISTRIBUTIONS:
        assert build_only not in names
    assert "pyinstaller-hooks-contrib" not in names


def test_license_file_matching_and_deduplication(tmp_path: Path) -> None:
    mod = _load_build_windows()
    assert mod._is_license_file("packaging-26.3.dist-info/licenses/LICENSE")
    assert mod._is_license_file("packaging-26.3.dist-info/licenses/LICENSE.APACHE")
    assert mod._is_license_file("packaging-26.3.dist-info/licenses/LICENSE.BSD")
    assert mod._is_license_file("pkg-1.0.dist-info/COPYING.LESSER")
    assert mod._is_license_file("pkg-1.0.dist-info/NOTICE.txt")
    assert not mod._is_license_file("packaging/licenses/__init__.py")
    assert not mod._is_license_file("packaging/licenses/_spdx.py")

    lic_body = (
        "MIT License\n\nCopyright (c) 2026 Example\n\n"
        "Permission is hereby granted, free of charge, to any person obtaining a copy "
        "of this software and associated documentation files (the 'Software'), to deal "
        "in the Software without restriction, including without limitation the rights "
        "to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies."
    )
    rel_files = [
        "demo-1.0.dist-info/licenses/LICENSE",
        "demo-1.0.dist-info/licenses/LICENSE.APACHE",
        "demo-1.0.dist-info/licenses/LICENSE.BSD",
        "demo/LICENSE",
        "demo/licenses/__init__.py",
    ]
    for rel in rel_files:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if rel.endswith(".py"):
            path.write_text("# python code\n", encoding="utf-8")
        elif rel.endswith(".APACHE"):
            path.write_text("Apache License\nVersion 2.0\n" + lic_body, encoding="utf-8")
        elif rel.endswith(".BSD"):
            path.write_text("BSD License\n" + lic_body, encoding="utf-8")
        else:
            # Identical license in dist-info and package tree must be deduplicated,
            # keeping the canonical dist-info path.
            path.write_text(lic_body + "\r\n", encoding="utf-8")

    fake = _FakeDist(tmp_path, "demo", "1.0", rel_files)
    texts = mod._read_dist_license_texts(fake)
    paths = [rel for rel, _ in texts]
    assert paths == [
        "demo-1.0.dist-info/licenses/LICENSE",
        "demo-1.0.dist-info/licenses/LICENSE.APACHE",
        "demo-1.0.dist-info/licenses/LICENSE.BSD",
    ]


def test_numpy_license_collection_has_no_duplicate_texts() -> None:
    mod = _load_build_windows()
    numpy_dist = importlib_metadata.distribution("numpy")
    texts = mod._read_dist_license_texts(numpy_dist)
    paths = [rel for rel, _ in texts]
    assert "numpy/ma/LICENSE" not in paths
    assert "numpy/random/LICENSE.md" not in paths
    assert "numpy/_core/include/numpy/random/LICENSE.txt" not in paths
    assert any(rel.endswith("numpy/ma/LICENSE") and ".dist-info/" in rel for rel in paths)
    assert any(rel.endswith("numpy/random/LICENSE.md") and ".dist-info/" in rel for rel in paths)


def test_build_third_party_licenses_full_native_and_mandatory_notices(tmp_path: Path) -> None:
    mod = _load_build_windows()
    internal = tmp_path / "_internal"
    internal.mkdir()
    sample_dlls = (
        "python312.dll",
        "VCRUNTIME140.dll",
        "msvcp140-a4c2229bdc2a2a630acdc095b4d86008.dll",
        "SDL2.dll",
        "SDL2_image.dll",
        "SDL2_mixer.dll",
        "SDL2_ttf.dll",
        "freetype.dll",
        "libjpeg-62.dll",
        "libpng16-16.dll",
        "libtiff-5.dll",
        "libwebp-7.dll",
        "libwebpdemux-2.dll",
        "libogg-0.dll",
        "libopus-0.dll",
        "libopusfile-0.dll",
        "libwavpack-1.dll",
        "libxmp.dll",
        "portmidi.dll",
        "libscipy_openblas64_-ed4f167a5330424524f45258e7ca2c8d.dll",
        "libffi-8.dll",
    )
    for dll in sample_dlls:
        (internal / dll).write_bytes(b"")

    text = mod.build_third_party_licenses(
        bundle=tmp_path,
        libxmp_info=mod.LibxmpInfo("4.6.3", False),
        freetype_year=2026,
    )
    lower = text.lower()
    assert (
        "Portions of this software are copyright (c) 2026 The FreeType Project "
        "(https://freetype.org). All rights reserved."
    ) in text
    assert "This software is based in part on the work of the Independent JPEG Group." in text
    assert "LGPL-2.1 Compliance and Source Availability" in text
    assert "https://github.com/pygame-community/pygame-ce" in text
    assert "https://github.com/krj29b/HardDaysEngine" in text
    assert "--onedir" in text
    assert "package: amitools" not in lower
    assert "package: setuptools" not in lower
    assert "package: packaging" not in lower
    assert "package: altgraph" not in lower
    assert "package: pefile" not in lower
    assert "package: pywin32-ctypes" not in lower
    for lib_marker in (
        "Native Library: SDL2",
        "Native Library: SDL2_image",
        "Native Library: SDL2_mixer",
        "Native Library: SDL2_ttf",
        "Native Library: libogg",
        "Native Library: libopus",
        "Native Library: opusfile",
        "Native Library: WavPack",
        "Native Library: libxmp",
        "Native Library: PortMidi",
    ):
        assert lib_marker in text
    assert "David Bryant" in text
    assert "Claudio Matsuoka" in text
    assert "Roger B. Dannenberg" in text
    assert "Xiph.org Foundation" in text

    good_zip = tmp_path / "good.zip"
    _make_valid_zip(
        good_zip,
        text,
        extra_entries={f"_internal/{dll}": b"" for dll in sample_dlls},
    )
    mod.verify_archive_contents(good_zip)


def test_libxmp_lgpl_branch_updates_notice_when_older_version(tmp_path: Path) -> None:
    mod = _load_build_windows()
    internal = tmp_path / "_internal"
    internal.mkdir()
    (internal / "libxmp.dll").write_bytes(b"")
    text = mod.build_third_party_licenses(
        bundle=tmp_path,
        libxmp_info=mod.LibxmpInfo("4.6.0", True),
        freetype_year=2026,
    )
    assert "libxmp (4.6.0) under LGPL-2.1-or-later" in text
    assert "License: LGPL-2.1-or-later" in text


def test_unknown_dll_fails_generation_and_archive_verification(tmp_path: Path) -> None:
    mod = _load_build_windows()
    bad_bundle = tmp_path / "bad_bundle"
    (bad_bundle / "_internal").mkdir(parents=True)
    (bad_bundle / "_internal" / "libmystery-9.dll").write_bytes(b"")
    with pytest.raises(ValueError, match=r"libmystery-9\.dll"):
        mod.build_third_party_licenses(
            bundle=bad_bundle, libxmp_info=mod.LibxmpInfo("4.6.3", False)
        )

    good_bundle = tmp_path / "good_bundle"
    (good_bundle / "_internal").mkdir(parents=True)
    (good_bundle / "_internal" / "SDL2.dll").write_bytes(b"")
    valid_text = mod.build_third_party_licenses(
        bundle=good_bundle, libxmp_info=mod.LibxmpInfo("4.6.3", False)
    )
    bad_zip = tmp_path / "unknown_dll.zip"
    _make_valid_zip(
        bad_zip,
        valid_text,
        extra_entries={"_internal/SDL2.dll": b"", "_internal/libmystery-9.dll": b""},
    )
    with pytest.raises(ValueError, match=r"libmystery-9\.dll"):
        mod.verify_archive_contents(bad_zip)


def test_verify_archive_rejects_missing_files_pointer_stubs_and_setuptools(tmp_path: Path) -> None:
    mod = _load_build_windows()
    valid_text = mod.build_third_party_licenses(
        bundle=None, libxmp_info=mod.LibxmpInfo("4.6.3", False), freetype_year=2026
    )

    missing_notice = tmp_path / "missing_notice.zip"
    with ZipFile(missing_notice, "w") as zipped:
        zipped.writestr("disks/", "")
        zipped.write(ROOT / "LICENSE", "LICENSE.txt")
        zipped.writestr("THIRD_PARTY_LICENSES.txt", valid_text)
    with pytest.raises(ValueError, match=r"NOTICE\.txt"):
        mod.verify_archive_contents(missing_notice)

    missing_tp = tmp_path / "missing_tp.zip"
    with ZipFile(missing_tp, "w") as zipped:
        zipped.writestr("disks/", "")
        zipped.write(ROOT / "LICENSE", "LICENSE.txt")
        zipped.write(ROOT / "NOTICE", "NOTICE.txt")
    with pytest.raises(ValueError, match=r"THIRD_PARTY_LICENSES\.txt"):
        mod.verify_archive_contents(missing_tp)

    with_setuptools = tmp_path / "with_setuptools.zip"
    _make_valid_zip(
        with_setuptools,
        valid_text,
        extra_entries={"_internal/setuptools/__init__.py": b""},
    )
    with pytest.raises(ValueError, match="setuptools"):
        mod.verify_archive_contents(with_setuptools)

    # A pointer-only package section without substantive license text must fail verification.
    sep = "=" * 78
    pointer_stub = (
        f"{valid_text}\n{sep}\nPackage: stubpkg 1.0\nLicense: Dual\n{sep}\n\n"
        "--- stubpkg-1.0.dist-info/LICENSE ---\n\n"
        "See LICENSE.APACHE or LICENSE.BSD for terms.\n"
    )
    pointer_zip = tmp_path / "pointer_stub.zip"
    _make_valid_zip(pointer_zip, pointer_stub)
    with pytest.raises(ValueError, match="empty or pointer-only"):
        mod.verify_archive_contents(pointer_zip)

    # A build-only package listed without being bundled must fail verification.
    mit_stub = (
        "MIT License\n\nCopyright (c) 2026 Example\n\n"
        "Permission is hereby granted, free of charge, to any person obtaining a copy "
        "of this software and associated documentation files (the 'Software'), to deal "
        "in the Software without restriction, including without limitation the rights "
        "to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies "
        "of the Software."
    )
    build_only_stub = (
        f"{valid_text}\n{sep}\nPackage: pefile 2024.8.26\nLicense: MIT\n{sep}\n\n{mit_stub}\n"
    )
    build_only_zip = tmp_path / "build_only.zip"
    _make_valid_zip(build_only_zip, build_only_stub)
    with pytest.raises(ValueError, match="non-redistributed build package: pefile"):
        mod.verify_archive_contents(build_only_zip)

    # A frozen archive containing HardDaysEngine.exe must require PyInstaller notice.
    frozen_missing_pi = tmp_path / "frozen_missing_pi.zip"
    _make_valid_zip(frozen_missing_pi, valid_text, extra_entries={"HardDaysEngine.exe": b"MZ"})
    with pytest.raises(ValueError, match="missing entry for pyinstaller"):
        mod.verify_archive_contents(frozen_missing_pi)

    # A known DLL present in _internal/ but omitted from THIRD_PARTY_LICENSES.txt must fail.
    undocumented_dll = tmp_path / "undocumented_dll.zip"
    _make_valid_zip(undocumented_dll, valid_text, extra_entries={"_internal/SDL2.dll": b""})
    with pytest.raises(ValueError, match=r"Bundled Native Libraries|SDL2\.dll"):
        mod.verify_archive_contents(undocumented_dll)


def test_runtime_system_dll_allowlist_is_narrow() -> None:
    mod = _load_build_windows()
    for allowed in (
        "python3.dll",
        "python312.dll",
        "VCRUNTIME140.dll",
        "VCRUNTIME140_1.dll",
        "msvcp140-a4c2229bdc2a2a630acdc095b4d86008.dll",
        "ucrtbase.dll",
        "api-ms-win-crt-runtime-l1-1-0.dll",
    ):
        assert mod.is_runtime_system_dll(allowed)
    for third_party in (
        "SDL2.dll",
        "libxmp.dll",
        "libcrypto-3.dll",
        "libssl-3.dll",
        "libffi-8.dll",
        "libscipy_openblas64_-1234.dll",
        "libfoo.dll",
    ):
        assert not mod.is_runtime_system_dll(third_party)
