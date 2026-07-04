"""
Regression tests for silent error swallow in bin/dr_failover_runbook_check.py.

These tests verify that ``DRFailoverValidator._parse_url`` binds the
exception and logs it at debug level via ``logger.debug(..., exc_info=True)``
instead of silently swallowing it. The function must still return ``None``
(URL treated as invalid) when the inner ``urlparse`` call raises any
exception, so ``_check_endpoint`` semantics are unchanged.
"""

import ast
import logging
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).parent.parent.parent
TARGET = REPO_ROOT / "bin" / "dr_failover_runbook_check.py"


def _read_source() -> str:
    return TARGET.read_text(encoding="utf-8")


class TestDRFailoverRunbookCheckSilentError:
    """Tests for silent error handling in _parse_url()."""

    def test_no_bare_except_in_parse_url(self):
        """_parse_url must not contain a bare ``except Exception:`` block
        that hides the error (i.e. with no ``as`` binding)."""
        source = _read_source()
        tree = ast.parse(source)

        target_fn = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_parse_url":
                target_fn = node
                break

        assert target_fn is not None, "_parse_url function not found"

        for child in ast.walk(target_fn):
            if isinstance(child, ast.ExceptHandler):
                if child.type is not None:
                    type_src = ast.unparse(child.type)
                    if "Exception" in type_src and child.name is None:
                        pytest.fail(
                            "Found bare 'except Exception:' (no 'as' binding) "
                            "in _parse_url(). Bind the exception and log it."
                        )

    def test_parse_url_binds_exception_and_logs_debug(self, monkeypatch, caplog):
        """When urlparse raises an unexpected error, the exception must
        be bound and logged at DEBUG level. Function still returns None."""
        from bin.dr_failover_runbook_check import DRFailoverValidator

        def _raise_value_error(_url):
            raise ValueError("synthetic urlparse failure")

        monkeypatch.setattr(
            "bin.dr_failover_runbook_check.urlparse", _raise_value_error,
        )

        v = DRFailoverValidator()
        with caplog.at_level(
            logging.DEBUG, logger="bin.dr_failover_runbook_check",
        ):
            result = v._parse_url("https://example.com")

        # Control flow preserved: still returns None so _check_endpoint
        # reports "Invalid URL".
        assert result is None
        # Silent-swallow fixed: at least one DEBUG log binding the exception.
        debug_records = [
            r for r in caplog.records if r.levelno == logging.DEBUG
        ]
        assert any(
            "synthetic urlparse failure" in r.getMessage() for r in debug_records
        ), f"Expected DEBUG log to bind the exception, got: {[r.getMessage() for r in debug_records]}"

    def test_parse_url_happy_path_unaffected(self):
        """Valid URL still returns the expected component dict — no
        regression on the happy path."""
        from bin.dr_failover_runbook_check import DRFailoverValidator

        v = DRFailoverValidator()
        result = v._parse_url("https://example.com/foo")
        assert result == {
            "host": "example.com",
            "port": 443,
            "scheme": "https",
        }

    def test_parse_url_no_hostname_still_returns_none(self):
        """Edge case where urlparse succeeds but hostname is empty
        (e.g. unix socket URL 'http://') still returns None without
        invoking the except path."""
        from bin.dr_failover_runbook_check import DRFailoverValidator

        v = DRFailoverValidator()
        # urlparse('http://') succeeds but hostname is None — must not raise.
        assert v._parse_url("http://") is None
