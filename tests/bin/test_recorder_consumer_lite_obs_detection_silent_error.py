#!/usr/bin/env python3
"""
Regression tests for silent error swallows in bin/recorder_consumer_lite.py OBS detection.

These tests verify that failed operations in _find_bundled_obs_exe are logged at debug level
(binding the exception) rather than silently swallowed.

Round 303: Surface silent errors in bin/recorder_consumer_lite.py _find_bundled_obs_exe
"""

import ast
from pathlib import Path

import pytest


class TestRecorderConsumerLiteObsDetectionSilentError:
    """Tests for silent error handling in _find_bundled_obs_exe()."""

    def _read_source(self) -> str:
        return (
            Path(__file__).parent.parent.parent
            / "bin"
            / "recorder_consumer_lite.py"
        ).read_text()

    def test_no_bare_except_in_find_bundled_obs_exe(self):
        """No bare ``except Exception:`` (without ``as`` binding) in _find_bundled_obs_exe."""
        source = self._read_source()
        tree = ast.parse(source)

        # Find the _find_bundled_obs_exe function
        found_function = False
        bare_excepts = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_find_bundled_obs_exe":
                found_function = True
                # Check only this function's body
                for subnode in ast.walk(node):
                    if isinstance(subnode, ast.Try):
                        for handler in subnode.handlers:
                            if handler.type is not None:
                                type_src = ast.unparse(handler.type)
                                if "Exception" in type_src and handler.name is None:
                                    bare_excepts.append(handler.lineno)

        assert found_function, "_find_bundled_obs_exe function not found"
        assert len(bare_excepts) == 0, (
            f"Found {len(bare_excepts)} bare 'except Exception:' "
            f"(no 'as' binding) at lines {bare_excepts} in _find_bundled_obs_exe. "
            f"Bind the exception and log it via logger.debug(...)."
        )

    def test_logger_imported(self):
        """A module-level logger must be defined so the exception can be logged."""
        source = self._read_source()
        # Check that logging is imported
        assert "import logging" in source
        # Check that logger is defined
        assert "logger = logging.getLogger" in source

    def test_find_bundled_obs_exe_logs_exe_parent_failure(self):
        """When exe_parent resolution fails, the exception should be logged."""
        source = self._read_source()
        # Check that there's a logger.debug call for exe_parent failure
        assert "failed to resolve exe_parent" in source, (
            "logger.debug should be used to log exe_parent resolution failure"
        )

    def test_find_bundled_obs_exe_logs_root_resolve_failure(self):
        """When root.resolve() fails, the exception should be logged."""
        source = self._read_source()
        # Check that there's a logger.debug call for root resolve failure
        assert "failed to resolve root" in source, (
            "logger.debug should be used to log root resolution failure"
        )

    def test_module_compiles(self):
        """The module should compile without syntax errors."""
        source = self._read_source()
        # This will raise SyntaxError if invalid
        ast.parse(source)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
