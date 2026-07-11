"""
Regression test: recorder_consumer_lite.py _join_rawvideo_frame_writer
should surface errors from proc.stdin.close(), not swallow them silently.

This test verifies:
1. No bare `except Exception:` in _join_rawvideo_frame_writer (AST check)
2. The except block binds the exception to a variable
3. The bound exception is logged at DEBUG level
"""

import ast
import logging
from pathlib import Path
from unittest.mock import MagicMock

# Import the module to test
import bin.recorder_consumer_lite as rcl


class TestJoinRawvideoFrameWriterSilentError:
    """Tests for _join_rawvideo_frame_writer error handling."""

    def test_no_bare_except_in_join_rawvideo_frame_writer(self):
        """AST check: no bare except Exception: in _join_rawvideo_frame_writer."""
        source_file = Path("bin/recorder_consumer_lite.py")
        source_code = source_file.read_text()
        tree = ast.parse(source_code)

        # Find the _join_rawvideo_frame_writer function
        func_node = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_join_rawvideo_frame_writer":
                func_node = node
                break

        assert func_node is not None, "_join_rawvideo_frame_writer function not found"

        # Check all try/except blocks in the function
        bare_excepts = []
        for node in ast.walk(func_node):
            if isinstance(node, ast.ExceptHandler):
                # Bare except (no type) or except Exception: without binding
                if node.type is None or (
                    isinstance(node.type, ast.Name) and node.type.id == "Exception" and node.name is None
                ):
                    bare_excepts.append(node.lineno)

        assert len(bare_excepts) == 0, (
            f"Found bare except Exception: at lines {bare_excepts} in _join_rawvideo_frame_writer. "
            "All exceptions must be bound and logged."
        )

    def test_stdin_close_failure_logged(self, caplog):
        """Runtime check: proc.stdin.close() failure is logged at DEBUG level."""
        # Create a mock handle with a mock proc that raises on stdin.close()
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.stdin.close.side_effect = IOError("Broken pipe")

        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True

        handle = rcl.VideoCaptureHandle(
            layer="rawvideo",
            out_path=Path("/tmp/test.mp4"),
            stdin_kind="pipe",
            proc=mock_proc,
            writer_thread=mock_thread,
        )

        with caplog.at_level(logging.DEBUG):
            rcl._join_rawvideo_frame_writer(handle, timeout=0.1)

        # Verify the DEBUG log was emitted with the exception info
        assert any(
            "stdin.close() failed" in record.message and "Broken pipe" in record.message
            for record in caplog.records
        ), f"Expected DEBUG log about stdin.close() failure, got: {[r.message for r in caplog.records]}"

    def test_stdin_close_success_no_log(self, caplog):
        """Runtime check: successful stdin.close() does not log errors."""
        # Create a mock handle with a mock proc that succeeds
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.stdin.close.return_value = None

        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True

        handle = rcl.VideoCaptureHandle(
            layer="rawvideo",
            out_path=Path("/tmp/test.mp4"),
            stdin_kind="pipe",
            proc=mock_proc,
            writer_thread=mock_thread,
        )

        with caplog.at_level(logging.DEBUG):
            rcl._join_rawvideo_frame_writer(handle, timeout=0.1)

        # Verify no DEBUG log about stdin.close() was emitted
        assert not any(
            "stdin.close() failed" in record.message
            for record in caplog.records
        ), f"Unexpected DEBUG log about stdin.close() failure: {[r.message for r in caplog.records]}"

    def test_module_compiles(self):
        """Sanity check: module compiles without errors."""
        import py_compile
        source_file = Path("bin/recorder_consumer_lite.py")
        py_compile.compile(str(source_file), doraise=True)
