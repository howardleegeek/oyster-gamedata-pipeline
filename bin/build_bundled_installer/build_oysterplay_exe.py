#!/usr/bin/env python3
"""R05C · build_oysterplay_exe.py — compile bin/oyster_play.py to OysterPlay.exe.

Spec: ``specs/R05C_consumer_launcher_compiled.md``

PyInstaller wraps ``bin/oyster_play.py`` (and its sibling
``bin/oyster_launch_mc.py``) into a single ``OysterPlay.exe`` that can
be double-clicked by the consumer. The .exe is run from inside the
Inno Setup install layout (``%LOCALAPPDATA%\\OysterRecorder\\``) and
delegates everything to the bundled JRE + Fabric instance staged
beside it.

Usage (CI / local build):
    python bin/build_bundled_installer/build_oysterplay_exe.py
    python bin/build_bundled_installer/build_oysterplay_exe.py --onefile
    python bin/build_bundled_installer/build_oysterplay_exe.py --check-only

Iron-law constraints:
  * No network at build time (PyInstaller already cached locally).
  * Refuses to build if PyInstaller is not installed (exits 4).
  * Bakes ``bin/oyster_launch_mc.py`` AND ``bin/oyster_play.py`` into
    the bundle. If we forget oyster_launch_mc.py the .exe will
    ImportError at startup.
  * On non-Windows we still construct the spec + emit the .spec file so
    the build can be cross-prepared from a CI runner — actual ``.exe``
    output is Windows-only.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Repo + path constants (same convention as fetch_jre.py)
REPO_ROOT = Path(__file__).resolve().parents[2]
BIN_DIR = REPO_ROOT / "bin"
ENTRY_SCRIPT = BIN_DIR / "oyster_play.py"
LAUNCHER_MODULE = BIN_DIR / "oyster_launch_mc.py"
DIST_DIR = REPO_ROOT / "dist" / "OysterPlay"
BUILD_DIR = REPO_ROOT / "build" / "OysterPlay"

# Exit codes
EXIT_OK = 0
EXIT_BAD_SOURCE = 2
EXIT_NO_PYINSTALLER = 4
EXIT_BUILD_FAILED = 5
EXIT_VERIFY_FAILED = 6


def _has_pyinstaller() -> bool:
    try:
        import PyInstaller  # type: ignore[import-not-found]  # noqa: F401
        return True
    except ImportError:
        return False


def _verify_sources() -> tuple[bool, list[str]]:
    """Both .py modules must exist; both must import cleanly under the
    current Python. Returns ``(ok, problems)``."""
    problems: list[str] = []
    for f in (ENTRY_SCRIPT, LAUNCHER_MODULE):
        if not f.is_file():
            problems.append(f"missing source file: {f}")

    if problems:
        return False, problems

    # Smoke-import both modules so we catch syntax errors before invoking
    # PyInstaller (much faster feedback loop than the build).
    repo_bin = str(BIN_DIR)
    env = os.environ.copy()
    env["PYTHONPATH"] = (
        repo_bin if not env.get("PYTHONPATH") else
        f"{repo_bin}{os.pathsep}{env['PYTHONPATH']}"
    )
    for module_name, source in (
        ("oyster_launch_mc", LAUNCHER_MODULE),
        ("oyster_play", ENTRY_SCRIPT),
    ):
        proc = subprocess.run(
            [sys.executable, "-c", f"import {module_name}"],
            env=env, capture_output=True, text=True,
        )
        if proc.returncode != 0:
            problems.append(
                f"{module_name} fails to import: {proc.stderr.strip()[:400]}"
            )
    return not problems, problems


def build(
    *,
    onefile: bool = True,
    name: str = "OysterPlay",
    icon: Path | None = None,
    extra_data: list[tuple[Path, str]] | None = None,
    clean: bool = False,
) -> int:
    """Run PyInstaller. Returns the exit code from PyInstaller (0 = OK)."""
    if not _has_pyinstaller():
        print(
            "ERROR: PyInstaller not installed. "
            "Run: pip install pyinstaller>=6.0",
            file=sys.stderr,
        )
        return EXIT_NO_PYINSTALLER

    ok, problems = _verify_sources()
    if not ok:
        for p in problems:
            print(f"ERROR: {p}", file=sys.stderr)
        return EXIT_BAD_SOURCE

    if clean and DIST_DIR.exists():
        shutil.rmtree(DIST_DIR, ignore_errors=True)
    if clean and BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR, ignore_errors=True)

    DIST_DIR.parent.mkdir(parents=True, exist_ok=True)

    # PyInstaller arg construction
    # Windowed mode (--noconsole) so a double-click doesn't spawn a black
    # console window. We still surface stderr from javaw via the
    # MessageBox path baked into oyster_launch_mc.py.
    args: list[str] = [
        sys.executable, "-m", "PyInstaller",
        "--name", name,
        "--noconsole",   # GUI app — no flashing console
        "--clean" if clean else "--noconfirm",
        "--distpath", str(DIST_DIR.parent),
        "--workpath", str(BUILD_DIR.parent),
        "--specpath", str(BUILD_DIR.parent),
    ]
    if onefile:
        args.append("--onefile")

    # Bake oyster_launch_mc.py in as a hidden import — PyInstaller's
    # static analyzer may miss the dynamic ``import oyster_launch_mc``
    # we do in oyster_play.py.
    args.extend(["--hidden-import", "oyster_launch_mc"])

    # Add bin/ to module search so PyInstaller finds the sibling.
    args.extend(["--paths", str(BIN_DIR)])

    if icon is not None and icon.is_file():
        args.extend(["--icon", str(icon)])

    # Extra data files (mod jars, fabric installer jar, etc.). Each
    # entry is (source_path_on_disk, target_dir_inside_bundle).
    for src, dest in (extra_data or []):
        if not src.exists():
            print(f"WARN: extra_data missing, skipping: {src}", file=sys.stderr)
            continue
        sep = ";" if os.name == "nt" else ":"
        args.extend(["--add-data", f"{src}{sep}{dest}"])

    # Entry script
    args.append(str(ENTRY_SCRIPT))

    print(f"Running: {' '.join(str(a) for a in args)}")
    proc = subprocess.run(args, cwd=str(REPO_ROOT))
    if proc.returncode != 0:
        print(f"ERROR: PyInstaller exited {proc.returncode}", file=sys.stderr)
        return EXIT_BUILD_FAILED

    # Verify output
    suffix = ".exe" if os.name == "nt" else ""
    output = DIST_DIR.parent / f"{name}{suffix}" if onefile else \
        DIST_DIR / f"{name}{suffix}"
    if not output.exists():
        print(f"ERROR: expected build output {output} not found",
              file=sys.stderr)
        return EXIT_VERIFY_FAILED

    print(f"OK: built {output}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--onefile", action="store_true", default=True,
                        help="Single-file .exe (default).")
    parser.add_argument("--onedir", dest="onefile", action="store_false",
                        help="Multi-file dist (faster cold start).")
    parser.add_argument("--name", default="OysterPlay",
                        help="Output binary name (default: OysterPlay).")
    parser.add_argument("--icon", type=Path, default=None,
                        help="Optional .ico file for the .exe.")
    parser.add_argument("--clean", action="store_true",
                        help="Wipe dist/ and build/ before building.")
    parser.add_argument("--check-only", action="store_true",
                        help="Verify sources + PyInstaller install but "
                             "don't actually build.")
    parser.add_argument("--add-data", action="append", default=[],
                        metavar="SRC=DEST",
                        help="Extra files to bundle: SRC=DEST_INSIDE_BUNDLE")

    args = parser.parse_args(argv)

    if args.check_only:
        if not _has_pyinstaller():
            print("CHECK FAIL: PyInstaller not installed", file=sys.stderr)
            return EXIT_NO_PYINSTALLER
        ok, problems = _verify_sources()
        if not ok:
            for p in problems:
                print(f"CHECK FAIL: {p}", file=sys.stderr)
            return EXIT_BAD_SOURCE
        print("CHECK OK: PyInstaller installed + sources import cleanly")
        return EXIT_OK

    extra_data: list[tuple[Path, str]] = []
    for spec in args.add_data:
        if "=" not in spec:
            print(f"ERROR: --add-data must be SRC=DEST, got {spec}",
                  file=sys.stderr)
            return EXIT_BAD_SOURCE
        src_str, dest = spec.split("=", 1)
        extra_data.append((Path(src_str), dest))

    return build(
        onefile=args.onefile,
        name=args.name,
        icon=args.icon,
        extra_data=extra_data,
        clean=args.clean,
    )


if __name__ == "__main__":
    sys.exit(main())
