#!/usr/bin/env python3
"""
Regression tests for silent error swallows in bin/recorder_consumer_lite.py ObsWebSocketClient.connect().

These tests verify that failed OBS WebSocket connect operations are logged at debug level
(binding the exception) rather than silently swallowed.

Round: Surface silent errors in bin/recorder_consumer_lite.py ObsWebSocketClient.connect()
"""

import ast
from pathlib import Path


class TestRecorderConsumerLiteObsConnectSilentError:
    """Tests for silent error handling in ObsWebSocketClient.connect()."""

    def _read_source(self) -> str:
        return (
            Path(__file__).parent.parent.parent
            / "bin"
            / "recorder_consumer_lite.py"
        ).read_text()

    def test_no_bare_except_in_obs_connect(self):
        """No bare ``except Exception:`` (without ``as`` binding) in ObsWebSocketClient.connect."""
        source = self._read_source()
        tree = ast.parse(source)

        # Find the ObsWebSocketClient.connect method
        found_method = False
        bare_excepts = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "ObsWebSocketClient":
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and item.name == "connect":
                        found_method = True
                        # Check only this method's body
                        for subnode in ast.walk(item):
                            if isinstance(subnode, ast.Try):
                                for handler in subnode.handlers:
                                    if handler.type is not None:
                                        type_src = ast.unparse(handler.type)
                                        if "Exception" in type_src and handler.name is None:
                                            bare_excepts.append(handler.lineno)

        assert found_method, "ObsWebSocketClient.connect method not found"
        assert len(bare_excepts) == 0, (
            f"Found {len(bare_excepts)} bare 'except Exception:' "
            f"(no 'as' binding) at lines {bare_excepts} in ObsWebSocketClient.connect. "
            f"Bind the exception and log it via logger.debug(...)."
        )

    def test_logger_imported(self):
        """A module-level logger must be defined so the exception can be logged."""
        source = self._read_source()
        # Check that logging is imported
        assert "import logging" in source
        # Check that logger is defined
        assert "logger = logging.getLogger" in source

    def test_obs_connect_logs_failure(self):
        """When OBS connect fails, the exception should be logged."""
        source = self._read_source()
        # Check that there's a logger.debug call in the source
        assert "logger.debug" in source, "logger.debug must be used to log exceptions"
        # Check that the debug log mentions OBS connect context
        assert "OBS identify failed with exception" in source, (
            "logger.debug should mention OBS connect/identify failure for context"
        )

    def test_module_compiles(self):
        """The module must be syntactically valid and importable."""
        source = self._read_source()
        # Just check it parses
        ast.parse(source)
