#!/usr/bin/env python3
"""
Regression tests for silent error swallows in bin/recorder_consumer_lite.py _obs_get_profile_parameter.

These tests verify that failed OBS WebSocket requests are logged at debug level
(binding the exception) rather than silently swallowed.

Round: Surface silent errors in bin/recorder_consumer_lite.py _obs_get_profile_parameter
"""

import ast
from pathlib import Path


class TestRecorderConsumerLiteProfileParameterSilentError:
    """Tests for silent error handling in _obs_get_profile_parameter()."""

    def _read_source(self) -> str:
        return (
            Path(__file__).parent.parent.parent
            / "bin"
            / "recorder_consumer_lite.py"
        ).read_text()

    def test_no_bare_except_in_get_profile_parameter(self):
        """No bare ``except Exception:`` (without ``as`` binding) in _obs_get_profile_parameter."""
        source = self._read_source()
        tree = ast.parse(source)

        # Find the _obs_get_profile_parameter function
        found_function = False
        bare_excepts = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_obs_get_profile_parameter":
                found_function = True
                # Check only this function's body
                for subnode in ast.walk(node):
                    if isinstance(subnode, ast.Try):
                        for handler in subnode.handlers:
                            if handler.type is not None:
                                type_src = ast.unparse(handler.type)
                                if "Exception" in type_src and handler.name is None:
                                    bare_excepts.append(handler.lineno)

        assert found_function, "_obs_get_profile_parameter function not found"
        assert len(bare_excepts) == 0, (
            f"Found {len(bare_excepts)} bare 'except Exception:' "
            f"(no 'as' binding) at lines {bare_excepts} in _obs_get_profile_parameter. "
            f"Bind the exception and log it via logger.debug(...)."
        )

    def test_logger_imported(self):
        """A module-level logger must be defined so the exception can be logged."""
        source = self._read_source()
        # Check that logging is imported
        assert "import logging" in source
        # Check that logger is defined
        assert "logger = logging.getLogger" in source

    def test_get_profile_parameter_logs_request_failure(self):
        """When client.request() fails, the exception should be logged."""
        source = self._read_source()
        # Check that there's a logger.debug call for request failure
        assert "logger.debug" in source, "logger.debug must be used to log exceptions"
        # Check that the debug log mentions the function context
        assert "GetProfileParameter" in source, (
            "logger.debug should mention GetProfileParameter for context"
        )

    def test_module_compiles(self):
        """The module must compile without syntax errors."""
        source = self._read_source()
        # This will raise SyntaxError if the file is invalid
        compile(source, "recorder_consumer_lite.py", "exec")
