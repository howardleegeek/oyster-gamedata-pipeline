#!/usr/bin/env python3
"""
Regression test for bin/real_depth_filler.py silent-error surfacing.

Three fail-soft helpers in real_depth_filler.py previously used bare
`except Exception:` that swallowed the cause. We now bind the exception
to a module-level logger and emit a DEBUG log with exc_info=True, while
preserving the original control flow (return "cpu", return None, return
False respectively). This test verifies:

  1. AST: no bare `except Exception:` in the three target functions.
  2. `select_device()` with a torch mock whose `cuda.is_available()` /
     `mps.is_available()` both raise still returns "cpu" (control flow
     preserved) AND emits a DEBUG log on the module logger.
  3. `_get_torch_dtype("cuda")` with a torch mock that raises on
     `import torch` still returns None (control flow preserved) AND
     emits a DEBUG log.
  4. `_verify_exr_channel()` with a broken path still returns False
     (control flow preserved) AND emits a DEBUG log.

Ref: rounds 285-293 follow the same pattern (surface silent error,
preserve fail-soft return value, add focused regression test).
"""

from __future__ import annotations

import ast
import logging
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BIN_DIR = REPO_ROOT / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))


# --- (1) AST: no bare `except Exception:` in the three target functions ---

@pytest.mark.parametrize(
    "func_name",
    ["select_device", "_get_torch_dtype", "_verify_exr_channel"],
)
def test_target_functions_have_no_bare_except_exception(func_name: str) -> None:
    src = (BIN_DIR / "real_depth_filler.py").read_text()
    tree = ast.parse(src)
    fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert func_name in fns, f"function {func_name!r} not found in real_depth_filler.py"
    fn = fns[func_name]
    bare = []
    for node in ast.walk(fn):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            bare.append(node.lineno)
        if (
            isinstance(node, ast.ExceptHandler)
            and node.type is not None
            and isinstance(node.type, ast.Name)
            and node.type.id == "Exception"
            and node.name is None
        ):
            bare.append(node.lineno)
    assert bare == [], f"{func_name}: bare `except Exception:` still present at lines {bare}"


# --- (2) select_device: torch raises → returns 'cpu' + DEBUG log ---


def test_select_device_logs_debug_when_torch_raises(caplog) -> None:
    """A torch whose attribute access raises should still fall back to cpu,
    AND should now emit a DEBUG log line naming the cause."""
    from unittest.mock import MagicMock

    import real_depth_filler

    # Build a torch whose cuda.is_available() raises (e.g. driver probe failure).
    mock_torch = MagicMock()
    mock_torch.cuda.is_available.side_effect = RuntimeError("driver probe failed")
    sys.modules["torch"] = mock_torch

    try:
        with caplog.at_level(logging.DEBUG, logger="real_depth_filler"):
            result = real_depth_filler.select_device()
    finally:
        # Restore so other tests aren't affected.
        sys.modules.pop("torch", None)

    # Control flow preserved: still falls back to "cpu".
    assert result == "cpu"
    # And the cause is now visible in the debug log.
    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert debug_records, "expected at least one DEBUG log on real_depth_filler logger"
    assert any("driver probe failed" in r.getMessage() for r in debug_records), (
        f"DEBUG log did not include the original cause; got: "
        f"{[r.getMessage() for r in debug_records]}"
    )


# --- (3) _get_torch_dtype: torch import raises → returns None + DEBUG log ---


def test_get_torch_dtype_logs_debug_when_torch_missing(caplog, monkeypatch) -> None:
    """If importing torch itself fails, _get_torch_dtype still returns None
    and now logs the cause at DEBUG."""
    import real_depth_filler

    import builtins

    real_import = builtins.__import__

    def _blocked(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: ANN001
        if name == "torch" or name.startswith("torch."):
            raise ImportError("blocked torch for missing-dependency test")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _blocked)

    with caplog.at_level(logging.DEBUG, logger="real_depth_filler"):
        result = real_depth_filler._get_torch_dtype("cuda")

    # Control flow preserved: still returns None on missing torch.
    assert result is None
    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert debug_records, "expected at least one DEBUG log on real_depth_filler logger"
    assert any("blocked torch" in r.getMessage() for r in debug_records), (
        f"DEBUG log did not include the original cause; got: "
        f"{[r.getMessage() for r in debug_records]}"
    )


# --- (4) _verify_exr_channel: garbage path → returns False + DEBUG log ---


def test_verify_exr_channel_logs_debug_on_missing_module(caplog) -> None:
    """If OpenEXR is not importable, _verify_exr_channel still returns False
    and now logs the cause at DEBUG."""
    import real_depth_filler

    import builtins

    real_import = builtins.__import__

    def _blocked(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: ANN001
        if name == "OpenEXR" or name.startswith("OpenEXR."):
            raise ImportError("blocked OpenEXR for missing-dependency test")
        return real_import(name, globals, locals, fromlist, level)

    saved = builtins.__import__
    builtins.__import__ = _blocked
    try:
        with caplog.at_level(logging.DEBUG, logger="real_depth_filler"):
            result = real_depth_filler._verify_exr_channel("/nonexistent/foo.exr")
    finally:
        builtins.__import__ = saved

    # Control flow preserved: still returns False on missing OpenEXR.
    assert result is False
    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert debug_records, "expected at least one DEBUG log on real_depth_filler logger"
    assert any("blocked OpenEXR" in r.getMessage() for r in debug_records), (
        f"DEBUG log did not include the original cause; got: "
        f"{[r.getMessage() for r in debug_records]}"
    )


# --- (5) module-level logger is wired up ---


def test_real_depth_filler_has_module_logger() -> None:
    """The module must expose a `logger` so the except blocks can bind to it."""
    import real_depth_filler

    assert hasattr(real_depth_filler, "logger"), (
        "real_depth_filler.py must define a module-level `logger` "
        "for the except blocks to bind the swallowed exception."
    )
    assert isinstance(real_depth_filler.logger, logging.Logger)
    assert real_depth_filler.logger.name == "real_depth_filler"
