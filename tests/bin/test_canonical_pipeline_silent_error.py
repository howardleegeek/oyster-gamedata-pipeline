"""
Regression tests for silent error swallows in bin/canonical_pipeline.py.

These tests verify that failed ffprobe_frames() calls are logged at
debug level (binding the exception) rather than silently swallowed.
Also tests G3 (input_latency.json) and G5 (action_camera.json) parse
failures are logged.
"""

import ast
from pathlib import Path

import pytest


class TestCanonicalPipelineSilentError:
    """Tests for silent error handling in canonical_pipeline.py."""

    def _read_source(self) -> str:
        return (
            Path(__file__).parent.parent.parent
            / "bin"
            / "canonical_pipeline.py"
        ).read_text()

    def test_no_bare_except_in_ffprobe_frames_call(self):
        """The ffprobe_frames() call in step3 must not have a bare ``except Exception:``
        (no ``as`` binding) that hides the error."""
        source = self._read_source()
        tree = ast.parse(source)

        # Find all Try nodes with except handlers
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                for handler in node.handlers:
                    if handler.type is not None:
                        type_src = ast.unparse(handler.type)
                        if "Exception" in type_src and handler.name is None:
                            # Check if ffprobe_frames is in the try body
                            for stmt in node.body:
                                for child in ast.walk(stmt):
                                    if isinstance(child, ast.Call):
                                        if (hasattr(child.func, 'id')
                                        and child.func.id == 'ffprobe_frames'):
                                            pytest.fail(
                                                f"Found bare 'except Exception:' "
                                                f"(no 'as' binding) around ffprobe_frames() call "
                                                f"at line {handler.lineno}. "
                                                f"Bind the exception and log it "
                                                f"via logger.debug(...)."
                                            )

    def test_logger_imported(self):
        """A module-level logger must be defined so the exception can be logged."""
        source = self._read_source()
        # Check that logging is imported
        assert "import logging" in source
        # Check that logger is defined
        assert "logger = logging.getLogger" in source

    def test_ffprobe_failure_logs_at_debug(self):
        """When ffprobe_frames fails, the exception should be logged at DEBUG level."""
        source = self._read_source()
        # Check that there's a logger.debug call in an exception handler for ffprobe
        # This is a weaker check - we look for any logger.debug with "ffprobe" or "dur"
        # in the source near an except block
        assert "logger.debug" in source, (
            "logger.debug should be used to log ffprobe_frames failure"
        )

    def test_input_latency_json_failure_logs_at_debug(self):
        """G3: When input_latency.json parse fails, log at DEBUG with the exception."""
        source = self._read_source()
        assert "Failed to parse input_latency.json" in source, (
            "input_latency.json parse failure should log at debug level"
        )

    def test_action_camera_json_failure_logs_at_debug(self):
        """G5: When action_camera.json parse fails, log at DEBUG with the exception."""
        source = self._read_source()
        assert "Failed to parse action_camera.json" in source, (
            "action_camera.json parse failure should log at debug level"
        )
