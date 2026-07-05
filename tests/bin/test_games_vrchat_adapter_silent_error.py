"""Tests for `bin/games/vrchat_adapter.py` silent-error-swallow fix.

Regression checks for the bare `except Exception:` blocks in _find_vrchat_process()
that are now bound to ``e`` with a ``logger.debug(...)`` call:

  1. Static guard: no bare `except Exception:` may remain in this module.
  2. Runtime guard: psutil.process_iter failure is logged at DEBUG.
  3. The module imports cleanly and exposes a module-level ``logger``.

Self-review: scope = one file (bin/games/vrchat_adapter.py), one logical
change (bind bare except block to ``e`` + log.debug in _find_vrchat_process()).
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
import pytest

# Add repo root to path so `import bin.games.vrchat_adapter` resolves
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BIN_DIR = REPO_ROOT / "bin"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(BIN_DIR))

import bin.games.vrchat_adapter as vrchat_adapter  # noqa: E402

VRCHAT_SRC = (BIN_DIR / "games" / "vrchat_adapter.py").read_text(encoding="utf-8")


def test_module_has_logger() -> None:
    """The module must expose a logger named after the module."""
    assert hasattr(vrchat_adapter, "logger"), "module-level logger missing"
    assert isinstance(vrchat_adapter.logger, logging.Logger)
    assert vrchat_adapter.logger.name == "bin.games.vrchat_adapter"


def test_no_bare_except_exception() -> None:
    """No bare `except Exception:` may remain in this module."""
    # Find any bare except Exception: (not bound to a variable)
    # This regex matches "except Exception:" followed by a newline or whitespace
    # but NOT "except Exception as <name>:"
    bare_pattern = r"except Exception\s*:\s*(?:\n|#)"
    matches = re.findall(bare_pattern, VRCHAT_SRC)
    assert not matches, (
        f"Found {len(matches)} bare `except Exception:` block(s); "
        "all must be bound to a variable for debug logging."
    )


def test_process_iter_failure_logs_at_debug(caplog: pytest.LogCaptureFixture) -> None:
    """psutil.process_iter failure is logged at DEBUG level."""
    caplog.set_level(logging.DEBUG)

    # Make psutil.process_iter raise an exception to trigger the except block
    def mock_process_iter_raises(*args, **kwargs):
        raise RuntimeError("Process iteration failed")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(vrchat_adapter.psutil, "process_iter", mock_process_iter_raises)
        result = vrchat_adapter._find_vrchat_process()

    # Should return None (control flow preserved)
    assert result is None

    # Should have logged the error at DEBUG level
    assert any(
        "Failed to iterate processes" in record.message
        for record in caplog.records
    ), "Expected debug log for process_iter failure"
    assert any(
        record.levelno == logging.DEBUG
        for record in caplog.records
    ), "Log must be at DEBUG level"


def test_module_compiles() -> None:
    """The module must compile without errors."""
    import py_compile
    src_path = BIN_DIR / "games" / "vrchat_adapter.py"
    py_compile.compile(str(src_path), doraise=True)
