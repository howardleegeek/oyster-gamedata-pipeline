#!/usr/bin/env python3
"""Regression test: storage_backend.py silent error surface in local backend upload idempotency check.

Verifies the `except (json.JSONDecodeError, OSError)` handler in
LocalBackend.upload is no longer a bare `pass` and now binds the exception
and logs the failure at debug level with the sidecar path context.
"""
import ast
import py_compile
import re
import unittest
from pathlib import Path

SOURCE_PATH = Path("bin/storage_backend.py")


def _read_source() -> str:
    assert SOURCE_PATH.is_file(), f"missing source: {SOURCE_PATH}"
    return SOURCE_PATH.read_text(encoding="utf-8")


class TestStorageBackendSilentError(unittest.TestCase):
    """LocalBackend.upload idempotency check should not silently swallow."""

    def test_module_compiles(self) -> None:
        """Source must compile cleanly."""
        src = _read_source()
        try:
            compile(src, str(SOURCE_PATH), "exec")
        except SyntaxError as exc:  # pragma: no cover
            self.fail(f"syntax error in {SOURCE_PATH}: {exc}")
        # And via py_compile to mirror CI
        py_compile.compile(str(SOURCE_PATH), doraise=True)

    def test_no_bare_pass_after_target_except(self) -> None:
        """The (json.JSONDecodeError, OSError) handler must not be a bare pass."""
        src = _read_source()
        # The target handler pattern: capture anything between
        # `except (json.JSONDecodeError, OSError):` and the next top-level
        # `target.parent.mkdir(...)` line.
        # Simpler: assert there is NO `pass  # corrupted sidecar` line.
        self.assertNotIn(
            "pass  # corrupted sidecar",
            src,
            "target except handler is still a bare pass — it must bind exc and log",
        )

    def test_target_except_binds_exception(self) -> None:
        """The (json.JSONDecodeError, OSError) handler must bind the exception as exc."""
        src = _read_source()
        self.assertIn(
            "except (json.JSONDecodeError, OSError) as exc:",
            src,
            "expected bound `except (json.JSONDecodeError, OSError) as exc:` in storage_backend.py",
        )

    def test_target_handler_logs_with_bound_exc(self) -> None:
        """The target handler must call logger.debug with the bound exc and meta_path context."""
        src = _read_source()
        # Look in the local upload method for the debug call
        m = re.search(
            r"except \(json\.JSONDecodeError, OSError\) as exc:\s*\n"
            r"([ \t]+logger\.debug\([^)]*\))",
            src,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(
            m,
            "expected logger.debug(...) immediately following the bound except handler",
        )
        debug_call = m.group(1)
        self.assertIn("meta_path", debug_call, "debug call should reference meta_path for context")
        self.assertIn("exc", debug_call, "debug call should reference the bound exception 'exc'")

    def test_logger_is_defined(self) -> None:
        """The module-level logger must be present (it is, and was — sanity check)."""
        src = _read_source()
        self.assertIn("logger = logging.getLogger", src, "module-level logger missing")
        self.assertIn("import logging", src, "logging import missing")

    def test_no_other_silent_passes_introduced(self) -> None:
        """No NEW bare 'except... pass' in the file (we only fix the target)."""
        src = _read_source()
        # Find every 'except ...: pass' in the file
        pattern = re.compile(r"except[^\n]*:\n[ \t]+pass[ \t]*(?:#[^\n]*)?\n")
        remaining = []
        for m in pattern.finditer(src):
            ln = src[: m.start()].count("\n") + 1
            remaining.append((ln, m.group(0).rstrip()))
        # We expect zero remaining bare 'pass' swallows in this file.
        self.assertEqual(
            remaining,
            [],
            f"found leftover bare 'pass' swallows in storage_backend.py: {remaining}",
        )

    def test_ast_handler_in_local_upload(self) -> None:
        """AST-walk confirms the bound handler exists in the source and references exc."""
        tree = ast.parse(_read_source())
        found_handlers = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                # match exactly (json.JSONDecodeError, OSError) as exc
                if node.type is None or node.name is None:
                    continue
                if node.name != "exc":
                    continue
                # Check the type is a Tuple of (json.JSONDecodeError, OSError)
                if isinstance(node.type, ast.Tuple):
                    elts = [getattr(e, "id", getattr(e, "attr", None)) for e in node.type.elts]
                    if "JSONDecodeError" in str(elts) and "OSError" in str(elts):
                        found_handlers.append(node.lineno)
        self.assertTrue(
            found_handlers,
            f"AST could not find a bound `except (json.JSONDecodeError, OSError) as exc` handler; "
            f"checked via ast.ExceptHandler walk. file={SOURCE_PATH}",
        )


if __name__ == "__main__":
    unittest.main()
