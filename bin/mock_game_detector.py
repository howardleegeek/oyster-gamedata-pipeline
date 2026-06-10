#!/usr/bin/env python3
"""mock_game_detector.py — Fake game-detection for local smoke / CI.

Returns a deterministic detection dict without scanning real processes or
windows.  Used by ``bin/recorder_local_smoke.py`` so the full pipeline
can be exercised on a headless dev box.

Usage
-----
    python3 bin/mock_game_detector.py
    # → {"game": "minecraft", "pid": 12345, "window_title": "MC 1.21.4"}

Exit codes
----------
    0 — detection dict printed to stdout
    1 — unexpected error
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, Optional


def detect_game(*, override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return a fake game-detection result.

    Args:
        override: If provided, merge into the default result (keys in
            *override* take precedence).  Useful for tests that want to
            inject a different game name or pid.

    Returns:
        Dict with at least ``game``, ``pid``, ``window_title`` keys.
    """
    result: Dict[str, Any] = {
        "game": "minecraft",
        "pid": 12345,
        "window_title": "MC 1.21.4",
    }
    if override:
        result.update(override)
    return result


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point — prints detection JSON to stdout."""
    try:
        result = detect_game()
        sys.stdout.write(json.dumps(result) + "\n")
        return 0
    except Exception as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
