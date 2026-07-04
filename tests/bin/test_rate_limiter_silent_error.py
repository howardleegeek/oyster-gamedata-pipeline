"""
Regression tests for silent error swallows in bin/rate_limiter.py.

These tests verify that corrupt state-file loads are logged at debug
level rather than silently swallowed.
"""

import ast
import logging
import sys
from pathlib import Path

import pytest


class TestRateLimiterSilentError:
    """Tests for silent error handling in rate_limiter.py."""

    def test_no_bare_pass_in_load_state(self):
        """Verify _load_state doesn't have bare 'pass' in json/key error handler."""
        source_path = (
            Path(__file__).parent.parent.parent / "bin" / "rate_limiter.py"
        )
        source = source_path.read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == "_load_state"
            ):
                for child in ast.walk(node):
                    if isinstance(child, ast.ExceptHandler):
                        if len(child.body) == 1 and isinstance(
                            child.body[0], ast.Pass
                        ):
                            pytest.fail(
                                "Found bare pass in except handler for "
                                f"{ast.unparse(child.type)}. Should use "
                                "logger.debug() to bind the exception."
                            )

    def test_corrupt_state_file_logs_at_debug(self, tmp_path, caplog):
        """Verify a corrupt state file produces a debug log binding the exception."""
        # Ensure clean import
        sys.modules.pop("bin.rate_limiter", None)

        # Write a deliberately invalid JSON state file
        state_file = tmp_path / "rate_limiter_state.json"
        state_file.write_text("{ this is not valid json")

        from bin.rate_limiter import VendorRateLimiter

        with caplog.at_level(logging.DEBUG, logger="bin.rate_limiter"):
            lim = VendorRateLimiter(state_file=state_file, default_budget=10)

        # VendorRateLimiter construction must complete (no exception) and
        # the corrupt file must be surfaced via a debug log.
        assert lim is not None
        debug_msgs = [
            r.getMessage()
            for r in caplog.records
            if r.levelno == logging.DEBUG
        ]
        assert any("corrupt state file" in m for m in debug_msgs), (
            f"Expected a debug log mentioning 'corrupt state file', got: {debug_msgs}"
        )

        # Control flow: must still be usable after a corrupt file load.
        bucket = lim.get_bucket("vendor_x")
        assert bucket.capacity == 10
