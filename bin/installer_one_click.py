#!/usr/bin/env python3
"""G134 · One-click vendor installer.

Detects OS, installs system/Python dependencies, and sets up PATH
for vendor tooling.  Stdlib-only; numpy/Pillow/PyYAML/openpyxl
installed via pip.  pydantic/torch are lazy-imported downstream.

Usage:
    python3 bin/installer_one_click.py [--vendor-dir DIR] [--skip-system]
"""

import argparse
import logging
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


def os_info() -> Tuple[str, str]:
    """Return (os_name, arch) normalised to common tokens."""
    name = platform.system().lower()
    arch = platform.machine().lower()
    if arch in ("x86_64", "amd64"):
        arch = "x64"
    elif arch in ("i386", "i686"):
        arch = "x86"
    elif "arm" in arch:
        arch = "arm64" if "64" in arch else "arm"
    return name, arch


def has_cmd(cmd: str) -> bool:
    """Return True if *cmd* is on PATH."""
    return shutil.which(cmd) is not None


def run_cmd(cmd: List[str]) -> Tuple[int, str]:
    """Execute *cmd* (list form).  Return (rc, stderr)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
        return r.returncode, r.stderr
    except Exception as exc:
        return 1, str(exc)


def detect_pkg_mgr() -> Optional[str]:
    """Return the first available system package manager, or None."""
    name, _ = os_info()
    if name == "linux":
        for mgr in ("apt-get", "yum", "dnf", "pacman", "zypper", "apk"):
            if has_cmd(mgr):
                return mgr
    elif name == "darwin" and has_cmd("brew"):
        return "brew"
    elif name == "windows":
        for mgr in ("choco", "winget"):
            if has_cmd(mgr):
                return mgr
    return None


def install_system_deps(mgr: str) -> Tuple[bool, str]:
    """Install baseline system packages via *mgr*."""
    cmds: dict = {
        "apt-get": [
            ["sudo", "apt-get", "update", "-y"],
            ["sudo", "apt-get", "install", "-y", "python3-pip", "python3-venv", "build-essential"],
        ],
        "yum": [["sudo", "yum", "install", "-y", "python3-pip", "python3-devel", "gcc"]],
        "dnf": [["sudo", "dnf", "install", "-y", "python3-pip", "python3-devel", "gcc"]],
        "pacman": [["sudo", "pacman", "-Sy", "--noconfirm", "python-pip", "python-virtualenv", "base-devel"]],
        "brew": [["brew", "install", "python"]],
        "choco": [["choco", "install", "python3", "-y"]],
        "winget": [["winget", "install", "Python.Python.3", "-y"]],
    }
    for step in cmds.get(mgr, []):
        rc, err = run_cmd(step)
        if rc:
            return False, f"{mgr} step failed: {err}"
    return True, "ok"


_PY_PKGS = ("numpy", "Pillow", "PyYAML", "openpyxl")


def install_python_deps() -> Tuple[bool, str]:
    """pip-install the standard vendor Python packages."""
    for pkg in _PY_PKGS:
        rc, err = run_cmd([sys.executable, "-m", "pip", "install", pkg])
        if rc:
            return False, f"pip install {pkg} failed: {err}"
    return True, "Python packages installed"


def setup_path(vendor_dir: str) -> Tuple[bool, str]:
    """Append *vendor_dir* to PATH in common shell config files."""
    vpath = Path(vendor_dir).resolve()
    if not vpath.is_dir():
        return False, f"Directory not found: {vpath}"
    export_line = f'\nexport PATH="$PATH:{vpath}"\n'
    updated: List[str] = []
    for cfg_name in (".bashrc", ".bash_profile", ".zshrc", ".profile"):
        cfg = Path.home() / cfg_name
        if cfg.exists():
            try:
                text = cfg.read_text()
                if str(vpath) not in text:
                    cfg.write_text(text + export_line)
                    updated.append(cfg_name)
            except OSError as exc:
                logger.debug("Failed to update %s: %s", cfg_name, exc)
    if updated:
        return True, f"Updated: {', '.join(updated)}"
    try:
        (Path.home() / ".bashrc").write_text(f"# G134 installer\n{export_line}")
        return True, "Created ~/.bashrc"
    except OSError as exc:
        return False, str(exc)


def make_vendor_dir() -> Path:
    """Create a persistent vendor directory under $HOME/.g134_vendor."""
    vdir = Path.home() / ".g134_vendor"
    vdir.mkdir(parents=True, exist_ok=True)
    Path(tempfile.mkdtemp(prefix="g134_"))  # staging area
    return vdir


def check_env() -> bool:
    """Quick sanity checks; return True if all pass."""
    py_ok = tuple(map(int, platform.python_version_tuple()[:2])) >= (3, 7)
    checks = [
        ("Python >= 3.7", py_ok),
        ("python3 on PATH", has_cmd("python3")),
        ("pip available", has_cmd("pip") or has_cmd("pip3")),
    ]
    for label, ok in checks:
        print(f"  {'✓' if ok else '✗'} {label}")
    return all(ok for _, ok in checks)


def main(argv: List[str]) -> int:
    """Entry-point for the one-click installer."""
    parser = argparse.ArgumentParser(description="G134 vendor environment installer")
    parser.add_argument("--validate-only", action="store_true", help="Run checks and exit")
    parser.add_argument("--vendor-dir", default=None, help="Custom vendor directory")
    parser.add_argument("--skip-system", action="store_true", help="Skip system packages")
    parser.add_argument("--skip-python", action="store_true", help="Skip Python packages")
    args = parser.parse_args(argv[1:])

    print("=" * 44)
    print("  G134 · Vendor Installer")
    print("=" * 44)

    if not check_env():
        print("\n✗ Environment check failed — aborting.")
        return 1
    if args.validate_only:
        print("\n✓ Validation passed.")
        return 0

    vdir = Path(args.vendor_dir) if args.vendor_dir else make_vendor_dir()
    vdir.mkdir(parents=True, exist_ok=True)
    print(f"\n✓ Vendor directory: {vdir}")

    if not args.skip_system:
        mgr = detect_pkg_mgr()
        if mgr:
            ok, msg = install_system_deps(mgr)
            print(f"  {'✓' if ok else '⚠'} System deps ({mgr}): {msg}")
        else:
            print("  ⚠ No supported package manager found.")
    else:
        print("  ⚠ --skip-system: skipped.")

    if not args.skip_python:
        ok, msg = install_python_deps()
        if not ok:
            print(f"\n✗ {msg}")
            return 1
        print(f"  ✓ {msg}")
    else:
        print("  ⚠ --skip-python: skipped.")

    ok, msg = setup_path(str(vdir))
    print(f"  {'✓' if ok else '⚠'} PATH: {msg}")

    print("\n" + "=" * 44)
    print("  INSTALLATION COMPLETE")
    print("=" * 44)
    print(f"\n  Vendor dir : {vdir}")
    print("  Next step  : source ~/.bashrc  (or restart terminal)")
    print("=" * 44)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except KeyboardInterrupt:
        print("\n✗ Cancelled by user.")
        sys.exit(130)
    except Exception as exc:
        print(f"\n✗ Unexpected error: {exc}")
        sys.exit(1)
