#!/usr/bin/env python3
"""R05C · bin/oyster_launch_mc.py — Fabric Minecraft launcher (sandboxed).

Replaces today's temporary ``bin/launch_mc_fabric.py``. This is the clean
production version with ALL bug-fixes from the 2026-05-08 debug session
baked in.

Bug-fixes baked in (do NOT regress these):

1.  **LEAF-only mainClass + ``arguments.jvm``** — when walking
    ``inheritsFrom`` chain in a Fabric loader JSON, only the LEAF
    (the Fabric loader profile) contributes ``mainClass`` and
    ``arguments.jvm``. Vanilla 1.21.4's parent profile MUST NOT
    overwrite Fabric's ``net.fabricmc.loader.impl.launch.knot.KnotClient``
    main class. We still walk the chain for ``libraries`` and
    ``assetIndex`` because those are additive.

2.  **stderr surfacing** — every ``subprocess.Popen(javaw)`` redirects
    ``stderr`` to a log file AND tails it; on non-zero exit we surface
    the actual Java exception in a Windows MessageBox (or print on
    macOS/Linux). Today's silent ``pythonw`` cost 90 minutes.

3.  **MC ready signal via latest.log** — instead of polling ``wmic`` or
    a process-name match, watch the freshly-truncated ``latest.log``
    inside the sandboxed instance for proof Fabric/Minecraft actually
    started (e.g. "Loading Minecraft 1.21.4 with Fabric Loader" or
    "Setting user: ...").

This module runs cross-platform: imports + functions work on macOS for
testing (``--dry-run`` prints the constructed ``javaw`` command line
without executing). Windows-specific code (registry, MessageBox) is
gated by ``os.name == 'nt'`` checks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger("oyster_launch_mc")

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

MC_VERSION = "1.21.4"
FABRIC_LOADER_VERSION = "0.16.10"
FABRIC_PROFILE_NAME = f"fabric-loader-{FABRIC_LOADER_VERSION}-{MC_VERSION}"

# Fabric's authoritative mainClass — anchored as a sentinel for the
# LEAF-only test below. If the resolved profile does not yield this
# class, something is wrong in the inheritsFrom chain.
EXPECTED_FABRIC_MAIN = "net.fabricmc.loader.impl.launch.knot.KnotClient"

# MC ready-signal markers, tested against ``latest.log``. Any one match
# proves the JVM finished its init and is past Fabric's bootstrap.
MC_READY_MARKERS = (
    "Loading Minecraft",  # Fabric prints this
    "with Fabric Loader",
    "Setting user:",  # vanilla print after auth
    "[Render thread/INFO]",  # standard MC log line, very early
)


# --------------------------------------------------------------------------
# Cross-platform sandbox path
# --------------------------------------------------------------------------


def install_root(override: str | os.PathLike[str] | None = None) -> Path:
    """Return the install root that holds the bundled JRE + mc-instance.

    Defaults to ``%LOCALAPPDATA%\\OysterRecorder\\`` on Windows, and to
    ``~/Library/Application Support/OysterRecorder`` on macOS,
    ``~/.local/share/OysterRecorder`` elsewhere. ``override`` wins (used
    by tests + ``--install-root``).
    """
    if override is not None:
        return Path(override).expanduser().resolve()

    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "OysterRecorder"
        return Path.home() / "AppData" / "Local" / "OysterRecorder"

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "OysterRecorder"

    return Path.home() / ".local" / "share" / "OysterRecorder"


def javaw_path(root: Path) -> Path:
    """Path to the bundled ``javaw.exe`` (Windows) or ``java`` (POSIX).

    On Windows we prefer ``javaw.exe`` so MC has no console window. On
    POSIX (testing) we use ``java`` from the same bundle layout.
    """
    if os.name == "nt":
        return root / "jre" / "bin" / "javaw.exe"
    return root / "jre" / "bin" / "java"


def mc_instance_dir(root: Path) -> Path:
    return root / "mc-instance"


def fabric_profile_json(root: Path, profile_name: str = FABRIC_PROFILE_NAME) -> Path:
    """Path to the LEAF Fabric profile JSON.

    Layout matches the spec:
    ``<INSTALL_ROOT>\\mc-instance\\versions\\fabric-loader-<x>-<mc>\\<name>.json``
    """
    return mc_instance_dir(root) / "versions" / profile_name / f"{profile_name}.json"


def latest_log_path(root: Path) -> Path:
    """MC's latest.log inside the sandboxed instance — NOT %APPDATA%\\.minecraft."""
    return mc_instance_dir(root) / "logs" / "latest.log"


# --------------------------------------------------------------------------
# Registry-based Desktop path detection (Bug-fix #2)
# --------------------------------------------------------------------------


def get_desktop_path() -> Path:
    """Return the user's real Desktop folder.

    On Windows, OneDrive redirects roughly half of consumer installs so
    ``%USERPROFILE%\\Desktop`` is wrong and creates "invisible
    shortcuts" that the user never sees. We resolve via two trusted
    sources in order:

    1.  ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\``
        ``User Shell Folders\\Desktop`` — what Explorer actually uses.
    2.  ``ctypes.windll.shell32.SHGetKnownFolderPath`` with
        ``FOLDERID_Desktop`` — the documented Win32 API fallback.

    On non-Windows, we return ``~/Desktop`` (testing only — this code
    path never runs in production).
    """
    if os.name != "nt":
        return Path.home() / "Desktop"

    # Approach 1: registry
    try:
        import winreg  # type: ignore[import-not-found]

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "Desktop")
        # The value uses %USERPROFILE% / %OneDrive% etc. — expand them.
        expanded = os.path.expandvars(value)
        p = Path(expanded)
        if p.is_dir():
            return p
        logger.warning(
            "registry Desktop path '%s' does not exist, trying SHGetKnownFolderPath",
            expanded,
        )
    except Exception as e:  # noqa: BLE001 — registry may be missing key
        logger.warning("registry Desktop lookup failed: %s", e)

    # Approach 2: SHGetKnownFolderPath (FOLDERID_Desktop)
    try:
        import ctypes
        from ctypes import wintypes

        # FOLDERID_Desktop = {B4BFCC3A-DB2C-424C-B029-7FE99A87C641}
        FOLDERID_Desktop = ctypes.c_byte * 16
        guid = FOLDERID_Desktop(
            0x3A,
            0xCC,
            0xBF,
            0xB4,
            0x2C,
            0xDB,
            0x4C,
            0x42,
            0xB0,
            0x29,
            0x7F,
            0xE9,
            0x9A,
            0x87,
            0xC6,
            0x41,
        )

        SHGetKnownFolderPath = ctypes.windll.shell32.SHGetKnownFolderPath
        SHGetKnownFolderPath.argtypes = [
            ctypes.c_void_p,  # rfid (REFKNOWNFOLDERID)
            wintypes.DWORD,  # dwFlags
            wintypes.HANDLE,  # hToken
            ctypes.POINTER(ctypes.c_wchar_p),  # ppszPath
        ]
        SHGetKnownFolderPath.restype = wintypes.LONG

        path_ptr = ctypes.c_wchar_p()
        hr = SHGetKnownFolderPath(
            ctypes.byref(guid),
            0,
            None,
            ctypes.byref(path_ptr),
        )
        if hr == 0 and path_ptr.value:
            return Path(path_ptr.value)
    except Exception as e:  # noqa: BLE001
        logger.warning("SHGetKnownFolderPath failed: %s", e)

    # Last-resort fallback — known to be wrong under OneDrive
    # redirection but better than crashing.
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"


# --------------------------------------------------------------------------
# inheritsFrom chain resolver (Bug-fix #1)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedProfile:
    """Output of :func:`resolve_profile_chain`. ``main_class`` and
    ``jvm_args`` come ONLY from the leaf; ``libraries`` and
    ``asset_index`` are accumulated from the whole chain.
    """

    main_class: str
    jvm_args: tuple[str, ...]
    game_args: tuple[str, ...]
    libraries: tuple[dict[str, Any], ...]
    asset_index: str
    chain: tuple[str, ...] = field(default_factory=tuple)
    leaf_profile_name: str = ""


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _flatten_args(
    arguments_section: Any,
    key: str,
) -> list[str]:
    """Extract args from MC's modern ``arguments.{jvm|game}`` block.

    The block is a list mixing plain strings and dicts of the form
    ``{"rules": [...], "value": "..."}``. For the launcher use-case we
    keep plain strings + values whose rules are unconditional; we drop
    OS-conditioned entries (those are handled by Mojang's launcher in
    ways we don't replicate). Tests cover both cases.
    """
    out: list[str] = []
    raw = arguments_section.get(key, [])
    for entry in raw:
        if isinstance(entry, str):
            out.append(entry)
        elif isinstance(entry, dict):
            value = entry.get("value")
            rules = entry.get("rules")
            # Unconditional dict-entry → keep
            if not rules and isinstance(value, (str, list)):
                if isinstance(value, str):
                    out.append(value)
                else:
                    out.extend(str(v) for v in value)
            # Conditional → skip (we don't synthesize OS rules client-side)
    return out


def resolve_profile_chain(
    leaf_profile_path: Path,
    *,
    versions_root: Path | None = None,
) -> ResolvedProfile:
    """Walk ``inheritsFrom`` chain and return a :class:`ResolvedProfile`.

    BUG-FIX #1: ``mainClass`` and ``arguments.jvm`` come from the LEAF
    profile ONLY. Everything else (libraries, assetIndex, game args)
    walks the chain. Tested by ``test_resolve_profile_chain_leaf_main``.

    Parameters
    ----------
    leaf_profile_path:
        e.g. ``<root>/mc-instance/versions/fabric-loader-X-Y/fabric-loader-X-Y.json``
    versions_root:
        directory holding all version subdirs; defaults to the leaf's
        parent's parent (``versions/``).
    """
    if not leaf_profile_path.is_file():
        raise FileNotFoundError(f"Fabric profile JSON missing: {leaf_profile_path}")

    if versions_root is None:
        versions_root = leaf_profile_path.parent.parent

    leaf = _load_json(leaf_profile_path)

    # ---- LEAF-only fields (the iron law) ----
    main_class = leaf.get("mainClass", "")
    if not main_class:
        raise ValueError(
            f"leaf profile {leaf_profile_path.name} has no mainClass — refusing "
            f"to inherit from parent (would yield wrong main class)."
        )

    leaf_jvm_args: tuple[str, ...] = ()
    if isinstance(leaf.get("arguments"), dict):
        leaf_jvm_args = tuple(_flatten_args(leaf["arguments"], "jvm"))

    # ---- Chain-accumulated fields ----
    libraries: list[dict[str, Any]] = list(leaf.get("libraries", []))
    asset_index = ""
    chain: list[str] = [leaf_profile_path.stem]
    leaf_game_args: list[str] = []
    if isinstance(leaf.get("arguments"), dict):
        leaf_game_args = _flatten_args(leaf["arguments"], "game")

    parent_profile = leaf
    visited = {leaf_profile_path.stem}
    while parent_profile.get("inheritsFrom"):
        parent_name = parent_profile["inheritsFrom"]
        if parent_name in visited:
            raise ValueError(f"inheritsFrom cycle detected at '{parent_name}' (chain: {chain})")
        parent_path = versions_root / parent_name / f"{parent_name}.json"
        if not parent_path.is_file():
            # Vanilla profile may instead be ``versions/1.21.4/1.21.4.json``
            # which we already covered above. Anything else is an error.
            raise FileNotFoundError(
                f"inheritsFrom '{parent_name}' points to missing profile: " f"{parent_path}"
            )
        parent_profile = _load_json(parent_path)
        visited.add(parent_name)
        chain.append(parent_name)

        # libraries: append parent libs at the END so leaf wins on
        # duplicates (Fabric overrides asm/etc. for its own ABI).
        libraries.extend(parent_profile.get("libraries", []))

        # assetIndex: take the FIRST one we encounter walking up.
        if not asset_index:
            ai = parent_profile.get("assetIndex") or {}
            asset_index = ai.get("id", "") if isinstance(ai, dict) else ""

        # game args: parent contributes (Fabric leaf rarely has any).
        if isinstance(parent_profile.get("arguments"), dict):
            leaf_game_args.extend(_flatten_args(parent_profile["arguments"], "game"))

        # CRITICAL: do NOT overwrite main_class or leaf_jvm_args here.
        # That's the whole point of bug-fix #1.

    # If the leaf itself defined assetIndex, use that.
    leaf_ai = leaf.get("assetIndex") or {}
    if isinstance(leaf_ai, dict) and leaf_ai.get("id"):
        asset_index = leaf_ai["id"]

    if not asset_index:
        # Default to MC 1.21.4 asset index id "19" — matches Mojang manifest.
        asset_index = "19"

    return ResolvedProfile(
        main_class=main_class,
        jvm_args=leaf_jvm_args,
        game_args=tuple(leaf_game_args),
        libraries=tuple(libraries),
        asset_index=asset_index,
        chain=tuple(chain),
        leaf_profile_name=leaf_profile_path.stem,
    )


# --------------------------------------------------------------------------
# Classpath assembly
# --------------------------------------------------------------------------


def _maven_artifact_to_relpath(artifact: str) -> Path:
    """``net.fabricmc:fabric-loader:0.16.10`` → ``net/fabricmc/fabric-loader/0.16.10/fabric-loader-0.16.10.jar``"""
    parts = artifact.split(":")
    if len(parts) < 3:
        raise ValueError(f"bad maven coordinate: {artifact}")
    group, name, version = parts[0], parts[1], parts[2]
    classifier = f"-{parts[3]}" if len(parts) > 3 else ""
    return Path(*group.split("."), name, version, f"{name}-{version}{classifier}.jar")


def assemble_classpath(
    libraries: Iterable[dict[str, Any]],
    libraries_root: Path,
    client_jar: Path,
) -> list[Path]:
    """Build the full ``-cp`` list. Deduplicates by maven coordinate
    (leaf wins thanks to insertion order)."""
    seen: dict[str, Path] = {}
    for lib in libraries:
        name = lib.get("name")
        if not isinstance(name, str):
            continue
        # Skip natives entries; Mojang downloads natives separately.
        if "natives" in lib:
            continue
        key = ":".join(name.split(":")[:2])  # group:artifact (no version)
        rel = _maven_artifact_to_relpath(name)
        if key not in seen:
            seen[key] = libraries_root / rel
    cp = list(seen.values())
    cp.append(client_jar)
    return cp


# --------------------------------------------------------------------------
# Java command construction
# --------------------------------------------------------------------------


def offline_uuid(username: str) -> str:
    """Mojang offline-mode UUID v3 from username."""
    md5 = hashlib.md5(f"OfflinePlayer:{username}".encode("utf-8")).digest()
    b = bytearray(md5)
    b[6] = (b[6] & 0x0F) | 0x30
    b[8] = (b[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(b)))


@dataclass(frozen=True)
class LaunchPlan:
    """All the inputs needed to actually call ``subprocess.Popen``."""

    cmd: tuple[str, ...]
    cwd: Path
    log_file: Path
    instance_dir: Path
    main_class: str  # for assertion in tests
    pause_on_lost_focus_disabled: bool


def ensure_pause_on_lost_focus_disabled(instance_dir: Path) -> bool:
    """Force Minecraft to keep rendering when the recorder/window focus changes."""

    options_path = Path(instance_dir) / "options.txt"
    options_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        lines = options_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        lines = []

    patched: list[str] = []
    saw_key = False
    for line in lines:
        key, sep, _value = line.partition(":")
        if sep and key == "pauseOnLostFocus":
            patched.append("pauseOnLostFocus:false")
            saw_key = True
        else:
            patched.append(line)

    if not saw_key:
        patched.append("pauseOnLostFocus:false")

    options_path.write_text("\n".join(patched) + "\n", encoding="utf-8")
    return True


def build_launch_plan(
    *,
    install_root_path: Path,
    username: str = "Player",
    java_xmx: str = "4G",
    profile_name: str = FABRIC_PROFILE_NAME,
    extra_jvm_args: Iterable[str] | None = None,
) -> LaunchPlan:
    """Assemble the final ``javaw`` command line.

    On exit, the returned ``LaunchPlan.cmd`` is ready to hand to
    ``subprocess.Popen`` (or print, with ``--dry-run``).
    """
    instance = mc_instance_dir(install_root_path)
    leaf_path = fabric_profile_json(install_root_path, profile_name=profile_name)
    resolved = resolve_profile_chain(leaf_path)
    pause_on_lost_focus_disabled = ensure_pause_on_lost_focus_disabled(instance)

    # Sanity check — bug-fix #1 sentinel.
    if resolved.main_class != EXPECTED_FABRIC_MAIN:
        raise RuntimeError(
            f"resolved main_class is '{resolved.main_class}', expected "
            f"'{EXPECTED_FABRIC_MAIN}'. inheritsFrom chain may be "
            f"overwriting LEAF mainClass — see bug-fix #1 in oyster_launch_mc.py"
        )

    libs_root = instance / "libraries"
    # Vanilla client.jar lives at versions/1.21.4/1.21.4.jar (parent profile).
    # We pick whichever profile in the chain has type=release (heuristic:
    # parent of leaf, named by inheritsFrom).
    parent_name = resolved.chain[1] if len(resolved.chain) > 1 else MC_VERSION
    client_jar = instance / "versions" / parent_name / f"{parent_name}.jar"

    classpath_entries = assemble_classpath(
        resolved.libraries,
        libraries_root=libs_root,
        client_jar=client_jar,
    )
    cp_sep = ";" if os.name == "nt" else ":"
    classpath = cp_sep.join(str(p) for p in classpath_entries)

    java = str(javaw_path(install_root_path))

    # Standard heap + GC tuning matching Mojang Launcher defaults +
    # Fabric's recommendations.
    heap_args = [
        f"-Xmx{java_xmx}",
        f"-Xms{java_xmx}",
        "-XX:+UnlockExperimentalVMOptions",
        "-XX:+UseG1GC",
        "-XX:G1NewSizePercent=20",
        "-XX:G1ReservePercent=20",
        "-XX:MaxGCPauseMillis=50",
        "-XX:G1HeapRegionSize=32M",
    ]

    # Fabric leaf's arguments.jvm — typically things like
    # ``-DFabricMcEmu=net.minecraft.client.main.Main``. Bug-fix #1 says
    # this MUST be carried through (parent does NOT overwrite).
    fabric_jvm = list(resolved.jvm_args)

    # Substitute Mojang variable patterns we know about.
    natives_dir = instance / "versions" / parent_name / "natives"
    substitutions = {
        "${library_directory}": str(libs_root),
        "${classpath_separator}": cp_sep,
        "${version_name}": resolved.leaf_profile_name,
        "${natives_directory}": str(natives_dir),
        "${classpath}": classpath,
        "${launcher_name}": "OysterPlay",
        "${launcher_version}": "1.0.0",
    }

    def _sub(s: str) -> str:
        out = s
        for k, v in substitutions.items():
            out = out.replace(k, v)
        return out

    fabric_jvm = [_sub(a) for a in fabric_jvm]

    log_dir = install_root_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"javaw_{int(time.time())}.log"

    cmd: list[str] = [java]
    cmd.extend(heap_args)
    cmd.extend(fabric_jvm)
    if extra_jvm_args:
        cmd.extend(list(extra_jvm_args))
    cmd.extend(["-Djava.library.path=" + str(natives_dir)])
    cmd.extend(["-cp", classpath])
    cmd.append(resolved.main_class)

    # --- Game arguments ---
    player_uuid = offline_uuid(username)
    game_args = [
        "--username",
        username,
        "--version",
        resolved.leaf_profile_name,
        "--gameDir",
        str(instance),
        "--assetsDir",
        str(instance / "assets"),
        "--assetIndex",
        resolved.asset_index,
        "--uuid",
        player_uuid,
        "--accessToken",
        "0",
        "--clientId",
        "",
        "--xuid",
        "",
        "--userType",
        "legacy",
        "--versionType",
        "release",
    ]
    cmd.extend(game_args)

    return LaunchPlan(
        cmd=tuple(cmd),
        cwd=instance,
        log_file=log_file,
        instance_dir=instance,
        main_class=resolved.main_class,
        pause_on_lost_focus_disabled=pause_on_lost_focus_disabled,
    )


# --------------------------------------------------------------------------
# Subprocess + stderr surfacing (Bug-fix #3)
# --------------------------------------------------------------------------


def _show_messagebox(title: str, body: str) -> None:
    """Best-effort error dialog. Falls back to stderr print on POSIX."""
    if os.name == "nt":
        try:
            import ctypes

            MB_ICONERROR = 0x10
            ctypes.windll.user32.MessageBoxW(0, body, title, MB_ICONERROR)
            return
        except Exception as exc:  # noqa: BLE001 - ctypes/windll can fail on WSL/missing DLLs
            logger.debug("Windows MessageBoxW unavailable, falling back to stderr: %s", exc)
    sys.stderr.write(f"\n[{title}] {body}\n")
    sys.stderr.flush()


def _tail_file(path: Path, n_lines: int = 80) -> str:
    """Return the last ``n_lines`` of a text file (UTF-8, errors='replace')."""
    if not path.is_file():
        return f"(log file not found: {path})"
    try:
        # Read whole file — these logs are small (<1MB even on crash).
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"(could not read log: {e})"
    lines = text.splitlines()
    return "\n".join(lines[-n_lines:])


def launch_javaw(
    plan: LaunchPlan,
    *,
    detach: bool = False,
) -> subprocess.Popen:
    """Spawn the JVM with stderr → log file (bug-fix #3).

    ``detach=False`` (default): caller manages the Popen object.
    ``detach=True``: Windows ``CREATE_NEW_PROCESS_GROUP`` so the
    launcher can exit while MC keeps running.
    """
    plan.instance_dir.mkdir(parents=True, exist_ok=True)
    ensure_pause_on_lost_focus_disabled(plan.instance_dir)
    plan.log_file.parent.mkdir(parents=True, exist_ok=True)

    creation_flags = 0
    if detach and os.name == "nt":
        # CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS
        creation_flags = 0x00000200 | 0x00000008

    log_handle = plan.log_file.open("w", encoding="utf-8", errors="replace")
    log_handle.write(f"=== oyster_launch_mc.py ===\ncmd: {plan.cmd}\n\n")
    log_handle.flush()

    proc = subprocess.Popen(
        list(plan.cmd),
        cwd=str(plan.cwd),
        stdout=log_handle,
        stderr=subprocess.STDOUT,  # interleave so order is preserved
        creationflags=creation_flags if os.name == "nt" else 0,
    )
    return proc


def wait_for_mc_ready(
    log_path: Path,
    *,
    timeout_sec: float = 60.0,
    poll_interval_sec: float = 0.5,
) -> bool:
    """Bug-fix #5: prove MC is up via ``latest.log`` instead of wmic.

    Poll the freshly-truncated ``logs/latest.log`` (inside the sandboxed
    instance, NOT %APPDATA%\\.minecraft) for any of
    :data:`MC_READY_MARKERS`. Returns True on first match, False on
    timeout.
    """
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if log_path.is_file():
            try:
                # We re-read — file is small and grows monotonically.
                text = log_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            for marker in MC_READY_MARKERS:
                if marker in text:
                    return True
        time.sleep(poll_interval_sec)
    return False


def surface_failure_messagebox(
    plan: LaunchPlan,
    return_code: int,
) -> None:
    """Read the tail of the JVM log and pop an error dialog."""
    tail = _tail_file(plan.log_file, n_lines=80)
    body = (
        f"Minecraft (Fabric) exited with code {return_code}.\n"
        f"Log: {plan.log_file}\n\n"
        f"--- last 80 lines ---\n{tail}"
    )
    _show_messagebox("Oyster Recorder — Minecraft failed to start", body)


# --------------------------------------------------------------------------
# Sanity / install verification
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class InstallStatus:
    ok: bool
    missing: tuple[str, ...] = ()


def verify_install(
    install_root_path: Path,
    profile_name: str = FABRIC_PROFILE_NAME,
) -> InstallStatus:
    """Cheap pre-flight check used by ``oyster_play.py`` before launch."""
    must_exist: list[Path] = [
        javaw_path(install_root_path),
        fabric_profile_json(install_root_path, profile_name),
        mc_instance_dir(install_root_path) / "versions" / MC_VERSION / f"{MC_VERSION}.jar",
    ]
    missing = [str(p) for p in must_exist if not p.exists()]
    return InstallStatus(ok=not missing, missing=tuple(missing))


# --------------------------------------------------------------------------
# CLI entry-point (used by --dry-run + tests)
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--install-root",
        type=Path,
        default=None,
        help="Override install root (default: %%LOCALAPPDATA%%\\OysterRecorder)",
    )
    parser.add_argument(
        "--username", default="Player", help="Minecraft username (offline mode UUID)"
    )
    parser.add_argument("--xmx", default="4G", help="Java max heap (e.g. 4G)")
    parser.add_argument(
        "--profile-name", default=FABRIC_PROFILE_NAME, help="Fabric loader profile dir name"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the constructed javaw command line and exit (no JVM spawned).",
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Spawn javaw and exit immediately (don't wait or surface stderr).",
    )
    parser.add_argument("--ready-timeout", type=float, default=60.0)
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    root = install_root(args.install_root)

    try:
        plan = build_launch_plan(
            install_root_path=root,
            username=args.username,
            java_xmx=args.xmx,
            profile_name=args.profile_name,
        )
    except FileNotFoundError as e:
        print(f"ERROR: install incomplete — {e}", file=sys.stderr)
        return 2
    except (ValueError, RuntimeError) as e:
        print(f"ERROR: bad Fabric profile — {e}", file=sys.stderr)
        return 3

    if args.dry_run:
        # Pretty-print so a human (or a smoke-test grep) can verify
        # KnotClient + -DFabricMcEmu are in there.
        print("=== oyster_launch_mc.py dry-run ===")
        print(f"install_root  : {root}")
        print(f"main_class    : {plan.main_class}")
        print(f"profile_name  : {args.profile_name}")
        print("pauseOnLostFocus: false")
        print(f"log_file      : {plan.log_file}")
        print(f"cwd           : {plan.cwd}")
        print(f"argc          : {len(plan.cmd)}")
        print("--- javaw cmd line ---")
        for token in plan.cmd:
            print(token)
        return 0

    # Actually spawn — Windows-only path in production.
    proc = launch_javaw(plan, detach=args.no_wait)
    if args.no_wait:
        print(f"javaw spawned (PID={proc.pid}); detaching.")
        return 0

    print(f"javaw spawned (PID={proc.pid}); waiting for MC ready signal…")
    log = latest_log_path(root)
    ready = wait_for_mc_ready(log, timeout_sec=args.ready_timeout)
    if not ready:
        print(
            f"WARN: MC ready marker not seen in {args.ready_timeout}s — "
            f"continuing anyway, log={log}"
        )

    rc = proc.wait()
    if rc != 0:
        surface_failure_messagebox(plan, rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
