#!/usr/bin/env python3
"""
bin/consent_dialog_cli.py

Terminal-based interactive consent dialog for OysterRecorder first-run.
Each option is presented with a description and a y/N prompt.

Usage:
    python -m bin.consent_dialog_cli
"""

from __future__ import annotations

import sys
from typing import Dict

# ---------------------------------------------------------------------------
# Consent prompts — each entry is (title, description, default_yes)
# ---------------------------------------------------------------------------

_PROMPTS = [
    (
        "Screen Recording",
        (
            "OysterRecorder will capture your screen while you play supported games.\n"
            "  • Only supported games are recorded (no desktop / other apps).\n"
            "  • This is required for the core recording functionality."
        ),
        True,  # default = yes (but user can decline → exit)
    ),
    (
        "Upload to Oyster",
        (
            "Recorded clips can be uploaded to Oyster servers.\n"
            "  • Servers are located in the US/EU.\n"
            "  • Data retention: 90 days unless you delete earlier."
        ),
        True,
    ),
    (
        "OAuth Login (Google / Discord)",
        (
            "You can sign in with Google or Discord.\n"
            "  • Purpose: link clips to your account, enable sharing."
        ),
        True,
    ),
    (
        "Automatic Updates",
        (
            "OysterRecorder can check for and install updates automatically.\n"
            "  • Ensures you always have the latest features and fixes."
        ),
        True,
    ),
    (
        "Anonymous Telemetry (optional)",
        (
            "Send anonymous usage statistics to help improve OysterRecorder.\n"
            "  • No personal data or clip content is included.\n"
            "  • You can change this later in settings."
        ),
        False,  # default = no
    ),
]


def _ask(prompt: str, default_yes: bool) -> bool:
    """
    Ask a yes/no question. Returns True for yes, False for no.

    Parameters
    ----------
    prompt : str
        The question text.
    default_yes : bool
        If True, the default answer (on empty input) is yes.
        If False, the default is no.
    """
    suffix = "[Y/n]" if default_yes else "[y/N]"
    while True:
        try:
            answer = input(f"  {prompt} {suffix} ").strip().lower()
        except EOFError:
            # Treat EOF as "no" for safety
            return False
        if answer == "":
            return default_yes
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("  Please enter 'y' or 'n'.")


def run_dialog() -> Dict[str, bool]:
    """
    Run the interactive consent dialog in the terminal.

    Returns a dict mapping consent keys to bool values:
        {
            "screen_record": bool,
            "upload": bool,
            "oauth": bool,
            "auto_update": bool,
            "telemetry": bool,
        }
    """
    consent_keys = ["screen_record", "upload", "oauth", "auto_update", "telemetry"]

    print("=" * 60)
    print("  OysterRecorder — First-Run Consent")
    print("=" * 60)
    print()
    print("Welcome! Before we start, please review and accept the following.")
    print("Your choices are saved locally and never uploaded.")
    print()

    results: Dict[str, bool] = {}

    for idx, (title, description, default_yes) in enumerate(_PROMPTS):
        print(f"--- {idx + 1}. {title} ---")
        print(description)
        results[consent_keys[idx]] = _ask(f"Do you consent to {title.lower()}?", default_yes)
        print()

    return results


def main() -> int:
    """CLI entry point."""
    choices = run_dialog()
    print("Your choices:")
    for key, value in choices.items():
        status = "✅ Yes" if value else "❌ No"
        print(f"  {key}: {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
