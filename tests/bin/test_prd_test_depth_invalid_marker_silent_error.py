"""Regression tests for silent error swallows in bin/prd_test_depth_invalid_marker.py.

The four `try: import OpenImageIO / OpenEXR: except ImportError: pass` blocks
in write_exr() and read_exr() are fallback chains: when an optional EXR
library is not installed the function falls through to the next backend.
Previously the ImportError was silently dropped, making the cause invisible
when (e.g.) the NPZ fallback was unexpectedly used. This test guards that:

  1. No `except (...):\n    pass` may remain in write_exr() / read_exr().
  2. When OpenImageIO / OpenEXR are not importable, the module logger
     records a DEBUG message binding the exception (so the fallback
     path is observable, not silent).
  3. Control flow is unchanged: a missing EXR library still falls
     through to the NPZ backend (write_exr returns True after NPZ
     write; read_exr returns the npz depth array).
"""

from __future__ import annotations

import ast
import builtins
import importlib
import logging
import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BIN_DIR = REPO_ROOT / "bin"
sys.path.insert(0, str(BIN_DIR))

# Force a clean import so module-level caches are empty.
sys.modules.pop("prd_test_depth_invalid_marker", None)
import prd_test_depth_invalid_marker as pdim  # noqa: E402
importlib.reload(pdim)


# ---------------------------------------------------------------------------
# AST guard: no bare `except (...): pass` in write_exr / read_exr
# ---------------------------------------------------------------------------


def _bare_pass_handlers(func: ast.FunctionDef) -> list[ast.ExceptHandler]:
    """Return ExceptHandler nodes whose body is exactly one ast.Pass."""
    found: list[ast.ExceptHandler] = []
    for child in ast.walk(func):
        if isinstance(child, ast.ExceptHandler):
            if len(child.body) == 1 and isinstance(child.body[0], ast.Pass):
                found.append(child)
    return found


def test_no_bare_pass_in_write_exr_or_read_exr() -> None:
    """write_exr() and read_exr() must not have a bare `except: pass`."""
    src = (BIN_DIR / "prd_test_depth_invalid_marker.py").read_text()
    tree = ast.parse(src)
    bad_total = 0
    bad_funcs: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in {"write_exr", "read_exr"}:
            bare = _bare_pass_handlers(node)
            if bare:
                bad_total += len(bare)
                bad_funcs.append(node.name)
    assert bad_total == 0, (
        f"Found {bad_total} bare `except ...: pass` handler(s) in "
        f"{bad_funcs}; bind the exception and call logger.debug()."
    )


# ---------------------------------------------------------------------------
# Runtime guard: write_exr fallback path emits a DEBUG log
# ---------------------------------------------------------------------------


def test_write_exr_fallback_logs_at_debug(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When OpenImageIO AND OpenEXR are unimportable, write_exr() must log
    a DEBUG message binding the ImportError before falling through to NPZ.
    """
    target = tmp_path / "depth.exr"
    depth = np.zeros((4, 4), dtype=np.float32)

    real_import = builtins.__import__

    def _fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in {"OpenImageIO", "OpenEXR", "Imath"}:
            raise ImportError(f"no {name} installed")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    with caplog.at_level(logging.DEBUG, logger="prd_test_depth_invalid_marker"):
        result = pdim.write_exr(target, depth)

    # Control flow: still True (NPZ fallback wrote the depth).
    assert result is True
    npz = target.with_suffix(".npz")
    assert npz.exists(), "NPZ fallback should have written the depth"
    # Logging: at least one DEBUG record naming the fallback.
    debug = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert debug, "expected DEBUG log records for the ImportError fallback"
    msgs = [r.getMessage() for r in debug]
    assert any("OpenImageIO" in m for m in msgs), (
        f"expected a DEBUG log mentioning the OpenImageIO import failure; got: {msgs}"
    )


# ---------------------------------------------------------------------------
# Runtime guard: read_exr fallback path emits a DEBUG log
# ---------------------------------------------------------------------------


def test_read_exr_fallback_logs_at_debug(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When OpenImageIO AND OpenEXR are unimportable, read_exr() must log
    a DEBUG message binding the ImportError before falling through to NPZ.
    """
    # Seed the NPZ fallback that write_exr would have produced.
    target = tmp_path / "depth.exr"
    npz = target.with_suffix(".npz")
    depth_in = np.arange(16, dtype=np.float32).reshape(4, 4)
    np.savez_compressed(npz, depth=depth_in)

    real_import = builtins.__import__

    def _fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in {"OpenImageIO", "OpenEXR", "Imath"}:
            raise ImportError(f"no {name} installed")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    with caplog.at_level(logging.DEBUG, logger="prd_test_depth_invalid_marker"):
        result = pdim.read_exr(target)

    # Control flow: NPZ fallback returns the saved depth.
    assert result is not None
    np.testing.assert_array_equal(result, depth_in)
    # Logging: at least one DEBUG record naming the fallback.
    debug = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert debug, "expected DEBUG log records for the ImportError fallback"
    msgs = [r.getMessage() for r in debug]
    assert any("OpenImageIO" in m for m in msgs), (
        f"expected a DEBUG log mentioning the OpenImageIO import failure; got: {msgs}"
    )


# ---------------------------------------------------------------------------
# Positive control: when both backends ARE importable, no DEBUG fallback log
# is emitted for the try/except ImportError blocks themselves. (A successful
# path is a successful path; we only surface failures.)
# ---------------------------------------------------------------------------


def test_write_exr_happy_path_oiio_no_debug_fallback(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If OpenImageIO is importable and `open()` succeeds, no ImportError
    fallback log is emitted by write_exr.
    """
    target = tmp_path / "depth.exr"
    depth = np.zeros((4, 4), dtype=np.float32)

    class _FakeOut:
        def __init__(self, *_a, **_kw):
            pass
        def open(self, *_a, **_kw):
            return True
        def write_image(self, *_a, **_kw):
            return True
        def close(self):
            pass

    class _FakeOI:
        FLOAT = 1

        class _ImageSpec:
            def __init__(self, *a, **kw):
                self.channelnames = []

        class _ImageOutput:
            @staticmethod
            def create(_path):
                return _FakeOut()

        ImageSpec = _ImageSpec
        ImageOutput = _ImageOutput

    monkeypatch.setitem(sys.modules, "OpenImageIO", _FakeOI)

    with caplog.at_level(logging.DEBUG, logger="prd_test_depth_invalid_marker"):
        result = pdim.write_exr(target, depth)

    assert result is True
    # No fallback-related DEBUG records (no ImportError happened).
    fallback_msgs = [
        r.getMessage() for r in caplog.records
        if r.levelno == logging.DEBUG and ("OpenImageIO" in r.getMessage() or "OpenEXR" in r.getMessage())
    ]
    assert not fallback_msgs, (
        f"happy path should not emit fallback DEBUG logs; got: {fallback_msgs}"
    )
