#!/usr/bin/env python3
"""
version_compatibility_check.py — G251

Detect the running Minecraft game version at launch and verify it against
the supported version matrix.  If the version is unsupported, a system-tray
notification is raised informing the user that recording is paused and
recommending the supported version range.

Supported version range: 1.20.4 (inclusive) through 1.21.x (inclusive).

Usage:
    python3 bin/version_compatibility_check.py --version 1.20.4
    python3 bin/version_compatibility_check.py --version-file version.txt
    python3 bin/version_compatibility_check.py --auto   # reads env var

Exit codes:
    0 — version is supported
    1 — version is unsupported (notification shown)
    2 — CLI / input error
"""

from __future__ import annotations

import argparse
import logging
import os
import platform
import re
import subprocess
import sys

logger = logging.getLogger(__name__)
from typing import Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUPPORTED_MIN: Tuple[int, int, int] = (1, 20, 4)
SUPPORTED_MAX: Tuple[int, int, int] = (1, 21, 99)  # covers all 1.21.x

VERSION_PATTERN: re.Pattern[str] = re.compile(
    r"^(?P<major>\d+)\.(?P<minor>\d+)(?:\.(?P<patch>\d+))?"
)

ENV_VERSION_VAR: str = "G251_GAME_VERSION"

# ---------------------------------------------------------------------------
# Version parsing
# ---------------------------------------------------------------------------


def parse_version(raw: str) -> Optional[Tuple[int, int, int]]:
    """Parse a version string like ``'1.20.4'`` or ``'1.21'`` into a tuple.

    Returns ``None`` when the string does not match the expected pattern.
    """
    match = VERSION_PATTERN.match(raw.strip())
    if match is None:
        return None
    major = int(match.group("major"))
    minor = int(match.group("minor"))
    patch = int(match.group("patch")) if match.group("patch") is not None else 0
    return (major, minor, patch)


def is_version_supported(version: Tuple[int, int, int]) -> bool:
    """Return ``True`` when *version* falls within the supported range."""
    return SUPPORTED_MIN <= version <= SUPPORTED_MAX


# ---------------------------------------------------------------------------
# Version detection helpers
# ---------------------------------------------------------------------------


def detect_version_from_file(path: str) -> Optional[str]:
    """Read the first non-empty line from *path* and return it as a version string."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if stripped:
                    return stripped
    except (OSError, UnicodeDecodeError) as exc:
        print(f"[WARN] Could not read version file {path}: {exc}", file=sys.stderr)
    return None


def detect_version_from_env() -> Optional[str]:
    """Return the version string from the ``G251_GAME_VERSION`` env var, if set."""
    return os.environ.get(ENV_VERSION_VAR)


# ---------------------------------------------------------------------------
# System-tray / desktop notification
# ---------------------------------------------------------------------------

_TITLE: str = "G251 Recorder"
_MESSAGE: str = (
    "This version is unsupported — recording has been paused.\n"
    f"Supported range: {SUPPORTED_MIN[0]}.{SUPPORTED_MIN[1]}.{SUPPORTED_MIN[2]} "
    f"– {SUPPORTED_MAX[0]}.{SUPPORTED_MAX[1]}.x"
)


def _notify_macos(title: str, message: str) -> bool:
    """Show a notification on macOS via ``osascript``."""
    script = (
        f'display notification "{message}" with title "{title}"'
    )
    try:
        subprocess.run(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            timeout=5,
        )
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.debug("osascript notification failed: %s", exc)
        return False


def _notify_linux(title: str, message: str) -> bool:
    """Show a notification on Linux via ``notify-send``."""
    try:
        subprocess.run(
            ["notify-send", title, message],
            check=False,
            capture_output=True,
            timeout=5,
        )
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.debug("notify-send notification failed: %s", exc)
        return False


def _notify_windows(title: str, message: str) -> bool:
    """Show a toast notification on Windows 10+ via PowerShell."""
    ps_script = (
        f'[Windows.UI.Notifications.ToastNotificationManager, '
        f"Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; "
        f'$template = [Windows.UI.Notifications.ToastTemplateType]::ToastText02; '
        f'$xml = [Windows.UI.Notifications.ToastNotificationManager]::'
        f'GetTemplateContent($template).GetXml(); '
        f'$xml.GetElementsByTagName("text")[0].AppendChild('
        f'$xml.CreateTextNode("{title}")); '
        f'$xml.GetElementsByTagName("text")[1].AppendChild('
        f'$xml.CreateTextNode("{message}")); '
        f"$toast = [Windows.UI.Notifications.ToastNotification]::new($xml); "
        f"[Windows.UI.Notifications.ToastNotificationManager]::"
        f'CreateToastNotifier("G251").Show($toast)'
    )
    try:
        subprocess.run(
            ["powershell", "-Command", ps_script],
            check=False,
            capture_output=True,
            timeout=5,
        )
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.debug("PowerShell notification failed: %s", exc)
        return False


def show_notification(title: str = _TITLE, message: str = _MESSAGE) -> None:
    """Dispatch a desktop notification using the best available method."""
    system = platform.system()
    if system == "Darwin":
        _notify_macos(title, message)
    elif system == "Linux":
        _notify_linux(title, message)
    elif system == "Windows":
        _notify_windows(title, message)
    # Fallback: always print to stderr so the message is never lost
    print(f"[{title}] {message}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser for the CLI."""
    parser = argparse.ArgumentParser(
        prog="version_compatibility_check",
        description=(
            "Check whether the detected Minecraft game version is supported "
            "by the G251 recorder.  Shows a system-tray notification when "
            "the version is out of range."
        ),
    )
    parser.add_argument(
        "--version",
        type=str,
        default=None,
        help="Explicit version string to check (e.g. 1.20.4).",
    )
    parser.add_argument(
        "--version-file",
        type=str,
        default=None,
        help="Path to a file whose first non-empty line is the version string.",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        default=False,
        help=f"Auto-detect version from the {ENV_VERSION_VAR} environment variable.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        default=False,
        help="Suppress stdout output (notification still fires on failure).",
    )
    return parser


def resolve_version(args: argparse.Namespace) -> Optional[str]:
    """Return the version string from the highest-priority source."""
    if args.version is not None:
        return args.version
    if args.version_file is not None:
        return detect_version_from_file(args.version_file)
    if args.auto:
        return detect_version_from_env()
    return None


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry-point: parse arguments, check version, notify if unsupported.

    Returns:
        0 when the version is supported,
        1 when unsupported (notification shown),
        2 on input / CLI errors.
    """
    parser = build_parser()
    parsed = parser.parse_args(argv)

    raw_version = resolve_version(parsed)
    if raw_version is None:
        print(
            "Error: no version provided. Use --version, --version-file, or --auto.",
            file=sys.stderr,
        )
        return 2

    version_tuple = parse_version(raw_version)
    if version_tuple is None:
        print(
            f"Error: cannot parse version string '{raw_version}'.",
            file=sys.stderr,
        )
        return 2

    supported = is_version_supported(version_tuple)
    ver_str = f"{version_tuple[0]}.{version_tuple[1]}.{version_tuple[2]}"

    if supported:
        if not parsed.quiet:
            print(f"OK — Minecraft {ver_str} is within the supported range.")
        return 0

    # --- unsupported ---
    if not parsed.quiet:
        print(
            f"UNSUPPORTED — Minecraft {ver_str} is outside the supported range "
            f"({SUPPORTED_MIN[0]}.{SUPPORTED_MIN[1]}.{SUPPORTED_MIN[2]} – "
            f"{SUPPORTED_MAX[0]}.{SUPPORTED_MAX[1]}.x).  Recording paused."
        )

    show_notification()
    return 1


# ---------------------------------------------------------------------------
# Module-level entry
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sys.exit(main())
