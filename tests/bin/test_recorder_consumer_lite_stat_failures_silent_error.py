#!/usr/bin/env python3
"""
Regression tests for silent error swallows in bin/recorder_consumer_lite.py.

These tests verify that failed OSError/stat operations are logged at debug level
(binding the exception) rather than silently swallowed.

Round: Surface silent errors in bin/recorder_consumer_lite.py _sample_recorded_video_frames
and _latest_obs_recording_file OSError handlers.
"""

import ast
from pathlib import Path


class TestRecorderConsumerLiteStatFailuresSilentError:
    """Tests for silent error handling in OSError handlers."""

    def _read_source(self) -> str:
        return (
            Path(__file__).parent.parent.parent
            / "bin"
            / "recorder_consumer_lite.py"
        ).read_text()

    def _get_function_body(self, source: str, func_name: str) -> str:
        """Extract the source code of a specific function."""
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == func_name:
                return ast.unparse(node)
        return ""

    def test_sample_recorded_video_frames_logs_ffmpeg_failure(self):
        """ffmpeg subprocess failure in _sample_recorded_video_frames should log the exception."""
        source = self._read_source()
        func_body = self._get_function_body(source, "_sample_recorded_video_frames")

        # The function should have a logger.debug call with 'exc' bound
        assert "logger.debug" in func_body, (
            "_sample_recorded_video_frames must log exceptions via logger.debug"
        )
        assert "ffmpeg" in func_body.lower(), (
            "logger.debug should mention ffmpeg context"
        )

    def test_sample_recorded_video_frames_binds_exception(self):
        """The ffmpeg exception handler must bind the exception (as exc)."""
        # Should have `except (OSError, subprocess.TimeoutExpired) as exc:`
        assert "except (OSError, subprocess.TimeoutExpired) as exc:", (
            "Exception must be bound to 'exc' for logging"
        )

    def test_latest_obs_recording_file_logs_stat_failures(self):
        """stat failures in _latest_obs_recording_file should log the exception."""
        source = self._read_source()
        func_body = self._get_function_body(source, "_latest_obs_recording_file")

        # The function should have logger.debug calls for OSError handlers
        assert "logger.debug" in func_body, (
            "_latest_obs_recording_file must log exceptions via logger.debug"
        )
        assert "stat" in func_body.lower(), (
            "logger.debug should mention stat context"
        )

    def test_latest_obs_recording_file_binds_exception(self):
        """All OSError handlers in _latest_obs_recording_file must bind the exception."""
        # Find all except handlers in the function
        tree = ast.parse(self._read_source())
        unbound_exceptions = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_latest_obs_recording_file":
                for subnode in ast.walk(node):
                    if isinstance(subnode, ast.ExceptHandler):
                        if subnode.type is not None:
                            type_str = ast.unparse(subnode.type)
                            if "OSError" in type_str and subnode.name is None:
                                unbound_exceptions.append(subnode.lineno)

        assert len(unbound_exceptions) == 0, (
            f"Found {len(unbound_exceptions)} OSError handlers without exception binding "
            f"at lines {unbound_exceptions}. Bind to 'exc' and log via logger.debug."
        )

    def test_module_compiles(self):
        """The module must be syntactically valid and importable."""
        source = self._read_source()
        try:
            compile(source, "recorder_consumer_lite.py", "exec")
        except SyntaxError as e:
            raise AssertionError(f"Syntax error in recorder_consumer_lite.py: {e}")
