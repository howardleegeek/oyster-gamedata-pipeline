"""Tests for `bin/cross_game_test_harness.py` silent-error-swallow fix.

Regression checks for the bare `except ImportError: pass` in discover_envs()
that is now bound to ``exc`` with a ``logger.debug(...)`` call:

  1. Static guard: no `except (...):\n    pass` may remain in discover_envs.
  2. Runtime guard: an ImportError during library discovery is logged at DEBUG
     (instead of being silently swallowed).

Self-review: scope = one file (bin/cross_game_test_harness.py), one logical
change (bind previously-bare except to ``exc`` + log.debug in discover_envs).
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
from types import SimpleNamespace
from typing import List

import pytest

# Add bin to path
BIN_DIR = Path(__file__).resolve().parent.parent.parent / "bin"
sys.path.insert(0, str(BIN_DIR))

import cross_game_test_harness as cgh  # noqa: E402


CGH_SRC = (BIN_DIR / "cross_game_test_harness.py").read_text(encoding="utf-8")


def _discover_envs_body() -> str:
    match = re.search(
        r"def discover_envs\(.*?(?=^    def |\Z)",
        CGH_SRC,
        re.M | re.S,
    )
    assert match, "discover_envs not found in source"
    return match.group(0)


def test_no_bare_pass_in_discover_envs() -> None:
    """No `except (...):\\n    pass` may remain in discover_envs."""
    body = _discover_envs_body()
    bare_pass = re.search(r"except[^\n]*:\s*\n\s+pass\b", body)
    assert not bare_pass, (
        "Silent-pass still present in discover_envs at offset "
        f"{bare_pass.start() if bare_pass else '?'}: "
        f"{bare_pass.group(0) if bare_pass else ''}"
    )


def test_import_error_logs_at_debug(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    """ImportError during library discovery is logged at DEBUG."""
    harness = cgh.TestHarness(verbose=False)

    # Patch os.path.isdir to return empty (force the library check path)
    # and patch importlib.import_module to raise ImportError
    with patch.object(cgh.os.path, "isdir", return_value=False):
        with patch(
            "cross_game_test_harness.importlib.import_module"
        ) as mock_import:
            mock_import.side_effect = ImportError("no such module")
            with caplog.at_level(logging.DEBUG, logger="cross_game_test_harness"):
                result = harness.discover_envs()

    # Should return empty list (no envs found) AND log the error
    assert result == []
    assert any(
        "not available" in rec.message for rec in caplog.records
    ), (
        "expected DEBUG log for ImportError; got "
        f"{[r.message for r in caplog.records]}"
    )
