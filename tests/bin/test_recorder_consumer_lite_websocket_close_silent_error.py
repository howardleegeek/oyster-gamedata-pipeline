"""
Regression test: recorder_consumer_lite._wait_for_obs_websocket() cleanup logs errors

Tests that the bare `except Exception:` block in the OBS client close during
the retry loop now binds the exception and logs at DEBUG level.

Author: Production Engineering Team
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

# Target: bin/recorder_consumer_lite
import bin.recorder_consumer_lite as recorder_consumer_lite


class TestObsWebsocketCloseSilentError:
    """Test that OBS client close errors are logged, not silently swallowed."""

    def test_obs_client_close_logs_at_debug(self, caplog: pytest.LogCaptureFixture) -> None:
        """Verify that client.close() errors are logged at DEBUG level."""
        # Set logging to DEBUG to capture our message
        caplog.set_level(logging.DEBUG, logger="bin.recorder_consumer_lite")

        # Create a mock client that raises on close()
        mock_client = MagicMock()
        mock_client.connect.side_effect = ConnectionRefusedError("OBS not ready")
        mock_client.close.side_effect = RuntimeError("Close failed")

        # Create a factory that returns our mock
        def client_factory():
            return mock_client

        # Mock proc with poll() returning None (still running)
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None

        # Patch time.monotonic to avoid infinite loop - make it timeout immediately
        with patch.object(recorder_consumer_lite.time, "monotonic") as mock_time:
            # First call: check proc.poll (False)
            # Second call: after sleep check, exceeds deadline
            mock_time.side_effect = [0, 0, 100]  # Deadline exceeded on 3rd check

            # Call the function - should raise ObsWebSocketError
            with pytest.raises(recorder_consumer_lite.ObsWebSocketError):
                recorder_consumer_lite._wait_for_obs_websocket(
                    mock_proc,
                    client_factory=client_factory,
                    timeout_sec=0.1,
                )

        # Assert: the close() error was logged at DEBUG
        assert any(
            "Failed to close OBS client during retry" in record.message
            and record.levelname == "DEBUG"
            for record in caplog.records
        ), "Expected DEBUG log about failed OBS client close"

    def test_module_has_logger(self) -> None:
        """Verify module-level logger is defined."""
        assert hasattr(recorder_consumer_lite, "logger")
        assert isinstance(recorder_consumer_lite.logger, logging.Logger)

    def test_no_bare_except_in_wait_for_obs_websocket(self) -> None:
        """Verify no bare except Exception: in _wait_for_obs_websocket."""
        import ast
        import inspect

        source = inspect.getsource(recorder_consumer_lite._wait_for_obs_websocket)
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                # Bare except or except Exception without binding
                if node.type is None:
                    pytest.fail("Found bare except in _wait_for_obs_websocket")
                if isinstance(node.type, ast.Name) and node.type.id == "Exception":
                    if node.name is None:
                        pytest.fail("Found bare except Exception in _wait_for_obs_websocket")
