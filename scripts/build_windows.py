"""Build and smoke-test a Windows release ZIP for the running interpreter's architecture."""

import os
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
    (bundle / "Start here.txt").write_text(
        "Copy your three original Moonstone Amiga disk images into the disks folder.\n"
        "Double-click HardDaysEngine.exe to play. Requires OpenGL 3.3.\n",
        encoding="utf-8",
    )
    return bundle


def verify_archive(archive: Path, extracted: Path) -> None:
    with ZipFile(archive) as zipped:
        names = set(zipped.namelist())
        if "disks/" not in names or any(
            name.lower().endswith(".adf")
            or (name.lower().startswith("disks/") and name != "disks/")
            for name in names
        ):
            raise ValueError("The release must contain an empty disks folder and no ADF images")
        for shader in (ROOT / "src" / SHADERS).glob("*"):
            if shader.is_file() and f"_internal/{SHADERS.as_posix()}/{shader.name}" not in names:
                raise ValueError(f"Missing shader {shader.name}")
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
