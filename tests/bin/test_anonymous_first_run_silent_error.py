"""
Regression tests for silent error handling in bin/anonymous_first_run.py.

These tests verify that the ``_write_json`` exception handler binds the
exception and surfaces it via the module logger, so that the underlying
write/serialize failure (and any secondary unlink failure) is observable
in DEBUG logs rather than being silently dropped.
"""

import ast
from pathlib import Path

import pytest


class TestAnonymousFirstRunSilentError:
    """Tests for silent error handling in anonymous_first_run.py."""

    def _read_source(self) -> str:
        return (
            Path(__file__).parent.parent.parent
            / "bin"
            / "anonymous_first_run.py"
        ).read_text()

    def test_no_bare_except_with_no_binding(self):
        """All except handlers that catch Exception/BaseException must bind the exception."""
        source = self._read_source()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                for handler in node.handlers:
                    if handler.type is not None:
                        type_src = ast.unparse(handler.type)
                        if (
                            "Exception" in type_src or "BaseException" in type_src
                        ) and handler.name is None:
                            pytest.fail(
                                f"Found bare 'except {type_src}:' (no 'as' binding) "
                                f"at line {handler.lineno}. Bind the exception and log it."
                            )

    def test_logger_imported(self):
        """A module-level logger must be defined so the exception can be logged."""
        source = self._read_source()
        assert "import logging" in source
        assert "logger = logging.getLogger" in source

    def test_write_json_handler_binds_exception(self):
        """The _write_json except handler must bind the exception as 'exc'."""
        source = self._read_source()
        assert "except BaseException as exc:" in source, (
            "_write_json must bind BaseException as 'exc' to surface the failure"
        )

    def test_write_json_handler_logs_unlink_failure(self):
        """The unlink failure inside _write_json must be logged at DEBUG."""
        source = self._read_source()
        # Check that there's a logger.debug call referencing tmp / unlink
        assert "logger.debug" in source, (
            "logger.debug should be used to log the unlink failure inside _write_json"
        )
        # Check that the debug log mentions tmp_name and the exception variable
        assert "tmp_name" in source and "unlink_exc" in source, (
            "logger.debug should include tmp_name and the unlink exception"
        )

    def test_module_compiles(self):
        """The module must compile (py_compile)."""
        import py_compile

        path = (
            Path(__file__).parent.parent.parent
            / "bin"
            / "anonymous_first_run.py"
        )
        py_compile.compile(str(path), doraise=True)
