#!/usr/bin/env python3
"""exe_packager.py — PyInstaller wrapper for one-click vendor binary packaging.

Usage:
    python bin/exe_packager.py --entry script.py --name myapp --out ./dist

Wraps PyInstaller to produce a standalone executable.  Build artefacts are
staged in a temporary directory (``tempfile.mkdtemp``) and only the final
binary is copied to the output path.

No external runtime deps beyond stdlib.  PyInstaller must be installed.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence

log = logging.getLogger(__name__)


def _build_cmd(
    entry: Path,
    name: str,
    icon: Path | None,
    onefile: bool,
    hidden_imports: list[str],
    extra_args: list[str],
    dist_tmp: Path,
    work_tmp: Path,
) -> list[str]:
    """Return the PyInstaller command as a list (no shell=True)."""
    cmd: list[str] = [
        sys.executable, "-m", "PyInstaller",
        "--name", name,
        "--distpath", str(dist_tmp),
        "--workpath", str(work_tmp),
        "--noconfirm",
    ]
    cmd.append("--onefile" if onefile else "--onedir")
    if icon:
        cmd += ["--icon", str(icon)]
    for imp in hidden_imports:
        cmd += ["--hidden-import", imp]
    cmd += extra_args
    cmd.append(str(entry))
    return cmd


def _find_built_artefact(dist_tmp: Path, name: str, onefile: bool) -> Path | None:
    """Locate the built binary inside the temporary dist directory."""
    if onefile:
        # On Windows the extension is .exe; on macOS/Linux there is none.
        for ext in ("", ".exe"):
            candidate = dist_tmp / f"{name}{ext}"
            if candidate.is_file():
                return candidate
    else:
        candidate = dist_tmp / name
        if candidate.is_dir():
            return candidate
    return None


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI args, invoke PyInstaller, return exit code."""
    parser = argparse.ArgumentParser(
        description="Package a Python script into a standalone executable via PyInstaller.",
    )
    parser.add_argument("--entry", required=True, type=Path,
                        help="Path to the Python entry-point script.")
    parser.add_argument("--name", default=None,
                        help="Output binary name (defaults to entry script stem).")
    parser.add_argument("--out", type=Path, default=Path("dist"),
                        help="Output directory for the final binary (default: ./dist).")
    parser.add_argument("--icon", type=Path, default=None,
                        help="Optional icon file (.ico / .icns / .png).")
    parser.add_argument("--onefile", action="store_true", default=True,
                        help="Bundle into a single executable (default).")
    parser.add_argument("--onedir", action="store_true",
                        help="Bundle into a directory instead of a single file.")
    parser.add_argument("--hidden-import", action="append", default=[],
                        dest="hidden_imports",
                        help="Additional hidden-import modules (repeatable).")
    parser.add_argument("--pyinstaller-arg", action="append", default=[],
                        dest="extra_args",
                        help="Extra args forwarded to PyInstaller (repeatable).")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Enable verbose logging.")

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
    )

    # --- validate inputs ---------------------------------------------------
    entry = args.entry.resolve()
    if not entry.is_file():
        log.error("Entry script not found: %s", entry)
        return 1

    name = args.name or entry.stem
    onefile = not args.onedir  # --onedir overrides default onefile

    # --- prepare temp staging dirs -----------------------------------------
    dist_tmp = Path(tempfile.mkdtemp(prefix="pyi_dist_"))
    work_tmp = Path(tempfile.mkdtemp(prefix="pyi_work_"))
    log.info("Staging dirs  dist=%s  work=%s", dist_tmp, work_tmp)

    try:
        cmd = _build_cmd(
            entry=entry,
            name=name,
            icon=args.icon,
            onefile=onefile,
            hidden_imports=args.hidden_imports,
            extra_args=args.extra_args,
            dist_tmp=dist_tmp,
            work_tmp=work_tmp,
        )
        log.info("Running: %s", " ".join(cmd))

        result = subprocess.run(cmd, check=False)
        if result.returncode != 0:
            log.error("PyInstaller exited with code %d", result.returncode)
            return result.returncode

        artefact = _find_built_artefact(dist_tmp, name, onefile)
        if artefact is None:
            log.error("Build succeeded but artefact not found in %s", dist_tmp)
            return 2

        # --- copy to final output ------------------------------------------
        out_dir = args.out.resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / artefact.name
        if artefact.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(artefact, dest)
        else:
            shutil.copy2(artefact, dest)
            os.chmod(dest, dest.stat().st_mode | 0o755)

        log.info("Packaged → %s", dest)
        return 0

    finally:
        # --- cleanup temp dirs ---------------------------------------------
        for tmp_dir in (dist_tmp, work_tmp):
            if tmp_dir.is_dir():
                shutil.rmtree(tmp_dir, ignore_errors=True)
                log.debug("Cleaned up %s", tmp_dir)


if __name__ == "__main__":
    sys.exit(main())
