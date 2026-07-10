"""
Regression tests for silent error swallows in bin/sprint_dashboard.py.

These tests verify that failed operations are logged at debug level
(binding the exception) rather than silently swallowed.
"""

import ast
from pathlib import Path


class TestSprintDashboardSilentError:
    """Tests for silent error handling in sprint_dashboard.py."""

    def _read_source(self) -> str:
        return (
            Path(__file__).parent.parent.parent
            / "bin"
            / "sprint_dashboard.py"
        ).read_text()

    def test_no_bare_except(self):
        """No bare ``except Exception:`` (without ``as`` binding) should exist."""
        source = self._read_source()
        tree = ast.parse(source)

        # Find all Try nodes with except handlers
        bare_excepts = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                for handler in node.handlers:
                    if handler.type is not None:
                        type_src = ast.unparse(handler.type)
                        if "Exception" in type_src and handler.name is None:
                            bare_excepts.append(handler.lineno)

        assert len(bare_excepts) == 0, (
            f"Found {len(bare_excepts)} bare 'except Exception:' "
            f"(no 'as' binding) at lines {bare_excepts}. "
            f"Bind the exception and log it via logger.debug(...)."
        )

    def test_logger_imported(self):
        """A module-level logger must be defined so the exception can be logged."""
        source = self._read_source()
        # Check that logging is imported
        assert "import logging" in source
        # Check that logger is defined
        assert "logger = logging.getLogger" in source

    def test_pytest_output_failure_logs_at_debug(self):
        """When pytest output parsing fails, the exception should be logged."""
        source = self._read_source()
        # Check that there's a logger.debug call for pytest output failure
        assert "logger.debug" in source, (
            "logger.debug should be used to log pytest output parsing failure"
        )
        # Check that the exception is bound
        assert "except Exception as e:" in source, (
            "Exception should be bound as 'e' to include in log message"
        )

    def test_module_compiles(self):
        """The module should compile without syntax errors."""
        source = self._read_source()
        # This will raise SyntaxError if invalid
        ast.parse(source)
