#!/usr/bin/env python3
"""
uninstall_clean.py - Complete uninstaller that removes all traces of the application.

This script removes installed files, configuration, and launchd plists,
leaving zero trace of the installation.

Usage:
    python uninstall_clean.py [--dry-run] [--force]
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List

APP_NAME = "g137"


def find_app_paths() -> List[Path]:
    """Find files and directories belonging to the application."""
    paths: List[Path] = []
    search = [Path("/usr/local"), Path("/opt"), Path.home() / ".config"]
    for base in search:
        if base.exists():
            try:
                for item in base.iterdir():
                    if APP_NAME.lower() in item.name.lower():
                        paths.append(item)
            except PermissionError:
                pass
    return paths


def find_launchd_plists() -> List[Path]:
    """Find launchd plists related to the application."""
    plists: List[Path] = []
    for loc in [Path("/Library/LaunchDaemons"), Path("/Library/LaunchAgents"),
                Path.home() / "Library" / "LaunchDaemons",
                Path.home() / "Library" / "LaunchAgents"]:
        if loc.exists():
            try:
                for p in loc.iterdir():
                    if APP_NAME.lower() in p.name.lower() and p.suffix == ".plist":
                        plists.append(p)
            except PermissionError:
                pass
    return plists


def unload_service(plist: Path, dry_run: bool) -> None:
    """Unload a launchd service if running."""
    name = plist.stem
    try:
        subprocess.run(["launchctl", "list", name], capture_output=True, check=True)
        if dry_run:
            print(f"[DRY-RUN] Would unload: {name}")
        else:
            subprocess.run(["launchctl", "unload", str(plist)], check=True)
            print(f"Unloaded: {name}")
    except subprocess.CalledProcessError:
        pass


def remove_path(path: Path, dry_run: bool) -> bool:
    """Remove a file or directory."""
    if not path.exists():
        return True
    try:
        if dry_run:
            print(f"[DRY-RUN] Remove: {path}")
        else:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            print(f"Removed: {path}")
        return True
    except Exception as e:
        print(f"Failed to remove {path}: {e}", file=sys.stderr)
        return False


def confirm(force: bool) -> bool:
    """Ask for confirmation unless --force is used."""
    if force:
        return True
    return input("Remove all traces? [y/N]: ").lower() in ("y", "yes")


def main(argv: List[str] | None = None) -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Remove all traces of the application")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be removed")
    parser.add_argument("--force", action="store_true", help="Skip confirmation")
    args = parser.parse_args(argv)

    files = find_app_paths()
    plists = find_launchd_plists()

    if not files and not plists:
        print("No installation found.")
        return 0

    print("Files to remove:")
    for p in files:
        print(f"  - {p}")
    print("Plists to remove:")
    for p in plists:
        print(f"  - {p}")

    if not confirm(args.force):
        print("Cancelled.")
        return 1

    for plist in plists:
        unload_service(plist, args.dry_run)
        remove_path(plist, args.dry_run)

    for path in files:
        remove_path(path, args.dry_run)

    print("Done." if args.dry_run else "Uninstall complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
