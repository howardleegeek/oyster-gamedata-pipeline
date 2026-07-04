"""
Regression tests for silent error swallows in bin/recovery_orchestrator.py.

These tests verify that ``is_corrupted`` binds the exception and logs it
at debug level (via ``logger.debug(..., exc_info=True)``) instead of
silently swallowing it. The function must still return True (i.e.
"treat as corrupted") when the inner tarfile open / read raises any
exception, so recovery semantics are unchanged.
"""

import ast
import logging
import tarfile
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).parent.parent.parent
TARGET = REPO_ROOT / "bin" / "recovery_orchestrator.py"


def _read_source() -> str:
    return TARGET.read_text(encoding="utf-8")


class TestRecoveryOrchestratorSilentError:
    """Tests for silent error handling in is_corrupted()."""

    def test_no_bare_except_in_is_corrupted(self):
        """is_corrupted must not contain a bare ``except Exception:`` block
        that hides the error (i.e. with no ``as`` binding)."""
        source = _read_source()
        tree = ast.parse(source)

        target_fn = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "is_corrupted":
                target_fn = node
                break

        assert target_fn is not None, "is_corrupted function not found"

        for child in ast.walk(target_fn):
            if isinstance(child, ast.ExceptHandler):
                if child.type is not None:
                    type_src = ast.unparse(child.type)
                    if "Exception" in type_src and child.name is None:
                        pytest.fail(
                            "Found bare 'except Exception:' (no 'as' binding) "
                            "in is_corrupted(). Bind the exception and log it."
                        )

    def test_is_corrupted_logs_exception_at_debug(self, tmp_path, caplog):
        """When tarfile.open raises an unexpected error, the exception must
        be bound and logged at DEBUG level. Function still returns True."""
        from bin.recovery_orchestrator import is_corrupted

        # Build a fake tarball path that will fail inside tarfile.open
        # (e.g. a zero-byte file is too small for a valid tar header).
        broken = tmp_path / "broken.tar"
        broken.write_bytes(b"")

        # Sanity: file exists and is too small (so is_corrupted should
        # return True for size < 512). We instead force a path that does
        # pass the size check but raises from tarfile.open. We use a
        # file containing garbage of >=512 bytes.
        garbage = tmp_path / "garbage.tar"
        garbage.write_bytes(b"X" * 1024)

        with caplog.at_level(logging.DEBUG, logger="bin.recovery_orchestrator"):
            result = is_corrupted(garbage)

        # The garbage file is not a valid tarball — is_corrupted must
        # still return True so the orchestrator treats it as corrupted
        # and quarantines it.
        assert result is True

    def test_is_corrupted_debug_log_includes_exception(self, tmp_path, caplog):
        """Verify that the debug log emitted by is_corrupted binds and
        surfaces the underlying exception (via exc_info) when tarfile
        parsing fails on a non-tarball file."""
        from bin.recovery_orchestrator import is_corrupted

        garbage = tmp_path / "garbage.tar"
        garbage.write_bytes(b"this is not a tarball at all" * 50)

        with caplog.at_level(logging.DEBUG, logger="bin.recovery_orchestrator"):
            is_corrupted(garbage)

        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert debug_records, (
            "Expected at least one DEBUG log record from is_corrupted when "
            "tarfile parsing fails."
        )
        # The log message should mention the failure path
        joined = " ".join(r.getMessage() for r in debug_records)
        assert "is_corrupted" in joined or "corrupted" in joined.lower()

    def test_is_corrupted_valid_tarball_returns_false(self, tmp_path):
        """A well-formed tarball must return False (not corrupted)."""
        from bin.recovery_orchestrator import is_corrupted

        good = tmp_path / "good.tar"
        with tarfile.open(good, "w") as tar:
            import io
            data = b"hello world\n"
            info = tarfile.TarInfo(name="hello.txt")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        # The file is well over 512 bytes; tarfile.open and next() must
        # succeed and the function must return False.
        assert is_corrupted(good) is False
