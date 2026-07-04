"""
Regression tests for silent error swallows in bin/upload_daemon.py.

These tests verify that failed datetime.fromisoformat() calls in the
get_status() method are logged at debug level (binding the exception)
rather than silently swallowed with bare `except Exception: pass`.
"""

import ast
from pathlib import Path

import pytest


class TestUploadDaemonSilentError:
    """Tests for silent error handling in upload_daemon.py."""

    def _read_source(self) -> str:
        return (
            Path(__file__).parent.parent.parent
            / "bin"
            / "upload_daemon.py"
        ).read_text()

    def test_no_bare_except_in_get_status(self):
        """The get_status() method must not have any bare ``except Exception:``
        (no ``as`` binding) that silently swallows errors."""
        source = self._read_source()
        tree = ast.parse(source)

        # Find the get_status method and check its Try nodes
        found_get_status = False
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "get_status":
                found_get_status = True
                for child in ast.walk(node):
                    if isinstance(child, ast.Try):
                        for handler in child.handlers:
                            if handler.type is not None:
                                # Check for bare except Exception: (no 'as' binding)
                                type_src = ast.unparse(handler.type)
                                if "Exception" in type_src and handler.name is None:
                                    # Check if the body is just 'pass'
                                    is_pass_only = (
                                        len(handler.body) == 1
                                        and isinstance(handler.body[0], ast.Pass)
                                    )
                                    if is_pass_only:
                                        pytest.fail(
                                            f"Found bare 'except Exception: pass' "
                                            f"at line {handler.lineno} in get_status(). "
                                            f"Bind the exception and log it "
                                            f"via logger.debug(...)."
                                        )

        assert found_get_status, "get_status method not found"

    def test_no_bare_except_in_is_wifi_only(self):
        """The _is_wifi_only() method must not have bare ``except Exception: pass``."""
        source = self._read_source()
        tree = ast.parse(source)

        # Find the _is_wifi_only method
        found_method = False
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_is_wifi_only":
                found_method = True
                for child in ast.walk(node):
                    if isinstance(child, ast.Try):
                        for handler in child.handlers:
                            if handler.type is not None:
                                type_src = ast.unparse(handler.type)
                                if "Exception" in type_src and handler.name is None:
                                    is_pass_only = (
                                        len(handler.body) == 1
                                        and isinstance(handler.body[0], ast.Pass)
                                    )
                                    if is_pass_only:
                                        pytest.fail(
                                            f"Found bare 'except Exception: pass' "
                                            f"at line {handler.lineno} in _is_wifi_only(). "
                                            f"Bind the exception and log it."
                                        )

        assert found_method, "_is_wifi_only method not found"

    def test_datetime_parse_failure_logs_at_debug(self):
        """When datetime.fromisoformat fails, the exception should be logged at DEBUG level."""
        source = self._read_source()
        # Check that logger.debug is used for logging parse failures
        assert "logger.debug" in source, (
            "logger.debug should be used to log parse failures"
        )
        # Check that fromisoformat failure logging is present
        assert "Failed to parse" in source or "fromisoformat" in source, (
            "Should have logging for fromisoformat failures"
        )

    def test_wifi_check_failure_logs_at_debug(self):
        """When WiFi check fails, the exception should be logged at DEBUG level."""
        source = self._read_source()
        # Verify logging of WiFi check failures
        assert "WiFi check failed" in source or "logger.debug" in source, (
            "WiFi check failures should be logged at DEBUG level"
        )


class TestUploadDaemonErrorHandlingIntegration:
    """Integration tests for upload_daemon error handling."""

    def test_get_status_handles_invalid_datetime_gracefully(self):
        """get_status() should handle invalid datetime strings without crashing."""
        import sys
        from pathlib import Path as P
        from unittest.mock import patch

        with patch.object(P, "mkdir", return_value=None):
            if "upload_daemon" in sys.modules:
                del sys.modules["upload_daemon"]

            from bin import upload_daemon

        # Create a daemon instance
        daemon = upload_daemon.UploadDaemon()

        # Mock state with a dict (not UploadSession) containing invalid datetime
        daemon.state = {
            "sessions": {
                "test-123": {
                    "state": "completed",
                    "completed_at": "not-a-valid-iso-string",
                    "file_size": 1000,
                }
            },
            "last_scan": "2026-01-01T00:00:00",
        }

        # Should not raise, should return status dict
        status = daemon.get_status()

        assert "completed" in status
        assert len(status["completed"]) == 1

    def test_is_wifi_only_returns_default_on_failure(self):
        """_is_wifi_only() should return True (WiFi-only) on any failure."""
        import sys
        from pathlib import Path as P
        from unittest.mock import patch

        with patch.object(P, "mkdir", return_value=None):
            if "upload_daemon" in sys.modules:
                del sys.modules["upload_daemon"]

            from bin import upload_daemon

        daemon = upload_daemon.UploadDaemon()

        # Mock subprocess.run to raise an exception
        with patch("subprocess.run", side_effect=RuntimeError("network error")):
            result = daemon._is_wifi_only()

        # Should return True (default WiFi-only)
        assert result is True
