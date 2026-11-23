"""Build and smoke-test a Windows release ZIP for the running interpreter's architecture."""

from __future__ import annotations

import importlib.metadata as importlib_metadata
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from harddaysengine import __version__

ROOT = Path(__file__).resolve().parents[1]
SHADERS = Path("harddaysengine/render/shaders")
WINDOWS_ARCHITECTURES = {"win32": "x86", "win-amd64": "x64"}
REQUIRED_LEGAL_FILES = ("LICENSE.txt", "NOTICE.txt", "THIRD_PARTY_LICENSES.txt")

_LICENSE_STEM_PREFIXES = ("license", "licence", "copying", "notice", "lgpl")
_TEXT_SUFFIXES = frozenset({"", ".txt", ".md", ".rst"})
_DIST_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+")

# Native libraries redistributed inside pygame-ce / numpy Windows wheels whose upstream
# notices are not emitted as separate dist-info license files by the wheel builder.
_NATIVE_DLL_NOTICES: tuple[tuple[str, str, str, str], ...] = (
    ("sdl2", "SDL2 / SDL2_image / SDL2_mixer / SDL2_ttf", "Zlib", "https://www.libsdl.org/"),
    ("freetype", "FreeType", "FTL / GPL-2.0-or-later", "https://freetype.org/"),
    ("libjpeg", "libjpeg / libjpeg-turbo", "IJG / BSD-3-Clause", "https://libjpeg-turbo.org/"),
    ("libpng", "libpng", "libpng License", "http://www.libpng.org/pub/png/libpng.html"),
    ("libtiff", "libtiff", "libtiff License", "https://libtiff.gitlab.io/libtiff/"),
    ("libwebp", "libwebp", "BSD-3-Clause", "https://chromium.googlesource.com/webm/libwebp"),
    ("libogg", "libogg", "BSD-3-Clause", "https://xiph.org/ogg/"),
    ("libopus", "libopus / opusfile", "BSD-3-Clause", "https://opus-codec.org/"),
    ("libwavpack", "WavPack", "BSD-3-Clause", "https://www.wavpack.com/"),
    ("libxmp", "libxmp", "MIT", "https://xmp.sourceforge.net/"),
    ("portmidi", "PortMidi", "MIT", "https://github.com/PortMidi/portmidi"),
    ("openblas", "OpenBLAS (scipy-openblas)", "BSD-3-Clause", "https://www.openblas.net/"),
)


def _normalize_dist_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _parse_requirement_name(requirement: str) -> str | None:
    if "extra ==" in requirement or "extra==" in requirement:
        return None
    head = requirement.split(";", 1)[0].split("[", 1)[0].strip()
    match = _DIST_NAME_RE.match(head)
    return match.group(0) if match else None


def collect_runtime_distributions(
    *, include_pyinstaller: bool = True
) -> list[importlib_metadata.Distribution]:
    """Collect transitive runtime distributions of ``harddaysengine`` in sorted order."""
    seen: set[str] = set()
    queue: list[str] = ["harddaysengine"]
    if include_pyinstaller:
        queue.append("pyinstaller")
    found: dict[str, importlib_metadata.Distribution] = {}
    while queue:
        raw_name = queue.pop(0)
        norm = _normalize_dist_name(raw_name)
        if norm in seen:
            continue
        seen.add(norm)
        try:
            dist = importlib_metadata.distribution(raw_name)
        except importlib_metadata.PackageNotFoundError:
            continue
        if norm != "harddaysengine":
            found[norm] = dist
        for req in dist.requires or ():
            dep = _parse_requirement_name(req)
            if dep is not None:
                queue.append(dep)
    return [found[key] for key in sorted(found)]


def _is_license_file(rel_path: str) -> bool:
    path = Path(rel_path)
    lower_parts = [part.lower() for part in path.parts]
    if path.suffix.lower() not in _TEXT_SUFFIXES:
        return False
    if "licenses" in lower_parts and any(p.endswith(".dist-info") for p in lower_parts):
        return True
    stem = path.stem.lower()
    return stem.startswith(_LICENSE_STEM_PREFIXES)


def _read_dist_license_texts(dist: importlib_metadata.Distribution) -> list[tuple[str, str]]:
    texts: list[tuple[str, str]] = []
    seen_resolved: set[Path] = set()
    for pkg_file in sorted(dist.files or (), key=str):
        rel = str(pkg_file).replace("\\", "/")
        if not _is_license_file(rel):
            continue
        located = Path(str(dist.locate_file(pkg_file)))
        if not located.is_file():
            continue
        resolved = located.resolve()
        if resolved in seen_resolved:
            continue
        seen_resolved.add(resolved)
        content = located.read_text(encoding="utf-8", errors="replace").strip()
        if content:
            texts.append((rel, content))
    return texts


def _dist_metadata_lines(dist: importlib_metadata.Distribution) -> list[str]:
    meta = dist.metadata
    license_name = meta.get("License-Expression") or meta.get("License") or "See license text"
    license_one_line = license_name.strip().splitlines()[0].strip()
    author = meta.get("Author") or meta.get("Author-email") or meta.get("Maintainer") or ""
    urls = [meta.get("Home-page") or "", *(meta.get_all("Project-URL") or ())]
    clean_urls = [url.strip() for url in urls if url and url.strip()]
    lines = [
        f"Package: {dist.name} {dist.version}",
        f"License: {license_one_line}",
    ]
    if author.strip():
        lines.append(f"Author: {author.strip()}")
    if clean_urls:
        lines.append(f"URL: {clean_urls[0]}")
    return lines


def _read_python_license() -> str:
    stdlib_dir = Path(os.__file__).resolve().parent
    candidates = (
        Path(sys.base_prefix) / "LICENSE.txt",
        Path(sys.base_prefix) / "LICENSE",
        stdlib_dir / "LICENSE.txt",
        stdlib_dir / "LICENSE",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8", errors="replace").strip()
    if sys.platform != "win32":
        return (
            "Python Software Foundation License Version 2 (https://docs.python.org/3/license.html)"
        )
    raise FileNotFoundError(f"CPython license file not found under {sys.base_prefix}")


def _collect_native_dll_notices(bundle: Path | None) -> list[str]:
    if bundle is None or not bundle.is_dir():
        return []
    dll_names = sorted({p.name for p in bundle.rglob("*.dll")})
    if not dll_names:
        return []
    lines = [
        "Bundled Native Libraries",
        "------------------------",
        "The following third-party native DLLs are redistributed with this binary package:",
    ]
    for prefix, title, lic, url in _NATIVE_DLL_NOTICES:
        matched = [name for name in dll_names if prefix in name.lower()]
        if matched:
            lines.append(f"- {title} ({lic}) - {url}")
            lines.append(f"  DLLs: {', '.join(matched)}")
    return lines


def build_third_party_licenses(
    *,
    distributions: list[importlib_metadata.Distribution] | None = None,
    bundle: Path | None = None,
) -> str:
    """Assemble ``THIRD_PARTY_LICENSES.txt`` for the frozen Windows distribution."""
    dists = distributions if distributions is not None else collect_runtime_distributions()
    sep = "=" * 78
    sections: list[str] = [
        "HardDaysEngine Third-Party Licenses and Notices\n"
        f"Release version: {__version__}\n\n"
        "HardDaysEngine itself is licensed under the MIT License (see LICENSE.txt and\n"
        "NOTICE.txt). This portable Windows distribution bundles the Python runtime and\n"
        "the third-party libraries listed below under their respective licenses.",
        f"{sep}\nPython {sys.version.splitlines()[0]}\nLicense: PSF-2.0\n"
        f"URL: https://www.python.org/\n{sep}\n\n{_read_python_license()}",
    ]
    for dist in dists:
        header = "\n".join(_dist_metadata_lines(dist))
        texts = _read_dist_license_texts(dist)
        if texts:
            body_parts = [f"--- {rel} ---\n\n{text}" for rel, text in texts]
            body = "\n\n".join(body_parts)
        else:
            body = (
                "No standalone license file was shipped in this package's wheel metadata.\n"
                "See the License and URL fields above for the upstream license terms and source."
            )
        sections.append(f"{sep}\n{header}\n{sep}\n\n{body}")
    native_lines = _collect_native_dll_notices(bundle)
    if native_lines:
        sections.append(f"{sep}\n" + "\n".join(native_lines))
    return "\n\n".join(sections) + "\n"


def freeze(stage: Path) -> Path:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--onedir",
            "--console",
            "--name",
            "HardDaysEngine",
            "--paths",
            str(ROOT / "src"),
            "--hidden-import",
            "glcontext.wgl",
            "--add-data",
            f"{ROOT / 'src' / SHADERS}:{SHADERS.as_posix()}",
            str(ROOT / "scripts/windows_launcher.py"),
        ],
        cwd=stage,
        check=True,
    )
    bundle = stage / "dist/HardDaysEngine"
    (bundle / "disks").mkdir()
    shutil.copyfile(ROOT / "LICENSE", bundle / "LICENSE.txt")
    shutil.copyfile(ROOT / "NOTICE", bundle / "NOTICE.txt")
    (bundle / "THIRD_PARTY_LICENSES.txt").write_text(
        build_third_party_licenses(bundle=bundle), encoding="utf-8", newline="\n"
    )
    (bundle / "Start here.txt").write_text(
        "Copy your three original Moonstone Amiga disk images into the disks folder.\n"
        "Double-click HardDaysEngine.exe to play. Requires OpenGL 3.3.\n"
        "See LICENSE.txt, NOTICE.txt, and THIRD_PARTY_LICENSES.txt for license notices.\n",
        encoding="utf-8",
    )
    return bundle


def verify_archive_contents(archive: Path) -> None:
    """Validate archive structure, legal notices, shaders, and absence of ADF images."""
    with ZipFile(archive) as zipped:
        names = set(zipped.namelist())
        if "disks/" not in names or any(
            name.lower().endswith(".adf")
            or (name.lower().startswith("disks/") and name != "disks/")
            for name in names
        ):
            raise ValueError("The release must contain an empty disks folder and no ADF images")
        for legal in REQUIRED_LEGAL_FILES:
            if legal not in names:
                raise ValueError(f"Missing legal notice file {legal}")
        if zipped.read("LICENSE.txt") != (ROOT / "LICENSE").read_bytes():
            raise ValueError("LICENSE.txt does not match repository LICENSE")
        if zipped.read("NOTICE.txt") != (ROOT / "NOTICE").read_bytes():
            raise ValueError("NOTICE.txt does not match repository NOTICE")
        third_party = zipped.read("THIRD_PARTY_LICENSES.txt").decode("utf-8")
        for required_marker in ("Python ", "pygame-ce", "moderngl", "numpy", "pillow", "pydantic"):
            if required_marker.lower() not in third_party.lower():
                raise ValueError(f"THIRD_PARTY_LICENSES.txt missing entry for {required_marker}")
        for shader in (ROOT / "src" / SHADERS).glob("*"):
            if shader.is_file() and f"_internal/{SHADERS.as_posix()}/{shader.name}" not in names:
                raise ValueError(f"Missing shader {shader.name}")


def verify_archive(archive: Path, extracted: Path) -> None:
    verify_archive_contents(archive)
    with ZipFile(archive) as zipped:
        zipped.extractall(extracted)
    env = os.environ.copy()
    env["PATH"] = str(Path(os.environ["SYSTEMROOT"]) / "System32")
    for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "HARDDAYSENGINE_ADF_DIR"):
        env.pop(key, None)
    for args, code, expected in (
        (["--version"], 0, f"HardDaysEngine {__version__}"),
        (["--list-disks"], 1, "0 image(s) found"),
        (["--headless-ticks", "10"], 0, "ticks=10"),
    ):
        run = subprocess.run(
            [str(extracted / "HardDaysEngine.exe"), *args],
            cwd=extracted,
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        if run.returncode != code or expected not in run.stdout:
            raise RuntimeError(f"{args} returned {run.returncode}\n{run.stdout}\n{run.stderr}")


def main() -> None:
    architecture = WINDOWS_ARCHITECTURES.get(sysconfig.get_platform())
    if architecture is None:
        raise SystemExit("Build with x86 or x64 Python on Windows")
    archive_name = f"HardDaysEngine-Windows-{architecture}.zip"
    output = ROOT / "build"
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output) as temporary:
        stage = Path(temporary)
        bundle = freeze(stage)
        archive = stage / archive_name
        with ZipFile(archive, "w", ZIP_DEFLATED) as zipped:
            for path in sorted(bundle.rglob("*")):
                zipped.write(path, path.relative_to(bundle))
        verify_archive(archive, stage / "Extracted game")
        archive.replace(output / archive_name)
    print(f"Verified {output / archive_name}")


if __name__ == "__main__":
    main()
