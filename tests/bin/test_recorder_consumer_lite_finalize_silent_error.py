"""
Regression test: recorder_consumer_lite.py _stop_obs_capture_handle
should surface errors from client.close(), not swallow them silently.

This test verifies:
1. No bare `except Exception:` in _stop_obs_capture_handle (AST check)
2. The except block binds the exception to a variable
3. The bound exception is logged at DEBUG level
"""

import ast
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

# Import the module to test
import bin.recorder_consumer_lite as rcl


class TestStopObsCaptureHandleSilentError:
    """Tests for _stop_obs_capture_handle error handling."""

    def test_no_bare_except_in_stop_obs_capture_handle(self):
        """AST check: no bare except Exception: in _stop_obs_capture_handle."""
        source_file = Path("bin/recorder_consumer_lite.py")
        source_code = source_file.read_text()
        tree = ast.parse(source_code)

        # Find the _stop_obs_capture_handle function
        func_node = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_stop_obs_capture_handle":
                func_node = node
                break

        assert func_node is not None, "_stop_obs_capture_handle function not found"

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
            f"Found bare except Exception: at lines {bare_excepts} in _stop_obs_capture_handle. "
            "All exceptions must be bound and logged."
        )

    def test_client_close_failure_logged(self, caplog):
        """Runtime check: client.close() failure is logged at DEBUG level."""
        # Create a mock handle that will successfully get the output path
        mock_client = MagicMock()
        # Return a valid response so we get past the StopRecord call
        mock_client.request.return_value = {"responseData": {"outputPath": "/tmp/test.mkv"}}

        mock_proc = MagicMock()

        handle = rcl.ObsCaptureHandle(
            layer="obs",
            out_path=Path("/tmp/test.mp4"),
            proc=mock_proc,
            client=mock_client,
            output_dir=Path("/tmp"),
            started_at=1234567890.0,
            video_encoder="h264",
            output_profile=None,
        )

        with caplog.at_level(logging.DEBUG):
            with patch.object(rcl, "_move_obs_output_to_video_path"):
                with patch.object(rcl, "_terminate_obs_process"):
                    # Make client.close() raise - this happens in the finally block
                    mock_client.close.side_effect = Exception("close failed")

                    # Should not raise, but should log
                    rcl._stop_obs_capture_handle(handle)

        # Check that the close failure was logged
        debug_messages = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("client.close() failed" in msg for msg in debug_messages), (
            f"Expected debug log about client.close() failure. Got: {debug_messages}"
        )


class TestModuleCompiles:
    """Sanity check: module still imports correctly."""

    def test_module_imports(self):
        """Module should import without errors."""
        assert rcl is not None
        assert hasattr(rcl, "_stop_obs_capture_handle")
