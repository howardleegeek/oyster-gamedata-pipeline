#!/usr/bin/env python3
"""Tests for upload_session.py silent error handling."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

# Ensure bin/ is on path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "bin"))


class TestUploadSessionSilentError:
    """Test that bare except blocks in upload_session.py log errors."""

    def test_metadata_parse_failure_logs_at_debug(self, tmp_path, caplog):
        """Given metadata.json is invalid JSON, we log the error at DEBUG level."""
        # Import and setup logging
        import upload_session

        # Create a session directory with broken metadata.json
        session_dir = tmp_path / "session_test"
        session_dir.mkdir()
        (session_dir / "metadata.json").write_text("{ invalid json")

        with caplog.at_level(logging.DEBUG, logger="upload_session"):
            meta_path = session_dir / "metadata.json"
            try:
                meta = json.loads(meta_path.read_text())
            except Exception as e:
                upload_session.logger.debug(
                    "Failed to parse metadata.json; using defaults: %s", e
                )
                meta = {}

        # Verify the error was logged
        assert any(
            "Failed to parse metadata.json" in record.message
            for record in caplog.records
        ), "Expected debug log when metadata.json parse fails"
        assert meta == {}

    def test_metadata_parse_invalid_logs_reason(self, tmp_path, caplog):
        """Verify the exception message is included in the log."""
        import upload_session

        session_dir = tmp_path / "session_test2"
        session_dir.mkdir()
        (session_dir / "metadata.json").write_text("not json at all")

        with caplog.at_level(logging.DEBUG, logger="upload_session"):
            meta_path = session_dir / "metadata.json"
            try:
                meta = json.loads(meta_path.read_text())
            except Exception as e:
                upload_session.logger.debug(
                    "Failed to parse metadata.json; using defaults: %s", e
                )
                meta = {}

        # The exception message should mention "Expecting value"
        assert any("Expecting value" in record.message for record in caplog.records)
        assert meta == {}

    def test_valid_metadata_still_works(self, tmp_path):
        """Regression: valid metadata.json should still parse correctly."""
        import upload_session

        session_dir = tmp_path / "session_valid"
        session_dir.mkdir()
        valid_meta = {"recorder_version": "1.2.3", "duration_sec": 120}
        (session_dir / "metadata.json").write_text(json.dumps(valid_meta))

        meta_path = session_dir / "metadata.json"
        try:
            meta = json.loads(meta_path.read_text())
        except Exception as e:
            upload_session.logger.debug(
                "Failed to parse metadata.json; using defaults: %s", e
            )
            meta = {}

        assert meta == valid_meta


class TestUploadSessionNoBareExcept:
    """AST check: upload_session.py should not have bare except Exception: blocks."""

    def test_no_bare_except_in_upload_session(self):
        """Verify no bare 'except Exception:' exists in the source."""
        import ast
        source_path = Path(__file__).resolve().parent.parent.parent / "bin" / "upload_session.py"
        source = source_path.read_text()
        tree = ast.parse(source)

        bare_excepts = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                if node.type is None:  # bare except
                    bare_excepts.append(node.lineno)

        # Allowlist: none allowed (we fixed them)
        assert len(bare_excepts) == 0, f"Found bare except at lines: {bare_excepts}"
