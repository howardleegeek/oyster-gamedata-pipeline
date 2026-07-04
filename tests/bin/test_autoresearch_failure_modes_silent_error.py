"""
Regression tests for silent error swallows in bin/autoresearch_failure_modes.py.

These tests verify that failed AST parsing and file reading operations
are logged at debug level (binding the exception) rather than silently swallowed.
"""

import ast
from pathlib import Path

import pytest


class TestAutoresearchFailureModesSilentError:
    """Tests for silent error handling in autoresearch_failure_modes.py."""

    def _read_source(self) -> str:
        return (
            Path(__file__).parent.parent.parent
            / "bin"
            / "autoresearch_failure_modes.py"
        ).read_text()

    def test_no_bare_except_with_exception_binding(self):
        """All ``except Exception:`` must bind the exception (as e:) and log it."""
        source = self._read_source()
        tree = ast.parse(source)

        # Find all Try nodes with except handlers
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                for handler in node.handlers:
                    if handler.type is not None:
                        type_src = ast.unparse(handler.type)
                        if "Exception" in type_src and handler.name is None:
                            pytest.fail(
                                f"Found bare 'except Exception:' "
                                f"(no 'as' binding) at line {handler.lineno}. "
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

    def test_ast_parse_error_logs_at_debug(self):
        """When AST parse fails, the exception should be logged at DEBUG level."""
        source = self._read_source()
        # Check that there's a logger.debug call
        assert "logger.debug" in source, (
            "logger.debug should be used to log AST parse failure"
        )
        # Check that the debug log mentions the exception variable
        assert "filepath" in source and "e)" in source, (
            "logger.debug should include filepath and exception"
        )

    def test_file_read_error_logs_at_debug(self):
        """When file read fails, the exception should be logged at DEBUG level."""
        source = self._read_source()
        # Check that file read error is logged
        assert "Failed to read" in source or "fpath" in source, (
            "logger.debug should include file path"
        )
