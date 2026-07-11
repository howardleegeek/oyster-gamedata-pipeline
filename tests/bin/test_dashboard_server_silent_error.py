"""
Regression tests for silent error swallows in dashboard/server.py.

These tests verify that error conditions are logged rather than silently swallowed.
"""

import ast
from pathlib import Path

import pytest


class TestDashboardServerSilentError:
    """Tests for silent error handling in dashboard/server.py."""

    def test_no_bare_except_pass_in_verify_session_provenance(self):
        """Verify verify_session_provenance doesn't have bare 'pass' in Exception handler."""
        source_path = Path(__file__).parent.parent.parent / "dashboard" / "server.py"
        source = source_path.read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "verify_session_provenance":
                for child in ast.walk(node):
                    if isinstance(child, ast.ExceptHandler):
                        # Check for bare pass: except ...: pass (single Pass statement)
                        if len(child.body) == 1 and isinstance(child.body[0], ast.Pass):
                            exc_type = ast.unparse(child.type) if child.type else "Exception"
                            pytest.fail(
                                f"Found bare pass in {exc_type} handler in "
                                f"verify_session_provenance. "
                                "Should use logger.debug() to bind the exception."
                            )

    def test_exception_handler_binds_name_and_logs(self):
        """Verify Exception handler in verify_session_provenance binds exception and logs."""
        source_path = Path(__file__).parent.parent.parent / "dashboard" / "server.py"
        source = source_path.read_text()
        tree = ast.parse(source)

        found_logging_handler = False

        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "verify_session_provenance":
                for child in ast.walk(node):
                    if isinstance(child, ast.ExceptHandler):
                        # Check if exception is bound to a name (e.g., "as exc")
                        if child.name is None:
                            continue
                        # Check if logger.debug is called in the handler
                        for stmt in child.body:
                            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                                if isinstance(stmt.value.func, ast.Attribute):
                                    if stmt.value.func.attr == "debug":
                                        found_logging_handler = True
                                        break

        assert found_logging_handler, (
            "Expected verify_session_provenance to have an Exception handler "
            "that binds the exception and calls logger.debug"
        )

    def test_module_has_logging_import(self):
        """Verify dashboard/server.py imports logging."""
        source_path = Path(__file__).parent.parent.parent / "dashboard" / "server.py"
        source = source_path.read_text()
        tree = ast.parse(source)

        has_logging_import = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "logging":
                        has_logging_import = True
                        break

        assert has_logging_import, "dashboard/server.py should import logging module"

    def test_module_has_logger_definition(self):
        """Verify dashboard/server.py defines a logger."""
        source_path = Path(__file__).parent.parent.parent / "dashboard" / "server.py"
        source = source_path.read_text()
        tree = ast.parse(source)

        has_logger_definition = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "logger":
                        has_logger_definition = True
                        break

        assert has_logger_definition, "dashboard/server.py should define a logger"
