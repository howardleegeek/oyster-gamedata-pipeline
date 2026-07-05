"""Tests for `bin/games/roblox_adapter.py` silent-error-swallow fix.

Regression checks for the bare `except Exception:` blocks in detect()
method that are now bound to ``exc`` with a ``logger.debug(...)`` call:

  1. Static guard: no bare `except Exception:` may remain in this module.
  2. Runtime guard: proc.exe() failure is logged at DEBUG.
  3. Runtime guard: proc.name() failure is logged at DEBUG.
  4. The module imports cleanly and exposes a module-level ``logger``.

Self-review: scope = one file (bin/games/roblox_adapter.py), one logical
change (bind 2 bare except blocks to ``exc`` + log.debug in detect()).
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add repo root to path so `import bin.games.roblox_adapter` resolves
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BIN_DIR = REPO_ROOT / "bin"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(BIN_DIR))

import bin.games.roblox_adapter as roblox_adapter  # noqa: E402

ROBLOX_SRC = (BIN_DIR / "games" / "roblox_adapter.py").read_text(encoding="utf-8")


def test_module_has_logger() -> None:
    """The module must expose a logger named after the module."""
    assert hasattr(roblox_adapter, "logger"), "module-level logger missing"
    assert isinstance(roblox_adapter.logger, logging.Logger)
    assert roblox_adapter.logger.name == "bin.games.roblox_adapter"


def test_no_bare_except_exception() -> None:
    """No bare `except Exception:` may remain in this module."""
    # Find any bare except Exception: (not bound to a variable)
    # This regex matches "except Exception:" followed by a newline or whitespace
    # but NOT "except Exception as <name>:"
    bare_pattern = r"except Exception\s*:\s*(?:\n|#)"
    matches = re.findall(bare_pattern, ROBLOX_SRC)
    assert not matches, (
        f"Found {len(matches)} bare `except Exception:` block(s); "
        "all must be bound to a variable for debug logging."
    )


def test_detect_logs_exe_failure_at_debug(caplog: pytest.LogCaptureFixture) -> None:
    """proc.exe() failure is logged at DEBUG level."""
    caplog.set_level(logging.DEBUG)
    
    # Create a mock that looks like a real Roblox process
    mock_proc = MagicMock()
    mock_proc.pid = 12345
    # Make proc.exe() raise - this is what we want to test
    mock_proc.exe.side_effect = Exception("Access denied")
    # proc.name() should work
    mock_proc.name.return_value = "RobloxPlayerBeta"
    mock_proc.info = {"name": "RobloxPlayerBeta", "exe": "C:\\Program Files\\Roblox\\RobloxPlayerBeta.exe"}
    
    # Make the mock process match the target so _find_roblox_process returns it
    def mock_process_iter(attrs=None):
        return [mock_proc]
    
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(roblox_adapter.psutil, "process_iter", mock_process_iter)
        mp.setattr(roblox_adapter, "_roblox_exe_name", lambda: "RobloxPlayerBeta")
        
        adapter = roblox_adapter.RobloxAdapter()
        result = adapter.detect()
    
    # proc.exe() should have been called and raised, which we logged
    assert result is None, "Expected None when proc.exe() fails"
    assert mock_proc.exe.called, "proc.exe() was not called"
    # Check that our debug log was emitted
    debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("Failed to get exe path" in msg for msg in debug_msgs), (
        f"DEBUG log for proc.exe() failure not found. Got: {debug_msgs}"
    )


def test_detect_logs_name_failure_at_debug(caplog: pytest.LogCaptureFixture) -> None:
    """proc.name() failure is logged at DEBUG level."""
    caplog.set_level(logging.DEBUG)
    
    # Create a mock that looks like a real Roblox process
    mock_proc = MagicMock()
    mock_proc.pid = 12345
    # proc.exe() succeeds
    mock_proc.exe.return_value = "C:\\Program Files\\Roblox\\RobloxPlayerBeta.exe"
    # Make proc.name() raise - this is what we want to test
    mock_proc.name.side_effect = Exception("Access denied")
    mock_proc.info = {"name": "RobloxPlayerBeta", "exe": "C:\\Program Files\\Roblox\\RobloxPlayerBeta.exe"}
    
    def mock_process_iter(attrs=None):
        return [mock_proc]
    
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(roblox_adapter.psutil, "process_iter", mock_process_iter)
        mp.setattr(roblox_adapter, "_roblox_exe_name", lambda: "RobloxPlayerBeta")
        
        adapter = roblox_adapter.RobloxAdapter()
        result = adapter.detect()
    
    # proc.name() should have been called and raised, which we logged
    assert result is not None, "Expected GameSession when only proc.name() fails"
    assert mock_proc.name.called, "proc.name() was not called"
    # Check that our debug log was emitted for the name failure
    debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("Failed to get window title" in msg for msg in debug_msgs), (
        f"DEBUG log for proc.name() failure not found. Got: {debug_msgs}"
    )


def test_module_compiles() -> None:
    """The module must compile without syntax errors."""
    import py_compile
    module_path = BIN_DIR / "games" / "roblox_adapter.py"
    py_compile.compile(str(module_path), doraise=True)
