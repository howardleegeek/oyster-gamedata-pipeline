"""
Regression test: recorder_consumer_lite.py _start_rawvideo_frame_writer
should surface errors from stdin.close(), not swallow them silently.

This test verifies:
1. No bare `except Exception:` in _start_rawvideo_frame_writer (AST check)
2. The except block in the finally block binds the exception to a variable
3. The bound exception is logged at DEBUG level
"""

import ast
import logging
from pathlib import Path

# Import the module to test
import bin.recorder_consumer_lite as rcl


class TestStartRawvideoFrameWriterSilentError:
    """Tests for _start_rawvideo_frame_writer error handling."""

    def test_no_bare_except_in_start_rawvideo_frame_writer(self):
        """AST check: no bare except Exception: in _start_rawvideo_frame_writer."""
        source_file = Path("bin/recorder_consumer_lite.py")
        source_code = source_file.read_text()
        tree = ast.parse(source_code)

        # Find the _start_rawvideo_frame_writer function
        func_node = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_start_rawvideo_frame_writer":
                func_node = node
                break

        assert func_node is not None, "_start_rawvideo_frame_writer function not found"

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
            f"Found bare except Exception: at lines {bare_excepts} in _start_rawvideo_frame_writer. "
            "All exceptions must be bound and logged."
        )

    def test_stdin_close_failure_logged(self, caplog):
        """Runtime check: stdin.close() failure is logged at DEBUG level."""
        import queue
        import threading
        from unittest.mock import MagicMock

        # Create a mock handle
        mock_handle = MagicMock()
        mock_handle.frame_queue = queue.Queue()
        mock_handle.stop_event = threading.Event()
        mock_handle.error_event = threading.Event()
        mock_handle.writer_lock = threading.Lock()
        mock_handle.writer_thread = None
        mock_handle.frames_written = 0

        # Create a mock process with a stdin that will raise on close
        mock_proc = MagicMock()
        mock_stdin = MagicMock()
        mock_stdin.close.side_effect = OSError("close failed")
        mock_proc.stdin = mock_stdin

        with caplog.at_level(logging.DEBUG):
            # Start the writer thread - it will process the stop event and hit finally
            mock_handle.stop_event.set()  # Exit immediately

            try:
                rcl._start_rawvideo_frame_writer(mock_handle, mock_proc)
                # Wait for thread to complete
                if mock_handle.writer_thread:
                    mock_handle.writer_thread.join(timeout=1.0)
            except Exception:
                # Thread may raise; that's ok - we just need to check the log
                pass

            # Check that the DEBUG log was emitted for stdin.close() failure
            debug_logs = [rec.message for rec in caplog.records if rec.levelno == logging.DEBUG]
            stdin_close_logged = any("stdin.close()" in msg for msg in debug_logs)
            assert stdin_close_logged, (
                f"Expected DEBUG log for stdin.close() failure. Got: {debug_logs}"
            )

    def test_module_compiles(self):
        """Sanity check: module compiles without errors."""
        import py_compile

        source_file = Path("bin/recorder_consumer_lite.py")
        py_compile.compile(str(source_file), doraise=True)
