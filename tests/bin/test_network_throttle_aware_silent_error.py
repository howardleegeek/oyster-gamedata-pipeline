"""
Regression tests for silent error swallows in bin/network_throttle_aware.py.

These tests verify that failed network detection calls are logged at debug
level (binding the exception) rather than silently swallowed. The module
must still return appropriate fallback values (e.g., NetworkType.UNKNOWN).
"""

import ast
from pathlib import Path

import pytest


class TestNetworkThrottleAwareSilentError:
    """Tests for silent error handling in network_throttle_aware.py."""

    def _read_source(self) -> str:
        return (
            Path(__file__).parent.parent.parent
            / "bin"
            / "network_throttle_aware.py"
        ).read_text()

    def test_no_bare_except_in_load_config(self):
        """The _load_config method must not have a bare ``except Exception:``
        (no ``as`` binding) that hides the error."""
        source = self._read_source()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == "_load_config"
            ):
                for child in ast.walk(node):
                    if isinstance(child, ast.ExceptHandler):
                        if child.type is not None:
                            type_src = ast.unparse(child.type)
                            if "Exception" in type_src and child.name is None:
                                pytest.fail(
                                    "Found bare 'except Exception:' "
                                    "(no 'as' binding) in "
                                    "_load_config. "
                                    "Bind the exception and log it "
                                    "via logger.debug(...)."
                                )

    def test_no_bare_except_in_detect_windows(self):
        """The _detect_windows method must not have a bare ``except Exception:``
        (no ``as`` binding) that hides the error."""
        source = self._read_source()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == "_detect_windows"
            ):
                for child in ast.walk(node):
                    if isinstance(child, ast.ExceptHandler):
                        if child.type is not None:
                            type_src = ast.unparse(child.type)
                            if "Exception" in type_src and child.name is None:
                                pytest.fail(
                                    "Found bare 'except Exception:' "
                                    "(no 'as' binding) in "
                                    "_detect_windows. "
                                    "Bind the exception and log it "
                                    "via logger.debug(...)."
                                )

    def test_no_bare_except_in_detect_macos(self):
        """The _detect_macos method must not have a bare ``except Exception:``
        (no ``as' binding) that hides the error."""
        source = self._read_source()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == "_detect_macos"
            ):
                for child in ast.walk(node):
                    if isinstance(child, ast.ExceptHandler):
                        if child.type is not None:
                            type_src = ast.unparse(child.type)
                            if "Exception" in type_src and child.name is None:
                                pytest.fail(
                                    "Found bare 'except Exception:' "
                                    "(no 'as' binding) in "
                                    "_detect_macos. "
                                    "Bind the exception and log it "
                                    "via logger.debug(...)."
                                )

    def test_no_bare_except_in_save_config(self):
        """The _save_config method must not have a bare ``except Exception:``
        (no ``as`` binding) that hides the error."""
        source = self._read_source()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == "_save_config"
            ):
                for child in ast.walk(node):
                    if isinstance(child, ast.ExceptHandler):
                        if child.type is not None:
                            type_src = ast.unparse(child.type)
                            if "Exception" in type_src and child.name is None:
                                pytest.fail(
                                    "Found bare 'except Exception:' "
                                    "(no 'as' binding) in "
                                    "_save_config. "
                                    "Bind the exception and log it "
                                    "via logger.debug(...)."
                                )

    def test_logger_imported(self):
        """A module-level logger must be defined so the exception can be logged."""
        source = self._read_source()
        assert "import logging" in source, "logging import missing"
        assert "logger = logging.getLogger" in source, (
            "module-level logger definition missing"
        )
