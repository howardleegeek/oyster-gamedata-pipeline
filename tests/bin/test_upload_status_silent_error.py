"""
Regression tests for silent error swallows in bin/upload_status.py.

The two ``datetime.fromisoformat`` parsing blocks in ``get_status()``
used to swallow every exception with a bare ``except Exception: pass``.
That hides corrupt/missing timestamps in the state JSON from operators.
This test verifies the exception is now bound and surfaced via
``logger.debug(..., exc_info=True)`` while preserving the control flow
(session is still skipped silently from the user's perspective — only
the dev/operator debug log changes).
"""

import ast
import logging
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).parent.parent.parent
TARGET = REPO_ROOT / "bin" / "upload_status.py"


def _read_source() -> str:
    return TARGET.read_text()


class TestUploadStatusSilentError:
    """Tests for silent error handling in get_status() timestamp parsing."""

    def test_no_bare_except_in_get_status(self):
        """get_status() must not have a bare ``except Exception:`` (no
        ``as`` binding) that hides the error."""
        source = _read_source()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "get_status":
                for child in ast.walk(node):
                    if isinstance(child, ast.ExceptHandler):
                        if child.type is None:
                            pytest.fail(
                                "Found bare 'except:' in get_status() at line "
                                f"{child.lineno}. Bind the exception."
                            )
                        type_src = ast.unparse(child.type)
                        if "Exception" in type_src and child.name is None:
                            pytest.fail(
                                "Found bare 'except Exception:' (no 'as' binding) "
                                f"in get_status() at line {child.lineno}. "
                                "Bind the exception and log via logger.debug(...)."
                            )

    def test_logger_module_level_present(self):
        """A module-level logger must be defined so the exception can be logged."""
        source = _read_source()
        assert "import logging" in source, "logging module must be imported"
        assert "logger = logging.getLogger(__name__)" in source, (
            "module-level logger binding is required"
        )

    def test_logger_debug_called_in_except_branches(self):
        """Both except branches in get_status() must invoke logger.debug(...)."""
        source = _read_source()
        tree = ast.parse(source)
        debug_calls = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "get_status":
                for child in ast.walk(node):
                    if (
                        isinstance(child, ast.ExceptHandler)
                        and child.name is not None
                    ):
                        # Check that logger.debug(...) is called inside the body
                        for stmt in child.body:
                            for sub in ast.walk(stmt):
                                if (
                                    isinstance(sub, ast.Call)
                                    and isinstance(sub.func, ast.Attribute)
                                    and sub.func.attr == "debug"
                                ):
                                    debug_calls += 1
        assert debug_calls >= 2, (
            f"Expected at least 2 logger.debug(...) calls in get_status() "
            f"except branches, found {debug_calls}"
        )

    def test_corrupt_completed_at_does_not_crash(self, tmp_path, monkeypatch, caplog):
        """Runtime: a corrupt ``completed_at`` must not raise; the function
        must still return a valid status dict."""
        # Patch STATE_FILE to a temp file with a corrupt timestamp
        state_file = tmp_path / "upload_state.json"
        state_file.write_text(
            '{"sessions": {'
            '"s1": {"state": "completed", "file_size": 1024, '
            '"session_id": "s1", "completed_at": "not-a-date"},'
            '"s2": {"state": "failed", "file_size": 0, '
            '"session_id": "s2", "created_at": "also-bad"}'
            '}}'
        )
        import bin.upload_status as upload_status
        monkeypatch.setattr(upload_status, "STATE_FILE", state_file)

        with caplog.at_level(logging.DEBUG, logger="bin.upload_status"):
            result = upload_status.get_status()

        # Must not raise; must return the expected structure
        assert isinstance(result, dict)
        assert result["completed"] and len(result["completed"]) == 1
        assert result["failed"] and len(result["failed"]) == 1
        # The corrupt timestamps must be skipped → last_24h counts stay at 0
        assert result["last_24h"]["count"] == 0
        assert result["last_24h"]["size"] == 0
        assert result["last_24h"]["failures"] == 0

        # Both debug logs should have been emitted
        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert len(debug_records) >= 2, (
            f"Expected ≥2 DEBUG records from corrupt timestamps, got "
            f"{len(debug_records)}: {[r.getMessage() for r in debug_records]}"
        )
        joined = "\n".join(r.getMessage() for r in debug_records)
        assert "completed_at" in joined
        assert "created_at" in joined

    def test_valid_timestamps_parsed_normally(self, tmp_path, monkeypatch):
        """Regression: well-formed ISO timestamps must be parsed into the
        last_24h counters and must NOT trigger any debug logs."""
        from datetime import datetime, timedelta

        now = datetime.now()
        recent = (now - timedelta(hours=1)).isoformat()
        old = (now - timedelta(hours=48)).isoformat()

        state_file = tmp_path / "upload_state.json"
        state_file.write_text(
            '{"sessions": {'
            '"s1": {"state": "completed", "file_size": 2048, '
            '"session_id": "s1", "completed_at": "' + recent + '"},'
            '"s2": {"state": "failed", "file_size": 0, '
            '"session_id": "s2", "created_at": "' + old + '"}'
            '}}'
        )
        import bin.upload_status as upload_status
        monkeypatch.setattr(upload_status, "STATE_FILE", state_file)

        result = upload_status.get_status()

        # Recent completed_at → counted in last_24h.count
        assert result["last_24h"]["count"] == 1
        assert result["last_24h"]["size"] == 2048
        # Old failed created_at → not counted
        assert result["last_24h"]["failures"] == 0
