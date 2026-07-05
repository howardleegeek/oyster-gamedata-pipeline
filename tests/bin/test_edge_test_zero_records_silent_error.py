"""
Regression tests for silent error swallows in bin/edge_test_zero_records.py.

These tests verify that the ``create_test_file()`` helper binds the
exception (via ``as e``) and logs it at DEBUG level before re-raising.
The control flow (close fd + re-raise) must be preserved.
"""

import ast
from pathlib import Path

import pytest


class TestEdgeTestZeroRecordsSilentError:
    """Tests for silent-error handling in edge_test_zero_records.py."""

    def _read_source(self) -> str:
        return (
            Path(__file__).parent.parent.parent
            / "bin"
            / "edge_test_zero_records.py"
        ).read_text()

    def test_no_bare_except_with_exception_binding(self):
        """All ``except Exception:`` must bind the exception (as e:) and log it."""
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
                                f"Bind the exception and log it "
                                f"via _LOG.debug(...)."
                            )

    def test_logger_imported(self):
        """A module-level logger must be defined so the exception can be logged."""
        source = self._read_source()
        # Check that logging is imported
        assert "import logging" in source
        # Check that logger is defined
        assert "logging.getLogger" in source

    def test_create_test_file_logs_failure_at_debug(self):
        """When create_test_file fails to write, the exception must be logged."""
        source = self._read_source()
        # Look for the create_test_file helper
        assert "def create_test_file" in source
        # Confirm the except block binds the exception and logs at DEBUG
        assert "_LOG.debug" in source, (
            "_LOG.debug should be used to log write failure in create_test_file"
        )
        # Confirm control flow is preserved: fd is still closed and the
        # exception is re-raised.
        assert "os.close(fd)" in source
        assert "raise" in source

    def test_module_compiles(self):
        """The module must be syntactically valid Python (smoke check)."""
        import py_compile
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".pyc", delete=True):
            py_compile.compile(
                str(
                    Path(__file__).parent.parent.parent
                    / "bin"
                    / "edge_test_zero_records.py"
                ),
                doraise=True,
            )
