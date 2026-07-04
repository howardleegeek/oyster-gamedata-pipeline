"""Regression test: bin/depth_anything_v2_inference.py no longer silently
swallows exceptions in _video_total_frames().

Previously the probe was:

    try:
        meta = iio.get_reader(...).get_meta_data()
        ...
    except Exception:
        pass
    return 0

We now bind the exception to a module-level logger and emit a DEBUG
record (with exc_info=True) so an uninstalled/broken ffmpeg or
unreadable container is diagnosable from the operator's log tail
without re-running the job. Control flow is unchanged: the function
still returns 0 on failure (so the UI just shows '?', not a crash).

Self-review: scope = one file (bin/depth_anything_v2_inference.py),
one logical change (bind previously-bare except to `e` + _LOG.debug).
"""

from __future__ import annotations

import ast
import logging
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BIN_DIR = REPO_ROOT / "bin"


def _ensure_imageio_stub() -> None:
    """The test venv may not have `imageio` installed. Inject a stub
    before importing the real module so we can monkeypatch its `iio`
    attribute later. The stub's `get_reader` will be replaced per-test."""
    if "imageio.v2" in sys.modules:
        return
    imageio_stub = types.ModuleType("imageio")
    v2_stub = types.ModuleType("imageio.v2")
    imageio_stub.v2 = v2_stub  # type: ignore[attr-defined]
    v2_stub.get_reader = MagicMock()  # type: ignore[attr-defined]
    sys.modules["imageio"] = imageio_stub
    sys.modules["imageio.v2"] = v2_stub


# Stub imageio before importing the module under test.
_ensure_imageio_stub()
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))
import depth_anything_v2_inference as dav2  # noqa: E402


# --- (1) AST: no bare `except Exception:` in _video_total_frames ---


def test_video_total_frames_has_no_bare_except_exception() -> None:
    src = (BIN_DIR / "depth_anything_v2_inference.py").read_text()
    tree = ast.parse(src)
    fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "_video_total_frames" in fns, (
        "function _video_total_frames not found in depth_anything_v2_inference.py"
    )
    fn = fns["_video_total_frames"]
    bare: list[int] = []
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
    assert bare == [], (
        f"_video_total_frames: bare `except Exception:` still present at lines {bare}"
    )


# --- (2) module-level logger present and named after the module ---


def test_module_logger_is_named_after_module() -> None:
    assert hasattr(dav2, "_LOG"), (
        "expected module-level _LOG logger on depth_anything_v2_inference"
    )
    assert dav2._LOG.name == "depth_anything_v2_inference"


# --- (3) iio.get_reader raises → return 0 (control flow preserved) + DEBUG log ---


def test_video_total_frames_returns_zero_and_logs_on_imageio_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing/garbage video path: iio.get_reader raises; the function
    must still return 0 (so the UI shows '?', no crash) AND must now
    emit a DEBUG log naming the offending path and the exception reason."""

    def _explode(*_args, **_kwargs):
        raise RuntimeError("ffmpeg binary missing or unreadable container")

    fake_iio = MagicMock()
    fake_iio.get_reader.side_effect = _explode
    monkeypatch.setattr(dav2, "iio", fake_iio)

    target = tmp_path / "missing.mp4"
    with caplog.at_level(logging.DEBUG, logger="depth_anything_v2_inference"):
        result = dav2._video_total_frames(target)

    assert result == 0, "control flow broken: should still return 0 on probe failure"
    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert debug_records, "expected a DEBUG log record on iio.get_reader failure"
    msg = " ".join(r.getMessage() for r in debug_records)
    assert str(target) in msg, (
        f"DEBUG log should mention the offending path; got: {msg!r}"
    )
    assert "ffmpeg binary missing" in msg, (
        f"DEBUG log should include the exception message; got: {msg!r}"
    )
