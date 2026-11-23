"""Build and smoke-test a Windows release ZIP for the running interpreter's architecture."""

from __future__ import annotations

import ctypes
import importlib.metadata as importlib_metadata
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from harddaysengine import __version__

ROOT = Path(__file__).resolve().parents[1]
SHADERS = Path("harddaysengine/render/shaders")
WINDOWS_ARCHITECTURES = {"win32": "x86", "win-amd64": "x64"}
REQUIRED_LEGAL_FILES = ("LICENSE.txt", "NOTICE.txt", "THIRD_PARTY_LICENSES.txt")
HARDDAYSENGINE_SOURCE_URL = "https://github.com/krj29b/HardDaysEngine"
PYGAME_CE_SOURCE_URL = "https://github.com/pygame-community/pygame-ce"
_SECTION_SEP = "=" * 78

_LICENSE_NAME_PREFIXES = ("license", "licence", "copying", "notice", "lgpl")
_NON_LICENSE_SUFFIXES = frozenset(
    {
        ".c",
        ".cpp",
        ".css",
        ".dll",
        ".exe",
        ".gif",
        ".h",
        ".hpp",
        ".html",
        ".ico",
        ".jpg",
        ".js",
        ".json",
        ".otf",
        ".png",
        ".py",
        ".pyc",
        ".pyd",
        ".pyi",
        ".pyo",
        ".so",
        ".svg",
        ".toml",
        ".ttf",
        ".whl",
        ".woff",
        ".woff2",
        ".yaml",
        ".yml",
        ".zip",
    }
)
_DIST_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+")
_FTL_CREDIT_RE = re.compile(
    r"Portions of this software are copyright \(c\) \d{4} "
    r"The FreeType Project \(https://freetype\.org\)\. All rights reserved\."
)
_IJG_CREDIT = "This software is based in part on the work of the Independent JPEG Group."

# Build-time packages installed alongside PyInstaller that must not be listed in
# THIRD_PARTY_LICENSES.txt unless they are actually redistributed inside the archive.
BUILD_ONLY_DISTRIBUTIONS = frozenset(
    {
        "altgraph",
        "packaging",
        "pefile",
        "pywin32-ctypes",
        "setuptools",
    }
)
_REQUIRED_RUNTIME_PACKAGES = (
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
)
_FORBIDDEN_BUNDLE_PREFIXES = ("_internal/setuptools", "_internal/pkg_resources")

# Narrow allowlist for CPython interpreter, MSVC C/C++ runtime, and Windows UCRT/API-set DLLs.
# Every other DLL in _internal/ must be explicitly covered by _NATIVE_DLL_SPECS.
_RUNTIME_SYSTEM_DLL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^python3(\d+)?\.dll$"),
    re.compile(r"^vcruntime140(_\d+)?\.dll$"),
    re.compile(r"^msvcp140(_\d+)?(-[0-9a-f]+)?\.dll$"),
    re.compile(r"^concrt140\.dll$"),
    re.compile(r"^vcomp140\.dll$"),
    re.compile(r"^ucrtbase\.dll$"),
    re.compile(r"^api-ms-win-.*\.dll$"),
)

_SUBSTANTIVE_LICENSE_MARKERS: tuple[str, ...] = (
    "permission is hereby granted",
    "redistribution and use",
    "gnu lesser general public license",
    "gnu general public license",
    "apache license",
    "this software is provided",
    "python software foundation license",
    "without warranty of any kind",
    "is granted to anyone to use this software",
)
_MIN_LICENSE_TEXT_CHARS = 250

_FREETYPE_RELEASE_YEARS: dict[tuple[int, int], int] = {
    (2, 10): 2020,
    (2, 11): 2021,
    (2, 12): 2022,
    (2, 13): 2024,
    (2, 14): 2026,
}

_ZLIB_LICENSE_BODY = """\
This software is provided 'as-is', without any express or implied
warranty.  In no event will the authors be held liable for any damages
arising from the use of this software.

Permission is granted to anyone to use this software for any purpose,
including commercial applications, and to alter it and redistribute it
freely, subject to the following restrictions:

1. The origin of this software must not be misrepresented; you must not
   claim that you wrote the original software. If you use this software
   in a product, an acknowledgment in the product documentation would be
   appreciated but is not required.
2. Altered source versions must be plainly marked as such, and must not be
   misrepresented as being the original software.
3. This notice may not be removed or altered from any source distribution."""

_MIT_LICENSE_BODY = """\
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE."""

_LIBOGG_LICENSE = """\
Copyright (c) 2002, Xiph.org Foundation

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions
are met:

- Redistributions of source code must retain the above copyright
  notice, this list of conditions and the following disclaimer.

- Redistributions in binary form must reproduce the above copyright
  notice, this list of conditions and the following disclaimer in the
  documentation and/or other materials provided with the distribution.

- Neither the name of the Xiph.org Foundation nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
A PARTICULAR PURPOSE ARE DISCLAIMED.  IN NO EVENT SHALL THE FOUNDATION
OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE."""

_LIBOPUS_LICENSE = """\
Copyright 2001-2011 Xiph.Org, Skype Limited, Octasic,
                    Jean-Marc Valin, Timothy B. Terriberry,
                    CSIRO, Gregory Maxwell, Mark Borgerding,
                    Erik de Castro Lopo

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions
are met:

- Redistributions of source code must retain the above copyright
  notice, this list of conditions and the following disclaimer.

- Redistributions in binary form must reproduce the above copyright
  notice, this list of conditions and the following disclaimer in the
  documentation and/or other materials provided with the distribution.

- Neither the name of Internet Society, IETF or IETF Trust, nor the
  names of specific contributors, may be used to endorse or promote
  products derived from this software without specific prior written
  permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER
OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

Opus is subject to the royalty-free patent licenses which are
specified at:
Xiph.Org Foundation: https://datatracker.ietf.org/ipr/1524/"""

_OPUSFILE_LICENSE = """\
Copyright (c) 1994-2013 Xiph.Org Foundation and contributors.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions
are met:

- Redistributions of source code must retain the above copyright
  notice, this list of conditions and the following disclaimer.

- Redistributions in binary form must reproduce the above copyright
  notice, this list of conditions and the following disclaimer in the
  documentation and/or other materials provided with the distribution.

- Neither the name of the Xiph.Org Foundation nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
A PARTICULAR PURPOSE ARE DISCLAIMED.  IN NO EVENT SHALL THE FOUNDATION
OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE."""

_WAVPACK_LICENSE = """\
Copyright (c) 1998 - 2024 David Bryant. All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice,
  this list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of WavPack nor the names of its contributors may be used
  to endorse or promote products derived from this software without specific
  prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
ARE DISCLAIMED.  IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
POSSIBILITY OF SUCH DAMAGE."""

_PORTMIDI_LICENSE = (
    "Copyright (c) 1999-2000 Ross Bencina and Phil Burk\n"
    "Copyright (c) 2001-2009 Roger B. Dannenberg\n"
    "Copyright (c) 2021 Mixxx DJ Software Team\n\n"
    f"{_MIT_LICENSE_BODY}\n\n"
    "The text above constitutes the entire PortMidi license; however,\n"
    "the PortMusic community also makes the following non-binding requests:\n\n"
    "Any person wishing to distribute modifications to the Software is\n"
    "requested to send the modifications to the original developer so that\n"
    "they can be incorporated into the canonical version. It is also\n"
    "requested that these non-binding requests be included along with the\n"
    "license above."
)

_LIBXMP_MIT_LICENSE = (
    "Extended Module Player (libxmp >= 4.6.1)\n"
    "Copyright (C) 1996-2025 Claudio Matsuoka and Hipolito Carraro Jr\n\n"
    f"{_MIT_LICENSE_BODY}"
)

_LIBFFI_LICENSE = (
    "libffi - Copyright (c) 1996-2022 Anthony Green, Red Hat, Inc and others.\n\n"
    f"{_MIT_LICENSE_BODY}"
)

_LIBJPEG_TURBO_LICENSE = """\
The Modified (3-clause) BSD License (libjpeg-turbo)

Copyright (C) 2009-2026 D. R. Commander
Copyright (C) 2018-2023 Randy <randy408@protonmail.com>

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

- Redistributions of source code must retain the above copyright notice,
  this list of conditions and the following disclaimer.
- Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.
- Neither the name of the libjpeg-turbo Project nor the names of its
  contributors may be used to endorse or promote products derived from this
  software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS",
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
ARE DISCLAIMED.  IN NO EVENT SHALL THE COPYRIGHT HOLDERS OR CONTRIBUTORS BE
LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
POSSIBILITY OF SUCH DAMAGE.

Note: The full Independent JPEG Group (IJG) License also appears in the pillow
package section above, and the mandatory IJG acknowledgment appears at the top
of this notice."""

_PSF_FALLBACK_LICENSE = """\
PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2
--------------------------------------------

1. This LICENSE AGREEMENT is between the Python Software Foundation ("PSF"), and
the Individual or Organization ("Licensee") accessing and otherwise using this
software ("Python") in source or binary form and its associated documentation.

2. Subject to the terms and conditions of this License Agreement, PSF hereby
grants Licensee a nonexclusive, royalty-free, world-wide license to reproduce,
analyze, test, perform and/or display publicly, prepare derivative works,
distribute, and otherwise use Python alone or in any derivative version,
provided, however, that PSF's License Agreement and PSF's notice of copyright
are retained in Python alone or in any derivative version prepared by Licensee.

3. PSF is making Python available to Licensee on an "AS IS" basis. PSF MAKES NO
REPRESENTATIONS OR WARRANTIES, EXPRESS OR IMPLIED. BY WAY OF EXAMPLE, BUT NOT
LIMITATION, PSF MAKES NO AND DISCLAIMS ANY REPRESENTATION OR WARRANTY OF
MERCHANTABILITY OR FITNESS FOR ANY PARTICULAR PURPOSE OR THAT THE USE OF PYTHON
WILL NOT INFRINGE ANY THIRD PARTY RIGHTS.

Full text: https://docs.python.org/3/license.html"""


@dataclass(frozen=True, slots=True)
class NativeDllSpec:
    """Attribution and license contract for a bundled third-party native DLL."""

    pattern: re.Pattern[str]
    title: str
    license_name: str
    url: str
    copyright_notice: str
    license_text: str | None = None
    upstream_reference: str | None = None
    upstream_markers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LibxmpInfo:
    """Empirically detected version and licensing mode of ``libxmp.dll``."""

    version: str | None
    is_lgpl: bool


def _sdl_license(name: str, extra_note: str = "") -> str:
    header = f"{name}\nCopyright (C) 1997-2025 Sam Lantinga <slouken@libsdl.org>"
    body = f"{header}\n\n{_ZLIB_LICENSE_BODY}"
    return f"{body}\n\n{extra_note}" if extra_note else body


_NATIVE_DLL_SPECS: tuple[NativeDllSpec, ...] = (
    NativeDllSpec(
        pattern=re.compile(r"^sdl2\.dll$"),
        title="SDL2",
        license_name="Zlib",
        url="https://www.libsdl.org/",
        copyright_notice="Copyright (C) 1997-2025 Sam Lantinga <slouken@libsdl.org>",
        license_text=_sdl_license("Simple DirectMedia Layer (SDL2)"),
    ),
    NativeDllSpec(
        pattern=re.compile(r"^sdl2_image\.dll$"),
        title="SDL2_image",
        license_name="Zlib",
        url="https://github.com/libsdl-org/SDL_image",
        copyright_notice="Copyright (C) 1997-2025 Sam Lantinga <slouken@libsdl.org>",
        license_text=_sdl_license("SDL2_image"),
    ),
    NativeDllSpec(
        pattern=re.compile(r"^sdl2_mixer\.dll$"),
        title="SDL2_mixer",
        license_name="Zlib",
        url="https://github.com/libsdl-org/SDL_mixer",
        copyright_notice="Copyright (C) 1997-2025 Sam Lantinga <slouken@libsdl.org>",
        license_text=_sdl_license("SDL2_mixer"),
    ),
    NativeDllSpec(
        pattern=re.compile(r"^sdl2_ttf\.dll$"),
        title="SDL2_ttf",
        license_name="Zlib",
        url="https://github.com/libsdl-org/SDL_ttf",
        copyright_notice="Copyright (C) 1997-2025 Sam Lantinga <slouken@libsdl.org>",
        license_text=_sdl_license(
            "SDL2_ttf",
            "Note: SDL2_ttf statically links FreeType and HarfBuzz, whose full licenses "
            "(FreeType Project License and HarfBuzz Old MIT License) are included verbatim "
            "in the pillow package section above.",
        ),
    ),
    NativeDllSpec(
        pattern=re.compile(r"^freetype(-\d+)?\.dll$"),
        title="FreeType",
        license_name="FTL / GPL-2.0-or-later",
        url="https://freetype.org/",
        copyright_notice=(
            "Copyright (C) 1996-2026 by David Turner, Robert Wilhelm, Werner Lemberg, "
            "and The FreeType Project contributors"
        ),
        upstream_reference=(
            "Included verbatim in the pillow package section above "
            "(The FreeType Project LICENSE / GPLv2)."
        ),
        upstream_markers=("the freetype project license",),
    ),
    NativeDllSpec(
        pattern=re.compile(r"^libjpeg(-\d+)?\.dll$"),
        title="libjpeg / libjpeg-turbo",
        license_name="IJG / BSD-3-Clause",
        url="https://libjpeg-turbo.org/",
        copyright_notice=(
            "Copyright (C) 1991-2026 Thomas G. Lane, Guido Vollbeding, "
            "D. R. Commander, and contributors"
        ),
        license_text=_LIBJPEG_TURBO_LICENSE,
    ),
    NativeDllSpec(
        pattern=re.compile(r"^libpng\d*(-\d+)?\.dll$"),
        title="libpng",
        license_name="libpng License",
        url="http://www.libpng.org/pub/png/libpng.html",
        copyright_notice="Copyright (c) 1995-2026 The PNG Reference Library Authors",
        upstream_reference="Included verbatim in the pillow package section above (libpng).",
        upstream_markers=("png reference library",),
    ),
    NativeDllSpec(
        pattern=re.compile(r"^libtiff(-\d+)?\.dll$"),
        title="libtiff",
        license_name="libtiff License",
        url="https://libtiff.gitlab.io/libtiff/",
        copyright_notice=(
            "Copyright (c) 1988-1997 Sam Leffler; Copyright (c) 1991-1997 Silicon Graphics, Inc."
        ),
        upstream_reference="Included verbatim in the pillow package section above (libtiff).",
        upstream_markers=("sam leffler",),
    ),
    NativeDllSpec(
        pattern=re.compile(r"^libwebp(demux|mux)?(-\d+)?\.dll$"),
        title="libwebp",
        license_name="BSD-3-Clause",
        url="https://chromium.googlesource.com/webm/libwebp",
        copyright_notice="Copyright (c) 2010, Google Inc. All rights reserved.",
        upstream_reference="Included verbatim in the pillow package section above (libwebp).",
        upstream_markers=("copyright (c) 2010, google inc.",),
    ),
    NativeDllSpec(
        pattern=re.compile(r"^libogg(-\d+)?\.dll$"),
        title="libogg",
        license_name="BSD-3-Clause",
        url="https://xiph.org/ogg/",
        copyright_notice="Copyright (c) 2002, Xiph.org Foundation",
        license_text=_LIBOGG_LICENSE,
    ),
    NativeDllSpec(
        pattern=re.compile(r"^libopus(-\d+)?\.dll$"),
        title="libopus",
        license_name="BSD-3-Clause",
        url="https://opus-codec.org/",
        copyright_notice=(
            "Copyright 2001-2011 Xiph.Org, Skype Limited, Octasic, Jean-Marc Valin, "
            "Timothy B. Terriberry, CSIRO, Gregory Maxwell, Mark Borgerding, Erik de Castro Lopo"
        ),
        license_text=_LIBOPUS_LICENSE,
    ),
    NativeDllSpec(
        pattern=re.compile(r"^libopusfile(-\d+)?\.dll$"),
        title="opusfile",
        license_name="BSD-3-Clause",
        url="https://opus-codec.org/",
        copyright_notice="Copyright (c) 1994-2013 Xiph.Org Foundation and contributors.",
        license_text=_OPUSFILE_LICENSE,
    ),
    NativeDllSpec(
        pattern=re.compile(r"^libwavpack(-\d+)?\.dll$"),
        title="WavPack",
        license_name="BSD-3-Clause",
        url="https://www.wavpack.com/",
        copyright_notice="Copyright (c) 1998 - 2024 David Bryant. All rights reserved.",
        license_text=_WAVPACK_LICENSE,
    ),
    NativeDllSpec(
        pattern=re.compile(r"^libxmp(-\d+)?\.dll$"),
        title="libxmp",
        license_name="MIT",
        url="https://xmp.sourceforge.net/",
        copyright_notice="Copyright (C) 1996-2025 Claudio Matsuoka and Hipolito Carraro Jr",
        license_text=_LIBXMP_MIT_LICENSE,
    ),
    NativeDllSpec(
        pattern=re.compile(r"^portmidi\.dll$"),
        title="PortMidi",
        license_name="MIT",
        url="https://github.com/PortMidi/portmidi",
        copyright_notice=(
            "Copyright (c) 1999-2000 Ross Bencina and Phil Burk; "
            "Copyright (c) 2001-2009 Roger B. Dannenberg; "
            "Copyright (c) 2021 Mixxx DJ Software Team"
        ),
        license_text=_PORTMIDI_LICENSE,
    ),
    NativeDllSpec(
        pattern=re.compile(r"^libscipy_openblas(32|64)?_.*\.dll$"),
        title="OpenBLAS (scipy-openblas)",
        license_name="BSD-3-Clause",
        url="https://www.openblas.net/",
        copyright_notice="Copyright (c) 2011-2014, The OpenBLAS Project",
        upstream_reference=(
            "Included verbatim in the numpy package section above "
            "(OpenBLAS, LAPACK, and GCC Runtime Library Exception)."
        ),
        upstream_markers=("the openblas project",),
    ),
    NativeDllSpec(
        pattern=re.compile(r"^(libcrypto|libssl)-\d+(-x64|-x86)?\.dll$"),
        title="OpenSSL",
        license_name="Apache-2.0",
        url="https://www.openssl.org/",
        copyright_notice="Copyright 1998-2024 The OpenSSL Project Authors. All rights reserved.",
        upstream_reference=(
            "Included verbatim in the Python runtime section above (Apache License 2.0)."
        ),
        upstream_markers=("apache license", "version 2.0, january 2004"),
    ),
    NativeDllSpec(
        pattern=re.compile(r"^libffi-\d+\.dll$"),
        title="libffi",
        license_name="MIT",
        url="https://sourceware.org/libffi/",
        copyright_notice="Copyright (c) 1996-2022 Anthony Green, Red Hat, Inc and others.",
        license_text=_LIBFFI_LICENSE,
    ),
)


def _normalize_dist_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _parse_requirement_name(requirement: str) -> str | None:
    if "extra ==" in requirement or "extra==" in requirement:
        return None
    head = requirement.split(";", 1)[0].split("[", 1)[0].strip()
    match = _DIST_NAME_RE.match(head)
    return match.group(0) if match else None


def is_runtime_system_dll(filename: str) -> bool:
    """Return True only for CPython interpreter, MSVC runtime, or Windows UCRT/API-set DLLs."""
    lower = filename.lower()
    return any(pattern.match(lower) for pattern in _RUNTIME_SYSTEM_DLL_PATTERNS)


def match_native_dll_spec(
    filename: str,
    *,
    libxmp_info: LibxmpInfo | None = None,
) -> NativeDllSpec | None:
    """Return the license specification matching ``filename``, or None if unknown."""
    lower = filename.lower()
    for spec in _NATIVE_DLL_SPECS:
        if spec.pattern.match(lower):
            if spec.title == "libxmp" and libxmp_info is not None and libxmp_info.is_lgpl:
                ver_label = f" {libxmp_info.version}" if libxmp_info.version else ""
                return NativeDllSpec(
                    pattern=spec.pattern,
                    title=f"libxmp{ver_label}",
                    license_name="LGPL-2.1-or-later",
                    url=spec.url,
                    copyright_notice=spec.copyright_notice,
                    upstream_reference=(
                        "Included verbatim in the pygame-ce package section above "
                        "(GNU Lesser General Public License v2.1)."
                    ),
                    upstream_markers=("gnu lesser general public license",),
                )
            return spec
    return None


def detect_libxmp_info() -> LibxmpInfo:
    """Empirically inspect the installed pygame-ce ``libxmp.dll`` version and license."""
    if sys.platform != "win32":
        return LibxmpInfo(version=None, is_lgpl=False)
    try:
        dist = importlib_metadata.distribution("pygame-ce")
    except importlib_metadata.PackageNotFoundError:
        return LibxmpInfo(version=None, is_lgpl=False)
    dll_path = Path(str(dist.locate_file("pygame/libxmp.dll")))
    if not dll_path.is_file() or dll_path.stat().st_size == 0:
        return LibxmpInfo(version=None, is_lgpl=False)
    try:
        lib = ctypes.CDLL(str(dll_path))
        vercode = int(ctypes.c_uint.in_dll(lib, "xmp_vercode").value)
        ver_raw = ctypes.c_char_p.in_dll(lib, "xmp_version").value
    except (OSError, ValueError):
        return LibxmpInfo(version=None, is_lgpl=False)
    version_tuple = ((vercode >> 16) & 0xFF, (vercode >> 8) & 0xFF, vercode & 0xFF)
    version_str = (
        ver_raw.decode("ascii", errors="replace")
        if ver_raw
        else ".".join(str(part) for part in version_tuple)
    )
    # Upstream libxmp relicensed the full library from LGPL-2.1-or-later to MIT in 4.6.1.
    return LibxmpInfo(version=version_str, is_lgpl=version_tuple < (4, 6, 1))


def detect_freetype_year() -> int:
    """Determine the copyright year matching the newest bundled FreeType build."""
    detected_years: list[int] = []
    try:
        import PIL.features as pil_features

        pil_ft = pil_features.version("freetype2")
        if pil_ft:
            parts = tuple(int(part) for part in pil_ft.split(".")[:2])
            if parts in _FREETYPE_RELEASE_YEARS:
                detected_years.append(_FREETYPE_RELEASE_YEARS[parts])
    except (ImportError, ValueError, OSError):
        pass
    try:
        import pygame.freetype as pg_ft

        pg_ft.init()
        try:
            ver = pg_ft.get_version()
            key = (int(ver[0]), int(ver[1]))
            if key in _FREETYPE_RELEASE_YEARS:
                detected_years.append(_FREETYPE_RELEASE_YEARS[key])
        finally:
            pg_ft.quit()
    except (ImportError, ValueError, OSError):
        pass
    return max(detected_years) if detected_years else 2026


def _contrib_rthook_stems() -> set[str]:
    try:
        dist = importlib_metadata.distribution("pyinstaller-hooks-contrib")
    except importlib_metadata.PackageNotFoundError:
        return set()
    stems: set[str] = set()
    for pkg_file in dist.files or ():
        rel = str(pkg_file).replace("\\", "/")
        if "/rthooks/" in rel and rel.endswith(".py"):
            stems.add(Path(rel).stem)
    return stems


def detect_embedded_contrib_rthooks(bundle: Path | None) -> tuple[str, ...]:
    """Return sorted ``pyinstaller-hooks-contrib`` runtime hooks embedded in the frozen exe."""
    if bundle is None:
        return ()
    exe = bundle / "HardDaysEngine.exe"
    if not exe.is_file() or exe.stat().st_size == 0:
        return ()
    contrib_stems = _contrib_rthook_stems()
    if not contrib_stems:
        return ()
    try:
        from PyInstaller.archive.readers import CArchiveReader
    except ImportError:
        return ()
    try:
        archive = CArchiveReader(str(exe))
    except Exception:
        return ()
    return tuple(sorted(name for name in archive.toc if name in contrib_stems))


def _bundle_top_entries(bundle: Path | None) -> set[str]:
    if bundle is None:
        return set()
    internal = bundle / "_internal"
    if not internal.is_dir():
        return set()
    return {_normalize_dist_name(p.name) for p in internal.iterdir()}


def collect_runtime_distributions(
    *,
    include_pyinstaller: bool = True,
    bundle: Path | None = None,
) -> list[importlib_metadata.Distribution]:
    """Collect transitive runtime distributions of ``harddaysengine`` without build-only deps."""
    seen: set[str] = set()
    queue: list[str] = ["harddaysengine"]
    found: dict[str, importlib_metadata.Distribution] = {}
    bundled_names = _bundle_top_entries(bundle)
    while queue:
        raw_name = queue.pop(0)
        norm = _normalize_dist_name(raw_name)
        if norm in seen:
            continue
        seen.add(norm)
        if norm in BUILD_ONLY_DISTRIBUTIONS and norm not in bundled_names:
            continue
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
    if include_pyinstaller:
        _append_leaf_distribution(found, "pyinstaller")
        if detect_embedded_contrib_rthooks(bundle):
            _append_leaf_distribution(found, "pyinstaller-hooks-contrib")
    return [found[key] for key in sorted(found)]


def _append_leaf_distribution(
    found: dict[str, importlib_metadata.Distribution],
    name: str,
) -> None:
    norm = _normalize_dist_name(name)
    if norm in found:
        return
    try:
        found[norm] = importlib_metadata.distribution(name)
    except importlib_metadata.PackageNotFoundError:
        return


def _is_license_file(rel_path: str) -> bool:
    path = Path(rel_path)
    if path.suffix.lower() in _NON_LICENSE_SUFFIXES:
        return False
    lower_parts = [part.lower() for part in path.parts]
    in_dist_info = any(part.endswith(".dist-info") for part in lower_parts)
    if in_dist_info and ("licenses" in lower_parts or "license_files" in lower_parts):
        return True
    name_lower = path.name.lower()
    return name_lower.startswith(_LICENSE_NAME_PREFIXES)


def _normalize_license_content(text: str) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    return "\n".join(line.rstrip() for line in lines).strip()


def _license_sort_key(pkg_file: object) -> tuple[int, str]:
    rel = str(pkg_file).replace("\\", "/")
    in_dist_info = 0 if ".dist-info/" in rel.lower() else 1
    return (in_dist_info, rel)


def _read_dist_license_texts(dist: importlib_metadata.Distribution) -> list[tuple[str, str]]:
    texts: list[tuple[str, str]] = []
    seen_resolved: set[Path] = set()
    seen_contents: set[str] = set()
    for pkg_file in sorted(dist.files or (), key=_license_sort_key):
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
        raw = located.read_bytes()
        if b"\x00" in raw:
            continue
        content = raw.decode("utf-8", errors="replace").strip()
        normalized = _normalize_license_content(content)
        if not normalized or normalized in seen_contents:
            continue
        seen_contents.add(normalized)
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
        return _PSF_FALLBACK_LICENSE
    raise FileNotFoundError(f"CPython license file not found under {sys.base_prefix}")


def _find_dist_version(
    dists: list[importlib_metadata.Distribution],
    normalized_name: str,
) -> str | None:
    for dist in dists:
        if _normalize_dist_name(dist.name) == normalized_name:
            return dist.version
    return None


def _build_header_section(
    *,
    pygame_version: str | None,
    freetype_year: int,
    libxmp_info: LibxmpInfo,
) -> str:
    pg_ver = pygame_version or "installed"
    pg_tag_url = (
        f"{PYGAME_CE_SOURCE_URL}/releases/tag/{pygame_version}"
        if pygame_version
        else PYGAME_CE_SOURCE_URL
    )
    pg_pypi_url = (
        f"https://pypi.org/project/pygame-ce/{pygame_version}/#files"
        if pygame_version
        else "https://pypi.org/project/pygame-ce/"
    )
    lgpl_extra = ""
    if libxmp_info.is_lgpl:
        xmp_ver = f" ({libxmp_info.version})" if libxmp_info.version else ""
        lgpl_extra = (
            f"\nIn addition, this build bundles libxmp{xmp_ver} under LGPL-2.1-or-later "
            "(source: https://xmp.sourceforge.net/), which resides in _internal\\libxmp.dll "
            "and may likewise be replaced."
        )
    return (
        "HardDaysEngine Third-Party Licenses and Notices\n"
        f"Release version: {__version__}\n\n"
        "HardDaysEngine itself is licensed under the MIT License (see LICENSE.txt and\n"
        "NOTICE.txt). This portable Windows distribution bundles the Python runtime and\n"
        "the third-party libraries listed below under their respective licenses.\n\n"
        "Mandatory Binary-Distribution Acknowledgments\n"
        "---------------------------------------------\n"
        f"- Portions of this software are copyright (c) {freetype_year} "
        "The FreeType Project (https://freetype.org). All rights reserved.\n"
        f"- {_IJG_CREDIT}\n\n"
        "LGPL-2.1 Compliance and Source Availability\n"
        "-------------------------------------------\n"
        f"This distribution includes pygame-ce {pg_ver} under the GNU Lesser General\n"
        "Public License v2.1 (LGPL-2.1; full text included in the pygame-ce section below).\n"
        f"Corresponding source code for pygame-ce {pg_ver} is available at:\n"
        f"  - {pg_tag_url}\n"
        f"  - {pg_pypi_url}\n"
        f"Corresponding source code for HardDaysEngine {__version__} (MIT) is available at:\n"
        f"  - {HARDDAYSENGINE_SOURCE_URL}\n"
        f"  - https://pypi.org/project/harddaysengine/{__version__}/\n"
        "In accordance with LGPL-2.1 Sections 4 and 6, this portable package uses the\n"
        "PyInstaller --onedir layout: pygame-ce extension modules and shared libraries\n"
        "reside as separate files under _internal\\pygame\\ and _internal\\ and are\n"
        "dynamically loaded at runtime. Users may replace those shared libraries with\n"
        "ABI-compatible builds of pygame-ce (or run HardDaysEngine from its Python\n"
        f"source against their own pygame-ce installation).{lgpl_extra}"
    )


def _collect_bundle_dlls(bundle: Path | None) -> list[str]:
    if bundle is None or not bundle.is_dir():
        return []
    internal = bundle / "_internal"
    search_root = internal if internal.is_dir() else bundle
    return sorted({path.name for path in search_root.rglob("*.dll")})


def _group_native_dlls(
    dll_names: list[str],
    *,
    libxmp_info: LibxmpInfo,
) -> list[tuple[NativeDllSpec, list[str]]]:
    grouped: dict[str, tuple[NativeDllSpec, list[str]]] = {}
    unknown: list[str] = []
    for dll_name in dll_names:
        if is_runtime_system_dll(dll_name):
            continue
        spec = match_native_dll_spec(dll_name, libxmp_info=libxmp_info)
        if spec is None:
            unknown.append(dll_name)
            continue
        if spec.title not in grouped:
            grouped[spec.title] = (spec, [])
        grouped[spec.title][1].append(dll_name)
    if unknown:
        raise ValueError(f"Unlicensed/unknown native DLL(s) in bundle: {', '.join(unknown)}")
    return list(grouped.values())


def _format_native_dll_section(
    bundle: Path | None,
    *,
    libxmp_info: LibxmpInfo,
) -> str | None:
    dll_names = _collect_bundle_dlls(bundle)
    if not dll_names:
        return None
    grouped = _group_native_dlls(dll_names, libxmp_info=libxmp_info)
    if not grouped:
        return None
    blocks: list[str] = [
        f"{_SECTION_SEP}\nBundled Native Libraries\n{_SECTION_SEP}\n\n"
        "The following third-party native DLLs are redistributed under _internal\\ "
        "in this binary package:"
    ]
    for spec, matched in grouped:
        entry_lines = [
            f"--- Native Library: {spec.title} ---",
            f"License: {spec.license_name}",
            f"URL: {spec.url}",
            f"DLLs: {', '.join(sorted(matched))}",
            f"Copyright: {spec.copyright_notice}",
        ]
        if spec.license_text is not None:
            entry_lines.append("")
            entry_lines.append(spec.license_text)
        elif spec.upstream_reference is not None:
            entry_lines.append(f"Full license text: {spec.upstream_reference}")
        blocks.append("\n".join(entry_lines))
    return "\n\n".join(blocks)


def build_third_party_licenses(
    *,
    distributions: list[importlib_metadata.Distribution] | None = None,
    bundle: Path | None = None,
    libxmp_info: LibxmpInfo | None = None,
    freetype_year: int | None = None,
) -> str:
    """Assemble ``THIRD_PARTY_LICENSES.txt`` for the frozen Windows distribution."""
    dists = (
        distributions if distributions is not None else collect_runtime_distributions(bundle=bundle)
    )
    resolved_xmp = libxmp_info if libxmp_info is not None else detect_libxmp_info()
    resolved_ft_year = freetype_year if freetype_year is not None else detect_freetype_year()
    sections: list[str] = [
        _build_header_section(
            pygame_version=_find_dist_version(dists, "pygame-ce"),
            freetype_year=resolved_ft_year,
            libxmp_info=resolved_xmp,
        ),
        f"{_SECTION_SEP}\nPython {sys.version.splitlines()[0]}\nLicense: PSF-2.0\n"
        f"URL: https://www.python.org/\n{_SECTION_SEP}\n\n{_read_python_license()}",
    ]
    for dist in dists:
        header = "\n".join(_dist_metadata_lines(dist))
        texts = _read_dist_license_texts(dist)
        if not texts:
            raise ValueError(f"No license text files found for distribution {dist.name}")
        body = "\n\n".join(f"--- {rel} ---\n\n{text}" for rel, text in texts)
        sections.append(f"{_SECTION_SEP}\n{header}\n{_SECTION_SEP}\n\n{body}")
    native_section = _format_native_dll_section(bundle, libxmp_info=resolved_xmp)
    if native_section is not None:
        sections.append(native_section)
    return "\n\n".join(sections) + "\n"


def _has_substantive_license_text(body: str) -> bool:
    stripped_lines = [
        line
        for line in body.splitlines()
        if not (line.startswith("--- ") and line.endswith(" ---"))
    ]
    cleaned = "\n".join(stripped_lines).strip()
    if len(cleaned) < _MIN_LICENSE_TEXT_CHARS:
        return False
    lower = cleaned.lower()
    return any(marker in lower for marker in _SUBSTANTIVE_LICENSE_MARKERS)


def _parse_top_sections(third_party: str) -> list[tuple[str, str]]:
    """Parse ``===...===\\n<header>\\n===...===\\n\\n<body>`` blocks from the notice file."""
    pattern = re.compile(
        rf"^{re.escape(_SECTION_SEP)}\n(.*?)\n{re.escape(_SECTION_SEP)}\n\n", re.M | re.S
    )
    matches = list(pattern.finditer(third_party))
    sections: list[tuple[str, str]] = []
    for idx, match in enumerate(matches):
        header = match.group(1).strip()
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(third_party)
        sections.append((header, third_party[start:end].strip()))
    return sections


def _parse_native_library_blocks(native_body: str) -> dict[str, tuple[str, str]]:
    """Map lower-cased DLL basenames to ``(header_title, block_text)`` from native entries."""
    marker = re.compile(r"^--- Native Library: (.+?) ---$", re.M)
    matches = list(marker.finditer(native_body))
    by_dll: dict[str, tuple[str, str]] = {}
    for idx, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(native_body)
        block = native_body[start:end].strip()
        dll_match = re.search(r"^DLLs:\s*(.+)$", block, re.M)
        if not dll_match:
            continue
        for item in dll_match.group(1).split(","):
            dll_name = item.strip()
            if dll_name:
                by_dll[dll_name.lower()] = (title, block)
    return by_dll


def _verify_mandatory_notices(third_party: str) -> None:
    if not _FTL_CREDIT_RE.search(third_party):
        raise ValueError("THIRD_PARTY_LICENSES.txt missing mandatory FreeType (FTL) credit notice")
    if _IJG_CREDIT not in third_party:
        raise ValueError("THIRD_PARTY_LICENSES.txt missing mandatory IJG credit notice")
    lower = third_party.lower()
    for required_lgpl in (
        "lgpl-2.1",
        PYGAME_CE_SOURCE_URL.lower(),
        HARDDAYSENGINE_SOURCE_URL.lower(),
        "--onedir",
    ):
        if required_lgpl not in lower:
            raise ValueError(
                f"THIRD_PARTY_LICENSES.txt missing LGPL-2.1 compliance detail: {required_lgpl}"
            )


def _collect_verified_package_names(sections: list[tuple[str, str]]) -> tuple[bool, set[str]]:
    if "no standalone license file was shipped" in "\n".join(b for _, b in sections).lower():
        raise ValueError("THIRD_PARTY_LICENSES.txt contains fallback section without license text")
    found_packages: set[str] = set()
    has_python = False
    for header, body in sections:
        first_line = header.splitlines()[0].strip()
        if first_line == "Bundled Native Libraries":
            continue
        if not _has_substantive_license_text(body):
            raise ValueError(
                f"THIRD_PARTY_LICENSES.txt section has empty or pointer-only text: {first_line}"
            )
        if first_line.startswith("Python "):
            has_python = True
        elif first_line.startswith("Package: "):
            pkg_raw = first_line.removeprefix("Package: ").split()[0]
            found_packages.add(_normalize_dist_name(pkg_raw))
    return has_python, found_packages


def _verify_package_sections(
    sections: list[tuple[str, str]],
    *,
    archive_names: set[str],
) -> None:
    has_python, found_packages = _collect_verified_package_names(sections)
    if not has_python:
        raise ValueError("THIRD_PARTY_LICENSES.txt missing Python runtime section")
    required = list(_REQUIRED_RUNTIME_PACKAGES)
    if "HardDaysEngine.exe" in archive_names:
        required.append("pyinstaller")
    for pkg in required:
        if pkg not in found_packages:
            raise ValueError(f"THIRD_PARTY_LICENSES.txt missing entry for {pkg}")
    bundled_internal = {
        _normalize_dist_name(name.split("/")[1])
        for name in archive_names
        if name.startswith("_internal/") and len(name.split("/")) > 1
    }
    for build_only in sorted(BUILD_ONLY_DISTRIBUTIONS):
        if build_only in found_packages and build_only not in bundled_internal:
            raise ValueError(
                f"THIRD_PARTY_LICENSES.txt includes non-redistributed build package: {build_only}"
            )


def _verify_native_dll_coverage(
    third_party: str,
    sections: list[tuple[str, str]],
    *,
    archive_names: set[str],
) -> None:
    internal_dlls = sorted(
        {
            Path(name).name
            for name in archive_names
            if name.lower().startswith("_internal/") and name.lower().endswith(".dll")
        }
    )
    non_runtime_dlls = [name for name in internal_dlls if not is_runtime_system_dll(name)]
    if not non_runtime_dlls:
        return
    native_bodies = [body for header, body in sections if header == "Bundled Native Libraries"]
    if not native_bodies:
        raise ValueError("THIRD_PARTY_LICENSES.txt missing Bundled Native Libraries section")
    documented = _parse_native_library_blocks(native_bodies[0])
    lower_doc = third_party.lower()
    for dll_name in non_runtime_dlls:
        spec = match_native_dll_spec(dll_name)
        if spec is None:
            raise ValueError(f"Unknown/unlicensed DLL in _internal/: {dll_name}")
        entry = documented.get(dll_name.lower())
        if entry is None:
            raise ValueError(
                f"THIRD_PARTY_LICENSES.txt missing Bundled Native Libraries entry for {dll_name}"
            )
        title, block = entry
        if "Copyright:" not in block:
            raise ValueError(f"Native library entry for {title} ({dll_name}) missing Copyright")
        if spec.license_text is not None:
            if not _has_substantive_license_text(block):
                raise ValueError(
                    f"Native library entry for {title} ({dll_name}) missing full license text"
                )
        elif not all(marker in lower_doc for marker in spec.upstream_markers):
            raise ValueError(
                f"Referenced upstream full license text missing for {title} ({dll_name})"
            )


def verify_third_party_licenses(third_party: str, *, archive_names: set[str]) -> None:
    """Validate mandatory notices, package license bodies, and native DLL coverage."""
    _verify_mandatory_notices(third_party)
    sections = _parse_top_sections(third_party)
    _verify_package_sections(sections, archive_names=archive_names)
    _verify_native_dll_coverage(third_party, sections, archive_names=archive_names)


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
            "--exclude-module",
            "setuptools",
            "--exclude-module",
            "pkg_resources",
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
    """Validate archive structure, legal notices, DLL coverage, shaders, and absence of ADFs."""
    with ZipFile(archive) as zipped:
        names = set(zipped.namelist())
        if "disks/" not in names or any(
            name.lower().endswith(".adf")
            or (name.lower().startswith("disks/") and name != "disks/")
            for name in names
        ):
            raise ValueError("The release must contain an empty disks folder and no ADF images")
        for forbidden in _FORBIDDEN_BUNDLE_PREFIXES:
            if any(name.lower().startswith(forbidden) for name in names):
                raise ValueError(f"Unexpected build-only package bundled under {forbidden}")
        for legal in REQUIRED_LEGAL_FILES:
            if legal not in names:
                raise ValueError(f"Missing legal notice file {legal}")
        if zipped.read("LICENSE.txt") != (ROOT / "LICENSE").read_bytes():
            raise ValueError("LICENSE.txt does not match repository LICENSE")
        if zipped.read("NOTICE.txt") != (ROOT / "NOTICE").read_bytes():
            raise ValueError("NOTICE.txt does not match repository NOTICE")
        third_party = zipped.read("THIRD_PARTY_LICENSES.txt").decode("utf-8")
        verify_third_party_licenses(third_party, archive_names=names)
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
