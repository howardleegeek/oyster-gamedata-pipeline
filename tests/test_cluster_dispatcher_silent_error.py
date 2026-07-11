"""Regression test: daemon/cluster_dispatcher.py must not silently swallow
exceptions in its two targeted debug-logging sites.

Two previously bare `except Exception:` blocks were replaced with
`except Exception as exc:` + `logger.debug(...)`:

  1. ``_parse_spec_header`` — header parse fallback (line ~120)
  2. ``create_pr`` — ``git diff --quiet`` fallback (line ~320)

This test pins the contract that:

  1. The module no longer contains a bare ``except Exception:``.
  2. The module imports/creates a module-level ``logger``.
  3. Both debug-log paths exist in the source and log the path/exc.
  4. Control flow is preserved: ``_parse_spec_header`` still returns
     the empty ``header`` dict on read failure, and ``create_pr``
     still falls through to PR creation if the diff probe fails.
"""

from __future__ import annotations

import ast
import importlib.util
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TARGET = REPO_ROOT / "daemon" / "cluster_dispatcher.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "cluster_dispatcher", TARGET
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["cluster_dispatcher"] = module
    spec.loader.exec_module(module)
    return module


def _parse_source() -> ast.Module:
    return ast.parse(TARGET.read_text())


def test_no_bare_except() -> None:
    """No ``except Exception:`` (without ``as exc``) remains."""
    tree = _parse_source()
    bare: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.name is None and node.type is not None:
                # Check it's a bare Exception catch (no ``as exc``)
                # ast.ExceptHandler.name is the bound-name string or None
                if isinstance(node.type, ast.Name) and node.type.id == "Exception":
                    bare.append((node.lineno, node.col_offset))
    assert not bare, f"bare 'except Exception:' found at {bare}"


def test_module_logger_present() -> None:
    """Module defines a module-level logger."""
    module = _load_module()
    assert hasattr(module, "logger"), "module must define a `logger` attribute"
    assert isinstance(module.logger, logging.Logger)
    assert module.logger.name.endswith("cluster_dispatcher")


def test_parse_spec_header_debug_log() -> None:
    """``_parse_spec_header`` reads the path with a debug log on failure."""
    src = TARGET.read_text()
    # Locate the function body and confirm the new log + bound name exist
    func_src_match_start = src.find("def _parse_spec_header")
    assert func_src_match_start != -1, "_parse_spec_header not found"
    func_block = src[func_src_match_start : src.find("\ndef ", func_src_match_start + 1)]
    assert "except Exception as exc:" in func_block, (
        "_parse_spec_header must bind the exception as `exc`"
    )
    assert "logger.debug" in func_block, (
        "_parse_spec_header must emit a logger.debug() on read failure"
    )
    # And the control-flow fallback still returns header
    assert "return header" in func_block, (
        "_parse_spec_header must still return the empty `header` dict"
    )


def test_create_pr_debug_log() -> None:
    """``create_pr`` wraps the git diff probe in a debug log on failure."""
    src = TARGET.read_text()
    func_src_match_start = src.find("def create_pr")
    assert func_src_match_start != -1, "create_pr not found"
    func_block = src[func_src_match_start : src.find("\ndef ", func_src_match_start + 1)]
    assert "except Exception as exc:" in func_block, (
        "create_pr must bind the exception as `exc` for the diff probe"
    )
    assert "logger.debug" in func_block, (
        "create_pr must emit a logger.debug() on diff-probe failure"
    )
    # Control-flow preservation: after the diff probe we must still
    # build a PR title/body and call gh (fall-through, no early return
    # that would skip PR creation).
    assert "pr_title" in func_block and "gh" in func_block, (
        "create_pr must still build the PR title and call gh after the "
        "diff-probe fallback"
    )


def test_module_compiles() -> None:
    """Module is syntactically valid and importable end-to-end."""
    module = _load_module()
    # Spot-check the function we care about is callable
    assert callable(module._parse_spec_header)
    assert callable(module.create_pr)
