"""
Regression test: bin/vendor_scenario_first_clip.py must not silently
swallow PIL decode / import failures in the duration probe.

The previous `except Exception:` block on line 101 of
bin/vendor_scenario_first_clip.py was a bare `except` that recorded
`duration=skipped` to the checks list without logging. This made it
impossible to tell from logs why duration detection was skipped
(missing PIL, missing codec, corrupt file, etc.). This test pins the
contract that:

  1. The module no longer contains a bare `except Exception:`.
  2. The module imports a `logger` (root or named) and uses it.
  3. The duration-probe path still falls back to the
     `duration=skipped (no decoder)` check on import / decode failure
     (control flow preserved).
  4. The fallback path emits a logger.debug(...) call so operators
     tailing logs can see the real exception via exc_info=True.
"""

from __future__ import annotations

import ast
import importlib.util
import logging
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TARGET = REPO_ROOT / "bin" / "vendor_scenario_first_clip.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "vendor_scenario_first_clip", TARGET
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_no_bare_except_in_module():
    """AST scan: no `except Exception:` (bare, no `as e` binding) anywhere."""
    source = TARGET.read_text(encoding="utf-8")
    tree = ast.parse(source)
    bare = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            # `except Exception:` is bare if the name attribute is None.
            if node.name is None and node.type is not None:
                bare.append((node.lineno, ast.unparse(node.type)))
    assert not bare, f"bare `except` blocks found: {bare}"


def test_module_has_logger():
    """The module exposes a module-level `logger` (stdlib logging)."""
    module = _load_module()
    assert hasattr(module, "logger"), "module must define `logger`"
    assert isinstance(module.logger, logging.Logger)
    assert module.logger.name == "vendor_scenario_first_clip"


def test_duration_probe_logs_at_debug_on_failure(caplog):
    """When PIL is absent or fails, fallback path logs at DEBUG with exc_info."""
    module = _load_module()

    # Force the import inside _validate_clip to raise by hiding PIL.
    # We monkeypatch `__import__` only for the duration of the call.
    real_import = __builtins__["__import__"]

    def fake_import(name, *args, **kwargs):
        if name == "PIL" or name.startswith("PIL."):
            raise ImportError("simulated PIL absence for test")
        return real_import(name, *args, **kwargs)

    # Create a temp clip file
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        clip = Path(td) / "fake.mp4"
        clip.write_bytes(b"\x00" * 128)

        caplog.set_level(logging.DEBUG, logger="vendor_scenario_first_clip")
        try:
            __builtins__["__import__"] = fake_import
            result = module._validate_clip(clip)
        finally:
            __builtins__["__import__"] = real_import

    # Control flow preserved: skipped check still present, errors empty.
    assert any("duration=skipped" in c for c in result["checks"]), result
    assert result["errors"] == []

    # And the failure was surfaced to logs at DEBUG level.
    debug_records = [
        r for r in caplog.records if r.levelno == logging.DEBUG
    ]
    assert debug_records, "expected a DEBUG log from the duration probe"
    assert any(
        "duration probe" in r.getMessage().lower()
        or "duration=skipped" in r.getMessage()
        for r in debug_records
    ), f"unexpected debug message: {[r.getMessage() for r in debug_records]}"


def test_duration_probe_still_returns_valid_on_pil_missing():
    """Validation must remain 'valid' when PIL is missing (best-effort probe)."""
    module = _load_module()
    real_import = __builtins__["__import__"]

    def fake_import(name, *args, **kwargs):
        if name == "PIL" or name.startswith("PIL."):
            raise ImportError("simulated PIL absence for test")
        return real_import(name, *args, **kwargs)

    import tempfile

    with tempfile.TemporaryDirectory() as td:
        clip = Path(td) / "fake.mp4"
        clip.write_bytes(b"\x00" * 128)
        try:
            __builtins__["__import__"] = fake_import
            result = module._validate_clip(clip)
        finally:
            __builtins__["__import__"] = real_import

    # No errors should have been recorded for the optional duration probe.
    assert result["valid"] is True
    assert result["errors"] == []
    assert any("duration=skipped" in c for c in result["checks"])
