#!/usr/bin/env python3
"""Auto-install Fabric loader + Oyster Recorder mod into the user's
Minecraft Java Edition install. Closes Pipeline 1's "manual mod install"
gap so testers double-click the .exe and immediately get REAL game state.

Howard 2026-05-07 (D17): standalone helper called by
``recorder_consumer_lite.py`` on first session. Stdlib + urllib only —
PyInstaller bundles this module + the Fabric installer jar + the mod
jar; no runtime download.

Cross-platform MC detection:
    Windows: %APPDATA%\\.minecraft
    macOS:   ~/Library/Application Support/minecraft
    Linux:   ~/.minecraft

Idempotency: every step short-circuits if the desired state is already
in place (Fabric loader profile present, mod jar already at the right
version). Re-running is always safe.

Fail-soft: every error returns ``{"installed": False, "reason": ...}``;
the recorder treats this as "no mod, fall back to placeholder camera
fields" — never crashes the user's MC.

Module entry point: :func:`ensure_installed`.
"""
from __future__ import annotations

import logging
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Pinned versions — must match mc-mod/gradle.properties.
MC_VERSION = "1.21.4"
FABRIC_LOADER_VERSION = "0.16.9"
MOD_JAR_PREFIX = "oyster-recorder-mod-"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InstallResult:
    installed: bool
    mc_dir: Path | None = None
    fabric_profile_dir: Path | None = None
    mod_path: Path | None = None
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "installed": self.installed,
            "mc_dir": str(self.mc_dir) if self.mc_dir else None,
            "fabric_profile_dir": (
                str(self.fabric_profile_dir) if self.fabric_profile_dir else None
            ),
            "mod_path": str(self.mod_path) if self.mod_path else None,
            "reason": self.reason,
        }


def detect_minecraft_dir() -> Path | None:
    """Return the user's vanilla MC Java Edition install dir, or None.

    Honors the ``MINECRAFT_DIR_OVERRIDE`` env var so tests don't need
    real MC installs.
    """
    override = os.environ.get("MINECRAFT_DIR_OVERRIDE")
    if override:
        p = Path(override)
        return p if p.is_dir() else None

    sys_name = platform.system()
    home = Path.home()
    candidates: list[Path] = []
    if sys_name == "Windows":
        appdata = os.environ.get("APPDATA")
        if appdata:
            candidates.append(Path(appdata) / ".minecraft")
    elif sys_name == "Darwin":
        candidates.append(home / "Library" / "Application Support" / "minecraft")
    else:  # Linux + everything else
        candidates.append(home / ".minecraft")

    for c in candidates:
        if c.is_dir():
            return c
    return None


def fabric_profile_present(mc_dir: Path,
                           mc_version: str = MC_VERSION,
                           loader_version: str = FABRIC_LOADER_VERSION) -> Path | None:
    """Check if the Fabric loader profile for the given MC version is
    already installed under ``<mc_dir>/versions/``. Returns the profile
    dir if present, None otherwise.
    """
    versions_dir = mc_dir / "versions"
    if not versions_dir.is_dir():
        return None
    # Profile names look like ``fabric-loader-0.16.9-1.21.4``
    expected = f"fabric-loader-{loader_version}-{mc_version}"
    candidate = versions_dir / expected
    if candidate.is_dir() and (candidate / f"{expected}.json").is_file():
        return candidate
    # Also accept any fabric-loader-*-1.21.4 (newer loader for same MC version)
    for sub in versions_dir.iterdir():
        if not sub.is_dir():
            continue
        m = re.match(rf"fabric-loader-([0-9.]+)-{re.escape(mc_version)}$",
                     sub.name)
        if m and (sub / f"{sub.name}.json").is_file():
            return sub
    return None


def install_fabric_loader(mc_dir: Path, fabric_installer_jar: Path,
                          mc_version: str = MC_VERSION,
                          loader_version: str = FABRIC_LOADER_VERSION,
                          java_bin: str | None = None) -> tuple[bool, str]:
    """Run ``fabric-installer.jar client`` headlessly. Returns
    ``(success, message)``.

    The installer is pre-bundled (PyInstaller --add-data); we pass its
    path explicitly so this module never reaches out to the network.
    ``java_bin`` defaults to ``java`` on PATH; tests can pass a stub.
    """
    if not fabric_installer_jar.is_file():
        return False, f"fabric installer missing: {fabric_installer_jar}"
    java = java_bin or shutil.which("java")
    if not java:
        return False, (
            "java executable not found on PATH; ensure Java 21+ is "
            "installed (Minecraft 1.21+ requires it)"
        )
    # ``client`` subcommand of fabric-installer is non-interactive when
    # all flags are passed.
    cmd = [
        java, "-jar", str(fabric_installer_jar),
        "client",
        "-dir", str(mc_dir),
        "-mcversion", mc_version,
        "-loader", loader_version,
        "-noprofile",  # don't add to launcher_profiles.json — Oyster
                       # recorder leaves the user's launcher profile alone;
                       # they pick our profile themselves the first time.
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return False, "fabric installer timed out (>120 s)"
    except Exception as e:  # noqa: BLE001 — keep installer fail-soft
        logger.debug("fabric installer crashed: %s", e, exc_info=True)
        return False, f"fabric installer crashed: {e}"
    if proc.returncode != 0:
        return False, (
            f"fabric installer exited {proc.returncode}: "
            f"stderr={proc.stderr.strip()[:200]!r}"
        )
    return True, "fabric loader installed"


def drop_mod_jar(mc_dir: Path, source_jar: Path) -> tuple[bool, Path | None, str]:
    """Copy ``source_jar`` into ``<mc_dir>/mods/``. Removes any older
    ``oyster-recorder-mod-*.jar`` first; preserves all OTHER mods.

    Idempotent: if a same-or-newer Oyster jar is already present, no-op.

    Returns ``(success, dest_path_or_None, message)``.
    """
    if not source_jar.is_file():
        return False, None, f"source mod jar missing: {source_jar}"
    mods_dir = mc_dir / "mods"
    mods_dir.mkdir(exist_ok=True)
    dest = mods_dir / source_jar.name

    # Idempotency: same name + same size → already installed
    if dest.is_file() and dest.stat().st_size == source_jar.stat().st_size:
        return True, dest, "mod jar already present (same size)"

    # Remove other oyster-recorder-mod-*.jar files (not other mods!)
    removed: list[str] = []
    for existing in mods_dir.glob(f"{MOD_JAR_PREFIX}*.jar"):
        if existing.resolve() == dest.resolve():
            continue
        try:
            existing.unlink()
            removed.append(existing.name)
        except OSError as e:
            return False, None, (
                f"could not remove old mod jar {existing.name}: {e}"
            )

    try:
        shutil.copy2(source_jar, dest)
    except OSError as e:
        return False, None, f"copy failed: {e}"

    msg = f"mod jar installed at {dest}"
    if removed:
        msg += f" (removed {len(removed)} older: {', '.join(removed)})"
    return True, dest, msg


def ensure_installed(fabric_installer_jar: Path,
                     mod_jar: Path,
                     mc_dir: Path | None = None) -> InstallResult:
    """Top-level entry point. Returns :class:`InstallResult`.

    Steps (all idempotent + fail-soft):
        1. Detect MC install dir (or use provided)
        2. Install Fabric loader if not present
        3. Drop mod jar into mods/

    Caller (recorder) treats ``installed=False`` as "no mod, use
    placeholder fields" — never propagates the failure.
    """
    if mc_dir is None:
        mc_dir = detect_minecraft_dir()
    if mc_dir is None:
        return InstallResult(
            installed=False,
            reason="Minecraft Java Edition install not found; install MC "
                   "first or set MINECRAFT_DIR_OVERRIDE",
        )

    profile = fabric_profile_present(mc_dir)
    if profile is None:
        ok, msg = install_fabric_loader(mc_dir, fabric_installer_jar)
        if not ok:
            return InstallResult(installed=False, mc_dir=mc_dir, reason=msg)
        profile = fabric_profile_present(mc_dir)
        if profile is None:
            return InstallResult(
                installed=False, mc_dir=mc_dir,
                reason="fabric installer reported success but profile dir "
                       "not found post-install",
            )

    ok, dest, msg = drop_mod_jar(mc_dir, mod_jar)
    if not ok:
        return InstallResult(installed=False, mc_dir=mc_dir,
                             fabric_profile_dir=profile, reason=msg)

    return InstallResult(
        installed=True,
        mc_dir=mc_dir,
        fabric_profile_dir=profile,
        mod_path=dest,
        reason=msg,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for manual testing.

    Usage:
        python install_fabric_loader.py <fabric_installer.jar> <mod.jar>
    """
    import argparse  # noqa: PLC0415 — keep deferred for lean import
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("fabric_installer", type=Path,
                        help="Path to fabric-installer.jar")
    parser.add_argument("mod_jar", type=Path,
                        help="Path to oyster-recorder-mod-X.Y.Z.jar")
    args = parser.parse_args(argv)

    result = ensure_installed(args.fabric_installer, args.mod_jar)
    import json  # noqa: PLC0415
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.installed else 1


if __name__ == "__main__":
    sys.exit(main())
