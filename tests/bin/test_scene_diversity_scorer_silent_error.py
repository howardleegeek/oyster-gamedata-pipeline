"""Regression test for the silent-error swallow in
`bin/scene_diversity_scorer.py` `analyze_video` cleanup.

The handler used to be a bare `except Exception: pass` for both the
per-frame `f.unlink()` removal and the directory `os.rmdir()` removal.
A failing cleanup (e.g. permission error, file vanished mid-cleanup)
was therefore invisible to operators. The fix replaces each `pass`
with a `_log.debug(...)` call that binds the exception
(``exc_info=True``) so the failure is visible in DEBUG logs without
changing the control flow (the next file / the function still
proceeds normally).

Checks:
  1. Static guard: both `analyze_video` cleanup handlers must NOT be
     a bare `pass`.
  2. Static guard: both handlers must include `_log.debug` /
     `logger.debug` to surface the swallow.
  3. Behavioural guard: triggering a failure in the per-frame unlink
     loop produces a DEBUG log line and the function still completes
     successfully.
"""

from __future__ import annotations

import ast
import logging
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

BIN_DIR = Path(__file__).resolve().parent.parent.parent / "bin"
sys.path.insert(0, str(BIN_DIR))

SRC_PATH = BIN_DIR / "scene_diversity_scorer.py"


def _find_analyze_video_finally_handlers() -> list[ast.ExceptHandler]:
    """Return every `except Exception` handler inside the `finally` of
    `analyze_video`. The cleanup try/excepts live there and are
    currently identified by their position in the Try.body / Try.finalbody."""
    tree = ast.parse(SRC_PATH.read_text(encoding="utf-8"))
    handlers: list[ast.ExceptHandler] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "analyze_video":
            for child in ast.walk(node):
                if isinstance(child, ast.Try):
                    # The cleanup try/excepts are not the outer one
                    # (which wraps the work), they're the inner try
                    # blocks whose `body` is a single statement that
                    # could raise (e.g. `f.unlink()` or `os.rmdir(...)`).
                    for h in child.handlers:
                        type_text = ast.unparse(h.type) if h.type else "bare"
                        if "Exception" in type_text and not isinstance(
                            h.body[0], ast.Raise
                        ):
                            # Exclude the outer "main" handler (handled
                            # at module level, not here).
                            handlers.append(h)
            return handlers
    pytest.fail("Could not find analyze_video Try/Except handlers")


def test_no_bare_pass_in_analyze_video_cleanup() -> None:
    """analyze_video cleanup handlers must not be bare `pass`."""
    handlers = _find_analyze_video_finally_handlers()
    assert handlers, "Expected at least one Exception handler in analyze_video cleanup"
    bare_pass = [
        h for h in handlers
        if len(h.body) == 1 and isinstance(h.body[0], ast.Pass)
    ]
    if bare_pass:
        pytest.fail(
            f"Found {len(bare_pass)} bare `pass` in analyze_video cleanup "
            "handlers. Should use _log.debug(...) to bind the exception."
        )


def test_analyze_video_cleanup_logs_via_debug() -> None:
    """analyze_video cleanup handlers must include a debug log call."""
    handlers = _find_analyze_video_finally_handlers()
    assert handlers, "Expected at least one Exception handler in analyze_video cleanup"
    for h in handlers:
        src = ast.unparse(h)
        has_debug = (
            "_log.debug" in src
            or "logger.debug" in src
            or ("getLogger" in src and ".debug(" in src)
        )
        assert has_debug, (
            "analyze_video cleanup handler is missing _log.debug(...) call. "
            f"Got handler source:\n{src}"
        )


def test_analyze_video_unlink_failure_logs_and_continues(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """When per-frame cleanup fails, a DEBUG log is emitted and the
    function still returns the score dict normally (control flow
    unchanged)."""
    import scene_diversity_scorer as sds

    # analyze_video creates its output_dir via tempfile.mkdtemp(). We
    # need a frame file inside THAT directory for the cleanup glob to
    # find, so we monkey-patch tempfile.mkdtemp to return our staged
    # path. We then patch extract_frames, compute_frame_histograms,
    # compute_diversity_score to bypass the real work, and patch
    # Path.unlink to raise on the cleanup unlink. The function should
    # still complete and emit a DEBUG log line for the failed unlink.
    output_dir = tmp_path / "scene_div_test"
    output_dir.mkdir()
    staged_frame = output_dir / "frame_0001.jpg"
    staged_frame.write_bytes(b"")

    fake_hist = object()  # compute_diversity_score is patched separately

    with patch("scene_diversity_scorer.tempfile.mkdtemp", return_value=str(output_dir)), \
         patch.object(sds, "extract_frames", return_value=[str(staged_frame)]), \
         patch.object(sds, "compute_frame_histograms", return_value=fake_hist), \
         patch.object(sds, "compute_diversity_score", return_value=0.42), \
         patch.object(
             Path, "unlink", side_effect=PermissionError("denied")
         ) as _mock_unlink:
        with caplog.at_level(logging.DEBUG, logger="scene_diversity_scorer"):
            result = sds.analyze_video("/dev/null", threshold=0.35)

    # The function still returns the normal score dict.
    assert isinstance(result, dict)
    assert result["score"] == 0.42
    assert result["flagged"] is False
    # A DEBUG record was emitted mentioning the failed frame.
    debug_records = [
        r for r in caplog.records
        if r.levelno == logging.DEBUG
        and r.name == "scene_diversity_scorer"
    ]
    assert any(
        "frame" in r.getMessage().lower() and "remove" in r.getMessage().lower()
        for r in debug_records
    ), (
        "Expected a DEBUG log line for the failed frame unlink; "
        f"got messages: {[r.getMessage() for r in debug_records]}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
