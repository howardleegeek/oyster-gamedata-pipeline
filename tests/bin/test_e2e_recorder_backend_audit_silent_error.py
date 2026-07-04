"""
Regression tests for silent error swallows in bin/e2e_recorder_backend_audit.py.

These tests verify that exception handlers in the backend health/sessions
probes, gate JSON parsing, and shutdown cleanup all bind the exception
and emit a debug/warning log rather than silently swallowing it.
"""

import ast
from pathlib import Path

import pytest


class TestE2ERecorderBackendAuditSilentError:
    """Tests for silent error handling in e2e_recorder_backend_audit.py."""

    def _read_source(self) -> str:
        return (
            Path(__file__).parent.parent.parent
            / "bin"
            / "e2e_recorder_backend_audit.py"
        ).read_text()

    def test_no_bare_except_with_exception_binding(self):
        """All ``except Exception:`` must bind the exception (as e: / as exc:)."""
        source = self._read_source()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                for handler in node.handlers:
                    if handler.type is not None:
                        type_src = ast.unparse(handler.type)
                        if "Exception" in type_src and handler.name is None:
                            pytest.fail(
                                f"Found bare 'except Exception:' "
                                f"(no 'as' binding) at line {handler.lineno}. "
                                f"Bind the exception and log it."
                            )

    def test_logger_imported(self):
        """A module-level logger must be defined so exceptions can be logged."""
        source = self._read_source()
        assert "import logging" in source
        assert "logger = logging.getLogger" in source

    def test_healthz_probe_failure_logs_at_debug(self):
        """When the /v1/health probe raises, the exception should be logged at DEBUG."""
        source = self._read_source()
        # The retry-loop healthz probe must log at debug
        assert "Backend healthz probe failed" in source, (
            "healthz retry-loop handler should log at debug level"
        )

    def test_session_count_failure_logs_at_debug(self):
        """When counting sessions raises, the exception should be logged at DEBUG."""
        source = self._read_source()
        assert "Failed to count backend sessions" in source, (
            "session-count handler should log at debug level"
        )

    def test_gate_json_parse_failure_logs_at_debug(self):
        """When gate smoke stdout is not valid JSON, log at DEBUG."""
        source = self._read_source()
        assert "Failed to parse gate smoke stdout" in source, (
            "gate JSON parse handler should log at debug level"
        )

    def test_shutdown_kill_failure_logs_at_debug(self):
        """The final SIGKILL cleanup in shutdown should also log on failure."""
        source = self._read_source()
        assert "Backend SIGKILL cleanup failed" in source, (
            "shutdown kill cleanup should log at debug level"
        )

    def test_module_compiles(self):
        """Sanity check: the source must parse as valid Python."""
        source = self._read_source()
        ast.parse(source)
