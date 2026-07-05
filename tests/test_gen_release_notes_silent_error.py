#!/usr/bin/env python3
"""Regression tests: scripts/gen_release_notes.py should not silently swallow exceptions.

Both `_pr_url()` (git remote probe fallback) and `_find_last_tag()` (git tag probe
fallback) wrap git subprocess calls in `try/except Exception: pass`. If the
git probe fails (missing remote, no tags, non-zero exit, etc.) the function
silently returns a placeholder. The original exception context is lost,
which makes "why is the PR URL wrong / why is the diff base wrong" nearly
impossible to debug from release-notes output alone.

These tests pin the contract:
  1. No bare `except Exception:` blocks remain (must bind `as e`).
  2. Module-level logger `_LOG` is defined via `logging.getLogger(__name__)`.
  3. Both swallow-sites log at DEBUG with the bound exception.
  4. Control flow is preserved: both still return the placeholder/HEAD.
  5. Module compiles cleanly.
"""
import ast
import logging
import py_compile
import subprocess
from pathlib import Path

import pytest

# Ensure the scripts/ package is importable when running this test from
# the repo root (matches how tests/test_gen_release_notes.py imports it).
import sys
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.gen_release_notes import _find_last_tag, _pr_url  # noqa: E402


_SRC_PATH = Path("scripts/gen_release_notes.py")


# Test 1: No bare except blocks
def test_no_bare_except_in_gen_release_notes():
    """Module must not contain bare 'except Exception:' without binding 'as e'."""
    src = _SRC_PATH.read_text()
    tree = ast.parse(src)

    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            # Bare except (no type at all) or `except Exception:` without `as e`
            if node.type is None:
                violations.append((node.lineno, "bare 'except:'"))
                continue
            if (
                isinstance(node.type, ast.Name)
                and node.type.id == "Exception"
                and node.name is None
            ):
                violations.append((node.lineno, "'except Exception:' without binding"))

    assert not violations, (
        f"Found {len(violations)} bare except block(s) in gen_release_notes.py: "
        f"{violations}. These silently swallow errors and must bind the exception "
        f"and log it (see other silent-error regression tests for the pattern)."
    )


# Test 2: Module-level logger is defined
def test_module_logger_defined():
    """Module must define a module-level logger for DEBUG-level surfacing."""
    src = _SRC_PATH.read_text()
    assert "import logging" in src, "logging module must be imported"
    assert "_LOG = logging.getLogger(__name__)" in src, (
        "module-level _LOG = logging.getLogger(__name__) must be defined"
    )


# Test 3: _pr_url logs at DEBUG on exception
def test_pr_url_logs_on_exception():
    """_pr_url() must log the bound exception when git remote probe fails."""
    src = _SRC_PATH.read_text()
    assert "except Exception as e:" in src, "Exception must be bound as 'e' in _pr_url"

    # Find the body of _pr_url and confirm it calls _LOG.debug inside the except.
    tree = ast.parse(src)
    fn = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_pr_url"),
        None,
    )
    assert fn is not None, "_pr_url() not found"

    found_debug_in_except = False
    for node in ast.walk(fn):
        if isinstance(node, ast.ExceptHandler) and node.name == "e":
            for sub in ast.walk(node):
                if (
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Attribute)
                    and isinstance(sub.func.value, ast.Name)
                    and sub.func.value.id == "_LOG"
                    and sub.func.attr == "debug"
                ):
                    found_debug_in_except = True
                    break
    assert found_debug_in_except, (
        "_pr_url() exception handler must call _LOG.debug(...) with the bound exception"
    )


# Test 4: _find_last_tag logs at DEBUG on exception
def test_find_last_tag_logs_on_exception():
    """_find_last_tag() must log the bound exception when git describe fails."""
    src = _SRC_PATH.read_text()
    # _find_last_tag is at the bottom of the file
    tree = ast.parse(src)
    fn = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_find_last_tag"),
        None,
    )
    assert fn is not None, "_find_last_tag() not found"

    found_debug_in_except = False
    for node in ast.walk(fn):
        if isinstance(node, ast.ExceptHandler) and node.name == "e":
            for sub in ast.walk(node):
                if (
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Attribute)
                    and isinstance(sub.func.value, ast.Name)
                    and sub.func.value.id == "_LOG"
                    and sub.func.attr == "debug"
                ):
                    found_debug_in_except = True
                    break
    assert found_debug_in_except, (
        "_find_last_tag() exception handler must call _LOG.debug(...) with the bound exception"
    )


# Test 5: Control flow preserved — _pr_url still returns placeholder on subprocess failure
def test_pr_url_returns_placeholder_on_failure(caplog):
    """When `git remote get-url origin` raises, _pr_url() must still return the
    placeholder URL (control flow preserved) AND log the failure at DEBUG."""
    with caplog.at_level(logging.DEBUG, logger="scripts.gen_release_notes"):

        def _boom(*args, **kwargs):
            raise subprocess.CalledProcessError(128, args[0] if args else "git")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("scripts.gen_release_notes._run", _boom)
            result = _pr_url(42)

    assert result == "https://github.com/OWNER/REPO/pull/42", (
        f"control flow regression: _pr_url() should return the placeholder when "
        f"the git remote probe fails, got {result!r}"
    )
    # And the exception must have been logged at DEBUG with the PR number for context
    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("42" in r.getMessage() for r in debug_records), (
        f"expected a DEBUG log record mentioning PR #42, got: "
        f"{[r.getMessage() for r in debug_records]}"
    )


# Test 6: Control flow preserved — _find_last_tag still returns "HEAD" on subprocess failure
def test_find_last_tag_returns_head_on_failure(caplog):
    """When `git describe --tags --abbrev=0` raises, _find_last_tag() must still
    return "HEAD" (control flow preserved) AND log the failure at DEBUG."""
    with caplog.at_level(logging.DEBUG, logger="scripts.gen_release_notes"):

        def _boom(*args, **kwargs):
            raise subprocess.CalledProcessError(128, args[0] if args else "git")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("scripts.gen_release_notes._run", _boom)
            result = _find_last_tag()

    assert result == "HEAD", (
        f"control flow regression: _find_last_tag() should return 'HEAD' when "
        f"the git describe call fails, got {result!r}"
    )
    # And the failure must have been logged at DEBUG
    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("last git tag" in r.getMessage() for r in debug_records), (
        f"expected a DEBUG log record about last git tag failure, got: "
        f"{[r.getMessage() for r in debug_records]}"
    )


# Test 7: Module compiles
def test_module_compiles():
    """Module must compile without errors."""
    py_compile.compile(str(_SRC_PATH), doraise=True)
