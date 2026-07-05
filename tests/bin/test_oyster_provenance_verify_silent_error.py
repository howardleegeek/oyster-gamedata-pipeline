"""
Regression test: oyster_provenance/verify.py silent error surfacing.

This test verifies that bare `except Exception:` blocks in
verify.py have been replaced with a bound exception and
debug logging so silent failures are observable.

Touched sites:
  - verify_anchor()        : L200-ish, datetime.fromisoformat + get_week_range
  - verify_session()       : L344-ish, load_manifest in verbose section
"""

import ast
import logging
import sys
from pathlib import Path

# Ensure oyster_provenance is in path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from oyster_provenance import verify  # noqa: E402

SOURCE_FILE = Path(__file__).parent.parent.parent / "oyster_provenance" / "verify.py"


def _read_source() -> str:
    with open(SOURCE_FILE, "r") as f:
        return f.read()


def _function_bare_excepts(tree, func_name):
    """Return list of (lineno, label) for any bare/unbound except in func."""
    issues = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            for stmt in ast.walk(node):
                if isinstance(stmt, ast.Try):
                    for handler in stmt.handlers:
                        if handler.type is None:
                            issues.append((handler.lineno, "bare except (no type)"))
                        elif (
                            isinstance(handler.type, ast.Name)
                            and handler.type.id == "Exception"
                            and handler.name is None
                        ):
                            issues.append(
                                (handler.lineno, "`except Exception:` without binding")
                            )
            return issues
    raise AssertionError(f"{func_name} not found in verify.py")


class TestVerifySilentErrorSurfacing:
    """Tests for silent error surfacing in oyster_provenance/verify.py."""

    def test_logger_imported(self):
        """Module must define a module-level logger via logging.getLogger(__name__)."""
        source = _read_source()
        assert "import logging" in source, "verify.py must import logging"
        assert "logger = logging.getLogger(__name__)" in source, (
            "verify.py must define module-level logger"
        )

    def test_no_bare_except_in_verify_anchor(self):
        """verify_anchor must not have a bare except Exception:."""
        source = _read_source()
        tree = ast.parse(source)
        issues = _function_bare_excepts(tree, "verify_anchor")
        assert not issues, (
            f"verify_anchor has bare/unbound except(s): {issues} — must bind to `e` "
            f"and log via logger.debug()"
        )

    def test_no_bare_except_in_print_verification_result(self):
        """print_verification_result must not have a bare except Exception:."""
        source = _read_source()
        tree = ast.parse(source)
        issues = _function_bare_excepts(tree, "print_verification_result")
        assert not issues, (
            f"print_verification_result has bare/unbound except(s): {issues} — must bind to `e` "
            f"and log via logger.debug()"
        )

    def test_logger_debug_in_verify_anchor(self):
        """verify_anchor must log via logger.debug() inside its except block."""
        source = _read_source()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "verify_anchor":
                for stmt in ast.walk(node):
                    if isinstance(stmt, ast.Try):
                        for handler in stmt.handlers:
                            # The handler should reference logger.debug
                            src_segment = ast.get_source_segment(source, handler) or ""
                            assert "logger.debug" in src_segment, (
                                f"verify_anchor except at L{handler.lineno} "
                                f"must call logger.debug()"
                            )
                return
        raise AssertionError("verify_anchor not found")

    def test_logger_debug_in_print_verification_result_verbose_except(self):
        """print_verification_result's verbose except block must log via logger.debug()."""
        source = _read_source()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "print_verification_result":
                # Walk into the `if verbose:` If block to find the try
                for child in ast.walk(node):
                    if isinstance(child, ast.If):
                        for sub in ast.walk(child):
                            if isinstance(sub, ast.Try):
                                for handler in sub.handlers:
                                    hs = ast.get_source_segment(source, handler) or ""
                                    assert "logger.debug" in hs, (
                                        f"print_verification_result verbose except "
                                        f"at L{handler.lineno} must call logger.debug()"
                                    )
                                return
        raise AssertionError("print_verification_result verbose try block not found")

    def test_module_compiles(self):
        """Sanity check: verify.py must compile without syntax errors."""
        source = _read_source()
        compile(source, str(SOURCE_FILE), "exec")

    def test_logger_is_module_logger(self):
        """Module-level `logger` must come from logging.getLogger(__name__)."""
        assert hasattr(verify, "logger"), "verify module must expose module-level logger"
        assert isinstance(verify.logger, logging.Logger)
        assert verify.logger.name == "oyster_provenance.verify"
