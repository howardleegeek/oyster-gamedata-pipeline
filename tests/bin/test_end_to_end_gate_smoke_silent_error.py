"""
Regression tests for silent error swallows in bin/end_to_end_gate_smoke.py.

These tests verify that exception handlers in the H8 evidence detection
(_detect_h8_real) and ffprobe duration probe (_detect_video_non_integer_duration)
bind the exception and emit a debug log rather than silently swallowing it.
"""

import ast
from pathlib import Path

import pytest


class TestEndToEndGateSmokeSilentError:
    """Tests for silent error handling in end_to_end_gate_smoke.py."""

    def _read_source(self) -> str:
        return (
            Path(__file__).parent.parent.parent
            / "bin"
            / "end_to_end_gate_smoke.py"
        ).read_text()

    def test_no_bare_except_with_exception_binding(self):
        """All 'except Exception:' / 'except OSError:' must bind the exception."""
        source = self._read_source()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                for handler in node.handlers:
                    if handler.type is not None:
                        type_src = ast.unparse(handler.type)
                        # Catch bare 'Exception' or 'OSError' without binding
                        if type_src in ("Exception", "OSError") and handler.name is None:
                            pytest.fail(
                                f"Found bare 'except {type_src}:' "
                                f"(no 'as' binding) at line {handler.lineno}. "
                                f"Bind the exception and log it."
                            )

    def test_logger_imported(self):
        """A module-level logger must be defined so exceptions can be logged."""
        source = self._read_source()
        assert "import logging" in source
        assert "logger = logging.getLogger" in source

    def test_h8_marker_read_failure_logs_at_debug(self):
        """When H8 .source marker read/parse fails, the exception should be logged at DEBUG."""
        source = self._read_source()
        # Look for logger.debug in the _detect_h8_real function
        # The simplest check is that there's a logger.debug call in the file
        # for the marker failure
        assert 'logger.debug("H8 marker read/parse failed' in source, (
            "Expected a logger.debug call for H8 marker read/parse failures"
        )

    def test_h8_exr_rglob_failure_logs_at_debug(self):
        """When H8 EXR rglob fails, the exception should be logged at DEBUG."""
        source = self._read_source()
        assert 'logger.debug("H8 EXR rglob failed' in source, (
            "Expected a logger.debug call for H8 EXR rglob failures"
        )

    def test_ffprobe_failure_logs_at_debug(self):
        """When ffprobe invocation fails, the exception should be logged at DEBUG."""
        source = self._read_source()
        assert 'logger.debug("ffprobe failed' in source, (
            "Expected a logger.debug call for ffprobe failures"
        )

    def test_module_compiles(self):
        """Module must be importable / compile cleanly."""
        source = self._read_source()
        compile(source, "end_to_end_gate_smoke.py", "exec")
